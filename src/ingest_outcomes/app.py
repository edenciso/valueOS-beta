import os, json, time, uuid
import boto3
from common.response import ok, err
from common.ddb_rate_limit import enforce_two_bucket_quota
from common.timestream import write_metric

ddb = boto3.client("dynamodb")
s3  = boto3.client("s3")

RAW_EVENTS_TABLE = os.environ["RAW_EVENTS_TABLE"]
RUN_INDEX_TABLE  = os.environ["RUN_INDEX_TABLE"]
ATTRIBUTION_TABLE = os.environ["ATTRIBUTION_TABLE"]
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
    if "ingest:outcomes" not in scopes and "ingest:ai_runs" not in scopes:
        return err("insufficient_scope", 403)

    try:
        enforce_two_bucket_quota(tenant_id, lim_rpm, lim_rpd)
    except RuntimeError:
        return err("quota_exceeded", 429)

    body = json.loads(event.get("body") or "{}")
    outcomes = body.get("events") if isinstance(body.get("events"), list) else [body]

    ts_ms = int(time.time() * 1000)
    batch_id = str(uuid.uuid4())

    s3.put_object(
        Bucket=RAW_BUCKET,
        Key=f"{tenant_id}/outcomes/{ts_ms}-{batch_id}.json",
        Body=json.dumps(outcomes).encode("utf-8"),
        ContentType="application/json",
    )

    attributed = 0
    for o in outcomes:
        obj = o.get("object_ref") or {}
        system, typ, oid = obj.get("system"), obj.get("type"), obj.get("id")
        outcome_id = o.get("outcome_id") or f"out_{uuid.uuid4().hex}"

        pk = f"{tenant_id}#OUTCOME"
        sk = f"{ts_ms}#{outcome_id}"
        ddb.put_item(
            TableName=RAW_EVENTS_TABLE,
            Item={
                "pk": {"S": pk},
                "sk": {"S": sk},
                "tenant_id": {"S": tenant_id},
                "outcome_id": {"S": outcome_id},
                "ttl": {"N": str(_ttl(RAW_TTL_DAYS))}
            }
        )

        write_metric(tenant_id, "outcomes", 1.0, {"system": system or "unknown", "type": typ or "unknown"})

        if system and typ and oid:
            key = {"pk": {"S": f"{tenant_id}#{system}#{typ}#{oid}"}}
            idx = ddb.get_item(TableName=RUN_INDEX_TABLE, Key=key).get("Item")
            if idx and idx.get("run_id"):
                run_id = idx["run_id"]["S"]
                attributed += 1
                ddb.put_item(
                    TableName=ATTRIBUTION_TABLE,
                    Item={
                        "pk": {"S": f"{tenant_id}#ATTR"},
                        "sk": {"S": f"{ts_ms}#{outcome_id}"},
                        "tenant_id": {"S": tenant_id},
                        "outcome_id": {"S": outcome_id},
                        "run_id": {"S": run_id},
                        "credit": {"N": "1.0"},
                        "confidence": {"N": "0.6"},
                        "ttl": {"N": str(_ttl(90))}
                    }
                )
                write_metric(tenant_id, "attributed_outcomes", 1.0, {"system": system, "type": typ})

    return ok({"ok": True, "tenant_id": tenant_id, "accepted": len(outcomes), "attributed": attributed}, 202)
