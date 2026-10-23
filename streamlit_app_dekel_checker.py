import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import streamlit as st
from playwright.sync_api import sync_playwright
from PIL import Image, ImageOps, ImageFilter
import easyocr

st.set_page_config(page_title="Dekel Checker", layout="centered")

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


def _parse_ingredient_block(text, ingredient):
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    start_index = -1
    for i, line in enumerate(lines):
        if line.lower() == ingredient.lower():
            start_index = i
            break

    if start_index == -1:
        return None

    block = []
    for line in lines[start_index + 1:]:
        if line.startswith("מצאת טעות") or line.startswith("המידע באתר"):
            break
        block.append(line)
        if len(block) > 30:
            break

    role_line = next((l for l in block if l.startswith(">")), "")

    # מילת הדירוג היא השורה הראשונה (הקצרה) שמופיעה אחרי שורת התפקיד
    # ואחרי "שמות נוספים לאותו הרכיב" (אם קיימת), ולפני פסקת ההסבר הארוכה
    after_role = block[block.index(role_line) + 1:] if role_line in block else block
    after_role = [l for l in after_role if l != "שמות נוספים לאותו הרכיב"]
    rating_word = after_role[0] if after_role and len(after_role[0]) <= 12 else None

    skip = {role_line, rating_word, "שמות נוספים לאותו הרכיב"}
    description = "\n".join(l for l in block if l not in skip).strip()

    return role_line, rating_word, description


DICTIONARY_URL = "https://thedekel.co.il/#milonid"


def _result(emoji, rating, text, role=""):
    role = role.split(":", 1)[-1].strip() if role else ""
    return {"emoji": emoji, "rating": rating, "text": text, "role": role}


def _open_dictionary(page):
    page.goto(DICTIONARY_URL, timeout=30000)
    page.reload()
    page.wait_for_timeout(4000)


def _read_ingredient(page, ingredient):
    parsed = None
    for _ in range(3):
        parsed = _parse_ingredient_block(page.locator("body").inner_text(), ingredient)
        if parsed is not None:
            break
        page.wait_for_timeout(1500)
    return parsed


def _lookup(page, context, env, ingredient):
    _type_search(page, ingredient)
    suggestion = _wait_for_suggestion(page, ingredient)
    if suggestion is None:
        return _result("❓", "לא נמצא", f"❓ הרכיב \"{ingredient}\" לא נמצא במילון של דקל")

    try:
        suggestion.click(timeout=5000)
    except Exception:
        return _result("❌", "שגיאה", f"❌ לא הצלחתי ללחוץ על תוצאת החיפוש עבור: {ingredient}")

    page.wait_for_timeout(2500)

    # אם מופיע טופס התחברות - נתחבר ונחזור על החיפוש
    if page.locator('input[name="user_password"]:visible').count() > 0:
        logged_in = _login(page, env)
        if logged_in is not True:
            return _result(
                "🔒",
                "נדרשת התחברות",
                "🔒 האתר דורש התחברות כדי להציג את המידע על הרכיב, "
                "וההתחברות האוטומטית נכשלה. ודאי ש-DEKEL_EMAIL ו-DEKEL_PASSWORD "
                "מוגדרים נכון.",
            )

        context.storage_state(path=str(SESSION_PATH))
        _open_dictionary(page)
        _type_search(page, ingredient)
        suggestion = _wait_for_suggestion(page, ingredient)
        if suggestion is None:
            return _result("❓", "לא נמצא", f"❓ הרכיב \"{ingredient}\" לא נמצא במילון של דקל")
        suggestion.click(timeout=5000)
        page.wait_for_timeout(2500)

    parsed = _read_ingredient(page, ingredient)
    if parsed is None:
        return _result("⚠️", "לא אותר", f"⚠️ לא הצלחתי לאתר מידע על \"{ingredient}\" בעמוד שנפתח")

    role_line, rating_word, description = parsed

    if rating_word is None:
        return _result(
            "⚪",
            "אין דירוג",
            f"⚪ עדיין אין דירוג באתר דקל עבור \"{ingredient}\"\n\n{role_line}\n\n{description}",
            role_line,
        )

    emoji = RATING_COLORS.get(rating_word, "⚪")
    return _result(emoji, rating_word, f"{emoji} {rating_word}\n\n{role_line}\n\n{description}", role_line)


def search_many(ingredients, on_progress=None):
    env = load_env()
    ensure_playwright_browser()
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = _new_context(p, browser)
            page = context.new_page()
            _open_dictionary(page)

            for index, ingredient in enumerate(ingredients, start=1):
                result = None
                for attempt in range(2):
                    try:
                        result = _lookup(page, context, env, ingredient)
                        break
                    except Exception:
                        try:
                            _open_dictionary(page)
                        except Exception:
                            pass
                if result is None:
                    result = _result("❌", "שגיאה", f"❌ שגיאה בבדיקת {ingredient}")

                results[ingredient] = result
                if on_progress:
                    on_progress(index, len(ingredients), ingredient)

                if result["emoji"] == "🔒":
                    for rest in ingredients[index:]:
                        results[rest] = result
                    break
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
    bar = st.progress(0.0, text="מתחילה בדיקה...")

    def on_progress(done, total, name):
        bar.progress(done / total, text=f"{done}/{total}: {name}")

    st.session_state.results.update(search_many(todo, on_progress))
    bar.empty()


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
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🟢 טובים", good)
    c2.metric("🟡 על תנאי", caution)
    c3.metric("🔴 רעים", bad)
    c4.metric("⚪ אחר", other)

    st.dataframe(
        [
            {
                "רכיב": ing,
                "דירוג": f"{st.session_state.results[ing]['emoji']} {st.session_state.results[ing]['rating']}",
                "תפקיד": st.session_state.results[ing]["role"],
            }
            for ing in checked
        ],
        width="stretch",
        hide_index=True,
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
        processed_image = preprocess_for_ocr(image)
        reader = easyocr.Reader(["en"])
        results = reader.readtext(np.array(processed_image), detail=0)
        extracted_text = " ".join(results)
        # מסירה כותרות "Ingredients" שנדבקות לרכיב הראשון בלי פסיק
        extracted_text = re.sub(r"^(?:ingredients\s*:?\s*|\d+\s+)+", "", extracted_text, flags=re.IGNORECASE)

        st.write("הטקסט שזוהה:")
        st.text(extracted_text)

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
