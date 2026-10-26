"""CloudFormation custom resource: publish the static web app to the site bucket.

Copies every file under ./web into the bucket and writes config.js from the resource properties
(user pool client, region, title). On delete the bucket is emptied so the stack can be removed.
Every code path reports back to CloudFormation, so a failure never leaves the stack hanging.
"""
import json
import logging
import mimetypes
import time
import urllib.request
from pathlib import Path

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

WEB_DIR = Path(__file__).resolve().parent / "web"
CONTENT_TYPES = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml",
                 ".json": "application/json", ".png": "image/png", ".webp": "image/webp",
                 ".woff2": "font/woff2"}
s3 = boto3.client("s3")


def send_response(event, context, status, reason="", physical_id=None):
    body = json.dumps({
        "Status": status,
        "Reason": (reason or f"See CloudWatch log stream {context.log_stream_name}")[:1000],
        "PhysicalResourceId": physical_id or event.get("PhysicalResourceId") or context.log_stream_name,
        "StackId": event["StackId"], "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"], "NoEcho": False, "Data": {},
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


def content_type(path):
    return CONTENT_TYPES.get(path.suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def put(bucket, key, body, ctype):
    # no-cache: browsers revalidate on every load, so a redeploy shows up immediately.
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=ctype, CacheControl="no-cache")


def list_keys(bucket):
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        keys += [o["Key"] for o in page.get("Contents", [])]
    return keys


def delete_keys(bucket, keys):
    for i in range(0, len(keys), 1000):
        s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]], "Quiet": True})


def empty_bucket(bucket):
    """Delete every object version and delete marker, so a versioned bucket can be deleted too."""
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket):
        objs = [{"Key": o["Key"], "VersionId": o["VersionId"]}
                for o in page.get("Versions", []) + page.get("DeleteMarkers", [])]
        for i in range(0, len(objs), 1000):
            s3.delete_objects(Bucket=bucket, Delete={"Objects": objs[i:i + 1000], "Quiet": True})


def publish(bucket, config):
    keys = set()
    for path in sorted(p for p in WEB_DIR.rglob("*") if p.is_file()):
        key = path.relative_to(WEB_DIR).as_posix()
        put(bucket, key, path.read_bytes(), content_type(path))
        keys.add(key)
    put(bucket, "config.js", f"window.FRW_CONFIG = {json.dumps(config)};\n".encode(), "text/javascript")
    keys.add("config.js")
    stale = [k for k in list_keys(bucket) if k not in keys]
    delete_keys(bucket, stale)
    logger.info("published %d files, removed %d stale", len(keys), len(stale))


def handler(event, context):
    props = event.get("ResourceProperties", {})
    bucket = props.get("BucketName", "")
    try:
        if event["RequestType"] == "Delete":
            empty_bucket(bucket)
        else:
            publish(bucket, {
                "region": props["Region"],
                "userPoolClientId": props["UserPoolClientId"],
                "appTitle": props.get("AppTitle", ""),
                "apiBase": "/api",
            })
        send_response(event, context, "SUCCESS", physical_id=f"site-{bucket}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("site deployment failed")
        # Never fail a delete: the stack must be removable even if the bucket is already gone.
        send_response(event, context, "SUCCESS" if event["RequestType"] == "Delete" else "FAILED",
                      reason=f"{type(exc).__name__}: {exc}", physical_id=f"site-{bucket}")
