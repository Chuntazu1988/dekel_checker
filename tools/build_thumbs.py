"""בונה data/thumbs.json: תמונות מוצר ממוזערות (webp) לפי slug, מתוך הצעות החיפוש של דקל.

הרצה: python tools/build_thumbs.py --session dekel_session.json
"""
import argparse
import base64
import io
import json
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent

JS = r"""async (queries) => {
  const out = {};
  let i = 0;
  const one = async (q) => {
    try {
      const r = await fetch("/wp-admin/admin-ajax.php?action=get_product_suggestions&searchTerm=" + encodeURIComponent(q), {credentials: "same-origin"});
      if (!r.ok) return;
      const j = await r.json();
      for (const it of (Array.isArray(j.data) ? j.data : [])) {
        if (it.slug && it.productPicture && !out[it.slug]) out[it.slug] = it.productPicture;
      }
    } catch (e) {}
  };
  const worker = async () => { while (i < queries.length) { await one(queries[i++]); } };
  await Promise.all([worker(), worker(), worker()]);
  return out;
}"""


def thumb(b64, side=240):
    image = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
    background = Image.new("RGBA", image.size, (255, 255, 255, 255))
    background.alpha_composite(image)
    image = background.convert("RGB")
    image.thumbnail((side, side))
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=72)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default=str(ROOT / "dekel_session.json"))
    args = parser.parse_args()

    index = json.loads((ROOT / "data" / "catalog_index.json").read_text(encoding="utf-8"))["products"]
    wanted = {p["slug"]: p for p in index if p.get("verified")}
    queries = list(dict.fromkeys(p["name"] for p in wanted.values()))
    print(len(wanted), "verified products,", len(queries), "queries", flush=True)

    found = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(storage_state=args.session).new_page()
        page.goto("https://thedekel.co.il/robots.txt", wait_until="commit")
        for start in range(0, len(queries), 40):
            found.update(page.evaluate(JS, queries[start:start + 40]))
            print(f"  {min(start + 40, len(queries))}/{len(queries)} pictures: {len(found)}", flush=True)
        browser.close()

    result = {}
    for slug, b64 in found.items():
        if slug in wanted:
            try:
                result[slug] = thumb(b64)
            except Exception:
                pass
    out = ROOT / "data" / "thumbs.json"
    out.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    print(f"saved {len(result)}/{len(wanted)} thumbnails, {out.stat().st_size // 1024}KB")


if __name__ == "__main__":
    main()
