"""API handler (HTTP API, payload v2).

POST /api/query       question -> retrieval (Embed Pro query vector, vector search, optional Rerank) -> Claude answer with citations
                      (Claude reads the top pages as images plus text and says whether each cited fact is in a table, chart, text...).
                      With "context_focus": true (needs Rerank) Claude gets each page's image plus only the passages Rerank's
                      Context Focus marks as relevant; each source then carries its page text and the passage offsets.
                      Search-only requests accept "context_focus" in either mode, to show each result's matching passages.
                      With "images": false Claude gets the text only (no page images).
                      With "generate": false it stops after retrieval and returns the top 10 pages (search only).
GET  /api/page-image  ?key=<page image key> -> short-lived signed URL for one page image of the demo corpus
"""
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

import retrieval

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET = os.environ.get("DATA_BUCKET", "")
CLAUDE_MODEL_ID = os.environ.get("CLAUDE_MODEL_ID", "")
MAX_PAGE_CHARS = 6000
TOP_K = 5  # pages sent to Claude
SEARCH_TOP_K = 10  # pages returned when only searching
SNIPPET_CHARS = 280
PASSAGE_GAP = "\n...\n"
NO_PASSAGE = "(no passage on this page matched the question)"
URL_TTL_SECONDS = 900
# Only page images of the demo corpus can be signed; nothing else in the bucket is reachable through the API.
PAGE_KEY_RE = re.compile(r"^corpus/[a-z0-9-]+/pages/[a-z0-9_]+/\d{4}\.webp$")

# Regional endpoint and SigV4: objects are encrypted with a KMS key, which requires SigV4 signed URLs.
_s3 = boto3.client("s3", region_name=retrieval.REGION, endpoint_url=f"https://s3.{retrieval.REGION}.amazonaws.com",
                   config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
_bedrock = boto3.client("bedrock-runtime", region_name=retrieval.REGION)

SYSTEM = (
    "You are a research assistant for financial analysts. Answer the question using only the provided "
    "pages from annual reports (10-K filings). Each page comes with its page image and the text extracted from it. "
    "Look at the image: where the extracted text and the image disagree (a table with lost rows or columns, a chart, "
    "a figure the text dropped), trust the image. "
    "Cite every claim with the page it came from, one citation per page, in the form [doc_id p.N | kind] using the "
    "identifiers given, exactly as written (do not translate or change them). "
    "kind says where on that page the supporting information is, and must be exactly one of: "
    "table (a table), chart (a chart or graph), text (a paragraph of body text), footnote (a footnote or note under a "
    "table), figure (a diagram, infographic or picture). Always write kind in English, as listed, even when you answer "
    "in another language. Example: [bank_of_america_2024 p.144 | table]. "
    "If the pages do not contain the answer, say so plainly. Be concise, and quote figures exactly as they appear. "
    "The pages are in English, but write the answer in the language of the question."
)


def _label(hit):
    return f"{hit['doc_id']} p.{hit['page_number'] + 1}"


def _snippet(text, limit=SNIPPET_CHARS):
    """Short plain-text preview of a page: table markup and pipes become spaces, whitespace is collapsed."""
    plain = re.sub(r"<[^>]+>", " ", text or "")
    plain = re.sub(r"[|]+|[-:]{3,}", " ", plain)
    plain = " ".join(plain.split())
    return plain if len(plain) <= limit else plain[:limit].rsplit(" ", 1)[0] + "..."


def _sign(key):
    return _s3.generate_presigned_url("get_object", Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=URL_TTL_SECONDS)


def _page_image(hit):
    """Bytes of the page image, or None when it cannot be read (the page is then sent as text only)."""
    try:
        return _s3.get_object(Bucket=BUCKET, Key=hit["s3_key"])["Body"].read()
    except Exception:  # noqa: BLE001
        logger.warning("could not read page image %s", hit.get("s3_key"), exc_info=True)
        return None


def _page_text(hit):
    return (hit.get("text") or "")[:MAX_PAGE_CHARS]


def _passages(text, spans):
    """The text Claude gets for a page under Context Focus: its relevant passages in page order."""
    return PASSAGE_GAP.join(text[s["start"]:s["end"]].strip() for s in spans) or NO_PASSAGE


def _content(question, hits, texts=None, images=True):
    """One message: for every page its label, its image (when readable and wanted) and its text, then the question."""
    texts = texts if texts is not None else [_page_text(h) for h in hits]
    if images:
        with ThreadPoolExecutor(max_workers=max(1, len(hits))) as pool:
            images = list(pool.map(_page_image, hits))
    else:
        images = [None] * len(hits)
    blocks = []
    for hit, image, text in zip(hits, images, texts):
        blocks.append({"text": f"Page {_label(hit)}"})
        if image:
            blocks.append({"image": {"format": "webp", "source": {"bytes": image}}})
        blocks.append({"text": f"<page id=\"{_label(hit)}\">\n{text}\n</page>"})
    blocks.append({"text": f"Question: {question}"})
    return blocks


def generate(question, hits, texts=None, images=True):
    """Claude's answer and its token usage. texts replaces each page's text (Context Focus passages);
    images=False sends no page images."""
    resp = _bedrock.converse(
        modelId=CLAUDE_MODEL_ID,
        system=[{"text": SYSTEM}],
        messages=[{"role": "user", "content": _content(question, hits, texts, images)}],
        inferenceConfig={"maxTokens": 800, "temperature": 0.0},
    )
    usage = resp.get("usage", {})
    return {"text": resp["output"]["message"]["content"][0]["text"],
            "input_tokens": usage.get("inputTokens"), "output_tokens": usage.get("outputTokens")}


def _response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(body)}


def _endpoints_missing(exc):
    """SageMaker answers ValidationError when the endpoint does not exist (for example, models switched off)."""
    err = getattr(exc, "response", {}).get("Error", {}) if isinstance(exc, ClientError) else {}
    return err.get("Code") == "ValidationError" and "endpoint" in err.get("Message", "").lower()


def handle_query(event):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Request body must be JSON."})
    if not isinstance(body, dict):
        return _response(400, {"error": "Request body must be a JSON object."})

    question = body.get("question")
    mode = body.get("mode", "dense_text_rerank")
    generate_answer = body.get("generate", True)
    context_focus = body.get("context_focus", False)
    send_images = body.get("images", True)
    if not isinstance(generate_answer, bool):
        return _response(400, {"error": "generate must be true or false."})
    if not isinstance(context_focus, bool):
        return _response(400, {"error": "context_focus must be true or false."})
    if not isinstance(send_images, bool):
        return _response(400, {"error": "images must be true or false."})
    if not isinstance(question, str) or not 3 <= len(question.strip()) <= 500:
        return _response(400, {"error": "question must be a string of 3 to 500 characters."})
    if mode not in retrieval.MODES:
        return _response(400, {"error": f"mode must be one of {list(retrieval.MODES)}."})
    if context_focus and generate_answer and mode != "dense_text_rerank":
        return _response(400, {"error": "context_focus needs mode dense_text_rerank when generating an answer."})

    try:
        result = retrieval.retrieve(question.strip(), mode=mode, top_k=TOP_K if generate_answer else SEARCH_TOP_K)
        hits, timings = result["hits"], dict(result["timings_ms"])
        answer, claude = None, None
        page_texts = [_page_text(h) for h in hits]
        spans = None
        if context_focus and hits:
            started = time.time()
            spans = retrieval.focus(question.strip(), page_texts)
            timings["focus"] = round((time.time() - started) * 1000)
        if generate_answer:
            started = time.time()
            if hits:
                sent = [_passages(t, s) for t, s in zip(page_texts, spans)] if spans is not None else page_texts
                out = generate(question.strip(), hits, sent, send_images)
                answer = out["text"]
                claude = {"input_tokens": out["input_tokens"], "output_tokens": out["output_tokens"],
                          "page_chars": sum(map(len, page_texts)),
                          "sent_chars": sum(len(t) for t in sent if t != NO_PASSAGE)}
            else:
                answer = "No relevant pages were found."
            timings["generate"] = round((time.time() - started) * 1000)
        sources = []
        for i, h in enumerate(hits):
            src = {"rank": i + 1, "corpus_id": h.get("corpus_id"), "label": _label(h), "doc_id": h["doc_id"],
                   "page": h["page_number"] + 1, "score": h["score"], "text_source": h.get("text_source"),
                   "snippet": _snippet(h.get("text")), "image_url": _sign(h["s3_key"])}
            if generate_answer or spans is not None:
                src["text"] = page_texts[i]
            if spans is not None:
                src["spans"] = spans[i]
            sources.append(src)
        return _response(200, {"answer": answer, "sources": sources, "mode": mode, "generated": generate_answer,
                               "context_focus": spans is not None, "images": send_images if generate_answer else None,
                               "claude": claude, "timings_ms": timings})
    except Exception as exc:  # noqa: BLE001
        if _endpoints_missing(exc):
            logger.warning("model endpoint missing: %s", exc)
            return _response(503, {"error": "The model endpoints are not running, so search is unavailable right now."})
        logger.exception("query failed")
        return _response(500, {"error": "The query failed. Check the function logs for details."})


def handle_page_image(event):
    key = (event.get("queryStringParameters") or {}).get("key", "")
    if not PAGE_KEY_RE.match(key):
        return _response(400, {"error": "key must be a page image key of the demo corpus."})
    return _response(200, {"url": _sign(key), "expires_in": URL_TTL_SECONDS})


def handler(event, context):
    http = event.get("requestContext", {}).get("http", {})
    method, path = http.get("method"), event.get("rawPath", "")
    if method == "POST" and path == "/api/query":
        return handle_query(event)
    if method == "GET" and path == "/api/page-image":
        return handle_page_image(event)
    return _response(404, {"error": "Not found."})
