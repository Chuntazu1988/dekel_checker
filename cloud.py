"""שמירת נתוני משתמשת (פרופיל, שגרה, היסטוריה) ב-Supabase דרך REST, וברכה לפי שעה. בלי תלות ב-Streamlit."""

import base64
import datetime
import json
import urllib.error
import urllib.parse
import urllib.request

TABLE = "profiles"
MAX_HISTORY = 15
MAX_THUMB_B64 = 24_000


def greeting(now=None):
    """ברכה לפי שעה בישראל."""
    if now is None:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.datetime.now(ZoneInfo("Asia/Jerusalem"))
        except Exception:
            now = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
    hour = now.hour
    if 5 <= hour < 12:
        return "בוקר טוב"
    if 12 <= hour < 17:
        return "צהריים טובים"
    if 17 <= hour < 21:
        return "ערב טוב"
    return "לילה טוב"


def first_name(user):
    name = (user.get("given_name") or user.get("name") or "").strip()
    return name.split()[0] if name else ""


def pack(profile, routine, history, custom=None):
    """הופכת את המצב ל-JSON קטן שאפשר לשמור (בלי תמונות מוצר גדולות)."""
    entries = []
    for entry in (history or [])[:MAX_HISTORY]:
        product = dict(entry["product"]) if entry.get("product") else None
        if product:
            product.pop("picture", None)
        thumb = base64.b64encode(entry["thumb"]).decode("ascii") if entry.get("thumb") else ""
        if len(thumb) > MAX_THUMB_B64:
            thumb = ""
        entries.append({
            "kind": entry["kind"], "key": entry["key"], "title": entry["title"], "thumb": thumb,
            "text": entry.get("text", ""), "product": product, "meta": entry.get("meta"), "time": entry.get("time", ""),
        })
    return {"profile": profile or {}, "routine": list(routine or []), "history": entries, "custom": custom or {}}


def unpack(data):
    history = []
    for entry in (data or {}).get("history", []):
        history.append({
            "kind": entry.get("kind", "product"), "key": entry.get("key", ""), "title": entry.get("title", ""),
            "thumb": base64.b64decode(entry["thumb"]) if entry.get("thumb") else None,
            "text": entry.get("text", ""), "product": entry.get("product"), "meta": entry.get("meta"), "time": entry.get("time", ""),
        })
    return (data or {}).get("profile") or {}, list((data or {}).get("routine") or []), history, (data or {}).get("custom") or {}


def _request(url, key, method="GET", body=None, extra=None):
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    headers.update(extra or {})
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=8) as response:
        text = response.read().decode("utf-8")
        return json.loads(text) if text else None


def load(base_url, key, email):
    """מחזירה את הנתונים השמורים של המשתמשת, או None אם אין / אם יש שגיאה."""
    try:
        query = urllib.parse.quote(email, safe="")
        rows = _request(f"{base_url.rstrip('/')}/rest/v1/{TABLE}?email=eq.{query}&select=data", key)
        return rows[0]["data"] if rows else {}
    except (urllib.error.URLError, ValueError, KeyError, TimeoutError, OSError):
        return None


def save(base_url, key, email, data):
    try:
        _request(
            f"{base_url.rstrip('/')}/rest/v1/{TABLE}?on_conflict=email", key, method="POST",
            body={"email": email, "data": data, "updated_at": datetime.datetime.utcnow().isoformat() + "Z"},
            extra={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
        return True
    except (urllib.error.URLError, ValueError, TimeoutError, OSError):
        return False


SETUP_SQL = """create table if not exists profiles (
  email text primary key,
  data jsonb not null default '{}'::jsonb,
  updated_at timestamptz default now()
);
alter table profiles enable row level security;"""
