"""Gemini wrapper. Every call takes a `fallback` so the app still works (offline-demo mode)
when no API key is set or the API errors."""
import datetime as dt
import json
import logging
import re
import threading
from .config import settings

log = logging.getLogger("llm")
_client = None
LAST_ERROR = None
_lock = threading.Lock()
_USAGE = settings.DATA_DIR / "usage.json"


def _today():
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def usage():
    """Gemini calls made today (UTC) and the configured cap."""
    try:
        d = json.loads(_USAGE.read_text())
    except Exception:
        d = {}
    used = d.get("count", 0) if d.get("date") == _today() else 0
    return {"date": _today(), "used": used, "limit": settings.DAILY_LIMIT, "remaining": max(0, settings.DAILY_LIMIT - used)}


def _reserve():
    """Count one call; return False (and make no call) once the daily cap is hit."""
    with _lock:
        u = usage()
        if u["used"] >= u["limit"]:
            return False
        _USAGE.write_text(json.dumps({"date": u["date"], "count": u["used"] + 1}))
        return True


def enabled():
    return bool(settings.GEMINI_API_KEY)


def mode():
    if not enabled():
        return "offline-demo"
    return "gemini" if usage()["remaining"] > 0 else "offline-demo (daily limit reached)"


def _get_client():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


def _call(prompt, system, files, json_mode):
    from google.genai import types
    contents = [prompt]
    for mime, data in files or []:
        contents.append(types.Part.from_bytes(data=data, mime_type=mime))
    cfg = types.GenerateContentConfig(
        system_instruction=system, temperature=0.2,
        response_mime_type="application/json" if json_mode else None)
    r = _get_client().models.generate_content(model=settings.GEMINI_MODEL, contents=contents, config=cfg)
    return r.text or ""


def _fb(fallback):
    return fallback() if callable(fallback) else fallback


def text(prompt, system=None, files=None, fallback=""):
    global LAST_ERROR
    if not enabled() or not _reserve():
        LAST_ERROR = LAST_ERROR if not enabled() else "daily limit reached"
        return _fb(fallback)
    try:
        return _call(prompt, system, files, False)
    except Exception as e:
        LAST_ERROR = str(e)
        log.exception("Gemini text call failed")
        return _fb(fallback)


def json_(prompt, system=None, files=None, fallback=None):
    global LAST_ERROR
    if not enabled() or not _reserve():
        LAST_ERROR = LAST_ERROR if not enabled() else "daily limit reached"
        return _fb(fallback if fallback is not None else {})
    try:
        raw = _call(prompt, system, files, True)
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
        return json.loads(raw)
    except Exception as e:
        LAST_ERROR = str(e)
        log.exception("Gemini json call failed")
        return _fb(fallback if fallback is not None else {})
