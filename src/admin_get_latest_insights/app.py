import os, json
import boto3
from common.response import ok, err

s3 = boto3.client("s3")
BUCKET = os.environ["INSIGHTS_BUCKET"]

def handler(event, context):
    qs = (event.get("queryStringParameters") or {})
    tenant_id = qs.get("tenant_id")
    if not tenant_id:
        return err("missing_tenant_id", 400)
    key = f"{tenant_id}/latest.json"
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        return ok({"ok": True, "tenant_id": tenant_id, "insights": json.loads(obj["Body"].read())})
    except Exception:
        return err("no_insights_yet", 404)
