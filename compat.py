"""זיהוי פעילים בולטים בתכשיר, וכללי שילוב בין תכשירים. בלי תלות ב-Streamlit."""

import re

ACTIVE_PATTERNS = {
    "retinoid": re.compile(r"\b(retinol|retinal|retinaldehyde|retinyl|tretinoin|adapalene|tazarotene|retinoate)\b", re.I),
    "aha": re.compile(r"\b(glycolic acid|lactic acid|mandelic acid)\b", re.I),
    "bha": re.compile(r"\b(salicylic acid|betaine salicylate)\b", re.I),
    "pha": re.compile(r"\b(gluconolactone|lactobionic acid)\b", re.I),
    "vitc": re.compile(r"^(l-)?ascorbic acid$", re.I),
    "benzoyl": re.compile(r"benzoyl peroxide", re.I),
    "copper": re.compile(r"copper (tri)?peptide|copper tripeptide|copper peptide", re.I),
}

ACTIVE_NAMES = {
    "retinoid": "רטינואידים",
    "aha": "חומצות AHA",
    "bha": "חומצת BHA",
    "pha": "חומצות PHA",
    "vitc": "ויטמין C (חומצה אסקורבית)",
    "benzoyl": "בנזואיל פרוקסיד",
    "copper": "פפטידים של נחושת",
}

EXFOLIANTS = {"aha", "bha", "pha"}

# (פעילים בצד אחד, פעילים בצד שני, הסבר, האם הכלל חל בשני הכיוונים)
MIX_RULES = [
    ({"retinoid"}, {"aha", "bha", "pha"}, "השילוב עלול להעלות סיכון לגירוי ויובש. כדאי להפריד בין ערבים.", True),
    ({"retinoid"}, {"benzoyl"}, "השילוב עלול לגרות. כדאי להפריד בין בוקר לערב או בין ימים.", True),
    ({"retinoid"}, {"vitc"}, "כדאי להפריד: ויטמין C בבוקר ורטינואיד בערב.", True),
    ({"aha", "bha", "pha"}, {"vitc"}, "שניהם חומציים וממריצים גירוי. כדאי להפריד בין בוקר לערב.", True),
    ({"copper"}, {"aha", "bha", "pha", "vitc"}, "חומצות עלולות לפגוע בפעילות הפפטידים. כדאי להפריד.", False),
]


def detect_actives_from_names(entries, key_ingredients=(), top=15):
    """entries: רצף של (שם רכיב, מזהה קבוצה, לא בשימוש) לפי סדר הרשימה.
    הזיהוי לפי שם הרכיב בלבד: "קבוצות הפעילים" של דקל כוללות גם רכיבים כמו מסנני קרינה, ולכן לא מתאימות.
    רכיב נחשב רק אם הוא באחד המקומות הראשונים ברשימה (ריכוז משמעותי) או שדקל סימנה אותו כרכיב מפתח."""
    found = set()
    key_lower = {k.lower() for k in key_ingredients}
    for index, (name, group_id) in enumerate(entries):
        if index >= top and name.lower() not in key_lower:
            continue
        for code, pattern in ACTIVE_PATTERNS.items():
            if pattern.search(name):
                found.add(code)
    return found


def active_label(codes):
    return " / ".join(ACTIVE_NAMES[code] for code in sorted(codes))


def mixing_advice(actives):
    """מה לא כדאי לשלב עם מוצר שיש בו את הפעילים האלה (מוצרים אחרים בשגרה)."""
    advice = []
    for side_a, side_b, reason, both_ways in MIX_RULES:
        mine, other = actives & side_a, actives & side_b
        if mine and not other:
            advice.append((f"לא לשלב עם: {active_label(side_b)}", f"במוצר יש {active_label(mine)}. {reason}"))
        elif both_ways and other and not mine:
            advice.append((f"לא לשלב עם: {active_label(side_a)}", f"במוצר יש {active_label(other)}. {reason}"))
    if actives & EXFOLIANTS:
        advice.append(("לא לשלב עם: עוד פילינג או קרצוף", "כדאי לא להפעיל כמה סוגי פילינג באותו יום."))
    return advice


def compare_actives(first, second):
    """רשימת התנגשויות בין שני תכשירים שונים לפי הפעילים שלהם: [(כותרת, הסבר)]."""
    conflicts = []
    seen = set()
    for side_a, side_b, reason, _both_ways in MIX_RULES:
        crossing = (first & side_a and second & side_b) or (second & side_a and first & side_b)
        if crossing:
            title = f"{active_label(side_a & (first | second))} + {active_label(side_b & (first | second))}"
            if title not in seen:
                seen.add(title)
                conflicts.append((title, reason))
    if first & EXFOLIANTS and second & EXFOLIANTS:
        conflicts.append(("כפל חומצות פילינג", "שניהם מכילים חומצות פילינג. שימוש יחד באותו יום עלול לייבש ולגרות."))
    if "retinoid" in first and "retinoid" in second:
        conflicts.append(("כפל רטינואידים", "שני מוצרים עם רטינואידים באותה שגרה מעלים סיכון לגירוי."))
    return conflicts
