#!/usr/bin/env python3
"""Check an AWS account before deploying the demo. Read-only apart from one tiny Claude call (a few tokens).

Checks:
  1. Region        the demo's Cohere model packages are mapped for us-east-1 only.
  2. GPU quota     SageMaker "ml.g5.xlarge for endpoint usage" must cover the endpoints the stack creates:
                   2 by default (Embed Pro, Rerank Fast), 3 with --parse (live Parse endpoint).
  3. Bedrock       a one-token call to the Claude inference profile, to catch missing model access.
  4. Endpoints     lists SageMaker endpoints that already exist (they count against the quota and keep billing).

Not checked: AWS Marketplace subscriptions to the Cohere model packages. A stack launched without them fails when it
creates the models; subscribe to each listing first (package ARNs in template/stacks/models.yaml).

Usage: python scripts/preflight.py [--profile NAME] [--parse] [--region us-east-1] [--claude-profile us.anthropic....]
Exit status is 1 when a check fails.
"""
import argparse
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError

SUPPORTED_REGION = "us-east-1"
DEFAULT_CLAUDE = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
QUOTA_NAME = "ml.g5.xlarge for endpoint usage"


def endpoints_needed(parse):
    return 3 if parse else 2


def find_quota(quotas, name=QUOTA_NAME):
    """Value of the named quota from a list of {'QuotaName', 'Value'} dicts, or None when it is not listed."""
    for q in quotas:
        if q.get("QuotaName", "").lower() == name.lower():
            return q.get("Value")
    return None


def judge_quota(value, needed, existing):
    """('ok'|'fail'|'warn', message). `existing` endpoints already use some of the quota."""
    if value is None:
        return "warn", f"quota '{QUOTA_NAME}' not listed; check Service Quotas in the console (need {needed} free)"
    free = value - existing
    if free >= needed:
        return "ok", f"{QUOTA_NAME}: {value:g} (need {needed}, {existing} endpoint(s) already exist)"
    return "fail", (f"{QUOTA_NAME}: {value:g}, {existing} endpoint(s) already exist, so {max(free, 0):g} free but {needed} needed. "
                    "Request an increase in Service Quotas (new accounts often start at 0).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="AWS profile (default: AWS_PROFILE or your default credentials)")
    ap.add_argument("--region", default=SUPPORTED_REGION)
    ap.add_argument("--parse", action="store_true", help="also need the live Parse endpoint (a third GPU instance)")
    ap.add_argument("--claude-profile", default=DEFAULT_CLAUDE)
    args = ap.parse_args()

    failed = False

    def report(status, message):
        nonlocal failed
        failed = failed or status == "fail"
        print(f"[{ {'ok': ' ok ', 'fail': 'FAIL', 'warn': 'warn'}[status] }] {message}")

    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        ident = session.client("sts").get_caller_identity()
        print(f"Account {ident['Account']}, region {args.region}")
    except ClientError as exc:
        print(f"Cannot reach AWS: {exc.response['Error']['Code']}. Check your credentials.")
        return 1
    except BotoCoreError as exc:
        print(f"Cannot reach AWS: {exc}. Pass --profile NAME (or set AWS_PROFILE), and if it is an SSO profile, "
              "run `aws sso login --profile NAME` first.")
        return 1

    if args.region == SUPPORTED_REGION:
        report("ok", f"region {args.region}")
    else:
        report("fail", f"region {args.region}: the template only has Cohere model package ARNs for {SUPPORTED_REGION}")

    sm = session.client("sagemaker")
    # Failed endpoints hold no instance, so they do not count against the quota (and they do not bill).
    existing = [e["EndpointName"] for p in sm.get_paginator("list_endpoints").paginate() for e in p["Endpoints"]
                if e["EndpointStatus"] != "Failed"]
    try:
        quotas = [q for p in session.client("service-quotas").get_paginator("list_service_quotas").paginate(ServiceCode="sagemaker")
                  for q in p["Quotas"]]
        report(*judge_quota(find_quota(quotas), endpoints_needed(args.parse), len(existing)))
    except ClientError as exc:
        report("warn", f"could not read Service Quotas ({exc.response['Error']['Code']}); check them in the console")

    try:
        session.client("bedrock-runtime").converse(
            modelId=args.claude_profile, messages=[{"role": "user", "content": [{"text": "Reply with the word ok."}]}],
            inferenceConfig={"maxTokens": 5})
        report("ok", f"Bedrock: {args.claude_profile} answered")
    except ClientError as exc:
        report("fail", f"Bedrock: {args.claude_profile}: {exc.response['Error']['Code']}. Request model access in the Bedrock console.")

    if existing:
        report("warn", "SageMaker endpoints already exist (they bill while they exist): " + ", ".join(existing))
    else:
        report("ok", "no SageMaker endpoints exist yet")
    print("Not checked: AWS Marketplace subscriptions to the Cohere model packages (see template/stacks/models.yaml).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
