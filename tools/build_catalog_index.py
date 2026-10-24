"""בונה את data/catalog_index.json: הפעילים של כל תכשיר בקטלוג של דקל.

הרצה (מהתיקייה הראשית של הפרויקט, עם סשן מחובר שנשמר קודם על ידי האפליקציה):
    python tools/build_catalog_index.py --session dekel_session.json
"""

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from compat import detect_actives_from_names  # noqa: E402

SEED_TERMS = [
    "cream", "serum", "toner", "cleanser", "mask", "sunscreen", "spf", "retinol", "vitamin c",
    "niacinamide", "acid", "oil", "gel", "lotion", "eye", "peel", "moistur", "hyaluron", "azelaic",
    "salicylic", "ordinary", "cerave", "la roche", "paula", "dermalogica", "bioderma", "neutrogena",
] + list("abcdefghijklmnopqrstuvwxyz")

DISCOVER_JS = r"""async (terms) => {
  const base = "/wp-admin/admin-ajax.php?action=get_product_suggestions&searchTerm=";
  const found = {};
  let i = 0;
  const one = async (q) => {
    try {
      const r = await fetch(base + encodeURIComponent(q), {credentials: "same-origin"});
      if (!r.ok) return;
      const j = await r.json();
      for (const it of (Array.isArray(j.data) ? j.data : [])) {
        if (it.slug && !found[it.slug]) {
          found[it.slug] = {slug: it.slug, name: it.primaryName || "", name2: it.secondaryName || "",
                            company: it.companyName || "", verified: String(it.verified).toLowerCase() === "true"};
        }
      }
    } catch (e) {}
  };
  const worker = async () => { while (i < terms.length) { await one(terms[i++]); } };
  await Promise.all([worker(), worker(), worker()]);
  return Object.values(found);
}"""

PAGES_JS = r"""async (slugs) => {
  const out = [];
  let i = 0;
  const one = async (slug) => {
    try {
      const r = await fetch("/product-verified/?slug=" + encodeURIComponent(slug), {credentials: "same-origin"});
      const h = await r.text();
      const marker = h.indexOf('"ingredientsInTheList"');
      if (marker < 0) { out.push({slug, chunk: null}); return; }
      const start = h.lastIndexOf('{"id"', marker);
      out.push({slug, chunk: h.slice(start)});
    } catch (e) { out.push({slug, chunk: null}); }
  };
  const worker = async () => { while (i < slugs.length) { await one(slugs[i++]); } };
  await Promise.all([worker(), worker(), worker(), worker()]);
  return out;
}"""


def words_from(entry):
    text = f"{entry['name']} {entry['company']}"
    return [w for w in re.findall(r"[A-Za-z]{4,}|[֐-׿]{3,}", text)]


def discover(page, limit):
    catalog, queried = {}, set()
    queue = list(SEED_TERMS)
    dry_batches = 0
    while queue and len(queried) < limit and dry_batches < 6:
        batch, queue = queue[:12], queue[12:]
        queried.update(t.lower() for t in batch)
        items = page.evaluate(DISCOVER_JS, batch)
        new = 0
        for item in items:
            if item["slug"] not in catalog:
                catalog[item["slug"]] = item
                new += 1
                for word in words_from(item):
                    if word.lower() not in queried and word not in queue:
                        queue.append(word)
        dry_batches = dry_batches + 1 if new == 0 else 0
        print(f"  queried {len(queried):3d} terms | catalog {len(catalog):3d} (+{new}) | queue {len(queue)}", flush=True)
    return catalog


def parse_product(slug, chunk):
    if not chunk:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(chunk)
    except ValueError:
        return None
    records = obj.get("ingredientsInTheList") or []
    entries = [((r.get("name") or "").strip(), r.get("keyIngredientGroupId")) for r in records]
    key_ingredients = obj.get("keyIngredients") or []
    # באינדקס סופרים רק פעילים שמופיעים בתחילת הרשימה, כדי לא לסמן עקבות (למשל חומצה סליצילית בקרם הגנה)
    actives = detect_actives_from_names(entries)

    types = obj.get("productTypes") or []
    if isinstance(types, str):
        types = [types]
    types = [str(t).replace("\r", "").strip() for t in types if str(t).strip()]

    return {
        "slug": slug,
        "time": obj.get("idealTimeOfUse"),
        "types": types,
        "actives": sorted(actives),
        "pregnancy_ok": obj.get("approvedWhilePregnancyOrBreastFeeding"),
        "sun": obj.get("isIncreasesPhotoSensitivity"),
        "ingredients": len(records),
        "_names": [name for name, _ in entries],
        "_key": list(key_ingredients),
        "_records": [r for r in records if (r.get("name") or "").strip()],
        "_description": obj.get("description") or "",
    }


KEEP_FIELDS = (
    "name", "rating", "functionFamilies", "keyIngredientGroupId", "description",
    "increasesPhotoSensitivity", "contraindicatedInPregnancy", "contraindicatedWhileBreastfeeding",
)


def write_ingredient_db(products, out_dir):
    """מאגר רכיבים מקומי (שם באותיות קטנות -> פירוט) ורשימות רכיבים לכל מוצר, כדי להימנע מקריאות לאתר."""
    db, lists, meta = {}, {}, {}
    for product in products:
        names = []
        for record in product.get("_records", []):
            name = record["name"].strip()
            names.append(name)
            db.setdefault(name.lower(), {k: record.get(k) for k in KEEP_FIELDS if record.get(k) not in (None, "")})
        lists[product["slug"]] = names
        meta[product["slug"]] = {"description": product.get("_description", ""), "key": product.get("_key", [])}
    path = out_dir / "ingredients.json"
    path.write_text(json.dumps({"generated": date.today().isoformat(), "ingredients": db, "products": lists, "meta": meta},
                               ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"מאגר רכיבים: {len(db)} רכיבים, {len(lists)} מוצרים ({path.stat().st_size // 1024}KB)")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default=str(ROOT / "dekel_session.json"))
    parser.add_argument("--limit", type=int, default=400, help="מקסימום מונחי חיפוש")
    parser.add_argument("--out", default=str(ROOT / "data" / "catalog_index.json"))
    parser.add_argument("--raw", default="", help="קובץ לשמירת רשימות הרכיבים הגולמיות (לחישוב מחדש בלי סריקה)")
    parser.add_argument("--from-raw", default="", help="חישוב מחדש של הפעילים מקובץ גולמי, בלי גלישה")
    args = parser.parse_args()

    started = time.time()
    if args.from_raw:
        products = json.loads(Path(args.from_raw).read_text(encoding="utf-8"))
        for product in products:
            entries = [(name, None) for name in product.get("_names", [])]
            product["actives"] = sorted(detect_actives_from_names(entries))
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        write_ingredient_db(products, out.parent)
        for product in products:
            for key in ("_names", "_key", "_records", "_description"):
                product.pop(key, None)
        payload = {"generated": date.today().isoformat(), "count": len(products), "products": products}
        out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"חושבו מחדש {len(products)} מוצרים ({out.stat().st_size // 1024}KB)")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=args.session)
        page = context.new_page()
        page.goto("https://thedekel.co.il/#milonid")
        page.wait_for_timeout(3000)

        print("שלב 1: איתור מוצרים בקטלוג", flush=True)
        catalog = discover(page, args.limit)

        print(f"שלב 2: קריאת {len(catalog)} עמודי מוצר", flush=True)
        slugs = list(catalog)
        products, failed = [], []
        for start in range(0, len(slugs), 20):
            for row in page.evaluate(PAGES_JS, slugs[start:start + 20]):
                parsed = parse_product(row["slug"], row["chunk"])
                if parsed is None:
                    failed.append(row["slug"])
                    continue
                meta = catalog[row["slug"]]
                parsed.update({"name": meta["name"], "name2": meta["name2"], "company": meta["company"], "verified": meta["verified"]})
                products.append(parsed)
            print(f"  {min(start + 20, len(slugs))}/{len(slugs)} (failed: {len(failed)})", flush=True)
        browser.close()

    if args.raw:
        Path(args.raw).write_text(json.dumps(products, ensure_ascii=False), encoding="utf-8")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_ingredient_db(products, out.parent)
    for product in products:
        for key in ("_names", "_key", "_records", "_description"):
            product.pop(key, None)
    payload = {"generated": date.today().isoformat(), "count": len(products), "products": products}
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"נשמרו {len(products)} מוצרים ב-{out} ({out.stat().st_size // 1024}KB), נכשלו {len(failed)}, {time.time() - started:.0f} שניות")


if __name__ == "__main__":
    main()
