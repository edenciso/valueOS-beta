import os
import boto3
from common.response import ok, err

s3 = boto3.client("s3")
BUCKET = os.environ["EVIDENCE_BUCKET"]

def handler(event, context):
    qs = (event.get("queryStringParameters") or {})
    tenant_id = qs.get("tenant_id")
    if not tenant_id:
        return err("missing_tenant_id", 400)

    html_key = f"{tenant_id}/latest.html"
    pdf_key  = f"{tenant_id}/latest.pdf"

    html_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET, "Key": html_key},
        ExpiresIn=3600
    )
    pdf_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET, "Key": pdf_key},
        ExpiresIn=3600
    )

    return ok({"ok": True, "tenant_id": tenant_id, "html_url": html_url, "pdf_url": pdf_url})
