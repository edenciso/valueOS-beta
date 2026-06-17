import os, json, time
import boto3
from common.bedrock_claude import claude_summarize

ddb = boto3.client("dynamodb")
tsq = boto3.client("timestream-query")
s3  = boto3.client("s3")

TENANTS = os.environ["TENANTS_TABLE"]
DB = os.environ["TS_DB"]
TABLE = os.environ["TS_TABLE"]
BUCKET = os.environ["INSIGHTS_BUCKET"]

def _tenant_ids():
    resp = ddb.scan(TableName=TENANTS, ProjectionExpression="tenant_id")
    return [i["tenant_id"]["S"] for i in resp.get("Items", [])]

def _metrics_snapshot(tenant_id: str, hours: int = 168):
    query = f"""
      SELECT
        measure_name,
        SUM(CAST(measure_value::double AS double)) AS total
      FROM "{DB}"."{TABLE}"
      WHERE tenant_id = '{tenant_id}'
        AND time > ago({hours}h)
        AND measure_name IN ('ai_runs','outcomes','attributed_outcomes','cost_usd','latency_ms')
      GROUP BY measure_name
    """
    resp = tsq.query(QueryString=query)
    out = {}
    for r in resp.get("Rows", []):
        d = r["Data"]
        out[d[0]["ScalarValue"]] = float(d[1]["ScalarValue"])
    return out

def handler(event, context):
    now = int(time.time())
    only_tenant = event.get("tenant_id") if isinstance(event, dict) else None
    tenants = [only_tenant] if only_tenant else _tenant_ids()

    for tenant_id in tenants:
        if not tenant_id:
            continue
        snap = _metrics_snapshot(tenant_id, 168)
        user = f"""
Generate a concise executive beta insight summary for tenant {tenant_id} for the last 7 days.
Use the metrics snapshot below.
Output JSON with:
- summary_bullets (max 6)
- anomalies (max 3)
- value_signals (max 4)
- next_actions (max 5)
- confidence (0..1)
Snapshot: {json.dumps(snap)}
"""
        text = claude_summarize(
            system="You are a ValueOS ROI analyst. Output strict JSON only.",
            user=user,
            max_tokens=700
        )
        payload = {
            "generated_at": now,
            "tenant_id": tenant_id,
            "snapshot": snap,
            "model_output": text
        }
        s3.put_object(
            Bucket=BUCKET,
            Key=f"{tenant_id}/latest.json",
            Body=json.dumps(payload).encode("utf-8"),
            ContentType="application/json"
        )

    return {"statusCode": 200, "body": "ok"}
