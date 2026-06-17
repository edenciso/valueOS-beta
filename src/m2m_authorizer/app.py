import os, json
import boto3
from common.jwt_hs256 import verify_hs256

sm = boto3.client("secretsmanager")
JWT_SECRET_ARN = os.environ["JWT_SECRET_ARN"]

ISSUER = "valueos-token-broker"
AUDIENCE = "valueos-ingest"

def _get_hmac_secret() -> str:
    sec = sm.get_secret_value(SecretId=JWT_SECRET_ARN)
    return json.loads(sec["SecretString"])["hmac"]

def _policy(principal_id: str, effect: str, method_arn: str, ctx: dict | None = None):
    return {
        "principalId": principal_id,
        "policyDocument": {
            "Version":"2012-10-17",
            "Statement":[{"Action":"execute-api:Invoke","Effect":effect,"Resource":method_arn}]
        },
        "context": {k: str(v) for k, v in (ctx or {}).items()}
    }

def handler(event, context):
    token = (event.get("authorizationToken") or "").replace("Bearer ", "")
    method_arn = event.get("methodArn", "*")
    ok_sig, payload, _ = verify_hs256(token, _get_hmac_secret(), ISSUER, AUDIENCE)

    if not ok_sig:
        return _policy("denied", "Deny", method_arn)

    ctx = {
        "tenant_id": payload.get("tenant_id", ""),
        "scope": payload.get("scope", ""),
        "lim_rpm": payload.get("lim_rpm", 600),
        "lim_rpd": payload.get("lim_rpd", 50000),
        "client_id": payload.get("sub", ""),
    }
    if not ctx["tenant_id"]:
        return _policy("denied", "Deny", method_arn)

    return _policy(ctx["client_id"] or "service", "Allow", method_arn, ctx)
