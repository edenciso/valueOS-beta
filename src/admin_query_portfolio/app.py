import os
import boto3
from common.response import ok, err

tsq = boto3.client("timestream-query")
DB = os.environ["TS_DB"]
TABLE = os.environ["TS_TABLE"]

def handler(event, context):
    qs = (event.get("queryStringParameters") or {})
    tenant_id = qs.get("tenant_id")
    hours = int(qs.get("hours") or "168")
    if not tenant_id:
        return err("missing_tenant_id", 400)

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
    rows = resp.get("Rows", [])
    out = {}
    for r in rows:
        cells = r["Data"]
        out[cells[0]["ScalarValue"]] = float(cells[1]["ScalarValue"])
    return ok({"ok": True, "tenant_id": tenant_id, "hours": hours, "totals": out})
