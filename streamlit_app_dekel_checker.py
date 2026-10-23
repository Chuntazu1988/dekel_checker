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

APP_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;700&display=swap');

html, body, .stApp, .stMarkdown, p, h1, h2, h3, h4, label, button, input, textarea, table {
    font-family: 'Heebo', 'Segoe UI', Arial, sans-serif;
}

.stApp { direction: rtl; }
.block-container { max-width: 760px; padding-top: 2.2rem; padding-bottom: 4rem; }

h1, h2, h3, h4, p, label, li,
[data-testid="stMarkdownContainer"],
[data-testid="stWidgetLabel"],
[data-testid="stCaptionContainer"],
[data-testid="stFileUploader"] section,
[data-testid="stExpander"] summary,
[data-testid="stAlert"] {
    text-align: right;
}

h1 { font-weight: 700; color: #3A2F27; margin-bottom: 0.3rem; }
h2, h3 { font-weight: 700; color: #3A2F27; }

/* שדות הקלט באנגלית: כיוון LTR אבל צמודים לימין */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea {
    direction: ltr;
    text-align: right;
    border-radius: 10px;
}

/* כפתורים אחידים */
.stButton > button, .stLinkButton > a, [data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-primary"] {
    border-radius: 10px;
    font-weight: 500;
    padding: 0.45rem 1.1rem;
}
[data-testid="stBaseButton-primary"] { background: #6B5B4B; border-color: #6B5B4B; color: #fff; }
[data-testid="stBaseButton-primary"]:hover { background: #55473A; border-color: #55473A; }

[data-testid="stAlert"], [data-testid="stExpander"] { border-radius: 12px; }
blockquote { border-left: 0 !important; border-right: 3px solid #CFC3B6; padding: 0 0.9rem 0 0 !important; margin: 0.4rem 0; }

/* אריחי סיכום */
.tiles { display: flex; gap: 0.7rem; margin: 0.4rem 0 1rem; }
.tile { flex: 1; border-radius: 14px; padding: 0.7rem 0.4rem; text-align: center; }
.tile .num { font-size: 1.9rem; font-weight: 700; line-height: 1.1; }
.tile .lbl { font-size: 0.85rem; }
.tile.good { background: #E3F4E7; color: #1F6B3A; }
.tile.caution { background: #FFF1CC; color: #7A5600; }
.tile.bad { background: #FDE2E2; color: #A32222; }
.tile.other { background: #ECE7E1; color: #5B524A; }

/* טבלת רכיבים */
.ing-table { width: 100%; border-collapse: collapse; direction: rtl; font-size: 0.95rem; margin-bottom: 1rem; }
.ing-table th { text-align: right; font-weight: 700; color: #6B5B4B; border-bottom: 2px solid #D9CEC3; padding: 0.5rem 0.6rem; }
.ing-table td { text-align: right; padding: 0.5rem 0.6rem; border-bottom: 1px solid #E8DFD5; vertical-align: middle; }
.ing-table tr:hover td { background: #F5EFE8; }
.ing-table td.ing { direction: ltr; text-align: right; unicode-bidi: plaintext; font-weight: 500; }
.chip { display: inline-block; border-radius: 999px; padding: 0.1rem 0.7rem; font-size: 0.85rem; font-weight: 500; white-space: nowrap; }
.chip.good { background: #E3F4E7; color: #1F6B3A; }
.chip.caution { background: #FFF1CC; color: #7A5600; }
.chip.bad { background: #FDE2E2; color: #A32222; }
.chip.other { background: #ECE7E1; color: #5B524A; }
</style>
"""

EMOJI_CLASS = {"🟢": "good", "🟡": "caution", "🔴": "bad"}

st.markdown(APP_CSS, unsafe_allow_html=True)

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


def _result(emoji, rating, text, role=""):
    return {"emoji": emoji, "rating": rating, "text": text, "role": role}


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
    if detail.get("increasesPhotoSensitivity") is True:
        notes.append("☀️ מגביר רגישות לשמש")
    if detail.get("contraindicatedInPregnancy") is True:
        notes.append("🤰 לא מומלץ בהיריון")
    if detail.get("contraindicatedWhileBreastfeeding") is True:
        notes.append("🍼 לא מומלץ בהנקה")

    role_line = f"> תפקיד עיקרי: {roles}" if roles else ""
    body = "\n\n".join(part for part in (role_line, description, " | ".join(notes)) if part)

    if not rating:
        return _result("⚪", "אין דירוג", f"⚪ עדיין אין דירוג באתר דקל עבור \"{name}\"\n\n{body}", roles)

    emoji = RATING_COLORS.get(rating, "⚪")
    return _result(emoji, rating, f"{emoji} {rating}\n\n{body}", roles)


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

st.title("🔍 בדיקת רכיבים")
st.link_button("🌐 פתח את אתר דקל", "https://thedekel.co.il/#milonid")

if "ocr_ingredients_text" not in st.session_state:
    st.session_state.ocr_ingredients_text = ""
if "selected_ingredient" not in st.session_state:
    st.session_state.selected_ingredient = None
if "results" not in st.session_state:
    st.session_state.results = {}


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


def render_result(result):
    if result["emoji"] == "🔴":
        st.error(result["text"])
    elif result["emoji"] == "🟡":
        st.warning(result["text"])
    elif result["emoji"] == "🟢":
        st.success(result["text"])
    else:
        st.info(result["text"])


def show_result(ingredient):
    if ingredient not in st.session_state.results:
        with st.spinner(f"בודקת את {ingredient} באתר דקל..."):
            st.session_state.results.update(search_many([ingredient]))
    render_result(st.session_state.results[ingredient])


def check_all(ingredients):
    todo = [ing for ing in ingredients if ing not in st.session_state.results]
    if not todo:
        return
    started = time.time()
    bar = st.progress(0.0, text="מתחילה בדיקה...")

    def on_progress(done, total, name):
        bar.progress(done / total, text=f"{done}/{total}: {name}")

    st.session_state.results.update(search_many(todo, on_progress))
    bar.empty()
    st.caption(f"נבדקו {len(todo)} רכיבים תוך {time.time() - started:.0f} שניות")


def show_summary(ingredients):
    checked = [ing for ing in ingredients if ing in st.session_state.results]
    if not checked:
        return

    results = [st.session_state.results[ing] for ing in checked]
    good = sum(r["emoji"] == "🟢" for r in results)
    caution = sum(r["emoji"] == "🟡" for r in results)
    bad = sum(r["emoji"] == "🔴" for r in results)
    other = len(results) - good - caution - bad

    st.subheader("📊 סיכום")
    st.markdown(
        '<div class="tiles">'
        f'<div class="tile good"><div class="num">{good}</div><div class="lbl">🟢 טובים</div></div>'
        f'<div class="tile caution"><div class="num">{caution}</div><div class="lbl">🟡 על תנאי</div></div>'
        f'<div class="tile bad"><div class="num">{bad}</div><div class="lbl">🔴 רעים</div></div>'
        f'<div class="tile other"><div class="num">{other}</div><div class="lbl">⚪ אחר</div></div>'
        "</div>",
        unsafe_allow_html=True,
    )

    rows = "".join(
        "<tr>"
        f'<td class="ing">{html.escape(ing)}</td>'
        f'<td><span class="chip {EMOJI_CLASS.get(r["emoji"], "other")}">'
        f'{html.escape(r["emoji"])} {html.escape(r["rating"])}</span></td>'
        f'<td>{html.escape(r["role"])}</td>'
        "</tr>"
        for ing, r in ((ing, st.session_state.results[ing]) for ing in checked)
    )
    st.markdown(
        '<table class="ing-table"><thead><tr><th>רכיב</th><th>דירוג</th><th>תפקיד</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>",
        unsafe_allow_html=True,
    )

    flagged = [ing for ing in checked if st.session_state.results[ing]["emoji"] in ("🟡", "🔴")]
    if flagged:
        st.subheader("⚠️ רכיבים שדורשים תשומת לב")
        for ing in flagged:
            r = st.session_state.results[ing]
            with st.expander(f"{r['emoji']} {ing} - {r['rating']}"):
                st.write(r["text"])


st.subheader("📸 חיפוש לפי תמונת רכיבים")

uploaded_image = st.file_uploader("העלי תמונה של רשימת רכיבים", type=["jpg", "jpeg", "png"])

if uploaded_image:
    image = Image.open(uploaded_image)
    st.image(image, caption="התמונה שהועלתה", width="stretch")

    if st.button("חלץ רכיבים מהתמונה"):
        started = time.time()
        with st.spinner("מזהה טקסט מהתמונה..."):
            extracted_text = extract_text(uploaded_image.getvalue())
        st.caption(f"זיהוי הטקסט לקח {time.time() - started:.0f} שניות")

        with st.expander("הטקסט הגולמי שזוהה"):
            st.code(extracted_text, language=None, wrap_lines=True)

        ingredients = split_ingredients(extracted_text.replace(";", ","))
        st.session_state.ocr_ingredients_text = "\n".join(ingredients)

    if st.session_state.ocr_ingredients_text:
        st.subheader("רכיבים שזוהו:")
        st.caption("ה-OCR לא תמיד מדויק - אפשר לתקן כאן את הרשימה (רכיב אחד בכל שורה) לפני הבדיקה")
        st.session_state.ocr_ingredients_text = st.text_area(
            "רשימת רכיבים לעריכה",
            value=st.session_state.ocr_ingredients_text,
            height=200,
            label_visibility="collapsed",
        )

        ingredient_list = split_ingredients(st.session_state.ocr_ingredients_text)

        if st.button(f"✅ בדוק את כל הרכיבים ({len(ingredient_list)})", type="primary"):
            check_all(ingredient_list)

        show_summary(ingredient_list)

        with st.expander("בדיקה של רכיב בודד"):
            for ing in ingredient_list:
                if st.button(f"בדוק {ing}", key=f"img_{ing}"):
                    st.session_state.selected_ingredient = ing

if st.session_state.selected_ingredient:
    show_result(st.session_state.selected_ingredient)

st.subheader("🔎 חיפוש רכיב ידני")
ingredient_name = st.text_input("הכניסי רכיב:")

if st.button("חפש רכיב"):
    if ingredient_name.strip():
        show_result(ingredient_name.strip())
    else:
        st.warning("נא להזין שם רכיב")
