"""Query path: Embed Pro (query) -> k-NN over the page-text vectors -> optional Rerank, and optional Context Focus.

Used by the API Lambda and by the local evaluation script, so the benchmark measures the same code
that serves users. Configuration comes from environment variables.
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from http.client import HTTPException

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
COLLECTION_ENDPOINT = os.environ.get("COLLECTION_ENDPOINT", "").rstrip("/")
INDEX = os.environ.get("INDEX_NAME", "pages")
QUERY_EMBED_ENDPOINT = os.environ.get("QUERY_EMBED_ENDPOINT", "")
RERANK_ENDPOINT = os.environ.get("RERANK_ENDPOINT", "")
DIMENSION = int(os.environ.get("EMBEDDING_DIMENSION", "1024"))
RERANK_CHARS = int(os.environ.get("RERANK_CHARS", "3000"))
FOCUS_THRESHOLD = float(os.environ.get("FOCUS_THRESHOLD", "0.3"))
FOCUS_MAX_SPANS = int(os.environ.get("FOCUS_MAX_SPANS", "10"))

_cfg = Config(retries={"max_attempts": 4, "mode": "adaptive"}, read_timeout=60)
_session = boto3.Session(region_name=REGION)
_smr = _session.client("sagemaker-runtime", config=_cfg)

SOURCE_FIELDS = ["corpus_id", "doc_id", "page_number", "s3_key", "text", "text_source"]


def _invoke(endpoint, payload):
    resp = _smr.invoke_endpoint(EndpointName=endpoint, ContentType="application/json", Body=json.dumps(payload))
    return json.loads(resp["Body"].read())


def _search(body, index=None):
    creds = _session.get_credentials().get_frozen_credentials()
    data = json.dumps(body)
    url = f"{COLLECTION_ENDPOINT}/{index or INDEX}/_search"
    headers = {"Content-Type": "application/json", "x-amz-content-sha256": hashlib.sha256(data.encode()).hexdigest()}
    request = AWSRequest(method="POST", url=url, data=data, headers=headers)
    SigV4Auth(creds, "aoss", REGION).add_auth(request)
    http = urllib.request.Request(url, data=data.encode(), method="POST", headers=dict(request.headers))
    for attempt in range(4):
        try:
            with urllib.request.urlopen(http, timeout=30) as resp:
                return json.loads(resp.read())["hits"]["hits"]
        except urllib.error.HTTPError as err:
            if err.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Search failed: {err.code} {err.read().decode()[:300]}") from None
        except (urllib.error.URLError, HTTPException, ConnectionError, TimeoutError) as exc:
            # The collection sometimes drops a connection (for example while it wakes up); retry like a throttle.
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Search failed: {type(exc).__name__}") from None


def embed_query(question):
    out = _invoke(QUERY_EMBED_ENDPOINT, {"texts": [question], "input_type": "search_query",
                                  "embedding_types": ["int8"], "output_dimension": DIMENSION})
    return out["embeddings"]["int8"][0]


def _hit(h):
    src = h["_source"]
    return {"corpus_id": src["corpus_id"], "doc_id": src["doc_id"], "page_number": src["page_number"],
            "s3_key": src["s3_key"], "text": src.get("text", ""), "text_source": src.get("text_source"),
            "score": h.get("_score")}


def dense(vector, field, k):
    body = {"size": k, "_source": SOURCE_FIELDS, "query": {"knn": {field: {"vector": vector, "k": k}}}}
    return [_hit(h) for h in _search(body)]


def rerank(question, hits, top_n):
    if not hits:
        return []
    docs = [(h["text"] or "")[:RERANK_CHARS] or "(blank page)" for h in hits]
    out = _invoke(RERANK_ENDPOINT, {"query": question, "documents": docs, "top_n": min(top_n, len(docs))})
    return [dict(hits[r["index"]], score=r["relevance_score"]) for r in out["results"]]


def focus(question, texts):
    """Context Focus: for each text, the passages Rerank marks as relevant to the question, sorted by position.
    Returns one list of {"start", "end", "score"} per text, as character offsets into that text."""
    if not texts:
        return []
    out = _invoke(RERANK_ENDPOINT, {
        "query": question, "documents": [t or "(blank page)" for t in texts], "top_n": len(texts),
        "relevant_spans": {"mode": "enabled", "threshold": FOCUS_THRESHOLD, "max_spans": FOCUS_MAX_SPANS}})
    spans = [[] for _ in texts]
    for r in out["results"]:
        if texts[r["index"]]:
            spans[r["index"]] = sorted(({"start": s["start"], "end": s["end"], "score": round(s["score"], 3)}
                                        for s in r.get("relevant_spans", [])), key=lambda s: s["start"])
    return spans


MODES = ("dense_text", "dense_text_rerank")


def retrieve(question, mode="dense_text_rerank", top_k=5, candidates=30):
    """Returns {"hits": [...], "timings_ms": {...}}. Hits carry corpus_id, doc_id, page_number, s3_key, text, score."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    timings = {}
    t = time.time()

    def lap(name):
        nonlocal t
        now = time.time()
        timings[name] = round((now - t) * 1000)
        t = now

    vector = embed_query(question)
    lap("embed_query")
    # Rerank scores text, so it re-scores a larger set of candidates from the text vectors.
    hits = dense(vector, "text_embedding", candidates if mode == "dense_text_rerank" else top_k)
    lap("search")
    if mode == "dense_text_rerank":
        hits = rerank(question, hits, top_k)
        lap("rerank")
    else:
        hits = hits[:top_k]
    return {"hits": hits, "timings_ms": timings}
