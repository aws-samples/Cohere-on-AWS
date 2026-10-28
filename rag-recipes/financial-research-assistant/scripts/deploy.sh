#!/usr/bin/env bash
# Package and deploy the stack from a clone of this repository.
#
# Usage: scripts/deploy.sh <aws-profile> [extra --parameter-overrides Key=Value ...]
#   ARTIFACT_BUCKET  S3 bucket that "aws cloudformation package" uploads the Lambda code, nested templates and web app
#                    to (default: <stack>-artifacts-<account ID>-<region>, created on first run if missing)
#   STACK            stack name (default: frw)
#   REGION           AWS Region (default: us-east-1)
#   LINT             cfn-lint command (default: cfn-lint; install with "pip install cfn-lint")
# Example: scripts/deploy.sh my-profile AdminEmail=you@example.com
set -euo pipefail

PROFILE="${1:?usage: deploy.sh <aws-profile> [Key=Value ...]}"
shift || true
REGION="${REGION:-us-east-1}"
STACK="${STACK:-frw}"
LINT="${LINT:-cfn-lint}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGED="$(mktemp -t frw-packaged.XXXXXX).yaml"

# Republish the web app whenever any of its files change.
SITE_VERSION="$(cd "$ROOT/src/ui" && find . -type f ! -name '*.pyc' -print0 | sort -z | xargs -0 shasum | shasum | cut -c1-12)"

echo "== lint"
# W3002: local paths are rewritten by "aws cloudformation package", so the warning is expected.
"$LINT" -i W3002 -- "$ROOT"/template/main.yaml "$ROOT"/template/stacks/*.yaml

ACCOUNT="$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)"
ARTIFACT_BUCKET="${ARTIFACT_BUCKET:-$STACK-artifacts-$ACCOUNT-$REGION}"
if ! aws s3api head-bucket --profile "$PROFILE" --region "$REGION" --bucket "$ARTIFACT_BUCKET" \
    --expected-bucket-owner "$ACCOUNT" >/dev/null 2>&1; then
  echo "== creating artifact bucket $ARTIFACT_BUCKET"
  if [[ "$REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --profile "$PROFILE" --region "$REGION" --bucket "$ARTIFACT_BUCKET" >/dev/null
  else
    aws s3api create-bucket --profile "$PROFILE" --region "$REGION" --bucket "$ARTIFACT_BUCKET" \
      --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
  fi
  aws s3api put-public-access-block --profile "$PROFILE" --region "$REGION" --bucket "$ARTIFACT_BUCKET" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-tagging --profile "$PROFILE" --region "$REGION" --bucket "$ARTIFACT_BUCKET" \
    --tagging "TagSet=[{Key=project,Value=$STACK}]"
fi

echo "== package"
aws cloudformation package \
  --profile "$PROFILE" --region "$REGION" \
  --template-file "$ROOT/template/main.yaml" \
  --s3-bucket "$ARTIFACT_BUCKET" --s3-prefix cfn \
  --output-template-file "$PACKAGED"

STATUS="$(aws cloudformation describe-stacks --profile "$PROFILE" --region "$REGION" --stack-name "$STACK" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null || true)"
if [[ "$STATUS" == "ROLLBACK_COMPLETE" ]]; then
  echo "== removing failed $STACK (ROLLBACK_COMPLETE cannot be updated)"
  aws cloudformation delete-stack --profile "$PROFILE" --region "$REGION" --stack-name "$STACK"
  aws cloudformation wait stack-delete-complete --profile "$PROFILE" --region "$REGION" --stack-name "$STACK"
fi

echo "== deploy $STACK"
aws cloudformation deploy \
  --profile "$PROFILE" --region "$REGION" \
  --template-file "$PACKAGED" \
  --stack-name "$STACK" \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --tags project="$STACK" \
  --no-fail-on-empty-changeset \
  --parameter-overrides SiteVersion="$SITE_VERSION" "$@"
