import os, json, time, uuid
import boto3
from common.response import ok, err
from common.ddb_rate_limit import enforce_two_bucket_quota
from common.timestream import write_metric

ddb = boto3.client("dynamodb")
s3  = boto3.client("s3")

RAW_EVENTS_TABLE = os.environ["RAW_EVENTS_TABLE"]
RUN_INDEX_TABLE  = os.environ["RUN_INDEX_TABLE"]
RAW_BUCKET       = os.environ["RAW_BUCKET"]
RAW_TTL_DAYS      = int(os.environ["RAW_TTL_DAYS"])

def _ttl(days: int) -> int:
    return int(time.time()) + days * 86400

def handler(event, context):
    auth = (event.get("requestContext") or {}).get("authorizer") or {}
    tenant_id = auth.get("tenant_id")
    scopes = (auth.get("scope") or "").split()
    lim_rpm = int(auth.get("lim_rpm") or os.environ["DEFAULT_LIM_RPM"])
    lim_rpd = int(auth.get("lim_rpd") or os.environ["DEFAULT_LIM_RPD"])

    if not tenant_id:
        return err("missing_tenant", 401)
    if "ingest:ai_runs" not in scopes:
        return err("insufficient_scope", 403)

    try:
        enforce_two_bucket_quota(tenant_id, lim_rpm, lim_rpd)
    except RuntimeError:
        return err("quota_exceeded", 429)

    body = json.loads(event.get("body") or "{}")
    events = body.get("events") if isinstance(body.get("events"), list) else [body]

    batch_id = str(uuid.uuid4())
    ts_ms = int(time.time() * 1000)

    s3.put_object(
        Bucket=RAW_BUCKET,
        Key=f"{tenant_id}/ai_runs/{ts_ms}-{batch_id}.json",
        Body=json.dumps(events).encode("utf-8"),
        ContentType="application/json",
    )

    for e in events:
        run_id = (e.get("correlation") or {}).get("run_id") or e.get("run_id") or "unknown"
        workflow_id = (e.get("correlation") or {}).get("workflow_id") or e.get("workflow_id") or "unknown"
        model_id = ((e.get("data") or {}).get("model") or {}).get("model_id") or "unknown"
        latency_ms = float(((e.get("data") or {}).get("metrics") or {}).get("latency_ms") or 0)
        cost_usd = float(((e.get("data") or {}).get("metrics") or {}).get("cost_usd") or 0)

        pk = f"{tenant_id}#AI_RUN"
        sk = f"{ts_ms}#{run_id}#{uuid.uuid4().hex[:8]}"
        ddb.put_item(
            TableName=RAW_EVENTS_TABLE,
            Item={
                "pk": {"S": pk},
                "sk": {"S": sk},
                "tenant_id": {"S": tenant_id},
                "run_id": {"S": run_id},
                "workflow_id": {"S": workflow_id},
                "model_id": {"S": model_id},
                "ttl": {"N": str(_ttl(RAW_TTL_DAYS))}
            }
        )

        write_metric(tenant_id, "ai_runs", 1.0, {"model_id": model_id})
        if latency_ms > 0:
            write_metric(tenant_id, "latency_ms", latency_ms, {"model_id": model_id})
        if cost_usd > 0:
            write_metric(tenant_id, "cost_usd", cost_usd, {"model_id": model_id})

        ext = (e.get("correlation") or {}).get("external_object_refs") or []
        for ref in ext:
            system = ref.get("system"); typ = ref.get("type"); oid = ref.get("id")
            if system and typ and oid:
                ddb.put_item(
                    TableName=RUN_INDEX_TABLE,
                    Item={
                        "pk": {"S": f"{tenant_id}#{system}#{typ}#{oid}"},
                        "tenant_id": {"S": tenant_id},
                        "system": {"S": system},
                        "obj_type": {"S": typ},
                        "obj_id": {"S": oid},
                        "run_id": {"S": run_id},
                        "workflow_id": {"S": workflow_id},
                        "touched_at_ms": {"N": str(ts_ms)},
                        "ttl": {"N": str(_ttl(30))},
                    }
                )

    return ok({"ok": True, "tenant_id": tenant_id, "accepted": len(events)}, 202)
