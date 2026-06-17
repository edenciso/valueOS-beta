import os, json, time, secrets, hashlib
import boto3
from common.jwt_hs256 import sign_hs256
from common.response import ok, err

ddb = boto3.client("dynamodb")
sm  = boto3.client("secretsmanager")

SP_TABLE = os.environ["SERVICE_PRINCIPALS_TABLE"]
TENANTS_TABLE = os.environ["TENANTS_TABLE"]
JWT_SECRET_ARN = os.environ["JWT_SECRET_ARN"]

ISSUER = "valueos-token-broker"
AUDIENCE = "valueos-ingest"

def _pbkdf2(secret: str, salt_hex: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), bytes.fromhex(salt_hex), 120000, dklen=32)
    return dk.hex()

def _get_hmac_secret() -> str:
    sec = sm.get_secret_value(SecretId=JWT_SECRET_ARN)
    return json.loads(sec["SecretString"])["hmac"]

def handler(event, context):
    body = json.loads(event.get("body") or "{}")
    client_id = body.get("client_id")
    client_secret = body.get("client_secret")
    if not client_id or not client_secret:
        return err("missing_client_credentials", 400)

    sp = ddb.get_item(TableName=SP_TABLE, Key={"client_id": {"S": client_id}}).get("Item")
    if not sp or sp.get("status", {}).get("S") != "active":
        return err("invalid_client", 401)

    salt = sp["salt"]["S"]
    expected = sp["secret_hash"]["S"]
    computed = _pbkdf2(client_secret, salt)
    if not secrets.compare_digest(expected, computed):
        return err("invalid_client", 401)

    tenant_id = sp["tenant_id"]["S"]
    _ = ddb.get_item(TableName=TENANTS_TABLE, Key={"tenant_id": {"S": tenant_id}})

    scopes = " ".join(sp.get("scopes", {}).get("SS", []))
    lim_rpm = int(sp.get("lim_rpm", {}).get("N") or os.environ["DEFAULT_LIM_RPM"])
    lim_rpd = int(sp.get("lim_rpd", {}).get("N") or os.environ["DEFAULT_LIM_RPD"])

    ttl = int(os.environ["TOKEN_TTL_SECONDS"])
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": client_id,
        "tenant_id": tenant_id,
        "scope": scopes,
        "lim_rpm": lim_rpm,
        "lim_rpd": lim_rpd,
        "iat": now,
        "exp": now + ttl,
        "jti": "jti_" + secrets.token_hex(10),
    }

    token = sign_hs256(payload, _get_hmac_secret(), kid="v1")
    return ok({
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": ttl,
        "scope": scopes,
        "tenant_id": tenant_id
    }, 200)
