import base64, json, hmac, hashlib, time
from typing import Dict, Any, Tuple

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")

def _b64url_json(obj: Dict[str, Any]) -> str:
    return _b64url(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))

def sign_hs256(payload: Dict[str, Any], secret: str, kid: str = "v1") -> str:
    header = {"alg": "HS256", "typ": "JWT", "kid": kid}
    h = _b64url_json(header)
    p = _b64url_json(payload)
    msg = f"{h}.{p}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"

def verify_hs256(token: str, secret: str, issuer: str, audience: str) -> Tuple[bool, Dict[str, Any], str]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return False, {}, "bad_format"
        h, p, s = parts
        msg = f"{h}.{p}".encode("utf-8")
        exp_sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
        sig = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        if not hmac.compare_digest(exp_sig, sig):
            return False, {}, "bad_sig"
        payload = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
        now = int(time.time())
        if payload.get("iss") != issuer:
            return False, {}, "bad_iss"
        if payload.get("aud") != audience:
            return False, {}, "bad_aud"
        if int(payload.get("exp", 0)) < now:
            return False, {}, "expired"
        return True, payload, ""
    except Exception:
        return False, {}, "verify_error"
