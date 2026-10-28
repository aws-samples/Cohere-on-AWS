"""Ingest one page: pick the page text, embed it with Embed Pro, index in OpenSearch.

Invoked by the Step Functions Map state with {"bucket": ..., "page": {manifest row}}.

Where the page text comes from (the decision tree, applied per page):
  1. The bundle ships text for every page (<prefix>/text/...). Embed + Rerank needs nothing else.
  2. If PARSE_MODE is not "off", the router (router.py) checks the bundle text. Pages with almost no text
     (scans) or mostly tables are sent to Parse.
  3. For a routed page: use the Parse cache in the bundle (<prefix>/parsed/...) if it has the page; otherwise,
     when PARSE_MODE is "live", call the Parse endpoint and save the result to the cache.
  4. If Parse is unavailable or fails, keep the bundle text. A page with no text at all is recorded without a vector.

Idempotent: the DynamoDB state table and the deterministic OpenSearch document ID mean a retry or a
re-run never duplicates a page.
"""
import base64
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.exceptions import ClientError

import router

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ["AWS_REGION"]
TABLE = os.environ["STATE_TABLE"]
EMBED_ENDPOINT = os.environ["EMBED_ENDPOINT"]
COLLECTION_ENDPOINT = os.environ["COLLECTION_ENDPOINT"].rstrip("/")
INDEX = os.environ.get("INDEX_NAME", "pages")
DIMENSION = int(os.environ.get("EMBEDDING_DIMENSION", "1024"))
MAX_TEXT_CHARS = 30000

PARSE_MODE = os.environ.get("PARSE_MODE", "off")  # off | cached | live
PARSE_ENDPOINT = os.environ.get("PARSE_ENDPOINT", "")
ROUTE_LOW_TEXT = os.environ.get("ROUTE_LOW_TEXT", "true") == "true"
ROUTE_TABLES = os.environ.get("ROUTE_TABLES", "true") == "true"
MIN_TEXT_CHARS = int(os.environ.get("MIN_TEXT_CHARS", "600"))
TABLE_RATIO = float(os.environ.get("TABLE_RATIO", "0.5"))

_cfg = Config(retries={"max_attempts": 6, "mode": "adaptive"}, read_timeout=120)
s3 = boto3.client("s3")
smr = boto3.client("sagemaker-runtime", config=_cfg)
table = boto3.resource("dynamodb").Table(TABLE)


def invoke(endpoint, payload):
    resp = smr.invoke_endpoint(EndpointName=endpoint, ContentType="application/json", Body=json.dumps(payload))
    return json.loads(resp["Body"].read())


def embed_int8(item):
    """item is either {"texts": [...]} or {"inputs": [...]}; returns the first int8 vector."""
    body = {"input_type": "search_document", "embedding_types": ["int8"], "output_dimension": DIMENSION}
    body.update(item)
    out = invoke(EMBED_ENDPOINT, body)
    vector = out["embeddings"]["int8"][0]
    if len(vector) != DIMENSION:
        raise ValueError(f"Unexpected embedding length {len(vector)}")
    return vector


def index_document(doc_id, doc):
    creds = boto3.Session().get_credentials().get_frozen_credentials()
    data = json.dumps(doc)
    url = f"{COLLECTION_ENDPOINT}/{INDEX}/_doc/{doc_id}"
    headers = {"Content-Type": "application/json", "x-amz-content-sha256": hashlib.sha256(data.encode()).hexdigest()}
    request = AWSRequest(method="PUT", url=url, data=data, headers=headers)
    SigV4Auth(creds, "aoss", REGION).add_auth(request)
    http = urllib.request.Request(url, data=data.encode(), method="PUT", headers=dict(request.headers))
    for attempt in range(5):
        try:
            with urllib.request.urlopen(http, timeout=60) as resp:
                return resp.status
        except urllib.error.HTTPError as err:
            body = err.read().decode()[:300]
            # Retry throttling, server errors and the short NextGen wake-up window.
            if err.code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Indexing failed: {err.code} {body}") from None


def sibling_key(page_key, folder):
    """corpus/x/pages/doc/0001.webp -> corpus/x/<folder>/doc/0001.md"""
    return page_key.replace("/pages/", f"/{folder}/", 1).rsplit(".", 1)[0] + ".md"


def read_text(bucket, key):
    """Returns the object's text, or None when the object does not exist."""
    try:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    except ClientError as err:
        if err.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise


def parse_page(data_uri):
    parsed = invoke(PARSE_ENDPOINT, {
        "model": "parse-v5.0",
        "document": {"type": "image_url", "image_url": data_uri},
        "output_format": "markdown",
    })
    return (parsed["pages"][0]["markdown"]["content"] or "").strip()


def choose_text(bundle_text, *, mode, read_cache, write_cache, parse_live):
    """Pick the text to embed. Returns (text, text_source, route_reason).

    read_cache()/write_cache(text)/parse_live() are callables so this can be tested without AWS.
    text_source is one of: bundle, parse_cache, parse_live, none.
    route_reason may end in _no_parse (Parse unavailable), _parse_failed (Parse errored; bundle text kept) or
    _parse_empty (cache says Parse returned nothing and the page has no other text).
    """
    bundle_text = (bundle_text or "").strip()
    fallback = (bundle_text, "bundle") if bundle_text else ("", "none")
    if mode == "off":
        return (*fallback, "parse_off")

    needs_parse, reason = router.route(
        bundle_text, route_low_text=ROUTE_LOW_TEXT, route_tables=ROUTE_TABLES,
        min_chars=MIN_TEXT_CHARS, table_ratio=TABLE_RATIO)
    if not needs_parse:
        return (*fallback, reason)

    cached = read_cache()
    if cached is not None and cached.strip():
        return cached.strip(), "parse_cache", reason
    if cached is not None and not bundle_text:
        # Parse already ran on this page and returned nothing, and there is no other text: a genuinely blank page.
        # Do not call Parse again; it is recorded without a vector.
        return "", "none", f"{reason}_parse_empty"
    if mode == "live":
        try:
            text = parse_live()
        except Exception:  # Parse can return transient 500s; keep going with the bundle text if there is any
            logger.exception("Parse failed")
            if not bundle_text:
                raise  # nothing to fall back on: let Step Functions retry the page
            return (*fallback, f"{reason}_parse_failed")
        if text:
            write_cache(text)
            return text, "parse_live", reason
    return (*fallback, f"{reason}_no_parse")


def handler(event, context):
    bucket = event["bucket"]
    page = event["page"]
    page_id = str(page["corpus_id"])

    existing = table.get_item(Key={"page_id": page_id}).get("Item")
    if existing and existing.get("status") == "done":
        return {"page_id": page_id, "status": "skipped"}

    stage = "check_image"
    try:
        # Only the text is embedded. The page image is checked here so a missing one fails early (the web app shows it),
        # and it is read in full only if the page is sent to Parse.
        s3.head_object(Bucket=bucket, Key=page["key"])

        def data_uri():
            image = s3.get_object(Bucket=bucket, Key=page["key"])["Body"].read()
            return "data:image/webp;base64," + base64.b64encode(image).decode()

        stage = "choose_text"
        text_key, parsed_key = sibling_key(page["key"], "text"), sibling_key(page["key"], "parsed")
        text, text_source, reason = choose_text(
            read_text(bucket, text_key) or "",
            mode=PARSE_MODE,
            read_cache=lambda: read_text(bucket, parsed_key),
            write_cache=lambda t: s3.put_object(Bucket=bucket, Key=parsed_key, Body=t.encode("utf-8"),
                                                ContentType="text/markdown"),
            parse_live=lambda: parse_page(data_uri()),
        )
        text = text[:MAX_TEXT_CHARS]

        doc = {
            "corpus_id": page["corpus_id"],
            "doc_id": page["doc_id"],
            "page_number": page["page_number"],
            "s3_key": page["key"],
            "text": text,
            "text_source": text_source,
            "route_reason": reason,
        }
        if text:
            stage = "embed_text"
            doc["text_embedding"] = embed_int8({"texts": [text]})
        # A page with no text at all (a blank page) is recorded without a vector, so search cannot return it.

        stage = "index"
        index_document(page_id, doc)
        table.put_item(Item={"page_id": page_id, "status": "done", "chars": len(text), "text_source": text_source,
                             "route_reason": reason, "updated_at": int(time.time())})
        return {"page_id": page_id, "status": "done", "text_source": text_source}
    except Exception as exc:  # record, then let Step Functions retry
        logger.exception("page %s failed", page_id)
        table.put_item(Item={"page_id": page_id, "status": "failed", "stage": stage,
                             "error": f"{type(exc).__name__}: {exc}"[:500],
                             "updated_at": int(time.time())})
        raise
