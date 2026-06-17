import os, json, time, secrets, hashlib
import boto3
from common.response import ok, err

ddb = boto3.client("dynamodb")
TENANTS = os.environ["TENANTS_TABLE"]
SP_TABLE = os.environ["SERVICE_PRINCIPALS_TABLE"]

def _pbkdf2(secret: str, salt_hex: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), bytes.fromhex(salt_hex), 120000, dklen=32)
    return dk.hex()

def handler(event, context):
    body = json.loads(event.get("body") or "{}")
    tenant_id = body.get("tenant_id")
    if not tenant_id:
        return err("missing_tenant_id", 400)

    ddb.update_item(
        TableName=TENANTS,
        Key={"tenant_id":{"S":tenant_id}},
        UpdateExpression="SET #n = if_not_exists(#n, :n), created_at = if_not_exists(created_at, :t)",
        ExpressionAttributeNames={"#n":"name"},
        ExpressionAttributeValues={":n":{"S":body.get("name","")}, ":t":{"N":str(int(time.time()))}},
    )

    client_id = "sp_" + secrets.token_hex(8)
    client_secret = secrets.token_urlsafe(24)
    salt = secrets.token_hex(16)
    secret_hash = _pbkdf2(client_secret, salt)

    scopes = body.get("scopes") or ["ingest:ai_runs","ingest:outcomes"]
    lim_rpm = int(body.get("lim_rpm") or os.environ["DEFAULT_LIM_RPM"])
    lim_rpd = int(body.get("lim_rpd") or os.environ["DEFAULT_LIM_RPD"])

    ddb.put_item(
        TableName=SP_TABLE,
        Item={
            "client_id": {"S": client_id},
            "tenant_id": {"S": tenant_id},
            "salt": {"S": salt},
            "secret_hash": {"S": secret_hash},
            "status": {"S": "active"},
            "scopes": {"SS": [str(s) for s in scopes]},
            "lim_rpm": {"N": str(lim_rpm)},
            "lim_rpd": {"N": str(lim_rpd)},
            "created_at": {"N": str(int(time.time()))},
        },
    )

    return ok({"ok": True, "tenant_id": tenant_id, "client_id": client_id, "client_secret": client_secret, "scopes": scopes})
