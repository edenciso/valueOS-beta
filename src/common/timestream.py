import os, time
import boto3

tsw = boto3.client("timestream-write")
TS_DB = os.environ["TS_DB"]
TS_TABLE = os.environ["TS_TABLE"]

def write_metric(tenant_id: str, measure: str, value: float, dims: dict | None = None, ts_ms: int | None = None):
    ts_ms = ts_ms or int(time.time() * 1000)
    dimensions = [{"Name": "tenant_id", "Value": tenant_id}]
    if dims:
        for k, v in dims.items():
            dimensions.append({"Name": str(k), "Value": str(v)})

    tsw.write_records(
        DatabaseName=TS_DB,
        TableName=TS_TABLE,
        Records=[
            {
                "Dimensions": dimensions,
                "MeasureName": measure,
                "MeasureValue": str(value),
                "MeasureValueType": "DOUBLE",
                "Time": str(ts_ms),
                "TimeUnit": "MILLISECONDS",
            }
        ],
    )
