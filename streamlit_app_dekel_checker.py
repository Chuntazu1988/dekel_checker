import base64
import html
import io
import datetime
import json
import os
import re
import subprocess
import threading
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlencode

import numpy as np
import streamlit as st
from playwright.sync_api import sync_playwright
from PIL import Image, ImageOps, ImageFilter
import easyocr

from routine import plan_routine
import skinprofile
import cloud
from hebrew import has_hebrew, ingredient_matches, ingredient_options
from compat import (
    ACTIVE_NAMES,
    EXFOLIANTS,
    active_label,
    compare_actives,
    detect_actives_from_names,
    mixing_advice,
)

def _logo_data_uri():
    path = Path(__file__).parent / "assets" / "logo.svg"
    try:
        return "data:image/svg+xml;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""


_logo_png = Path(__file__).parent / "assets" / "logo.png"
st.set_page_config(
    page_title="Dekel Checker", page_icon=str(_logo_png) if _logo_png.exists() else "💧", layout="centered"
)

EMOJI_CLASS = {"🟢": "good", "🟡": "caution", "🔴": "bad"}
GRADE_CLASS = {"מעולה": "grade-excellent", "טוב": "grade-good", "עובר": "grade-pass"}


def grade_class(result):
    return GRADE_CLASS.get(result.get("rating", ""), "")

st.markdown(
    f"<style>{(Path(__file__).parent / 'style.css').read_text(encoding='utf-8')}"
    f"\n:root{{--logo:url('{_logo_data_uri()}')}}</style>",
    unsafe_allow_html=True,
)

_mascot_file = Path(__file__).parent / "assets" / "dekel_verified.webp"
if _mascot_file.exists():
    _mascot_b64 = base64.b64encode(_mascot_file.read_bytes()).decode()
    st.markdown(
        f"<style>.dv-mascot, .cel-logo {{ background-image: url(data:image/webp;base64,{_mascot_b64}); }}</style>",
        unsafe_allow_html=True,
    )

BASE_DIR = Path(__file__).parent
ENV_PATH = BASE_DIR / ".env"
SESSION_PATH = BASE_DIR / "dekel_session.json"

RATING_COLORS = {
    "מעולה": "🟢",
    "טוב": "🟢",
    "עובר": "🟢",
    "על תנאי": "🟡",
    "רע": "🔴",
}


def load_env():
    env = dict(os.environ)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    try:
        for key in ("DEKEL_EMAIL", "DEKEL_PASSWORD"):
            if key in st.secrets:
                env.setdefault(key, st.secrets[key])
    except Exception:
        pass
    return env


@st.cache_resource(show_spinner="מתקינה דפדפן לבדיקה (רק בפעם הראשונה)...")
def ensure_playwright_browser():
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=True,
    )
    return True


# ======================
# פונקציות עזר לחיפוש באתר דקל
# ======================

def _new_context(p, browser):
    context = browser.new_context(storage_state=str(SESSION_PATH)) if SESSION_PATH.exists() else browser.new_context()
    # תמונות, גופנים ווידאו לא נחוצים לשליפת נתונים, וחוסכים זמן טעינה
    context.route(
        "**/*",
        lambda route: route.abort() if route.request.resource_type in ("image", "font", "media") else route.continue_(),
    )
    return context


class FastPathUnavailable(Exception):
    pass


def _open_fast_page(p, browser):
    """פותחת עמוד קל (robots.txt) על הדומיין של דקל עם הסשן השמור, בלי לטעון את האתר המלא."""
    context = _new_context(p, browser)
    if not any(c["name"].startswith("wordpress_logged_in") for c in context.cookies()):
        raise FastPathUnavailable()
    page = context.new_page()
    page.goto("https://thedekel.co.il/robots.txt", wait_until="commit", timeout=30000)
    return page


def _login(page, env):
    email = env.get("DEKEL_EMAIL")
    password = env.get("DEKEL_PASSWORD")
    if not email or not password:
        return "no-credentials"

    login_field = page.locator('input[name="user_login"]:visible').first
    pass_field = page.locator('input[name="user_password"]:visible').first
    login_field.click()
    login_field.fill(email)
    pass_field.click()
    pass_field.fill(password)

    login_btn = page.locator('a[data-id="masterstudy-authorization-login-button"]:visible').first
    login_btn.click(timeout=5000)
    page.wait_for_timeout(4000)

    if page.locator('input[name="user_password"]:visible').count() == 0:
        return True
    errors = [e.inner_text() for e in page.locator('[class*="error"]:visible').all()]
    return "error: " + " / ".join(errors)[:200]


def _type_search(page, ingredient):
    search_box = page.locator('input[type="search"]:visible').first
    search_box.click()
    search_box.fill(ingredient)


def _wait_for_suggestion(page, ingredient, timeout_ms=8000):
    waited = 0
    while waited < timeout_ms:
        suggestion = _find_suggestion(page, ingredient)
        if suggestion is not None:
            return suggestion
        page.wait_for_timeout(300)
        waited += 300
    return None


def _find_suggestion(page, ingredient):
    exact = page.locator(f'.suggestion-item[data-name="{ingredient}" i]')
    if exact.count() > 0:
        return exact.first
    fallback = page.locator(
        "button.suggestion-item, div.suggestion-item",
        has_text=re.compile(rf"^\s*{re.escape(ingredient)}\s*$", re.IGNORECASE),
    )
    if fallback.count() > 0:
        return fallback.first
    return None


DICTIONARY_URL = "https://thedekel.co.il/#milonid"

RATING_ALIASES = {"תלוי": "על תנאי"}

# מבצע את אותן קריאות שהאתר עצמו שולח כשלוחצים על רכיב, עם הסשן המחובר של הדף
BATCH_JS = """async (names) => {
  const url = "/wp-admin/admin-ajax.php";
  const post = async (data) => {
    const r = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
      body: new URLSearchParams(data).toString(),
    });
    if (!r.ok) return {error: "http " + r.status};
    return await r.json();
  };
  const one = async (name) => {
    try {
      const term = name.split("/")[0].trim();
      const s = await post({action: "fetch_ingredient_suggestions", search_term: term});
      if (s.data && s.data.status === 404) return {name, notFound: true};
      if (!Array.isArray(s.data)) return {name, error: s.error || "no-suggestions"};
      const hit = s.data.find(x => x.name.toLowerCase() === name.toLowerCase());
      if (!hit) return {name, notFound: true};
      const d = await post({
        action: "fetch_user_selected_ingredient",
        ingredient_id: String(hit.ingredientId),
        selected_name: hit.name,
      });
      if (!d.data) return {name, error: d.error || "no-detail"};
      return {name, detail: d.data};
    } catch (e) {
      return {name, error: String(e)};
    }
  };
  const out = [];
  let i = 0;
  const worker = async () => {
    while (i < names.length) {
      const n = names[i++];
      out.push(await one(n));
    }
  };
  await Promise.all(Array.from({length: 6}, worker));
  return out;
}"""


def _result(emoji, rating, text, role="", description="", notes=(), flags=(), roles=(), group_id=None):
    return {
        "emoji": emoji, "rating": rating, "text": text, "role": role,
        "description": description, "notes": list(notes), "flags": list(flags),
        "roles": list(roles), "group_id": group_id,
    }


def _open_dictionary(page, reload=False):
    page.goto(DICTIONARY_URL, timeout=30000)
    if reload:
        page.reload()
    try:
        page.wait_for_selector('input[type="search"]', state="visible", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(500)


def _is_logged_in(page):
    header = page.locator("body").inner_text()[:400]
    return "היי," in header and "איזור אישי" not in header


def _ensure_logged_in(page, context, env):
    if _is_logged_in(page):
        return True

    _type_search(page, "Water")
    suggestion = _wait_for_suggestion(page, "Water")
    if suggestion is None:
        return "no-suggestions"
    suggestion.click(timeout=5000)
    page.wait_for_timeout(2500)

    if page.locator('input[name="user_password"]:visible').count() > 0:
        outcome = _login(page, env)
        if outcome is not True:
            return outcome

    context.storage_state(path=str(SESSION_PATH))
    _open_dictionary(page, reload=True)
    return True if _is_logged_in(page) else "not-logged-in"


def _plain_text(html_text):
    text = re.sub(r"<br\s*/?>", "\n", html_text or "")
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _to_result(item):
    name = item["name"]
    if item.get("notFound"):
        return _result("❓", "לא נמצא", f"❓ הרכיב \"{name}\" לא נמצא במילון של דקל")
    detail = item.get("detail")
    if not detail:
        return _result("❌", "שגיאה", f"❌ שגיאה בבדיקת \"{name}\" ({item.get('error')})")

    rating = (detail.get("rating") or "").strip()
    rating = RATING_ALIASES.get(rating, rating)
    roles = (detail.get("functionFamilies") or "").strip()
    role_list = [part.strip() for part in roles.split(",") if part.strip()]
    group_id = detail.get("keyIngredientGroupId")
    description = _plain_text(detail.get("description"))

    notes = []
    flags = []
    if detail.get("increasesPhotoSensitivity") is True:
        notes.append("☀️ מגביר רגישות לשמש")
        flags.append("sun")
    if detail.get("contraindicatedInPregnancy") is True:
        notes.append("🤰 לא מומלץ בהיריון")
        flags.append("pregnancy")
    if detail.get("contraindicatedWhileBreastfeeding") is True:
        notes.append("🍼 לא מומלץ בהנקה")
        flags.append("breastfeeding")

    role_line = f"> תפקיד עיקרי: {roles}" if roles else ""
    body = "\n\n".join(part for part in (role_line, description, " | ".join(notes)) if part)

    if not rating:
        return _result(
            "⚪", "אין דירוג", f"⚪ עדיין אין דירוג באתר דקל עבור \"{name}\"\n\n{body}",
            roles, description, notes, flags, role_list, group_id,
        )

    emoji = RATING_COLORS.get(rating, "⚪")
    return _result(emoji, rating, f"{emoji} {rating}\n\n{body}", roles, description, notes, flags, role_list, group_id)


@st.cache_resource
def _ingredient_cache():
    return {}


CACHE_TTL_SECONDS = 24 * 60 * 60
INGREDIENT_DB_PATH = Path(__file__).parent / "data" / "ingredients.json"


@st.cache_resource(show_spinner=False)
def load_ingredient_db():
    """מאגר מקומי שנבנה מסריקת הקטלוג: רכיבים, רשימות רכיבים של מוצרים, והוראות שימוש. חוסך קריאות לאתר."""
    try:
        return json.loads(INGREDIENT_DB_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ingredients": {}, "products": {}, "meta": {}}


def search_many(ingredients, on_progress=None):
    ingredients = list(dict.fromkeys(ingredients))
    cache = _ingredient_cache()
    now = time.time()
    cached = {
        ing: cache[ing.lower()][1]
        for ing in ingredients
        if ing.lower() in cache and now - cache[ing.lower()][0] < CACHE_TTL_SECONDS
    }
    local = load_ingredient_db()["ingredients"]
    for ing in ingredients:
        record = local.get(ing.lower())
        if ing not in cached and record:
            cached[ing] = _to_result({"name": ing, "detail": record})
    missing = [ing for ing in ingredients if ing not in cached]
    if not missing:
        return cached

    fetched = _fetch_many(missing, on_progress)
    for ing, result in fetched.items():
        if result["emoji"] not in ("🔒", "❌"):
            cache[ing.lower()] = (now, result)
    return {**cached, **fetched}


def _fetch_many(ingredients, on_progress=None):
    env = load_env()
    ensure_playwright_browser()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            try:
                fast_page = _open_fast_page(p, browser)
                fast = _run_batches(fast_page, ingredients, on_progress)
                if fast is not None:
                    return fast
            except FastPathUnavailable:
                pass

            context = _new_context(p, browser)
            page = context.new_page()
            _open_dictionary(page)

            login = _ensure_logged_in(page, context, env)
            if login is not True:
                failure = _result(
                    "🔒",
                    "נדרשת התחברות",
                    "🔒 האתר דורש התחברות כדי להציג את המידע על הרכיב, "
                    "וההתחברות האוטומטית נכשלה. ודאי ש-DEKEL_EMAIL ו-DEKEL_PASSWORD "
                    "מוגדרים נכון.",
                )
                return {ingredient: failure for ingredient in ingredients}

            return _run_batches(page, ingredients, on_progress)
        finally:
            browser.close()


def _run_batches(page, ingredients, on_progress=None):
    """בודקת רכיבים דרך הדף. מחזירה None אם הסשן המהיר לא עבד (כל הרכיבים בקבוצה הראשונה נכשלו)."""
    results, failed = {}, []
    chunk = 12
    for start in range(0, len(ingredients), chunk):
        part = ingredients[start:start + chunk]
        items = page.evaluate(BATCH_JS, part)
        if start == 0 and items and all(item.get("error") for item in items):
            return None
        for item in items:
            if item.get("error"):
                failed.append(item["name"])
            else:
                results[item["name"]] = _to_result(item)
        if on_progress:
            on_progress(min(start + chunk, len(ingredients)), len(ingredients), part[-1])

    if failed:
        for item in page.evaluate(BATCH_JS, failed):
            results[item["name"]] = _to_result(item)
    return results


PRODUCTS_JS = """async (term) => {
  const r = await fetch(
    "/wp-admin/admin-ajax.php?action=get_product_suggestions&searchTerm=" + encodeURIComponent(term),
    {credentials: "same-origin"}
  );
  if (!r.ok) return {error: "http " + r.status};
  return await r.json();
}"""

FIND_PRODUCTS_JS = """async (queries) => {
  const base = "/wp-admin/admin-ajax.php?action=get_product_suggestions&searchTerm=";
  const one = async (q) => {
    try {
      const r = await fetch(base + encodeURIComponent(q), {credentials: "same-origin"});
      if (!r.ok) return [];
      const j = await r.json();
      return Array.isArray(j.data) ? j.data : [];
    } catch (e) { return []; }
  };
  const lists = [];
  let i = 0;
  const worker = async () => { while (i < queries.length) { lists.push(await one(queries[i++])); } };
  await Promise.all([worker(), worker(), worker()]);
  const seen = {};
  const merged = [];
  for (const list of lists) {
    for (const item of list) {
      if (item.slug && !seen[item.slug]) { seen[item.slug] = 1; merged.push(item); }
    }
  }
  return merged;
}"""

PRODUCT_PAGE_JS = """async (slug) => {
  const r = await fetch("/product-verified/?slug=" + encodeURIComponent(slug), {credentials: "same-origin"});
  return await r.text();
}"""

STOPWORDS = {"the", "and", "for", "of", "with", "to", "in", "a", "de", "la", "le", "et", "pour"}


def _clean(value):
    value = (value or "").strip()
    return "" if value in ("None", "null") else value


def _product_from_item(item):
    return {
        "name": _clean(item.get("primaryName")),
        "name2": _clean(item.get("secondaryName")),
        "company": _clean(item.get("companyName")),
        "description": _plain_text(item.get("description")),
        "slug": _clean(item.get("slug")),
        "picture": _clean(item.get("productPicture")),
        "verified": str(item.get("verified")).lower() == "true",
    }


def _with_logged_in_page(callback):
    env = load_env()
    ensure_playwright_browser()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            try:
                fast_page = _open_fast_page(p, browser)
                outcome = callback(fast_page)
                if outcome and not (isinstance(outcome, dict) and "error" in outcome):
                    return outcome
            except FastPathUnavailable:
                pass
            except Exception:
                pass

            context = _new_context(p, browser)
            page = context.new_page()
            _open_dictionary(page)

            if _ensure_logged_in(page, context, env) is not True:
                raise RuntimeError("login")

            return callback(page)
        finally:
            browser.close()


@st.cache_data(ttl=3600, show_spinner=False)
def search_products(term):
    """מחפשת תכשירים בקטלוג של דקל. שגיאות לא נשמרות במטמון."""
    response = _with_logged_in_page(lambda page: page.evaluate(PRODUCTS_JS, term))

    items = response.get("data") if isinstance(response, dict) else None
    if (isinstance(items, dict) and items.get("status") == 404) or (
        isinstance(response, dict) and response.get("success") is False and isinstance(items, str)
    ):
        return []  # האתר מחזיר "No product suggestions found" כשאין שום תוצאה
    if not isinstance(items, list):
        raise RuntimeError("bad-response")
    return [_product_from_item(item) for item in items]


def find_products(queries):
    items = _with_logged_in_page(lambda page: page.evaluate(FIND_PRODUCTS_JS, queries))
    return [_product_from_item(item) for item in items or []]


COUPONS_JS = """() => {
  const rows = [];
  const leaves = [...document.querySelectorAll('p,div,span,li')].filter(
    e => /קוד קופון|בתוקף/.test(e.textContent) && ![...e.children].some(c => /קוד קופון|בתוקף/.test(c.textContent)));
  for (const leaf of leaves) {
    let card = leaf;
    while (card.parentElement && !card.querySelector('img')) card = card.parentElement;
    const text = card.innerText.trim();
    if (!text) continue;
    const links = [...card.querySelectorAll('a[href]')];
    const buy = links.find(a => a.innerText.includes('לרכישה')) || links[0];
    const logo = [...card.querySelectorAll('img')].map(i => i.src).find(src => src && !src.includes('emoji'));
    rows.push({text, href: buy ? buy.href : '', logo: logo || ''});
  }
  return rows;
}"""

COUPON_EXPIRY = re.compile(r"בתוקף עד\s*(\d{1,2})\.(\d{1,2})\.(\d{4})")
COUPON_CODE = re.compile(r"קוד קופון\s+([A-Za-z0-9_\-]+)")


def parse_coupons(rows, today=None):
    today = today or datetime.date.today()
    coupons, seen = [], set()
    for row in rows:
        lines = [part.strip() for part in re.split(r"[\t\n]+", row["text"]) if part.strip()]
        if len(lines) < 2:
            continue
        title, body = lines[0], lines[1:]
        expiry_match = next((m for m in map(COUPON_EXPIRY.search, body) if m), None)
        expires = None
        if expiry_match:
            day, month, year = (int(g) for g in expiry_match.groups())
            try:
                expires = datetime.date(year, month, day)
            except ValueError:
                expires = None
        code_match = next((m for m in map(COUPON_CODE.search, body) if m), None)
        description = " ".join(
            line for line in body
            if not COUPON_EXPIRY.search(line) and not COUPON_CODE.search(line)
            and line.strip("| ") not in ("לרכישה >>", "")
        )
        key = (title, description)
        if key in seen or (expires and expires < today):
            continue
        seen.add(key)
        coupons.append({
            "title": title, "description": description, "code": code_match.group(1) if code_match else "",
            "expires": expires, "url": row.get("href", ""), "logo": row.get("logo", ""),
        })
    coupons.sort(key=lambda c: (c["expires"] is None, c["expires"] or today))
    return coupons


def _scrape_coupons():
    ensure_playwright_browser()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = _new_context(p, browser).new_page()
            page.goto("https://thedekel.co.il/", timeout=30000)
            page.wait_for_selector("text=קוד קופון", state="attached", timeout=15000)
            rows = page.evaluate(COUPONS_JS)
        finally:
            browser.close()
    coupons = parse_coupons(rows)
    if not coupons:
        raise RuntimeError("no-coupons")
    return coupons


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_coupons():
    """קוראת את ההטבות הפעילות מדף הבית של דקל. שגיאות לא נשמרות במטמון."""
    return _scrape_coupons()


COUPON_TTL = 1800
COUPON_RETRY = 300


@st.cache_resource(show_spinner=False)
def coupon_holder():
    return {"coupons": None, "ts": 0.0, "busy": False, "failed": 0.0}


def _refresh_coupons(holder):
    try:
        holder["coupons"] = _scrape_coupons()
        holder["ts"] = time.time()
    except Exception:
        holder["failed"] = time.time()
    finally:
        holder["busy"] = False


def ensure_coupons_async():
    """מביאה הטבות ברקע (בלי לעכב את המסך), כדי שההיסטוריה תישאר 'חיה'."""
    holder = coupon_holder()
    fresh = holder["coupons"] is not None and time.time() - holder["ts"] < COUPON_TTL
    recently_failed = time.time() - holder["failed"] < COUPON_RETRY
    if holder["busy"] or fresh or recently_failed:
        return
    holder["busy"] = True
    threading.Thread(target=_refresh_coupons, args=(holder,), daemon=True).start()


BRAND_STOP = {"the", "skincare", "cosmetics", "labs", "lab", "beauty", "care", "and", "paris", "דה", "הטבה", "קבועה"}


def brand_tokens(company):
    words = re.findall(r"[a-z0-9\u0590-\u05ff]+", (company or "").lower())
    return {w for w in words if len(w) >= 3 and w not in BRAND_STOP}


def coupons_for(company, coupons):
    tokens = brand_tokens(company)
    if not tokens:
        return []
    found = []
    for coupon in coupons or []:
        text = f"{coupon['title']} {coupon['description']}".lower()
        if any(token in text for token in tokens):
            found.append(coupon)
    return found


def history_alerts():
    """רשימת (רשומת היסטוריה, הטבות) לפריטים שיש להם הטבה פעילה כרגע."""
    coupons = coupon_holder()["coupons"]
    if not coupons:
        return []
    alerts = []
    for entry in st.session_state.history:
        product = entry.get("product") or {}
        matches = coupons_for(product.get("company", ""), coupons)
        if matches:
            alerts.append((entry, matches))
    return alerts


NAV_ICONS = {
    "home": ":material/home:", "list": ":material/checklist:", "product": ":material/photo_camera:",
    "compat": ":material/handshake:", "routine": ":material/calendar_month:", "history": ":material/history:",
    "coupons": ":material/local_activity:", "profile": ":material/face:", "sample": ":material/auto_awesome:",
    "more": ":material/more_horiz:",
}


def product_coupons(product):
    """הטבות פעילות כרגע למותג של מוצר, אם כבר נטענו."""
    coupons = coupon_holder()["coupons"]
    if not coupons or not product:
        return []
    return coupons_for(product.get("company", ""), coupons)


def routine_lookup():
    merged = dict(routine_entries_by_slug())
    merged.update(st.session_state.get("custom_products", {}))
    return merged


def custom_entry(slug, name, company, names, results_map, meta, verified=False):
    actives = detect_actives(names, results_map, (meta or {}).get("key_ingredients", ()))
    flags = {flag for n in names for flag in results_map.get(n, {}).get("flags", [])}
    return {
        "slug": slug, "name": name, "name2": "", "company": company or "", "verified": verified,
        "time": (meta or {}).get("time"), "types": [], "actives": sorted(actives),
        "pregnancy_ok": False if "pregnancy" in flags else True, "sun": "sun" in flags, "ingredients": len(names),
    }


def add_to_routine(entry):
    """מוסיפה לשגרה ובודקת מיד אם המוצר מתנגש עם מה שכבר בשגרה."""
    lookup = routine_lookup()
    existing = [lookup[slug] for slug in st.session_state.routine if slug in lookup]
    if entry["slug"] in routine_entries_by_slug():
        pass
    else:
        st.session_state.custom_products[entry["slug"]] = entry
    if entry["slug"] not in st.session_state.routine:
        st.session_state.routine.append(entry["slug"])
        save_routine()
    clashes = []
    for other in existing:
        found = compare_actives(set(entry.get("actives", [])), set(other.get("actives", [])))
        if found:
            clashes.append((other["name"], found))
    st.session_state.routine_check = {"name": entry["name"], "clashes": clashes}


def routine_alerts():
    """[(רשומת מוצר, הטבות)] למוצרים בשגרה שיש להם הטבה פעילה."""
    coupons = coupon_holder()["coupons"]
    if not coupons:
        return []
    by_slug = routine_lookup()
    alerts = []
    for slug in st.session_state.get("routine", []):
        entry = by_slug.get(slug)
        if entry:
            matches = coupons_for(entry["company"], coupons)
            if matches:
                alerts.append((entry, matches))
    return alerts


def coupon_line(coupon):
    code = f" · קוד {coupon['code']}" if coupon.get("code") else ""
    return f"{coupon['title']}{code}"


def toast_once(key, text):
    seen = st.session_state.setdefault("notified", set())
    if key not in seen:
        seen.add(key)
        st.toast(text, icon="🎟️")


def render_coupon_alerts():
    """פס התראה בראש כל מסך, וטוסט חד-פעמי לכל הטבה חדשה על מוצר בשגרה."""
    alerts = routine_alerts()
    if not alerts:
        return
    for entry, matches in alerts:
        for coupon in matches[:1]:
            toast_once(f"{entry['slug']}:{coupon['title']}", f"יש הטבה פעילה למוצר מהשגרה שלך, {entry['name']}: {coupon_line(coupon)}")
    names = ", ".join(entry["name"] for entry, _m in alerts[:2]) + (" ועוד" if len(alerts) > 2 else "")
    first = alerts[0][1][0]
    st.markdown(
        '<div class="alert-strip"><span class="as-bell">🔔</span><div><b>הטבה פעילה למוצר מהשגרה שלך</b>'
        f'<small>{html.escape(names)} · {html.escape(coupon_line(first))}</small></div></div>',
        unsafe_allow_html=True,
    )
    if st.session_state.get("nav") != TAB_COUPONS and st.button("🎟️ לצפייה בהטבה", key="alert_go"):
        go_to(TAB_COUPONS)


def nav_label(tab):
    names = {
        TAB_HOME: f"{NAV_ICONS['home']} בית",
        TAB_INGREDIENTS: f"{NAV_ICONS['list']} העלאת רכיבים",
        TAB_PRODUCT: f"{NAV_ICONS['product']} העלאת מוצר",
        TAB_COMPAT: f"{NAV_ICONS['compat']} שילובים",
        TAB_ROUTINE: f"{NAV_ICONS['routine']} השגרה שלי",
        TAB_HISTORY: f"{NAV_ICONS['history']} היסטוריה",
        TAB_COUPONS: f"{NAV_ICONS['coupons']} קופונים",
    }
    label = names.get(tab, tab)
    if tab == TAB_ROUTINE and routine_alerts():
        return f"{label} 🔔{len(routine_alerts())}"
    if tab == TAB_HISTORY and st.session_state.history:
        count = len(history_alerts())
        if count:
            return f"{label} 🔔{count}"
    return label


def coupon_card_html(coupon):
    today = datetime.date.today()
    expires = coupon["expires"]
    if expires:
        days = (expires - today).days
        badge_class = "soon" if days <= 3 else ""
        when = "פג היום" if days == 0 else f"בתוקף עד {expires.strftime('%d.%m.%Y')}"
    else:
        badge_class, when = "", "ללא תאריך תפוגה"
    logo = f'<img class="cp-logo" src="{html.escape(coupon["logo"])}" alt="" referrerpolicy="no-referrer">' if coupon["logo"] else ""
    code = (
        f'<div class="cp-code"><span>קוד קופון</span><b>{html.escape(coupon["code"])}</b></div>'
        if coupon["code"] else ""
    )
    link = (
        f'<a class="plink" href="{html.escape(coupon["url"])}" target="_blank" rel="noopener">לרכישה ↗</a>'
        if coupon["url"].startswith("http") else ""
    )
    return (
        f'<div class="cp-card">{logo}<div class="cp-body">'
        f'<div class="cp-title">{html.escape(coupon["title"])}'
        f'<span class="cp-when {badge_class}">{html.escape(when)}</span></div>'
        f'<div class="cp-desc">{html.escape(coupon["description"])}</div>{code}{link}</div></div>'
    )


def _extract_json_array(page_html, key):
    start = page_html.find(f'"{key}":[')
    if start < 0:
        return None
    begin = page_html.find("[", start)
    depth = 0
    for position in range(begin, len(page_html)):
        char = page_html[position]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(page_html[begin:position + 1])
                except ValueError:
                    return None
    return None


def _extract_product_object(page_html):
    marker = page_html.find('"ingredientsInTheList"')
    start = page_html.rfind('{"id"', 0, marker) if marker >= 0 else -1
    if start < 0:
        return {}
    depth = 0
    for position in range(start, len(page_html)):
        char = page_html[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(page_html[start:position + 1])
                except ValueError:
                    return {}
    return {}


def load_product_ingredients(slug):
    """מביאה את רשימת הרכיבים המלאה, הדירוגים והוראות השימוש מעמוד המוצר של דקל."""
    local = load_ingredient_db()
    if slug in local["products"]:
        names, results = [], {}
        for name in local["products"][slug]:
            record = local["ingredients"].get(name.lower())
            if record and name not in results:
                names.append(name)
                results[name] = _to_result({"name": name, "detail": record})
        info = local["meta"].get(slug, {})
        index_entry = next((e for e in (load_catalog_index() or {}).get("products", []) if e["slug"] == slug), {})
        if names:
            return names, results, {
                "time": index_entry.get("time"),
                "description": _plain_text(info.get("description")),
                "key_ingredients": info.get("key") or [],
            }

    page_html = _with_logged_in_page(lambda page: page.evaluate(PRODUCT_PAGE_JS, slug))
    records = _extract_json_array(page_html, "ingredientsInTheList")
    if not records:
        raise RuntimeError("no-ingredients")

    product = _extract_product_object(page_html)
    meta = {
        "time": product.get("idealTimeOfUse"),
        "description": _plain_text(product.get("description")),
        "key_ingredients": product.get("keyIngredients") or [],
    }

    names, results = [], {}
    for record in records:
        name = (record.get("name") or "").strip()
        if name and name not in results:
            names.append(name)
            results[name] = _to_result({"name": name, "detail": record})
    return names, results, meta


def _tokens(text):
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]


def ingredient_list_matches(text):
    """כמה חלקים בטקסט (מופרד בפסיקים) הם רכיבים מוכרים במאגר, ומתוך כמה."""
    known = load_ingredient_db()["ingredients"]
    parts = [re.sub(r"[^a-z0-9 \-/()']", "", part.lower()).strip() for part in re.split(r"[,;\n]", text or "")]
    parts = [part for part in parts if len(part) >= 3]
    hits = sum(1 for part in parts if part in known or part.rstrip(".") in known)
    return hits, len(parts)


def looks_like_ingredient_list(text):
    hits, total = ingredient_list_matches(text)
    return bool(total) and hits >= 6 and hits / total >= 0.35


PRODUCT_OCR_SIDE = 1280


@st.cache_data(show_spinner=False)
def ocr_lines(image_bytes):
    image = open_image(io.BytesIO(image_bytes))
    # שם ומותג על האריזה גדולים, ולכן אפשר לקרוא ברזולוציה נמוכה יותר ולחסוך זמן
    result = get_ocr_reader().readtext(
        np.array(preprocess_for_ocr(image, PRODUCT_OCR_SIDE)), detail=1, canvas_size=PRODUCT_OCR_SIDE
    )
    lines = []
    for box, text, confidence in result:
        ys = [point[1] for point in box]
        lines.append((text.strip(), float(confidence), float(max(ys) - min(ys)), float(sum(ys) / len(ys))))
    return lines


GENERIC_WORDS = {"acid", "cream", "serum", "toner", "with", "for", "body", "face", "gel", "oil", "lotion", "cleanser", "water", "mask"}


def _clean_lines(lines, min_confidence):
    cleaned = []
    for text, confidence, height, y in lines:
        text = re.sub(r"[^A-Za-z0-9%&+.\- ]", " ", text)
        text = re.sub(r"\s+", " ", text).strip(" .-:")
        if confidence >= min_confidence and len(text) >= 3 and re.search(r"[A-Za-z]{3}", text):
            cleaned.append((text, height, y))
    return cleaned


def build_queries(lines):
    cleaned = _clean_lines(lines, 0.45)
    if not cleaned:
        return []

    cleaned.sort(key=lambda item: item[2])
    biggest = {text for text, _, _ in sorted(cleaned, key=lambda item: -item[1])[:6]}
    ordered = [text for text, _, _ in cleaned if text in biggest]

    # קודם צירופים (הכי ממוקדים), ואחר כך שורות בודדות
    queries = []
    if len(ordered) >= 3:
        queries.append(" ".join(ordered[:3]))
    queries += [f"{a} {b}" for a, b in zip(ordered, ordered[1:])]
    queries += ordered

    unique, seen = [], set()
    for query in queries:
        if len(query) >= 5 and query.lower() not in seen:
            seen.add(query.lower())
            unique.append(query)
    return unique[:6]


def build_extra_queries(lines, used):
    """שאילתות נוספות כשההתאמות הראשונות לא נכונות: שורות פחות בולטות ומילים בודדות."""
    taken = {query.lower() for query in used}
    cleaned = sorted(_clean_lines(lines, 0.3), key=lambda item: -item[1])

    candidates = [text for text, _, _ in cleaned]
    for text, _, _ in cleaned:
        for word in re.findall(r"[A-Za-z]{4,}", text):
            if word.lower() not in GENERIC_WORDS:
                candidates.append(word)

    extra = []
    for query in candidates:
        if len(query) >= 4 and query.lower() not in taken:
            taken.add(query.lower())
            extra.append(query)
    return extra[:6]


def rank_products(products, lines, min_matched=2, exclude=()):
    seen_tokens = set()
    for text, confidence, _, _ in lines:
        if confidence >= 0.3:
            seen_tokens.update(_tokens(text))

    excluded = set(exclude)
    scored = []
    for product in products:
        if product.get("slug") in excluded:
            continue
        english_company = product["company"].split("|")[-1]
        candidate = set(_tokens(f"{product['name']} {english_company} {product['name2']}"))
        if not candidate:
            continue
        matched_tokens = candidate & seen_tokens
        matched = len(matched_tokens)
        if min_matched <= 1:
            # בהתאמה מתונה מקבלים גם מילה אחת, אבל לא מילה כללית כמו acid או cream
            specific = [t for t in matched_tokens if len(t) >= 4 and t not in GENERIC_WORDS]
            ok = matched >= 2 or bool(specific)
        else:
            ok = matched >= 2 or (matched == 1 and len(candidate) <= 2)
        if ok:
            scored.append((matched - 0.25 * (len(candidate) - matched), product))

    scored.sort(key=lambda item: -item[0])
    return [product for _, product in scored[:5]]


def search_open_beauty_facts(query):
    params = urlencode({
        "search_terms": query, "search_simple": 1, "action": "process", "json": 1,
        "page_size": 8, "fields": "code,product_name,brands,ingredients_text",
    })
    request = urllib.request.Request(
        "https://world.openbeautyfacts.org/cgi/search.pl?" + params,
        headers={"User-Agent": "DekelChecker/1.0 (personal project)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []

    found = []
    for product in data.get("products", []):
        ingredients = (product.get("ingredients_text") or "").strip()
        name = (product.get("product_name") or "").strip()
        if ingredients and name:
            found.append({"brand": (product.get("brands") or "").strip(), "name": name, "ingredients": ingredients})
    return found[:4]


# ======================
# קבוצות לפי שימוש + מדריך שימוש
# ======================

# סדר הקבוצות הוא גם סדר העדיפות: רכיב נכנס לקבוצה הראשונה שמתאימה לאחד מהתפקידים שלו
USAGE_GROUPS = [
    ("exfoliate", "✨", "פילינג והתחדשות", {"מקלף", "שוחק"}),
    ("brighten", "🌟", "הבהרה", {"מבהיר"}),
    ("protect", "🛡️", "הגנה ונוגדי חמצון", {"אנטיאוקסידנט (נוגד חמצון)", "מסנן קרינת UV"}),
    ("calm", "🌿", "הרגעה", {"מרגיע", "נוגד דלקת"}),
    ("acne", "🧫", "אנטי-בקטריאלי", {"אנטי בקטריאלי"}),
    ("hydrate", "💧", "לחות והזנה", {"מלחח", "מזין", "משפר מצב עור", "רכיב טבעי בעור", "מרכך"}),
    ("boost", "🚀", "שיפור ספיגה ופעילות", {"משפר / מגביר חדירה", "רכיב משפר תפקוד | מחולל תהליכים"}),
    ("clean", "🧼", "ניקוי", {"חומר ניקוי פעיל שטח"}),
    ("base", "🧪", "בסיס ומרקם", {"ממס", "מתחלב", "מווסת צמיגות", "מייצב", "מווסת חומציות", "מכווץ", "סופג"}),
    ("preserve", "🧴", "שימור", {"חומר משמר", "אנטי מיקרוביאלי", "מפחית מתכות"}),
    ("scent", "🌸", "ריח וצבע", {"מבשם", "צבען", "דאודורנט"}),
]
OTHER_GROUP = ("other", "🧩", "אחר", set())


def group_ingredients(names, results):
    groups = {code: [] for code, *_ in USAGE_GROUPS}
    groups["other"] = []
    for name in names:
        roles = set(results[name].get("roles", []))
        for code, _icon, _title, group_roles in USAGE_GROUPS:
            if roles & group_roles:
                groups[code].append(name)
                break
        else:
            groups["other"].append(name)
    return groups


def usage_groups_html(names, results):
    groups = group_ingredients(names, results)
    blocks = []
    for code, icon, title, _roles in USAGE_GROUPS + [OTHER_GROUP]:
        members = groups[code]
        if not members:
            continue
        chips = "".join(
            f'<span class="ugchip {EMOJI_CLASS.get(results[n]["emoji"], "other")} {grade_class(results[n])}" '
            f'style="--i:{index}">{html.escape(n)}</span>'
            for index, n in enumerate(members)
        )
        blocks.append(
            f'<div class="ug"><div class="ug-head"><span class="ug-ico">{icon}</span>'
            f'<b>{html.escape(title)}</b><em>{len(members)}</em></div><div class="ug-chips">{chips}</div></div>'
        )
    return '<div class="ug-grid">' + "".join(blocks) + "</div>"


# ---- פעילים שחשוב לדעת עליהם (הלוגיקה והכללים במודול compat) ----
def detect_actives(names, results, key_ingredients=(), top=15):
    return detect_actives_from_names(
        ((name, results.get(name, {}).get("group_id")) for name in names), key_ingredients, top
    )


# ---- התאמה לשאר המוצרים בקטלוג של דקל ----
CATALOG_INDEX_PATH = Path(__file__).parent / "data" / "catalog_index.json"
OPPOSITE_TIMES = {("MORNING", "EVENING"), ("EVENING", "MORNING")}
# סוגי המוצרים של דקל הם צירופים ארוכים; מאחדים אותם לקטגוריות קצרות וברורות
CATEGORY_RULES = [
    ("שיער", ("שיער",)),
    ("קרם הגנה", ("קרם הגנה",)),
    ("סרום", ("סרום",)),
    ("מסיכות", ("מסיכת פנים",)),
    ("אזור העיניים", ("עיניים", "ריסים")),
    ("ניקוי פנים", ("סבון פנים", "מיסלריים", "באלם ניקוי")),
    ("טונר ואסנס", ("טונר", "מי פנים", "אסנס")),
    ("קרם פנים", ("קרם פנים", "קרם CC")),
    ("טיפול נקודתי", ("נקודתי", "SOS")),
    ("שפתיים", ("שפתון",)),
    ("איפור", ("איפור",)),
    ("גוף", ("גוף", "ידיים", "רגליים", "מתיחה", "דאודורנט")),
]
FACE_CATEGORIES = ["סרום", "קרם פנים", "ניקוי פנים", "טונר ואסנס", "קרם הגנה", "מסיכות", "אזור העיניים", "טיפול נקודתי"]


def categories_of(entry):
    found = []
    for raw in entry.get("types") or []:
        for token in raw.split(","):
            for name, words in CATEGORY_RULES:
                if any(word in token for word in words):
                    if name not in found:
                        found.append(name)
                    break
    return found or ["אחר"]


@st.cache_data(show_spinner=False)
def load_catalog_index():
    try:
        return json.loads(CATALOG_INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def catalog_compatibility(slug, actives, time_code):
    index = load_catalog_index()
    if not index:
        return None

    groups = {"ok": [], "separate": [], "avoid": []}
    for entry in index["products"]:
        if entry["slug"] == slug or not entry.get("verified"):
            continue
        conflicts = compare_actives(actives, set(entry.get("actives", [])))
        if not conflicts:
            groups["ok"].append((entry, []))
        elif (time_code, entry.get("time")) in OPPOSITE_TIMES:
            groups["separate"].append((entry, conflicts))
        else:
            groups["avoid"].append((entry, conflicts))
    return groups, index.get("generated", "")


def _shown_category(entry, chosen):
    for category in categories_of(entry):
        if category in chosen:
            return category
    return None


def compat_group_html(rows, with_reasons, chosen):
    if not rows:
        return '<div class="cx-empty">אין מוצרים בקבוצה הזאת.</div>'

    by_type = {}
    for entry, conflicts in rows:
        by_type.setdefault(_shown_category(entry, chosen), []).append((entry, conflicts))

    blocks = []
    for type_name, items in sorted(by_type.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        chips = []
        for entry, _conflicts in sorted(items, key=lambda item: item[0]["name"].lower()):
            url = "https://thedekel.co.il/product-verified/?slug=" + quote(entry["slug"])
            brand = entry["company"].split("|")[-1].strip()
            chips.append(
                f'<a class="cx-chip" href="{html.escape(url)}" target="_blank" rel="noopener">'
                f'{cx_img(entry["slug"])}'
                f'<span class="cx-t"><b>{html.escape(entry["name"])}</b><small>{html.escape(brand)}</small></span></a>'
            )
        reasons = ""
        if with_reasons:
            seen, lines = set(), []
            for _entry, conflicts in items:
                for title, reason in conflicts:
                    if title not in seen:
                        seen.add(title)
                        lines.append(f"<li><b>{html.escape(title)}:</b> {html.escape(reason)}</li>")
            reasons = f'<ul class="cx-reasons">{"".join(lines)}</ul>'
        blocks.append(
            f'<div class="cx-group"><div class="cx-type">{html.escape(type_name)}<em>{len(items)}</em></div>'
            f'<div class="cx-chips">{"".join(chips)}</div>{reasons}</div>'
        )
    return "".join(blocks)


def show_compatibility(names, results, product, meta):
    actives = detect_actives(names, results, (meta or {}).get("key_ingredients", ()))
    computed = catalog_compatibility(product["slug"], actives, (meta or {}).get("time"))
    if computed is None:
        return
    groups, generated = computed

    total_ok, total_sep, total_avoid = len(groups["ok"]), len(groups["separate"]), len(groups["avoid"])
    if actives:
        why = f"במוצר הזה יש {active_label(actives)}, ולכן {total_avoid} מוצרים בקטלוג מתנגשים איתו"
        if total_sep:
            why += f" ו-{total_sep} מתאימים רק בנפרד (בוקר מול ערב)"
        why += f". {total_ok} מוצרים מתאימים לשימוש יחד איתו."
    else:
        why = f"לא זוהו במוצר פעילים חזקים, ולכן {total_ok} מוצרים בקטלוג מתאימים לשימוש יחד איתו."
    st.markdown(f'<div class="cx-sum"><b>איך לקרוא את זה</b><span>{html.escape(why)}</span></div>', unsafe_allow_html=True)

    routine_slugs = st.session_state.get("routine", [])
    if routine_slugs:
        lookup = routine_lookup()
        clashes = []
        for slug in routine_slugs:
            other = lookup.get(slug)
            if other and slug != product.get("slug"):
                found = compare_actives(actives, set(other.get("actives", [])))
                if found:
                    clashes.append((other["name"], found[0][0]))
        if clashes:
            st.warning("מול השגרה שלך: " + "; ".join(f"{name} ({title})" for name, title in clashes[:4]) + ". כדאי להפריד ביניהם.")
        else:
            st.success("מול השגרה שלך: אין התנגשות עם המוצרים שכבר בשגרה.")

    counts = {}
    for rows in groups.values():
        for entry, _conflicts in rows:
            for category in categories_of(entry):
                counts[category] = counts.get(category, 0) + 1
    options = [name for name, _words in CATEGORY_RULES if name in counts] + (["אחר"] if "אחר" in counts else [])
    chosen = st.multiselect(
        "סוגי מוצרים להצגה",
        options,
        default=[name for name in FACE_CATEGORIES if name in options],
        key="compat_types",
        help="ברירת המחדל: מוצרי פנים. אפשר להוסיף גם שיער, גוף ועוד.",
    )
    query = st.text_input(
        "חיפוש חופשי",
        placeholder="חיפוש חופשי: שם מוצר או מותג, למשל CeraVe",
        key="compat_query",
        label_visibility="collapsed",
    ).strip().lower()
    if query:
        # חיפוש חופשי מחפש בכל הקטגוריות
        chosen = options
        groups = {
            bucket: [
                (entry, conflicts) for entry, conflicts in rows
                if query in f"{entry['name']} {entry.get('name2', '')} {entry['company']}".lower()
            ]
            for bucket, rows in groups.items()
        }
    else:
        groups = {
            bucket: [(entry, conflicts) for entry, conflicts in rows if _shown_category(entry, chosen)]
            for bucket, rows in groups.items()
        }

    tab_ok, tab_separate, tab_avoid = st.tabs([
        f"✅ אפשר לשלב ({len(groups['ok'])})",
        f"🕒 רק בנפרד ({len(groups['separate'])})",
        f"⛔ עדיף לא ({len(groups['avoid'])})",
    ])
    with tab_ok:
        st.caption("אין ביניהם התנגשות בין הפעילים, ולכן אפשר להשתמש בהם באותה שגרה.")
        st.markdown(compat_group_html(groups["ok"], False, chosen), unsafe_allow_html=True)
    with tab_separate:
        st.caption("שילוב בעייתי באותה שגרה, אבל אפשר להשתמש בשניהם אם אחד בבוקר והשני בערב.")
        st.markdown(compat_group_html(groups["separate"], True, chosen), unsafe_allow_html=True)
    with tab_avoid:
        st.caption("בין המוצרים האלה למוצר שבחרת יש התנגשות בין פעילים, והסיבה מופיעה מתחת לכל קבוצה.")
        st.markdown(compat_group_html(groups["avoid"], True, chosen), unsafe_allow_html=True)

    st.caption(
        f"ההשוואה כוללת רק מוצרים מאושרי דקל (נכון ל-{generated}), לפי פעילים בולטים וכללי שילוב כלליים. "
        "היא להכוונה בלבד ואינה ייעוץ רפואי."
    )


TIME_LABELS = {
    "MORNING": ("☀️", "מומלץ בבוקר"),
    "EVENING": ("🌙", "מומלץ בערב"),
    "MORNING_AND_EVENING": ("☀️🌙", "מתאים לבוקר ולערב"),
}


def usage_time(names, results, actives, meta):
    code = (meta or {}).get("time")
    if code in TIME_LABELS:
        icon, label = TIME_LABELS[code]
        return icon, label, "לפי דקל"

    sensitizing = any("sun" in results[n].get("flags", []) for n in names)
    has_uv_filter = any("מסנן קרינת UV" in results[n].get("roles", []) for n in names)
    if actives & ({"retinoid"} | EXFOLIANTS) or sensitizing:
        return "🌙", "עדיף בערב, ובבוקר קרם הגנה", "הערכה לפי הרכיבים"
    if has_uv_filter:
        return "☀️", "מתאים לבוקר (יש בו מסנני קרינה)", "הערכה לפי הרכיבים"
    return "☀️🌙", "מתאים לבוקר ולערב", "הערכה לפי הרכיבים"


def usage_frequency(actives):
    if "retinoid" in actives:
        return "להתחיל פעמיים עד שלוש בשבוע ולעלות בהדרגה לפי עמידות העור."
    if actives & EXFOLIANTS:
        return "להתחיל פעמיים בשבוע ולעלות בהדרגה לפי עמידות העור."
    if "vitc" in actives:
        return "אפשר להשתמש פעם ביום, ולהתחיל במינון נמוך אם העור רגיש."
    return "אפשר להשתמש מדי יום, לפי הוראות היצרן."


USAGE_KEYWORDS = ("אופן השימוש", "מומלץ", "לשימוש", "חשוב", "פורמולה", "התכשיר", "פעמיים", "בשבוע", "קרם הגנה")
FREQUENCY_PATTERN = re.compile(r"(פעם ביום|פעמיים|פעמים|מדי יום|כל יום|בשבוע|יומיומי)")


def _strip_marks(line):
    return re.sub(r"^[^0-9A-Za-z֐-׿(]+", "", line).strip()


def dekel_usage_lines(description):
    lines = []
    for line in (description or "").split("\n"):
        line = line.strip()
        if not line or line.startswith(("✔", "-", "•")) or line.startswith("ברשימת הרכיבים"):
            continue
        if any(keyword in line for keyword in USAGE_KEYWORDS):
            line = _strip_marks(line)
            lines.append(line if len(line) <= 280 else line[:280].rsplit(" ", 1)[0] + "…")
    return lines[:6]


def dekel_frequency(lines):
    for line in lines:
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            if FREQUENCY_PATTERN.search(sentence):
                return sentence.strip()
    return ""


def guide_html(names, results, meta):
    actives = detect_actives(names, results, (meta or {}).get("key_ingredients", ()))
    icon, label, source = usage_time(names, results, actives, meta)
    dekel_lines = dekel_usage_lines((meta or {}).get("description", ""))

    frequency = dekel_frequency(dekel_lines)
    frequency_source = "לפי דקל"
    if not frequency:
        frequency = usage_frequency(actives)
        frequency_source = "הערכה כללית"

    advice = mixing_advice(actives)
    if advice:
        mix = "".join(
            f'<div class="g-mix"><span>⚠️</span><div><b>{html.escape(title)}</b>'
            f"<p>{html.escape(text)}</p></div></div>"
            for title, text in advice
        )
    else:
        mix = (
            '<div class="g-mix ok"><span>✅</span><div><b>לא זוהו שילובים בעייתיים</b>'
            "<p>לא נמצאו במוצר פעילים חזקים שדורשים הפרדה.</p></div></div>"
        )

    active_chips = "".join(f"<span>{html.escape(ACTIVE_NAMES[a])}</span>" for a in sorted(actives))
    active_row = f'<div class="g-actives">פעילים בולטים: {active_chips}</div>' if actives else ""

    instructions = ""
    if dekel_lines:
        items = "".join(f"<li>{html.escape(line)}</li>" for line in dekel_lines)
        instructions = (
            '<div class="g-instr"><div class="g-kicker">הוראות שימוש ואזהרות</div>'
            f'<ul class="g-list">{items}</ul><div class="g-src">מתוך עמוד המוצר אצל דקל</div></div>'
        )

    return (
        '<div class="guide"><div class="g-grid">'
        f'<div class="g-card"><div class="g-ico">{icon}</div><div><div class="g-kicker">מתי להשתמש</div>'
        f'<div class="g-title">{html.escape(label)}</div><div class="g-src">{html.escape(source)}</div></div></div>'
        f'<div class="g-card"><div class="g-ico">🗓️</div><div><div class="g-kicker">באיזו תדירות</div>'
        f'<div class="g-title g-small">{html.escape(frequency)}</div><div class="g-src">{html.escape(frequency_source)}</div></div></div>'
        "</div>"
        f"{instructions}{active_row}"
        '<div class="g-kicker g-sep">עם מה לא כדאי לשלב</div>'
        f"{mix}"
        '<div class="g-note">הנחיות כלליות להכוונה בלבד ואינן ייעוץ רפואי. כדאי לפעול לפי ההוראות על האריזה ולהתייעץ עם רופא/ת עור, במיוחד בהיריון, בהנקה ובעור רגיש.</div>'
        "</div>"
    )


def search_dekel(ingredient):
    return search_many([ingredient])[ingredient]


# ======================
# UI
# ======================

def _logo_uri():
    path = Path(__file__).parent / "assets" / "logo.svg"
    try:
        return "data:image/svg+xml;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""


def brand_html(size="md"):
    return (
        f'<div class="brand {size}"><img class="brand-mark" alt="" src="{_logo_uri()}">'
        '<div class="brand-name"><b>Dekel</b> Checker<small>SKINCARE LAB</small></div></div>'
    )


THUMBS_PATH = Path(__file__).parent / "data" / "thumbs.json"


@st.cache_resource(show_spinner=False)
def load_thumbs():
    try:
        return json.loads(THUMBS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def thumb_uri(slug):
    data = load_thumbs().get(slug or "")
    return f"data:image/webp;base64,{data}" if data else ""


def cx_img(slug):
    uri = thumb_uri(slug)
    return f'<img class="cx-img" alt="" src="{uri}">' if uri else ""


def picture_uri(product):
    """תמונת מוצר: מהחיפוש אם קיימת, ואם לא מהמאגר המקומי."""
    picture = (product or {}).get("picture", "")
    if picture and re.fullmatch(r"[A-Za-z0-9+/=]+", picture):
        mime = "image/webp" if picture.startswith("UklGR") else "image/png" if picture.startswith("iVBOR") else "image/jpeg"
        return f"data:{mime};base64,{picture}"
    return thumb_uri((product or {}).get("slug"))


def showcase_slugs(count, offset=0):
    """מוצרים מאושרים עם תמונה, בסדר יציב שמתחלף מדי יום."""
    thumbs = load_thumbs()
    verified = sorted(e["slug"] for e in (load_catalog_index() or {}).get("products", []) if e.get("verified") and e["slug"] in thumbs)
    if not verified:
        return []
    shift = (datetime.date.today().toordinal() * 7 + offset) % len(verified)
    rotated = verified[shift:] + verified[:shift]
    return rotated[:count]


def marquee_html():
    by_slug = routine_entries_by_slug()

    def row(slugs, reverse):
        items = "".join(
            f'<a class="mq-item" data-slug="{html.escape(slug)}" target="_blank" rel="noopener" title="{html.escape(by_slug[slug]["name"])}" '
            f'href="https://thedekel.co.il/product-verified/?slug={quote(slug)}"><figure><img alt="{html.escape(by_slug[slug]["name"])}" src="{thumb_uri(slug)}"><figcaption>'
            f'{html.escape(by_slug[slug]["company"].split("|")[-1].strip())}</figcaption></figure></a>'
            for slug in slugs if slug in by_slug
        )
        return f'<div class="mq-row{" rev" if reverse else ""}"><div class="mq-track">{items}{items}</div></div>'

    slugs = showcase_slugs(26)
    if len(slugs) < 8:
        return ""
    return f'<div class="marquee">{row(slugs[:13], False)}{row(slugs[13:], True)}</div>'


def hero_html():
    mascot = (
        f'<div class="hero-mascot"><i></i><img alt="" src="data:image/webp;base64,{_mascot_b64}"></div>'
        if _mascot_b64 else ""
    )
    user = current_user()
    hello = cloud.greeting() + (f", {user['name']}" if user and user["name"] else "")
    return (
        '<div class="hero">' + mascot +
        f'<div class="greet">{html.escape(hello)}</div>'
        '<div class="badge"><i></i>THE INGREDIENT ATELIER</div>'
        '<h1>מה באמת<br>יש במוצר שלך?</h1>'
        '<p>מצלמים, והמערכת קוראת כל רכיב, מדרגת אותו לפי דקל ומתאימה אותו אלייך.</p>'
        '<div class="steps"><span><b>01</b> צילום</span><i class="arr">←</i>'
        '<span><b>02</b> סריקה</span><i class="arr">←</i><span><b>03</b> הבנה</span></div></div>'
    )


def stats_html():
    index = load_catalog_index() or {}
    products = index.get("products", [])
    verified = sum(1 for entry in products if entry.get("verified"))
    ingredients = len(load_ingredient_db().get("ingredients", {}))
    items = (
        (len(products), "מוצרים בקטלוג"), (verified, "מאושרי Dekel Verified"), (ingredients, "רכיבים מדורגים"),
    )
    cells = "".join(
        f'<div class="stat st{n}" style="--i:{n}"><b class="num" data-t="{value}">{value}</b><span>{html.escape(label)}</span></div>'
        for n, (value, label) in enumerate(items)
    )
    return f'<div class="stats">{cells}</div>'



if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0
if "manual_key" not in st.session_state:
    st.session_state.manual_key = 0


def reset_all():
    st.session_state.results = {}
    st.session_state.ocr_ingredients_text = ""
    st.session_state.selected_ingredient = None
    st.session_state.product_candidates = []
    st.session_state.product_source = None
    st.session_state.product_status = None
    st.session_state.selected_product = None
    st.session_state.product_meta = None
    st.session_state.celebrate_id = None
    st.session_state.result_popup = False
    st.session_state.photo_overlay = None
    st.session_state.nv_product = None
    st.session_state.product_hits = None
    st.session_state.nf_pending = None
    st.session_state.ing_choices = []
    st.session_state.product_lines = []
    st.session_state.product_pool = {}
    st.session_state.product_rejected = []
    st.session_state.product_queries_used = []
    st.session_state.product_obf_tried = False
    st.session_state.uploader_key += 1
    st.session_state.manual_key += 1


def current_user():
    """פרטי המשתמשת המחוברת: {email, name} או None. עובד גם כשהכניסה עם גוגל לא הוגדרה."""
    try:
        user = st.user
        if getattr(user, "is_logged_in", False):
            return {"email": user.get("email", ""), "name": cloud.first_name(user)}
    except Exception:
        pass
    return None


def auth_configured():
    try:
        return "auth" in st.secrets and bool(st.secrets["auth"].get("client_id"))
    except Exception:
        return False


def supabase_conf():
    try:
        conf = st.secrets["supabase"]
        return conf["url"], conf["key"]
    except Exception:
        return None


def login_button(label="התחברות עם גוגל", key="login_btn"):
    if st.button(label, key=key, icon=":material/login:"):
        if auth_configured():
            st.login()
        else:
            st.toast("ההתחברות עם גוגל עדיין לא הופעלה באתר. היא תעבוד אחרי הגדרת החשבון.", icon="ℹ️")


def ensure_account_state():
    """מאתחלת את מפתחות המצב האישיים לפני שהכניסה טוענת אותם (הכניסה רצה לפני שאר האיתחול)."""
    state = st.session_state
    if "profile" not in state:
        state.profile = skinprofile.decode(st.query_params.get("profile", ""))
    if "routine" not in state:
        state.routine = [slug for slug in st.query_params.get("routine", "").split(",") if slug]
    state.setdefault("history", [])
    state.setdefault("custom_products", {})


def load_account_data(user):
    ensure_account_state()
    """טוענת פרופיל, שגרה והיסטוריה של המשתמשת המחוברת פעם אחת בכל הפעלה."""
    if st.session_state.get("cloud_loaded") == user["email"]:
        return
    st.session_state.cloud_loaded = user["email"]
    conf = supabase_conf()
    if not conf or not user["email"]:
        return
    data = cloud.load(conf[0], conf[1], user["email"])
    if not data:
        st.session_state.cloud_hash = None
        return
    profile, routine, history, custom = cloud.unpack(data)
    if custom:
        st.session_state.custom_products = custom
    if profile:
        st.session_state.profile = skinprofile.normalize(profile)
    if routine:
        st.session_state.routine = routine
    if history:
        st.session_state.history = history
    st.session_state.cloud_hash = json.dumps(cloud.pack(st.session_state.profile, st.session_state.routine, st.session_state.history, st.session_state.custom_products), sort_keys=True)


def sync_account_data():
    ensure_account_state()
    """שומרת בענן אם משהו השתנה מאז השמירה האחרונה. נקראת בסוף כל הרצה."""
    user = current_user()
    conf = supabase_conf()
    if not user or not conf or not user["email"] or st.session_state.get("cloud_loaded") != user["email"]:
        return
    payload = cloud.pack(st.session_state.profile, st.session_state.routine, st.session_state.history, st.session_state.custom_products)
    digest = json.dumps(payload, sort_keys=True)
    if digest != st.session_state.get("cloud_hash"):
        if cloud.save(conf[0], conf[1], user["email"], payload):
            st.session_state.cloud_hash = digest


def render_gate():
    """מסך כניסה: רקע שחור, הדמות של דקל עם תג Dekel Verified בגדול, וכפתור כניסה."""
    try:
        st.html(
            f"<script>{(Path(__file__).parent / 'fx.js').read_text(encoding='utf-8')}</script>",
            unsafe_allow_javascript=True,
        )
    except TypeError:
        pass
    stars = "".join(f'<i style="--x:{x}%;--y:{y}%;--d:{d}s"></i>' for x, y, d in (
        (8, 14, 0), (18, 62, 1.2), (27, 28, 2.1), (36, 80, 0.6), (47, 10, 1.8), (58, 70, 2.6), (66, 22, 0.9),
        (74, 86, 1.5), (84, 38, 2.3), (92, 12, 0.3), (12, 90, 1.9), (88, 66, 0.8), (52, 46, 2.9), (4, 44, 1.1),
        (96, 90, 2.0), (30, 8, 2.7), (70, 6, 1.4), (62, 96, 0.5),
    ))
    mascot = f'<img class="gate-mascot" alt="Dekel Verified" src="data:image/webp;base64,{_mascot_b64}">' if _mascot_b64 else ""
    st.markdown(
        '<div class="gate"><div class="gate-dust">' + stars + '</div>'
        '<div class="gate-stage"><div class="gate-rays"></div><div class="gate-glow"></div>'
        '<i class="gate-ring"></i><i class="gate-ring r2"></i><i class="gate-ring r3"></i>' + mascot + '</div>'
        '<div class="gate-title"><b>DEKEL</b> CHECKER</div>'
        '<div class="gate-sub">בדיקת רכיבים · זיהוי מוצרים · התאמה אישית</div></div>',
        unsafe_allow_html=True,
    )
    with st.container(key="enter"):
        if st.button("כניסה לאתר", key="enter_btn"):
            st.session_state.entered = True
            st.rerun()
    with st.container(key="gate_login"):
        login_button("התחברות עם גוגל", key="gate_login_btn")


if "entered" not in st.session_state:
    st.session_state.entered = False
_user = current_user()
if _user:
    st.session_state.entered = True
    load_account_data(_user)
if not st.session_state.entered:
    render_gate()
    st.markdown('<div class="copyright gate-copy">© כל הזכויות שמורות לנטע ונטוררו שבירו</div>', unsafe_allow_html=True)
    st.stop()

st.markdown(brand_html(), unsafe_allow_html=True)
top_left, top_right, _, top_account = st.columns([1, 1, 1, 1.3])
top_left.button("🔄 התחלה מחדש", on_click=reset_all, key="restart")
top_right.link_button("🌐 אתר דקל", "https://thedekel.co.il/#milonid")
with top_account:
    _who = current_user()
    if _who:
        if st.button(f"👤 {_who['name'] or 'החשבון שלי'} · התנתקות", key="top_logout"):
            st.session_state.cloud_loaded = None
            st.logout()
    else:
        login_button("התחברות", key="top_login_btn")


try:
    st.html(
        f"<script>{(Path(__file__).parent / 'fx.js').read_text(encoding='utf-8')}</script>",
        unsafe_allow_javascript=True,
    )
except TypeError:
    pass

if "ocr_ingredients_text" not in st.session_state:
    st.session_state.ocr_ingredients_text = ""
if "selected_ingredient" not in st.session_state:
    st.session_state.selected_ingredient = None
if "results" not in st.session_state:
    st.session_state.results = {}
if "product_candidates" not in st.session_state:
    st.session_state.product_candidates = []
if "product_source" not in st.session_state:
    st.session_state.product_source = None
if "product_status" not in st.session_state:
    st.session_state.product_status = None
if "selected_product" not in st.session_state:
    st.session_state.selected_product = None
if "product_meta" not in st.session_state:
    st.session_state.product_meta = None
if "celebrate_id" not in st.session_state:
    st.session_state.celebrate_id = None
if "nv_product" not in st.session_state:
    st.session_state.nv_product = None
if "celebrate_kind" not in st.session_state:
    st.session_state.celebrate_kind = "yes"
if "product_lines" not in st.session_state:
    st.session_state.product_lines = []
if "product_pool" not in st.session_state:
    st.session_state.product_pool = {}
if "product_rejected" not in st.session_state:
    st.session_state.product_rejected = []
if "product_queries_used" not in st.session_state:
    st.session_state.product_queries_used = []
if "product_obf_tried" not in st.session_state:
    st.session_state.product_obf_tried = False
if "product_hits" not in st.session_state:
    st.session_state.product_hits = None
if "nf_pending" not in st.session_state:
    st.session_state.nf_pending = None
if "ing_choices" not in st.session_state:
    st.session_state.ing_choices = []
if "auto_scan" not in st.session_state:
    st.session_state.auto_scan = False
if "result_popup" not in st.session_state:
    st.session_state.result_popup = False
if "custom_products" not in st.session_state:
    st.session_state.custom_products = {}
if "routine_check" not in st.session_state:
    st.session_state.routine_check = None
if "photo_overlay" not in st.session_state:
    st.session_state.photo_overlay = None
if "history" not in st.session_state:
    st.session_state.history = []
if "photo_thumb" not in st.session_state:
    st.session_state.photo_thumb = None
if "photo_source" not in st.session_state:
    st.session_state.photo_source = False
if "profile" not in st.session_state:
    st.session_state.profile = skinprofile.decode(st.query_params.get("profile", ""))
if "routine" not in st.session_state:
    # השגרה נשמרת בכתובת (ובדפדפן), כדי שתחזור בביקור הבא
    st.session_state.routine = [slug for slug in st.query_params.get("routine", "").split(",") if slug]


def save_profile():
    encoded = skinprofile.encode(st.session_state.profile)
    if encoded:
        st.query_params["profile"] = encoded
    elif "profile" in st.query_params:
        del st.query_params["profile"]


def save_routine():
    if st.session_state.routine:
        st.query_params["routine"] = ",".join(st.session_state.routine)
    elif "routine" in st.query_params:
        del st.query_params["routine"]

if st.session_state.celebrate_id:
    st.markdown(
        f'<div class="celebrate-marker" data-id="{st.session_state.celebrate_id}" '
        f'data-kind="{st.session_state.celebrate_kind}"></div>',
        unsafe_allow_html=True,
    )


ICON_MAP = {
    "📸": "photo_camera", "📝": "edit_note", "📊": "insights", "🔎": "search", "📦": "inventory_2", "🏷️": "sell",
    "➕": "add_circle", "🧴": "water_drop", "📅": "event_available", "⚠️": "warning", "🕘": "history",
    "🎟️": "local_activity", "🤝": "handshake", "⚡": "bolt", "🗂️": "category", "🧾": "receipt_long",
    "🔬": "biotech", "🧭": "explore", "📋": "checklist", "🗓️": "calendar_month", "🧬": "face", "🧪": "science",
}


def msr(icon):
    """אייקון Material בצבע הערכה; אם אין התאמה נשאר האימוג'י."""
    name = ICON_MAP.get(icon)
    return f'<span class="msr">{name}</span>' if name else icon


def section(icon, title, subtitle="", index=""):
    sub = f'<div class="s">{html.escape(subtitle)}</div>' if subtitle else ""
    num = f'<div class="n">{html.escape(index)}</div>' if index else ""
    st.markdown(
        f'<div class="sec"><div class="ico">{msr(icon)}</div>'
        f'<div><div class="t">{html.escape(title)}</div>{sub}</div>{num}</div>',
        unsafe_allow_html=True,
    )


@st.cache_resource(show_spinner="טוענת מנוע זיהוי טקסט (רק בפעם הראשונה)...")
def get_ocr_reader():
    return easyocr.Reader(["en"], gpu=False)


@st.cache_data(show_spinner=False)
def extract_text(image_bytes):
    """מחזירה (טקסט, תיבות): תיבות הטקסט באחוזים מגודל התמונה, להצגה על התמונה."""
    image = open_image(io.BytesIO(image_bytes))
    processed_image = preprocess_for_ocr(image)
    results = get_ocr_reader().readtext(np.array(processed_image), detail=1, canvas_size=MAX_OCR_SIDE)
    width, height = processed_image.size
    boxes = []
    for box, text, _confidence in results:
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        boxes.append({
            "x": max(0.0, min(xs)) / width * 100, "y": max(0.0, min(ys)) / height * 100,
            "w": (max(xs) - min(xs)) / width * 100, "h": (max(ys) - min(ys)) / height * 100, "text": text,
        })
    extracted_text = " ".join(text for _box, text, _c in results)
    # מסירה כותרות "Ingredients" שנדבקות לרכיב הראשון בלי פסיק
    cleaned = re.sub(
        r"^[\W_]*(?:(?:ingredients?|ingr[eé]dients?)[\s:/]*|\d+\s+)*",
        "",
        extracted_text,
        flags=re.IGNORECASE,
    )
    return cleaned, boxes


MAX_OCR_SIDE = 1920


def open_image(source):
    """פותחת תמונה (גם גדולה מהטלפון), מיישרת לפי EXIF ומקטינה לגודל בטוח לזיכרון."""
    image = Image.open(source)
    image = ImageOps.exif_transpose(image).convert("RGB")
    longest = max(image.size)
    if longest > MAX_OCR_SIDE * 1.25:
        scale = MAX_OCR_SIDE / longest
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    return image


def preview_image(image):
    preview = image.copy()
    preview.thumbnail((1200, 1200))
    return preview


def preprocess_for_ocr(image, max_side=None):
    # גודל אחיד וחסום (עד MAX_OCR_SIDE) + ניגודיות + חידוד: עוזרים ל-OCR לתפוס פסיקים
    # קטנים בלי לנפח את הזיכרון בתמונות גדולות מהטלפון
    image = image.convert("RGB")
    scale = min(2.0, (max_side or MAX_OCR_SIDE) / max(image.size))
    if abs(scale - 1) > 0.05:
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    image = ImageOps.grayscale(image)
    image = ImageOps.autocontrast(image, cutoff=1)
    image = image.filter(ImageFilter.SHARPEN)
    return image


def split_ingredients(text):
    # ה-OCR לפעמים קורא פסיק כנקודה-פסיק, ולפעמים מפספס לגמרי את
    # המפריד בין שני רכיבים - לכן יש תיבת עריכה ידנית מתחת לרשימה.
    # פסיק שאחריו ספרה (כמו ב"1,2-Hexanediol") לא נחשב מפריד.
    items = [
        ing.strip(" .")
        for ing in re.split(r",(?!\d)|;|\n", text)
        if len(ing.strip(" .")) > 2
    ]
    return list(dict.fromkeys(items))


def result_card_html(name, result):
    kind = EMOJI_CLASS.get(result["emoji"], "other")
    grade = grade_class(result)
    chip = f'<span class="chip {kind} {grade}">{html.escape(result["emoji"])} {html.escape(result["rating"])}</span>'
    role = f'<div class="rc-role">{html.escape(result["role"])}</div>' if result.get("role") else ""

    description = result.get("description") or ""
    if not description and not result.get("role"):
        description = result["text"].split("\n", 1)[-1] if result["emoji"] in ("⚪", "🟢", "🟡", "🔴") else result["text"]
    paragraphs = "".join(
        f"<p>{html.escape(line)}</p>" for line in description.split("\n") if line.strip()
    )
    notes = "".join(f"<span>{html.escape(n)}</span>" for n in result.get("notes", []))
    notes = f'<div class="rc-notes">{notes}</div>' if notes else ""

    return (
        f'<div class="rcard {kind} {grade}"><div class="rc-head">'
        f'<span class="rc-name">{html.escape(name)}</span>{chip}</div>'
        f"{role}{paragraphs}{notes}</div>"
    )


def render_result(name, result):
    st.markdown(result_card_html(name, result), unsafe_allow_html=True)


def show_result(ingredient):
    if ingredient not in st.session_state.results:
        with st.spinner(f"סורקת את {ingredient}..."):
            st.session_state.results.update(search_many([ingredient]))
    render_result(ingredient, st.session_state.results[ingredient])


def scanner_html(done, total, recent, finished=False):
    percent = int(done / total * 100) if total else 0
    chips = "".join(f'<span style="--i:{i}">{html.escape(name)}</span>' for i, name in enumerate(recent[-8:]))
    title = "✓ הסריקה הושלמה" if finished else "🔬 סורקת את הרכיבים"
    return (
        f'<div class="scanner{" done" if finished else ""}"><div class="sc-laser"></div>'
        f'<div class="sc-head">{title} <b>{done}/{total}</b></div>'
        f'<div class="sc-bar"><i style="width:{percent}%"></i></div>'
        f'<div class="sc-chips">{chips}</div></div>'
    )


def check_all(ingredients):
    todo = [ing for ing in ingredients if ing not in st.session_state.results]
    if not todo:
        return
    started = time.time()
    panel = st.empty()
    panel.markdown(scanner_html(0, len(todo), ["מתחילה..."]), unsafe_allow_html=True)
    recent = []

    def on_progress(done, total, name):
        recent.append(name)
        panel.markdown(scanner_html(done, total, recent), unsafe_allow_html=True)

    st.session_state.results.update(search_many(todo, on_progress))
    panel.markdown(scanner_html(len(todo), len(todo), recent, finished=True), unsafe_allow_html=True)
    time.sleep(0.9)
    panel.empty()
    st.caption(f"נסרקו {len(todo)} רכיבים תוך {time.time() - started:.0f} שניות")


SCORE_WEIGHTS = {"מעולה": 100, "טוב": 95, "עובר": 85, "על תנאי": 55, "רע": 15}

FLAG_INFO = {
    "sun": ("☀️", "מגביר רגישות לשמש", "כדאי להקפיד על קרם הגנה ביום."),
    "pregnancy": ("🤰", "לא מומלץ בהיריון", "לפי המילון של דקל. כדאי להתייעץ עם רופא/ה."),
    "breastfeeding": ("🍼", "לא מומלץ בהנקה", "לפי המילון של דקל. כדאי להתייעץ עם רופא/ה."),
}


def product_score(results):
    rated = [r for r in results if r["rating"] in SCORE_WEIGHTS]
    if not rated:
        return None
    average = sum(SCORE_WEIGHTS[r["rating"]] for r in rated) / len(rated)
    bad = sum(r["rating"] == "רע" for r in rated)
    return max(0, round(average - 5 * bad)), len(rated)


def score_verdict(score):
    if score >= 85:
        return "good", "מוצר נקי ומומלץ"
    if score >= 70:
        return "good", "מוצר טוב, עם כמה נקודות קטנות"
    if score >= 50:
        return "caution", "כדאי לשים לב לכמה רכיבים"
    return "bad", "כדאי לבדוק את המוצר היטב"


def score_card_html(results):
    scored = product_score(results)
    if scored is None:
        return ""
    score, rated = scored
    kind, verdict = score_verdict(score)
    good = sum(r["emoji"] == "🟢" for r in results)
    caution = sum(r["emoji"] == "🟡" for r in results)
    bad = sum(r["emoji"] == "🔴" for r in results)
    unrated = len(results) - rated
    extra = f" ל-{unrated} רכיבים אין דירוג באתר ולכן לא נכללו בציון." if unrated else ""
    return (
        f'<div class="score-card {kind}" data-score="{score}">'
        f'<div class="ring" style="--score:{score}"><div class="val"><span class="num" data-t="{score}">{score}</span>'
        '<small>/100</small></div></div>'
        '<div class="score-text">'
        '<div class="score-kicker">ציון משוער למוצר</div>'
        f'<div class="score-verdict">{html.escape(verdict)}</div>'
        f'<div class="score-detail">{good} רכיבים טובים, {caution} על תנאי, {bad} רעים.{html.escape(extra)}</div>'
        '<div class="score-note">הציון מחושב על ידי האפליקציה לפי הדירוגים של דקל, והוא להערכה בלבד.</div>'
        "</div></div>"
    )


def warnings_card_html(checked, results):
    rows = []
    for code, (icon, title, hint) in FLAG_INFO.items():
        names = [ing for ing, r in zip(checked, results) if code in r.get("flags", [])]
        if not names:
            continue
        chips = "".join(f"<span>{html.escape(n)}</span>" for n in names)
        rows.append(
            f'<div class="warn-row"><div class="warn-ico">{icon}</div><div>'
            f'<div class="warn-title">{html.escape(title)} <b>({len(names)})</b></div>'
            f'<div class="warn-chips">{chips}</div>'
            f'<div class="warn-hint">{html.escape(hint)}</div></div></div>'
        )
    if not rows:
        return ""
    return '<div class="warn-card"><div class="warn-head">⚡ אזהרות ברמת המוצר</div>' + "".join(rows) + "</div>"


@st.dialog("🧬 הפרופיל שלי", width="large")
def profile_dialog():
    current = skinprofile.normalize(st.session_state.profile)
    st.caption("ספרי לנו על העור שלך, ונתאים את הסיכום והאזהרות אליך. הפרטים נשמרים רק בדפדפן שלך.")
    skin = st.segmented_control(
        "סוג העור", list(skinprofile.SKIN_TYPES), default=current["skin"] or None,
        format_func=lambda key: skinprofile.SKIN_TYPES[key], key="pf_skin",
    )
    concerns = st.multiselect(
        "מה מטריד אותך?", list(skinprofile.CONCERNS), default=current["concerns"],
        format_func=lambda key: skinprofile.CONCERNS[key], key="pf_concerns",
    )
    preg = st.toggle("🤰 בהיריון או מניקה", value=current["preg"], key="pf_preg")
    avoid = st.text_input(
        "רכיבים שאת רוצה להימנע מהם (באנגלית, מופרדים בפסיק)", value=", ".join(current["avoid"]),
        placeholder="parfum, alcohol, coconut", key="pf_avoid",
    )
    save_col, clear_col = st.columns(2)
    if save_col.button("💾 שמרי", type="primary", key="pf_save", use_container_width=True):
        st.session_state.profile = skinprofile.normalize({
            "skin": skin or "", "concerns": concerns, "preg": preg,
            "avoid": [part for part in avoid.split(",")],
        })
        save_profile()
        st.rerun()
    if clear_col.button("🗑️ נקי פרופיל", key="pf_clear", use_container_width=True):
        st.session_state.profile = skinprofile.normalize({})
        save_profile()
        st.rerun()


def profile_chips_html(profile):
    profile = skinprofile.normalize(profile)
    chips = []
    if profile["skin"]:
        chips.append(skinprofile.SKIN_TYPES[profile["skin"]])
    chips += [skinprofile.CONCERNS[c] for c in profile["concerns"]]
    if profile["preg"]:
        chips.append("היריון/הנקה")
    chips += [f"בלי {a}" for a in profile["avoid"]]
    return "".join(f"<span>{html.escape(c)}</span>" for c in chips)


def personal_card_html(checked, results_map, meta):
    if skinprofile.is_empty(st.session_state.profile):
        return ""
    actives = detect_actives(checked, results_map, (meta or {}).get("key_ingredients", ()))
    notes = skinprofile.personal_notes(st.session_state.profile, checked, results_map, actives)
    if not notes:
        return ""
    rows = "".join(
        f'<div class="pn {level}"><span>{icon}</span><p>{html.escape(text)}</p></div>' for icon, text, level in notes
    )
    return (
        '<div class="personal"><div class="pn-head">🧬 בשבילך'
        f'<div class="pn-chips">{profile_chips_html(st.session_state.profile)}</div></div>{rows}</div>'
    )


def ingredient_table_html(checked):
    rows = "".join(
        f'<tr style="--i:{min(index, 14)}">'
        f'<td class="ing">{html.escape(ing)}</td>'
        f'<td><span class="chip {EMOJI_CLASS.get(r["emoji"], "other")} {grade_class(r)}">'
        f'{html.escape(r["emoji"])} {html.escape(r["rating"])}</span></td>'
        f'<td>{html.escape(r["role"])}</td>'
        "</tr>"
        for index, (ing, r) in enumerate((ing, st.session_state.results[ing]) for ing in checked)
    )
    return (
        '<div class="table-wrap"><table class="ing-table">'
        "<thead><tr><th>רכיב</th><th>דירוג</th><th>תפקיד</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


@st.dialog("🧭 מדריך שימוש", width="large")
def guide_dialog(checked):
    st.markdown(
        guide_html(checked, st.session_state.results, st.session_state.product_meta), unsafe_allow_html=True
    )


@st.dialog("🗂️ מה כל רכיב עושה", width="large")
def groups_dialog(checked):
    st.markdown(usage_groups_html(checked, st.session_state.results), unsafe_allow_html=True)


@st.dialog("🧾 כל הרכיבים", width="large")
def table_dialog(checked):
    st.markdown(ingredient_table_html(checked), unsafe_allow_html=True)


@st.dialog("🔬 התוצאה שלך", width="large")
def result_dialog(names):
    checked = [name for name in names if name in st.session_state.results]
    results = [st.session_state.results[name] for name in checked]
    product = st.session_state.selected_product
    if product and product.get("source") != "obf":
        st.markdown(product_stage_html(product), unsafe_allow_html=True)
        matches = product_coupons(product)
        if matches:
            st.markdown('<div class="alert-strip hot"><span class="as-bell">🎟️</span><div><b>יש קופון פעיל למוצר הזה</b>'
                        f'<small>{html.escape(coupon_line(matches[0]))}</small></div></div>', unsafe_allow_html=True)
    for card in (
        score_card_html(results),
        key_points_html(checked, st.session_state.results, st.session_state.product_meta),
        personal_card_html(checked, st.session_state.results, st.session_state.product_meta),
        warnings_card_html(checked, results),
    ):
        if card:
            st.markdown(card, unsafe_allow_html=True)
    st.caption("סגרי את החלון כדי לראות את הפירוט המלא: מדריך שימוש, קבוצות וטבלה.")


def key_points_html(checked, results_map, meta):
    """כמה משפטים פשוטים בראש התוצאה, לפני כל הפירוט."""
    results = [results_map[name] for name in checked]
    actives = detect_actives(checked, results_map, (meta or {}).get("key_ingredients", ()))
    icon, label, _source = usage_time(checked, results_map, actives, meta)
    good = sum(r["emoji"] == "🟢" for r in results)
    points = [("🧪", f"{good} מתוך {len(results)} רכיבים מדורגים טוב"), (icon, label)]

    advice = mixing_advice(actives)
    if advice:
        points.append(("⚠️", advice[0][0]))
    flags = {flag for r in results for flag in r.get("flags", [])}
    if "pregnancy" in flags:
        points.append(("🤰", "יש רכיבים שלא מומלצים בהיריון"))
    elif "sun" in flags:
        points.append(("☀️", "יש רכיבים שמגבירים רגישות לשמש, חשוב קרם הגנה"))

    items = "".join(
        f'<div class="kp" style="--i:{index}"><span>{point_icon}</span><b>{html.escape(text)}</b></div>'
        for index, (point_icon, text) in enumerate(points[:4])
    )
    return f'<div class="kps">{items}</div>'


TONE_RANK = {"bad": 3, "caution": 2, "good": 1, "other": 0}


def _norm_text(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def match_boxes(boxes, ingredients, results_map):
    """לכל תיבת טקסט: הרכיבים שהיא מכילה, והגוון של הגרוע שביניהם. [(תיבה, גוון, [רכיבים])]"""
    normalized = [(name, _norm_text(name)) for name in ingredients if len(_norm_text(name)) >= 3]
    matched = []
    for box in boxes:
        text = _norm_text(box["text"])
        if len(text) < 3:
            continue
        hits = [name for name, key in normalized if key in text or (len(text) >= 5 and text in key)]
        if not hits:
            continue
        worst = max(hits, key=lambda name: TONE_RANK.get(EMOJI_CLASS.get(results_map[name]["emoji"], "other"), 0))
        matched.append((box, _tone(results_map[worst]), hits))
    return matched


def live_photo_html(overlay, checked, results_map):
    matched = match_boxes(overlay["boxes"], checked, results_map)
    if not matched:
        return ""
    img = base64.b64encode(overlay["img"]).decode("ascii")
    try:
        with Image.open(io.BytesIO(overlay["img"])) as im:
            ratio = im.width / im.height
    except Exception:
        ratio = 1.0
    marks = "".join(
        f'<i class="hl {tone}" style="left:{box["x"]:.2f}%;top:{box["y"]:.2f}%;width:{box["w"]:.2f}%;height:{box["h"]:.2f}%;--i:{index}" '
        f'title="{html.escape(", ".join(hits))}"></i>'
        for index, (box, tone, hits) in enumerate(matched)
    )
    found = len({name for _b, _t, hits in matched for name in hits})
    return (
        f'<div class="live reveal"><div class="live-frame" style="--ar:{ratio:.3f}"><img alt="" src="data:image/jpeg;base64,{img}">{marks}'
        '<div class="live-laser"></div></div>'
        f'<div class="live-cap">זוהו {found} מתוך {len(checked)} רכיבים על התמונה. הצבע מראה את הדירוג של כל רכיב.</div></div>'
    )


def chapter_html(number, title, subtitle=""):
    sub = f'<div class="chap-s">{html.escape(subtitle)}</div>' if subtitle else ""
    return (
        f'<div class="chap reveal"><span class="chap-n">{number}</span>'
        f'<div><div class="chap-t">{html.escape(title)}</div>{sub}</div></div>'
    )


def _tone(result):
    return f'{EMOJI_CLASS.get(result["emoji"], "other")} {grade_class(result)}'.strip()


def spectrum_html(checked, results_map):
    segments = "".join(
        f'<i class="sg {_tone(results_map[name])}" style="--i:{index}" title="{html.escape(name)} · {html.escape(results_map[name]["rating"])}"></i>'
        for index, name in enumerate(checked)
    )
    return (
        '<div class="spectrum reveal"><div class="sp-bar">' + segments + '</div>'
        '<div class="sp-axis"><span>מתחילת הרשימה (הריכוז הגבוה)</span><span>סוף הרשימה</span></div></div>'
    )


def ribbons_html(checked, results_map, limit=12):
    rows = []
    for index, name in enumerate(checked[:limit]):
        result = results_map[name]
        role = result.get("role") or "ללא תיאור תפקיד"
        rows.append(
            f'<div class="rib reveal {_tone(result)}" style="--i:{index}"><span class="rib-n">{index + 1:02d}</span>'
            f'<div class="rib-main"><b>{html.escape(name)}</b><small>{html.escape(role)}</small></div>'
            f'<span class="chip {_tone(result)}">{html.escape(result["emoji"])} {html.escape(result["rating"])}</span></div>'
        )
    return '<div class="ribbons">' + "".join(rows) + "</div>"


def show_summary(ingredients):
    checked = [ing for ing in ingredients if ing in st.session_state.results]
    if not checked:
        return

    results_map = st.session_state.results
    results = [results_map[ing] for ing in checked]
    good = sum(r["emoji"] == "🟢" for r in results)
    caution = sum(r["emoji"] == "🟡" for r in results)
    bad = sum(r["emoji"] == "🔴" for r in results)
    other = len(results) - good - caution - bad

    st.markdown(chapter_html("01", "הציון", f"{len(results)} רכיבים נסרקו"), unsafe_allow_html=True)
    for card in (
        score_card_html(results),
        key_points_html(checked, results_map, st.session_state.product_meta),
        personal_card_html(checked, results_map, st.session_state.product_meta),
    ):
        if card:
            st.markdown(card, unsafe_allow_html=True)
    if skinprofile.is_empty(st.session_state.profile):
        if st.button(f"{NAV_ICONS['profile']} רוצה התאמה אישית? הגדירי פרופיל עור", key="pf_prompt", use_container_width=True):
            profile_dialog()

    step = ["02"]

    def next_chapter(title, subtitle=""):
        st.markdown(chapter_html(step[0], title, subtitle), unsafe_allow_html=True)
        step[0] = f"{int(step[0]) + 1:02d}"

    overlay = st.session_state.photo_overlay
    if overlay:
        live = live_photo_html(overlay, checked, results_map)
        if live:
            next_chapter("התמונה שלך, מוארת", "הרכיבים שזוהו על האריזה, בצבע הדירוג שלהם")
            st.markdown(live, unsafe_allow_html=True)

    next_chapter("ספקטרום הרכיבים", "כל פס הוא רכיב אחד, לפי הסדר ברשימה")
    st.markdown(spectrum_html(checked, results_map), unsafe_allow_html=True)
    st.markdown(
        '<div class="tiles">'
        f'<div class="tile good"><div class="num" data-t="{good}">{good}</div><div class="lbl">טובים</div></div>'
        f'<div class="tile caution"><div class="num" data-t="{caution}">{caution}</div><div class="lbl">על תנאי</div></div>'
        f'<div class="tile bad"><div class="num" data-t="{bad}">{bad}</div><div class="lbl">רעים</div></div>'
        f'<div class="tile other"><div class="num" data-t="{other}">{other}</div><div class="lbl">אחר</div></div>'
        "</div>",
        unsafe_allow_html=True,
    )
    if good:
        counts = {name: sum(r["rating"] == name for r in results) for name in GRADE_CLASS}
        pills = "".join(
            f'<span class="lg chip {GRADE_CLASS[name]}"><i></i>{name} <b>{counts[name]}</b></span>'
            for name in ("מעולה", "טוב", "עובר")
            if counts[name]
        )
        st.markdown(f'<div class="legend">הירוקים לפי דרגה: {pills}</div>', unsafe_allow_html=True)

    next_chapter("הרכיבים המובילים", "הרכיבים הראשונים ברשימה, שנמצאים בריכוז הגבוה ביותר")
    st.markdown(ribbons_html(checked, results_map), unsafe_allow_html=True)
    if len(checked) > 12:
        if st.button(f"{NAV_ICONS['list']} לכל {len(checked)} הרכיבים", key="open_table", use_container_width=True):
            table_dialog(checked)

    warn = warnings_card_html(checked, results)
    flagged = [ing for ing in checked if results_map[ing]["emoji"] in ("🟡", "🔴")]
    if warn or flagged:
        next_chapter("כדאי לשים לב", "אזהרות ורכיבים שמסומנים 'על תנאי' או 'רע'")
        if warn:
            st.markdown(warn, unsafe_allow_html=True)
        for ing in flagged:
            r = results_map[ing]
            with st.expander(f"{r['emoji']} {ing} · {r['rating']}"):
                render_result(ing, r)

    next_chapter("איך להשתמש", "מתי, באיזו תדירות ועם מה לא לשלב")
    guide_col, groups_col = st.columns(2)
    if guide_col.button(f"{NAV_ICONS['compat']} מדריך שימוש", key="open_guide", use_container_width=True):
        guide_dialog(checked)
    if groups_col.button("מה כל רכיב עושה", key="open_groups", use_container_width=True):
        groups_dialog(checked)


def coupon_badge(product):
    return '<span class="cp-badge">🎟️ קופון פעיל</span>' if product_coupons(product) else ""


def product_card_html(product):
    verified = product["verified"]
    seal = (
        '<span class="dv-seal"><i>✔</i> DEKEL VERIFIED</span>'
        if verified
        else '<span class="dv-no">לא עומד בתקן Dekel Verified</span>'
    )

    picture = product.get("picture", "")
    img = ""
    if picture and re.fullmatch(r"[A-Za-z0-9+/=]+", picture):
        mime = "image/webp" if picture.startswith("UklGR") else "image/png" if picture.startswith("iVBOR") else "image/jpeg"
        img = (
            f'<img class="pimg" alt="{html.escape(product["name"])}" title="לחצי להגדלה" '
            f'src="data:{mime};base64,{picture}">'
        )

    description = product["description"]
    limit = 260 if verified else 420
    if len(description) > limit:
        description = description[:limit].rsplit(" ", 1)[0] + "…"
    paragraphs = "".join(f"<p>{html.escape(line)}</p>" for line in description.split("\n") if line.strip())

    link = ""
    if verified and product.get("slug"):
        url = "https://thedekel.co.il/product-verified/?slug=" + quote(product["slug"])
        link = f'<a class="plink" href="{html.escape(url)}" target="_blank" rel="noopener">לעמוד המוצר באתר דקל ↗</a>'

    name2 = f'<div class="pname2">{html.escape(product["name2"])}</div>' if product.get("name2") else ""
    mascot = '<span class="dv-mascot" role="img" aria-label="Dekel Verified"></span>' if verified else ""
    return (
        f'<div class="pcard {"verified" if verified else "notverified"}">{mascot}{img}<div class="pbody">'
        f'<div class="pco">{html.escape(product["company"])}</div>'
        f'<div class="pname">{html.escape(product["name"])}</div>{name2}'
        f'<div class="pseal">{seal}{coupon_badge(product)}</div>{paragraphs}{link}</div></div>'
    )


def _remember_pool(products):
    pool = st.session_state.product_pool
    for product in products:
        if product.get("slug"):
            pool[product["slug"]] = product


def identify_product(image_bytes):
    st.session_state.photo_thumb = make_thumb(image_bytes)
    st.session_state.photo_source = True
    st.session_state.product_candidates = []
    st.session_state.product_source = None
    st.session_state.product_pool = {}
    st.session_state.product_rejected = []
    st.session_state.product_queries_used = []
    st.session_state.product_obf_tried = False

    with st.spinner("קוראת את הטקסט מהתמונה..."):
        lines = ocr_lines(image_bytes)
    st.session_state.product_lines = lines
    if looks_like_ingredient_list(", ".join(line[0] for line in lines)):
        st.session_state.product_status = "wrong-list"
        return
    queries = build_queries(lines)
    if not queries:
        st.session_state.product_status = "no-text"
        return

    try:
        with st.spinner("מחפשת את המוצר בקטלוג של דקל..."):
            found = find_products(queries)
    except Exception:
        st.session_state.product_status = "error"
        return
    st.session_state.product_queries_used = list(queries)
    _remember_pool(found)

    candidates = rank_products(found, lines)
    source = "dekel"
    if not candidates:
        with st.spinner("לא נמצא בדקל, מחפשת במאגר פתוח..."):
            candidates = search_open_beauty_facts(" ".join(queries[0].split()[:8]))
        st.session_state.product_obf_tried = True
        source = "obf"

    st.session_state.product_candidates = candidates
    st.session_state.product_source = source
    st.session_state.product_status = "ok" if candidates else "none"


def continue_search():
    """המשתמשת סימנה שאף התאמה לא נכונה: ממשיכים לחפש, בלי להציג שוב את מה שנדחה."""
    if st.session_state.product_source == "dekel":
        rejected = set(st.session_state.product_rejected)
        rejected.update(c["slug"] for c in st.session_state.product_candidates if c.get("slug"))
        st.session_state.product_rejected = list(rejected)
    else:
        rejected = set(st.session_state.product_rejected)

    lines = st.session_state.product_lines
    candidates, source = [], "dekel"

    if st.session_state.product_source == "dekel":
        # 1) דירוג מתון יותר של מה שכבר נמצא
        candidates = rank_products(list(st.session_state.product_pool.values()), lines, min_matched=1, exclude=rejected)

        # 2) חיפוש בקטלוג עם שאילתות חדשות מהטקסט שבתמונה
        if not candidates:
            extra = build_extra_queries(lines, st.session_state.product_queries_used)
            if extra:
                try:
                    with st.spinner("ממשיכה לחפש בקטלוג של דקל..."):
                        found = find_products(extra)
                    st.session_state.product_queries_used += extra
                    _remember_pool(found)
                    candidates = rank_products(
                        list(st.session_state.product_pool.values()), lines, min_matched=1, exclude=rejected
                    )
                except Exception:
                    pass

        # 3) מאגר פתוח, פעם אחת
        if not candidates and not st.session_state.product_obf_tried:
            queries = build_queries(lines)
            if queries:
                with st.spinner("לא נמצא בדקל, מחפשת במאגר פתוח..."):
                    candidates = search_open_beauty_facts(" ".join(queries[0].split()[:8]))
                source = "obf"
            st.session_state.product_obf_tried = True

    st.session_state.product_candidates = candidates
    st.session_state.product_source = source if candidates else st.session_state.product_source
    st.session_state.product_status = "ok" if candidates else "exhausted"
    st.rerun()


CONFLICTS_URL = "https://thedekel.co.il/ingredients_conflicts/"
TAB_INGREDIENTS, TAB_PRODUCT, TAB_COMPAT, TAB_COUPONS = "📋 צילום רשימת רכיבים", "📦 צילום המוצר עצמו", "🤝 שילובים", "🎟️ קופונים"
TAB_ROUTINE = "🗓️ השגרה שלי"
TAB_HISTORY = "🕘 היסטוריה"
TAB_HOME = "🏠 בית"


@st.cache_data(show_spinner=False)
def product_search_options():
    """אפשרויות להשלמה אוטומטית של חיפוש מוצר: שמות (בעברית ובאנגלית) מהקטלוג המקומי."""
    options = {}
    for entry in (load_catalog_index() or {}).get("products", []):
        brand = entry["company"].split("|")[-1].strip()
        for name in (entry["name"], entry.get("name2", "")):
            if name:
                options.setdefault(f"{name} · {brand}" if brand else name, name)
    return options


@st.cache_data(show_spinner=False)
def ingredient_search_options():
    return ingredient_options(load_ingredient_db()["ingredients"])


def _name_tokens(text):
    return {t for t in re.findall(r"[a-z0-9\u0590-\u05ff]+", (text or "").lower()) if t not in STOPWORDS}


def _product_text(product):
    return f"{product.get('name', '')} {product.get('name2', '')} {product.get('company', '')}"


def has_exact_match(term, products):
    """האם יש מוצר בקטלוג שכל מילות החיפוש מופיעות בשמו או במותג שלו."""
    wanted = _name_tokens(term)
    return bool(wanted) and any(wanted <= _name_tokens(_product_text(p)) for p in products)


def similar_products(term, found, limit=5):
    """מוצרים דומים: מה שהאתר החזיר, ואם אין, התאמה לפי מילים משותפות מהאינדקס המקומי."""
    if found:
        return found[:limit]
    wanted = _name_tokens(term)
    scored = []
    for entry in (load_catalog_index() or {}).get("products", []):
        if not entry.get("verified"):
            continue
        overlap = len(wanted & _name_tokens(_product_text(entry)))
        if overlap:
            scored.append((overlap, entry))
    scored.sort(key=lambda item: -item[0])
    return [
        {
            "name": e["name"], "name2": e.get("name2", ""), "company": e["company"], "description": "",
            "slug": e["slug"], "picture": "", "verified": True,
        }
        for _score, e in scored[:limit]
    ]


@st.dialog("המוצר לא נמצא בקטלוג")
def not_found_dialog(term, similar):
    st.warning(f"לא מצאתי בקטלוג של דקל מוצר בשם \"{term}\". ייתכן שהוא עדיין לא נבדק על ידי דקל.")
    if similar:
        st.caption("אלה מוצרים דומים שכן נמצאים בקטלוג:")
        for index, product in enumerate(similar):
            st.markdown(product_card_html(product), unsafe_allow_html=True)
            if st.button("✔ זה המוצר שלי", key=f"nf_pick_{index}"):
                choose_dekel_product(product)
    else:
        st.info("לא נמצאו מוצרים דומים. נסי לכתוב את שם המותג או חלק משם המוצר באנגלית.")


def flag_not_found(term, found):
    if not has_exact_match(term, found):
        st.session_state.nf_pending = (term, similar_products(term, found))


def search_by_typed_name(term):
    st.session_state.photo_source = False
    if term.count(",") >= 4:
        st.session_state.product_status = "wrong-list"
        st.rerun()
    try:
        with st.spinner("מחפשת בקטלוג של דקל..."):
            found = search_products(term)
    except Exception:
        st.session_state.product_status = "error"
        return
    rejected = set(st.session_state.product_rejected)
    candidates = [p for p in found if p.get("slug") not in rejected][:5]
    st.session_state.product_candidates = candidates
    st.session_state.product_source = "dekel"
    st.session_state.product_status = "ok" if candidates else "typed-none"
    flag_not_found(term, found)
    st.rerun()


def make_thumb(image_bytes, side=240):
    """תמונה ממוזערת (JPEG קטן) לשמירה בהיסטוריה."""
    try:
        image = open_image(io.BytesIO(image_bytes))
        image.thumbnail((side, side))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=78)
        return buffer.getvalue()
    except Exception:
        return None


def catalog_thumb(product):
    picture = product.get("picture", "")
    if picture and re.fullmatch(r"[A-Za-z0-9+/=]+", picture) and len(picture) < 400_000:
        try:
            return make_thumb(base64.b64decode(picture))
        except Exception:
            return None
    return None


MAX_HISTORY = 30


def add_history(kind, key, title, thumb, product=None, meta=None):
    entry = {
        "kind": kind, "key": key, "title": title, "thumb": thumb,
        "text": st.session_state.ocr_ingredients_text, "product": product, "meta": meta,
        "time": datetime.datetime.now().strftime("%d.%m %H:%M"),
    }
    history = [item for item in st.session_state.history if item["key"] != key]
    st.session_state.history = ([entry] + history)[:MAX_HISTORY]
    if kind == "product":
        ensure_coupons_async()


def choose_dekel_product(product, from_photo=False):
    if not product.get("verified"):
        # אין רשימת רכיבים מדקל למוצר שלא מאושר: מציגות אזהרה עם אנימציה שלילית
        st.session_state.nv_product = product
        st.session_state.product_candidates = []
        st.session_state.product_status = None
        st.session_state.product_hits = None
        st.session_state.celebrate_kind = "no"
        st.session_state.celebrate_id = int(time.time() * 1000)
        st.rerun()
    try:
        with st.spinner("מביאה את הרכיבים והדירוגים מדקל..."):
            names, results, meta = load_product_ingredients(product["slug"])
    except Exception:
        st.error("לא הצלחתי להביא את הרכיבים של המוצר כרגע. נסי שוב בעוד רגע.")
        return

    st.session_state.results.update(results)
    st.session_state.ocr_ingredients_text = "\n".join(names)
    st.session_state.selected_product = product
    st.session_state.photo_overlay = None
    st.session_state.product_meta = meta
    st.session_state.product_candidates = []
    st.session_state.product_status = None
    st.session_state.product_hits = None
    thumb = (st.session_state.photo_thumb if from_photo else None) or catalog_thumb(product)
    add_history("product", f"p:{product.get('slug') or product['name']}", product["name"], thumb, product, meta)
    st.session_state.auto_scan = True
    st.session_state.celebrate_kind = "yes" if product.get("verified") else "no"
    st.session_state.celebrate_id = int(time.time() * 1000)
    st.session_state.goto_tab = TAB_INGREDIENTS
    st.rerun()


def choose_obf_product(candidate):
    names = split_ingredients(candidate["ingredients"].replace(";", ","))
    st.session_state.ocr_ingredients_text = "\n".join(names)
    st.session_state.selected_product = {
        "source": "obf", "name": candidate["name"], "company": candidate["brand"],
    }
    st.session_state.product_meta = None
    add_history(
        "product", f"o:{candidate['brand']}:{candidate['name']}", candidate["name"] or "מוצר",
        st.session_state.photo_thumb, st.session_state.selected_product, None,
    )
    st.session_state.auto_scan = True
    st.session_state.celebrate_kind = "no"
    st.session_state.celebrate_id = int(time.time() * 1000)
    st.session_state.product_candidates = []
    st.session_state.product_status = None
    st.session_state.goto_tab = TAB_INGREDIENTS
    st.rerun()


def render_candidates():
    status = st.session_state.product_status
    if status is None and not st.session_state.product_candidates:
        return

    if status == "no-text":
        st.warning("לא הצלחתי לקרוא טקסט בתמונה. נסי לצלם שוב מקרוב ובאור טוב, כשהשם והמותג נראים בבירור.")
    elif status == "error":
        st.error("לא הצלחתי לחפש את המוצר כרגע. נסי שוב בעוד רגע.")
    elif status == "wrong-list":
        st.error("זו נראית כמו תמונה של רשימת רכיבים ולא של המוצר עצמו. כדי לבדוק את הרכיבים, עברי ללשונית העלאת תמונת רכיבים.")
        if st.button("📋 למסך צילום רשימת הרכיבים", key="wl_go"):
            st.session_state.product_status = None
            go_to(TAB_INGREDIENTS)
    elif status == "none":
        st.info("לא מצאתי התאמה בקטלוג. אפשר לכתוב את שם המוצר או המותג כאן למטה.")
    elif status == "exhausted":
        st.info("לא נמצאו עוד התאמות מהתמונה. כתבי כאן את שם המוצר או המותג, או צלמי שוב מקרוב.")
    elif status == "typed-none":
        st.info("לא נמצא מוצר בשם הזה בקטלוג של דקל. נסי לכתוב את המותג או חלק משם המוצר באנגלית.")

    candidates = st.session_state.product_candidates
    if candidates:
        if st.session_state.product_source == "dekel":
            st.caption("זה המוצר שלך? בחרי את ההתאמה הנכונה")
            for index, product in enumerate(candidates):
                st.markdown(product_card_html(product), unsafe_allow_html=True)
                if st.button("✔ זה המוצר שלי", key=f"pick_{index}"):
                    choose_dekel_product(product, from_photo=st.session_state.photo_source)
        else:
            st.warning(
                "המוצר לא נמצא בקטלוג של דקל. אלה התאמות ממאגר פתוח (Open Beauty Facts), "
                "וייתכן שההרכב בהן שונה מהאריזה שלך."
            )
            for index, candidate in enumerate(candidates):
                label = f"{candidate['brand']} · {candidate['name']}".strip(" ·")
                st.markdown(
                    f'<div class="pcard"><div class="pbody"><div class="pname">{html.escape(label)}</div>'
                    f'<p>{html.escape(candidate["ingredients"][:160])}…</p></div></div>',
                    unsafe_allow_html=True,
                )
                if st.button("✔ זה המוצר שלי", key=f"pick_obf_{index}"):
                    choose_obf_product(candidate)

        if st.button("✖ זה לא המוצר שלי, המשיכי לחפש", key="not_mine"):
            continue_search()

    st.markdown('<div class="typed-sep">לא מוצאת? אפשר לכתוב את שם המוצר או המותג:</div>', unsafe_allow_html=True)
    typed = st.text_input(
        "שם המוצר",
        label_visibility="collapsed",
        placeholder="The Ordinary Niacinamide, CeraVe...",
        key=f"typed_product_{st.session_state.manual_key}",
    )
    if st.button("🔎 חפשי לפי שם", key="typed_search"):
        if len(typed.strip()) < 2:
            st.warning("נא להזין שם מוצר או מותג")
        else:
            search_by_typed_name(typed.strip())


def product_stage_html(product):
    img = picture_uri(product)
    photo = f'<div class="stage-photo"><i></i><img alt="{html.escape(product["name"])}" src="{img}"></div>' if img else ""
    verified = product.get("verified", True)
    seal = '<span class="dv-seal"><i>✔</i> DEKEL VERIFIED</span>' if verified else '<span class="dv-no">לא עומד בתקן Dekel Verified</span>'
    name2 = f'<div class="stage-name2">{html.escape(product["name2"])}</div>' if product.get("name2") else ""
    link = ""
    if product.get("slug"):
        url = "https://thedekel.co.il/product-verified/?slug=" + quote(product["slug"])
        link = f'<a class="plink" href="{html.escape(url)}" target="_blank" rel="noopener">לעמוד המוצר באתר דקל ↗</a>'
    brand = product.get("company", "").split("|")[-1].strip()
    return (
        f'<div class="stage">{photo}<div class="stage-text"><div class="stage-brand">{html.escape(brand)}</div>'
        f'<div class="stage-name">{html.escape(product["name"])}</div>{name2}<div class="stage-seal">{seal}</div>{link}</div></div>'
    )


def selected_product_banner():
    product = st.session_state.selected_product
    if not product:
        return
    if product.get("source") == "obf":
        label = f"{product.get('company', '')} · {product['name']}".strip(" ·")
        st.info(f"המוצר: {label}. רשימת הרכיבים נלקחה מ-Open Beauty Facts ולא מדקל, וייתכן שהיא שונה מהאריזה שלך.")
    else:
        st.markdown(product_stage_html(product), unsafe_allow_html=True)
        matches = product_coupons(product)
        if matches:
            toast_once(f"sel:{product.get('slug')}", f"יש קופון פעיל ל-{product['name']}: {coupon_line(matches[0])}")
            st.markdown('<div class="alert-strip hot"><span class="as-bell">🎟️</span><div><b>יש קופון פעיל למוצר הזה</b></div></div>', unsafe_allow_html=True)
            st.markdown("".join(coupon_card_html(c) for c in matches[:2]), unsafe_allow_html=True)
    st.link_button("📊 טבלת ערבובי רכיבים של דקל", CONFLICTS_URL)


def how_box(icon, title, text):
    """כותרת מרכזית של כל מסך: כרטיס מעוטר עם מדליון אייקון, כותרת סריף וטקסט."""
    st.markdown(
        f'<div class="how"><div class="how-ico">{msr(icon)}</div><div class="how-body"><b>{html.escape(title)}</b>'
        f'<span>{html.escape(text)}</span><i class="how-rule"></i></div></div>',
        unsafe_allow_html=True,
    )


def tab_ingredients():
    how_box(
        "📋", "צילום של רשימת הרכיבים",
        "מצלמות את הטקסט הקטן בגב האריזה (INGREDIENTS), והמערכת קוראת כל רכיב ומדרגת אותו. "
        "אם אין לך את הגב של האריזה, עברי ללשונית 'העלאת תמונת מוצר'.",
    )
    with st.container(border=True):
        section("📸", "צילום רשימת הרכיבים", "מצלמות את הרכיבים שבאריזה, והמערכת קוראת אותם")
        uploaded_image = st.file_uploader(
            "העלי תמונה של רשימת רכיבים",
            type=["jpg", "jpeg", "png"],
            label_visibility="collapsed",
            key=f"uploader_{st.session_state.uploader_key}",
        )
        st.caption("📱 בטלפון: לוחצים על Upload ובוחרים 'צילום' כדי לצלם ישירות במצלמה")

        if uploaded_image:
            st.image(preview_image(open_image(uploaded_image)), caption="התמונה שהועלתה", width="stretch")

            if st.session_state.get("wrong_photo"):
                if st.button("📦 למסך צילום המוצר עצמו", key="wp_go"):
                    st.session_state.wrong_photo = False
                    go_to(TAB_PRODUCT)
            if st.button("✨ סרקי את הרכיבים מהתמונה"):
                st.session_state.wrong_photo = False
                started = time.time()
                with st.spinner("קוראת את הטקסט מהתמונה..."):
                    extracted_text, ocr_boxes = extract_text(uploaded_image.getvalue())
                st.caption(f"זיהוי הטקסט לקח {time.time() - started:.0f} שניות")

                with st.expander("הטקסט הגולמי שזוהה"):
                    st.code(extracted_text, language=None, wrap_lines=True)

                ingredients = split_ingredients(extracted_text.replace(";", ","))
                hits, _total = ingredient_list_matches(extracted_text)
                if hits < 2:
                    st.error("לא זיהיתי כאן רשימת רכיבים. נראה שזו תמונה של המוצר עצמו (חזית האריזה). כדי לזהות את המוצר, עברי ללשונית העלאת תמונת מוצר.")
                    st.session_state.wrong_photo = True
                    ingredients = []
                if ingredients:
                    st.session_state.ocr_ingredients_text = "\n".join(ingredients)
                    st.session_state.selected_product = None
                    st.session_state.product_meta = None
                    st.session_state.auto_scan = True
                st.session_state.photo_overlay = {
                    "img": make_thumb(uploaded_image.getvalue(), 1000), "boxes": ocr_boxes,
                }
                if ingredients:
                    add_history(
                        "ingredients", "i:" + "|".join(ingredients[:6]).lower(),
                        "רשימת רכיבים: " + ", ".join(ingredients[:3]),
                        make_thumb(uploaded_image.getvalue()),
                    )

    if st.session_state.ocr_ingredients_text:
        with st.container(border=True):
            section("📝", "הרכיבים", "אפשר לתקן את הרשימה (רכיב אחד בכל שורה) לפני הסריקה")
            selected_product_banner()
            st.session_state.ocr_ingredients_text = st.text_area(
                "רשימת רכיבים לעריכה",
                value=st.session_state.ocr_ingredients_text,
                height=200,
                label_visibility="collapsed",
            )

            ingredient_list = split_ingredients(st.session_state.ocr_ingredients_text)

            if st.session_state.auto_scan:
                st.session_state.auto_scan = False
                check_all(ingredient_list)
            if st.button(f"🚀 סרקי את כל הרכיבים ({len(ingredient_list)})", type="primary"):
                check_all(ingredient_list)

            scanned = any(name in st.session_state.results for name in ingredient_list)
            product = st.session_state.selected_product
            can_add = bool(product and product.get("slug") and product["slug"] in routine_entries_by_slug())
            if scanned:
                sum_col, add_col = st.columns(2)
                if sum_col.button("📄 סיכום מוצר", key="open_summary", use_container_width=True):
                    result_dialog(ingredient_list)
                if can_add:
                    if product["slug"] in st.session_state.routine:
                        add_col.button("✔ המוצר בשגרה שלי", key="in_routine", disabled=True, use_container_width=True)
                    elif add_col.button("➕ הוסיפי לשגרה שלי", key="add_routine_top", use_container_width=True):
                        st.session_state.routine.append(product["slug"])
                        save_routine()
                        st.toast("המוצר נוסף לשגרה")
                        st.rerun()

            show_summary(ingredient_list)

            with st.expander("בדיקה של רכיב בודד מהרשימה"):
                for ing in ingredient_list:
                    if st.button(f"בדקי {ing}", key=f"img_{ing}"):
                        st.session_state.selected_ingredient = ing
    else:
        st.info("עוד אין רכיבים לבדיקה. צלמי רשימת רכיבים, או זהי מוצר בלשונית 'העלאת תמונת מוצר'.")

    if st.session_state.selected_ingredient:
        show_result(st.session_state.selected_ingredient)

    with st.container(border=True):
        section("🔎", "חיפוש רכיב בודד", "כותבים שם של רכיב באנגלית, למשל Niacinamide")
        ingredient_name = st.selectbox(
            "הכניסי רכיב:",
            ingredient_search_options(),
            index=None,
            accept_new_options=True,
            label_visibility="collapsed",
            placeholder="התחילי להקליד, בעברית או באנגלית: גליצרין, Niacinamide...",
            key=f"manual_{st.session_state.manual_key}",
        )

        if st.button("חפשי רכיב"):
            query = (ingredient_name or "").strip()
            st.session_state.ing_choices = []
            if not query:
                st.warning("נא להזין שם רכיב")
            elif has_hebrew(query):
                matches = ingredient_matches(query, load_ingredient_db()["ingredients"])
                if not matches:
                    st.info("לא מצאתי רכיב בשם הזה. נסי לכתוב את שמו באנגלית, או חלק אחר מהשם.")
                elif len(matches) == 1:
                    show_result(matches[0])
                else:
                    st.session_state.ing_choices = matches
            else:
                show_result(query)

        if st.session_state.ing_choices:
            st.caption("נמצאו כמה רכיבים מתאימים, בחרי אחד:")
            for index, name in enumerate(st.session_state.ing_choices):
                if st.button(name, key=f"ingc_{index}"):
                    st.session_state.selected_ingredient = name
                    st.session_state.ing_choices = []
                    st.rerun()


def tab_product():
    if st.session_state.nv_product:
        product = st.session_state.nv_product
        st.markdown(product_card_html(product), unsafe_allow_html=True)
        st.warning(
            "🚫 המוצר הזה לא Dekel Verified, ולכן אין לנו ממנו רשימת רכיבים ודירוגים מדקל. "
            "אפשר לצלם את רשימת הרכיבים שבגב האריזה ולבדוק אותם בלשונית 'העלאת תמונת רכיבים'."
        )
        left, right = st.columns(2)
        if left.button("📋 צלמי את רשימת הרכיבים", key="nv_to_list"):
            st.session_state.nv_product = None
            go_to(TAB_INGREDIENTS)
        if right.button("🔎 חפשי מוצר אחר", key="nv_clear"):
            st.session_state.nv_product = None
            st.rerun()
    how_box(
        "📦", "צילום של המוצר עצמו",
        "מצלמות את חזית האריזה (שם ומותג), והמערכת מזהה את המוצר בקטלוג של דקל וטוענת את הרכיבים והדירוגים שלו משם. "
        "לא צריך לצלם את רשימת הרכיבים.",
    )
    with st.container(border=True):
        section("📦", "זיהוי מוצר מצילום", "מצלמות את המוצר עצמו, ואנחנו מוצאות אותו בקטלוג של דקל")
        product_photo = st.file_uploader(
            "העלי תמונה של המוצר",
            type=["jpg", "jpeg", "png"],
            label_visibility="collapsed",
            key=f"product_photo_{st.session_state.uploader_key}",
        )
        st.caption("📱 צלמי את המוצר עצמו מקרוב, כשהמותג והשם נראים בבירור. בטלפון: Upload ואז 'צילום'.")
        if product_photo:
            st.image(preview_image(open_image(product_photo)), caption="תמונת המוצר", width="stretch")
            if st.button("🔎 זהי את המוצר"):
                identify_product(product_photo.getvalue())
        render_candidates()

    with st.container(border=True):
        section("🏷️", "האם המוצר מאושר דקל?", "חפשי מוצר לפי שם או מותג ונבדוק אם הוא Dekel Verified")
        product_options = product_search_options()
        product_choice = st.selectbox(
            "שם מוצר או מותג",
            list(product_options),
            index=None,
            accept_new_options=True,
            label_visibility="collapsed",
            placeholder="התחילי להקליד, בעברית או באנגלית: סרווה, The Ordinary, CeraVe...",
            key=f"product_{st.session_state.manual_key}",
        )
        product_term = product_options.get(product_choice, product_choice or "")

        if st.button("בדקי מוצר", key="product_search"):
            if len(product_term.strip()) < 3:
                st.warning("נא להזין לפחות 3 אותיות משם המוצר או המותג")
            elif product_term.count(",") >= 4:
                st.error("נראה שהדבקת רשימת רכיבים ולא שם של מוצר. כדי לבדוק רכיבים, עברי ללשונית העלאת תמונת רכיבים.")
            else:
                try:
                    with st.spinner("מחפשת בקטלוג של דקל..."):
                        st.session_state.product_hits = search_products(product_term.strip())
                    flag_not_found(product_term.strip(), st.session_state.product_hits)
                except Exception:
                    st.session_state.product_hits = None
                    st.error("לא הצלחתי לבדוק את המוצר כרגע. נסי שוב בעוד רגע.")

        found = st.session_state.product_hits
        if found is not None and not found:
            st.info("לא נמצא מוצר כזה בקטלוג של דקל. נסי לכתוב את שם המותג או חלק משם המוצר באנגלית.")
        elif found:
            verified_count = sum(p["verified"] for p in found)
            st.caption(f"נמצאו {len(found)} מוצרים, {verified_count} מהם Dekel Verified")
            for index, product in enumerate(found[:8]):
                st.markdown(product_card_html(product), unsafe_allow_html=True)
                if st.button("✔ זה המוצר שלי, טעני את הרכיבים", key=f"search_pick_{index}"):
                    choose_dekel_product(product)

    if st.session_state.nf_pending:
        term, similar = st.session_state.nf_pending
        st.session_state.nf_pending = None
        not_found_dialog(term, similar)


def pair_verdict(first, second):
    """האם אפשר לשלב בין שני מוצרים: (קוד, כותרת, הסבר, התנגשויות)."""
    conflicts = compare_actives(set(first.get("actives", [])), set(second.get("actives", [])))
    if not conflicts:
        return "ok", "אפשר לשלב", "אין התנגשות בין הפעילים של שני המוצרים, ולכן אפשר להשתמש בהם באותה שגרה.", []
    pair = (first.get("time"), second.get("time"))
    if pair in OPPOSITE_TIMES:
        return "separate", "רק בנפרד", "יש ביניהם התנגשות, אבל הם מיועדים לשעות הפוכות: אחד בבוקר והשני בערב, ולכן אפשר להשתמש בשניהם בהפרדה.", conflicts
    return "avoid", "עדיף לא לשלב", "יש התנגשות בין הפעילים של שני המוצרים. אם רוצים להשתמש בשניהם, כדאי להפריד ביניהם בין בוקר וערב או בין ימים.", conflicts


def verdict_html(first, second, verdict):
    code, title, text, conflicts = verdict
    icon = {"ok": "✅", "separate": "🕒", "avoid": "⛔"}[code]
    reasons = "".join(f"<li><b>{html.escape(t)}:</b> {html.escape(r)}</li>" for t, r in conflicts)
    reasons = f'<ul class="cx-reasons">{reasons}</ul>' if reasons else ""

    def side(entry):
        uri = thumb_uri(entry["slug"])
        img = f'<img src="{uri}" alt="">' if uri else '<div class="pv-noimg">🧴</div>'
        brand = entry["company"].split("|")[-1].strip()
        return f'<div class="pv-side">{img}<b>{html.escape(entry["name"])}</b><small>{html.escape(brand)}</small></div>'

    return (
        f'<div class="pv {code}"><div class="pv-row">{side(first)}<div class="pv-plus">+</div>{side(second)}</div>'
        f'<div class="pv-res"><span>{icon}</span><div><b>{title}</b><p>{html.escape(text)}</p>{reasons}</div></div></div>'
    )


def _select_catalog_product(slug):
    entry = routine_entries_by_slug().get(slug)
    if not entry:
        return
    names, results_map, meta = load_product_ingredients(slug)
    st.session_state.results.update(results_map)
    st.session_state.ocr_ingredients_text = chr(10).join(names)
    st.session_state.selected_product = {
        "name": entry["name"], "name2": entry.get("name2", ""), "company": entry["company"], "description": "",
        "slug": slug, "picture": "", "verified": entry.get("verified", True),
    }
    st.session_state.product_meta = meta


def compat_slot(title, tag, options):
    """בוחרת מוצר אחד להשוואה: מרשימה נפתחת או מצילום. מחזירה slug או None."""
    chosen = st.session_state.get(f"cmp_photo_{tag}")
    if chosen:
        entry = routine_lookup().get(chosen)
        st.markdown(f"**{title}:** {html.escape(entry['name']) if entry else chosen}")
        if st.button("החליפי מוצר", key=f"cmp_clear_{tag}"):
            st.session_state[f"cmp_photo_{tag}"] = None
            st.rerun()
        return chosen
    t_pick, t_photo = st.tabs(["🔎 חיפוש", "📷 צילום"])
    with t_pick:
        pick = st.selectbox(
            title, list(options), index=None, placeholder=title, key=f"cmp_{tag}_{st.session_state.manual_key}"
        )
    with t_photo:
        photo = st.file_uploader(
            f"{title}, צילום", type=["jpg", "jpeg", "png"], label_visibility="collapsed",
            key=f"cmp_up_{tag}_{st.session_state.uploader_key}",
        )
        st.caption("צלמי את חזית האריזה מקרוב, כשהמותג והשם נראים.")
        if photo and st.button("🔎 זהי את המוצר", key=f"cmp_id_{tag}"):
            identify_product(photo.getvalue())
            st.session_state[f"cmp_cands_{tag}"] = (st.session_state.product_status, list(st.session_state.product_candidates) if st.session_state.product_source == "dekel" else [])
            st.session_state.product_candidates = []
            st.session_state.product_status = None
            st.session_state.product_source = None
            st.session_state.photo_source = False
        status, cands = st.session_state.get(f"cmp_cands_{tag}") or (None, [])
        if status == "wrong-list":
            st.error("זו נראית כמו רשימת רכיבים ולא המוצר עצמו. צלמי את חזית האריזה.")
        elif status in ("none", "no-text", "error") or (status == "ok" and not cands):
            st.info("לא זיהיתי מוצר מהתמונה. נסי לצלם שוב מקרוב, או חפשי לפי שם.")
        for index, product in enumerate(cands[:3]):
            st.markdown(product_card_html(product), unsafe_allow_html=True)
            if st.button("✔ זה המוצר", key=f"cmp_sel_{tag}_{index}"):
                slug = product.get("slug")
                if slug and slug not in routine_lookup():
                    with st.spinner("מביאה את הרכיבים של המוצר..."):
                        names, results_map, meta = load_product_ingredients(slug)
                    st.session_state.results.update(results_map)
                    st.session_state.custom_products[slug] = custom_entry(
                        slug, product["name"], product.get("company", ""), names, results_map, meta, product.get("verified", False)
                    )
                st.session_state[f"cmp_photo_{tag}"] = slug
                st.session_state[f"cmp_cands_{tag}"] = None
                st.rerun()
    return options.get(pick)


def tab_compat():
    how_box(
        "🤝", "שילובים: אפשר להשתמש בשני מוצרים יחד?",
        "בוחרים מוצר אחד, אחר כך מוצר שני, בחיפוש לפי שם או בצילום של האריזה, ולוחצים שלב. נשווה ביניהם לפי הרכיבים הפעילים ונגיד אם אפשר להשתמש בשניהם באותה שגרה.",
    )
    options = routine_options()
    with st.container(border=True):
        section("🔀", "השוואה בין שני מוצרים", "מחפשים לפי שם, בעברית או באנגלית, או מצלמים את המוצר")
        first_col, second_col = st.columns(2)
        with first_col:
            first_slug = compat_slot("מוצר ראשון", "a", options)
        with second_col:
            second_slug = compat_slot("מוצר שני", "b", options)
        if st.button("🔀 שלב", key="compat_combine", type="primary", use_container_width=True):
            if not first_slug or not second_slug:
                st.warning("בחרי שני מוצרים, מהרשימה או מצילום")
            elif first_slug == second_slug:
                st.info("בחרת את אותו מוצר פעמיים. בחרי מוצר שני אחר.")
            else:
                st.session_state.compat_pair = (first_slug, second_slug)

    pair = st.session_state.get("compat_pair")
    by_slug = routine_lookup()
    if pair and pair[0] in by_slug and pair[1] in by_slug:
        first, second = by_slug[pair[0]], by_slug[pair[1]]
        st.markdown(verdict_html(first, second, pair_verdict(first, second)), unsafe_allow_html=True)
        st.caption("ההערכה לפי הפעילים הבולטים בכל מוצר וכללי שילוב כלליים, ואינה ייעוץ רפואי.")
        if st.toggle(f"הצגי את כל המוצרים שמתאימים ל-{first['name']}", key="compat_show_all"):
            _select_catalog_product(pair[0])
            show_compatibility(
                split_ingredients(st.session_state.ocr_ingredients_text), st.session_state.results,
                st.session_state.selected_product, st.session_state.product_meta,
            )
    else:
        st.info("בחרי שני מוצרים ולחצי שלב כדי לראות אם אפשר להשתמש בהם יחד.")
    st.link_button("📊 טבלת ערבובי רכיבים של דקל", CONFLICTS_URL)


@st.cache_data(show_spinner=False)
def routine_entries_by_slug():
    return {entry["slug"]: entry for entry in (load_catalog_index() or {}).get("products", [])}


@st.cache_data(show_spinner=False)
def routine_options():
    """תווית להשלמה אוטומטית -> slug, רק למוצרים שנסרקו בקטלוג."""
    options = {}
    for entry in (load_catalog_index() or {}).get("products", []):
        brand = entry["company"].split("|")[-1].strip()
        options.setdefault(f"{entry['name']} · {brand}" if brand else entry["name"], entry["slug"])
    return options


ROUTINE_TIME_LABELS = {"MORNING": "בוקר", "EVENING": "ערב", "MORNING_AND_EVENING": "בוקר וערב"}


def routine_slot_html(title, icon, items):
    if not items:
        return f'<div class="rt-col"><div class="rt-head">{icon} {title}</div><div class="cx-empty">אין מוצרים.</div></div>'
    rows = "".join(
        f'<div class="rt-item"><span class="rt-n">{index}</span>'
        f'{("<img class=rt-img alt= src=" + chr(34) + thumb_uri(entry["slug"]) + chr(34) + ">") if thumb_uri(entry["slug"]) else ""}'
        f'<div><b>{html.escape(entry["name"])}</b>'
        f'<small>{html.escape(entry["company"].split("|")[-1].strip())} · '
        f'{html.escape(" / ".join(categories_of(entry)))}</small></div></div>'
        for index, entry in enumerate(items, 1)
    )
    return f'<div class="rt-col"><div class="rt-head">{icon} {title}</div>{rows}</div>'


def _add_candidate_to_routine(product):
    """מוסיפה מוצר שנמצא בקטלוג (מחיפוש או מצילום) לשגרה."""
    slug = product.get("slug")
    if not slug:
        return
    if slug in routine_entries_by_slug():
        add_to_routine(routine_entries_by_slug()[slug])
        return
    with st.spinner("מביאה את הרכיבים של המוצר..."):
        names, results_map, meta = load_product_ingredients(slug)
    st.session_state.results.update(results_map)
    add_to_routine(custom_entry(slug, product["name"], product.get("company", ""), names, results_map, meta, product.get("verified", False)))


def routine_add_panel():
    with st.container(border=True):
        section("➕", "הוספת מוצר לשגרה", "בוחרים דרך: חיפוש, צילום של המוצר, או צילום של רשימת הרכיבים")
        t_search, t_product, t_list = st.tabs(["🔎 חיפוש לפי שם", "📦 צילום המוצר", "📋 צילום רכיבים"])

        with t_search:
            options = product_search_options()
            choice = st.selectbox(
                "שם מוצר", list(options), index=None, accept_new_options=True, label_visibility="collapsed",
                placeholder="התחילי להקליד שם או מותג, בעברית או באנגלית",
                key=f"routine_pick_{st.session_state.manual_key}",
            )
            term = options.get(choice, choice or "").strip()
            if st.button("חפשי", key="routine_search"):
                if len(term) < 3:
                    st.warning("נא להזין לפחות 3 אותיות")
                elif term.count(",") >= 4:
                    st.error("נראה שהדבקת רשימת רכיבים. כדי להוסיף מרשימת רכיבים, השתמשי בלשונית העלאת תמונת רכיבים.")
                else:
                    try:
                        with st.spinner("מחפשת בקטלוג של דקל..."):
                            st.session_state.routine_hits = search_products(term)
                    except Exception:
                        st.session_state.routine_hits = None
                        st.error("לא הצלחתי לחפש כרגע. נסי שוב בעוד רגע.")
            hits = st.session_state.get("routine_hits")
            if hits is not None and not hits:
                st.info("לא נמצא מוצר כזה בקטלוג של דקל. אפשר להוסיף אותו דרך צילום רשימת הרכיבים.")
            for index, product in enumerate((hits or [])[:6]):
                st.markdown(product_card_html(product), unsafe_allow_html=True)
                if st.button("➕ הוסיפי לשגרה", key=f"rt_hit_{index}"):
                    _add_candidate_to_routine(product)
                    st.session_state.routine_hits = None
                    st.rerun()

        with t_product:
            photo = st.file_uploader("תמונה של המוצר", type=["jpg", "jpeg", "png"], label_visibility="collapsed", key=f"rt_photo_{st.session_state.uploader_key}")
            st.caption("צלמי את חזית האריזה מקרוב, כשהמותג והשם נראים.")
            if photo and st.button("🔎 זהי והציעי", key="rt_identify"):
                identify_product(photo.getvalue())
            status = st.session_state.product_status
            if status == "wrong-list":
                st.error("זו נראית כמו תמונה של רשימת רכיבים ולא של המוצר עצמו. השתמשי בלשונית העלאת תמונת רכיבים.")
            elif status in ("none", "no-text"):
                st.info("לא זיהיתי מוצר מהתמונה. נסי לצלם שוב מקרוב, או חפשי לפי שם.")
            if st.session_state.product_source == "dekel":
                for index, product in enumerate(st.session_state.product_candidates[:4]):
                    st.markdown(product_card_html(product), unsafe_allow_html=True)
                    if st.button("➕ זה המוצר, הוסיפי לשגרה", key=f"rt_cand_{index}"):
                        _add_candidate_to_routine(product)
                        st.session_state.product_candidates = []
                        st.session_state.product_status = None
                        st.rerun()

        with t_list:
            photo2 = st.file_uploader("תמונה של רשימת הרכיבים", type=["jpg", "jpeg", "png"], label_visibility="collapsed", key=f"rt_list_{st.session_state.uploader_key}")
            name = st.text_input("איך לקרוא למוצר?", placeholder="למשל: סרום הלילה שלי", key=f"rt_list_name_{st.session_state.manual_key}")
            if photo2 and st.button("✨ סרקי והוסיפי לשגרה", key="rt_list_go"):
                with st.spinner("קוראת את הרכיבים..."):
                    text, _boxes = extract_text(photo2.getvalue())
                    hits, _t = ingredient_list_matches(text)
                    ingredients = split_ingredients(text.replace(";", ","))
                if hits < 2 or not ingredients:
                    st.error("לא זיהיתי רשימת רכיבים בתמונה. נראה שזו תמונה של המוצר עצמו, אז השתמשי בלשונית העלאת תמונת מוצר.")
                else:
                    with st.spinner("בודקת את הרכיבים..."):
                        st.session_state.results.update(search_many([i for i in ingredients if i not in st.session_state.results]))
                    title = name.strip() or f"מוצר מצילום ({ingredients[0]})"
                    slug = "custom-" + re.sub(r"[^a-z0-9]+", "", (title + ingredients[0]).lower())[:24] + str(len(ingredients))
                    add_to_routine(custom_entry(slug, title, "", ingredients, st.session_state.results, None))
                    st.rerun()


def routine_check_banner():
    check = st.session_state.get("routine_check")
    if not check:
        return
    if check["clashes"]:
        lines = " ".join(
            f"**{other}**: " + " ".join(f"{title}." for title, _r in found) for other, found in check["clashes"][:4]
        )
        st.warning(f"נוסף **{check['name']}**. בדיקת השגרה: יש התנגשות עם {lines} כדאי להפריד בין הבוקר לערב או בין ימים.")
    else:
        st.success(f"נוסף **{check['name']}**. בדיקת השגרה: אין התנגשות עם שאר המוצרים.")
    if st.button("הבנתי", key="rt_check_ok"):
        st.session_state.routine_check = None
        st.rerun()


def tab_routine():
    how_box(
        "🗓️", "השגרה שלי",
        "מוסיפות את המוצרים שאת משתמשת בהם בחיפוש, בצילום המוצר או בצילום הרכיבים. "
        "אחרי כל הוספה אנחנו בודקות אם הוא מתנגש עם מה שכבר בשגרה, ומסדרות הכול לבוקר ולערב.",
    )
    routine_check_banner()
    routine_add_panel()
    by_slug = routine_lookup()
    st.session_state.routine = [slug for slug in st.session_state.routine if slug in by_slug]
    entries = [by_slug[slug] for slug in st.session_state.routine]
    if not entries:
        st.info("עוד אין מוצרים בשגרה. הוסיפי מוצר באחת הדרכים שלמעלה.")
        return

    with st.container(border=True):
        section("🧴", "המוצרים שלי", f"{len(entries)} מוצרים")
        for index, entry in enumerate(entries):
            left, right = st.columns([5, 1])
            time_label = ROUTINE_TIME_LABELS.get(entry.get("time"), "בוקר או ערב")
            left.markdown(
                f'<div class="rt-line"><b>{html.escape(entry["name"])}</b>'
                f'<small>{html.escape(entry["company"].split("|")[-1].strip())} · {time_label}'
                f'{" · " + active_label(set(entry["actives"])) if entry.get("actives") else ""}</small></div>',
                unsafe_allow_html=True,
            )
            if right.button("הסרה", key=f"routine_rm_{index}"):
                st.session_state.routine.remove(entry["slug"])
                save_routine()
                st.session_state.routine_check = None
                st.rerun()

    slots, conflicts, notes = plan_routine(entries, categories_of)
    with st.container(border=True):
        section("📅", "הסדר המומלץ", "מהנוזלי אל הסמיך, וקרם הגנה אחרון בבוקר")
        st.markdown(
            '<div class="rt-grid">'
            + routine_slot_html("בוקר", "☀️", slots["AM"])
            + routine_slot_html("ערב", "🌙", slots["PM"])
            + "</div>",
            unsafe_allow_html=True,
        )

    with st.container(border=True):
        section("⚠️", "בדיקת שילובים", "התנגשויות בין המוצרים שבאותה שגרה")
        if conflicts:
            for slot, first, second, found in conflicts:
                when = "בבוקר" if slot == "AM" else "בערב"
                st.warning(
                    f"**{first['name']}** ו-**{second['name']}** {when}: "
                    + " ".join(f"{title}. {reason}" for title, reason in found)
                    + " כדאי להשתמש באחד מהם בזמן אחר ביום או בימים שונים."
                )
        else:
            st.success("לא נמצאו התנגשויות בין הפעילים של המוצרים בשגרה.")
        for icon, text in notes:
            st.info(f"{icon} {text}")
        st.caption("ההמלצות לפי הפעילים הבולטים בכל מוצר וכללי שילוב כלליים, ואינן ייעוץ רפואי.")


def open_history_entry(entry):
    st.session_state.ocr_ingredients_text = entry["text"]
    st.session_state.selected_product = entry["product"]
    st.session_state.product_meta = entry["meta"]
    st.session_state.celebrate_id = None
    st.session_state.goto_tab = TAB_INGREDIENTS
    st.rerun()


def tab_history():
    how_box(
        "🕘", "היסטוריית חיפושים",
        "כל מוצר שזיהית וכל רשימת רכיבים שסרקת בביקור הזה נשמרים כאן, עם התמונה. "
        "לחיצה על 'פתחי סיכום' טוענת מחדש את הסיכום.",
    )
    history = st.session_state.history
    if not history:
        st.info("עוד אין היסטוריה. אחרי שתזהי מוצר או תסרקי רשימת רכיבים, הם יופיעו כאן.")
        return
    ensure_coupons_async()
    alerts = {entry["key"]: matches for entry, matches in history_alerts()}
    if alerts:
        st.success(f"🔔 יש הטבות פעילות אצל דקל ל-{len(alerts)} מהמוצרים בהיסטוריה שלך")
    elif coupon_holder()["busy"]:
        st.caption("בודקת אם יש הטבות פעילות למוצרים שלך...")
    with st.container(border=True):
        section("🕘", "החיפושים שלי", f"{len(history)} פריטים")
        for index, entry in enumerate(history):
            thumb_col, text_col, button_col = st.columns([1, 4, 2], vertical_alignment="center")
            if entry["thumb"]:
                thumb_col.image(entry["thumb"], width=72)
            else:
                thumb_col.markdown('<div class="hist-noimg">🧴</div>', unsafe_allow_html=True)
            kind = "מוצר" if entry["kind"] == "product" else "צילום רכיבים"
            company = (entry["product"] or {}).get("company", "") if entry["kind"] == "product" else ""
            text_col.markdown(
                f'<div class="hist-line"><b>{html.escape(entry["title"])}</b>'
                f'<small>{html.escape(kind)}{" · " + html.escape(company.split("|")[-1].strip()) if company else ""}'
                f' · {entry["time"]}</small></div>',
                unsafe_allow_html=True,
            )
            if button_col.button("📄 פתחי סיכום", key=f"hist_open_{index}"):
                open_history_entry(entry)
            for coupon in alerts.get(entry["key"], []):
                st.markdown(coupon_card_html(coupon), unsafe_allow_html=True)
        if st.button("🗑️ נקי היסטוריה", key="hist_clear"):
            st.session_state.history = []
            st.rerun()


def go_to(tab):
    st.session_state.goto_tab = tab
    st.rerun()


@st.dialog("📷 מה מצלמים?")
def camera_dialog():
    st.caption("בחרי מה את מצלמת, ונפתח לך את ההעלאה. בטלפון תוכלי לבחור 'צילום' ישירות במצלמה.")
    if st.button("📋 את רשימת הרכיבים בגב האריזה", key="cam_list", use_container_width=True):
        go_to(TAB_INGREDIENTS)
    if st.button("📦 את המוצר עצמו (חזית האריזה)", key="cam_product", use_container_width=True):
        go_to(TAB_PRODUCT)


@st.dialog("עוד")
def more_dialog():
    for label, tab, key in (
        ("🤝 שילובים", TAB_COMPAT, "more_compat"),
        ("🎟️ קופונים והטבות", TAB_COUPONS, "more_coupons"),
        ("📋 צילום רשימת רכיבים", TAB_INGREDIENTS, "more_list"),
        ("📦 צילום המוצר עצמו", TAB_PRODUCT, "more_product"),
    ):
        if st.button(label, key=key, use_container_width=True):
            go_to(tab)


def render_bottom_nav(active):
    """סרגל ניווט קבוע בתחתית המסך (מוצג רק בטלפון)."""
    history_label = "היסטוריה" + (" 🔔" if "🔔" in nav_label(TAB_HISTORY) else "")
    items = (
        ("bn_home", NAV_ICONS["home"], "בית", lambda: go_to(TAB_HOME), active == TAB_HOME),
        ("bn_routine", NAV_ICONS["routine"], "שגרה", lambda: go_to(TAB_ROUTINE), active == TAB_ROUTINE),
        ("bn_camera", NAV_ICONS["product"], "צילום", camera_dialog, False),
        ("bn_history", NAV_ICONS["history"], history_label, lambda: go_to(TAB_HISTORY), active == TAB_HISTORY),
        ("bn_more", NAV_ICONS["more"], "עוד", more_dialog, active in (TAB_COMPAT, TAB_COUPONS)),
    )
    with st.container(key="bottomnav"):
        for column, (key, icon, label, action, on) in zip(st.columns(len(items)), items):
            if column.button(f"{icon}  \n{label}", key=key + ("_on" if on else ""), use_container_width=True):
                action()


SAMPLE_SLUG = "the-ordinary-glycolic-acid-7-exfoliating-toner"


def home_tile_css():
    slugs = showcase_slugs(3, offset=11)
    rules = []
    for key, slug in zip(("go_list", "go_product", "go_search"), slugs):
        uri = thumb_uri(slug)
        if uri:
            rules.append(f'.st-key-{key} button::after{{background-image:url("{uri}") !important}}')
    return f"<style>{''.join(rules)}</style>" if rules else ""


def account_bar():
    user = current_user()
    if user:
        left, right = st.columns([3, 1], vertical_alignment="center")
        left.caption(f"מחוברת כ-{user['email']}. הפרופיל, השגרה וההיסטוריה שלך נשמרים בחשבון.")
        if right.button("התנתקות", key="logout_btn"):
            st.session_state.cloud_loaded = None
            st.logout()
    else:
        st.caption("התחברות עם גוגל שומרת את הפרופיל, השגרה וההיסטוריה שלך בין ביקורים ובין מכשירים.")


def _mq_open():
    slug = (st.session_state.get("mq_open") or "").strip()
    st.session_state.mq_open = ""
    if slug:
        st.session_state.mq_pending = slug


def tab_home():
    with st.container(key="mqbridge"):
        st.text_input("mq", key="mq_open", label_visibility="collapsed", on_change=_mq_open)
    pending = st.session_state.pop("mq_pending", None)
    if pending:
        entry = routine_entries_by_slug().get(pending)
        if entry:
            choose_dekel_product({
                "name": entry["name"], "name2": entry.get("name2", ""), "company": entry["company"],
                "description": "", "slug": pending, "picture": "", "verified": entry.get("verified", True),
            })
    account_bar()
    st.markdown(home_tile_css(), unsafe_allow_html=True)
    st.markdown(hero_html(), unsafe_allow_html=True)
    st.markdown(marquee_html(), unsafe_allow_html=True)
    st.markdown('<div class="ask">מה את רוצה לבדוק?</div>', unsafe_allow_html=True)
    first, second, third = st.columns(3)
    if first.button("**צילום רשימת רכיבים**  \nהטקסט הקטן בגב האריזה", key="go_list", use_container_width=True):
        go_to(TAB_INGREDIENTS)
    if second.button("**צילום המוצר עצמו**  \nמזהים אותו בקטלוג של דקל", key="go_product", use_container_width=True):
        go_to(TAB_PRODUCT)
    if third.button("**חיפוש לפי שם**  \nמוצר או רכיב, בעברית או באנגלית", key="go_search", use_container_width=True):
        go_to(TAB_PRODUCT)

    st.markdown(stats_html(), unsafe_allow_html=True)
    st.markdown('<div class="ask small">או הרבה יותר מזה</div>', unsafe_allow_html=True)
    cols = st.columns(4)
    for col, (label, tab, key) in zip(cols, (
        (nav_label(TAB_ROUTINE), TAB_ROUTINE, "home_routine"),
        (nav_label(TAB_HISTORY), TAB_HISTORY, "home_history"),
        (nav_label(TAB_COMPAT), TAB_COMPAT, "home_compat"),
        (nav_label(TAB_COUPONS), TAB_COUPONS, "home_coupons"),
    )):
        if col.button(label, key=key, use_container_width=True):
            go_to(tab)

    featured = showcase_slugs(4, offset=3)
    if featured:
        by_slug = routine_entries_by_slug()
        st.markdown('<div class="ask">מוצרים נבחרים מהקטלוג</div>', unsafe_allow_html=True)
        for column, slug in zip(st.columns(len(featured)), featured):
            entry = by_slug[slug]
            with column:
                brand = entry["company"].split("|")[-1].strip()
                st.markdown(
                    f'<div class="feat"><div class="feat-photo"><img alt="" src="{thumb_uri(slug)}"></div>'
                    f'<div class="feat-brand">{html.escape(brand)}</div><div class="feat-name">{html.escape(entry["name"])}</div>'
                    f'<div class="feat-tag">{html.escape(" · ".join(categories_of(entry)[:1]))}</div></div>',
                    unsafe_allow_html=True,
                )
                if st.button("בדקי", key=f"feat_{slug}"[:60], use_container_width=True):
                    choose_dekel_product({
                        "name": entry["name"], "name2": entry.get("name2", ""), "company": entry["company"],
                        "description": "", "slug": slug, "picture": "", "verified": True,
                    })

    recent = st.session_state.history[:4]
    if recent:
        st.markdown('<div class="ask small">המשיכי מאיפה שעצרת</div>', unsafe_allow_html=True)
        for column, entry in zip(st.columns(len(recent)), recent):
            with column:
                if entry["thumb"]:
                    st.image(entry["thumb"], width=84)
                st.caption(entry["title"][:28])
                if st.button("פתחי", key=f"home_recent_{entry['key']}"[:60], use_container_width=True):
                    open_history_entry(entry)

    profile_label = f"{NAV_ICONS['profile']} הפרופיל שלי" + ("  ✓" if not skinprofile.is_empty(st.session_state.profile) else "  (חדש)")
    if st.button(profile_label, key="home_profile", use_container_width=True):
        profile_dialog()

    entry = routine_entries_by_slug().get(SAMPLE_SLUG)
    if entry:
        if st.button(f"{NAV_ICONS['sample']} רוצה לראות איך זה עובד? נסי עם מוצר לדוגמה", key="home_sample", use_container_width=True):
            choose_dekel_product({
                "name": entry["name"], "name2": entry.get("name2", ""), "company": entry["company"],
                "description": "", "slug": entry["slug"], "picture": "", "verified": True,
            })


def tab_coupons():
    with st.container(border=True):
        section("🎟️", "הטבות וקופונים", "הטבות פעילות באתר דקל, מעודכן אוטומטית")
        if st.button("🔄 רענני הטבות", key="refresh_coupons"):
            fetch_coupons.clear()
        try:
            with st.spinner("בודקת אילו הטבות פעילות באתר..."):
                coupons = fetch_coupons()
        except Exception:
            st.error("לא הצלחתי לטעון את ההטבות כרגע. נסי שוב בעוד רגע.")
            st.link_button("🌐 להטבות באתר דקל", "https://thedekel.co.il/#promotions")
            return
        st.caption(f"{len(coupons)} הטבות פעילות")
        st.markdown("".join(coupon_card_html(c) for c in coupons), unsafe_allow_html=True)
        st.caption("ההטבות נקראות מהאתר של דקל וייתכן שהן משתנות. כדאי לוודא את התנאים בעמוד ההטבה.")


NAV_TABS = [TAB_HOME, TAB_INGREDIENTS, TAB_PRODUCT, TAB_COMPAT, TAB_ROUTINE, TAB_HISTORY, TAB_COUPONS]
if st.session_state.get("goto_tab") in NAV_TABS:
    st.session_state.nav = st.session_state.pop("goto_tab")
if st.session_state.get("nav") not in NAV_TABS:
    st.session_state.nav = TAB_HOME

active_tab = st.session_state.nav
if active_tab != TAB_HOME:
    active_tab = st.segmented_control(
        "ניווט", NAV_TABS, key="nav", label_visibility="collapsed", format_func=nav_label
    ) or active_tab
    if active_tab == TAB_HOME:
        st.rerun()
def coupon_watcher():
    holder = coupon_holder()
    if not holder["busy"] and st.session_state.get("coupons_seen") != holder["ts"]:
        st.session_state.coupons_seen = holder["ts"]
        st.rerun()


ensure_coupons_async()
if coupon_holder()["busy"]:
    st.fragment(run_every=4)(coupon_watcher)()

render_coupon_alerts()

{
    TAB_HOME: tab_home,
    TAB_INGREDIENTS: tab_ingredients,
    TAB_PRODUCT: tab_product,
    TAB_COMPAT: tab_compat,
    TAB_ROUTINE: tab_routine,
    TAB_HISTORY: tab_history,
    TAB_COUPONS: tab_coupons,
}[active_tab]()

render_bottom_nav(active_tab)
sync_account_data()

st.markdown(
    '<div class="to-top-wrap"><a class="to-top" href="#">↑ חזרה למעלה</a></div>'
    '<div class="footer">אפליקציה לא רשמית ולא קשורה לדקל. המידע מבוסס על מילון הרכיבים והקטלוג באתר דקל.</div>'
    '<div class="copyright">© כל הזכויות שמורות לנטע ונטוררו שבירו</div>',
    unsafe_allow_html=True,
)
