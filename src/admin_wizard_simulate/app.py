import os, json, time, uuid, secrets, hashlib, re
import boto3
from common.response import ok, err
from common.bedrock_claude import claude_summarize

ddb = boto3.client("dynamodb")
lam = boto3.client("lambda")

TENANTS = os.environ["TENANTS_TABLE"]
SP_TABLE = os.environ["SERVICE_PRINCIPALS_TABLE"]
DEMO_SESS = os.environ["DEMO_SESSIONS_TABLE"]

FN_INGEST_AI = os.environ["FN_INGEST_AI_RUNS"]
FN_INGEST_OUT = os.environ["FN_INGEST_OUTCOMES"]
FN_DAILY = os.environ["FN_DAILY_INSIGHTS"]
FN_EVID = os.environ["FN_EVIDENCE_PACK"]

DEMO_TTL_HOURS = int(os.environ.get("DEMO_TTL_HOURS", "72"))
MAX_SAMPLES = int(os.environ.get("MAX_SAMPLES", "12"))
MAX_SAMPLE_BYTES = int(os.environ.get("MAX_SAMPLE_BYTES", "50000"))
MAX_CANONICAL_EVENTS = int(os.environ.get("MAX_CANONICAL_EVENTS", "120"))
MAX_BATCH_EVENTS = int(os.environ.get("MAX_BATCH_EVENTS", "40"))
MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", "250000"))

PII_KEYS = {
    "email","e-mail","mail","phone","mobile","ssn","socialsecurity","address",
    "first_name","last_name","fullname","name","contact","customer_name"
}
PII_PAT_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
PII_PAT_PHONE = re.compile(r"\+?\d[\d\-\s\(\)]{8,}\d")

def _redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in PII_KEYS:
                out[k] = "[REDACTED]"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    if isinstance(obj, str):
        s = obj
        if PII_PAT_EMAIL.search(s): s = PII_PAT_EMAIL.sub("[REDACTED_EMAIL]", s)
        if PII_PAT_PHONE.search(s): s = PII_PAT_PHONE.sub("[REDACTED_PHONE]", s)
        return s
    return obj

def _pbkdf2(secret: str, salt_hex: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), bytes.fromhex(salt_hex), 120000, dklen=32)
    return dk.hex()

def _ttl(seconds_from_now: int) -> int:
    return int(time.time()) + seconds_from_now

def _invoke(fn_name: str, payload: dict):
    resp = lam.invoke(
        FunctionName=fn_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8")
    )
    raw = resp["Payload"].read()
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw.decode("utf-8", errors="ignore")}

def _build_prompt(system: str, samples: list, demo_profile: str | None, max_events: int):
    return f"""
You are ValueOS Demo Simulation Generator.

Goal:
Transform arbitrary integration samples into TWO canonical event batches that drive near-real-time ValueOS metrics and attribution.

CRITICAL OUTPUT RULES:
- Output STRICT JSON ONLY. No markdown. No comments.
- The output MUST parse as JSON.
- Do NOT include tenant_id anywhere.
- Keep total events <= {max_events}.
- Ensure that at least 3 AI_RUN events include correlation.external_object_refs referencing the SAME objects used by BUSINESS_OUTCOME events (so last-touch attribution works).
- Use realistic IDs. Use ISO8601 for event_time.

OUTPUT JSON SHAPE (exact keys):
{{
  "ai_runs": {{ "events": [ ...AI_RUN objects... ] }},
  "business_outcomes": {{ "events": [ ...BUSINESS_OUTCOME objects... ] }},
  "notes": {{
    "assumptions": [string],
    "coverage": {{ "ai_runs_count": number, "outcomes_count": number }}
  }}
}}

AI_RUN object schema:
{{
  "event_id": "ulid_or_uuid",
  "event_time": "YYYY-MM-DDTHH:MM:SSZ",
  "source": "demo_simulator",
  "correlation": {{
    "run_id": "run_x",
    "workflow_id": "wf_x",
    "external_object_refs": [
      {{ "system": "<system>", "type": "<object_type>", "id": "<object_id>" }}
    ]
  }},
  "data": {{
    "asset_id": "asset_or_agent_id",
    "model": {{ "provider": "bedrock", "model_id": "claude" }},
    "metrics": {{ "latency_ms": 1234, "cost_usd": 0.012 }}
  }}
}}

BUSINESS_OUTCOME object schema:
{{
  "outcome_id": "out_x",
  "event_time": "YYYY-MM-DDTHH:MM:SSZ",
  "event_type": "BUSINESS_OUTCOME",
  "object_ref": {{ "system": "<system>", "type": "<object_type>", "id": "<object_id>" }},
  "event_name": "pipeline_stage_change|ticket_resolved|conversion|revenue_delta",
  "delta": {{ "value": 123.45, "unit": "USD" }},
  "kpi_tags": ["revenue","conversion","cycle_time"]
}}

Context:
- System = {system}
- Demo profile = {demo_profile or "generic"}
- Samples (redacted):
{json.dumps(samples)[:12000]}
"""

def handler(event, context):
    body_raw = event.get("body") or "{}"
    if isinstance(body_raw, str) and len(body_raw.encode("utf-8")) > MAX_BODY_BYTES:
        return err("payload_too_large", 413)

    try:
        body = json.loads(body_raw) if isinstance(body_raw, str) else (body_raw or {})
    except Exception:
        return err("invalid_json", 400)

    system = body.get("system", "crm")
    demo_profile = body.get("demo_profile")
    samples = body.get("samples") or []
    if not isinstance(samples, list) or len(samples) == 0:
        return err("missing_samples", 400)
    if len(samples) > MAX_SAMPLES:
        return err(f"too_many_samples_max_{MAX_SAMPLES}", 400)

    redacted_samples = []
    for s in samples:
        s_json = json.dumps(s)
        if len(s_json.encode("utf-8")) > MAX_SAMPLE_BYTES:
            return err(f"sample_too_large_max_{MAX_SAMPLE_BYTES}", 400)
        redacted_samples.append(_redact(s))

    session_id = "ds_" + uuid.uuid4().hex[:12]
    demo_tenant_id = "demo_" + uuid.uuid4().hex[:10]
    now = int(time.time())
    exp = now + DEMO_TTL_HOURS * 3600

    ddb.put_item(
        TableName=TENANTS,
        Item={
            "tenant_id": {"S": demo_tenant_id},
            "name": {"S": body.get("tenant_label") or f"Demo ({system})"},
            "demo": {"BOOL": True},
            "created_at": {"N": str(now)},
            "expires_at": {"N": str(exp)}
        }
    )

    client_id = "sp_" + secrets.token_hex(8)
    client_secret = secrets.token_urlsafe(24)
    salt = secrets.token_hex(16)
    secret_hash = _pbkdf2(client_secret, salt)

    lim_rpm = int(body.get("lim_rpm") or 5000)
    lim_rpd = int(body.get("lim_rpd") or 200000)

    ddb.put_item(
        TableName=SP_TABLE,
        Item={
            "client_id": {"S": client_id},
            "tenant_id": {"S": demo_tenant_id},
            "salt": {"S": salt},
            "secret_hash": {"S": secret_hash},
            "status": {"S": "active"},
            "scopes": {"SS": ["ingest:ai_runs", "ingest:outcomes"]},
            "lim_rpm": {"N": str(lim_rpm)},
            "lim_rpd": {"N": str(lim_rpd)},
            "demo": {"BOOL": True},
            "ttl": {"N": str(_ttl(DEMO_TTL_HOURS * 3600))}
        }
    )

    prompt = _build_prompt(system, redacted_samples, demo_profile, MAX_CANONICAL_EVENTS)
    model_text = claude_summarize(
        system="You output strict JSON only. No extra text.",
        user=prompt,
        max_tokens=1200
    )

    try:
        generated = json.loads(model_text)
    except Exception:
        ddb.put_item(
            TableName=DEMO_SESS,
            Item={
                "session_id": {"S": session_id},
                "tenant_id": {"S": demo_tenant_id},
                "created_at": {"N": str(now)},
                "ttl": {"N": str(_ttl(DEMO_TTL_HOURS * 3600))},
                "status": {"S": "model_output_invalid_json"},
            }
        )
        return err("bedrock_output_invalid_json", 502)

    ai_events = (generated.get("ai_runs") or {}).get("events") or []
    out_events = (generated.get("business_outcomes") or {}).get("events") or []
    if not isinstance(ai_events, list) or not isinstance(out_events, list):
        return err("invalid_generated_shape", 502)

    if len(ai_events) + len(out_events) > MAX_CANONICAL_EVENTS:
        return err(f"generated_too_many_events_max_{MAX_CANONICAL_EVENTS}", 502)

    authorizer_ctx = {
        "tenant_id": demo_tenant_id,
        "scope": "ingest:ai_runs ingest:outcomes",
        "lim_rpm": str(lim_rpm),
        "lim_rpd": str(lim_rpd),
        "client_id": client_id
    }

    def invoke_ingest(fn_name: str, events_list: list):
        for i in range(0, len(events_list), MAX_BATCH_EVENTS):
            batch = events_list[i:i+MAX_BATCH_EVENTS]
            payload = {
                "requestContext": {"authorizer": authorizer_ctx},
                "body": json.dumps({"events": batch})
            }
            _invoke(fn_name, payload)

    invoke_ingest(FN_INGEST_AI, ai_events)
    invoke_ingest(FN_INGEST_OUT, out_events)

    _invoke(FN_DAILY, {"tenant_id": demo_tenant_id})
    _invoke(FN_EVID, {"tenant_id": demo_tenant_id})

    ddb.put_item(
        TableName=DEMO_SESS,
        Item={
            "session_id": {"S": session_id},
            "tenant_id": {"S": demo_tenant_id},
            "system": {"S": system},
            "created_at": {"N": str(now)},
            "expires_at": {"N": str(exp)},
            "ttl": {"N": str(_ttl(DEMO_TTL_HOURS * 3600))},
            "counts": {"S": json.dumps({"ai_runs": len(ai_events), "outcomes": len(out_events)})},
            "status": {"S": "ok"}
        }
    )

    domain = (event.get("requestContext") or {}).get("domainName", "")
    stage = os.environ.get("STAGE","dev")
    api_base = f"https://{domain}/{stage}" if domain else ""

    return ok({
        "ok": True,
        "demo_tenant_id": demo_tenant_id,
        "demo_session_id": session_id,
        "m2m_credentials": {
            "client_id": client_id,
            "client_secret": client_secret,
            "scopes": ["ingest:ai_runs", "ingest:outcomes"],
            "limits": {"lim_rpm": lim_rpm, "lim_rpd": lim_rpd}
        },
        "generated_counts": {"ai_runs": len(ai_events), "business_outcomes": len(out_events)},
        "demo_links": {
            "portfolio": f"{api_base}/v1/admin/portfolio?tenant_id={demo_tenant_id}&hours=168",
            "latest_insights": f"{api_base}/v1/admin/insights/latest?tenant_id={demo_tenant_id}",
            "latest_evidence": f"{api_base}/v1/admin/evidence/latest?tenant_id={demo_tenant_id}",
            "m2m_token": f"{api_base}/v1/m2m/token",
            "m2m_ingest_ai_runs": f"{api_base}/v1/m2m/events/ai-runs",
            "m2m_ingest_outcomes": f"{api_base}/v1/m2m/events/business-outcomes"
        }
    }, 200)
