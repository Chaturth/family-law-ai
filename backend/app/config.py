import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env", override=True)
except ImportError:  # dotenv optional
    pass


class Settings:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    DAILY_LIMIT = int(os.getenv("GEMINI_DAILY_LIMIT", "200") or 200)
    DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE / "data")))
    DISCLAIMER = ("AI-generated draft for review by a qualified advocate. "
                  "Not legal advice. Verify all citations against official reports.")


settings = Settings()
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
