"""Encryption at rest for all case data (DPDP Act / Family Courts Act s.11 confidentiality)."""
import json
import os
from cryptography.fernet import Fernet
from .config import settings


def _key() -> bytes:
    env = os.getenv("FERNET_KEY", "").strip()
    if env:
        return env.encode()
    p = settings.DATA_DIR / "secret.key"
    if p.exists():
        return p.read_bytes()
    k = Fernet.generate_key()
    p.write_bytes(k)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return k


_f = Fernet(_key())


def enc(obj) -> bytes:
    return _f.encrypt(json.dumps(obj, ensure_ascii=False, default=str).encode())


def dec(blob: bytes):
    return json.loads(_f.decrypt(blob))
