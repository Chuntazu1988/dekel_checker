"""בניית שגרת טיפוח מומלצת מהקטלוג לפי פרופיל המשתמשת ורמת השגרה שבחרה. בלי תלות ב-Streamlit."""

from compat import EXFOLIANTS, compare_actives

LEVELS = {
    "easy": ("קלה", "3 שלבים: ניקוי, קרם לחות וקרם הגנה"),
    "medium": ("בינונית", "4 שלבים: ניקוי, סרום, קרם וקרם הגנה"),
    "full": ("מורכבת", "5 עד 6 שלבים: גם טונר ואזור העיניים"),
}

STEPS = {
    "easy": {"AM": ["ניקוי פנים", "קרם פנים", "קרם הגנה"], "PM": ["ניקוי פנים", "קרם פנים"]},
    "medium": {"AM": ["ניקוי פנים", "סרום", "קרם פנים", "קרם הגנה"], "PM": ["ניקוי פנים", "סרום", "קרם פנים"]},
    "full": {
        "AM": ["ניקוי פנים", "טונר ואסנס", "סרום", "אזור העיניים", "קרם פנים", "קרם הגנה"],
        "PM": ["ניקוי פנים", "טונר ואסנס", "סרום", "אזור העיניים", "קרם פנים"],
    },
}
MAX_STRONG = {"easy": 0, "medium": 1, "full": 2}
STRONG = EXFOLIANTS | {"retinoid", "benzoyl"}

# פעילים שמתאימים לכל מטרה, ומילות מפתח בשם המוצר לכל סוג עור או מטרה
CONCERN_ACTIVES = {
    "acne": {"bha", "benzoyl"}, "pigment": {"vitc", "aha"}, "aging": {"retinoid", "vitc"},
}
KEYWORDS = {
    "oily": ("niacinamide", "gel", "oil-free", "ניאצינמיד", "ג'ל", "זנב"),
    "combo": ("niacinamide", "gel", "ניאצינמיד", "ג'ל"),
    "dry": ("ceramide", "hyaluron", "squalane", "panthenol", "צרמיד", "היאלורון", "סקוואלן", "פנתנול", "cream", "balm"),
    "sensitive": ("centella", "madecassoside", "panthenol", "calm", "soothing", "סנטלה", "מרגיע", "פנתנול", "sensitive"),
    "redness": ("centella", "madecassoside", "calm", "soothing", "azelaic", "סנטלה", "מרגיע"),
    "dryness": ("ceramide", "hyaluron", "squalane", "צרמיד", "היאלורון", "סקוואלן"),
    "acne": ("salicylic", "niacinamide", "azelaic", "סליצילית", "ניאצינמיד", "acne", "blemish"),
    "pigment": ("vitamin c", "tranexamic", "arbutin", "azelaic", "ויטמין c", "ניאצינמיד", "niacinamide"),
    "aging": ("retinol", "retinal", "peptide", "רטינול", "פפטיד"),
}
GENTLE_PROFILE = ("sensitive",)
GENTLE_CONCERNS = ("redness", "dryness")


def _text(entry):
    return " ".join([entry.get("name", ""), entry.get("name2", ""), entry.get("company", "")]).lower()


def _allowed(entry, slot, profile, strong_ok):
    actives = set(entry.get("actives", []))
    time = entry.get("time")
    if time == "MORNING" and slot == "PM" or time == "EVENING" and slot == "AM":
        return False
    if slot == "AM" and (actives & STRONG - {"benzoyl"} or "retinoid" in actives):
        return False
    if slot == "PM" and "vitc" in actives:
        return False
    if profile["preg"] and (entry.get("pregnancy_ok") is False or "retinoid" in actives):
        return False
    text = _text(entry)
    if any(word and word in text for word in profile["avoid"]):
        return False
    gentle = profile["skin"] in GENTLE_PROFILE or any(c in GENTLE_CONCERNS for c in profile["concerns"])
    if actives & STRONG and (gentle or not strong_ok):
        return False
    return True


def _score(entry, slot, profile, other_slot_pick):
    actives = set(entry.get("actives", []))
    text = _text(entry)
    score, reasons = 0.0, []
    if entry.get("verified"):
        score += 2
    for concern in profile["concerns"]:
        hit = actives & CONCERN_ACTIVES.get(concern, set())
        if hit:
            score += 3
            reasons.append("מתאים ל" + {"acne": "אקנה", "pigment": "פיגמנטציה", "aging": "סימני גיל"}[concern])
        elif any(word in text for word in KEYWORDS.get(concern, ())):
            score += 1.5
            reasons.append("מתאים ל" + {"acne": "אקנה", "pigment": "פיגמנטציה", "aging": "סימני גיל", "redness": "אדמומיות", "dryness": "יובש"}[concern])
    skin_words = KEYWORDS.get(profile["skin"], ())
    if skin_words and any(word in text for word in skin_words):
        score += 1.5
        reasons.append("מתאים ל" + {"oily": "עור שמן", "combo": "עור מעורב", "dry": "עור יבש", "sensitive": "עור רגיש"}.get(profile["skin"], "העור שלך"))
    if other_slot_pick == entry["slug"]:
        score += 1.5
        reasons.append("אותו מוצר גם בשעה השנייה, כדי לפשט")
    if slot == "PM" and actives & STRONG:
        score += 0.5
    return score, reasons


def recommend(profile, level, entries, categories_of):
    """מחזירה {"AM": [(קטגוריה, מוצר, [סיבות])], "PM": [...]} ורשימת הערות."""
    level = level if level in STEPS else "easy"
    strong_budget = MAX_STRONG[level]
    plan = {"AM": [], "PM": []}
    picked_other = {}
    notes = []

    # מתחילים בערב כי שם נמצאים הפעילים החזקים, ואז הבוקר משתמש בבחירות כדי לפשט
    for slot in ("PM", "AM"):
        chosen = []
        strong_used = 0
        for category in STEPS[level][slot]:
            best = None
            for entry in entries:
                if category not in categories_of(entry):
                    continue
                strong_ok = strong_used < strong_budget
                if not _allowed(entry, slot, profile, strong_ok):
                    continue
                if any(compare_actives(set(entry.get("actives", [])), set(other.get("actives", []))) for _c, other, _r in chosen):
                    continue
                score, reasons = _score(entry, slot, profile, picked_other.get(category))
                key = (score, entry["name"])
                if best is None or key[0] > best[0][0] or (key[0] == best[0][0] and key[1] < best[0][1]):
                    best = (key, entry, reasons)
            if best:
                entry, reasons = best[1], best[2]
                chosen.append((category, entry, reasons))
                if set(entry.get("actives", [])) & STRONG:
                    strong_used += 1
                if slot == "PM":
                    picked_other[category] = entry["slug"]
        plan[slot] = chosen

    missing = [c for slot in plan for c in STEPS[level][slot] if c not in [x[0] for x in plan[slot]]]
    if missing:
        notes.append(("ℹ️", "לא מצאנו בקטלוג מוצר מתאים לשלבים: " + ", ".join(dict.fromkeys(missing)) + ". אפשר להוסיף מוצר משלך בחיפוש."))
    if profile["preg"]:
        notes.append(("🤰", "הסתפקנו במוצרים שדקל לא מסמנת כבעייתיים בהיריון והנקה, ובלי רטינואידים."))
    if profile["skin"] in GENTLE_PROFILE or any(c in GENTLE_CONCERNS for c in profile["concerns"]):
        notes.append(("🌿", "בגלל רגישות, אדמומיות או יובש לא כללנו חומצות מקלפות או רטינואידים."))
    elif level == "easy":
        notes.append(("✨", "שגרה קלה כוללת רק מוצרים עדינים. כשתרצי לטפל במטרה ספציפית, עברי לרמה בינונית."))
    return plan, notes
