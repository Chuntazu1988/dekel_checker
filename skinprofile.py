"""התאמה אישית של תוצאת הסריקה לפרופיל המשתמשת (סוג עור, מטרות, היריון, רגישויות). בלי תלות ב-Streamlit."""

import re

SKIN_TYPES = {"oily": "עור שמן", "dry": "עור יבש", "combo": "עור מעורב", "normal": "עור רגיל", "sensitive": "עור רגיש"}
CONCERNS = {
    "acne": "אקנה ופגמים", "pigment": "פיגמנטציה וכתמים", "aging": "קמטים וסימני גיל",
    "redness": "אדמומיות ורוזציאה", "dryness": "יובש ומתיחות",
}

FRAGRANCE = ("parfum", "fragrance", "linalool", "limonene", "geraniol", "citronellol", "coumarin", "eugenol", "cinnamal")
DRYING_ALCOHOL = re.compile(r"(alcohol denat|sd alcohol|isopropyl alcohol|ethanol)", re.I)
COMEDOGENIC = ("coconut oil", "cocos nucifera", "isopropyl myristate", "isopropyl palmitate", "wheat germ", "cocoa butter", "theobroma")

# מרכיבים שמסייעים לכל מטרה (חלקי שם באנגלית)
HELPFUL = {
    "oily": (("salicylic", "חומצה סליצילית"), ("niacinamide", "ניאצינמיד"), ("zinc", "אבץ"), ("azelaic", "חומצה אזלאית")),
    "dry": (("ceramide", "צרמידים"), ("hyaluron", "חומצה היאלורונית"), ("glycerin", "גליצרין"), ("squalane", "סקוואלן"), ("panthenol", "פנתנול")),
    "acne": (("salicylic", "חומצה סליצילית"), ("benzoyl peroxide", "בנזואיל פרוקסיד"), ("azelaic", "חומצה אזלאית"), ("niacinamide", "ניאצינמיד"), ("adapalene", "אדפלין")),
    "pigment": (("ascorbic", "ויטמין C"), ("niacinamide", "ניאצינמיד"), ("azelaic", "חומצה אזלאית"), ("tranexamic", "טרנקסמית"), ("arbutin", "ארבוטין"), ("kojic", "קוג'יק"), ("glycolic", "חומצה גליקולית")),
    "aging": (("retinol", "רטינול"), ("retinal", "רטינאל"), ("peptide", "פפטידים"), ("ascorbic", "ויטמין C"), ("bakuchiol", "בקוצ'יול")),
    "redness": (("centella", "סנטלה"), ("madecassoside", "מדקאסוסייד"), ("allantoin", "אלנטואין"), ("panthenol", "פנתנול"), ("bisabolol", "ביסבולול"), ("azelaic", "חומצה אזלאית")),
    "dryness": (("ceramide", "צרמידים"), ("hyaluron", "חומצה היאלורונית"), ("glycerin", "גליצרין"), ("squalane", "סקוואלן"), ("shea", "חמאת שיאה")),
}
HELP_KEY = {"oily": "oily", "combo": "oily", "dry": "dry", "normal": None, "sensitive": None}


def _hits(names, patterns):
    lowered = [(name, name.lower()) for name in names]
    found = []
    for pattern, label in patterns:
        if any(pattern in low for _name, low in lowered) and label not in found:
            found.append(label)
    return found


def _contains(names, patterns):
    return [name for name in names if any(p in name.lower() for p in patterns)]


def _alcohols(names):
    return [name for name in names if DRYING_ALCOHOL.search(name)]


def normalize(profile):
    profile = profile or {}
    return {
        "skin": profile.get("skin") if profile.get("skin") in SKIN_TYPES else "",
        "concerns": [c for c in profile.get("concerns", []) if c in CONCERNS],
        "preg": bool(profile.get("preg")),
        "avoid": [a.strip().lower() for a in profile.get("avoid", []) if a and a.strip()],
    }


def is_empty(profile):
    profile = normalize(profile)
    return not (profile["skin"] or profile["concerns"] or profile["preg"] or profile["avoid"])


def encode(profile):
    profile = normalize(profile)
    if is_empty(profile):
        return ""
    return "~".join([profile["skin"], ",".join(profile["concerns"]), "1" if profile["preg"] else "0", ",".join(profile["avoid"])])


def decode(text):
    parts = (text or "").split("~")
    parts += [""] * (4 - len(parts))
    return normalize({
        "skin": parts[0], "concerns": [c for c in parts[1].split(",") if c],
        "preg": parts[2] == "1", "avoid": [a for a in parts[3].split(",") if a],
    })


def personal_notes(profile, names, results, actives=()):
    """הערות אישיות: [(אייקון, טקסט, רמה)] כאשר רמה היא good / warn / bad."""
    profile = normalize(profile)
    notes = []
    flags = {flag for name in names for flag in results.get(name, {}).get("flags", [])}

    if profile["preg"]:
        risky = [n for n in names if {"pregnancy", "breastfeeding"} & set(results.get(n, {}).get("flags", []))]
        if risky:
            notes.append(("🤰", "לפי הפרופיל שלך (היריון או הנקה): לא מומלץ ברכיבים " + ", ".join(risky[:4]) + ". כדאי להתייעץ עם רופא/ה.", "bad"))
        elif "retinoid" in actives:
            notes.append(("🤰", "במוצר יש רטינואידים, ובהיריון ובהנקה מומלץ להימנע מהם.", "bad"))
        else:
            notes.append(("🤰", "לא סימנו במוצר רכיבים שדקל מגדירה כלא מומלצים בהיריון והנקה.", "good"))

    for word in profile["avoid"]:
        bad = _contains(names, [word])
        if bad:
            notes.append(("🚫", f"הרכיבים הבאים תואמים את מה שביקשת להימנע ממנו ({word}): " + ", ".join(bad[:4]), "bad"))

    skin = profile["skin"]
    if skin in ("sensitive",):
        scented = _contains(names, FRAGRANCE)
        alcohol = _alcohols(names)
        if scented:
            notes.append(("🌸", "לעור רגיש: יש במוצר בושם או מרכיבי ניחוח (" + ", ".join(scented[:3]) + ") שעלולים לגרות.", "warn"))
        if alcohol:
            notes.append(("🧴", "לעור רגיש: יש אלכוהול מייבש (" + ", ".join(alcohol[:2]) + ").", "warn"))
        if actives & {"retinoid", "aha", "bha", "pha", "benzoyl"}:
            notes.append(("⚡", "לעור רגיש: יש במוצר פעילים חזקים. כדאי להתחיל לאט ולבדוק על אזור קטן.", "warn"))
        if not (scented or alcohol or actives & {"retinoid", "aha", "bha", "pha", "benzoyl"}):
            notes.append(("🌿", "לעור רגיש: לא זיהינו במוצר בושם, אלכוהול מייבש או פעילים חזקים.", "good"))

    if skin in ("oily", "combo"):
        clog = _contains(names, COMEDOGENIC)
        if clog:
            notes.append(("🫒", "לעור שמן או מעורב: יש שמנים שעלולים לסתום נקבוביות (" + ", ".join(clog[:3]) + ").", "warn"))
    if skin == "dry":
        alcohol = _alcohols(names)
        if alcohol:
            notes.append(("💧", "לעור יבש: יש אלכוהול מייבש (" + ", ".join(alcohol[:2]) + ").", "warn"))

    seen = set()
    wanted = [(HELP_KEY.get(skin), SKIN_TYPES.get(skin, ""))] + [(c, CONCERNS[c]) for c in profile["concerns"]]
    for key, label in wanted:
        if not key or key in seen:
            continue
        seen.add(key)
        helpful = _hits(names, HELPFUL.get(key, ()))
        if helpful:
            notes.append(("✅", f"מתאים ל{label}: יש במוצר " + ", ".join(helpful[:4]) + ".", "good"))
        else:
            notes.append(("➖", f"ל{label}: לא זיהינו במוצר מרכיבי פעילות בולטים לכך.", "warn"))
    return notes
