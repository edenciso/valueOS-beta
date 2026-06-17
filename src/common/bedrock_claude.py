import os, json, boto3

bedrock = boto3.client("bedrock-runtime")
MODEL_ID = os.environ["BEDROCK_MODEL_ID"]

def claude_summarize(system: str, user: str, max_tokens: int = 600) -> str:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "temperature": 0.2,
    }

    resp = bedrock.invoke_model(
        modelId=MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body).encode("utf-8"),
    )
    out = json.loads(resp["body"].read())
    if isinstance(out.get("content"), list) and out["content"]:
        return out["content"][0].get("text", "")
    return out.get("completion", "") or json.dumps(out)
