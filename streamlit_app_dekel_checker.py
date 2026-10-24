import html
import io
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import streamlit as st
from playwright.sync_api import sync_playwright
from PIL import Image, ImageOps, ImageFilter
import easyocr

st.set_page_config(page_title="Dekel Checker", layout="centered")

EMOJI_CLASS = {"🟢": "good", "🟡": "caution", "🔴": "bad"}

st.markdown(
    f"<style>{(Path(__file__).parent / 'style.css').read_text(encoding='utf-8')}</style>",
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


def _result(emoji, rating, text, role="", description="", notes=(), flags=()):
    return {
        "emoji": emoji, "rating": rating, "text": text, "role": role,
        "description": description, "notes": list(notes), "flags": list(flags),
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
            roles, description, notes, flags,
        )

    emoji = RATING_COLORS.get(rating, "⚪")
    return _result(emoji, rating, f"{emoji} {rating}\n\n{body}", roles, description, notes, flags)


@st.cache_resource
def _ingredient_cache():
    return {}


CACHE_TTL_SECONDS = 24 * 60 * 60


def search_many(ingredients, on_progress=None):
    ingredients = list(dict.fromkeys(ingredients))
    cache = _ingredient_cache()
    now = time.time()
    cached = {
        ing: cache[ing.lower()][1]
        for ing in ingredients
        if ing.lower() in cache and now - cache[ing.lower()][0] < CACHE_TTL_SECONDS
    }
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
    image = Image.open(io.BytesIO(image_bytes))
    processed_image = preprocess_for_ocr(image)
    results = get_ocr_reader().readtext(np.array(processed_image), detail=0)
    extracted_text = " ".join(results)
    # מסירה כותרות "Ingredients" שנדבקות לרכיב הראשון בלי פסיק
    return re.sub(r"^(?:ingredients\s*:?\s*|\d+\s+)+", "", extracted_text, flags=re.IGNORECASE)


def preprocess_for_ocr(image):
    # הגדלת רזולוציה + ניגודיות + חידוד עוזרות ל-OCR לתפוס פסיקים
    # קטנים שהוא נוטה לפספס בגבולות בין אזורי טקסט
    scale = 2
    image = image.convert("RGB").resize((image.width * scale, image.height * scale), Image.LANCZOS)
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
    chip = f'<span class="chip {kind}">{html.escape(result["emoji"])} {html.escape(result["rating"])}</span>'
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
        f'<div class="rcard {kind}"><div class="rc-head">'
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

    rows = "".join(
        f'<tr style="--i:{min(index, 14)}">'
        f'<td class="ing">{html.escape(ing)}</td>'
        f'<td><span class="chip {EMOJI_CLASS.get(r["emoji"], "other")}">'
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


with st.container(border=True):
    section("📸", "צילום של רשימת הרכיבים", "מעלות תמונה ברורה של הרכיבים מאחורי האריזה", "01")
    uploaded_image = st.file_uploader(
        "העלי תמונה של רשימת רכיבים",
        type=["jpg", "jpeg", "png"],
        label_visibility="collapsed",
        key=f"uploader_{st.session_state.uploader_key}",
    )
    st.caption("📱 בטלפון: לוחצים על Upload ובוחרים 'צילום' כדי לצלם ישירות במצלמה")

    if uploaded_image:
        image = Image.open(uploaded_image)
        st.image(image, caption="התמונה שהועלתה", width="stretch")

        if st.button("✨ סרקי את הרכיבים מהתמונה"):
            started = time.time()
            with st.spinner("קוראת את הטקסט מהתמונה..."):
                extracted_text = extract_text(uploaded_image.getvalue())
            st.caption(f"זיהוי הטקסט לקח {time.time() - started:.0f} שניות")

            with st.expander("הטקסט הגולמי שזוהה"):
                st.code(extracted_text, language=None, wrap_lines=True)

            ingredients = split_ingredients(extracted_text.replace(";", ","))
            st.session_state.ocr_ingredients_text = "\n".join(ingredients)

if uploaded_image and st.session_state.ocr_ingredients_text:
    with st.container(border=True):
        section("📝", "הרכיבים שזוהו", "ה-OCR לא תמיד מדויק, אפשר לתקן כאן (רכיב אחד בכל שורה)", "02")
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

        with st.expander("בדיקה של רכיב בודד"):
            for ing in ingredient_list:
                if st.button(f"בדקי {ing}", key=f"img_{ing}"):
                    st.session_state.selected_ingredient = ing

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

st.markdown(
    '<div class="to-top-wrap"><a class="to-top" href="#">↑ חזרה למעלה</a></div>'
    '<div class="footer">המידע מבוסס על מילון הרכיבים של דקל</div>',
    unsafe_allow_html=True,
)
