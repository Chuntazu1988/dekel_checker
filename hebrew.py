"""חיפוש רכיבים בעברית. מילון הרכיבים של דקל באנגלית בלבד, ולכן מתרגמים לפי כינויים נפוצים ולפי תיאורי הרכיבים. בלי תלות ב-Streamlit."""

import re

# שם בעברית -> חלקי שם באנגלית שמופיעים בשמות הרכיבים
ALIASES = {
    "גליצרין": ["glycerin"],
    "ניאצינמיד": ["niacinamide"],
    "נאיצינמיד": ["niacinamide"],
    "ויטמין b3": ["niacinamide"],
    "רטינול": ["retinol"],
    "רטינאל": ["retinal"],
    "רטינואיד": ["retino", "retinyl"],
    "ויטמין a": ["retin"],
    "חומצה היאלורונית": ["hyaluron"],
    "היאלורונית": ["hyaluron"],
    "היאלורון": ["hyaluron"],
    "ויטמין c": ["ascorbic", "ascorbyl"],
    "ויטמין סי": ["ascorbic", "ascorbyl"],
    "חומצה אסקורבית": ["ascorbic"],
    "ויטמין e": ["tocopher"],
    "ויטמין אי": ["tocopher"],
    "ויטמין b5": ["panthenol"],
    "פנתנול": ["panthenol"],
    "חומצה סליצילית": ["salicylic"],
    "סליצילית": ["salicylic"],
    "חומצה גליקולית": ["glycolic"],
    "גליקולית": ["glycolic"],
    "חומצה לקטית": ["lactic acid"],
    "לקטית": ["lactic acid"],
    "חומצה מנדלית": ["mandelic"],
    "חומצה אזלאית": ["azelaic"],
    "אזלאית": ["azelaic"],
    "חומצה ציטרית": ["citric acid"],
    "חומצה": ["acid"],
    "קפאין": ["caffeine"],
    "סקוואלן": ["squalane"],
    "סקוואלאן": ["squalane"],
    "צרמיד": ["ceramide"],
    "צרמידים": ["ceramide"],
    "אלנטואין": ["allantoin"],
    "אבץ": ["zinc"],
    "אוריאה": ["urea"],
    "קולגן": ["collagen"],
    "פפטיד": ["peptide"],
    "פפטידים": ["peptide"],
    "בקוצ'יול": ["bakuchiol"],
    "בקוצ׳יול": ["bakuchiol"],
    "חמאת שיאה": ["butyrospermum", "shea"],
    "שיאה": ["butyrospermum", "shea"],
    "אלוורה": ["aloe"],
    "אלוי": ["aloe"],
    "תה ירוק": ["camellia sinensis"],
    "ג'ובובה": ["simmondsia", "jojoba"],
    "ג׳ובובה": ["simmondsia", "jojoba"],
    "קוקוס": ["cocos nucifera", "coco-"],
    "זית": ["olea europaea"],
    "לבנדר": ["lavandula", "lavender"],
    "ורד": ["rosa "],
    "בושם": ["parfum", "fragrance"],
    "ניחוח": ["parfum", "fragrance"],
    "פרבן": ["paraben"],
    "פרבנים": ["paraben"],
    "פנוקסיאתנול": ["phenoxyethanol"],
    "אלכוהול": ["alcohol"],
    "סיליקון": ["dimethicone", "siloxane"],
    "דימתיקון": ["dimethicone"],
    "וזלין": ["petrolatum"],
    "פטרולטום": ["petrolatum"],
    "שמן מינרלי": ["mineral oil", "paraffinum"],
    "טיטניום דו חמצני": ["titanium dioxide"],
    "זינק אוקסיד": ["zinc oxide"],
    "סולפט": ["sulfate"],
    "בנזואיל פרוקסיד": ["benzoyl peroxide"],
    "רטינויד": ["retino"],
    "אקמול": [],
}
ALIASES = {key: value for key, value in ALIASES.items() if value}

HEBREW = re.compile(r"[֐-׿]")


def has_hebrew(text):
    return bool(HEBREW.search(text or ""))


def _normalize(text):
    text = (text or "").lower().strip()
    text = re.sub(r"[׳’`\"״]", "'", text)
    return re.sub(r"\s+", " ", text)


def _plain(description):
    return re.sub(r"<[^>]+>", " ", description or "")


def ingredient_matches(query, ingredients, limit=8):
    """מחזירה שמות רכיבים באנגלית שמתאימים לחיפוש בעברית.
    ingredients: מילון {שם באותיות קטנות: רשומה עם name ו-description}."""
    wanted = _normalize(query)
    if len(wanted) < 2:
        return []

    patterns = []
    for alias, parts in ALIASES.items():
        alias_norm = _normalize(alias)
        if wanted == alias_norm or (len(wanted) >= 3 and (alias_norm.startswith(wanted) or wanted in alias_norm)):
            patterns.extend(parts)

    names = [record["name"] for record in ingredients.values() if record.get("name")]
    if patterns:
        found = [name for name in names if any(p in name.lower() for p in dict.fromkeys(patterns))]
        found.sort(key=lambda name: (len(name), name.lower()))
        if found:
            return found[:limit]

    # אין כינוי מוכר: מחפשות את המילה בתיאור הרכיב, ומעדיפות התאמה בתחילת התיאור
    scored = []
    for record in ingredients.values():
        position = _normalize(_plain(record.get("description"))).find(wanted)
        if 0 <= position < 200:
            scored.append((position, len(record["name"]), record["name"]))
    scored.sort()
    return [name for _pos, _length, name in scored[:limit]]


def ingredient_options(ingredients):
    """אפשרויות להשלמה אוטומטית: כינויים בעברית ושמות באנגלית."""
    english = sorted({record["name"] for record in ingredients.values() if record.get("name")}, key=str.lower)
    return sorted(ALIASES) + english
