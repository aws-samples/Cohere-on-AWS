#!/usr/bin/env python3
"""Report what an ingest run did, from the ingest-state table: how many pages were routed to Parse and what happened.

For every page the pipeline records its route (route_reason) and where the embedded text came from (text_source).
This script counts them so a run is auditable:

  routed               pages the router sent to Parse (low_text or table_heavy)
  parse output used    of those, pages that got Parse output: from the shipped cache (parse_cache) or by calling the
                       Parse endpoint now (parse_live)
  fell back            routed pages that kept the bundle text instead, because Parse failed (..._parse_failed) or was
                       not available (..._no_parse). These pages are marked done, so a "clean" run needs them retried.

Read-only by default. `--reset-fallbacks --apply` deletes the state rows of fallback pages (and failed pages) so that
starting the ingest again retries only those pages; finished pages are skipped.

Usage:
  python scripts/ingest_report.py [--profile NAME] [--stack frw] [--region us-east-1]
      [--out report.json] [--reset-fallbacks --apply]
The state table, data bucket and manifest come from the stack; --state-table, --bucket and --manifest-key override them.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

DEFAULT_MANIFEST = "corpus/vidore-v3-finance-en/manifest.jsonl"
ROUTED = ("low_text", "table_heavy")
FALLBACK_SUFFIXES = ("_parse_failed", "_no_parse", "_parse_empty")


def base_reason(reason):
    for suffix in FALLBACK_SUFFIXES:
        if reason.endswith(suffix):
            return reason[: -len(suffix)]
    return reason


def is_blank(item):
    """No text at all from the bundle or from Parse: nothing to retry, the page is recorded without a vector."""
    return int(item.get("chars", 0) or 0) == 0 and item.get("text_source") == "none"


def is_fallback(item):
    """Routed page that kept bundle text because Parse failed or was unavailable. A blank page is not a fallback."""
    return (item.get("status") == "done" and str(item.get("route_reason", "")).endswith(FALLBACK_SUFFIXES)
            and not is_blank(item))


def summarise(items, expected_ids=None):
    """items: state-table rows (dicts). expected_ids: ids the manifest lists, to detect pages that never ran."""
    items = list(items)
    done = [i for i in items if i.get("status") == "done"]
    failed = [i for i in items if i.get("status") == "failed"]
    routed = [i for i in done if base_reason(str(i.get("route_reason", ""))) in ROUTED]
    used_cache = [i for i in routed if i.get("text_source") == "parse_cache"]
    used_live = [i for i in routed if i.get("text_source") == "parse_live"]
    fell_back = [i for i in routed if is_fallback(i)]
    blank_routed = [i for i in routed if is_blank(i)]
    times = [int(i["updated_at"]) for i in items if i.get("updated_at") is not None]
    out = {
        "pages_recorded": len(items),
        "done": len(done),
        "failed": len(failed),
        "by_route_reason": dict(Counter(str(i.get("route_reason", "?")) for i in done)),
        "by_text_source": dict(Counter(str(i.get("text_source", "?")) for i in done)),
        "routed_to_parse": len(routed),
        "routed_by_reason": dict(Counter(base_reason(str(i["route_reason"])) for i in routed)),
        "parse_output_used": len(used_cache) + len(used_live),
        "from_cache": len(used_cache),
        "parsed_live_this_run": len(used_live),
        "routed_but_fell_back": len(fell_back),
        "routed_but_blank": len(blank_routed),
        "fallback_page_ids": sorted(str(i["page_id"]) for i in fell_back),
        "failed_page_ids": sorted(str(i["page_id"]) for i in failed),
        "blank_pages": sum(1 for i in done if is_blank(i)),
        "share_with_parse_output": round((len(used_cache) + len(used_live)) / len(done), 4) if done else 0.0,
        "first_update": min(times) if times else None,
        "last_update": max(times) if times else None,
        "duration_s": (max(times) - min(times)) if times else None,
    }
    if expected_ids is not None:
        seen = {str(i["page_id"]) for i in done}
        missing = sorted(set(map(str, expected_ids)) - seen)
        out["manifest_pages"] = len(set(map(str, expected_ids)))
        out["not_done"] = len(missing)
        out["not_done_ids"] = missing[:50]
    out["clean"] = (not failed and not fell_back and out.get("not_done", 0) == 0 and bool(done))
    return out


def scan_state(table):
    items, resp = [], table.scan()
    while True:
        items += [{k: (int(v) if hasattr(v, "as_tuple") and v == int(v) else v) for k, v in row.items()}
                  for row in resp["Items"]]
        if "LastEvaluatedKey" not in resp:
            return items
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="AWS profile (default: AWS_PROFILE or your default credentials)")
    ap.add_argument("--stack", default="frw", help="stack to read the table, bucket and manifest from")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--state-table", default="", help="default: <ProjectName>-ingest-state from the stack")
    ap.add_argument("--bucket", default="", help="default: the stack's DataBucketName output")
    ap.add_argument("--manifest-key", default=DEFAULT_MANIFEST)
    ap.add_argument("--out", default="")
    ap.add_argument("--reset-fallbacks", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        if not (args.state_table and args.bucket):
            stack = session.client("cloudformation").describe_stacks(StackName=args.stack)["Stacks"][0]
            params = {p["ParameterKey"]: p["ParameterValue"] for p in stack.get("Parameters", [])}
            outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
            args.state_table = args.state_table or f"{params.get('ProjectName', 'frw')}-ingest-state"
            args.bucket = args.bucket or outputs.get("DataBucketName", "")
        table = session.resource("dynamodb").Table(args.state_table)
        items = scan_state(table)
        expected = None
        if args.bucket and args.manifest_key:
            body = session.client("s3").get_object(Bucket=args.bucket, Key=args.manifest_key)["Body"].read().decode()
            expected = [json.loads(x)["corpus_id"] for x in body.splitlines() if x.strip()]
    except (BotoCoreError, ClientError) as exc:
        sys.exit(f"Cannot read from AWS: {exc}\nPass --profile NAME (or set AWS_PROFILE), and if it is an SSO profile, "
                 "run `aws sso login --profile NAME` first.")
    print(f"state table {args.state_table}" + (f", manifest s3://{args.bucket}/{args.manifest_key}" if args.bucket else ""))
    report = summarise(items, expected)

    print(f"pages recorded {report['pages_recorded']}  done {report['done']}  failed {report['failed']}"
          + (f"  manifest {report['manifest_pages']}  not done {report['not_done']}" if expected is not None else ""))
    print(f"routed to Parse: {report['routed_to_parse']}  {report['routed_by_reason']}")
    print(f"  Parse output used: {report['parse_output_used']}  (shipped cache {report['from_cache']}, "
          f"called Parse this run {report['parsed_live_this_run']})")
    print(f"  routed but kept bundle text (Parse failed or unavailable): {report['routed_but_fell_back']}")
    print(f"  routed but blank (Parse returned nothing, no vector): {report['routed_but_blank']}")
    print(f"text source: {report['by_text_source']}")
    print(f"route reason: {report['by_route_reason']}")
    print(f"blank pages (no text, no vector): {report['blank_pages']}")
    if report["duration_s"] is not None:
        print(f"first to last page update: {report['duration_s'] / 60:.1f} minutes")
    print("CLEAN RUN" if report["clean"] else "NOT CLEAN: see failed, fallback or not-done pages above")

    if args.reset_fallbacks:
        ids = report["fallback_page_ids"] + report["failed_page_ids"]
        print(f"\n{'APPLY' if args.apply else 'DRY RUN'}: {len(ids)} state rows to delete so the next run retries them")
        if args.apply:
            with table.batch_writer() as batch:
                for page_id in ids:
                    batch.delete_item(Key={"page_id": page_id})
            print("  deleted; start the ingest again with the same manifest")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
