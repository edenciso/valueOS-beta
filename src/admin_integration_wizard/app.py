import json
from common.response import ok, err
from common.bedrock_claude import claude_summarize

def handler(event, context):
    body = json.loads(event.get("body") or "{}")
    system = body.get("system", "crm")
    samples = body.get("samples", [])
    if not samples:
        return err("missing_samples", 400)

    prompt = f"""
You are ValueOS Integration Wizard.
Goal: produce a minimal mapping spec to ValueOS beta schema.
Return JSON only with:
- required_fields_ai_run
- required_fields_business_outcome
- mapping_rules (jsonpath-like pointers from sample -> canonical)
- recommended_webhook_fields
- minimal_connector_steps (bulleted)
System: {system}
Samples:
{json.dumps(samples)[:12000]}
"""
    text = claude_summarize(
        system="You generate strict JSON. No commentary.",
        user=prompt,
        max_tokens=800
    )
    return ok({"ok": True, "wizard_output": text})
