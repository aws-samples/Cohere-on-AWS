"""CloudFormation custom resource handler for the data stack.

Actions (ResourceProperties.Action):
  CreateIndex         create the OpenSearch Serverless page index (idempotent)
  EmptyBucketOnDelete empty an S3 bucket when the stack is deleted

Every code path reports back to CloudFormation, including on error and on near-timeout,
so a failure never leaves the stack hanging.
"""
import hashlib
import json
import logging
import time
import urllib.error
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = boto3.session.Session().region_name


def send_response(event, context, status, reason="", data=None, physical_id=None):
    body = json.dumps({
        "Status": status,
        "Reason": (reason or f"See CloudWatch log stream {context.log_stream_name}")[:1000],
        "PhysicalResourceId": physical_id or event.get("PhysicalResourceId") or context.log_stream_name,
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "NoEcho": False,
        "Data": data or {},
    }).encode()
    req = urllib.request.Request(event["ResponseURL"], data=body, method="PUT",
                                 headers={"Content-Type": "", "Content-Length": str(len(body))})
    for attempt in range(3):
        try:
            urllib.request.urlopen(req, timeout=30).read()
            return
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to send response (attempt %s): %s", attempt + 1, exc)
            time.sleep(2)


# ----------------------------- OpenSearch Serverless -----------------------------
def aoss_endpoint(collection_name, deadline):
    client = boto3.client("opensearchserverless")
    while time.time() < deadline:
        details = client.batch_get_collection(names=[collection_name])["collectionDetails"]
        if details and details[0]["status"] == "ACTIVE" and details[0].get("collectionEndpoint"):
            return details[0]["collectionEndpoint"]
        time.sleep(10)
    raise TimeoutError(f"Collection {collection_name} did not become ACTIVE in time")


def aoss_request(host, method, path, body=None):
    creds = boto3.Session().get_credentials().get_frozen_credentials()
    data = json.dumps(body) if body is not None else ""
    url = f"{host}{path}"
    # OpenSearch Serverless requires the payload hash header on signed requests.
    headers = {"Content-Type": "application/json", "x-amz-content-sha256": hashlib.sha256(data.encode()).hexdigest()}
    request = AWSRequest(method=method, url=url, data=data, headers=headers)
    SigV4Auth(creds, "aoss", REGION).add_auth(request)
    http = urllib.request.Request(url, data=data.encode() if data else None, method=method, headers=dict(request.headers))
    try:
        with urllib.request.urlopen(http, timeout=60) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode()


def index_mapping(dimension):
    knn = {
        "type": "knn_vector",
        "dimension": dimension,
        "data_type": "byte",
        # NextGen collections reject an explicit "engine" and pick faiss themselves.
        "method": {"name": "hnsw", "space_type": "l2"},
    }
    return {
        "settings": {"index": {"knn": True}},
        "mappings": {"properties": {
            "corpus_id": {"type": "long"},
            "doc_id": {"type": "keyword"},
            "page_number": {"type": "integer"},
            "s3_key": {"type": "keyword"},
            "text": {"type": "text"},
            "text_source": {"type": "keyword"},  # bundle | parse_cache | parse_live | none
            "route_reason": {"type": "keyword"},  # text_ok | low_text | table_heavy | parse_off | ..._no_parse | ..._parse_failed
            "text_embedding": knn,
        }},
    }


def create_index(props, deadline):
    host = aoss_endpoint(props["CollectionName"], deadline)
    index = props["IndexName"]
    mapping = index_mapping(int(props["Dimension"]))
    last = None
    while time.time() < deadline:
        status, text = aoss_request(host, "PUT", f"/{index}", mapping)
        logger.info("create index -> %s %s", status, text[:300])
        if status in (200, 201):
            return {"IndexName": index}
        if status == 400 and "resource_already_exists" in text:
            return {"IndexName": index}
        last = f"{status} {text[:300]}"
        # 403 is expected for a short while after the data access policy is created. Anything
        # else other than throttling/5xx is a permanent error, so fail fast with the reason.
        if status not in (403, 404, 429) and status < 500:
            raise RuntimeError(f"Index creation rejected: {last}")
        time.sleep(15)
    raise TimeoutError(f"Index creation did not succeed in time: {last}")


# ----------------------------------- S3 -----------------------------------------
def empty_bucket(bucket):
    s3 = boto3.client("s3")
    try:
        paginator = s3.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket):
            objs = [{"Key": o["Key"], "VersionId": o["VersionId"]}
                    for o in page.get("Versions", []) + page.get("DeleteMarkers", [])]
            for i in range(0, len(objs), 1000):
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objs[i:i + 1000], "Quiet": True})
    except s3.exceptions.NoSuchBucket:
        logger.info("Bucket %s already gone", bucket)


# ---------------------------------- handler -------------------------------------
def handler(event, context):
    logger.info("event: %s", json.dumps({k: v for k, v in event.items() if k != "ResponseURL"}))
    props = event.get("ResourceProperties", {})
    request_type = event["RequestType"]
    # Leave 60 seconds to report back to CloudFormation.
    deadline = time.time() + context.get_remaining_time_in_millis() / 1000 - 60
    try:
        action = props.get("Action")
        if action == "CreateIndex":
            physical_id = f"{props['CollectionName']}/{props['IndexName']}"
            data = {}
            if request_type in ("Create", "Update"):
                data = create_index(props, deadline)
            send_response(event, context, "SUCCESS", data=data, physical_id=physical_id)
        elif action == "EmptyBucketOnDelete":
            if request_type == "Delete":
                empty_bucket(props["BucketName"])
            send_response(event, context, "SUCCESS", physical_id=props["BucketName"])
        else:
            send_response(event, context, "FAILED", reason=f"Unknown action {action!r}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Handler failed")
        # Never fail a delete because of cleanup problems that CloudFormation cannot fix.
        status = "SUCCESS" if request_type == "Delete" else "FAILED"
        send_response(event, context, status, reason=f"{type(exc).__name__}: {exc}")
