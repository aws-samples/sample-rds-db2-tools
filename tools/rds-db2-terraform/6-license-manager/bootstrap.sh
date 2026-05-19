#!/usr/bin/env bash
# bootstrap.sh — one-time License Manager init per AWS account + region.
#
# Run this BEFORE `terraform apply` the first time in a fresh account/region.
# Safe to re-run: each step is idempotent.
#
# Requirements:
#   - AWS_PROFILE exported (or pass --profile)
#   - AWS_REGION exported or edit REGION below

set -uo pipefail

REGION="${AWS_REGION:-us-gov-east-1}"
PROFILE_ARG=""
[ -n "${AWS_PROFILE:-}" ] && PROFILE_ARG="--profile ${AWS_PROFILE}"

echo "Bootstrapping License Manager in region ${REGION}..."

# 1. Service-linked role — no-op if it already exists
echo "  → Creating AWSServiceRoleForAWSLicenseManagerRole (if missing)..."
aws iam create-service-linked-role \
  --aws-service-name license-manager.amazonaws.com \
  --description "SLR for AWS License Manager" \
  ${PROFILE_ARG} 2>&1 | grep -qiE "EntityAlreadyExists|^$" \
  && echo "     already present" \
  || echo "     created"

# 2. Verify service settings respond (GovCloud may return empty settings until
#    first successful api call — we just confirm no permission errors)
echo "  → Checking License Manager service settings..."
if aws license-manager get-service-settings \
      --region "${REGION}" ${PROFILE_ARG} >/dev/null 2>&1; then
  echo "     service ready"
else
  echo "     WARNING: get-service-settings failed — check IAM perms for license-manager:*"
  exit 1
fi

echo "Done. You can now run: terraform apply"
