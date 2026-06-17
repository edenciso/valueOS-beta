import os, time
import boto3
from botocore.exceptions import ClientError

ddb = boto3.client("dynamodb")
RATE_TABLE = os.environ["RATE_LIMIT_TABLE"]

def _ttl(seconds_from_now: int) -> int:
    return int(time.time()) + seconds_from_now

def enforce_two_bucket_quota(tenant_id: str, lim_rpm: int, lim_rpd: int) -> None:
    now_ms = int(time.time() * 1000)
    minute_bucket = now_ms // 60000
    day_bucket = time.strftime("%Y%m%d", time.gmtime())

    pk_min = {"S": f"{tenant_id}#minute#{minute_bucket}"}
    pk_day = {"S": f"{tenant_id}#day#{day_bucket}"}

    try:
        ddb.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": RATE_TABLE,
                        "Key": {"pk": pk_min},
                        "UpdateExpression": "SET cnt = if_not_exists(cnt, :z) + :one, ttl = :ttl",
                        "ConditionExpression": "attribute_not_exists(cnt) OR cnt < :lim",
                        "ExpressionAttributeValues": {
                            ":z": {"N": "0"},
                            ":one": {"N": "1"},
                            ":lim": {"N": str(lim_rpm)},
                            ":ttl": {"N": str(_ttl(180))},
                        },
                    }
                },
                {
                    "Update": {
                        "TableName": RATE_TABLE,
                        "Key": {"pk": pk_day},
                        "UpdateExpression": "SET cnt = if_not_exists(cnt, :z) + :one, ttl = :ttl",
                        "ConditionExpression": "attribute_not_exists(cnt) OR cnt < :lim",
                        "ExpressionAttributeValues": {
                            ":z": {"N": "0"},
                            ":one": {"N": "1"},
                            ":lim": {"N": str(lim_rpd)},
                            ":ttl": {"N": str(_ttl(172800))},
                        },
                    }
                },
            ]
        )
    except ClientError as e:
        if e.response["Error"]["Code"] in ("TransactionCanceledException", "ConditionalCheckFailedException"):
            raise RuntimeError("quota_exceeded")
        raise
