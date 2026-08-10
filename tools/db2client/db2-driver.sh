#!/usr/bin/env bash

SCRIPT_CLIENT="db2-driver.sh"
SCRIPT_AIRGAP="db2client-airgap.sh"
SCRIPT_CONFIGURE="db2client-configure.sh"
FILE_FUNCTIONS="functions.sh"
FILE_README="README.txt"
INCLUDE_OTHER_TOOLS=${INCLUDE_OTHER_TOOLS:-TRUE}

# Db2 version selection — set DB2_VER before running:
#   DB2_VER=11.5  (default) → installs Db2 11.5.9 RT client
#   DB2_VER=12.1            → installs Db2 12.1.3 RT client
DB2_VER=${DB2_VER:-"11.5"}

case "$DB2_VER" in
  11.5)
    DRIVER_RT="v11.5.9_linuxx64_rtcl.tar"
    TOOLS_ZIP="db211.5-tools.zip"
    DB2_INSTALL_DIR="/opt/ibm/db2/V11.5"
    DB2_VERSION_LABEL="11.5.9"
    ;;
  12.1)
    DRIVER_RT="v12.1.4_linuxx64_rtcl.tar"
    TOOLS_ZIP="db212.1-tools.zip"
    DB2_INSTALL_DIR="/opt/ibm/db2/V12.1"
    DB2_VERSION_LABEL="12.1.4"
    ;;
  *)
    echo "ERROR: Unsupported DB2_VER='${DB2_VER}'. Valid values: 11.5, 12.1" >&2
    exit 1
    ;;
esac

# Public source (online mode)
SOURCE_URL="https://aws-blogs-artifacts-public.s3.amazonaws.com/artifacts/DBBLOG-4900"

# =============================================================================
# db2-driver.sh  —  Install RDS DB2 RT client
# =============================================================================
# Works in two modes — auto-detected based on whether BUCKET is set:
#
# ONLINE mode  (CloudShell / EC2 with internet access):
#   curl -sL https://bit.ly/getdb2driver | bash
#   — or —
#   REGION=us-east-1 ./${SCRIPT_CLIENT}
#
# AIRGAP mode  (private subnet, no internet — run ${SCRIPT_AIRGAP} first):
#   export BUCKET=db2client-artifacts-<account>-<region> REGION=<region>
#   ./${SCRIPT_CLIENT}
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

# --- Curl-pipe detection ---
# True when script is being piped via bash (not run as a saved file)
CURL_PIPE=false
if [ ! -f "${BASH_SOURCE[0]:-}" ]; then
  CURL_PIPE=true
fi



# --- Defaults ---
PROFILE=${PROFILE:-""}
REGION=${REGION:-""}
DB2USER_NAME=${DB2USER_NAME:-"db2inst1"}
BUCKET=${BUCKET:-""}
VERBOSE=${VERBOSE:-false}
CHECK_PERMISSIONS=${CHECK_PERMISSIONS:-false}
PROFILE_ARG=""   # always defined; only ever set to "--profile <name>" when PROFILE is non-empty

# --- Argument parsing ---
while [[ $# -gt 0 ]]; do
  case $1 in
    --region)  REGION="$2";  shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --bucket)  BUCKET="$2";  shift 2 ;;
    --verbose) VERBOSE=true; shift ;;
    --check-permissions|--check) CHECK_PERMISSIONS=true; shift ;;
    -h|--help)
      echo "Usage: [BUCKET=<bucket>] [REGION=<region>] [DB2_VER=11.5|12.1] ./$SCRIPT_CLIENT [options]"
      echo
      echo "Options:"
      echo "  --region REGION        AWS region (or REGION=<region>)"
      echo "  --profile PROFILE      AWS CLI profile (default: instance role / env credentials)"
      echo "  --bucket BUCKET        Private artifact bucket (enables airgap mode)"
      echo "  --check-permissions    Read-only pre-flight check; installs nothing"
      echo "  --verbose              Verbose logging"
      echo "  -h, --help             This help"
      echo
      echo "  No BUCKET = online mode  (downloads from public S3)"
      echo "  BUCKET set = airgap mode (downloads from private bucket)"
      exit 0 ;;
    *) log_error "Unknown option: $1"
       log_error "Run './$SCRIPT_CLIENT --help' for usage."
       exit 1 ;;
  esac
done

log_debug() { [[ "$VERBOSE" == "true" ]] && echo -e "${BLUE}[ DEBUG]${NC} $(date '+%H:%M:%S') - $1" >&2 || true; }

# =============================================================================
# Validation
# =============================================================================
detect_region() {
  [ -n "$REGION" ] && return 0
  if [ -n "${AWS_DEFAULT_REGION:-}" ]; then
    REGION="$AWS_DEFAULT_REGION"
    log_info "Detected region from environment: $REGION"
  elif curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/ >/dev/null 2>&1; then
    local token
    token=$(curl -sX PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" 2>/dev/null)
    REGION=$(curl -s -H "X-aws-ec2-metadata-token: $token" \
      http://169.254.169.254/latest/meta-data/placement/region 2>/dev/null)
    [ -n "$REGION" ] && log_info "Detected region from EC2 metadata: $REGION"
  fi
  if [ -z "$REGION" ] && command -v aws &>/dev/null; then
    REGION=$(aws configure get region 2>/dev/null)
    [ -n "$REGION" ] && log_info "Detected region from AWS CLI config: $REGION"
  fi
  [ -n "$REGION" ]
}

validate() {
  if ! detect_region; then
    log_error "REGION not set. Either: export REGION=us-east-1  or use --region us-east-1"
    exit 1
  fi

  if [ "$(uname -s)" != "Linux" ]; then
    log_error "This script only supports Linux. Detected: $(uname -s)"
    exit 1
  fi

  if ! sudo -n true 2>/dev/null; then
    log_error "This script requires sudo privileges."
    exit 1
  fi

  if ! command -v aws &>/dev/null; then
    log_error "aws CLI not found. Please install it first."
    exit 1
  fi

  if ! command -v curl &>/dev/null; then
    log_error "curl not found. Install it first (e.g. sudo yum install -y curl)."
    exit 1
  fi

  # Resolve credentials BEFORE ensure_jq.
  # set_credentials() parses IMDS JSON with json_field(), which does not require
  # jq, so there is no chicken-and-egg problem. Doing it in this order also
  # guarantees PROFILE_ARG is correct before any aws call runs.
  set_credentials  # sets CREDS_FROM_METADATA=true when sourced from CloudShell/EC2
  ensure_jq

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
  fi

  if [ -n "$BUCKET" ]; then
    log_success "Validation passed | Mode: AIRGAP | Region: $REGION | Bucket: $BUCKET"
  else
    log_success "Validation passed | Mode: ONLINE | Region: $REGION"
  fi
}

# =============================================================================
# JSON field extraction — works with or without jq
# =============================================================================
# Minimal AMIs (notably Amazon Linux 2023) do not ship jq. Credential discovery
# must therefore never depend on jq, otherwise the script cannot even reach the
# point where it installs it. Resolution order: jq → python3 → sed.
# Usage:  value=$(json_field AccessKeyId <<< "$json")
json_field() {
  local field="$1" data
  data=$(</dev/stdin)   # bash builtin redirection — no external cat needed
  [ -z "$data" ] && return 0
  if command -v jq &>/dev/null; then
    printf '%s' "$data" | jq -r --arg f "$field" '.[$f] // empty'
  elif command -v python3 &>/dev/null; then
    printf '%s' "$data" | python3 -c \
      'import sys,json; print(json.load(sys.stdin).get(sys.argv[1],"") or "")' "$field" 2>/dev/null
  else
    # Flat-JSON fallback — sufficient for IMDS credential documents
    printf '%s' "$data" \
      | sed -n "s/.*\"${field}\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -1
  fi
}

# =============================================================================
# Ensure jq is available
# =============================================================================
# jq is used by functions.sh and db2client-configure.sh (Secrets Manager
# parsing), so install it when missing. Resolution order:
#   1. AIRGAP (BUCKET set) — static binary from s3://$BUCKET/scripts/jq
#   2. ONLINE              — distro package manager
#   3. ONLINE              — static binary from the jq GitHub release
# Never fatal: json_field() has python3/sed fallbacks.
ensure_jq() {
  if command -v jq &>/dev/null; then
    log_debug "jq already present: $(command -v jq)"
    return 0
  fi

  # --- 1. Airgap: private bucket (populated by db2client-airgap.sh) ---
  if [ -n "$BUCKET" ]; then
    log_info "jq not found — downloading from s3://${BUCKET}/scripts/jq ..."
    local tmp_jq
    tmp_jq=$(mktemp)
    if aws s3 cp "s3://${BUCKET}/scripts/jq" "$tmp_jq" \
         --region "$REGION" $PROFILE_ARG --quiet 2>/dev/null && [ -s "$tmp_jq" ]; then
      sudo install -m 0755 "$tmp_jq" /usr/local/bin/jq
      rm -f "$tmp_jq"
      hash -r 2>/dev/null || true
      if command -v jq &>/dev/null; then
        log_success "jq installed from s3://${BUCKET}/scripts/jq"
        return 0
      fi
    fi
    rm -f "$tmp_jq"
    log_warning "Could not install jq from s3://${BUCKET}/scripts/jq — trying package manager"
  fi

  # --- 2. Distro package manager ---
  log_info "Installing jq via package manager ..."
  local pm
  for pm in dnf yum apt-get zypper; do
    command -v "$pm" &>/dev/null || continue
    case "$pm" in
      apt-get)
        sudo apt-get update -qq &>/dev/null || true
        sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq jq &>/dev/null || true ;;
      zypper)
        sudo zypper --non-interactive install jq &>/dev/null || true ;;
      *)
        sudo "$pm" install -y jq &>/dev/null || true ;;
    esac
    hash -r 2>/dev/null || true
    if command -v jq &>/dev/null; then
      log_success "jq installed via $pm"
      return 0
    fi
  done

  # --- 3. Static binary from GitHub (online only) ---
  if [ -z "$BUCKET" ]; then
    local jq_ver="jq-1.7.1" jq_bin arch tmp_jq
    arch=$(uname -m)
    case "$arch" in
      x86_64|amd64) jq_bin="jq-linux-amd64" ;;
      aarch64|arm64) jq_bin="jq-linux-arm64" ;;
      *) jq_bin="" ;;
    esac
    if [ -n "$jq_bin" ]; then
      log_info "Downloading static jq binary (${jq_ver}/${jq_bin}) ..."
      tmp_jq=$(mktemp)
      if curl -fsSL "https://github.com/jqlang/jq/releases/download/${jq_ver}/${jq_bin}" \
           -o "$tmp_jq" 2>/dev/null && [ -s "$tmp_jq" ]; then
        sudo install -m 0755 "$tmp_jq" /usr/local/bin/jq
        rm -f "$tmp_jq"
        hash -r 2>/dev/null || true
        if command -v jq &>/dev/null; then
          log_success "jq installed to /usr/local/bin/jq"
          return 0
        fi
      fi
      rm -f "$tmp_jq"
    fi
  fi

  log_warning "jq could not be installed — falling back to python3/sed for JSON parsing."
  log_warning "Install it manually for the best experience: sudo yum install -y jq"
  return 0
}

# =============================================================================
# Credentials — probe CloudShell, EC2 IMDSv2, then fall back to profile/env
# Precedence:
#   1. Exported AWS_ACCESS_KEY_ID/SECRET  → use immediately
#   2. PROFILE explicitly set             → validate with sts, exit if fails
#   3. No profile                         → probe CloudShell IMDS → EC2 IMDS → exit if neither works
# =============================================================================
set_credentials() {
  local creds
  CREDS_FROM_METADATA=false

  # Priority 1: exported env var credentials
  if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
    PROFILE_ARG=""
    return 0
  fi

  # Priority 2: explicit profile — skip IMDS, validate immediately
  if [ -n "$PROFILE" ]; then
    PROFILE_ARG="--profile $PROFILE"
    log_info "Using explicit profile: $PROFILE"
    return 0
  fi

  # Priority 3a: CloudShell IMDS
  if curl -s --connect-timeout 1 http://127.0.0.1:1338/latest/meta-data/ >/dev/null 2>&1; then
    log_info "Detected AWS CloudShell environment"
    local token
    token=$(curl -sX PUT "http://127.0.0.1:1338/latest/api/token" \
      -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
    creds=$(curl -s -H "Authorization: $token" \
      "http://127.0.0.1:1338/latest/meta-data/container/security-credentials")
    export AWS_ACCESS_KEY_ID=$(json_field AccessKeyId     <<< "$creds")
    export AWS_SECRET_ACCESS_KEY=$(json_field SecretAccessKey <<< "$creds")
    export AWS_SESSION_TOKEN=$(json_field Token           <<< "$creds")
    if [ -z "${AWS_ACCESS_KEY_ID:-}" ]; then
      log_error "CloudShell detected but credentials could not be parsed."
      [ "${CREDS_SOFT:-false}" = "true" ] && return 1
      exit 1
    fi
    PROFILE_ARG=""
    CREDS_FROM_METADATA=true
    return 0
  fi

  # Priority 3b: EC2 IMDSv2
  if curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/ >/dev/null 2>&1; then
    log_info "Detected EC2 environment"
    local token role
    token=$(curl -sX PUT "http://169.254.169.254/latest/api/token" \
      -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
    role=$(curl -s -H "X-aws-ec2-metadata-token: $token" \
      http://169.254.169.254/latest/meta-data/iam/security-credentials/ | head -1)
    if [ -n "$role" ]; then
      creds=$(curl -s -H "X-aws-ec2-metadata-token: $token" \
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/$role")
      export AWS_ACCESS_KEY_ID=$(json_field AccessKeyId     <<< "$creds")
      export AWS_SECRET_ACCESS_KEY=$(json_field SecretAccessKey <<< "$creds")
      export AWS_SESSION_TOKEN=$(json_field Token           <<< "$creds")
      if [ -n "${AWS_ACCESS_KEY_ID:-}" ]; then
        log_info "Using IAM instance role: $role"
        PROFILE_ARG=""
        CREDS_FROM_METADATA=true
        return 0
      fi
      log_warning "Instance role '$role' found but credentials could not be parsed."
    else
      log_warning "EC2 detected but no IAM instance profile is attached."
    fi
  fi

  # CREDS_SOFT=true → report and let the caller decide (used by --check-permissions)
  if [ "${CREDS_SOFT:-false}" = "true" ]; then
    return 1
  fi
  log_error "No credentials found. Attach an IAM instance profile, or set"
  log_error "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, or export PROFILE=<name>."
  exit 1
}

# =============================================================================
# Pre-flight check  —  ./db2-driver.sh --check-permissions
# =============================================================================
# Read-only. Verifies the host prerequisites, AWS credentials, IAM permissions
# and artifact reachability needed by db2-driver.sh AND db2client-configure.sh
# before anything is installed. Never creates, modifies or deletes anything.
#
# Exit code: 0 = all required checks passed, 1 = at least one required failure.
# =============================================================================
CHK_PASS=0; CHK_FAIL=0; CHK_WARN=0; CHK_SKIP=0
declare -a MISSING_ACTIONS=()

chk_pass() { CHK_PASS=$((CHK_PASS+1)); echo -e "  ${GREEN}[ PASS ]${NC} $1" >&2; }
chk_fail() { CHK_FAIL=$((CHK_FAIL+1)); echo -e "  ${RED}[ FAIL ]${NC} $1" >&2; }
chk_warn() { CHK_WARN=$((CHK_WARN+1)); echo -e "  ${YELLOW}[ WARN ]${NC} $1" >&2; }
chk_skip() { CHK_SKIP=$((CHK_SKIP+1)); echo -e "  ${BLUE}[ SKIP ]${NC} $1" >&2; }
chk_head() { echo >&2; echo -e "${BLUE}--- $1 ${NC}" >&2; }

# Record an IAM action that failed so the remediation policy can list it
chk_missing() { MISSING_ACTIONS+=("$1"); }

check_host_prereqs() {
  chk_head "Host prerequisites"

  if [ "$(uname -s)" = "Linux" ]; then
    chk_pass "Operating system is Linux ($(uname -m))"
  else
    chk_fail "Operating system is $(uname -s) — this script supports Linux only"
  fi

  local c
  for c in aws curl tar; do
    if command -v "$c" &>/dev/null; then
      chk_pass "$c found ($(command -v "$c"))"
    else
      chk_fail "$c not found — required"
    fi
  done

  if command -v unzip &>/dev/null; then
    chk_pass "unzip found"
  else
    chk_warn "unzip not found — optional tools (db2exfmt, db2advis) will be skipped"
  fi

  # jq is no longer required (json_field falls back to python3/sed) but is preferred
  if command -v jq &>/dev/null; then
    chk_pass "jq found ($(command -v jq))"
  elif command -v python3 &>/dev/null; then
    chk_warn "jq not found — will be installed automatically; python3 available as fallback"
  else
    chk_warn "jq and python3 both missing — will fall back to sed for JSON parsing"
  fi

  if sudo -n true 2>/dev/null; then
    chk_pass "Passwordless sudo available"
  else
    chk_fail "Passwordless sudo required (the installer writes to /opt/ibm/db2)"
  fi

  # db2icrt needs roughly 512 MB of scratch space
  # 'df -k' + awk is portable; --output=avail is GNU-only and returns junk elsewhere.
  # The numeric guard keeps a surprising value from breaking arithmetic under set -e.
  local tmp_free
  tmp_free=$(df -k /tmp 2>/dev/null | awk 'NR==2 {print $4}' || true)
  if [[ "$tmp_free" =~ ^[0-9]+$ ]]; then
    if [ "$tmp_free" -ge 524288 ]; then
      chk_pass "/tmp has $((tmp_free/1024)) MB free"
    else
      chk_warn "/tmp has only $((tmp_free/1024)) MB free — will bind-mount /var/tmp over /tmp"
    fi
  else
    chk_skip "Could not determine free space on /tmp"
  fi

  # AL2023 ships without libcrypt.so.1, which db2iure links against
  if command -v ldconfig &>/dev/null; then
    if ldconfig -p 2>/dev/null | grep -q libcrypt.so.1; then
      chk_pass "libcrypt.so.1 present"
    else
      chk_warn "libcrypt.so.1 missing — installer will add libxcrypt-compat (needs yum/dnf access)"
    fi
  fi
}

check_credentials() {
  chk_head "AWS credentials"

  if ! detect_region; then
    chk_fail "Region could not be determined — set REGION=<region> or use --region"
    return 1
  fi
  chk_pass "Region: $REGION"

  CREDS_SOFT=true
  if ! set_credentials; then
    chk_fail "No AWS credentials found (no instance profile, no env vars, no profile)"
    CREDS_SOFT=false
    return 1
  fi
  CREDS_SOFT=false

  local ident
  if ! ident=$(aws sts get-caller-identity $PROFILE_ARG --region "$REGION" \
                 --query 'Arn' --output text 2>&1); then
    # Distinguish the common causes — a bad region and an expired token produce
    # very different remediations.
    case "$ident" in
      *"Could not connect to the endpoint"*|*EndpointConnectionError*|*"Invalid endpoint"*)
        chk_fail "sts:GetCallerIdentity failed — region '$REGION' is invalid or its endpoint is unreachable" ;;
      *ExpiredToken*|*"security token included in the request is expired"*)
        chk_fail "sts:GetCallerIdentity failed — credentials have expired; refresh them and retry" ;;
      *InvalidClientTokenId*|*SignatureDoesNotMatch*)
        chk_fail "sts:GetCallerIdentity failed — credentials are invalid" ;;
      *AccessDenied*|*"not authorized"*)
        chk_fail "sts:GetCallerIdentity denied by policy"
        chk_missing "sts:GetCallerIdentity" ;;
      *)
        chk_fail "sts:GetCallerIdentity failed: $(echo "$ident" | head -1)" ;;
    esac
    log_debug "$ident"
    return 1
  fi
  chk_pass "Caller identity: $ident"
  return 0
}

check_iam_permissions() {
  chk_head "IAM permissions"

  # --- rds:DescribeDBInstances (db2client-configure.sh, functions.sh) ---
  local instances err
  if err=$(aws rds describe-db-instances --region "$REGION" $PROFILE_ARG \
             --query 'DBInstances[?starts_with(Engine, `db2`)].DBInstanceIdentifier' \
             --output text 2>&1); then
    instances="$err"
    chk_pass "rds:DescribeDBInstances"
    if [ -n "$instances" ]; then
      chk_pass "Found RDS for Db2 instance(s): $(echo $instances | tr '\t' ' ')"
    else
      chk_warn "No RDS for Db2 instances found in $REGION — DSN configuration will have nothing to register"
    fi
  else
    chk_fail "rds:DescribeDBInstances denied"
    chk_missing "rds:DescribeDBInstances"
    log_debug "$err"
    instances=""
  fi

  local first_instance
  # '|| true' guards against SIGPIPE from head closing tr early under pipefail
  first_instance=$(echo "$instances" | tr '\t' '\n' | head -1 || true)

  # --- rds:DescribeDBParameters (reads db2comm / ssl_svcename) ---
  if [ -z "$first_instance" ]; then
    chk_skip "rds:DescribeDBParameters — no Db2 instance available to test against"
  else
    local pg
    pg=$(aws rds describe-db-instances --db-instance-identifier "$first_instance" \
           --region "$REGION" $PROFILE_ARG \
           --query 'DBInstances[0].DBParameterGroups[0].DBParameterGroupName' \
           --output text 2>/dev/null || true)
    if [ -z "$pg" ] || [ "$pg" = "None" ]; then
      chk_skip "rds:DescribeDBParameters — no parameter group on $first_instance"
    elif err=$(aws rds describe-db-parameters --db-parameter-group-name "$pg" \
                 --region "$REGION" $PROFILE_ARG \
                 --query "Parameters[?ParameterName=='db2comm'].ParameterValue" \
                 --output text 2>&1); then
      chk_pass "rds:DescribeDBParameters (db2comm=${err:-<not set>} on $pg)"
    else
      chk_fail "rds:DescribeDBParameters denied — SSL port/db2comm detection will fail"
      chk_missing "rds:DescribeDBParameters"
      log_debug "$err"
    fi
  fi

  # --- secretsmanager:GetSecretValue (+ kms:Decrypt) ---
  # The secret value is never printed — only the success of the call is reported.
  if [ -z "$first_instance" ]; then
    chk_skip "secretsmanager:GetSecretValue — no Db2 instance available to test against"
  else
    local secret_arn
    secret_arn=$(aws rds describe-db-instances --db-instance-identifier "$first_instance" \
                   --region "$REGION" $PROFILE_ARG \
                   --query 'DBInstances[0].MasterUserSecret.SecretArn' \
                   --output text 2>/dev/null || true)
    if [ -z "$secret_arn" ] || [ "$secret_arn" = "None" ]; then
      chk_skip "secretsmanager:GetSecretValue — $first_instance does not use a managed master password"
    elif aws secretsmanager get-secret-value --secret-id "$secret_arn" \
           --region "$REGION" $PROFILE_ARG --query SecretString --output text >/dev/null 2>&1; then
      chk_pass "secretsmanager:GetSecretValue (and kms:Decrypt) on the master user secret"
    else
      chk_fail "secretsmanager:GetSecretValue denied — you will be prompted for the master password"
      chk_missing "secretsmanager:GetSecretValue"
      chk_missing "kms:Decrypt"
    fi
  fi
}

check_artifacts() {
  if [ -n "$BUCKET" ]; then
    chk_head "Artifact reachability (AIRGAP mode — s3://$BUCKET)"

    if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" $PROFILE_ARG &>/dev/null; then
      chk_pass "Bucket reachable: s3://$BUCKET"
    else
      chk_fail "Cannot reach s3://$BUCKET — check the name, the region and s3:ListBucket"
      chk_missing "s3:ListBucket"
    fi

    local key required
    for key in "scripts/${FILE_FUNCTIONS}" "scripts/${FILE_README}" \
               "scripts/${SCRIPT_CONFIGURE}" "drivers/${DRIVER_RT}" \
               "ssl/${REGION}-bundle.pem" "scripts/jq" "scripts/${TOOLS_ZIP}"; do
      required=true
      case "$key" in "scripts/jq"|"scripts/${TOOLS_ZIP}") required=false ;; esac
      if aws s3api head-object --bucket "$BUCKET" --key "$key" \
           --region "$REGION" $PROFILE_ARG &>/dev/null; then
        chk_pass "s3:GetObject s3://${BUCKET}/${key}"
      elif [ "$required" = "true" ]; then
        chk_fail "Missing or unreadable: s3://${BUCKET}/${key}"
        chk_missing "s3:GetObject"
      else
        chk_warn "Optional object absent: s3://${BUCKET}/${key}"
      fi
    done
  else
    chk_head "Artifact reachability (ONLINE mode — public endpoints)"

    # Snapshot so the NAT-gateway hint reacts only to failures in THIS section
    local fail_before=$CHK_FAIL
    local url
    for url in "${SOURCE_URL}/${DRIVER_RT}" "${SOURCE_URL}/${SCRIPT_CONFIGURE}" \
               "${SOURCE_URL}/${FILE_FUNCTIONS}"; do
      if curl -fsSI --connect-timeout 10 "$url" >/dev/null 2>&1; then
        chk_pass "Reachable: $(basename "$url")"
      else
        chk_fail "Unreachable: $url"
      fi
    done

    if [ "$INCLUDE_OTHER_TOOLS" = "TRUE" ]; then
      if curl -fsSI --connect-timeout 10 "${SOURCE_URL}/${TOOLS_ZIP}" >/dev/null 2>&1; then
        chk_pass "Reachable: ${TOOLS_ZIP}"
      else
        chk_warn "Unreachable: ${TOOLS_ZIP} — optional tools will be skipped"
      fi
    fi

    local cert_url
    cert_url=$(rds_truststore_url "$REGION")
    if curl -fsSI --connect-timeout 10 "$cert_url" >/dev/null 2>&1; then
      chk_pass "Reachable: RDS certificate bundle for $REGION"
    else
      chk_fail "Unreachable: $cert_url"
    fi

    if [ "$CHK_FAIL" -gt "$fail_before" ]; then
      log_warning "Public endpoints unreachable usually means the private subnet has no"
      log_warning "NAT gateway. Use the airgap flow instead: ./${SCRIPT_AIRGAP} --help"
    fi
  fi
}

print_remediation() {
  [ ${#MISSING_ACTIONS[@]} -eq 0 ] && return 0

  # Deduplicate
  local uniq
  uniq=$(printf '%s\n' "${MISSING_ACTIONS[@]}" | sort -u)

  echo >&2
  log_warning "Attach a policy granting the missing actions to the identity above."
  log_warning "Minimum policy for this tool (scope Resource down to taste):"
  echo >&2
  {
    echo '{'
    echo '  "Version": "2012-10-17",'
    echo '  "Statement": ['
    echo '    {'
    echo '      "Effect": "Allow",'
    echo '      "Action": ['
    local first=true a
    while IFS= read -r a; do
      [ -z "$a" ] && continue
      [ "$first" = "true" ] || echo ','
      printf '        "%s"' "$a"
      first=false
    done <<< "$uniq"
    echo
    echo '      ],'
    echo '      "Resource": "*"'
    echo '    }'
    echo '  ]'
    echo '}'
  } >&2
  echo >&2
}

check_permissions() {
  echo >&2
  log_info "============================================================="
  log_info "db2-driver.sh pre-flight check (read-only)"
  log_info "Db2 version: ${DB2_VERSION_LABEL} | Mode: $([ -n "$BUCKET" ] && echo AIRGAP || echo ONLINE)"
  log_info "============================================================="

  check_host_prereqs

  if check_credentials; then
    check_iam_permissions
    check_artifacts
  else
    chk_head "IAM permissions"
    chk_skip "Skipped — credentials could not be established"
    chk_head "Artifact reachability"
    chk_skip "Skipped — credentials could not be established"
  fi

  print_remediation

  echo >&2
  log_info "============================================================="
  log_info "Summary: ${CHK_PASS} passed, ${CHK_FAIL} failed, ${CHK_WARN} warnings, ${CHK_SKIP} skipped"
  if [ "$CHK_FAIL" -eq 0 ]; then
    log_success "Pre-flight check passed — safe to run the installer:"
    if [ -n "$BUCKET" ]; then
      log_info "  BUCKET=$BUCKET REGION=$REGION DB2_VER=$DB2_VER ./${SCRIPT_CLIENT}"
    else
      log_info "  REGION=$REGION DB2_VER=$DB2_VER ./${SCRIPT_CLIENT}"
    fi
    log_info "============================================================="
    return 0
  fi
  log_error "Pre-flight check found ${CHK_FAIL} blocking issue(s) — resolve them before installing."
  log_info "============================================================="
  return 1
}

# =============================================================================
# Download artifacts — online (curl from public S3) or airgap (aws s3 cp)
# =============================================================================
curl_download() {
  local url="$1" dest="$2"
  if ! curl -fsSL "$url" -o "$dest"; then
    log_error "Failed to download: $url"
    return 1
  fi
}

# RDS cert bundle host differs by partition:
#   commercial → truststore.pki.rds.amazonaws.com
#   GovCloud   → truststore.pki.<region>.rds.amazonaws.com
rds_truststore_url() {
  local region="$1"
  case "$region" in
    us-gov-*) echo "https://truststore.pki.${region}.rds.amazonaws.com/${region}/${region}-bundle.pem" ;;
    cn-*)     echo "https://truststore.pki.${region}.rds.amazonaws.com.cn/${region}/${region}-bundle.pem" ;;
    *)        echo "https://truststore.pki.rds.amazonaws.com/${region}/${region}-bundle.pem" ;;
  esac
}

s3_download() {
  local key="$1" dest="$2"
  aws s3 cp "s3://${BUCKET}/${key}" "$dest" \
    --region "$REGION" $PROFILE_ARG --quiet
}

download_artifacts() {
  local work_dir="$1"

  if [ -n "$BUCKET" ]; then
    # --- Airgap mode: pull from private bucket ---
    for f in "$FILE_FUNCTIONS" "$FILE_README"; do
      s3_download "scripts/${f}" "${work_dir}/${f}"
    done
    if [ "$INCLUDE_OTHER_TOOLS" = "TRUE" ]; then
      # Non-fatal: the tools zip is only staged when db2client-airgap.sh ran
      # with INCLUDE_LICENSED_TOOLS=TRUE. Missing zip just skips the extras.
      if ! s3_download "scripts/${TOOLS_ZIP}" "${work_dir}/${TOOLS_ZIP}" 2>/dev/null; then
        rm -f "${work_dir}/${TOOLS_ZIP}"
        log_warning "s3://${BUCKET}/scripts/${TOOLS_ZIP} not found — skipping optional tools"
      fi
    fi
    s3_download "ssl/${REGION}-bundle.pem"    "${work_dir}/${REGION}-bundle.pem"
    s3_download "drivers/${DRIVER_RT}"        "${work_dir}/${DRIVER_RT}"
    s3_download "scripts/${SCRIPT_CONFIGURE}" "${work_dir}/${SCRIPT_CONFIGURE}"
  else
    # --- Online mode: pull from public S3 via curl ---
    for f in "$FILE_FUNCTIONS" "$FILE_README"; do
      curl_download "${SOURCE_URL}/${f}" "${work_dir}/${f}"
    done
    if [ "$INCLUDE_OTHER_TOOLS" = "TRUE" ]; then
      if ! curl_download "${SOURCE_URL}/${TOOLS_ZIP}" "${work_dir}/${TOOLS_ZIP}"; then
        rm -f "${work_dir}/${TOOLS_ZIP}"
        log_warning "${TOOLS_ZIP} not available — skipping optional tools"
      fi
    fi
    curl_download \
      "$(rds_truststore_url "$REGION")" \
      "${work_dir}/${REGION}-bundle.pem"
    curl_download "${SOURCE_URL}/${DRIVER_RT}"        "${work_dir}/${DRIVER_RT}"
    curl_download "${SOURCE_URL}/${SCRIPT_CONFIGURE}" "${work_dir}/${SCRIPT_CONFIGURE}"
  fi

  echo "${DRIVER_RT}"
}

# =============================================================================
# User creation
# =============================================================================
create_db2_user() {
  local username="$DB2USER_NAME"
  local start_id=1001

  while getent group "$start_id" >/dev/null; do start_id=$((start_id + 1)); done
  local gid=$start_id
  while getent passwd "$start_id" >/dev/null; do start_id=$((start_id + 1)); done
  local uid=$start_id

  log_info "Creating group $username (GID $gid) and user (UID $uid)"
  sudo groupadd -g "$gid" "$username"
  sudo useradd -u "$uid" -g "$gid" -d "/home/$username" -m -s /bin/bash "$username"
  log_success "User $username created"
}


# =============================================================================
# Install RT Client (runtime client)  — mirrors install_rt_client() in db2-driver.sh
# =============================================================================
install_rt_client() {
  local work_dir="$1"
  local driver_pkg="$2"

  log_info "============================================================================"
  log_info "Deploying Db2 ${DB2_VERSION_LABEL} Runtime client"
  log_debug "Extracting ${driver_pkg} from ${work_dir}"
  if ! tar -xf "${work_dir}/${driver_pkg}" -C "$work_dir" 2>/tmp/tar_err; then
    log_error "tar extraction failed: $(cat /tmp/tar_err)"
    return 1
  fi

  if id "$DB2USER_NAME" &>/dev/null; then
    log_info "User $DB2USER_NAME already exists. Skipping user creation."
  else
    create_db2_user
  fi

  # db2_install only if not already done for this version
  if [ ! -d "${DB2_INSTALL_DIR}" ]; then
    log_info "Installing Db2 ${DB2_VERSION_LABEL} runtime client"
    # AL2023 ships without libcrypt.so.1 — db2iure requires it
    if ! ldconfig -p | grep -q libcrypt.so.1; then
      log_info "Installing libxcrypt-compat for AL2023 compatibility"
      sudo yum install -y libxcrypt-compat &>/dev/null
    fi
    (cd "${work_dir}/rtcl" && sudo TMPDIR=/var/tmp ./db2_install -f sysreq -y -b /opt/ibm/db2 2>/tmp/db2install_err) || true
    if [ ! -d "/opt/ibm/db2" ]; then
      log_error "db2_install failed — /opt/ibm/db2 not found."
      return 1
    fi
  else
    log_info "Db2 software already installed at ${DB2_INSTALL_DIR} — skipping db2_install"
  fi
  rm -rf "${work_dir}/rtcl"

  # Always remove sqllib before db2icrt so it can recreate it cleanly
  sudo rm -rf "/home/$DB2USER_NAME/sqllib" &>/dev/null || true
  local tmp_free
  tmp_free=$(df -k /tmp 2>/dev/null | awk 'NR==2 {print $4}' || true)
  if [[ "$tmp_free" =~ ^[0-9]+$ ]] && [ "$tmp_free" -lt 524288 ]; then
    log_info "/tmp has insufficient space (${tmp_free}KB) — bind-mounting /var/tmp over /tmp"
    sudo mount --bind /var/tmp /tmp
    trap "sudo umount /tmp 2>/dev/null; rm -rf $work_dir" EXIT
  fi

  local icrt_out
  icrt_out=$(sudo env -i TMPDIR=/var/tmp PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    /opt/ibm/db2/instance/db2icrt -s client "$DB2USER_NAME" 2>&1) || true

  if [ ! -d "/home/$DB2USER_NAME/sqllib" ]; then
    log_error "db2icrt failed — /home/$DB2USER_NAME/sqllib not found."
    log_error "db2icrt output: $icrt_out"
    return 1
  fi

  # Place functions.sh and README.txt
  sudo mv -f "${work_dir}/${FILE_FUNCTIONS}" "/home/$DB2USER_NAME/"
  sudo chown "$DB2USER_NAME:$DB2USER_NAME" "/home/$DB2USER_NAME/${FILE_FUNCTIONS}"
  sudo mv -f "${work_dir}/${FILE_README}" "/home/$DB2USER_NAME/"
  sudo chown "$DB2USER_NAME:$DB2USER_NAME" "/home/$DB2USER_NAME/${FILE_README}"

  if [ "$INCLUDE_OTHER_TOOLS" = "TRUE" ] && [ -f "${work_dir}/${TOOLS_ZIP}" ] \
     && ! command -v unzip &>/dev/null; then
    log_warning "unzip not found — skipping optional tools (install with: sudo yum install -y unzip)"
  elif [ "$INCLUDE_OTHER_TOOLS" = "TRUE" ] && [ -f "${work_dir}/${TOOLS_ZIP}" ]; then
    log_info "Installing tools from ${TOOLS_ZIP}..."
    # Extract tools zip — expected contents: db2exfmt, db2advis, db2advisbind.zip
    local tools_dir="${work_dir}/tools"
    mkdir -p "$tools_dir"
    unzip -o "${work_dir}/${TOOLS_ZIP}" -d "$tools_dir" &>/dev/null

    # Place db2advisbind.zip into sqllib/bnd and unzip
    if [ -f "${tools_dir}/db2advisbind.zip" ]; then
      sudo mv -f "${tools_dir}/db2advisbind.zip" "/home/$DB2USER_NAME/sqllib/bnd/"
      sudo bash -c "
        cd /home/$DB2USER_NAME/sqllib/bnd
        rm -f db2adv*.bnd
        unzip -o db2advisbind.zip &>/dev/null
        chown -R bin:bin db2adv*.bnd
        rm -f db2advisbind.zip
      "
    fi

    # Place db2exfmt and db2advis into /opt/ibm/db2/bin
    for bin in db2exfmt db2advis; do
      if [ -f "${tools_dir}/${bin}" ]; then
        sudo mv -f "${tools_dir}/${bin}" /opt/ibm/db2/bin/
        sudo chown bin:bin "/opt/ibm/db2/bin/${bin}"
        sudo chmod +x "/opt/ibm/db2/bin/${bin}"
      fi
    done
    rm -rf "$tools_dir"
  else
    log_info "Skipping additional tools — set INCLUDE_OTHER_TOOLS=TRUE to enable"
  fi

  echo "$DB2USER_NAME ALL=(ALL) NOPASSWD:ALL" | sudo tee "/etc/sudoers.d/$DB2USER_NAME" >/dev/null
  sudo chmod 440 "/etc/sudoers.d/$DB2USER_NAME"

  sudo mv -f "${work_dir}/${SCRIPT_CONFIGURE}" "/home/$DB2USER_NAME/${SCRIPT_CONFIGURE}"
  sudo chown "$DB2USER_NAME:$DB2USER_NAME" "/home/$DB2USER_NAME/${SCRIPT_CONFIGURE}"
  sudo chmod +x "/home/$DB2USER_NAME/${SCRIPT_CONFIGURE}"

  log_success "Db2 ${DB2_VERSION_LABEL} Runtime client installed successfully for user $DB2USER_NAME"
  log_info "============================================================================"
}

# =============================================================================
# Curl-pipe handler — download script then exit so user can run it directly
# =============================================================================
handle_curl_pipe() {
  log_info "Curl-pipe detected — downloading $SCRIPT_CLIENT and $SCRIPT_AIRGAP for direct use"
  local dest_client="./$SCRIPT_CLIENT"
  local dest_airgap="./$SCRIPT_AIRGAP"
  curl -fsSL "${SOURCE_URL}/${SCRIPT_CLIENT}" -o "$dest_client" && chmod +x "$dest_client"
  log_success "Saved: $dest_client"
  curl -fsSL "${SOURCE_URL}/${SCRIPT_AIRGAP}" -o "$dest_airgap" && chmod +x "$dest_airgap"
  log_success "Saved: $dest_airgap"
  curl -fsSL "${SOURCE_URL}/${FILE_README}" -o "./$FILE_README"
  log_success "Saved: ./$FILE_README"
  echo
  echo "============================================================="
  echo "  ONLINE mode (EC2 / CloudShell with internet):"
  echo "    REGION=<region> ./$SCRIPT_CLIENT"
  echo "    DB2_VER=12.1 REGION=<region> ./$SCRIPT_CLIENT   # install Db2 12.1"
  echo "    DB2_VER=11.5 REGION=<region> ./$SCRIPT_CLIENT   # install Db2 11.5 (default)"
  echo
  echo "  AIRGAP mode (no internet — private subnet):"
  echo "    Step 1: On any machine WITH internet, download all artifacts:"
  echo "      DB2_VER=12.1 ./$SCRIPT_AIRGAP --mode download --region <region>"
  echo "                       # saves to ./db2client-artifacts/"
  echo
  echo "    Step 2: On a machine WITH AWS configured, upload to S3:"
  echo "      ./$SCRIPT_AIRGAP --mode upload --region <region>"
  echo "                       # creates bucket + uploads artifacts"
  echo
  echo "    Step 3: Follow steps given after completion of step 2:"
  echo "============================================================="
}

# =============================================================================
# Main
# =============================================================================
main() {
  # --check-permissions is honoured in both forms, so this works as documented:
  #   curl -sL https://bit.ly/getdb2driver | bash -s -- --check-permissions
  if [ "$CHECK_PERMISSIONS" = "true" ]; then
    check_permissions
    return $?
  fi

  if [ "$CURL_PIPE" = "true" ]; then
    handle_curl_pipe
    return
  fi

  validate

  local work_dir
  work_dir=$(mktemp -d -p /var/tmp)
  trap "rm -rf $work_dir" EXIT

  log_info "Downloading artifacts ..."
  local driver_pkg
  driver_pkg=$(download_artifacts "$work_dir")
  log_success "Downloading artifacts ... Done."

  install_rt_client "$work_dir" "$driver_pkg"

  log_success "============================================================="
  log_success "DB2 RT client installed successfully"
  log_info "To configure DSN entries, switch to the DB2 user and run:"
  log_info "  1. sudo su - $DB2USER_NAME"
  if [ -n "$BUCKET" ]; then
    log_info "  2. BUCKET=$BUCKET REGION=$REGION source $SCRIPT_CONFIGURE"
  else
    log_info "  2. REGION=$REGION source $SCRIPT_CONFIGURE"
  fi
  log_success "============================================================="
}

main "$@"
