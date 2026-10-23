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
        return False

    login_field = page.locator('input[name="user_login"]:visible').first
    pass_field = page.locator('input[name="user_password"]:visible').first
    login_field.click()
    login_field.fill(email)
    pass_field.click()
    pass_field.fill(password)

    login_btn = page.locator('a[data-id="masterstudy-authorization-login-button"]:visible').first
    login_btn.click(timeout=5000)
    page.wait_for_timeout(4000)

    return page.locator('input[name="user_password"]:visible').count() == 0


def _debug_info(page):
    try:
        body = page.locator("body").inner_text()[:300].replace(chr(10), " | ")
        count = page.locator(".suggestion-item").count()
        return f"[debug] title={page.title()!r} url={page.url} suggestions={count} body={body}"
    except Exception as e:
        return f"[debug] {e}"


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


def search_dekel(ingredient):
    env = load_env()
    ensure_playwright_browser()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = _new_context(p, browser)
            page = context.new_page()
            page.goto("https://thedekel.co.il/#milonid", timeout=30000)
            page.wait_for_timeout(4000)

            search_box = page.locator('input[type="search"]:visible').first
            search_box.click()
            search_box.fill(ingredient)
            page.wait_for_timeout(2000)

            suggestion = _find_suggestion(page, ingredient)
            if suggestion is None:
                return f"❓ הרכיב \"{ingredient}\" לא נמצא במילון של דקל\n\n" + _debug_info(page)

            try:
                suggestion.click(timeout=5000)
            except Exception:
                return f"❌ לא הצלחתי ללחוץ על תוצאת החיפוש עבור: {ingredient}"

            page.wait_for_timeout(3000)

            # אם מופיע טופס התחברות - נתחבר ונחזור על החיפוש
            if page.locator('input[name="user_password"]:visible').count() > 0:
                logged_in = _login(page, env)
                if not logged_in:
                    return (
                        "🔒 האתר דורש התחברות כדי להציג את המידע על הרכיב, "
                        "וההתחברות האוטומטית נכשלה. ודאי ש-DEKEL_EMAIL ו-DEKEL_PASSWORD "
                        "בקובץ .env נכונים."
                    )

                context.storage_state(path=str(SESSION_PATH))

                search_box = page.locator('input[type="search"]:visible').first
                search_box.click()
                search_box.fill(ingredient)
                page.wait_for_timeout(2000)

                suggestion = _find_suggestion(page, ingredient)
                if suggestion is None:
                    return f"❓ הרכיב \"{ingredient}\" לא נמצא במילון של דקל\n\n" + _debug_info(page)
                suggestion.click(timeout=5000)
                page.wait_for_timeout(3000)

            text = page.locator("body").inner_text()
            parsed = _parse_ingredient_block(text, ingredient)

            if parsed is None:
                return f"⚠️ לא הצלחתי לאתר מידע על \"{ingredient}\" בעמוד שנפתח"

            role_line, rating_word, description = parsed

            if rating_word is None:
                return f"⚪ עדיין אין דירוג באתר דקל עבור \"{ingredient}\"\n\n{role_line}\n\n{description}"

            emoji = RATING_COLORS.get(rating_word, "⚪")
            return f"{emoji} {rating_word}\n\n{role_line}\n\n{description}"
        finally:
            browser.close()


# ======================
# UI
# ======================

st.title("🔍 בדיקת רכיבים")
st.link_button("🌐 פתח את אתר דקל", "https://thedekel.co.il/#milonid")

if "ocr_ingredients_text" not in st.session_state:
    st.session_state.ocr_ingredients_text = ""
if "selected_ingredient" not in st.session_state:
    st.session_state.selected_ingredient = None


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
    return [
        ing.strip(" .")
        for ing in re.split(r",(?!\d)|;|\n", text)
        if len(ing.strip(" .")) > 2
    ]


def show_result(ingredient):
    with st.spinner(f"בודקת את {ingredient} באתר דקל..."):
        result = search_dekel(ingredient)
    if "🔴" in result:
        st.error(result)
    elif "🟡" in result:
        st.warning(result)
    elif "🟢" in result:
        st.success(result)
    else:
        st.info(result)


st.subheader("📸 חיפוש לפי תמונת רכיבים")

uploaded_image = st.file_uploader("העלי תמונה של רשימת רכיבים", type=["jpg", "jpeg", "png"])

if uploaded_image:
    image = Image.open(uploaded_image)
    st.image(image, caption="התמונה שהועלתה", use_container_width=True)

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

        for ing in split_ingredients(st.session_state.ocr_ingredients_text):
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
