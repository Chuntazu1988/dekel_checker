"""בניית שגרת טיפוח (בוקר וערב) ממוצרי הקטלוג, ובדיקת התנגשויות ביניהם. בלי תלות ב-Streamlit."""

from compat import EXFOLIANTS, compare_actives

LAYER_ORDER = [
    "ניקוי פנים", "טונר ואסנס", "סרום", "טיפול נקודתי", "אזור העיניים", "קרם פנים", "מסיכות", "קרם הגנה",
]
FIXED_SLOT = {"MORNING": "AM", "EVENING": "PM"}


def _actives(entry):
    return set(entry.get("actives", []))


def _clashes(entry, others):
    return sum(bool(compare_actives(_actives(entry), _actives(other))) for other in others)


def _preferred_slot(entry):
    actives = _actives(entry)
    if "retinoid" in actives or actives & EXFOLIANTS:
        return "PM"
    if "vitc" in actives:
        return "AM"
    return None


def _layer_key(entry, categories_of):
    cats = categories_of(entry)
    positions = [LAYER_ORDER.index(c) for c in cats if c in LAYER_ORDER]
    return min(positions) if positions else len(LAYER_ORDER)


def plan_routine(entries, categories_of):
    """מחלקת מוצרים לבוקר וערב ומחזירה גם התנגשויות שנשארו ואזהרות."""
    slots = {"AM": [], "PM": []}
    flexible = []
    for entry in entries:
        slot = FIXED_SLOT.get(entry.get("time"))
        if slot:
            slots[slot].append(entry)
        else:
            flexible.append(entry)

    for entry in flexible:
        am_clash, pm_clash = _clashes(entry, slots["AM"]), _clashes(entry, slots["PM"])
        if am_clash == 0 and pm_clash == 0:
            preferred = _preferred_slot(entry)
            targets = [preferred] if preferred else ["AM", "PM"]
        elif am_clash == pm_clash:
            targets = [_preferred_slot(entry) or "PM"]
        else:
            targets = ["AM"] if am_clash < pm_clash else ["PM"]
        for slot in targets:
            slots[slot].append(entry)

    conflicts = []
    for slot, items in slots.items():
        for index, first in enumerate(items):
            for second in items[index + 1:]:
                found = compare_actives(_actives(first), _actives(second))
                if found:
                    conflicts.append((slot, first, second, found))

    notes = []
    morning = slots["AM"]
    has_spf = any("קרם הגנה" in categories_of(e) for e in morning)
    if morning and any(e.get("sun") for e in morning) and not has_spf:
        notes.append(("☀️", "במוצרי הבוקר יש רכיבים שמגבירים רגישות לשמש, ואין קרם הגנה. כדאי להוסיף קרם הגנה כל בוקר."))
    elif morning and not has_spf:
        notes.append(("☀️", "בשגרת הבוקר אין קרם הגנה. מומלץ לסיים את הבוקר בקרם הגנה."))
    if any(e.get("pregnancy_ok") is False for e in entries):
        names = ", ".join(e["name"] for e in entries if e.get("pregnancy_ok") is False)
        notes.append(("🤰", f"לפי דקל, המוצרים האלה לא מומלצים בהיריון והנקה: {names}."))

    for slot in slots:
        slots[slot] = sorted(slots[slot], key=lambda e: _layer_key(e, categories_of))
    return slots, conflicts, notes
