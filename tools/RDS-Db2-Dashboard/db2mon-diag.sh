#!/usr/bin/env bash
# =============================================================================
# db2mon-diag.sh — Diagnose DB2Mon deployment issues
#
# Checks:
#   1.  AWS credentials & region
#   2.  Lambda function — exists, VPC config, KMS key, last invocation errors
#   3.  VPC DNS attributes (required for Interface endpoints)
#   4.  VPC endpoints — existence and state for all required services
#   5.  Security group — port 443 egress scope (0.0.0.0/0 vs VPC-CIDR-only)
#   5b. Secrets Manager endpoint inbound 443 from Lambda SG
#   6.  Subnet available IPs
#   7.  Secrets Manager secret — exists and readable
#   8.  S3 bucket — exists and Lambda role can write
#   9.  EventBridge schedules — state (ENABLED/DISABLED)
#  10.  CloudWatch log group — recent errors
#
# Usage:
#   REGION=us-west-1 ACCOUNT_ID=123456789012 ./db2mon-diag.sh
#   REGION=us-west-1 ACCOUNT_ID=123456789012 DB_INSTANCE_ID=mydb ./db2mon-diag.sh
#   ./db2mon-diag.sh --region us-west-1 --instance mydb --secret SM-mydb-DB2DB-VIZ
# =============================================================================

if [ -z "$BASH_VERSION" ]; then exec bash "$0" "$@"; fi
set -eo pipefail
export AWS_PAGER=""

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

PASS=0; WARN=0; FAIL=0

ok()   { echo -e "  ${GREEN}[  PASS]${NC} $1"; PASS=$((PASS+1)); }
warn() { echo -e "  ${YELLOW}[  WARN]${NC} $1"; WARN=$((WARN+1)); }
fail() { echo -e "  ${RED}[  FAIL]${NC} $1"; FAIL=$((FAIL+1)); }
info() { echo -e "  ${BLUE}[  INFO]${NC} $1"; }
section() { echo; echo -e "${BOLD}${CYAN}=== $1 ===${NC}"; }

PROFILE=${PROFILE:-""}
REGION=${REGION:-""}
DB_INSTANCE_ID=${DB_INSTANCE_ID:-""}
DBNAME=${DBNAME:-""}
TAG=${TAG:-""}
SECRET_NAME=${SECRET_NAME:-""}
LAMBDA_FUNCTION=${LAMBDA_FUNCTION:-""}
ENV_FILE=${ENV_FILE:-""}

# --- Argument parsing ---
while [[ $# -gt 0 ]]; do
  case $1 in
    --region)    REGION="$2";         shift 2 ;;
    --instance)  DB_INSTANCE_ID="$2"; shift 2 ;;
    --secret)    SECRET_NAME="$2";    shift 2 ;;
    --lambda)    LAMBDA_FUNCTION="$2"; shift 2 ;;
    --profile)   PROFILE="$2";        shift 2 ;;
    --env)       ENV_FILE="$2";        shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--region REGION] [--instance DB_INSTANCE_ID] [--secret SECRET_NAME]"
      echo "           [--lambda LAMBDA_FUNCTION] [--profile PROFILE] [--env ENV_FILE]"
      echo
      echo "  Auto-loads ./db2monitor.env if present (or specify --env path)."
      echo "  Derives SECRET_NAME from DB_INSTANCE_ID/DBNAME/TAG when not specified."
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# --- Auto-load env file ---
# Prefer explicit --env, then ./db2monitor.env next to this script, then cwd
_env_candidates=()
[ -n "$ENV_FILE" ] && _env_candidates+=("$ENV_FILE")
_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")"; pwd)"
_env_candidates+=("${_script_dir}/db2monitor.env" "./db2monitor.env")
for _f in "${_env_candidates[@]}"; do
  if [ -f "$_f" ]; then
    echo -e "  ${BLUE}[  INFO]${NC} Loading env: $_f"
    # Source only export lines to avoid side-effects
    while IFS= read -r _line; do
      [[ "$_line" =~ ^export\ ([A-Z_]+)=\"(.*)\"$ ]] || continue
      _k="${BASH_REMATCH[1]}"; _v="${BASH_REMATCH[2]}"
      # Only set if not already set by CLI args or environment
      case "$_k" in
        ACCOUNT_ID)    [ -z "${ACCOUNT_ID:-}"  ] && ACCOUNT_ID="$_v" ;;
        PROFILE)       [ -z "$PROFILE"       ] || [ "$PROFILE" = "default" ] && PROFILE="$_v" ;;
        REGION)        [ -z "$REGION"        ] && REGION="$_v" ;;
        DB_INSTANCE_ID)[ -z "$DB_INSTANCE_ID"] && DB_INSTANCE_ID="$_v" ;;
        DBNAME)        [ -z "$DBNAME"        ] && DBNAME="$_v" ;;
        TAG)           [ -z "$TAG"           ] && TAG="$_v" ;;
      esac
    done < "$_f"
    break
  fi
done

# --- Pre-flight: REGION and ACCOUNT_ID are required ---
if [ -z "$REGION" ]; then
  echo -e "  ${RED}[ERROR]${NC} REGION is not set."
  echo "  Set it via: REGION=us-west-1 ./db2mon-diag.sh"
  echo "           or: ./db2mon-diag.sh --region us-west-1"
  echo "           or: export REGION=us-west-1 in db2monitor.env"
  exit 1
fi
if [ -z "${ACCOUNT_ID:-}" ]; then
  ACCOUNT_ID=$(aws sts get-caller-identity --region "$REGION" $PROFILE_ARG \
    --query Account --output text --cli-connect-timeout 3 2>/dev/null || true)
  if [ -z "$ACCOUNT_ID" ]; then
    echo -e "  ${RED}[ERROR]${NC} ACCOUNT_ID is not set and could not be auto-detected."
    echo "  Set it via: ACCOUNT_ID=123456789012 REGION=$REGION ./db2mon-diag.sh"
    echo "           or: export ACCOUNT_ID=123456789012 in db2monitor.env"
    exit 1
  fi
fi

# --- Derive SECRET_NAME and LAMBDA_FUNCTION from loaded values ---
if [ -z "$SECRET_NAME" ] && [ -n "$DB_INSTANCE_ID" ] && [ -n "$DBNAME" ] && [ -n "$TAG" ]; then
  SECRET_NAME="SM-${DB_INSTANCE_ID}-${DBNAME}-${TAG}"
fi

# Credential precedence:
#   1. Exported AWS_ACCESS_KEY_ID/SECRET  → use immediately, skip everything
#   2. PROFILE explicitly set             → test with sts get-caller-identity, exit if fails
#   3. No profile set                     → probe CloudShell IMDS → EC2 IMDS → exit if neither works
_CREDS_FROM_METADATA=false
_ENV_TYPE="local"  # local | cloudshell | ec2

if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
  # Priority 1: exported env var credentials
  PROFILE_ARG=""
elif [ -n "$PROFILE" ]; then
  # Priority 2: explicit profile — validate in section 1, exit on failure
  PROFILE_ARG="--profile $PROFILE"
elif curl -s --connect-timeout 1 http://127.0.0.1:1338/latest/meta-data/ >/dev/null 2>&1; then
  # Priority 3a: CloudShell IMDS
  info "Detected AWS CloudShell environment"
  _ENV_TYPE="cloudshell"
  _token=$(curl -s --connect-timeout 2 -X PUT "http://127.0.0.1:1338/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
  _creds=$(curl -s --connect-timeout 2 -H "Authorization: $_token" \
    "http://127.0.0.1:1338/latest/meta-data/container/security-credentials")
  export AWS_ACCESS_KEY_ID=$(echo "$_creds"     | jq -r .AccessKeyId)
  export AWS_SECRET_ACCESS_KEY=$(echo "$_creds" | jq -r .SecretAccessKey)
  export AWS_SESSION_TOKEN=$(echo "$_creds"     | jq -r .Token)
  PROFILE_ARG=""
  _CREDS_FROM_METADATA=true
elif curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/ >/dev/null 2>&1; then
  # Priority 3b: EC2 IMDSv2
  info "Detected EC2 environment"
  _ENV_TYPE="ec2"
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
    _CREDS_FROM_METADATA=true
  fi
else
  fail "No credentials found. Set AWS_ACCESS_KEY_ID/SECRET, export PROFILE=<name>, or run from CloudShell/EC2."
  exit 1
fi

# =============================================================================
# 1. AWS Credentials & Region
# =============================================================================
section "1. AWS Credentials & Region"

if [ -z "$REGION" ]; then
  REGION=$(aws configure get region $PROFILE_ARG 2>/dev/null || true)
  if [ -z "$REGION" ]; then
    read -p "  Enter AWS region (e.g. us-west-1): " REGION
  fi
fi

if [ "${_CREDS_FROM_METADATA:-false}" = "false" ]; then
  if aws sts get-caller-identity $PROFILE_ARG --region "$REGION" >/dev/null 2>&1; then
    ACCOUNT_ID=$(aws sts get-caller-identity $PROFILE_ARG --region "$REGION" --query Account --output text)
    CALLER_ARN=$(aws sts get-caller-identity $PROFILE_ARG --region "$REGION" --query Arn --output text)
  else
    if [ -n "$PROFILE" ]; then
      fail "Profile '$PROFILE' credentials are invalid or expired."
      echo "  Run: aws sts get-caller-identity --profile $PROFILE"
      echo "  Either refresh credentials for '$PROFILE' or unset PROFILE to use instance metadata."
    else
      fail "AWS credentials invalid. Set AWS_ACCESS_KEY_ID/SECRET or export PROFILE=<name>."
    fi
    exit 1
  fi
else
  # CloudShell/EC2 metadata — use regional STS endpoint (works via VPC endpoint on EC2, public on CloudShell)
  _sts_endpoint="https://sts.${REGION}.amazonaws.com"
  ACCOUNT_ID=${ACCOUNT_ID:-$(aws sts get-caller-identity \
    --endpoint-url "$_sts_endpoint" \
    --cli-connect-timeout 5 \
    --query Account --output text 2>/dev/null || echo "")}
  CALLER_ARN=$(aws sts get-caller-identity \
    --endpoint-url "$_sts_endpoint" \
    --cli-connect-timeout 5 \
    --query Arn --output text 2>/dev/null || echo "${_ENV_TYPE}-metadata-credentials")
fi
ok "AWS credentials valid | Account: $ACCOUNT_ID | Region: $REGION"
info "Caller: $CALLER_ARN"

TARGET_BUCKET="lambda-functions-${ACCOUNT_ID}-${REGION}"

# =============================================================================
# 1b. IAM Deployment Permissions
# =============================================================================
section "1b. IAM Deployment Permissions"

_required_actions=(
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

# iam:SimulatePrincipalPolicy uses the global IAM endpoint — no VPC endpoint.
# Treat timeout or error as "not available" rather than failing.
_sim_result=$(aws iam simulate-principal-policy \
  --policy-source-arn "$CALLER_ARN" \
  --action-names "iam:SimulatePrincipalPolicy" \
  $PROFILE_ARG --region "$REGION" --cli-connect-timeout 5 \
  --query 'EvaluationResults[0].EvalDecision' --output text 2>/dev/null || echo "timeout")

if [ "$_sim_result" != "allowed" ]; then
  warn "iam:SimulatePrincipalPolicy not available (IAM has no VPC endpoint) — skipping permission simulation"
  info "Run './db2monitor.sh --check-permissions' from a machine with internet access to verify"
else
  _missing=()
  for _action in "${_required_actions[@]}"; do
    _result=$(aws iam simulate-principal-policy \
      --policy-source-arn "$CALLER_ARN" \
      --action-names "$_action" \
      $PROFILE_ARG --region "$REGION" --cli-connect-timeout 5 \
      --query 'EvaluationResults[0].EvalDecision' --output text 2>/dev/null || echo "timeout")
    [ "$_result" != "allowed" ] && _missing+=("$_action")
  done
  if [ ${#_missing[@]} -eq 0 ]; then
    ok "All ${#_required_actions[@]} required deployment permissions present"
  else
    for _a in "${_missing[@]}"; do
      fail "Missing permission: $_a"
    done
    info "Run './db2monitor.sh --check-permissions' to generate a policy document"
  fi
fi

# =============================================================================
# 2. Lambda Function
# =============================================================================
section "2. Lambda Function"

if [ -z "$LAMBDA_FUNCTION" ]; then
  LAMBDA_FUNCTION="DB2Mon-Lambda-Function-${REGION}"
fi

lambda_config=$(aws lambda get-function-configuration \
  --function-name "$LAMBDA_FUNCTION" \
  --region "$REGION" $PROFILE_ARG 2>/dev/null || true)

if [ -z "$lambda_config" ]; then
  fail "Lambda function not found: $LAMBDA_FUNCTION"
else
  ok "Lambda function exists: $LAMBDA_FUNCTION"

  runtime=$(echo "$lambda_config" | jq -r '.Runtime')
  state=$(echo "$lambda_config"   | jq -r '.State')
  last_update=$(echo "$lambda_config" | jq -r '.LastUpdateStatus')
  timeout=$(echo "$lambda_config" | jq -r '.Timeout')
  memory=$(echo "$lambda_config"  | jq -r '.MemorySize')
  role_arn=$(echo "$lambda_config" | jq -r '.Role')

  info "Runtime: $runtime | State: $state | LastUpdate: $last_update | Timeout: ${timeout}s | Memory: ${memory}MB"
  info "Role: $role_arn"

  [ "$state" != "Active" ] && fail "Lambda state is '$state' (expected Active)" || ok "Lambda state: Active"
  [ "$last_update" != "Successful" ] && warn "Lambda last update status: $last_update" || ok "Lambda last update: Successful"

  # VPC config
  vpc_id=$(echo "$lambda_config"     | jq -r '.VpcConfig.VpcId // ""')
  subnet_ids=$(echo "$lambda_config" | jq -r '.VpcConfig.SubnetIds // [] | join(",")')
  sg_ids=$(echo "$lambda_config"     | jq -r '.VpcConfig.SecurityGroupIds // [] | join(",")')

  if [ -z "$vpc_id" ] || [ "$vpc_id" = "null" ]; then
    warn "Lambda is NOT in a VPC — it will use public internet for AWS API calls"
  else
    ok "Lambda VPC: $vpc_id"
    info "Lambda subnets: $subnet_ids"
    info "Lambda SGs: $sg_ids"
    VPC_ID="$vpc_id"
    LAMBDA_SG_IDS="$sg_ids"
    LAMBDA_SUBNET_IDS="$subnet_ids"
  fi

  # KMS key check — org SCPs often deny kms:Decrypt on default AWS-managed keys
  lambda_kms=$(echo "$lambda_config" | jq -r '.KMSKeyArn // ""')
  if [ -z "$lambda_kms" ]; then
    warn "Lambda env vars use default AWS-managed key (aws/lambda) — org SCPs may deny kms:Decrypt"
    info "  Fix: redeploy with KMS_KEY_ARN=<customer-key-arn> or use the RDS instance KMS key"
    info "  Check: aws kms describe-key --key-id alias/aws/lambda --region $REGION $PROFILE_ARG"
  else
    ok "Lambda KMS key: $lambda_kms"
    # Verify Lambda role can use this key
    # list-grants hangs in airgap (no KMS VPC endpoint) — hard timeout via `timeout` command
    _kms_grants=$(timeout 8 aws kms list-grants --key-id "$lambda_kms" \
      --region "$REGION" $PROFILE_ARG --cli-connect-timeout 5 \
      --query "Grants[?GranteePrincipal=='${role_arn}'].Operations" \
      --output text 2>/dev/null || true)
    if [ -n "$_kms_grants" ]; then
      ok "Lambda role has KMS grant on $lambda_kms"
    elif [ -n "$BUCKET" ]; then
      info "KMS grant check skipped (airgap — no KMS VPC endpoint reachable)"
    else
      warn "No KMS grant found for Lambda role on $lambda_kms — verify key policy allows kms:Decrypt"
      info "  Check: aws kms get-key-policy --key-id $lambda_kms --policy-name default --region $REGION $PROFILE_ARG"
    fi
  fi

  # Recent invocation errors from CloudWatch
  log_group="/aws/lambda/${LAMBDA_FUNCTION}"
  info "Checking recent Lambda errors in $log_group ..."
  recent_errors=$(aws logs filter-log-events \
    --log-group-name "$log_group" \
    --filter-pattern "ERROR" \
    --start-time $(( ($(date +%s) - 3600) * 1000 )) \
    --region "$REGION" $PROFILE_ARG \
    --query 'events[*].message' --output text 2>/dev/null | head -5 || true)
  if [ -n "$recent_errors" ]; then
    warn "Recent Lambda errors (last 1h):"
    echo "$recent_errors" | while IFS= read -r line; do
      echo "    $line"
    done
  else
    ok "No Lambda errors in the last 1 hour"
  fi
fi

# =============================================================================
# 3. Secrets Manager Secret
# =============================================================================
section "3. Secrets Manager Secret"

# Auto-detect secret if not provided
if [ -z "$SECRET_NAME" ]; then
  if [ -n "$DB_INSTANCE_ID" ]; then
    # Try to find a matching secret by DB instance tag
    SECRET_NAME=$(aws secretsmanager list-secrets \
      --region "$REGION" $PROFILE_ARG \
      --filter Key=tag-key,Values=DBInstanceId Key=tag-value,Values="$DB_INSTANCE_ID" \
      --query 'SecretList[0].Name' --output text 2>/dev/null || true)
    [ "$SECRET_NAME" = "None" ] && SECRET_NAME=""
  fi
  if [ -z "$SECRET_NAME" ]; then
    # List SM-* secrets and let user pick
    secrets=$(aws secretsmanager list-secrets \
      --region "$REGION" $PROFILE_ARG \
      --filter Key=name,Values=SM- \
      --query 'SecretList[*].Name' --output text 2>/dev/null | tr '\t' '\n' || true)
    if [ -z "$secrets" ]; then
      warn "No SM-* secrets found in $REGION — skipping secret checks"
    else
      echo "  Available secrets:"
      i=1; declare -a secret_arr
      while IFS= read -r s; do
        echo "    $i. $s"; secret_arr+=("$s"); ((i++))
      done <<< "$secrets"
      read -p "  Select secret number (or Enter to skip): " choice
      [ -n "$choice" ] && SECRET_NAME="${secret_arr[$((choice-1))]}"
    fi
  fi
fi

if [ -n "$SECRET_NAME" ]; then
  secret_string=$(aws secretsmanager get-secret-value \
    --secret-id "$SECRET_NAME" --region "$REGION" $PROFILE_ARG \
    --query SecretString --output text 2>/dev/null || true)
  if [ -z "$secret_string" ]; then
    fail "Cannot read secret: $SECRET_NAME — check IAM permissions"
  else
    ok "Secret readable: $SECRET_NAME"
    # Extract key fields
    VPC_ID=${VPC_ID:-$(echo "$secret_string" | jq -r '.vpcID // ""')}
    SG_ID=$(echo "$secret_string"   | jq -r '.sgID // ""')
    DB_HOST=$(echo "$secret_string" | jq -r '.host // ""')
    DB_PORT=$(echo "$secret_string" | jq -r '.port // ""')
    SSL_EN=$(echo "$secret_string"  | jq -r '.ssl // "false"')
    SSL_CERT=$(echo "$secret_string" | jq -r '.sslCertLocation // ""')
    DB_INSTANCE_ID=${DB_INSTANCE_ID:-$(echo "$secret_string" | jq -r '.dbInstanceIdentifier // ""')}

    info "DB host: $DB_HOST:$DB_PORT | SSL: $SSL_EN"
    [ -n "$SG_ID" ] && info "Secret SG: $SG_ID"
    [ -n "$VPC_ID" ] && info "Secret VPC: $VPC_ID"

    # SSL cert check
    if [ "$SSL_EN" = "true" ]; then
      if [ -z "$SSL_CERT" ]; then
        fail "ssl=true but sslCertLocation is empty in secret"
      else
        ok "SSL cert location: $SSL_CERT"
        # Check cert exists in S3
        s3_cert_key=$(echo "$SSL_CERT" | sed 's|s3://[^/]*/||')
        s3_cert_bucket=$(echo "$SSL_CERT" | sed 's|s3://||' | cut -d'/' -f1)
        if aws s3api head-object --bucket "$s3_cert_bucket" --key "$s3_cert_key" \
           --region "$REGION" $PROFILE_ARG &>/dev/null; then
          ok "SSL cert exists in S3: $SSL_CERT"
        else
          fail "SSL cert NOT found in S3: $SSL_CERT"
        fi
      fi
    fi
  fi
fi

# =============================================================================
# 3b. DB Host Network Connectivity (nc)
# =============================================================================
if [ -n "${DB_HOST:-}" ] && [ -n "${DB_PORT:-}" ]; then
  section "3b. DB Host Network Connectivity"
  # Resolve host to check if it's a private IP — nc from CloudShell/outside VPC will always fail
  resolved_ip=$(getent hosts "$DB_HOST" 2>/dev/null | awk '{print $1}' | head -1 || true)
  _is_private=false
  if [[ "$resolved_ip" =~ ^10\. ]] || [[ "$resolved_ip" =~ ^172\.(1[6-9]|2[0-9]|3[01])\. ]] || [[ "$resolved_ip" =~ ^192\.168\. ]]; then
    _is_private=true
  fi
  if command -v nc &>/dev/null; then
    if nc -zv "$DB_HOST" "$DB_PORT" &>/dev/null 2>&1; then
      ok "nc: $DB_HOST:$DB_PORT reachable"
    elif [ "$_is_private" = "true" ] && [ "$_ENV_TYPE" = "cloudshell" ]; then
      warn "nc: $DB_HOST ($resolved_ip) is a private VPC IP — not reachable from CloudShell (expected)"
      info "  Run from inside VPC to verify: nc -zv $DB_HOST $DB_PORT"
    else
      fail "nc: $DB_HOST:$DB_PORT NOT reachable — check VPC routing, SG inbound rules, and RDS status"
      info "  Manual check: nc -zv $DB_HOST $DB_PORT"
    fi
  else
    info "nc not available — skipping TCP connectivity check to $DB_HOST:$DB_PORT"
    info "  Install: sudo yum install -y nc  # or: sudo apt-get install netcat"
  fi
fi

# =============================================================================
# 4. VPC DNS Attributes
# =============================================================================
if [ -n "${VPC_ID:-}" ]; then
  section "4. VPC DNS Attributes (VPC: $VPC_ID)"

  dns_support=$(aws ec2 describe-vpc-attribute --vpc-id "$VPC_ID" \
    --attribute enableDnsSupport --region "$REGION" $PROFILE_ARG \
    --query 'EnableDnsSupport.Value' --output text 2>/dev/null || echo "unknown")
  dns_hostnames=$(aws ec2 describe-vpc-attribute --vpc-id "$VPC_ID" \
    --attribute enableDnsHostnames --region "$REGION" $PROFILE_ARG \
    --query 'EnableDnsHostnames.Value' --output text 2>/dev/null || echo "unknown")

  [ "$dns_support" = "True" ]    && ok "enableDnsSupport: true"    || fail "enableDnsSupport: $dns_support (required for Interface endpoints)"
  [ "$dns_hostnames" = "True" ]  && ok "enableDnsHostnames: true"  || fail "enableDnsHostnames: $dns_hostnames (required for Interface endpoints)"

  # =============================================================================
  # 5. VPC Endpoints
  # =============================================================================
  section "5. VPC Endpoints (VPC: $VPC_ID)"

  # Minimum endpoints required BEFORE running db2monitor.sh in a private subnet.
  # Without these, the script cannot reach the EC2 API to create the rest.
  # Created by launch-ec2.sh automatically.
  minimum_services=(
    "com.amazonaws.${REGION}.s3"
    "com.amazonaws.${REGION}.ssm"
    "com.amazonaws.${REGION}.ssmmessages"
    "com.amazonaws.${REGION}.ec2messages"
    "com.amazonaws.${REGION}.ec2"
  )

  # Remaining endpoints created automatically by db2monitor.sh during deployment.
  deployment_services=(
    "com.amazonaws.${REGION}.sts"
    "com.amazonaws.${REGION}.secretsmanager"
    "com.amazonaws.${REGION}.monitoring"
    "com.amazonaws.${REGION}.logs"
    "com.amazonaws.${REGION}.lambda"
    "com.amazonaws.${REGION}.rds"
    "com.amazonaws.${REGION}.sns"
    "com.amazonaws.${REGION}.sqs"
    "com.amazonaws.${REGION}.scheduler"
    "com.amazonaws.${REGION}.cloudformation"
  )

  required_services=("${minimum_services[@]}" "${deployment_services[@]}")

  # Fetch all endpoints for this VPC once
  all_endpoints=$(aws ec2 describe-vpc-endpoints \
    --filters "Name=vpc-id,Values=${VPC_ID}" \
    --region "$REGION" $PROFILE_ARG \
    --query 'VpcEndpoints[*].{svc:ServiceName,state:State,type:VpcEndpointType,id:VpcEndpointId,sgs:Groups[*].GroupId}' \
    --output json 2>/dev/null || echo "[]")

  for svc in "${required_services[@]}"; do
    short=$(echo "$svc" | awk -F'.' '{print $NF}')
    ep=$(echo "$all_endpoints" | jq -r --arg s "$svc" '.[] | select(.svc==$s)')
    # Determine if this is a minimum (pre-requisite) or deployment endpoint
    is_minimum=false
    for min_svc in "${minimum_services[@]}"; do
      [ "$svc" = "$min_svc" ] && is_minimum=true && break
    done
    if [ -z "$ep" ]; then
      if $is_minimum; then
        fail "Missing MINIMUM endpoint: $short — required before running db2monitor.sh"
      else
        warn "Missing endpoint: $short — will be created automatically by db2monitor.sh"
      fi
    else
      ep_id=$(echo "$ep"    | jq -r '.id')
      ep_state=$(echo "$ep" | jq -r '.state')
      ep_type=$(echo "$ep"  | jq -r '.type')
      if [ "$ep_state" = "available" ]; then
        ok "Endpoint $short: $ep_id ($ep_type, $ep_state)"
      else
        warn "Endpoint $short: $ep_id ($ep_type) — state: $ep_state"
      fi
    fi
  done

  # =============================================================================
  # 6. Security Group — port 443 egress
  # =============================================================================
  section "6. Security Group — Port 443 Egress"

  # Also check Lambda role has S3 read access (needed for SSL cert download)
  if [ -n "${lambda_config:-}" ]; then
    role_name=$(echo "$lambda_config" | jq -r '.Role' | awk -F'/' '{print $NF}')
    s3_policy=$(aws iam list-attached-role-policies --role-name "$role_name" \
      --cli-connect-timeout 5 \
      --query 'AttachedPolicies[?contains(PolicyName,`S3`)].PolicyName' \
      --output text 2>/dev/null || true)
    if [ -z "$s3_policy" ] || [ "$s3_policy" = "None" ]; then
      warn "Lambda role $role_name: S3 policy check skipped (IAM unreachable — no VPC endpoint for IAM)"
    elif [ -n "$s3_policy" ]; then
      ok "Lambda role $role_name has S3 policy: $s3_policy"
    else
      fail "Lambda role $role_name has NO S3 policy — SSL cert download will fail with 403 (HeadObject Forbidden)"
      info "  Fix: role should have inline policy S3MonitoringBucket granting s3:GetObject/s3:PutObject on the monitoring bucket"
    fi
  fi

  # Collect all unique SGs: Lambda SGs + secret SG + endpoint SGs
  declare -A all_sgs_map
  for sg in $(echo "${LAMBDA_SG_IDS:-}" | tr ',' ' ') "${SG_ID:-}"; do
    [ -n "$sg" ] && all_sgs_map["$sg"]=1
  done
  # Add SGs attached to Interface endpoints
  while IFS= read -r ep_sg; do
    [ -n "$ep_sg" ] && all_sgs_map["$ep_sg"]=1
  done < <(echo "$all_endpoints" | jq -r '.[].sgs[]?' 2>/dev/null || true)

  for sg in "${!all_sgs_map[@]}"; do
    info "Checking SG: $sg ..."
    sg_name=$(aws ec2 describe-security-groups --group-ids "$sg" \
      --cli-connect-timeout 5 \
      --region "$REGION" $PROFILE_ARG \
      --query 'SecurityGroups[0].GroupName' --output text 2>/dev/null || echo "unknown")

    # Check for port 443 egress: explicit 443 OR all-traffic (proto -1)
    has_443=$(aws ec2 describe-security-groups --group-ids "$sg" \
      --cli-connect-timeout 5 \
      --region "$REGION" $PROFILE_ARG \
      --query 'SecurityGroups[0].IpPermissionsEgress[?ToPort==`443`]' \
      --output text 2>/dev/null || true)
    has_all=$(aws ec2 describe-security-groups --group-ids "$sg" \
      --cli-connect-timeout 5 \
      --region "$REGION" $PROFILE_ARG \
      --query "SecurityGroups[0].IpPermissionsEgress[?IpProtocol=='-1']" \
      --output text 2>/dev/null || true)

    if [ -n "$has_443" ] || [ -n "$has_all" ]; then
      # Port 443 egress rule exists — now check if destination is VPC-CIDR-only
      # (the exact bug that caused ConnectTimeoutError on S3 get_object for PEM file)
      _egress_cidrs=$(aws ec2 describe-security-groups --group-ids "$sg" \
        --cli-connect-timeout 5 \
        --region "$REGION" $PROFILE_ARG \
        --query 'SecurityGroups[0].IpPermissionsEgress[?ToPort==`443` || IpProtocol==`-1`].IpRanges[*].CidrIp' \
        --output text 2>/dev/null || true)
      _has_open=$(echo "$_egress_cidrs" | grep -c '0\.0\.0\.0/0' || true)
      _has_prefix=$(aws ec2 describe-security-groups --group-ids "$sg" \
        --cli-connect-timeout 5 \
        --region "$REGION" $PROFILE_ARG \
        --query 'SecurityGroups[0].IpPermissionsEgress[?ToPort==`443` || IpProtocol==`-1`].PrefixListIds[*].PrefixListId' \
        --output text 2>/dev/null | grep -c 'pl-' || true)
      if [ "${_has_open:-0}" -gt 0 ] || [ "${_has_prefix:-0}" -gt 0 ]; then
        ok "SG $sg ($sg_name): port 443 egress to 0.0.0.0/0 (or prefix list)"
      else
        # Has a 443 rule but destination is VPC CIDR only — this is the silent killer
        _vpc_cidr=$(aws ec2 describe-vpcs --vpc-ids "$VPC_ID" \
          --region "$REGION" $PROFILE_ARG \
          --query 'Vpcs[0].CidrBlock' --output text 2>/dev/null || true)
        fail "SG $sg ($sg_name): port 443 egress is VPC-CIDR-only (${_vpc_cidr:-restricted}) — Lambda CANNOT reach S3/SecretsManager/KMS outside VPC"
        info "  This was the root cause of ConnectTimeoutError on S3 get_object (PEM file download)"
        info "  Fix: EC2 Console → Security Groups → $sg → Outbound rules → Edit"
        info "       Add rule: Type=HTTPS, Port=443, Destination=0.0.0.0/0"
      fi
    else
      fail "SG $sg ($sg_name): NO port 443 egress — Lambda cannot reach AWS API endpoints"
      info "  Fix: add outbound rule — Type: HTTPS, Port: 443, Destination: 0.0.0.0/0 (or prefix list)"
    fi
  done

  # =============================================================================
  # 6b. Secrets Manager Endpoint — inbound 443 from Lambda SG
  # =============================================================================
  section "6b. Secrets Manager Endpoint Inbound"

  _sm_ep_sgs=$(echo "$all_endpoints" | jq -r \
    --arg svc "com.amazonaws.${REGION}.secretsmanager" \
    '.[] | select(.svc==$svc) | .sgs[]?' 2>/dev/null || true)

  if [ -z "$_sm_ep_sgs" ]; then
    warn "Secrets Manager Interface endpoint not found — skipping inbound check (see Section 5)"
  elif [ -z "${LAMBDA_SG_IDS:-}" ]; then
    info "Lambda SG unknown — skipping Secrets Manager endpoint inbound check"
  else
    for _ep_sg in $_sm_ep_sgs; do
      _ep_sg_name=$(aws ec2 describe-security-groups --group-ids "$_ep_sg" \
        --cli-connect-timeout 5 --region "$REGION" $PROFILE_ARG \
        --query 'SecurityGroups[0].GroupName' --output text 2>/dev/null || echo "unknown")
      for lambda_sg in $(echo "$LAMBDA_SG_IDS" | tr ',' ' '); do
        _sm_inbound=$(aws ec2 describe-security-groups --group-ids "$_ep_sg" \
          --cli-connect-timeout 5 --region "$REGION" $PROFILE_ARG \
          --query "SecurityGroups[0].IpPermissions[?ToPort==\`443\` && UserIdGroupPairs[?GroupId==\`${lambda_sg}\`]]" \
          --output text 2>/dev/null || true)
        _sm_inbound_all=$(aws ec2 describe-security-groups --group-ids "$_ep_sg" \
          --cli-connect-timeout 5 --region "$REGION" $PROFILE_ARG \
          --query "SecurityGroups[0].IpPermissions[?IpProtocol=='-1']" \
          --output text 2>/dev/null || true)
        _sm_inbound_cidr=$(aws ec2 describe-security-groups --group-ids "$_ep_sg" \
          --cli-connect-timeout 5 --region "$REGION" $PROFILE_ARG \
          --query 'SecurityGroups[0].IpPermissions[?ToPort==`443`].IpRanges[*].CidrIp' \
          --output text 2>/dev/null || true)
        if [ -n "$_sm_inbound" ] || [ -n "$_sm_inbound_all" ] || [ -n "$_sm_inbound_cidr" ]; then
          ok "SecretsManager endpoint SG $_ep_sg ($_ep_sg_name): allows inbound 443 from Lambda SG $lambda_sg"
        else
          fail "SecretsManager endpoint SG $_ep_sg ($_ep_sg_name): NO inbound 443 from Lambda SG $lambda_sg"
          info "  This causes ResourceNotFoundException — Lambda reaches endpoint but TLS handshake is blocked"
          info "  Fix: add inbound rule to $_ep_sg — Type: HTTPS, Port: 443, Source: $lambda_sg"
        fi
      done
    done
  fi

  # =============================================================================
  # 7. Subnet Available IPs
  # =============================================================================
  section "7. Subnet Available IPs"

  subnets_to_check="${LAMBDA_SUBNET_IDS:-}"
  if [ -z "$subnets_to_check" ] && [ -n "${VPC_ID:-}" ]; then
    subnets_to_check=$(aws ec2 describe-subnets \
      --filters "Name=vpc-id,Values=${VPC_ID}" \
      --region "$REGION" $PROFILE_ARG \
      --query 'Subnets[*].SubnetId' --output text 2>/dev/null | tr '\t' ',' || true)
  fi

  for subnet in $(echo "$subnets_to_check" | tr ',' ' '); do
    subnet_info=$(aws ec2 describe-subnets --subnet-ids "$subnet" \
      --region "$REGION" $PROFILE_ARG \
      --query 'Subnets[0].{az:AvailabilityZone,avail:AvailableIpAddressCount,cidr:CidrBlock}' \
      --output json 2>/dev/null || true)
    avail=$(echo "$subnet_info" | jq -r '.avail // 0')
    az=$(echo "$subnet_info"    | jq -r '.az // "unknown"')
    cidr=$(echo "$subnet_info"  | jq -r '.cidr // "unknown"')
    if [ "${avail:-0}" -lt 5 ]; then
      fail "Subnet $subnet ($az, $cidr): only $avail IPs available — Lambda ENI creation will fail"
    elif [ "${avail:-0}" -lt 20 ]; then
      warn "Subnet $subnet ($az, $cidr): $avail IPs available — running low"
    else
      ok "Subnet $subnet ($az, $cidr): $avail IPs available"
    fi
  done
fi

# =============================================================================
# 8. S3 Bucket
# =============================================================================
section "8. S3 Artifact Bucket"

if aws s3api head-bucket --bucket "$TARGET_BUCKET" --region "$REGION" $PROFILE_ARG 2>/dev/null; then
  ok "S3 bucket exists: $TARGET_BUCKET"

  # Check required keys
  for key in \
    "Lambda/DB2Mon/DB2Mon-Code.zip" \
    "Lambda/DB2Mon/DB2Mon-Layer.zip" \
    "CFN/rds-db2monitor-main.yml" \
    "CFN/create-db2mon-eventbridge.yml"; do
    if aws s3api head-object --bucket "$TARGET_BUCKET" --key "$key" \
       --region "$REGION" $PROFILE_ARG &>/dev/null; then
      ok "S3 key exists: $key"
    else
      fail "S3 key missing: s3://${TARGET_BUCKET}/${key}"
    fi
  done

  # SSL cert
  ssl_key="ssl/${REGION}-bundle.pem"
  if aws s3api head-object --bucket "$TARGET_BUCKET" --key "$ssl_key" \
     --region "$REGION" $PROFILE_ARG &>/dev/null; then
    ok "SSL cert exists: s3://${TARGET_BUCKET}/${ssl_key}"
  else
    warn "SSL cert missing: s3://${TARGET_BUCKET}/${ssl_key} (only needed if SSL enabled)"
  fi
else
  fail "S3 bucket not found: $TARGET_BUCKET — run db2mon-airgap.sh --mode upload --region $REGION"
fi

# =============================================================================
# 9. EventBridge Schedules
# =============================================================================
section "9. EventBridge Schedules"

schedules=$(aws scheduler list-schedules \
  --region "$REGION" $PROFILE_ARG \
  --query 'Schedules[?contains(Name,`db2mon`) || contains(Name,`DB2Mon`) || contains(Name,`EB-`)].[Name,State]' \
  --output text 2>/dev/null || true)

if [ -z "$schedules" ]; then
  warn "No DB2Mon EventBridge schedules found in $REGION"
else
  while IFS=$'\t' read -r name state; do
    [ -z "$name" ] && continue
    if [ "$state" = "ENABLED" ]; then
      ok "Schedule ENABLED: $name"
    else
      warn "Schedule DISABLED: $name (run db2monitor.sh --module start to enable)"
    fi
  done <<< "$schedules"
fi

# =============================================================================
# 10. CloudWatch Log Group — Recent Errors
# =============================================================================
section "10. CloudWatch Log Group"

log_group="/aws/lambda/${LAMBDA_FUNCTION}"
lg_exists=$(aws logs describe-log-groups \
  --log-group-name-prefix "$log_group" \
  --region "$REGION" $PROFILE_ARG \
  --query 'logGroups[0].logGroupName' --output text 2>/dev/null || true)

if [ -z "$lg_exists" ] || [ "$lg_exists" = "None" ]; then
  warn "Log group not found: $log_group (Lambda has never run or was just created)"
else
  ok "Log group exists: $log_group"

  # 403 HeadObject — Lambda role missing S3 policy (SSL cert download fails)
  s3_403_errors=$(aws logs filter-log-events \
    --log-group-name "$log_group" \
    --filter-pattern "HeadObject" \
    --start-time $(( ($(date +%s) - 86400) * 1000 )) \
    --region "$REGION" $PROFILE_ARG \
    --output json 2>/dev/null | jq '[.events[]] | length' || echo 0)
  if [ "${s3_403_errors:-0}" -gt 0 ]; then
    fail "S3 403 HeadObject errors found ($s3_403_errors in last 24h) — Lambda role missing S3 policy (SSL cert download fails)"
    info "  Fix: role should have inline policy S3MonitoringBucket granting s3:GetObject/s3:PutObject on the monitoring bucket"
  else
    ok "No S3 403 HeadObject errors in last 24h"
  fi

  # ConnectTimeoutError — the specific error from the customer issue
  timeout_errors=$(aws logs filter-log-events \
    --log-group-name "$log_group" \
    --filter-pattern "ConnectTimeoutError" \
    --start-time $(( ($(date +%s) - 86400) * 1000 )) \
    --region "$REGION" $PROFILE_ARG \
    --output json 2>/dev/null | jq '[.events[]] | length' || echo 0)
  if [ "${timeout_errors:-0}" -gt 0 ]; then
    fail "ConnectTimeoutError found ($timeout_errors occurrences in last 24h) — VPC endpoint or SG port 443 issue"
  else
    ok "No ConnectTimeoutError in last 24h"
  fi

  # General errors
  error_count=$(aws logs filter-log-events \
    --log-group-name "$log_group" \
    --filter-pattern "ERROR" \
    --start-time $(( ($(date +%s) - 3600) * 1000 )) \
    --region "$REGION" $PROFILE_ARG \
    --output json 2>/dev/null | jq '[.events[]] | length' || echo 0)
  if [ "${error_count:-0}" -gt 0 ]; then
    warn "$error_count ERROR log events in last 1h — check CloudWatch Logs"
    info "  aws logs filter-log-events --log-group-name '$log_group' --filter-pattern ERROR --region $REGION"
  else
    ok "No ERROR events in last 1h"
  fi
fi

# =============================================================================
# Summary
# =============================================================================
echo
echo -e "${BOLD}=============================================================${NC}"
echo -e "${BOLD} DIAGNOSTIC SUMMARY${NC}"
echo -e "${BOLD}=============================================================${NC}"
echo -e "  ${GREEN}PASS: $PASS${NC}  ${YELLOW}WARN: $WARN${NC}  ${RED}FAIL: $FAIL${NC}"
echo
if [ "$FAIL" -gt 0 ]; then
  echo -e "  ${RED}Action required — review FAIL items above.${NC}"
elif [ "$WARN" -gt 0 ]; then
  echo -e "  ${YELLOW}Review WARN items — deployment may work but has risks.${NC}"
else
  echo -e "  ${GREEN}All checks passed — DB2Mon deployment looks healthy.${NC}"
fi
echo -e "${BOLD}=============================================================${NC}"
echo
