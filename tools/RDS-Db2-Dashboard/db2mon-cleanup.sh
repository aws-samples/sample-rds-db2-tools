#!/usr/bin/env bash
# Db2 Monitor - Cleanup Script
set -eo pipefail
export AWS_PAGER=""

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()    { echo -e "${BLUE}[   INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[ WARN ]${NC} $1"; }
log_error()   { echo -e "${RED}[  ERROR]${NC} $1"; }

# --- Auth & Region ---
# Credential precedence:
#   1. Exported AWS_ACCESS_KEY_ID/SECRET  → use immediately, skip everything
#   2. PROFILE explicitly set             → test with sts get-caller-identity, exit if fails
#   3. No profile set                     → probe CloudShell IMDS → EC2 IMDS → exit if neither works
CREDS_FROM_METADATA=false
PROFILE=${PROFILE:-""}

if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
  # Priority 1: exported env var credentials
  PROFILE_ARG=""
elif [ -n "$PROFILE" ]; then
  # Priority 2: explicit profile — validate immediately, exit on failure
  PROFILE_ARG="--profile $PROFILE"
elif curl -s --connect-timeout 1 http://127.0.0.1:1338/latest/meta-data/ >/dev/null 2>&1; then
  # Priority 3a: CloudShell IMDS
  log_info "Detected AWS CloudShell environment"
  _token=$(curl -s --connect-timeout 2 -X PUT "http://127.0.0.1:1338/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
  _creds=$(curl -s --connect-timeout 2 -H "Authorization: $_token" \
    "http://127.0.0.1:1338/latest/meta-data/container/security-credentials")
  export AWS_ACCESS_KEY_ID=$(echo "$_creds"     | jq -r .AccessKeyId)
  export AWS_SECRET_ACCESS_KEY=$(echo "$_creds" | jq -r .SecretAccessKey)
  export AWS_SESSION_TOKEN=$(echo "$_creds"     | jq -r .Token)
  PROFILE_ARG=""
  CREDS_FROM_METADATA=true
elif curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/ >/dev/null 2>&1; then
  # Priority 3b: EC2 IMDSv2
  log_info "Detected EC2 environment"
  _token=$(curl -s --connect-timeout 2 -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
  _role=$(curl -s --connect-timeout 2 -H "X-aws-ec2-metadata-token: $_token" \
    http://169.254.169.254/latest/meta-data/iam/security-credentials/)
  if [ -n "$_role" ]; then
    _creds=$(curl -s --connect-timeout 2 -H "X-aws-ec2-metadata-token: $_token" \
      "http://169.254.169.254/latest/meta-data/iam/security-credentials/$_role")
    export AWS_ACCESS_KEY_ID=$(echo "$_creds"     | jq -r .AccessKeyId)
    export AWS_SECRET_ACCESS_KEY=$(echo "$_creds" | jq -r .SecretAccessKey)
    export AWS_SESSION_TOKEN=$(echo "$_creds"     | jq -r .Token)
    PROFILE_ARG=""
    CREDS_FROM_METADATA=true
  fi
else
  log_error "No credentials found. Set AWS_ACCESS_KEY_ID/SECRET, export PROFILE=<name>, or run from CloudShell/EC2."
  exit 1
fi

# Resolve REGION: arg > env > EC2 metadata > aws configure
if [ -z "${REGION:-}" ]; then
  if [ -n "${AWS_DEFAULT_REGION:-}" ]; then
    REGION="$AWS_DEFAULT_REGION"
  elif [ "${_ENV_TYPE:-}" = "ec2" ]; then
    _token2=$(curl -s --connect-timeout 2 -X PUT "http://169.254.169.254/latest/api/token" \
      -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" 2>/dev/null)
    REGION=$(curl -s --connect-timeout 2 -H "X-aws-ec2-metadata-token: $_token2" \
      http://169.254.169.254/latest/meta-data/placement/region 2>/dev/null)
  else
    REGION=$(aws configure get region $PROFILE_ARG 2>/dev/null || true)
  fi
fi
[ -z "$REGION" ] && { log_error "REGION not set. Export REGION= and retry."; exit 1; }

if [ "${CREDS_FROM_METADATA:-false}" = "false" ]; then
  if ! aws sts get-caller-identity $PROFILE_ARG --region "$REGION" >/dev/null 2>&1; then
    if [ -n "$PROFILE" ]; then
      log_error "Profile '$PROFILE' credentials are invalid or expired."
      log_error "Run: aws sts get-caller-identity --profile $PROFILE"
      log_error "Either refresh credentials for '$PROFILE' or unset PROFILE to use instance metadata."
    else
      log_error "AWS credentials invalid. Set AWS_ACCESS_KEY_ID/SECRET or export PROFILE=<name>."
    fi
    exit 1
  fi
  ACCOUNT_ID=$(aws sts get-caller-identity $PROFILE_ARG --region "$REGION" --query Account --output text)
fi
# Metadata env: use regional STS endpoint (routes via VPC endpoint on EC2, public on CloudShell)
if [ -z "${ACCOUNT_ID:-}" ]; then
  ACCOUNT_ID=$(aws sts get-caller-identity $PROFILE_ARG \
    --endpoint-url "https://sts.${REGION}.amazonaws.com" \
    --cli-connect-timeout 5 \
    --query Account --output text 2>/dev/null || true)
fi
SSM_PARAM="/db2mon/instances"

# --- Read SSM registry ---
log_info "Reading instance registry from SSM: $SSM_PARAM"
registry=$(aws ssm get-parameter --name "$SSM_PARAM" $PROFILE_ARG --region "$REGION" \
  --query 'Parameter.Value' --output text 2>/dev/null || true)

if [ -z "$registry" ] || [ "$registry" = "None" ]; then
  log_error "No instances registered in $SSM_PARAM. Nothing to clean up."; exit 1
fi

# --- Parse and display ---
IFS=',' read -ra secrets <<< "$registry"
echo
echo "Registered db2mon instances:"
i=1
for s in "${secrets[@]}"; do
  # Secret format: SM-<DB_INSTANCE_ID>-<DBNAME>-<TAG>
  # Strip SM- prefix for display
  trimmed="${s#SM-}"
  echo "  $i. $s  ($trimmed)"
  ((i++))
done

echo
while true; do
  read -p "Select instance to clean up dashboard (1-${#secrets[@]}): " choice
  choice=${choice:-1}
  [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#secrets[@]}" ] && break
  log_error "Invalid choice. Enter a number between 1 and ${#secrets[@]}."
done

SECRET="${secrets[$((choice-1))]}"

# --- Derive identifiers from secret ---
log_info "Reading secret: $SECRET"
secret_json=$(aws secretsmanager get-secret-value --secret-id "$SECRET" \
  $PROFILE_ARG --region "$REGION" --query SecretString --output text 2>/dev/null || true)

if [ -z "$secret_json" ]; then
  log_warning "Secret not found: $SECRET (may have been deleted manually)"
  read -p "Remove $SECRET from SSM registry? (y/N): " purge
  if [[ "$purge" =~ ^[yY]$ ]]; then
    remaining=$(printf '%s\n' "${secrets[@]}" | grep -vx "$SECRET" | paste -sd ',' - || true)
    if [ -z "$remaining" ]; then
      aws ssm delete-parameter --name "$SSM_PARAM" $PROFILE_ARG --region "$REGION" 2>/dev/null || true
      log_success "SSM registry empty — parameter deleted"
    else
      aws ssm put-parameter --name "$SSM_PARAM" --type StringList --value "$remaining" \
        --overwrite $PROFILE_ARG --region "$REGION" >/dev/null
      log_success "Removed $SECRET from SSM registry"
    fi
  fi
  exit 0
fi

DB_INSTANCE_ID=$(echo "$secret_json" | jq -r '.dbInstanceIdentifier // ""')
DBNAME=$(echo "$secret_json"         | jq -r '.database // ""')
TAG=$(echo "$secret_json"            | jq -r '.tag // ""')
VPC_ID=$(echo "$secret_json"         | jq -r '.vpcID // ""')

if [ -z "$DB_INSTANCE_ID" ] || [ -z "$DBNAME" ] || [ -z "$TAG" ]; then
  log_error "Could not read required fields from secret. Got: instance=$DB_INSTANCE_ID dbname=$DBNAME tag=$TAG"
  exit 1
fi

DASHBOARD_STACK="DB2-Dashboard-${DB_INSTANCE_ID}-${DBNAME}-${TAG}"
EB_STACK="DB2-Dashboard-EventBridge-${DB_INSTANCE_ID}-${DBNAME}-${TAG}"
EB_SCHEDULE_CW="db2mon-cw-${DB_INSTANCE_ID}-${DBNAME}-${TAG}"
EB_SCHEDULE_S3="db2mon-s3-${DB_INSTANCE_ID}-${DBNAME}-${TAG}"
EXPORT_STACK="db2mon-export-cfn-${REGION}"
LAMBDA_FUNCTION="DB2Mon-Lambda-Function-${REGION}"
LAMBDA_LAYER="DB2Mon-Layer-${REGION}"
LAMBDA_ROLE="DB2Mon-Lambda-Role-${REGION}"

TARGET_BUCKET="lambda-functions-${ACCOUNT_ID}-${REGION}"
echo "  Instance : $DB_INSTANCE_ID"
echo "  DB Name  : $DBNAME"
echo "  TAG      : $TAG"
echo "  Secret   : $SECRET"
echo
read -p "Proceed? (y/N): " confirm
[[ "$confirm" =~ ^[yY]$ ]] || { log_info "Aborted."; exit 0; }

# --- Helper ---
delete_stack() {
  local name="$1"
  if aws cloudformation describe-stacks --stack-name "$name" $PROFILE_ARG --region "$REGION" &>/dev/null; then
    log_info "Deleting stack: $name"
    aws cloudformation delete-stack --stack-name "$name" $PROFILE_ARG --region "$REGION"
    aws cloudformation wait stack-delete-complete --stack-name "$name" $PROFILE_ARG --region "$REGION"
    log_success "Deleted: $name"
  else
    log_info "Not found (skip): $name"
  fi
}

# --- 1. EventBridge schedules ---
for sched in "$EB_SCHEDULE_CW" "$EB_SCHEDULE_S3"; do
  if aws scheduler get-schedule --name "$sched" $PROFILE_ARG --region "$REGION" &>/dev/null; then
    log_info "Deleting schedule: $sched"
    aws scheduler delete-schedule --name "$sched" $PROFILE_ARG --region "$REGION"
    log_success "Deleted: $sched"
  else
    log_info "Not found (skip): $sched"
  fi
done

# --- 2. CFN stacks ---
delete_stack "$EB_STACK"
delete_stack "$DASHBOARD_STACK"

# --- 3. Secret (force, no retention) ---
log_info "Deleting secret: $SECRET"
aws secretsmanager delete-secret --secret-id "$SECRET" --force-delete-without-recovery \
  $PROFILE_ARG --region "$REGION"
log_success "Deleted: $SECRET"

# --- 4. Optional: Lambda / bucket / VPC endpoints ---
echo
read -p "Also delete Lambda function, layer, role, S3 bucket and VPC endpoints? (y/N): " deep
if [[ "$deep" =~ ^[yY]$ ]]; then

  # Lambda function
  if aws lambda get-function --function-name "$LAMBDA_FUNCTION" $PROFILE_ARG --region "$REGION" &>/dev/null; then
    aws lambda delete-function --function-name "$LAMBDA_FUNCTION" $PROFILE_ARG --region "$REGION"
    log_success "Deleted Lambda: $LAMBDA_FUNCTION"
  fi

  # Lambda layer versions
  versions=$(aws lambda list-layer-versions --layer-name "$LAMBDA_LAYER" $PROFILE_ARG --region "$REGION" \
    --query 'LayerVersions[*].Version' --output text 2>/dev/null || true)
  for v in $versions; do
    aws lambda delete-layer-version --layer-name "$LAMBDA_LAYER" --version-number "$v" $PROFILE_ARG --region "$REGION"
    log_success "Deleted layer version: $LAMBDA_LAYER:$v"
  done

  # Lambda IAM role — delete via CFN export stack
  role_stack="db2mon-export-cfn-${REGION}"
  if aws cloudformation describe-stacks --stack-name "$role_stack" $PROFILE_ARG --region "$REGION" &>/dev/null; then
    log_info "Deleting Lambda role CFN stack: $role_stack"
    aws cloudformation delete-stack --stack-name "$role_stack" $PROFILE_ARG --region "$REGION"
    aws cloudformation wait stack-delete-complete --stack-name "$role_stack" $PROFILE_ARG --region "$REGION"
    log_success "Deleted: $role_stack"
  else
    log_info "Not found (skip): $role_stack"
  fi

  # S3 bucket (delete all versions first due to versioning)
  if aws s3api head-bucket --bucket "$TARGET_BUCKET" $PROFILE_ARG --region "$REGION" &>/dev/null; then
    _ver="" _del=""
    _ver=$(aws s3api list-object-versions --bucket "$TARGET_BUCKET" $PROFILE_ARG --region "$REGION" \
      --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null)
    [ "$(echo "$_ver" | jq '.Objects | length')" -gt 0 ] && \
      aws s3api delete-objects --bucket "$TARGET_BUCKET" $PROFILE_ARG --region "$REGION" \
        --delete "$_ver" >/dev/null 2>/dev/null || true
    _del=$(aws s3api list-object-versions --bucket "$TARGET_BUCKET" $PROFILE_ARG --region "$REGION" \
      --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null)
    [ "$(echo "$_del" | jq '.Objects | length')" -gt 0 ] && \
      aws s3api delete-objects --bucket "$TARGET_BUCKET" $PROFILE_ARG --region "$REGION" \
        --delete "$_del" >/dev/null 2>/dev/null || true
    aws s3api delete-bucket --bucket "$TARGET_BUCKET" $PROFILE_ARG --region "$REGION"
    log_success "Deleted bucket: $TARGET_BUCKET"
  fi

  # VPC endpoints
  if [ -n "$VPC_ID" ]; then
    ep_ids=$(aws ec2 describe-vpc-endpoints \
      --filters "Name=vpc-id,Values=${VPC_ID}" \
                "Name=vpc-endpoint-state,Values=available,pending" \
                "Name=vpc-endpoint-type,Values=Interface" \
      $PROFILE_ARG --region "$REGION" \
      --query "VpcEndpoints[?contains(ServiceName,'secretsmanager') || contains(ServiceName,'monitoring') || contains(ServiceName,'logs') || contains(ServiceName,'lambda') || contains(ServiceName,'rds') || contains(ServiceName,'ec2') || contains(ServiceName,'sns') || contains(ServiceName,'sqs') || contains(ServiceName,'scheduler')].VpcEndpointId" \
      --output text 2>/dev/null || true)
    if [ -n "$ep_ids" ]; then
      aws ec2 delete-vpc-endpoints --vpc-endpoint-ids $ep_ids $PROFILE_ARG --region "$REGION" >/dev/null 2>&1 \
        && log_success "Deleted VPC endpoints: $ep_ids" \
        || log_warning "Could not delete VPC endpoints (add ec2:DeleteVpcEndpoints to instance role) — skipping"
    fi
  fi

  # Export stack (only safe to delete when no more instances)
  remaining_after=$(aws ssm get-parameter --name "$SSM_PARAM" $PROFILE_ARG --region "$REGION" \
    --query 'Parameter.Value' --output text 2>/dev/null || true)
  if [ -z "$remaining_after" ] || [ "$remaining_after" = "None" ]; then
    delete_stack "$EXPORT_STACK"
  else
    log_warning "Other instances still registered — skipping Export stack deletion"
  fi
fi

# --- 5. Remove from SSM registry (last — so re-run works if deep cleanup failed) ---
remaining=$(printf '%s\n' "${secrets[@]}" | grep -vx "$SECRET" | paste -sd ',' - || true)
if [ -z "$remaining" ]; then
  aws ssm delete-parameter --name "$SSM_PARAM" $PROFILE_ARG --region "$REGION" 2>/dev/null || true
  log_success "SSM registry empty — parameter deleted"
else
  aws ssm put-parameter --name "$SSM_PARAM" --type StringList --value "$remaining" \
    --overwrite $PROFILE_ARG --region "$REGION" >/dev/null
  log_success "Removed $SECRET from SSM registry"
fi

# --- 5b. Remove VPC endpoint cache ---
if [ -n "$VPC_ID" ]; then
  aws ssm delete-parameter --name "/db2mon/endpoints-ready/${VPC_ID}" \
    $PROFILE_ARG --region "$REGION" 2>/dev/null || true
  log_success "Cleared VPC endpoint cache: /db2mon/endpoints-ready/${VPC_ID}"
fi

echo
log_success "Cleanup complete for $DB_INSTANCE_ID / $DBNAME."
