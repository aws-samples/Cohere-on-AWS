#!/usr/bin/env python3
"""Start ingestion of the demo pages once the model endpoints are running.

Reads the stack's outputs and parameters, checks that the endpoints the pipeline needs are InService, then starts the
ingestion state machine on the page manifest in the data bucket. Run it once after the stack has finished creating.

Usage: python scripts/start_ingestion.py [--profile NAME] [--stack frw] [--region us-east-1] [--dry-run]
       [--manifest-key corpus/vidore-v3-finance-en/manifest.jsonl]
Progress: open the state machine execution in the Step Functions console, or run scripts/ingest_report.py afterwards.
"""
import argparse
import json
import sys

import boto3

DEFAULT_MANIFEST = "corpus/vidore-v3-finance-en/manifest.jsonl"


def needed_endpoints(project, parse_mode):
    """Endpoints that ingestion calls: Embed Pro always, Parse only when ParseMode is live."""
    names = [f"{project}-embed-pro"]
    if parse_mode == "live":
        names.append(f"{project}-parse")
    return names


def not_ready(needed, statuses):
    """Names of needed endpoints that are missing or not InService, with their status."""
    return {n: statuses.get(n, "missing") for n in needed if statuses.get(n) != "InService"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="AWS profile (default: AWS_PROFILE or your default credentials)")
    ap.add_argument("--stack", default="frw")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--manifest-key", default=DEFAULT_MANIFEST)
    ap.add_argument("--dry-run", action="store_true", help="check everything but do not start")
    args = ap.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    stack = session.client("cloudformation").describe_stacks(StackName=args.stack)["Stacks"][0]
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
    params = {p["ParameterKey"]: p["ParameterValue"] for p in stack.get("Parameters", [])}
    project, parse_mode = params.get("ProjectName", "frw"), params.get("ParseMode", "off")

    bucket, arn = outputs.get("DataBucketName"), outputs.get("IngestStateMachineArn")
    if not bucket or not arn:
        sys.exit("The stack has no DataBucketName / IngestStateMachineArn output yet. Wait for it to finish creating.")

    s3 = session.client("s3")
    try:
        s3.head_object(Bucket=bucket, Key=args.manifest_key)
    except Exception:
        sys.exit(f"s3://{bucket}/{args.manifest_key} was not found. Copy the data bundle into the bucket first.")

    sm = session.client("sagemaker")
    statuses = {e["EndpointName"]: e["EndpointStatus"] for p in sm.get_paginator("list_endpoints").paginate() for e in p["Endpoints"]}
    pending = not_ready(needed_endpoints(project, parse_mode), statuses)
    if pending:
        sys.exit("Endpoints are not ready: " + ", ".join(f"{n} ({s})" for n, s in pending.items()) +
                 ". Wait until they show InService (about 15 to 20 minutes after the stack starts).")

    print(f"Ready: manifest s3://{bucket}/{args.manifest_key}, endpoints InService, ParseMode {parse_mode}.")
    if args.dry_run:
        print("Dry run: not starting.")
        return
    out = session.client("stepfunctions").start_execution(
        stateMachineArn=arn, input=json.dumps({"bucket": bucket, "manifest_key": args.manifest_key}))
    print("Started:", out["executionArn"])


if __name__ == "__main__":
    main()
