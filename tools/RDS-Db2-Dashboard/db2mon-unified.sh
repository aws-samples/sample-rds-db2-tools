#!/usr/bin/env bash
# =============================================================================
# DB2 Dashboard Deployment Script
# =============================================================================
# curl -fsSL https://aws-blogs-artifacts-public.s3.us-east-1.amazonaws.com/artifacts/DBBLOG-3742/db2mon-unified.sh | bash
# =============================================================================

if [ -z "$BASH_VERSION" ]; then
  exec bash "$0" "$@"
fi

PLATFORM="unknown"
case "$(uname -s)" in
  Darwin*) PLATFORM="macos" ;;
  Linux*)  PLATFORM="linux" ;;
esac

set -eo pipefail
export AWS_PAGER=""

# --- Configuration ---
PROFILE=${PROFILE:-""}
CREDS_FROM_METADATA=false
REGION=${REGION:-""}
SUBNET_IDS=${SUBNET_IDS:-""}
KMS_KEY_ARN=${KMS_KEY_ARN:-""}
VERBOSE=${VERBOSE:-false}
CHECK_PERMISSIONS=${CHECK_PERMISSIONS:-false}
INTERACTIVE_MODE=${INTERACTIVE_MODE:-true}
DB_INSTANCE_ID=${DB_INSTANCE_ID:-""}
DBNAME=${DBNAME:-""}
TAG=${TAG:-""}
PASSWORD=${PASSWORD:-""}
SCRIPT_PATH=${SCRIPT_PATH:-"$0"}

SOURCE_BUCKET="aws-blogs-artifacts-public"
SOURCE_PREFIX="artifacts/DBBLOG-3742"
SOURCE_URL="https://${SOURCE_BUCKET}.s3.amazonaws.com/${SOURCE_PREFIX}"

SCRIPT_MONITOR="db2monitor.sh"
SCRIPT_AIRGAP="db2mon-airgap.sh"
SCRIPT_DIAG="db2mon-diag.sh"
SCRIPT_CLEANUP="db2mon-cleanup.sh"
SCRIPT_ENV="db2monitor.env"

# BUCKET: unset = online mode (download from public S3 via curl)
#         set   = airgap mode (pull from private bucket via aws s3 cp)
BUCKET=${BUCKET:-""}
SKIP_SG_ENDPOINT_ATTACH=${SKIP_SG_ENDPOINT_ATTACH:-false}

DB2_SYSTEM_NS="RDS-DB2-MON"

# --- Colors ---
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

start_time=$(date +%s)

log_info()    { echo -e "${BLUE}[   INFO]${NC} $(date '+%H:%M:%S') - $1" >&2; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $(date '+%H:%M:%S') - $1" >&2; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $(date '+%H:%M:%S') - $1" >&2; }
log_error()   { echo -e "${RED}[  ERROR]${NC} $(date '+%H:%M:%S') - $1" >&2; }
log_debug()   { [[ "$VERBOSE" == "true" ]] && echo -e "${CYAN}[ DEBUG ]${NC} $(date '+%H:%M:%S') - $1" >&2; }

report_elapsed_time() {
  local elapsed=$(( $(date +%s) - start_time ))
  echo "Elapsed: $(( elapsed/3600 ))h $(( (elapsed%3600)/60 ))m $(( elapsed%60 ))s"
}

# --- Input Helper ---
getInput() {
  local var_name=$1 default_value=$2 prompt_message=$3
  local var_value="${!var_name:-}"
  if [ -n "$var_value" ] && [ "$var_value" != "null" ]; then
    echo "$var_value"
  elif [[ "$INTERACTIVE_MODE" == "false" ]]; then
    [ -n "$default_value" ] && echo "$default_value" || { log_error "Required: $var_name"; exit 1; }
  else
    if [ -z "$default_value" ]; then
      read -p "$prompt_message: " user_input; echo "${user_input}"
    else
      read -p "$prompt_message (default $default_value): " user_input; echo "${user_input:-$default_value}"
    fi
  fi
}

# --- Curl-pipe detection ---
CURL_PIPE=false
if [ ! -f "${BASH_SOURCE[0]:-}" ]; then
  CURL_PIPE=true
fi

handle_curl_pipe_download() {
  SCRIPT_PATH="./${SCRIPT_MONITOR}"
  local script_path="$SCRIPT_PATH"
  local airgap_path="./${SCRIPT_AIRGAP}"
  local diag_path="./${SCRIPT_DIAG}"
  local cleanup_path="./${SCRIPT_CLEANUP}"
  local env_path="./${SCRIPT_ENV}"

  log_info "Downloading ${SCRIPT_MONITOR} ..."
  curl -fsSL "${SOURCE_URL}/db2mon-unified.sh" -o "$script_path" && chmod +x "$script_path"
  log_success "Saved: $script_path"

  log_info "Downloading ${SCRIPT_AIRGAP} ..."
  curl -fsSL "${SOURCE_URL}/${SCRIPT_AIRGAP}" -o "$airgap_path" && chmod +x "$airgap_path"
  log_success "Saved: $airgap_path"

  log_info "Downloading ${SCRIPT_DIAG} ..."
  curl -fsSL "${SOURCE_URL}/${SCRIPT_DIAG}" -o "$diag_path" && chmod +x "$diag_path"
  log_success "Saved: $diag_path"

  log_info "Downloading ${SCRIPT_CLEANUP} ..."
  curl -fsSL "${SOURCE_URL}/${SCRIPT_CLEANUP}" -o "$cleanup_path" && chmod +x "$cleanup_path"
  log_success "Saved: $cleanup_path"

  log_info "Downloading README.md ..."
  curl -fsSL "${SOURCE_URL}/README.md" -o "./README.md"
  log_success "Saved: ./README.md"

  # Generate sample env file
  cat > "$env_path" << 'ENVEOF'
# =============================================================================
# db2monitor.env - Configuration for db2monitor.sh
# Edit the values below, then run: source db2monitor.env && ./db2monitor.sh
# =============================================================================

# AWS profile (run 'aws configure list-profiles' to see available profiles)
export PROFILE="default"

# AWS region (e.g. us-east-1, eu-west-1)
export REGION="us-east-1"

# RDS DB2 instance identifier
export DB_INSTANCE_ID=""

# Database name to monitor
export DBNAME="DB2DB"

# A single word tag to identify this deployment (auto-generated if blank)
export TAG=""

# Master password — leave blank if RDS manages the password automatically
export PASSWORD=""

# Optional: override subnet selection (space-separated subnet IDs)
# export SUBNET_IDS="subnet-aaa subnet-bbb"

# AIRGAP mode: set BUCKET to your private bucket name (leave blank for online mode)
# export BUCKET="lambda-functions-<account>-<region>"
ENVEOF
  log_success "Sample config saved: $env_path"

  echo
  echo "============================================================="
  echo "  Downloaded. Choose your deployment mode:"
  echo "============================================================="
  echo
  echo "ONLINE mode (CloudShell / EC2 with internet access):"
  echo "   $script_path --region <your-region>"
  echo
  echo "AIRGAP mode (private subnet, no internet):"
  echo "   Step 1: On any machine WITH internet, download all artifacts:"
  echo "     $airgap_path --mode download --region <your-region>"
  echo "                  # saves to ./db2mon-artifacts/"
  echo
  echo "   Step 2: On a machine WITH AWS configured, upload to S3:"
  echo "     $airgap_path --mode upload --region <your-region>"
  echo "                  # creates bucket + uploads artifacts"
  echo
  echo "   Step 3: On the private subnet machine, pull the script and run:"
  echo "     aws s3 cp s3://lambda-functions-<account>-<region>/${SCRIPT_MONITOR} . && chmod +x ${SCRIPT_MONITOR}"
  echo "     BUCKET=lambda-functions-<account>-<region> $script_path --region <your-region>"
  echo
  echo "Optional: diagnose VPC/endpoint/SG issues before deploying:"
  echo "   ./${SCRIPT_DIAG} --region <your-region>"
  echo
  echo "Optional: remove all deployed resources:"
  echo "   ./${SCRIPT_CLEANUP} --region <your-region>"
  echo
  echo "Optional: pre-configure before deploying:"
  echo "   vi $env_path && source $env_path"
  echo "============================================================="
}

# =============================================================================
# Credentials — probe CloudShell, EC2 IMDSv2, then fall back to env vars/profile
# Order: CloudShell metadata → EC2 IMDSv2 → env vars → named profile
# =============================================================================
set_credentials() {
  CREDS_FROM_METADATA=false

  # Priority 1: exported env var credentials — use immediately, skip everything
  if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
    PROFILE_ARG=""
    log_info "Using exported AWS_ACCESS_KEY_ID credentials"
    return 0
  fi

  # Priority 2: explicit profile (any value, including 'default') — validate with sts, exit if fails
  if [ -n "$PROFILE" ]; then
    PROFILE_ARG="--profile $PROFILE"
    log_info "Using explicit profile: $PROFILE (skipping instance metadata)"
    return 0
  fi

  # Priority 3a: CloudShell IMDS (127.0.0.1:1338)
  if curl -s --connect-timeout 1 http://127.0.0.1:1338/latest/meta-data/ >/dev/null 2>&1; then
    log_info "Detected AWS CloudShell environment"
    local token creds
    token=$(curl -sX PUT "http://127.0.0.1:1338/latest/api/token" \
      -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" --max-time 3)
    creds=$(curl -s -H "Authorization: $token" \
      "http://127.0.0.1:1338/latest/meta-data/container/security-credentials" --max-time 3)
    export AWS_ACCESS_KEY_ID=$(echo "$creds"     | jq -r .AccessKeyId)
    export AWS_SECRET_ACCESS_KEY=$(echo "$creds" | jq -r .SecretAccessKey)
    export AWS_SESSION_TOKEN=$(echo "$creds"     | jq -r .Token)
    PROFILE_ARG=""
    CREDS_FROM_METADATA=true
    return 0
  fi

  # Priority 3b: EC2 IMDSv2
  if curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/ >/dev/null 2>&1; then
    log_info "Detected EC2 environment"
    local token role creds
    token=$(curl -sX PUT "http://169.254.169.254/latest/api/token" \
      -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
    role=$(curl -s -H "X-aws-ec2-metadata-token: $token" \
      http://169.254.169.254/latest/meta-data/iam/security-credentials/)
    if [ -n "$role" ]; then
      creds=$(curl -s -H "X-aws-ec2-metadata-token: $token" \
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/$role")
      export AWS_ACCESS_KEY_ID=$(echo "$creds"     | jq -r .AccessKeyId)
      export AWS_SECRET_ACCESS_KEY=$(echo "$creds" | jq -r .SecretAccessKey)
      export AWS_SESSION_TOKEN=$(echo "$creds"     | jq -r .Token)
      PROFILE_ARG=""
      CREDS_FROM_METADATA=true
      return 0
    fi
  fi

  log_error "No credentials found. Set AWS_ACCESS_KEY_ID/SECRET, export PROFILE=<name>, or run from CloudShell/EC2."
  exit 1
}

# --- Ensure companion scripts are present alongside db2monitor.sh ---
ensure_companion_scripts() {
  local script_dir; script_dir="$(cd "$(dirname "${SCRIPT_PATH}")" && pwd)" 2>/dev/null || script_dir="."
  local changed=false
  for script in "$SCRIPT_AIRGAP" "$SCRIPT_DIAG" "$SCRIPT_CLEANUP"; do
    local dest="${script_dir}/${script}"
    if [ ! -f "$dest" ]; then
      log_info "Downloading missing companion: $script"
      if [ -n "$BUCKET" ]; then
        aws s3 cp "s3://${TARGET_BUCKET}/${script}" "$dest" \
          --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} --quiet && chmod +x "$dest"
      else
        curl -fsSL "${SOURCE_URL}/${script}" -o "$dest" && chmod +x "$dest"
      fi
      log_success "Saved: $dest"
      changed=true
    fi
  done
  $changed || log_info "Companion scripts present"
}

# --- Download helpers: online (curl) or airgap (aws s3 cp) ---
curl_download() {
  local url="$1" dest="$2"
  curl -fsSL "$url" -o "$dest"
}

s3_download() {
  local key="$1" dest="$2"
  aws s3 cp "s3://${TARGET_BUCKET}/${key}" "$dest" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} --quiet
}

# --- Ensure jq is available — install from Lambda bucket if missing (airgap) ---
ensure_jq() {
  command -v jq &>/dev/null && return 0
  if [ -n "$BUCKET" ]; then
    log_info "jq not found — downloading from s3://${TARGET_BUCKET}/scripts/jq ..."
    local tmp_jq
    tmp_jq=$(mktemp)
    aws s3 cp "s3://${TARGET_BUCKET}/scripts/jq" "$tmp_jq" \
      --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} --quiet
    sudo mv -f "$tmp_jq" /usr/local/bin/jq
    sudo chmod +x /usr/local/bin/jq
    log_success "jq installed from private bucket"
  else
    log_error "jq not found. Install it: sudo yum install -y jq  # or apt-get install jq"
    exit 1
  fi
}

# --- Prerequisites ---
check_prerequisites() {
  log_info "Checking prerequisites..."
  local missing=()
  for cmd in aws curl; do
    command -v "$cmd" &>/dev/null || missing+=("$cmd")
  done
  if [ ${#missing[@]} -gt 0 ]; then
    log_error "Missing required tools: ${missing[*]}"
    case "$PLATFORM" in
      macos) echo "  brew install ${missing[*]}" ;;
      linux) echo "  sudo yum install -y ${missing[*]}  # or apt-get" ;;
    esac
    exit 1
  fi
  # AWS CLI version check (require v2)
  local aws_ver
  aws_ver=$(aws --version 2>&1 | grep -oE 'aws-cli/[0-9]+' | cut -d'/' -f2)
  if [ "${aws_ver:-1}" -lt 2 ]; then
    log_error "AWS CLI v2 required (found: $(aws --version 2>&1 | head -1)). Install from https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
  fi
  log_success "Prerequisites OK"
}

# --- AWS Environment ---
setup_aws_environment() {
  log_info "Setting up AWS environment..."

  # Set PROFILE_ARG early (before ensure_jq) based on env vars — same as db2-driver validate()
  if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
    PROFILE_ARG=""
  else
    PROFILE_ARG="--profile $PROFILE"
  fi

  # Resolve REGION before ensure_jq (needed for bucket name)
  if [ -z "$REGION" ]; then
    if [ -n "${AWS_DEFAULT_REGION:-}" ]; then
      REGION="$AWS_DEFAULT_REGION"
      log_info "Detected region from environment: $REGION"
    elif curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/ >/dev/null 2>&1; then
      local token
      token=$(curl -sX PUT "http://169.254.169.254/latest/api/token" \
        -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" 2>/dev/null)
      REGION=$(curl -s -H "X-aws-ec2-metadata-token: $token" \
        http://169.254.169.254/latest/meta-data/placement/region 2>/dev/null)
      log_info "Detected region from EC2 metadata: $REGION"
    else
      REGION=$(aws configure get region ${PROFILE_ARG:+$PROFILE_ARG} 2>/dev/null || true)
    fi
    if [ -z "$REGION" ]; then
      log_error "REGION not set. Run 'aws configure' or export REGION=<region>"
      exit 1
    fi
  fi

  # Use BUCKET if pre-set (airgap), otherwise derive after ACCOUNT_ID is known
  TARGET_BUCKET="${BUCKET:-lambda-functions-placeholder-${REGION}}"

  set_credentials  # probe CloudShell → EC2 → env vars → profile; may update PROFILE_ARG
  ensure_jq

  # Prompt for profile only when not using metadata/env creds
  if [ "$CREDS_FROM_METADATA" = "false" ] && \
     [ -z "${AWS_ACCESS_KEY_ID:-}" ] && \
     [ -z "$PROFILE" ] && \
     [ "$INTERACTIVE_MODE" = "true" ]; then
    PROFILE=$(getInput "PROFILE" "" "Enter AWS profile (leave blank for env vars/instance metadata)")
    [ -n "$PROFILE" ] && PROFILE_ARG="--profile $PROFILE"
  fi

  if [ "${CREDS_FROM_METADATA:-false}" = "false" ]; then
    if ! aws sts get-caller-identity ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" >/dev/null 2>&1; then
      if [ -n "$PROFILE" ]; then
        log_error "Profile '$PROFILE' credentials are invalid or expired."
        log_error "Either refresh credentials for '$PROFILE' or unset PROFILE to use instance metadata."
      else
        log_error "AWS credentials invalid. Run 'aws configure' or set AWS_ACCESS_KEY_ID/SECRET."
      fi
      exit 1
    fi
    ACCOUNT_ID=$(aws sts get-caller-identity ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" --query Account --output text)
  fi
  # Fallback: parse account ID from bucket name (airgap CloudShell — no STS reachable)
  if [ -z "${ACCOUNT_ID:-}" ] && [ -n "$BUCKET" ]; then
    ACCOUNT_ID=$(echo "$BUCKET" | grep -oE '[0-9]{12}')
  fi
  TARGET_BUCKET="${BUCKET:-lambda-functions-${ACCOUNT_ID}-${REGION}}"
  export REGION PROFILE ACCOUNT_ID
  if [ -n "$BUCKET" ]; then
    log_success "AWS ready | Mode: AIRGAP | Account: $ACCOUNT_ID | Region: $REGION | Bucket: $TARGET_BUCKET"
  else
    log_success "AWS ready | Mode: ONLINE | Account: $ACCOUNT_ID | Region: $REGION"
  fi

  resolve_db2mon_kms_key
}

# --- Resolve or create a customer-managed KMS key for Lambda + Secrets Manager ---
resolve_db2mon_kms_key() {
  # Honour env override — always fast path
  if [ -n "$KMS_KEY_ARN" ]; then
    log_info "Using KMS key from environment: $KMS_KEY_ARN"
    return
  fi

  # In airgap mode KMS endpoint may not be available — prompt or skip
  if [ -n "$BUCKET" ]; then
    if [ "$INTERACTIVE_MODE" = "true" ]; then
      echo ""
      log_warning "KMS_KEY_ARN not set — no customer-managed key encryption will be applied."
      echo "  NOTE: AWS Org SCPs may block default KMS keys (aws/lambda, aws/secretsmanager)."
      echo "  To use a CMK, exit now and re-run with:  KMS_KEY_ARN=<key-arn> ./db2mon-unified.sh"
      echo ""
      local user_input
      read -p "  Continue without KMS key? [y/N]: " user_input
      case "${user_input,,}" in
        y|yes) log_warning "Continuing without customer-managed KMS key." ;;
        *) log_error "Aborted. Set KMS_KEY_ARN in your env file and re-run."; exit 1 ;;
      esac
    else
      log_warning "KMS_KEY_ARN not set — skipping KMS encryption for Lambda and Secrets Manager."
      log_warning "Set KMS_KEY_ARN=<key-arn> in your env file to enable customer-managed key encryption."
    fi
    return
  fi

  log_info "Fetching customer-managed KMS keys in $REGION..."
  local customer_keys=() key_id tmpdir all_key_ids
  tmpdir=$(mktemp -d)
  all_key_ids=$(aws kms list-keys --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
    --output json 2>/dev/null | jq -r '.Keys[].KeyId')

  for key_id in $all_key_ids; do
    (
      aws kms describe-key --key-id "$key_id" \
        --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} --output json 2>/dev/null \
      | jq -r 'select(
          .KeyMetadata.KeyManager=="CUSTOMER" and
          .KeyMetadata.Enabled==true and
          .KeyMetadata.KeyState=="Enabled"
        ) | .KeyMetadata.Arn' > "$tmpdir/$key_id"
    ) &
  done
  wait

  for key_id in $all_key_ids; do
    local arn; arn=$(cat "$tmpdir/$key_id" 2>/dev/null)
    [ -n "$arn" ] && customer_keys+=("$arn")
  done
  rm -rf "$tmpdir"

  if [ ${#customer_keys[@]} -eq 0 ]; then
    log_warning "No customer-managed KMS keys found — creating one for db2mon..."
    KMS_KEY_ARN=$(aws kms create-key \
      --description "db2mon key for Lambda and Secrets Manager encryption" \
      --key-usage ENCRYPT_DECRYPT --origin AWS_KMS \
      --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
      --query 'KeyMetadata.Arn' --output text)
    aws kms create-alias \
      --alias-name "alias/db2mon-key" \
      --target-key-id "$KMS_KEY_ARN" \
      --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} 2>/dev/null || true
    log_success "Created KMS key: $KMS_KEY_ARN"
    return
  fi

  if [ "$INTERACTIVE_MODE" = "true" ]; then
    echo "  Available customer-managed KMS keys:"
    local i=1
    for key_arn in "${customer_keys[@]}"; do
      echo "    $i) $key_arn"
      ((i++))
    done
    local user_input
    read -p "  Select KMS key for Lambda + Secrets Manager (default 1): " user_input
    local idx=${user_input:-1}
    if [[ "$idx" =~ ^[0-9]+$ ]] && [ "$idx" -ge 1 ] && [ "$idx" -le ${#customer_keys[@]} ]; then
      KMS_KEY_ARN="${customer_keys[$((idx-1))]}"
    else
      log_warning "Invalid selection — using first key"
      KMS_KEY_ARN="${customer_keys[0]}"
    fi
  else
    KMS_KEY_ARN="${customer_keys[0]}"
  fi
  log_info "Using KMS key: $KMS_KEY_ARN"
}

# --- Permission Check ---
check_permissions() {
  [[ "$CHECK_PERMISSIONS" != "true" ]] && return 0

  log_info "Checking IAM permissions..."
  local arn
  arn=$(aws sts get-caller-identity ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" --query Arn --output text)

  local required_actions=(
    "s3:CreateBucket" "s3:PutObject" "s3:GetObject" "s3:ListBucket"
    "lambda:CreateFunction" "lambda:UpdateFunctionCode" "lambda:UpdateFunctionConfiguration"
    "lambda:GetFunction" "lambda:GetFunctionConfiguration"
    "lambda:PublishLayerVersion" "lambda:ListLayerVersions"
    "lambda:GetLayerVersion" "lambda:GetLayerVersionByArn" "lambda:AddPermission"
    "iam:CreateRole" "iam:AttachRolePolicy" "iam:DetachRolePolicy"
    "iam:DeleteRole" "iam:GetRole" "iam:ListAttachedRolePolicies" "iam:PassRole"
    "iam:PutRolePolicy" "iam:DeleteRolePolicy" "iam:GetRolePolicy"
    "cloudformation:CreateStack" "cloudformation:DescribeStacks"
    "cloudwatch:PutDashboard"
    "logs:CreateLogGroup" "logs:DescribeLogGroups"
    "scheduler:CreateSchedule" "scheduler:GetSchedule"
    "scheduler:UpdateSchedule" "scheduler:ListSchedules"
    "secretsmanager:GetSecretValue" "secretsmanager:DescribeSecret"
    "ssm:GetParameter" "ssm:PutParameter" "ssm:DeleteParameter"
    "ec2:DescribeVpcs" "ec2:DescribeSubnets" "ec2:DescribeSecurityGroups"
    "ec2:DescribeVpcEndpoints" "ec2:DescribeRouteTables" "ec2:DescribeInternetGateways"
    "ec2:DescribeVpcAttribute" "ec2:ModifyVpcAttribute"
    "ec2:CreateVpcEndpoint" "ec2:ModifyVpcEndpoint"
    "sns:CreateTopic"
    "sqs:CreateQueue" "sqs:DeleteQueue" "sqs:GetQueueAttributes"
    "sqs:SetQueueAttributes" "sqs:GetQueueUrl"
    "rds:DescribeDBInstances" "rds:DescribeDBParameters"
  )

  # iam:SimulatePrincipalPolicy uses the global IAM endpoint — no VPC endpoint exists.
  # In airgap/private-subnet mode this will hang; treat timeout as "not available".
  local simulate_result
  simulate_result=$(aws iam simulate-principal-policy \
    --policy-source-arn "$arn" --action-names "iam:SimulatePrincipalPolicy" \
    ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" --cli-connect-timeout 5 \
    --query 'EvaluationResults[0].EvalDecision' --output text 2>/dev/null || echo "timeout")

  if [ "$simulate_result" != "allowed" ]; then
    log_warning "iam:SimulatePrincipalPolicy not available — cannot verify permissions automatically."
    echo >&2
    echo "  You need minimum following permissions to deploy:" >&2
    echo "  Ask your IAM administrator to add the following policy to your user/role:" >&2
    echo >&2
    local policy_json
    policy_json=$(printf '%s\n' "${required_actions[@]}" | jq -R . | jq -s \
      '{Version:"2012-10-17",Statement:[{Effect:"Allow",Action:.,Resource:"*"}]}')
    echo "$policy_json" | tee missing_permissions.json >&2
    echo >&2
    log_info "Policy also saved to: missing_permissions.json"
    log_info "Once permissions are granted, rerun without --check-permissions to deploy."
    exit 0
  fi

  local missing_actions=()
  for action in "${required_actions[@]}"; do
    local result
    result=$(aws iam simulate-principal-policy \
      --policy-source-arn "$arn" --action-names "$action" \
      ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" --cli-connect-timeout 5 \
      --query 'EvaluationResults[0].EvalDecision' --output text 2>/dev/null || echo "timeout")
    [[ "$result" != "allowed" ]] && missing_actions+=("$action")
  done

  if [ ${#missing_actions[@]} -eq 0 ]; then
    log_success "All required permissions present"
  else
    log_warning "Missing permissions:"
    printf '  - %s\n' "${missing_actions[@]}"
    cat > missing_permissions.json <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":$(printf '%s\n' "${missing_actions[@]}" | jq -R . | jq -s .),"Resource":"*"}]}
EOF
    log_info "Policy saved to: missing_permissions.json"
  fi
  log_info "Permission check complete. Rerun without --check-permissions to deploy."
  exit 0
}

# --- DB Instance Selection ---
list_db_instances() {
  local instances
  instances=($(aws rds describe-db-instances \
    ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" \
    --query "DBInstances[?starts_with(Engine,'db2')].DBInstanceIdentifier" \
    --output text 2>/dev/null))

  if [ ${#instances[@]} -eq 0 ]; then
    log_error "No RDS DB2 instances found in $REGION"
    return 1
  fi

  if [ -n "$DB_INSTANCE_ID" ]; then
    for i in "${instances[@]}"; do
      [[ "$i" == "$DB_INSTANCE_ID" ]] && return 0
    done
    log_warning "DB_INSTANCE_ID '$DB_INSTANCE_ID' not found; available: ${instances[*]}"
    [[ "$INTERACTIVE_MODE" == "false" ]] && { DB_INSTANCE_ID="${instances[0]}"; return 0; }
  fi

  if [[ "$INTERACTIVE_MODE" == "false" ]]; then
    DB_INSTANCE_ID="${instances[0]}"
    log_info "Using first DB2 instance: $DB_INSTANCE_ID"
    return 0
  fi

  local choice=0
  while [ "$choice" -lt 1 ] || [ "$choice" -gt ${#instances[@]} ]; do
    echo "Available DB2 instances:" >&2
    for i in "${!instances[@]}"; do echo "$((i+1)). ${instances[$i]}" >&2; done
    read -p "Select instance (1-${#instances[@]}): " choice; choice=${choice:-1}
    [[ $choice -ge 1 && $choice -le ${#instances[@]} ]] && DB_INSTANCE_ID="${instances[$((choice-1))]}" || choice=0
  done
}

# --- Tag ---
LAST_TAG_FILE="${HOME}/.db2mon_last_tag"
LAST_DBNAME_FILE="${HOME}/.db2mon_last_dbname"
LAST_SUBNETS_DIR="${HOME}/.db2mon_last_subnets"

get_tag() {
  if [ -n "$TAG" ]; then return 0; fi

  # Read last used tag as suggested default
  local last_tag=""
  [ -f "$LAST_TAG_FILE" ] && last_tag=$(cat "$LAST_TAG_FILE" 2>/dev/null | tr -d '[:space:]')

  while true; do
    if [ -n "$last_tag" ]; then
      read -p "  Enter a tag to identify this deployment (last used: $last_tag): " TAG
      TAG=${TAG:-$last_tag}
    else
      read -p "  Enter a tag to identify this deployment (e.g. ACME, PROJ1): " TAG
    fi
    TAG=$(echo "$TAG" | tr '[:lower:]' '[:upper:]' | tr -d '[:space:]')
    if [ -z "$TAG" ]; then
      log_error "TAG is required — it uniquely identifies your deployment resources."
    elif [[ ! "$TAG" =~ ^[A-Z0-9-]+$ ]] || [[ "$TAG" =~ ^- ]] || [[ "$TAG" =~ -$ ]] || [[ "$TAG" =~ -- ]]; then
      log_error "Invalid tag '$TAG' — use only letters, numbers, hyphens; no leading/trailing/consecutive hyphens."
    else
      echo "$TAG" > "$LAST_TAG_FILE"
      break
    fi
  done
}

# --- Create Secrets Manager secret ---
create_secret() {
  log_info "Secret '$SECRET_MANAGER_NAME' not found — creating it now..."

  # Try RDS managed password first
  local master_secret_arn
  master_secret_arn=$(aws rds describe-db-instances \
    --db-instance-identifier "$DB_INSTANCE_ID" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
    --query "DBInstances[0].MasterUserSecret.SecretArn" --output text 2>/dev/null)

  if [ -n "$master_secret_arn" ] && [ "$master_secret_arn" != "None" ]; then
    log_info "Retrieving password from RDS managed secret..."
    PASSWORD=$(aws secretsmanager get-secret-value \
      --secret-id "$master_secret_arn" \
      --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
      --query SecretString --output text 2>/dev/null | jq -r '.password')
    [ -z "$PASSWORD" ] || [ "$PASSWORD" = "null" ] && { log_error "Failed to retrieve managed password"; return 1; }
    log_success "Password retrieved from RDS managed secret"
  else
    if [ -n "${PASSWORD:-}" ]; then
      :  # use exported PASSWORD as-is
    elif [ "$INTERACTIVE_MODE" = "false" ]; then
      log_error "Required: PASSWORD"; return 1
    else
      # Hidden prompt; paste or type. Any character allowed, including $ ! \ " '
      local _pw1 _pw2
      while :; do
        read -rsp "Enter database password: " _pw1; echo >&2
        read -rsp "Confirm database password: " _pw2; echo >&2
        if [ "$_pw1" = "$_pw2" ] && [ -n "$_pw1" ]; then
          PASSWORD="$_pw1"
          break
        fi
        log_warning "Passwords did not match or were empty. Try again."
      done
      unset _pw1 _pw2
    fi
  fi

  local rds_info
  rds_info=$(aws rds describe-db-instances \
    --db-instance-identifier "$DB_INSTANCE_ID" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} --output json 2>/dev/null)
  [ -z "$rds_info" ] && { log_error "Failed to retrieve RDS instance info"; return 1; }

  local host port username engine engine_version instance_type vpc_id sg_id subnet_group az multi_az storage_type iops allocated_storage publicly_accessible param_group_name rds_kms_key_id
  host=$(echo "$rds_info"               | jq -r '.DBInstances[0].Endpoint.Address // ""')
  port=$(echo "$rds_info"               | jq -r '.DBInstances[0].Endpoint.Port // 50000')
  username=$(echo "$rds_info"           | jq -r '.DBInstances[0].MasterUsername // "admin"')
  engine=$(echo "$rds_info"             | jq -r '.DBInstances[0].Engine // "db2-se"')
  engine_version=$(echo "$rds_info"     | jq -r '.DBInstances[0].EngineVersion // ""')
  instance_type=$(echo "$rds_info"      | jq -r '.DBInstances[0].DBInstanceClass // ""')
  vpc_id=$(echo "$rds_info"             | jq -r '.DBInstances[0].DBSubnetGroup.VpcId // ""')
  sg_id=$(echo "$rds_info"              | jq -r '.DBInstances[0].VpcSecurityGroups[0].VpcSecurityGroupId // ""')
  subnet_group=$(echo "$rds_info"       | jq -r '.DBInstances[0].DBSubnetGroup.DBSubnetGroupName // ""')
  az=$(echo "$rds_info"                 | jq -r '.DBInstances[0].AvailabilityZone // ""')
  multi_az=$(echo "$rds_info"           | jq -r '.DBInstances[0].MultiAZ // false')
  storage_type=$(echo "$rds_info"       | jq -r '.DBInstances[0].StorageType // ""')
  iops=$(echo "$rds_info"               | jq -r '.DBInstances[0].Iops // ""')
  allocated_storage=$(echo "$rds_info"  | jq -r '.DBInstances[0].AllocatedStorage // ""')
  publicly_accessible=$(echo "$rds_info"| jq -r '.DBInstances[0].PubliclyAccessible // false')
  param_group_name=$(echo "$rds_info"   | jq -r '.DBInstances[0].DBParameterGroups[0].DBParameterGroupName // ""')
  rds_kms_key_id=$(echo "$rds_info"     | jq -r '.DBInstances[0].KmsKeyId // ""')

  [ -z "$host" ] || [ "$host" = "None" ] && { log_error "Could not retrieve RDS endpoint"; return 1; }

  # --- Detect SSL from parameter group ---
  local use_ssl="false" ssl_port="$port" ssl_cert_location=""
  if [ -n "$param_group_name" ]; then
    local db2comm
    db2comm=$(aws rds describe-db-parameters \
      --db-parameter-group-name "$param_group_name" \
      --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
      --query "Parameters[?ParameterName=='db2comm'].ParameterValue" \
      --output text 2>/dev/null)
    log_info "db2comm=$db2comm (param group: $param_group_name)"
    local db2comm_upper; db2comm_upper=$(echo "$db2comm" | tr '[:lower:]' '[:upper:]')
    local has_ssl=false
    for _proto in $(echo "$db2comm_upper" | tr ',' ' '); do
      [[ "$_proto" == "SSL" ]] && has_ssl=true && break
    done
    if $has_ssl; then
      local ssl_svcename
      ssl_svcename=$(aws rds describe-db-parameters \
        --db-parameter-group-name "$param_group_name" \
        --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
        --query "Parameters[?ParameterName=='ssl_svcename'].ParameterValue" \
        --output text 2>/dev/null)
      if [[ "$ssl_svcename" =~ ^[0-9]+$ ]]; then
        use_ssl="true"
        ssl_port="$ssl_svcename"
        ssl_cert_location="s3://lambda-functions-${ACCOUNT_ID}-${REGION}/ssl/${REGION}-bundle.pem"
        log_info "SSL enabled: port=$ssl_port cert=$ssl_cert_location"
      else
        log_warning "db2comm contains SSL but ssl_svcename='$ssl_svcename' is not a number — ssl=false"
      fi
    else
      log_info "TCP only (db2comm=$db2comm): ssl=false"
    fi
  fi

  local az_subnet_id=""
  [ -n "$az" ] && [ -n "$vpc_id" ] && az_subnet_id=$(aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=$vpc_id" "Name=availability-zone,Values=$az" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
    --query 'Subnets[0].SubnetId' --output text 2>/dev/null)

  local accessible_type="no-publicly-accessible"
  [ "$publicly_accessible" = "true" ] && accessible_type="publicly-accessible"

  local master_secret_arn_field=""
  [ -n "$master_secret_arn" ] && [ "$master_secret_arn" != "None" ] && \
    master_secret_arn_field="\"masterSecretArn\": \"${master_secret_arn}\","

  # JSON-escape password (handles " \ and control chars). jq -Rs . emits a fully quoted JSON string.
  local password_json
  password_json=$(printf '%s' "$PASSWORD" | jq -Rs .)

  local secret_json
  secret_json=$(cat <<EOF
{
  "tag": "${TAG}",
  "dbInstanceIdentifier": "${DB_INSTANCE_ID}",
  ${master_secret_arn_field}
  "host": "${host}",
  "port": "${ssl_port}",
  "database": "${DBNAME}",
  "username": "${username}",
  "password": ${password_json},
  "ssl": "${use_ssl}",
  "sslCertLocation": "${ssl_cert_location}",
  "instanceType": "${instance_type}",
  "vpcID": "${vpc_id}",
  "sgID": "${sg_id}",
  "subnetGroupName": "${subnet_group}",
  "accessibleType": "${accessible_type}",
  "multiAZ": "${multi_az}",
  "iops": "${iops}",
  "allocatedStorage": "${allocated_storage}",
  "az": "${az}",
  "azSubnetID": "${az_subnet_id}",
  "storageType": "${storage_type}",
  "engineVersion": "${engine_version}",
  "Engine": "${engine}"
}
EOF
)

  aws secretsmanager create-secret \
    --name "$SECRET_MANAGER_NAME" \
    --description "DB2 monitoring credentials for ${DB_INSTANCE_ID}" \
    --secret-string "$secret_json" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
    ${KMS_KEY_ARN:+--kms-key-id "$KMS_KEY_ARN"} \
    --tags Key=project,Value="${TAG}" Key=DBInstanceId,Value="${DB_INSTANCE_ID}" >/dev/null
  log_success "Secret created: $SECRET_MANAGER_NAME"

  register_in_ssm
}

# --- Register SECRET_MANAGER_NAME in SSM (idempotent, called early so cleanup always works) ---
register_in_ssm() {
  local param="/db2mon/instances"
  local existing_val
  existing_val=$(aws ssm get-parameter --name "$param" ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" \
    --query 'Parameter.Value' --output text 2>/dev/null || true)
  if [ -z "$existing_val" ] || [ "$existing_val" = "None" ]; then
    aws ssm put-parameter --name "$param" --type StringList --value "$SECRET_MANAGER_NAME" \
      --description "db2mon registered instances" ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" >/dev/null
  elif ! echo "$existing_val" | tr ',' '\n' | grep -qx "$SECRET_MANAGER_NAME"; then
    aws ssm put-parameter --name "$param" --type StringList --value "${existing_val},${SECRET_MANAGER_NAME}" \
      --overwrite ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" >/dev/null
  else
    log_info "Already registered in SSM: $SECRET_MANAGER_NAME"
    return 0
  fi
  log_success "Registered in SSM: $param -> $SECRET_MANAGER_NAME"
}

# --- Read Secret for existing values ---
load_secret_values() {
  local secret_name="$1"
  local secret_string
  secret_string=$(aws secretsmanager get-secret-value \
    --secret-id "$secret_name" --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
    --query SecretString --output text 2>/dev/null) || return 1

  DB_ENDPOINT_URL=$(echo "$secret_string" | jq -r '.host // ""')
  DB2PORT=$(echo "$secret_string"        | jq -r '.port // "50000"')
  USERNAME=$(echo "$secret_string"       | jq -r '.username // ""')
  VPC_ID=$(echo "$secret_string"         | jq -r '.vpcID // ""')
  SG_ID=$(echo "$secret_string"          | jq -r '.sgID // ""')
  DB2_SSL=$(echo "$secret_string"        | jq -r '.ssl // "false"')
  DB2_SSL_CERT=$(echo "$secret_string"   | jq -r '.sslCertLocation // ""')
  [[ -z "$PASSWORD" ]] && PASSWORD=$(echo "$secret_string" | jq -r '.password // ""')
  [[ -z "$TAG" ]]      && TAG=$(echo "$secret_string"      | jq -r '.tag // ""')
  log_success "Loaded values from secret: $secret_name (ssl=$DB2_SSL)"
}

# --- Setup Lambda S3 bucket ---
# Online mode:  create bucket if needed, then upload artifacts from public S3
# Airgap mode:  bucket was pre-populated by db2mon-airgap.sh — just verify
setup_lambda_bucket() {
  log_info "Using Lambda bucket: $TARGET_BUCKET"

  if [ -z "$BUCKET" ]; then
    # --- Online mode: create bucket + upload artifacts ---
    if ! aws s3api head-bucket --bucket "$TARGET_BUCKET" --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} 2>/dev/null; then
      log_info "Creating bucket: $TARGET_BUCKET"
      if [ "$REGION" = "us-east-1" ]; then
        aws s3api create-bucket --bucket "$TARGET_BUCKET" --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} >/dev/null
      else
        aws s3api create-bucket --bucket "$TARGET_BUCKET" --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
          --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
      fi
      aws s3api put-bucket-versioning --bucket "$TARGET_BUCKET" \
        --versioning-configuration Status=Enabled --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} >/dev/null

      # Block all public access
      aws s3api put-public-access-block --bucket "$TARGET_BUCKET" \
        --public-access-block-configuration \
          "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" \
        --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} >/dev/null

      # Default encryption: SSE-KMS if a CMK is configured, otherwise SSE-S3
      if [ -n "$KMS_KEY_ARN" ]; then
        aws s3api put-bucket-encryption --bucket "$TARGET_BUCKET" \
          --server-side-encryption-configuration \
            "{\"Rules\":[{\"ApplyServerSideEncryptionByDefault\":{\"SSEAlgorithm\":\"aws:kms\",\"KMSMasterKeyID\":\"${KMS_KEY_ARN}\"},\"BucketKeyEnabled\":true}]}" \
          --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} >/dev/null
      else
        aws s3api put-bucket-encryption --bucket "$TARGET_BUCKET" \
          --server-side-encryption-configuration \
            '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}' \
          --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} >/dev/null
      fi

      # Deny non-HTTPS requests
      aws s3api put-bucket-policy --bucket "$TARGET_BUCKET" \
        --policy "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"DenyNonHTTPS\",\"Effect\":\"Deny\",\"Principal\":\"*\",\"Action\":\"s3:*\",\"Resource\":[\"arn:aws:s3:::${TARGET_BUCKET}\",\"arn:aws:s3:::${TARGET_BUCKET}/*\"],\"Condition\":{\"Bool\":{\"aws:SecureTransport\":\"false\"}}}]}" \
        --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} >/dev/null

      log_success "Bucket created: $TARGET_BUCKET"
    else
      log_info "Bucket already exists: $TARGET_BUCKET"
    fi

    log_info "Uploading Lambda ZIPs and CFN templates from public S3..."
    local tmp_dir; tmp_dir=$(mktemp -d)
    trap "rm -rf $tmp_dir" RETURN

    mkdir -p "${tmp_dir}/Lambda/DB2Mon" "${tmp_dir}/CFN" "${tmp_dir}/ssl"
    for zip in DB2Mon-Code.zip DB2Mon-Layer.zip; do
      curl_download "${SOURCE_URL}/Lambda/DB2Mon/${zip}" "${tmp_dir}/Lambda/DB2Mon/${zip}"
    done
    for cfn in rds-db2monitor-main.yml rds-db2-dashboard.yml create-db2mon-eventbridge.yml; do
      curl_download "${SOURCE_URL}/CFN/${cfn}" "${tmp_dir}/CFN/${cfn}"
    done
    curl_download "${SOURCE_URL}/README.md" "${tmp_dir}/README.md"
    if [[ "${DB2_SSL:-false}" == "true" ]]; then
      curl_download \
        "https://truststore.pki.rds.amazonaws.com/${REGION}/${REGION}-bundle.pem" \
        "${tmp_dir}/ssl/${REGION}-bundle.pem"
    fi

    aws s3 sync "${tmp_dir}/" "s3://${TARGET_BUCKET}/" \
      --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} --quiet
    log_success "Artifacts uploaded to s3://${TARGET_BUCKET}/"
  else
    # --- Airgap mode: verify bucket was pre-populated by db2mon-airgap.sh ---
    if ! aws s3api head-bucket --bucket "$TARGET_BUCKET" --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} 2>/dev/null; then
      log_error "Bucket $TARGET_BUCKET not found."
      log_error "Run: ./$SCRIPT_AIRGAP --mode upload --region $REGION"
      exit 1
    fi

    local missing=false
    for key in \
      Lambda/DB2Mon/DB2Mon-Code.zip \
      Lambda/DB2Mon/DB2Mon-Layer.zip \
      CFN/rds-db2monitor-main.yml \
      CFN/rds-db2-dashboard.yml \
      CFN/create-db2mon-eventbridge.yml; do
      if aws s3api head-object --bucket "$TARGET_BUCKET" --key "$key" \
         --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} &>/dev/null; then
        log_info "Found: s3://${TARGET_BUCKET}/${key}"
      else
        log_error "Missing: s3://${TARGET_BUCKET}/${key}"
        missing=true
      fi
    done

    if [[ "${DB2_SSL:-false}" == "true" ]]; then
      local ssl_key="ssl/${REGION}-bundle.pem"
      if aws s3api head-object --bucket "$TARGET_BUCKET" --key "$ssl_key" \
         --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} &>/dev/null; then
        log_info "Found: s3://${TARGET_BUCKET}/${ssl_key}"
      else
        log_error "Missing: s3://${TARGET_BUCKET}/${ssl_key} — run ./$SCRIPT_AIRGAP --mode download first"
        missing=true
      fi
    fi
    [ "$missing" = "true" ] && { log_error "Re-run ./$SCRIPT_AIRGAP to upload missing artifacts."; exit 1; }
  fi

  log_success "Bucket ready: $TARGET_BUCKET"
}

# --- Merged CFN export stack: IAM role + Lambda ARN export ---
# Single stack db2mon-export-cfn-{REGION} replaces the old DB2Mon-Role-{REGION}
# and DB2Mon-Export-{REGION} stacks.
# Phase 1 (create): provisions the IAM role, exports LambdaRoleArn.
# Phase 2 (update): called after Lambda is created, adds DB2MonFunctionArn export.
create_lambda_role() {
  LAMBDA_ROLE_NAME="DB2Mon-Lambda-Role-${REGION}"
  local stack_name="db2mon-export-cfn-${REGION}"
  local status; status=$(stack_status "$stack_name")

  # Resolve the KMS key that encrypts the RDS-managed master user secret (if any).
  # Empty = AWS-owned key (aws/secretsmanager) — no explicit kms:Decrypt needed.
  local RDS_MASTER_SECRET_KMS_ARN=""
  if [ -n "${DB_INSTANCE_ID:-}" ]; then
    local _mus_kms
    _mus_kms=$(aws rds describe-db-instances \
      --db-instance-identifier "$DB_INSTANCE_ID" \
      --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
      --query "DBInstances[0].MasterUserSecret.KmsKeyId" --output text 2>/dev/null)
    if [ -n "$_mus_kms" ] && [ "$_mus_kms" != "None" ] && [ "$_mus_kms" != "null" ]; then
      # Normalize to a full key ARN (describe-key resolves aliases and key ids)
      RDS_MASTER_SECRET_KMS_ARN=$(aws kms describe-key \
        --key-id "$_mus_kms" \
        --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
        --query "KeyMetadata.Arn" --output text 2>/dev/null || true)
      [ -n "$RDS_MASTER_SECRET_KMS_ARN" ] && \
        log_info "RDS master user secret uses CMK: $RDS_MASTER_SECRET_KMS_ARN"
    fi
  fi

  _db2mon_export_cfn_template() {
    local fn_arn="${1:-placeholder}"
    # Scope to all SM-* app secrets in this account/region so a single role
    # works across multiple registered DB instances. (Previously scoped to
    # SM-${DB_INSTANCE_ID}-${DBNAME}-${TAG}-* but that requires per-instance roles.)
    local secret_arn="arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:SM-*"
    # RDS-managed master user secret (referenced by masterSecretArn inside the app secret)
    local rds_secret_arn="arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:rds!db-*"
    local log_group_arn="arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/lambda/DB2Mon-Lambda-Function-${REGION}:*"
    # App-created log groups are named DB2MonLG_<instance>_<db>; scope to all DB2MonLG_* in this account/region
    local app_log_group_arn="arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:DB2MonLG_*"
    local sns_topic_arn="arn:aws:sns:${REGION}:${ACCOUNT_ID}:DB2Mon-SNS-${REGION}"
    # Scope to all RDS DB instances in this account/region so a single role
    # supports multi-instance dashboards (one Lambda monitors many DBs).
    local rds_arn="arn:aws:rds:${REGION}:${ACCOUNT_ID}:db:*"
    local s3_bucket_arn="arn:aws:s3:::lambda-functions-${ACCOUNT_ID}-${REGION}"

    # Build inline policies array — always includes the four scoped service policies.
    # KMS policy is appended when either the app CMK or the RDS master secret CMK is set.
    local inline_policies
    inline_policies="[
        {
          \"PolicyName\": \"CloudWatchMetrics\",
          \"PolicyDocument\": {
            \"Version\": \"2012-10-17\",
            \"Statement\": [{
              \"Effect\": \"Allow\",
              \"Action\": \"cloudwatch:PutMetricData\",
              \"Resource\": \"*\",
              \"Condition\": {\"StringEquals\": {\"cloudwatch:namespace\": \"${DB2_SYSTEM_NS}\"}}
            }]
          }
        },
        {
          \"PolicyName\": \"CloudWatchLogs\",
          \"PolicyDocument\": {
            \"Version\": \"2012-10-17\",
            \"Statement\": [
              {
                \"Sid\": \"ListLogGroups\",
                \"Effect\": \"Allow\",
                \"Action\": \"logs:DescribeLogGroups\",
                \"Resource\": \"*\"
              },
              {
                \"Sid\": \"WriteLogs\",
                \"Effect\": \"Allow\",
                \"Action\": [
                  \"logs:CreateLogGroup\",
                  \"logs:CreateLogStream\",
                  \"logs:DescribeLogStreams\",
                  \"logs:PutLogEvents\"
                ],
                \"Resource\": [\"${log_group_arn}\", \"${app_log_group_arn}\"]
              }
            ]
          }
        },
        {
          \"PolicyName\": \"SecretsManagerRead\",
          \"PolicyDocument\": {
            \"Version\": \"2012-10-17\",
            \"Statement\": [{
              \"Effect\": \"Allow\",
              \"Action\": \"secretsmanager:GetSecretValue\",
              \"Resource\": [\"${secret_arn}\", \"${rds_secret_arn}\"]
            }]
          }
        },
        {
          \"PolicyName\": \"RDSDescribe\",
          \"PolicyDocument\": {
            \"Version\": \"2012-10-17\",
            \"Statement\": [
              {
                \"Effect\": \"Allow\",
                \"Action\": \"rds:DescribeDBInstances\",
                \"Resource\": \"${rds_arn}\"
              },
              {
                \"Effect\": \"Allow\",
                \"Action\": \"ec2:DescribeInstances\",
                \"Resource\": \"*\"
              }
            ]
          }
        },
        {
          \"PolicyName\": \"S3MonitoringBucket\",
          \"PolicyDocument\": {
            \"Version\": \"2012-10-17\",
            \"Statement\": [{
              \"Effect\": \"Allow\",
              \"Action\": [\"s3:GetObject\", \"s3:PutObject\"],
              \"Resource\": \"${s3_bucket_arn}/*\"
            }]
          }
        },
        {
          \"PolicyName\": \"SNSPublish\",
          \"PolicyDocument\": {
            \"Version\": \"2012-10-17\",
            \"Statement\": [
              {
                \"Effect\": \"Allow\",
                \"Action\": \"sns:Publish\",
                \"Resource\": \"${sns_topic_arn}\"
              },
              {
                \"Effect\": \"Allow\",
                \"Action\": \"sns:ListTopics\",
                \"Resource\": \"*\"
              }
            ]
          }
        }"
    # Combine KMS keys: the app's CMK (if set) and the RDS master secret's CMK (if set).
    # If neither is set, no KMSDecrypt policy is emitted (AWS-owned keys are implicit).
    local kms_resources=""
    [ -n "$KMS_KEY_ARN" ] && kms_resources="\"${KMS_KEY_ARN}\""
    if [ -n "$RDS_MASTER_SECRET_KMS_ARN" ] && [ "$RDS_MASTER_SECRET_KMS_ARN" != "$KMS_KEY_ARN" ]; then
      [ -n "$kms_resources" ] && kms_resources="${kms_resources},"
      kms_resources="${kms_resources}\"${RDS_MASTER_SECRET_KMS_ARN}\""
    fi
    if [ -n "$kms_resources" ]; then
      inline_policies="${inline_policies},{
          \"PolicyName\": \"KMSDecrypt\",
          \"PolicyDocument\": {
            \"Version\": \"2012-10-17\",
            \"Statement\": [{
              \"Effect\": \"Allow\",
              \"Action\": [\"kms:Decrypt\", \"kms:GenerateDataKey*\", \"kms:DescribeKey\"],
              \"Resource\": [${kms_resources}]
            }]
          }
        }"
    fi
    inline_policies="${inline_policies}]"
    cat <<CFNEOF
{
  "AWSTemplateFormatVersion": "2010-09-09",
  "Resources": {
    "LambdaRole": {
      "Type": "AWS::IAM::Role",
      "Properties": {
        "RoleName": "${LAMBDA_ROLE_NAME}",
        "AssumeRolePolicyDocument": {
          "Version": "2012-10-17",
          "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]
        },
        "ManagedPolicyArns": [
          "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
        ],
        "Policies": ${inline_policies}
      }
    }
  },
  "Outputs": {
    "LambdaRoleArn": {"Value": {"Fn::GetAtt": ["LambdaRole", "Arn"]}},
    "DB2MonFunctionArn": {"Value": "${fn_arn}", "Export": {"Name": "DB2MonFunctionArn"}}
  }
}
CFNEOF
  }

  case "$status" in
    CREATE_COMPLETE|UPDATE_COMPLETE)
      log_info "Export CFN stack already exists: $stack_name"
      LAMBDA_ROLE_ARN=$(cfn_output "$stack_name" "LambdaRoleArn")
      log_info "Lambda role ARN: $LAMBDA_ROLE_ARN"
      return 0 ;;
    CREATE_IN_PROGRESS|UPDATE_IN_PROGRESS)
      log_info "Export CFN stack in progress, waiting..."
      wait_for_stack "$stack_name" "stack-create-complete" >/dev/null
      LAMBDA_ROLE_ARN=$(cfn_output "$stack_name" "LambdaRoleArn")
      return 0 ;;
    *ROLLBACK*|*FAILED*)
      log_warning "Stack $stack_name is in $status — deleting and recreating..."
      aws cloudformation delete-stack --stack-name "$stack_name" --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG}
      aws cloudformation wait stack-delete-complete --stack-name "$stack_name" --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG}
      log_success "Deleted failed stack: $stack_name"
      _delete_orphaned_role ;;
    STACK_NOT_EXISTS|DELETE_COMPLETE)
      : ;;
  esac

  _create_export_stack() {
    log_info "Creating export CFN stack (IAM role + exports): $stack_name"
    aws cloudformation create-stack \
      --stack-name "$stack_name" \
      --capabilities CAPABILITY_NAMED_IAM \
      --template-body "$(_db2mon_export_cfn_template)" \
      --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} >/dev/null

    local final_status; final_status=$(wait_for_stack "$stack_name" "stack-create-complete")
    echo "$final_status"
  }

  _delete_orphaned_role() {
    if aws iam get-role --role-name "$LAMBDA_ROLE_NAME" ${PROFILE_ARG:+$PROFILE_ARG} --output text 2>/dev/null | grep -q "$LAMBDA_ROLE_NAME"; then
      log_warning "Orphaned IAM role found: $LAMBDA_ROLE_NAME — deleting..."
      local policy_arn
      while IFS= read -r policy_arn; do
        [ -z "$policy_arn" ] && continue
        aws iam detach-role-policy --role-name "$LAMBDA_ROLE_NAME" --policy-arn "$policy_arn" ${PROFILE_ARG:+$PROFILE_ARG} 2>/dev/null || true
      done < <(aws iam list-attached-role-policies --role-name "$LAMBDA_ROLE_NAME" \
        ${PROFILE_ARG:+$PROFILE_ARG} --query 'AttachedPolicies[*].PolicyArn' --output text 2>/dev/null | tr '\t' '\n')
      aws iam delete-role --role-name "$LAMBDA_ROLE_NAME" ${PROFILE_ARG:+$PROFILE_ARG}
      log_success "Deleted orphaned IAM role: $LAMBDA_ROLE_NAME"
    fi
  }

  _cleanup_failed_stack() {
    local failure_reason
    failure_reason=$(aws cloudformation describe-stack-events \
      --stack-name "$stack_name" --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
      --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`].ResourceStatusReason' \
      --output text 2>/dev/null | head -3)
    log_warning "CFN failure reason: ${failure_reason:-unknown}"
    aws cloudformation delete-stack --stack-name "$stack_name" --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG}
    aws cloudformation wait stack-delete-complete --stack-name "$stack_name" --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG}
    log_success "Deleted failed stack: $stack_name"
    _delete_orphaned_role
  }

  local final_status; final_status=$(_create_export_stack)
  if [[ "$final_status" != "CREATE_COMPLETE" ]]; then
    log_warning "Export CFN stack failed ($final_status) — cleaning up and retrying once..."
    _cleanup_failed_stack
    final_status=$(_create_export_stack)
    if [[ "$final_status" != "CREATE_COMPLETE" ]]; then
      local failure_reason
      failure_reason=$(aws cloudformation describe-stack-events \
        --stack-name "$stack_name" --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
        --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`].ResourceStatusReason' \
        --output text 2>/dev/null | head -3)
      log_error "Export CFN stack failed after retry: $final_status"
      log_error "Reason: ${failure_reason:-check CloudFormation console for details}"
      exit 1
    fi
  fi
  LAMBDA_ROLE_ARN=$(cfn_output "$stack_name" "LambdaRoleArn")
  log_success "Lambda role created: $LAMBDA_ROLE_ARN"
}

# --- Create Lambda Layer ---
create_lambda_layer() {
  local layer_name="DB2Mon-Layer-${REGION}"
  local s3_key="Lambda/DB2Mon/DB2Mon-Layer.zip"

  _publish_layer() {
    local s3_version s3_etag
    s3_version=$(aws s3api head-object --bucket "$TARGET_BUCKET" --key "$s3_key" \
      ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" --query 'VersionId' --output text 2>/dev/null || true)
    s3_etag=$(aws s3api head-object --bucket "$TARGET_BUCKET" --key "$s3_key" \
      ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" --query 'ETag' --output text 2>/dev/null | tr -d '"')
    local content_arg="S3Bucket=${TARGET_BUCKET},S3Key=${s3_key}"
    [ -n "$s3_version" ] && [ "$s3_version" != "None" ] && \
      content_arg="${content_arg},S3ObjectVersion=${s3_version}"

    local err
    LAMBDA_LAYER_ARN=$(aws lambda publish-layer-version \
      --layer-name "$layer_name" \
      --description "etag:${s3_etag}" \
      --content "$content_arg" \
      --compatible-runtimes python3.11 python3.12 \
      --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
      --query 'LayerVersionArn' --output text 2>/tmp/layer_err) || err=$(cat /tmp/layer_err)
    if [ -z "$LAMBDA_LAYER_ARN" ] || [ "$LAMBDA_LAYER_ARN" = "None" ]; then
      log_error "Could not publish layer: ${err:-unknown error}"
      log_error "Bucket: $TARGET_BUCKET  Key: $s3_key  Version: ${s3_version:-none}"
      LAMBDA_LAYER_ARN=""
    else
      log_success "Lambda layer published: $LAMBDA_LAYER_ARN"
    fi
  }

  local existing_arn
  existing_arn=$(aws lambda list-layer-versions \
    --layer-name "$layer_name" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
    --query 'LayerVersions[0].LayerVersionArn' --output text 2>/dev/null || true)

  if [ -z "$existing_arn" ] || [ "$existing_arn" = "None" ]; then
    log_info "No existing layer — publishing: $layer_name"
    _publish_layer
    return 0
  fi

  # Compare S3 ETag stored in layer description vs current S3 ETag
  # _publish_layer stores the ETag in the layer description for future comparison
  local s3_etag stored_etag
  s3_etag=$(aws s3api head-object --bucket "$TARGET_BUCKET" --key "$s3_key" \
    ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" --query 'ETag' --output text 2>/dev/null | tr -d '"')
  stored_etag=$(aws lambda get-layer-version-by-arn --arn "$existing_arn" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
    --query 'Description' --output text 2>/dev/null \
    | sed -n 's/.*etag:\([a-f0-9-][a-f0-9-]*\).*/\1/p' || true)

  if [ -n "$s3_etag" ] && [ "$s3_etag" = "$stored_etag" ]; then
    log_info "Lambda layer up to date: $existing_arn"
    LAMBDA_LAYER_ARN="$existing_arn"
  else
    log_info "Lambda layer zip has changed (etag differs) — publishing new version"
    _publish_layer
  fi
}

# --- Create or update Lambda function ---
create_lambda_function() {
  LAMBDA_FUNCTION_NAME="DB2Mon-Lambda-Function-${REGION}"
  local log_group="/aws/lambda/${LAMBDA_FUNCTION_NAME}"

  # Ensure log group
  aws logs create-log-group --log-group-name "$log_group" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} 2>/dev/null || true

  local existing
  existing=$(aws lambda get-function --function-name "$LAMBDA_FUNCTION_NAME" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
    --query 'Configuration.FunctionArn' --output text 2>/dev/null || true)

  if [ -n "$existing" ] && [ "$existing" != "None" ]; then
    log_info "Lambda function already exists: $LAMBDA_FUNCTION_NAME"
    LAMBDA_FUNCTION_ARN="$existing"
    log_info "Updating Lambda function code..."
    aws lambda update-function-code \
      --function-name "$LAMBDA_FUNCTION_NAME" \
      --s3-bucket "$TARGET_BUCKET" --s3-key "Lambda/DB2Mon/DB2Mon-Code.zip" \
      --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} >/dev/null
    log_success "Lambda code updated"
    # Update layer if a new version was published
    if [ -n "$LAMBDA_LAYER_ARN" ] && [ "$LAMBDA_LAYER_ARN" != "None" ]; then
      local current_layer
      current_layer=$(aws lambda get-function-configuration \
        --function-name "$LAMBDA_FUNCTION_NAME" \
        --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
        --query 'Layers[0].Arn' --output text 2>/dev/null || true)
      if [ "$current_layer" != "$LAMBDA_LAYER_ARN" ]; then
        log_info "Updating Lambda layer: $LAMBDA_LAYER_ARN"
        aws lambda wait function-updated \
          --function-name "$LAMBDA_FUNCTION_NAME" \
          --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} 2>/dev/null || true
        aws lambda update-function-configuration \
          --function-name "$LAMBDA_FUNCTION_NAME" \
          --layers "$LAMBDA_LAYER_ARN" \
          --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} >/dev/null
        log_success "Lambda layer updated: $LAMBDA_LAYER_ARN"
      fi
    fi
  else
    log_info "Creating Lambda function: $LAMBDA_FUNCTION_NAME"

    local layers_arg=""
    [ -n "$LAMBDA_LAYER_ARN" ] && layers_arg="--layers $LAMBDA_LAYER_ARN"

    local subnet_ids
    get_private_subnets
    subnet_ids="$PRIVATE_SUBNETS" 

    LAMBDA_FUNCTION_ARN=$(aws lambda create-function \
      --function-name "$LAMBDA_FUNCTION_NAME" \
      --runtime python3.12 \
      --role "$LAMBDA_ROLE_ARN" \
      --handler "main.main" \
      --code "S3Bucket=${TARGET_BUCKET},S3Key=Lambda/DB2Mon/DB2Mon-Code.zip" \
      --timeout 300 \
      --memory-size 512 \
      --vpc-config "SubnetIds=${subnet_ids},SecurityGroupIds=${SG_ID}" \
      --environment "Variables={REGION=${REGION}}" \
      ${KMS_KEY_ARN:+--kms-key-arn "$KMS_KEY_ARN"} \
      $layers_arg \
      --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
      --query 'FunctionArn' --output text)

    if [ -n "$KMS_KEY_ARN" ]; then
      log_success "Lambda function created with KMS key: $KMS_KEY_ARN"
    else
      log_warning "Lambda function created with default AWS-managed key — set KMS_KEY_ARN if org SCP blocks kms:Decrypt on aws/lambda"
    fi
    log_success "Lambda function created: $LAMBDA_FUNCTION_ARN"
  fi

  # Update the merged export stack with the real Lambda ARN (no-op if unchanged)
  local export_stack="db2mon-export-cfn-${REGION}"
  log_info "Updating export CFN stack with Lambda ARN: $export_stack"
  local update_out
  update_out=$(aws cloudformation update-stack \
    --stack-name "$export_stack" \
    --capabilities CAPABILITY_NAMED_IAM \
    --template-body "$(_db2mon_export_cfn_template "$LAMBDA_FUNCTION_ARN")" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} 2>&1) || true
  if echo "$update_out" | grep -q "No updates are to be performed"; then
    log_info "Export CFN stack already up to date"
  else
    wait_for_stack "$export_stack" "stack-update-complete" >/dev/null
    log_success "DB2MonFunctionArn export updated: $LAMBDA_FUNCTION_ARN"
  fi
}


# --- Get private subnets (no IGW route), sets PRIVATE_SUBNETS csv ---
# If SUBNET_IDS is pre-set, use it directly.
# Otherwise show all subnets with public/private label and let customer choose.
get_private_subnets() {
  if [ -n "$PRIVATE_SUBNETS" ]; then
    log_info "Using already-selected subnets: $PRIVATE_SUBNETS"
    return 0
  fi

  if [ -n "$SUBNET_IDS" ]; then
    PRIVATE_SUBNETS=$(echo "$SUBNET_IDS" | tr ' ' ',')
    log_info "Using pre-specified subnets: $PRIVATE_SUBNETS"
    return 0
  fi

  local _seen_azs="" _subnet_list=""

  # Detect IGW — used only for public/private labelling, not for filtering
  local igws
  igws=$(aws ec2 describe-internet-gateways \
    --filters "Name=attachment.vpc-id,Values=${VPC_ID}" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
    --query 'InternetGateways[*].InternetGatewayId' --output text 2>/dev/null || true)

  # Fetch all subnets with id, az, available IPs, and name tag
  local -a _all_subnets _all_azs _all_avail _all_labels
  while IFS=$'\t' read -r subnet_id az avail name; do
    local label="private"
    if [ -n "$igws" ]; then
      # Check if this subnet's route table has an IGW route
      local rtb
      rtb=$(aws ec2 describe-route-tables \
        --filters "Name=association.subnet-id,Values=${subnet_id}" \
        --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
        --query 'RouteTables[0].RouteTableId' --output text 2>/dev/null)
      if [ -z "$rtb" ] || [ "$rtb" = "None" ]; then
        rtb=$(aws ec2 describe-route-tables \
          --filters "Name=vpc-id,Values=${VPC_ID}" "Name=association.main,Values=true" \
          --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
          --query 'RouteTables[0].RouteTableId' --output text 2>/dev/null)
      fi
      for igw in $igws; do
        aws ec2 describe-route-tables --route-table-ids "$rtb" \
          --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
          --query "RouteTables[0].Routes[?GatewayId=='${igw}']" \
          --output text 2>/dev/null | grep -q "$igw" && label="public" && break
      done
    fi
    local display_name=""
    [ -n "$name" ] && [ "$name" != "None" ] && display_name=" $name"
    _all_subnets+=("$subnet_id")
    _all_azs+=("$az")
    _all_avail+=("$avail")
    _all_labels+=("$az, $label, ${avail} IPs${display_name}")
  done < <(aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=${VPC_ID}" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
    --query 'Subnets[*].[SubnetId,AvailabilityZone,AvailableIpAddressCount,Tags[?Key==`Name`].Value|[0]]' \
    --output text)

  if [ ${#_all_subnets[@]} -eq 0 ]; then
    log_warning "No subnets found in VPC $VPC_ID"
    return 1
  fi

  if [[ "$INTERACTIVE_MODE" == "false" ]]; then
    # Non-interactive: pick first private (or first available) per AZ
    for i in "${!_all_subnets[@]}"; do
      local az="${_all_azs[$i]}"
      echo "$_seen_azs" | grep -qw "$az" && continue
      # Skip public subnets in non-interactive if private ones exist
      [[ "${_all_labels[$i]}" == *"public"* ]] && continue
      _seen_azs="$_seen_azs $az"
      _subnet_list="${_subnet_list:+${_subnet_list},}${_all_subnets[$i]}"
    done
    # Fallback: if all were public, just pick one per AZ
    if [ -z "$_subnet_list" ]; then
      for i in "${!_all_subnets[@]}"; do
        local az="${_all_azs[$i]}"
        echo "$_seen_azs" | grep -qw "$az" && continue
        _seen_azs="$_seen_azs $az"
        _subnet_list="${_subnet_list:+${_subnet_list},}${_all_subnets[$i]}"
      done
    fi
    PRIVATE_SUBNETS="$_subnet_list"
    return 0
  fi

  # Interactive: show all subnets with labels
  echo >&2
  local no_igw_note=""
  [ -z "$igws" ] && no_igw_note=" (no IGW in VPC — all subnets shown)"
  echo "Available subnets in VPC $VPC_ID${no_igw_note}:" >&2
  for i in "${!_all_subnets[@]}"; do
    local avail_warn=""
    [ "${_all_avail[$i]:-0}" -lt 5 ] && avail_warn=" ⚠ LOW IPs"
    echo "  $((i+1)). ${_all_subnets[$i]}  (${_all_labels[$i]})${avail_warn}" >&2
  done
  echo "  A. Use all of the above" >&2
  echo >&2
  echo "Note: Choose subnets with available IPs. Prefer private subnets for Lambda." >&2
  # Load last-used subnet indices for this VPC
  local last_subnets_file="${LAST_SUBNETS_DIR}/${VPC_ID}"
  local last_choice=""
  mkdir -p "$LAST_SUBNETS_DIR"
  [ -f "$last_subnets_file" ] && last_choice=$(cat "$last_subnets_file" 2>/dev/null | tr -d '[:space:]')

  local raw_choice
  if [ -n "$last_choice" ]; then
    read -p "Enter subnet numbers to use (e.g. 1,3) or A for all (last used: $last_choice): " raw_choice
    raw_choice=${raw_choice:-$last_choice}
  else
    read -p "Enter subnet numbers to use (e.g. 1,3) or A for all: " raw_choice
  fi
  raw_choice=$(echo "$raw_choice" | tr '[:lower:]' '[:upper:]')

  if [ "$raw_choice" = "A" ] || [ -z "$raw_choice" ]; then
    for s in "${_all_subnets[@]}"; do
      _subnet_list="${_subnet_list:+${_subnet_list},}${s}"
    done
  else
    IFS=',' read -ra _choices <<< "$raw_choice"
    for c in "${_choices[@]}"; do
      c=$(echo "$c" | tr -d ' ')
      local idx=$(( c - 1 ))
      if [ "$idx" -ge 0 ] && [ "$idx" -lt ${#_all_subnets[@]} ]; then
        _subnet_list="${_subnet_list:+${_subnet_list},}${_all_subnets[$idx]}"
      else
        log_warning "Invalid subnet choice: $c (ignored)"
      fi
    done
  fi

  [ -z "$_subnet_list" ] && _subnet_list="${_all_subnets[0]}"
  PRIVATE_SUBNETS="$_subnet_list"
  echo "$raw_choice" > "$last_subnets_file"
  log_info "Selected subnets: $PRIVATE_SUBNETS"
}

# --- Service Endpoints ---
ensure_service_endpoints() {
  local cache_param="/db2mon/endpoints-ready/${VPC_ID}"
  local cache_ttl_days=7
  local cached_ts now age_days
  cached_ts=$(aws ssm get-parameter --name "$cache_param" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
    --query 'Parameter.Value' --output text 2>/dev/null || true)
  if [ -n "$cached_ts" ] && [ "$cached_ts" != "None" ]; then
    now=$(date +%s)
    age_days=$(( (now - cached_ts) / 86400 ))
    if [ "$age_days" -lt "$cache_ttl_days" ]; then
      log_info "VPC endpoints already verified ${age_days}d ago (TTL ${cache_ttl_days}d) — skipping check"
      return
    else
      log_info "VPC endpoint cache is ${age_days}d old (TTL ${cache_ttl_days}d) — re-verifying"
    fi
  fi

  log_info "Checking VPC service endpoints for VPC: $VPC_ID"

  # Verify VPC DNS attributes required for Interface endpoints with private DNS
  local dns_support dns_hostnames
  dns_support=$(aws ec2 describe-vpc-attribute --vpc-id "$VPC_ID" --attribute enableDnsSupport \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} --query 'EnableDnsSupport.Value' --output text)
  dns_hostnames=$(aws ec2 describe-vpc-attribute --vpc-id "$VPC_ID" --attribute enableDnsHostnames \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} --query 'EnableDnsHostnames.Value' --output text)
  if [ "$dns_support" != "True" ] || [ "$dns_hostnames" != "True" ]; then
    log_warning "VPC $VPC_ID: enableDnsSupport=$dns_support enableDnsHostnames=$dns_hostnames — attempting to enable..."
    if aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-support \
         --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} 2>/dev/null && \
       aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-hostnames \
         --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} 2>/dev/null; then
      log_success "VPC DNS attributes enabled on $VPC_ID"
    else
      log_error "Cannot enable VPC DNS attributes on $VPC_ID — insufficient permissions."
      log_error "Ask your admin to enable: VPC → Your VPC → Actions → Edit VPC settings"
      exit 1
    fi
  fi

  local services=(
    "com.amazonaws.${REGION}.secretsmanager"
    "com.amazonaws.${REGION}.monitoring"
    "com.amazonaws.${REGION}.logs"
    "com.amazonaws.${REGION}.lambda"
    "com.amazonaws.${REGION}.rds"
    "com.amazonaws.${REGION}.ec2"
    "com.amazonaws.${REGION}.sns"
    "com.amazonaws.${REGION}.sqs"
    "com.amazonaws.${REGION}.scheduler"
    "com.amazonaws.${REGION}.cloudformation"
    "com.amazonaws.${REGION}.ssm"
    "com.amazonaws.${REGION}.ssmmessages"
    "com.amazonaws.${REGION}.ec2messages"
    "com.amazonaws.${REGION}.sts"
  )

  get_private_subnets
  # Use only the first private subnet for Interface endpoints (avoids DuplicateSubnetsInSameZone)
  local endpoint_subnets
  endpoint_subnets=$(echo "$PRIVATE_SUBNETS" | cut -d',' -f1)
  if [ -z "$endpoint_subnets" ]; then
    endpoint_subnets=$(aws ec2 describe-subnets \
      --filters "Name=vpc-id,Values=${VPC_ID}" \
      --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
      --query 'Subnets[0].SubnetId' --output text)
  fi

  # Wait for any deleting endpoints to finish before proceeding
  local deleting_count
  deleting_count=$(aws ec2 describe-vpc-endpoints \
    --filters "Name=vpc-id,Values=${VPC_ID}" "Name=vpc-endpoint-state,Values=deleting" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
    --query 'length(VpcEndpoints)' --output text 2>/dev/null || echo 0)
  if [ "${deleting_count:-0}" -gt 0 ]; then
    log_info "Waiting for $deleting_count endpoint(s) still deleting..."
    local waited=0
    while [ "${deleting_count:-0}" -gt 0 ] && [ $waited -lt 3600 ]; do
      sleep 10; waited=$((waited+10))
      printf '.' >&2
      deleting_count=$(aws ec2 describe-vpc-endpoints \
        --filters "Name=vpc-id,Values=${VPC_ID}" "Name=vpc-endpoint-state,Values=deleting" \
        --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
        --query 'length(VpcEndpoints)' --output text 2>/dev/null || echo 0)
    done
    echo >&2
    [ "${deleting_count:-0}" -gt 0 ] && log_warning "Some endpoints still deleting after 60 min — proceeding anyway"
    log_success "Endpoint deletion complete"
  fi

  # S3 Gateway endpoint
  local s3_ep
  s3_ep=$(aws ec2 describe-vpc-endpoints \
    --filters "Name=vpc-id,Values=${VPC_ID}" \
      "Name=service-name,Values=com.amazonaws.${REGION}.s3" \
      "Name=vpc-endpoint-type,Values=Gateway" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
    --query 'VpcEndpoints[0].VpcEndpointId' --output text 2>/dev/null)
  if [ -z "$s3_ep" ] || [ "$s3_ep" = "None" ]; then
    log_info "Creating S3 Gateway endpoint..."
    local rt_ids
    rt_ids=$(aws ec2 describe-route-tables \
      --filters "Name=vpc-id,Values=${VPC_ID}" \
      --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
      --query 'RouteTables[*].RouteTableId' --output text | tr '\t' ' ')
    aws ec2 create-vpc-endpoint \
      --vpc-id "$VPC_ID" \
      --service-name "com.amazonaws.${REGION}.s3" \
      --vpc-endpoint-type Gateway \
      --route-table-ids $rt_ids \
      --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} >/dev/null
    log_success "S3 Gateway endpoint created"
  else
    log_info "S3 Gateway endpoint exists: $s3_ep"
  fi

  # Interface endpoints
  for svc in "${services[@]}"; do
    local ep_id
    ep_id=$(aws ec2 describe-vpc-endpoints \
      --filters "Name=vpc-id,Values=${VPC_ID}" \
        "Name=service-name,Values=${svc}" \
        "Name=vpc-endpoint-state,Values=available,pending" \
      --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
      --query 'VpcEndpoints[0].VpcEndpointId' --output text 2>/dev/null)
    if [ -z "$ep_id" ] || [ "$ep_id" = "None" ]; then
      log_info "Creating Interface endpoint: $svc"
      local ep_out ep_err
      ep_err=$(aws ec2 create-vpc-endpoint \
        --vpc-id "$VPC_ID" \
        --service-name "$svc" \
        --vpc-endpoint-type Interface \
        --subnet-ids ${endpoint_subnets//,/ } \
        --security-group-ids "$SG_ID" \
        --private-dns-enabled \
        --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} 2>&1 >/dev/null) || true
      if echo "$ep_err" | grep -q 'InvalidParameter\|already.*conflicting\|DuplicateSubnetsInSameZone'; then
        log_info "Endpoint already exists (conflict): $svc"
      elif [ -n "$ep_err" ]; then
        log_warning "Could not create endpoint $svc: $ep_err"
      else
        log_success "Created: $svc"
      fi
    else
      log_info "Endpoint exists ($ep_id): $svc"
    fi
  done

  # Ensure SG_ID is on ALL Interface endpoints in the VPC (fix from 0cr-ins.sh)
  if [[ "$SKIP_SG_ENDPOINT_ATTACH" == "true" ]]; then
    log_info "Skipping SG attachment to endpoints (SKIP_SG_ENDPOINT_ATTACH=true)"
  else
    log_info "Ensuring SG ${SG_ID} is attached to all Interface endpoints..."
    while IFS=$'\t' read -r ep_id svc_name; do
      [ -z "$ep_id" ] && continue
      local attached_sgs
      attached_sgs=$(aws ec2 describe-vpc-endpoints \
        --vpc-endpoint-ids "$ep_id" \
        --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
        --query 'VpcEndpoints[0].Groups[*].GroupId' --output text)
      if ! echo "$attached_sgs" | grep -qw "$SG_ID"; then
        log_info "Adding SG ${SG_ID} to ${ep_id} (${svc_name})"
        aws ec2 modify-vpc-endpoint \
          --vpc-endpoint-id "$ep_id" \
          --add-security-group-ids "$SG_ID" \
          --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} >/dev/null
      fi
    done < <(aws ec2 describe-vpc-endpoints \
      --filters "Name=vpc-id,Values=${VPC_ID}" \
        "Name=vpc-endpoint-type,Values=Interface" \
        "Name=vpc-endpoint-state,Values=available" \
      --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
      --query 'VpcEndpoints[*].[VpcEndpointId,ServiceName]' --output text)
  fi

  log_success "Service endpoints ready"
  aws ssm put-parameter --name "/db2mon/endpoints-ready/${VPC_ID}" \
    --value "$(date +%s)" --type String --overwrite \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} >/dev/null 2>&1 || true
}

# --- CFN Helpers ---
wait_for_stack() {
  local stack_name="$1" wait_action="$2"
  log_info "Waiting for $stack_name ($wait_action)..."
  aws cloudformation wait "$wait_action" --stack-name "$stack_name" \
    ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" || true
  local status
  status=$(aws cloudformation describe-stacks --stack-name "$stack_name" \
    ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "UNKNOWN")
  echo "$status"
}

stack_status() {
  aws cloudformation describe-stacks --stack-name "$1" \
    ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "STACK_NOT_EXISTS"
}

cfn_output() {
  aws cloudformation describe-stacks --stack-name "$1" \
    ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" \
    --output text 2>/dev/null || true
}

# --- Deploy Dashboard CFN ---
deploy_dashboard_stack() {
  local stack_name="DB2-Dashboard-${DB_INSTANCE_ID}-${DBNAME}-${TAG}"
  local status; status=$(stack_status "$stack_name")

  if [[ "$status" == "CREATE_COMPLETE" || "$status" == "UPDATE_COMPLETE" ]]; then
    log_success "Dashboard stack already deployed: $stack_name"
    local url; url=$(cfn_output "$stack_name" "DashboardURL")
    [ -n "$url" ] && log_success "Dashboard URL: $url"
    return 0
  fi

  if [[ "$status" == *"ROLLBACK"* ]]; then
    log_error "Stack $stack_name is in $status - delete it manually first:"
    log_error "  aws cloudformation delete-stack --stack-name $stack_name --region $REGION --profile $PROFILE"
    return 1
  fi

  log_info "Deploying dashboard stack: $stack_name"
  local out
  out=$(aws cloudformation create-stack \
    --stack-name "$stack_name" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameters \
      ParameterKey=customName,ParameterValue="${stack_name}" \
      ParameterKey=instanceId,ParameterValue="${DB_INSTANCE_ID}" \
      ParameterKey=database,ParameterValue="${DBNAME}" \
      ParameterKey=cloudWatchNamespace,ParameterValue="${DB2_SYSTEM_NS}" \
      "ParameterKey=rdsDb2DashboardStack,ParameterValue=https://${TARGET_BUCKET}.s3.${REGION}.amazonaws.com/CFN/rds-db2-dashboard.yml" \
    --template-url "https://${TARGET_BUCKET}.s3.${REGION}.amazonaws.com/CFN/rds-db2monitor-main.yml" \
    ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" 2>&1) || true

  if echo "$out" | grep -qE 'StackId|arn:aws:cloudformation'; then
    local final_status; final_status=$(wait_for_stack "$stack_name" "stack-create-complete")
    if [[ "$final_status" == "CREATE_COMPLETE" ]]; then
      log_success "Dashboard stack deployed: $stack_name"
      local url; url=$(cfn_output "$stack_name" "DashboardURL")
      [ -n "$url" ] && log_success "Dashboard URL: $url"
    else
      log_error "Dashboard stack failed: $final_status"
      aws cloudformation describe-stack-events --stack-name "$stack_name" \
        ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" \
        --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`].[LogicalResourceId,ResourceStatusReason]' \
        --output text 2>/dev/null | head -20 >&2 || true
      return 1
    fi
  else
    log_error "Failed to create dashboard stack: $out"
    return 1
  fi
}

# --- Ensure shared regional SNS topic exists (idempotent) ---
ensure_sns_topic() {
  local topic_name="DB2Mon-SNS-${REGION}"
  SNS_TOPIC_ARN=$(aws sns create-topic --name "$topic_name" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} --query TopicArn --output text)
  log_info "SNS topic ready: $SNS_TOPIC_ARN"
}

# --- Deploy EventBridge CFN ---
deploy_eventbridge_stack() {
  local stack_name="DB2-Dashboard-EventBridge-${DB_INSTANCE_ID}-${DBNAME}-${TAG}"
  local custom_name="${DB_INSTANCE_ID}-${DBNAME}-${TAG}"
  local status; status=$(stack_status "$stack_name")

  if [[ "$status" == "CREATE_COMPLETE" || "$status" == "UPDATE_COMPLETE" ]]; then
    log_success "EventBridge stack already deployed: $stack_name"
    return 0
  fi

  if [[ "$status" == *"ROLLBACK"* || "$status" == *"FAILED"* || "$status" == "DELETE_COMPLETE" ]]; then
    log_warning "Stack $stack_name is in $status — deleting and recreating..."
    aws cloudformation delete-stack --stack-name "$stack_name" --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} 2>/dev/null || true
    aws cloudformation wait stack-delete-complete --stack-name "$stack_name" --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} 2>/dev/null || true
    log_success "Deleted failed stack: $stack_name"
  fi

  # Check if the EventBridge rule name is already owned by a different stack
  local rule_name="db2mon-cw-${DB_INSTANCE_ID}-${DBNAME}-${TAG}"
  local owning_stack
  owning_stack=$(aws cloudformation describe-stack-resources \
    --physical-resource-id "$rule_name" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
    --query 'StackResourceDetail.StackName' --output text 2>/dev/null || true)
  if [ -n "$owning_stack" ] && [ "$owning_stack" != "None" ] && [ "$owning_stack" != "$stack_name" ]; then
    log_error "EventBridge rule '$rule_name' already exists in stack: $owning_stack"
    log_error "Delete that stack first:"
    log_error "  aws cloudformation delete-stack --stack-name $owning_stack --region $REGION ${PROFILE_ARG:+$PROFILE_ARG}"
    return 1
  fi

  log_info "Deploying EventBridge stack: $stack_name"
  ensure_sns_topic
  local out
  out=$(aws cloudformation create-stack \
    --stack-name "$stack_name" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameters \
      ParameterKey=monitoredInstanceType,ParameterValue=rds \
      "ParameterKey=customName,ParameterValue=${custom_name}" \
      "ParameterKey=snsTopicArn,ParameterValue=${SNS_TOPIC_ARN}" \
      ParameterKey=secretName,ParameterValue="${SECRET_MANAGER_NAME}" \
      "ParameterKey=cloudWatchNamespace,ParameterValue=${DB2_SYSTEM_NS}" \
      "ParameterKey=kmsKeyArn,ParameterValue=${KMS_KEY_ARN:-}" \
    --template-url "https://${TARGET_BUCKET}.s3.${REGION}.amazonaws.com/CFN/create-db2mon-eventbridge.yml" \
    ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" 2>&1) || true

  if echo "$out" | grep -qE 'StackId|arn:aws:cloudformation'; then
    local final_status; final_status=$(wait_for_stack "$stack_name" "stack-create-complete")
    if [[ "$final_status" == "CREATE_COMPLETE" ]]; then
      log_success "EventBridge stack deployed: $stack_name"
      local cw_arn; cw_arn=$(cfn_output "$stack_name" "LambdaScheduleRuleCWArn")
      [ -n "$cw_arn" ] && log_info "CW Schedule ARN: $cw_arn"
    else
      log_error "EventBridge stack failed: $final_status"
      return 1
    fi
  else
    log_error "Failed to create EventBridge stack: $out"
    return 1
  fi
}

# --- Resolve VPC_ID + SG_ID from EC2 instance metadata (airgap bootstrap) ---
# Uses IMDS only — no AWS API calls, so works before any VPC endpoints exist.
resolve_vpc_from_metadata() {
  [ -n "$VPC_ID" ] && [ -n "$SG_ID" ] && return 0

  curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/ >/dev/null 2>&1 || return 0

  local token mac
  token=$(curl -sX PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" --connect-timeout 2 2>/dev/null || true)
  [ -z "$token" ] && return 0

  mac=$(curl -s -H "X-aws-ec2-metadata-token: $token" \
    http://169.254.169.254/latest/meta-data/network/interfaces/macs/ 2>/dev/null | head -1 | tr -d '/')
  [ -z "$mac" ] && return 0

  if [ -z "$VPC_ID" ]; then
    VPC_ID=$(curl -s -H "X-aws-ec2-metadata-token: $token" \
      "http://169.254.169.254/latest/meta-data/network/interfaces/macs/${mac}/vpc-id" 2>/dev/null || true)
    [ -n "$VPC_ID" ] && log_info "Resolved VPC from instance metadata: $VPC_ID"
  fi

  if [ -z "$SG_ID" ]; then
    # IMDS returns newline-separated SG IDs — take the first one
    SG_ID=$(curl -s -H "X-aws-ec2-metadata-token: $token" \
      "http://169.254.169.254/latest/meta-data/network/interfaces/macs/${mac}/security-group-ids" \
      2>/dev/null | head -1 || true)
    [ -n "$SG_ID" ] && log_info "Resolved SG from instance metadata: $SG_ID"
  fi

  if [ -z "$SUBNET_IDS" ]; then
    local subnet_id
    subnet_id=$(curl -s -H "X-aws-ec2-metadata-token: $token" \
      "http://169.254.169.254/latest/meta-data/network/interfaces/macs/${mac}/subnet-id" \
      2>/dev/null || true)
    [ -n "$subnet_id" ] && SUBNET_IDS="$subnet_id" && \
      log_info "Resolved subnet from instance metadata: $subnet_id"
  fi
}

# --- Main Install ---
install_db2_dashboard() {
  echo "============================================================="
  echo "  DB2 Dashboard Deployment"
  echo "============================================================="

  # Resolve VPC/SG early from IMDS (EC2 only) so ensure_service_endpoints
  # can run before any AWS service calls. Skipped on CloudShell (no IMDS).
  resolve_vpc_from_metadata

  if [ -n "$VPC_ID" ]; then
    echo "============================================================="
    log_info "Ensuring VPC service endpoints..."
    ensure_service_endpoints
  fi

  if ! list_db_instances; then
    log_error "No DB2 instances found. Exiting."
    return 1
  fi

  if [ -z "$DBNAME" ]; then
    local last_dbname=""
    [ -f "$LAST_DBNAME_FILE" ] && last_dbname=$(cat "$LAST_DBNAME_FILE" 2>/dev/null | tr -d '[:space:]')
    if [ -n "$last_dbname" ]; then
      read -p "  Enter database name to monitor (last used: $last_dbname): " DBNAME
      DBNAME=${DBNAME:-$last_dbname}
    else
      read -p "  Enter database name to monitor: " DBNAME
    fi
    DBNAME=$(echo "$DBNAME" | tr -d '[:space:]')
    [ -n "$DBNAME" ] && echo "$DBNAME" > "$LAST_DBNAME_FILE"
  fi
  get_tag
  SECRET_MANAGER_NAME="SM-${DB_INSTANCE_ID}-${DBNAME}-${TAG}"

  # Create secret if it doesn't exist, then load values
  if ! load_secret_values "$SECRET_MANAGER_NAME"; then
    create_secret || return 1
    load_secret_values "$SECRET_MANAGER_NAME" || { log_error "Failed to load secret after creation"; return 1; }
  fi

  # Register in SSM immediately — ensures cleanup works even if later steps fail
  register_in_ssm

  echo "============================================================="
  log_info "Setting up Lambda S3 bucket and uploading code..."
  setup_lambda_bucket

  # ensure_service_endpoints already ran above; re-run now that VPC_ID/SG_ID
  # are confirmed from the secret (may refine SG to the RDS instance's SG)
  echo "============================================================="
  log_info "Verifying VPC service endpoints post-secret..."
  ensure_service_endpoints

  echo "============================================================="
  log_info "Creating Lambda IAM role..."
  create_lambda_role

  echo "============================================================="
  log_info "Publishing Lambda layer..."
  create_lambda_layer

  echo "============================================================="
  log_info "Creating Lambda function..."
  create_lambda_function

  echo "============================================================="
  log_info "Deploying CloudWatch dashboard..."
  deploy_dashboard_stack

  echo "============================================================="
  log_info "Deploying EventBridge scheduler (starts disabled)..."
  deploy_eventbridge_stack

  echo "============================================================="
  log_success "Deployment complete!"
  log_info "Enabling EventBridge scheduler by using \"./${SCRIPT_MONITOR} --module start\""
  echo
  # Write populated env file for future pipeline use
  local env_path="./db2monitor.env"
  cat > "$env_path" << EOF
# =============================================================================
# db2monitor.env - Generated after deployment on $(date '+%Y-%m-%d %H:%M:%S')
# You can also deploy dashboard through a pipeline. Make necessary edits to $env_path
# Then run: source $env_path && $SCRIPT_PATH
# =============================================================================
export PROFILE="${PROFILE}"
export REGION="${REGION}"
export DB_INSTANCE_ID="${DB_INSTANCE_ID}"
export DBNAME="${DBNAME}"
export TAG="${TAG}"
export PASSWORD=""
EOF
  log_info "Pipeline config saved: $env_path"
  echo
  local schedule="db2mon-cw-${DB_INSTANCE_ID}-${DBNAME}-${TAG}"
  update_scheduler "$schedule" "enabled"
  report_elapsed_time
  echo "============================================================="
}

# --- Start/Stop monitoring ---
update_scheduler() {
  local schedule_name="$1" desired_state="$2"
  local details group_name
  group_name=$(aws scheduler list-schedules \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} \
    --query "Schedules[?Name=='${schedule_name}'].GroupName" \
    --output text 2>/dev/null | head -1)
  if [ -z "$group_name" ] || [ "$group_name" = "None" ]; then
    log_warning "Schedule not found: $schedule_name"
    log_info "Tip: run manually: aws scheduler list-schedules --region $REGION ${PROFILE_ARG:+$PROFILE_ARG}"
    return 1
  fi
  if ! details=$(aws scheduler get-schedule --name "$schedule_name" \
    --group-name "$group_name" --output json \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} 2>/dev/null) || [ -z "$details" ]; then
    log_warning "Schedule not found: $schedule_name (group=$group_name)"
    return 1
  fi

  local current_state; current_state=$(echo "$details" | jq -r '.State')
  local upper_state; upper_state=$(echo "$desired_state" | tr '[:lower:]' '[:upper:]')

  if [ "$current_state" == "$upper_state" ]; then
    log_info "Schedule '$schedule_name' already $current_state"
    return 0
  fi

  aws scheduler update-schedule \
    --name "$schedule_name" \
    --group-name "$group_name" --output json \
    --schedule-expression "$(echo "$details" | jq -r '.ScheduleExpression')" \
    --flexible-time-window "$(echo "$details" | jq -c '.FlexibleTimeWindow')" \
    --target "$(echo "$details" | jq -c '.Target')" \
    --state "$upper_state" \
    --region "$REGION" ${PROFILE_ARG:+$PROFILE_ARG} >/dev/null
  log_success "Schedule '$schedule_name' set to $upper_state"
}

# --- Select registered instance from SSM ---
select_registered_instance() {
  local registry
  registry=$(aws ssm get-parameter --name "/db2mon/instances" ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" \
    --query 'Parameter.Value' --output text 2>/dev/null || true)
  [ -z "$registry" ] || [ "$registry" = "None" ] && { log_error "No instances in SSM registry"; return 1; }
  IFS=',' read -ra _secrets <<< "$registry"
  if [ ${#_secrets[@]} -eq 1 ]; then
    SECRET_MANAGER_NAME="${_secrets[0]}"
  else
    echo "Registered instances:" >&2
    for i in "${!_secrets[@]}"; do echo "  $((i+1)). ${_secrets[$i]}"; done >&2
    local choice=0
    while [[ ! "$choice" =~ ^[0-9]+$ ]] || [ "$choice" -lt 1 ] || [ "$choice" -gt ${#_secrets[@]} ]; do
      read -p "Select (1-${#_secrets[@]}): " choice
    done
    SECRET_MANAGER_NAME="${_secrets[$((choice-1))]}"
  fi
  local secret_json
  secret_json=$(aws secretsmanager get-secret-value --secret-id "$SECRET_MANAGER_NAME" \
    ${PROFILE_ARG:+$PROFILE_ARG} --region "$REGION" --query SecretString --output text 2>/dev/null)
  DB_INSTANCE_ID=$(echo "$secret_json" | jq -r '.dbInstanceIdentifier // ""')
  DBNAME=$(echo "$secret_json"         | jq -r '.database // ""')
  log_info "Selected: $DB_INSTANCE_ID / $DBNAME"
}

start_db2_monitoring() {
  select_registered_instance || return 1
  local schedule="db2mon-cw-${DB_INSTANCE_ID}-${DBNAME}-${TAG}"
  update_scheduler "$schedule" "enabled"
  log_success "Monitoring started for $DB_INSTANCE_ID / $DBNAME"
}

stop_db2_monitoring() {
  select_registered_instance || return 1
  local schedule="db2mon-cw-${DB_INSTANCE_ID}-${DBNAME}-${TAG}"
  update_scheduler "$schedule" "disabled"
  log_success "Monitoring stopped for $DB_INSTANCE_ID / $DBNAME"
}

refresh_db2_monitoring() {
  select_registered_instance || return 1
  load_secret_values "$SECRET_MANAGER_NAME" && log_success "Secret values refreshed" || log_warning "Could not refresh secret"
}

# --- Argument Parsing ---
INSTALL_MODULE=${INSTALL_MODULE:-""}

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case $1 in
      -m|--module)    INSTALL_MODULE="$2"; shift 2 ;;
      -r|--region)    REGION="$2"; shift 2 ;;
      -p|--profile)   PROFILE="$2"; shift 2 ;;
      -b|--bucket)    BUCKET="$2"; shift 2 ;;
      --verbose)      VERBOSE=true; shift ;;
      --check-permissions) CHECK_PERMISSIONS=true; shift ;;
      --non-interactive)   INTERACTIVE_MODE=false; shift ;;
      -h|--help)
        echo "Usage: [BUCKET=<bucket>] [REGION=<region>] $0 [--module install|start|stop|refresh] [--region REGION] [--profile PROFILE] [--verbose] [--check-permissions]"
        echo "  No BUCKET = online mode  (downloads artifacts from public S3)"
        echo "  BUCKET set = airgap mode (uses artifacts from private bucket)"
        exit 0 ;;
      *) log_error "Unknown option: $1"; exit 1 ;;
    esac
  done
}

# --- Entry Point ---
main() {
  parse_arguments "$@"

  if $CURL_PIPE; then
    if [ -z "$INSTALL_MODULE" ] && [ -z "$DB_INSTANCE_ID" ]; then
      handle_curl_pipe_download
      exit 0
    fi
    INTERACTIVE_MODE=false
  fi

  check_prerequisites
  setup_aws_environment  # includes ensure_jq
  ! $CURL_PIPE && ensure_companion_scripts
  check_permissions  # exits if --check-permissions

  if [ -z "$INSTALL_MODULE" ]; then
    if [[ "$INTERACTIVE_MODE" == "false" ]] || [ -n "$DB_INSTANCE_ID" ]; then
      INSTALL_MODULE="install"
    else
      local choice=0
      while [ "$choice" -lt 1 ] || [ "$choice" -gt 5 ]; do
        echo "Select module:" >&2
        echo "1) Install DB2 dashboards (default)" >&2
        echo "2) Start monitoring" >&2
        echo "3) Stop monitoring" >&2
        echo "4) Refresh secret values" >&2
        echo "5) Exit" >&2
        read -p "Choice (1-5): " choice; choice=${choice:-1}
      done
      case $choice in
        1) INSTALL_MODULE="install" ;;
        2) INSTALL_MODULE="start" ;;
        3) INSTALL_MODULE="stop" ;;
        4) INSTALL_MODULE="refresh" ;;
        5) log_info "Exiting."; exit 0 ;;
      esac
    fi
  fi

  case "$INSTALL_MODULE" in
    install) install_db2_dashboard ;;
    start)   start_db2_monitoring ;;
    stop)    stop_db2_monitoring ;;
    refresh) refresh_db2_monitoring ;;
    *) log_error "Unknown module: $INSTALL_MODULE"; exit 1 ;;
  esac
}

trap 'echo "Interrupted"; exit 130' INT TERM

if [[ "${BASH_SOURCE[0]:-$0}" == "${0}" ]]; then
  main "$@"
fi
