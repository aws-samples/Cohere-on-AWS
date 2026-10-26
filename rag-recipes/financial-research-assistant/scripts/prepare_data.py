#!/usr/bin/env python3
"""Download the public demo dataset and load it into your deployment's data bucket.

Run this once after the stack has finished creating and before starting ingestion. It needs no model endpoint and
no GPU, only network access to Hugging Face and AWS credentials that can write to the data bucket.

What it loads (ViDoRe V3 Finance, English: three FY2024 annual reports, 928 pages):
  <prefix>/pages/<doc_id>/<page 4 digits>.webp   page images, at most 2048 px on the long side (the format Parse expects)
  <prefix>/text/<doc_id>/<page 4 digits>.md      the dataset's page text, so Embed + Rerank needs no Parse
  <prefix>/manifest.jsonl                        one row per page; ingestion reads this
  <prefix>/queries.jsonl, qrels.jsonl            benchmark questions (six languages) and the pages that answer them
  <prefix>/documents_metadata.jsonl              source document details

Usage:
  pip install -r scripts/requirements-data.txt
  python scripts/prepare_data.py [--stack frw] [--region us-east-1] [--profile NAME] [--dry-run]
  python scripts/prepare_data.py --bucket <data-bucket-name>      # instead of looking it up from the stack

Safe to run again: pages already in the bucket are skipped (use --force to rewrite them).
Licence: the documents are SEC filings labelled "Public Domain" in the dataset metadata; the questions, judgments and
annotations are CC BY 4.0 (see NOTICE.md).
"""
import argparse
import io
import json
import sys

import boto3

REPO = "vidore/vidore_v3_finance_en"
DOCS = ["morgan_stanley_2024", "bank_of_america_2024", "wells_fargo_2024"]
MAX_SIDE = 2048
DEFAULT_PREFIX = "corpus/vidore-v3-finance-en"


def page_key(prefix, doc_id, page_number, kind="pages"):
    """S3 key for a page image (kind='pages', .webp) or its text (kind='text', .md)."""
    ext = "webp" if kind == "pages" else "md"
    return f"{prefix}/{kind}/{doc_id}/{int(page_number):04d}.{ext}"


def to_webp(img_bytes):
    from PIL import Image  # imported here so the pure helpers below can be tested without Pillow

    img = Image.open(io.BytesIO(img_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=85)
    return out.getvalue()


def select_eval_rows(queries, qrels, page_ids):
    """Questions and judgments restricted to the pages we load: only positive judgments whose page is loaded."""
    kept = [q for q in qrels if q["corpus_id"] in page_ids and q["score"] > 0]
    query_ids = {q["query_id"] for q in kept}
    queries_out = [{k: q[k] for k in ("query_id", "query", "language", "query_types", "answer")}
                   for q in queries if q["query_id"] in query_ids]
    qrels_out = [{k: q[k] for k in ("query_id", "corpus_id", "score")} for q in kept]
    return queries_out, qrels_out


def existing_keys(s3, bucket, prefix):
    keys = set()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix + "/"):
        keys.update(o["Key"] for o in page.get("Contents", []))
    return keys


def resolve_bucket(session, stack):
    outputs = session.client("cloudformation").describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
    for o in outputs:
        if o["OutputKey"] == "DataBucketName":
            return o["OutputValue"]
    sys.exit(f"Stack {stack} has no DataBucketName output yet. Wait for it to finish creating, or pass --bucket.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stack", default="frw", help="stack to read the data bucket name from")
    ap.add_argument("--bucket", help="data bucket name (skips the stack lookup)")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--profile", help="AWS profile (default: your usual credentials)")
    ap.add_argument("--prefix", default=DEFAULT_PREFIX)
    ap.add_argument("--cache", default=".cache/dataset", help="where the download is kept (about 2 GB)")
    ap.add_argument("--force", action="store_true", help="rewrite pages that are already in the bucket")
    ap.add_argument("--dry-run", action="store_true", help="download and check, but write nothing to S3")
    args = ap.parse_args()

    import pyarrow.parquet as pq
    from huggingface_hub import hf_hub_download, list_repo_files

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    bucket = args.bucket or resolve_bucket(session, args.stack)
    s3 = session.client("s3")
    print(f"Target: s3://{bucket}/{args.prefix}/" + ("  (dry run: nothing will be written)" if args.dry_run else ""))

    files = [f for f in list_repo_files(REPO, repo_type="dataset") if f.endswith(".parquet")]
    by_config = {}
    for f in files:
        by_config.setdefault(f.split("/")[0], []).append(f)

    def local(path):
        return hf_hub_download(REPO, path, repo_type="dataset", cache_dir=args.cache)

    def put(key, body, content_type):
        if not args.dry_run:
            s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)

    have = set() if args.force else existing_keys(s3, bucket, args.prefix)
    wanted = set(DOCS)
    manifest, skipped = [], 0
    for path in sorted(by_config["corpus"]):
        pf = pq.ParquetFile(local(path))
        columns = ["corpus_id", "doc_id", "page_number_in_doc", "image", "markdown"]
        for batch in pf.iter_batches(batch_size=16, columns=columns):
            for row in batch.to_pylist():
                if row["doc_id"] not in wanted:
                    continue
                n = int(row["page_number_in_doc"])
                image_key = page_key(args.prefix, row["doc_id"], n, "pages")
                text_key = page_key(args.prefix, row["doc_id"], n, "text")
                if image_key in have and text_key in have:
                    skipped += 1
                else:
                    put(image_key, to_webp(row["image"]["bytes"]), "image/webp")
                    put(text_key, (row["markdown"] or "").encode("utf-8"), "text/markdown")
                manifest.append({"corpus_id": row["corpus_id"], "doc_id": row["doc_id"], "page_number": n,
                                 "key": image_key})
                if len(manifest) % 100 == 0:
                    print(f"  {len(manifest)} pages", file=sys.stderr)

    meta = pq.read_table(local(by_config["documents_metadata"][0])).to_pylist()
    queries = pq.read_table(local(by_config["queries"][0])).to_pylist()
    qrels = pq.read_table(local(by_config["qrels"][0])).to_pylist()
    queries_out, qrels_out = select_eval_rows(queries, qrels, {m["corpus_id"] for m in manifest})

    for name, rows in (("manifest.jsonl", manifest), ("queries.jsonl", queries_out),
                       ("qrels.jsonl", qrels_out), ("documents_metadata.jsonl", meta)):
        body = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows).encode()
        put(f"{args.prefix}/{name}", body, "application/x-ndjson")
        print(f"  {name}: {len(rows)} rows")

    print(f"Done: {len(manifest)} pages ({skipped} already present), {len(queries_out)} questions in all languages.")
    print("Next: scripts/start_ingestion.py" if not args.dry_run else "Dry run complete.")


if __name__ == "__main__":
    main()
