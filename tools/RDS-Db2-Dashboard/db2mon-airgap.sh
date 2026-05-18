#!/usr/bin/env bash

SCRIPT_MONITOR="db2monitor.sh"
SCRIPT_AIRGAP="db2mon-airgap.sh"
SCRIPT_DIAG="db2mon-diag.sh"
SCRIPT_CLEANUP="db2mon-cleanup.sh"

# =============================================================================
# $SCRIPT_AIRGAP  —  Populate Lambda bucket for air-gapped deployments
# =============================================================================
# MODE: download  — download all artifacts to ./db2mon-artifacts/ (needs internet)
# MODE: upload    — create bucket and upload from ./db2mon-artifacts/ (needs AWS)
# MODE: both      — download then upload in one shot (default)
#
# Usage:
#   ./$SCRIPT_AIRGAP --mode download --region us-west-1   # step 1: laptop with internet
#   ./$SCRIPT_AIRGAP --mode upload   --region us-west-1   # step 2: EC2 with AWS access
#   ./$SCRIPT_AIRGAP --mode both     --region us-west-1   # download + upload in one shot
#
# NOTE: --region is required for all modes. It determines the RDS SSL certificate
#       filename (e.g. us-west-1-bundle.pem) — mismatching regions cause upload
#       verification to fail.
# =============================================================================

if [ -z "$BASH_VERSION" ]; then exec bash "$0" "$@"; fi
set -eo pipefail
export AWS_PAGER=""

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'
log_info()    { echo -e "${BLUE}[   INFO]${NC} $(date '+%H:%M:%S') - $1" >&2; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $(date '+%H:%M:%S') - $1" >&2; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $(date '+%H:%M:%S') - $1" >&2; }
log_error()   { echo -e "${RED}[  ERROR]${NC} $(date '+%H:%M:%S') - $1" >&2; }

SOURCE_BUCKET="aws-blogs-artifacts-public"
SOURCE_PREFIX="artifacts/DBBLOG-3742"
ARTIFACTS_DIR="./db2mon-artifacts"
MODE=${MODE:-"both"}

# --- Curl-pipe detection ---
CURL_PIPE=false
if [ ! -f "${BASH_SOURCE[0]:-}" ]; then
  CURL_PIPE=true
fi

handle_curl_pipe_download() {
  log_info "Downloading ${SCRIPT_AIRGAP} ..."
  curl -fsSL "https://${SOURCE_BUCKET}.s3.amazonaws.com/${SOURCE_PREFIX}/${SCRIPT_AIRGAP}" \
    -o "./${SCRIPT_AIRGAP}" && chmod +x "./${SCRIPT_AIRGAP}"
  log_success "Saved: ./${SCRIPT_AIRGAP}"

  log_info "Downloading ${SCRIPT_MONITOR} ..."
  curl -fsSL "https://${SOURCE_BUCKET}.s3.amazonaws.com/${SOURCE_PREFIX}/db2mon-unified.sh" \
    -o "./${SCRIPT_MONITOR}" && chmod +x "./${SCRIPT_MONITOR}"
  log_success "Saved: ./${SCRIPT_MONITOR}"

  echo
  echo "============================================================="
  echo "  Downloaded. Steps for air-gapped deployment:"
  echo "============================================================="
  echo
  echo "STEP 1a — Download all artifacts on this machine (needs internet):"
  echo "   ./$SCRIPT_AIRGAP --mode download --region <your-region>"
  echo
  echo "STEP 1b — Copy $SCRIPT_AIRGAP, $SCRIPT_MONITOR and db2mon-artifacts/"
  echo "   to a machine with AWS access (private subnet). Then upload:"
  echo "   ./$SCRIPT_AIRGAP --mode upload --region <your-region>"
  echo
  echo "   Or if this machine also has AWS access, run both in one shot:"
  echo "   ./$SCRIPT_AIRGAP --mode both --region <your-region>"
  echo
  echo "STEP 2 — On the private subnet machine, download the monitor script and run:"
  echo "   aws s3 cp s3://lambda-functions-<account>-<region>/${SCRIPT_MONITOR} . && chmod +x ${SCRIPT_MONITOR}"
  echo "   ./$SCRIPT_MONITOR --region <your-region>"
  echo "============================================================="
}

# --- Argument parsing ---
while [[ $# -gt 0 ]]; do
  case $1 in
    --mode)    MODE="$2"; shift 2 ;;
    --region)  REGION="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 --mode download|upload|both --region REGION [--profile PROFILE]"
      echo "       --region is required: determines the RDS SSL certificate filename."
      exit 0 ;;
    *) log_error "Unknown option: $1"; exit 1 ;;
  esac
done

# --- Enforce mandatory --region ---
if [ -z "${REGION:-}" ]; then
  log_error "--region is required. Example: $0 --mode download --region us-west-1"
  exit 1
fi

set_credentials_airgap() {
  CREDS_FROM_METADATA=false

  # If an explicit non-default profile was requested, skip metadata entirely
  # and use the named profile so its credentials take precedence.
  if [ -n "$PROFILE" ] && [ "$PROFILE" != "default" ]; then
    PROFILE_ARG="--profile $PROFILE"
    log_info "Using explicit profile: $PROFILE (skipping instance metadata)"
    return 0
  fi  
  # If creds already exported in environment, use them as-is
  if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
    log_info "Using AWS credentials from environment variables"
    CREDS_FROM_METADATA=false
    return
  fi

  if curl -s --connect-timeout 1 http://127.0.0.1:1338/latest/meta-data/ >/dev/null 2>&1; then
    log_info "Detected AWS CloudShell environment"
    local token creds
    token=$(curl -sX PUT "http://127.0.0.1:1338/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
    creds=$(curl -s -H "Authorization: $token" "http://127.0.0.1:1338/latest/meta-data/container/security-credentials")
    export AWS_ACCESS_KEY_ID=$(echo "$creds"     | python3 -c "import sys,json; print(json.load(sys.stdin)['AccessKeyId'])")
    export AWS_SECRET_ACCESS_KEY=$(echo "$creds" | python3 -c "import sys,json; print(json.load(sys.stdin)['SecretAccessKey'])")
    export AWS_SESSION_TOKEN=$(echo "$creds"     | python3 -c "import sys,json; print(json.load(sys.stdin)['Token'])")
    CREDS_FROM_METADATA=true
    return
  fi
  if curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/ >/dev/null 2>&1; then
    log_info "Detected EC2 environment"
    local token role creds
    token=$(curl -sX PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
    role=$(curl -s -H "X-aws-ec2-metadata-token: $token" http://169.254.169.254/latest/meta-data/iam/security-credentials/)
    creds=$(curl -s -H "X-aws-ec2-metadata-token: $token" "http://169.254.169.254/latest/meta-data/iam/security-credentials/$role")
    export AWS_ACCESS_KEY_ID=$(echo "$creds"     | python3 -c "import sys,json; print(json.load(sys.stdin)['AccessKeyId'])")
    export AWS_SECRET_ACCESS_KEY=$(echo "$creds" | python3 -c "import sys,json; print(json.load(sys.stdin)['SecretAccessKey'])")
    export AWS_SESSION_TOKEN=$(echo "$creds"     | python3 -c "import sys,json; print(json.load(sys.stdin)['Token'])")
    CREDS_FROM_METADATA=true
    return
  fi
  CREDS_FROM_METADATA=false
}

setup_aws() {
  PROFILE=${PROFILE:-"default"}
  CREDS_FROM_METADATA=false
  set_credentials_airgap

  if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
    PROFILE_ARG=""
  else
    PROFILE_ARG="--profile $PROFILE"
  fi

  if [ "$CREDS_FROM_METADATA" = "false" ]; then
    if ! aws sts get-caller-identity $PROFILE_ARG --region "$REGION" >/dev/null 2>&1; then
      log_error "AWS credentials invalid. Run 'aws configure' or set AWS_ACCESS_KEY_ID/SECRET."
      exit 1
    fi
  fi
  ACCOUNT_ID=$(aws sts get-caller-identity $PROFILE_ARG --region "$REGION" --query Account --output text 2>/dev/null)
  if [ -z "$ACCOUNT_ID" ] || [ "$ACCOUNT_ID" = "None" ]; then
    log_error "Could not determine AWS Account ID. Check credentials."
    exit 1
  fi
  TARGET_BUCKET="lambda-functions-${ACCOUNT_ID}-${REGION}"
  log_success "AWS ready | Account: $ACCOUNT_ID | Region: $REGION"
}

# --- Region needed for PEM filename even in download mode ---
resolve_region() {
  if [ -z "${REGION:-}" ]; then
    # Try AWS config if available, otherwise prompt
    REGION=$(aws configure get region 2>/dev/null || true)
    if [ -z "$REGION" ]; then
      read -p "Enter AWS region (needed for SSL certificate filename, e.g. ap-southeast-2): " REGION
    fi
  fi
}

# =============================================================================
# STEP 1 — Download all artifacts to ARTIFACTS_DIR
# =============================================================================
do_download() {
  resolve_region
  mkdir -p "${ARTIFACTS_DIR}/Lambda/DB2Mon" "${ARTIFACTS_DIR}/CFN" "${ARTIFACTS_DIR}/ssl" "${ARTIFACTS_DIR}/scripts"

  log_info "Downloading jq static binary ..."
  curl -fsSL "https://github.com/jqlang/jq/releases/download/jq-1.7.1/jq-linux-amd64" \
    -o "${ARTIFACTS_DIR}/scripts/jq"
  log_success "Downloaded: scripts/jq"

  log_info "Downloading Lambda ZIPs..."
  for zip in DB2Mon-Code.zip DB2Mon-Layer.zip; do
    curl -fsSL "https://${SOURCE_BUCKET}.s3.amazonaws.com/${SOURCE_PREFIX}/Lambda/DB2Mon/${zip}" \
      -o "${ARTIFACTS_DIR}/Lambda/DB2Mon/${zip}"
    log_success "Downloaded: Lambda/DB2Mon/${zip}"
  done

  log_info "Downloading CFN templates..."
  for cfn in rds-db2monitor-main.yml rds-db2-dashboard.yml create-db2mon-eventbridge.yml; do
    curl -fsSL "https://${SOURCE_BUCKET}.s3.amazonaws.com/${SOURCE_PREFIX}/CFN/${cfn}" \
      -o "${ARTIFACTS_DIR}/CFN/${cfn}"
    log_success "Downloaded: CFN/${cfn}"
  done

  log_info "Downloading companion scripts..."
  for script in "$SCRIPT_DIAG" "$SCRIPT_CLEANUP" "README.md"; do
    curl -fsSL "https://${SOURCE_BUCKET}.s3.amazonaws.com/${SOURCE_PREFIX}/${script}" \
      -o "${ARTIFACTS_DIR}/scripts/${script}"
    log_success "Downloaded: scripts/${script}"
  done

  log_info "Downloading RDS SSL certificate for region: $REGION ..."
  local pem_file="${REGION}-bundle.pem"
  if ! curl -fsSL "https://truststore.pki.rds.amazonaws.com/${REGION}/${pem_file}" \
       -o "${ARTIFACTS_DIR}/ssl/${pem_file}"; then
    log_error "Failed to download SSL certificate."
    return 1
  fi
  log_success "Downloaded: ssl/${pem_file}"

  echo
  log_success "All artifacts saved to: ${ARTIFACTS_DIR}/"
  echo
  echo "  Next: copy" 
  echo "    ${SCRIPT_MONITOR}, ${SCRIPT_AIRGAP}, ${SCRIPT_DIAG}, ${SCRIPT_CLEANUP} and"
  echo "    directory ${ARTIFACTS_DIR}/ to your system (private subnets) that has aws command configured. Run :"
  echo "    ./$SCRIPT_AIRGAP --mode upload --region $REGION"
  echo
}

# =============================================================================
# STEP 2 — Create bucket and upload from ARTIFACTS_DIR
# =============================================================================
do_upload() {
  setup_aws

  if [ ! -d "$ARTIFACTS_DIR" ]; then
    log_error "Artifacts directory not found: $ARTIFACTS_DIR"
    log_error "Run './$SCRIPT_AIRGAP --mode download' first, then copy the directory here."
    exit 1
  fi

  # --- Create target bucket if needed ---
  if ! aws s3api head-bucket --bucket "$TARGET_BUCKET" --region "$REGION" $PROFILE_ARG 2>/dev/null; then
    log_info "Creating bucket: $TARGET_BUCKET"
    if [ "$REGION" = "us-east-1" ]; then
      aws s3api create-bucket --bucket "$TARGET_BUCKET" --region "$REGION" $PROFILE_ARG >/dev/null
    else
      aws s3api create-bucket --bucket "$TARGET_BUCKET" --region "$REGION" $PROFILE_ARG \
        --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
    fi
    aws s3api put-bucket-versioning --bucket "$TARGET_BUCKET" \
      --versioning-configuration Status=Enabled --region "$REGION" $PROFILE_ARG >/dev/null
    log_success "Bucket created: $TARGET_BUCKET"
  else
    log_info "Bucket already exists: $TARGET_BUCKET"
  fi

  # --- Upload all artifacts preserving directory structure ---
  log_info "Uploading artifacts to s3://${TARGET_BUCKET}/ ..."
  aws s3 sync "${ARTIFACTS_DIR}/" "s3://${TARGET_BUCKET}/" \
    --region "$REGION" $PROFILE_ARG --quiet
  log_success "Upload complete"

  # --- Copy scripts to bucket so private subnet machines can pull via S3 GW ---
  log_info "Copying scripts to s3://${TARGET_BUCKET}/ ..."
  aws s3 cp "$(realpath "$0")" "s3://${TARGET_BUCKET}/${SCRIPT_AIRGAP}" \
    --region "$REGION" $PROFILE_ARG --quiet
  aws s3 cp "$(dirname "$(realpath "$0")")/"${SCRIPT_MONITOR} "s3://${TARGET_BUCKET}/${SCRIPT_MONITOR}" \
    --region "$REGION" $PROFILE_ARG --quiet
  for script in "$SCRIPT_DIAG" "$SCRIPT_CLEANUP"; do
    aws s3 cp "${ARTIFACTS_DIR}/scripts/${script}" "s3://${TARGET_BUCKET}/${script}" \
      --region "$REGION" $PROFILE_ARG --quiet
  done
  aws s3 cp "${ARTIFACTS_DIR}/scripts/README.md" "s3://${TARGET_BUCKET}/README.md" \
    --region "$REGION" $PROFILE_ARG --quiet
  log_success "Scripts uploaded to s3://${TARGET_BUCKET}/"

  # --- Verify ---
  log_info "Verifying uploads..."
  local missing=false
  for key in \
    scripts/jq \
    Lambda/DB2Mon/DB2Mon-Code.zip \
    Lambda/DB2Mon/DB2Mon-Layer.zip \
    CFN/rds-db2monitor-main.yml \
    CFN/rds-db2-dashboard.yml \
    CFN/create-db2mon-eventbridge.yml \
    "ssl/${REGION}-bundle.pem" \
    "$SCRIPT_DIAG" \
    "$SCRIPT_CLEANUP" \
    "README.md"; do
    if aws s3api head-object --bucket "$TARGET_BUCKET" --key "$key" \
       --region "$REGION" $PROFILE_ARG &>/dev/null; then
      log_success "OK: s3://${TARGET_BUCKET}/${key}"
    else
      log_warning "Missing: s3://${TARGET_BUCKET}/${key}"
      missing=true
    fi
  done
  [ "$missing" = "true" ] && log_warning "Some artifacts missing — check errors above."

  echo
  echo "============================================================="
  echo "  Bucket ready: s3://${TARGET_BUCKET}"
  echo "  SSL cert    : s3://${TARGET_BUCKET}/ssl/${REGION}-bundle.pem"
  echo
  echo "  STEP 1b (continued) — On the private subnet machine, download the monitor script:"
  echo "    aws s3 cp s3://${TARGET_BUCKET}/${SCRIPT_MONITOR} . && chmod +x ${SCRIPT_MONITOR}"
  echo
  echo "  STEP 2 — Deploy DB2 monitoring (airgap mode):"
  echo "    BUCKET=${TARGET_BUCKET} REGION=${REGION} ./$SCRIPT_MONITOR"
  echo "    — or —"
  echo "    export BUCKET=${TARGET_BUCKET} REGION=${REGION}"
  echo "    ./$SCRIPT_MONITOR"
  echo
  echo "  Optional: encrypt Lambda and Secrets Manager with a customer-managed KMS key:"
  echo "    BUCKET=${TARGET_BUCKET} REGION=${REGION} KMS_KEY_ARN=<key-arn> ./$SCRIPT_MONITOR"
  echo "    — or —"
  echo "    export KMS_KEY_ARN=<key-arn>   # find in AWS Console > KMS > Customer managed keys"
  echo "    ./$SCRIPT_MONITOR"
  echo "============================================================="
}

# =============================================================================
# Main
# =============================================================================
if $CURL_PIPE; then
  handle_curl_pipe_download
  exit 0
fi

case "$MODE" in
  download) do_download ;;
  upload)   do_upload ;;
  both)     do_download; do_upload ;;
  *) log_error "Unknown mode: $MODE. Use download, upload, or both."; exit 1 ;;
esac
