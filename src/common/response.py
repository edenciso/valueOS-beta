import json

def ok(body: dict, code: int = 200):
    return {"statusCode": code, "headers": {"content-type":"application/json"}, "body": json.dumps(body)}

def err(message: str, code: int):
    return ok({"ok": False, "error": message}, code)
