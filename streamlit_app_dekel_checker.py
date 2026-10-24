import base64
import html
import io
import datetime
import json
import os
import re
import subprocess
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

from compat import (
    ACTIVE_NAMES,
    EXFOLIANTS,
    active_label,
    compare_actives,
    detect_actives_from_names,
    mixing_advice,
)

st.set_page_config(page_title="Dekel Checker", layout="centered")

EMOJI_CLASS = {"🟢": "good", "🟡": "caution", "🔴": "bad"}
GRADE_CLASS = {"מעולה": "grade-excellent", "טוב": "grade-good", "עובר": "grade-pass"}


def grade_class(result):
    return GRADE_CLASS.get(result.get("rating", ""), "")

st.markdown(
    f"<style>{(Path(__file__).parent / 'style.css').read_text(encoding='utf-8')}</style>",
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
    if SESSION_PATH.exists():
        return browser.new_context(storage_state=str(SESSION_PATH))
    return browser.new_context()


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
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
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

            chunk = 12
            failed = []
            for start in range(0, len(ingredients), chunk):
                part = ingredients[start:start + chunk]
                for item in page.evaluate(BATCH_JS, part):
                    if item.get("error"):
                        failed.append(item["name"])
                    else:
                        results[item["name"]] = _to_result(item)
                if on_progress:
                    on_progress(min(start + chunk, len(ingredients)), len(ingredients), part[-1])

            if failed:
                for item in page.evaluate(BATCH_JS, failed):
                    results[item["name"]] = _to_result(item)
        finally:
            browser.close()

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


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_coupons():
    """קוראת את ההטבות הפעילות מדף הבית של דקל. שגיאות לא נשמרות במטמון."""
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


@st.cache_data(show_spinner=False)
def ocr_lines(image_bytes):
    image = open_image(io.BytesIO(image_bytes))
    result = get_ocr_reader().readtext(np.array(preprocess_for_ocr(image)), detail=1, canvas_size=MAX_OCR_SIDE)
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
            f'<span class="ugchip {EMOJI_CLASS.get(results[n]["emoji"], "other")} {grade_class(results[n])}">{html.escape(n)}</span>'
            for n in members
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
                f'<b>{html.escape(entry["name"])}</b><small>{html.escape(brand)}</small></a>'
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

    section("🤝", "שילובים עם מוצרים אחרים בקטלוג", "אילו מוצרים של דקל מתאימים לשימוש יחד עם המוצר הזה")
    if actives:
        st.caption(f"פעילים בולטים במוצר: {active_label(actives)}")
    else:
        st.caption("לא זוהו במוצר פעילים חזקים, ולכן רוב המוצרים מתאימים לשימוש יחד איתו.")

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
        f"✅ מתאימים ({len(groups['ok'])})",
        f"🕒 בהפרדה ({len(groups['separate'])})",
        f"⛔ לא לשלב ({len(groups['avoid'])})",
    ])
    with tab_ok:
        st.markdown(compat_group_html(groups["ok"], False, chosen), unsafe_allow_html=True)
    with tab_separate:
        st.caption("שילוב בעייתי באותה שגרה, אבל אפשר להשתמש בשניהם אם אחד בבוקר והשני בערב.")
        st.markdown(compat_group_html(groups["separate"], True, chosen), unsafe_allow_html=True)
    with tab_avoid:
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

HERO_HTML = """
<div class="hero">
    <div class="scan"></div>
    <div class="badge"><i></i>INGREDIENT SCANNER</div>
    <h1>בדיקת רכיבים</h1>
    <p>מצלמים את רשימת הרכיבים, והמערכת סורקת כל רכיב ומראה מה באמת יש במוצר.</p>
    <div class="steps">
        <span><b>01</b> צילום</span>
        <span><b>02</b> סריקה</span>
        <span><b>03</b> הבנה</span>
    </div>
</div>
"""

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
    st.session_state.product_hits = None
    st.session_state.product_lines = []
    st.session_state.product_pool = {}
    st.session_state.product_rejected = []
    st.session_state.product_queries_used = []
    st.session_state.product_obf_tried = False
    st.session_state.uploader_key += 1
    st.session_state.manual_key += 1


top_left, top_right, _ = st.columns([1, 1, 2])
top_left.button("🔄 התחלה מחדש", on_click=reset_all, key="restart")
top_right.link_button("🌐 אתר דקל", "https://thedekel.co.il/#milonid")

st.markdown(HERO_HTML, unsafe_allow_html=True)

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

if st.session_state.celebrate_id:
    st.markdown(
        f'<div class="celebrate-marker" data-id="{st.session_state.celebrate_id}"></div>',
        unsafe_allow_html=True,
    )


def section(icon, title, subtitle="", index=""):
    sub = f'<div class="s">{html.escape(subtitle)}</div>' if subtitle else ""
    num = f'<div class="n">{html.escape(index)}</div>' if index else ""
    st.markdown(
        f'<div class="sec"><div class="ico">{icon}</div>'
        f'<div><div class="t">{html.escape(title)}</div>{sub}</div>{num}</div>',
        unsafe_allow_html=True,
    )


@st.cache_resource(show_spinner="טוענת מנוע זיהוי טקסט (רק בפעם הראשונה)...")
def get_ocr_reader():
    return easyocr.Reader(["en"], gpu=False)


@st.cache_data(show_spinner=False)
def extract_text(image_bytes):
    image = open_image(io.BytesIO(image_bytes))
    processed_image = preprocess_for_ocr(image)
    results = get_ocr_reader().readtext(np.array(processed_image), detail=0, canvas_size=MAX_OCR_SIDE)
    extracted_text = " ".join(results)
    # מסירה כותרות "Ingredients" שנדבקות לרכיב הראשון בלי פסיק
    return re.sub(
        r"^[\W_]*(?:(?:ingredients?|ingr[eé]dients?)[\s:/]*|\d+\s+)*",
        "",
        extracted_text,
        flags=re.IGNORECASE,
    )


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


def preprocess_for_ocr(image):
    # גודל אחיד וחסום (עד MAX_OCR_SIDE) + ניגודיות + חידוד: עוזרים ל-OCR לתפוס פסיקים
    # קטנים בלי לנפח את הזיכרון בתמונות גדולות מהטלפון
    image = image.convert("RGB")
    scale = min(2.0, MAX_OCR_SIDE / max(image.size))
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


def check_all(ingredients):
    todo = [ing for ing in ingredients if ing not in st.session_state.results]
    if not todo:
        return
    started = time.time()
    bar = st.progress(0.0, text="מתחילה סריקה...")

    def on_progress(done, total, name):
        bar.progress(done / total, text=f"{done}/{total} · {name}")

    st.session_state.results.update(search_many(todo, on_progress))
    bar.empty()
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
        f'<div class="score-card {kind}">'
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


def show_summary(ingredients):
    checked = [ing for ing in ingredients if ing in st.session_state.results]
    if not checked:
        return

    results = [st.session_state.results[ing] for ing in checked]
    good = sum(r["emoji"] == "🟢" for r in results)
    caution = sum(r["emoji"] == "🟡" for r in results)
    bad = sum(r["emoji"] == "🔴" for r in results)
    other = len(results) - good - caution - bad

    section("📊", "תוצאות הסריקה", f"{len(results)} רכיבים נסרקו", "03")
    for card in (score_card_html(results), warnings_card_html(checked, results)):
        if card:
            st.markdown(card, unsafe_allow_html=True)
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

    section("🗂️", "קבוצות לפי שימוש", "מה כל רכיב עושה במוצר")
    st.markdown(usage_groups_html(checked, st.session_state.results), unsafe_allow_html=True)

    section("🧭", "מדריך שימוש", "מתי, באיזו תדירות ועם מה לא כדאי לשלב")
    st.markdown(
        guide_html(checked, st.session_state.results, st.session_state.product_meta),
        unsafe_allow_html=True,
    )

    rows = "".join(
        f'<tr style="--i:{min(index, 14)}">'
        f'<td class="ing">{html.escape(ing)}</td>'
        f'<td><span class="chip {EMOJI_CLASS.get(r["emoji"], "other")} {grade_class(r)}">'
        f'{html.escape(r["emoji"])} {html.escape(r["rating"])}</span></td>'
        f'<td>{html.escape(r["role"])}</td>'
        "</tr>"
        for index, (ing, r) in enumerate((ing, st.session_state.results[ing]) for ing in checked)
    )
    st.markdown(
        '<div class="table-wrap"><table class="ing-table">'
        "<thead><tr><th>רכיב</th><th>דירוג</th><th>תפקיד</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>",
        unsafe_allow_html=True,
    )

    flagged = [ing for ing in checked if st.session_state.results[ing]["emoji"] in ("🟡", "🔴")]
    if flagged:
        section("⚡", "כדאי לשים לב", "רכיבים שמסומנים כ'על תנאי' או 'רע'")
        for ing in flagged:
            r = st.session_state.results[ing]
            with st.expander(f"{r['emoji']} {ing} · {r['rating']}"):
                render_result(ing, r)


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
        f'<div class="pseal">{seal}</div>{paragraphs}{link}</div></div>'
    )


def _remember_pool(products):
    pool = st.session_state.product_pool
    for product in products:
        if product.get("slug"):
            pool[product["slug"]] = product


def identify_product(image_bytes):
    st.session_state.product_candidates = []
    st.session_state.product_source = None
    st.session_state.product_pool = {}
    st.session_state.product_rejected = []
    st.session_state.product_queries_used = []
    st.session_state.product_obf_tried = False

    with st.spinner("קוראת את הטקסט מהתמונה..."):
        lines = ocr_lines(image_bytes)
    st.session_state.product_lines = lines
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
TAB_INGREDIENTS, TAB_PRODUCT, TAB_COMPAT, TAB_COUPONS = "🧪 בדיקת רכיבים", "📦 זיהוי מוצר", "🤝 שילובים", "🎟️ קופונים"


def search_by_typed_name(term):
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
    st.rerun()


def choose_dekel_product(product):
    try:
        with st.spinner("מביאה את הרכיבים והדירוגים מדקל..."):
            names, results, meta = load_product_ingredients(product["slug"])
    except Exception:
        st.error("לא הצלחתי להביא את הרכיבים של המוצר כרגע. נסי שוב בעוד רגע.")
        return

    st.session_state.results.update(results)
    st.session_state.ocr_ingredients_text = "\n".join(names)
    st.session_state.selected_product = product
    st.session_state.product_meta = meta
    st.session_state.product_candidates = []
    st.session_state.product_status = None
    st.session_state.product_hits = None
    if product.get("verified"):
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
                    choose_dekel_product(product)
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


def selected_product_banner():
    product = st.session_state.selected_product
    if not product:
        return
    if product.get("source") == "obf":
        label = f"{product.get('company', '')} · {product['name']}".strip(" ·")
        st.info(f"המוצר: {label}. רשימת הרכיבים נלקחה מ-Open Beauty Facts ולא מדקל, וייתכן שהיא שונה מהאריזה שלך.")
    else:
        st.markdown(product_card_html(product), unsafe_allow_html=True)
    st.link_button("📊 טבלת ערבובי רכיבים של דקל", CONFLICTS_URL)


def tab_ingredients():
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

            if st.button("✨ סרקי את הרכיבים מהתמונה"):
                started = time.time()
                with st.spinner("קוראת את הטקסט מהתמונה..."):
                    extracted_text = extract_text(uploaded_image.getvalue())
                st.caption(f"זיהוי הטקסט לקח {time.time() - started:.0f} שניות")

                with st.expander("הטקסט הגולמי שזוהה"):
                    st.code(extracted_text, language=None, wrap_lines=True)

                ingredients = split_ingredients(extracted_text.replace(";", ","))
                st.session_state.ocr_ingredients_text = "\n".join(ingredients)
                st.session_state.selected_product = None
                st.session_state.product_meta = None

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

            if st.button(f"🚀 סרקי את כל הרכיבים ({len(ingredient_list)})", type="primary"):
                check_all(ingredient_list)

            show_summary(ingredient_list)

            with st.expander("בדיקה של רכיב בודד מהרשימה"):
                for ing in ingredient_list:
                    if st.button(f"בדקי {ing}", key=f"img_{ing}"):
                        st.session_state.selected_ingredient = ing
    else:
        st.info("עוד אין רכיבים לבדיקה. צלמי רשימת רכיבים, או זהי מוצר בלשונית 'זיהוי מוצר'.")

    if st.session_state.selected_ingredient:
        show_result(st.session_state.selected_ingredient)

    with st.container(border=True):
        section("🔎", "חיפוש רכיב בודד", "כותבים שם של רכיב באנגלית, למשל Niacinamide")
        ingredient_name = st.text_input(
            "הכניסי רכיב:",
            label_visibility="collapsed",
            placeholder="Niacinamide",
            key=f"manual_{st.session_state.manual_key}",
        )

        if st.button("חפשי רכיב"):
            if ingredient_name.strip():
                show_result(ingredient_name.strip())
            else:
                st.warning("נא להזין שם רכיב")


def tab_product():
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
        product_term = st.text_input(
            "שם מוצר או מותג",
            label_visibility="collapsed",
            placeholder="The Ordinary, CeraVe, Cetaphil...",
            key=f"product_{st.session_state.manual_key}",
        )

        if st.button("בדקי מוצר", key="product_search"):
            if len(product_term.strip()) < 2:
                st.warning("נא להזין שם מוצר או מותג")
            else:
                try:
                    with st.spinner("מחפשת בקטלוג של דקל..."):
                        st.session_state.product_hits = search_products(product_term.strip())
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


def tab_compat():
    product = st.session_state.selected_product
    if not (product and product.get("slug") and product.get("source") != "obf"):
        st.info(
            "כדי לראות אילו מוצרים אפשר לשלב, בחרי מוצר של דקל בלשונית 'זיהוי מוצר'. "
            "ההשוואה זמינה למוצרים שנמצאו בקטלוג של דקל."
        )
        st.link_button("📊 טבלת ערבובי רכיבים של דקל", CONFLICTS_URL)
        return
    selected_product_banner()
    names = split_ingredients(st.session_state.ocr_ingredients_text)
    show_compatibility(names, st.session_state.results, product, st.session_state.product_meta)


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


NAV_TABS = [TAB_INGREDIENTS, TAB_PRODUCT, TAB_COMPAT, TAB_COUPONS]
if st.session_state.get("goto_tab") in NAV_TABS:
    st.session_state.nav = st.session_state.pop("goto_tab")
if st.session_state.get("nav") not in NAV_TABS:
    st.session_state.nav = TAB_INGREDIENTS

active_tab = st.segmented_control("ניווט", NAV_TABS, key="nav", label_visibility="collapsed") or TAB_INGREDIENTS
{
    TAB_INGREDIENTS: tab_ingredients,
    TAB_PRODUCT: tab_product,
    TAB_COMPAT: tab_compat,
    TAB_COUPONS: tab_coupons,
}[active_tab]()

st.markdown(
    '<div class="to-top-wrap"><a class="to-top" href="#">↑ חזרה למעלה</a></div>'
    '<div class="footer">אפליקציה לא רשמית ולא קשורה לדקל. המידע מבוסס על מילון הרכיבים והקטלוג באתר דקל.</div>',
    unsafe_allow_html=True,
)
