#!/usr/bin/env bash

SCRIPT_CLIENT="db2-driver.sh"
SCRIPT_AIRGAP="db2client-airgap.sh"
SCRIPT_CONFIGURE="db2client-configure.sh"
DRIVER_RT="v11.5.9_linuxx64_rtcl.tar"
FILE_FUNCTIONS="functions.sh"
FILE_README="README.txt"
FILE_EXFMT="db2exfmt"
FILE_ADVIS="db2advis"
FILE_ADVISBIND="db2advisbind.zip"

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
PROFILE=${PROFILE:-"default"}
REGION=${REGION:-""}
DB2USER_NAME=${DB2USER_NAME:-"db2inst1"}
BUCKET=${BUCKET:-""}
VERBOSE=${VERBOSE:-false}

# --- Argument parsing ---
while [[ $# -gt 0 ]]; do
  case $1 in
    --region)  REGION="$2";  shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --bucket)  BUCKET="$2";  shift 2 ;;
    --verbose) VERBOSE=true; shift ;;
    -h|--help)
      echo "Usage: [BUCKET=<bucket>] [REGION=<region>] ./$SCRIPT_CLIENT [--region REGION] [--profile PROFILE]"
      echo "  No BUCKET = online mode  (downloads from public S3)"
      echo "  BUCKET set = airgap mode (downloads from private bucket)"
      exit 0 ;;
    *) log_error "Unknown option: $1"; exit 1 ;;
  esac
done

log_debug() { [[ "$VERBOSE" == "true" ]] && echo -e "${BLUE}[ DEBUG]${NC} $(date '+%H:%M:%S') - $1" >&2 || true; }

# =============================================================================
# Validation
# =============================================================================
validate() {
  # Auto-detect region if not set
  if [ -z "$REGION" ]; then
    if [ -n "${AWS_DEFAULT_REGION:-}" ]; then
      REGION="$AWS_DEFAULT_REGION"
      log_info "Detected region from environment: $REGION"
    elif curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/ >/dev/null 2>&1; then
      local token
      token=$(curl -sX PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" 2>/dev/null)
      REGION=$(curl -s -H "X-aws-ec2-metadata-token: $token" \
        http://169.254.169.254/latest/meta-data/placement/region 2>/dev/null)
      log_info "Detected region from EC2 metadata: $REGION"
    fi
  fi

  if [ -z "$REGION" ]; then
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

  # Set PROFILE_ARG early so ensure_jq can use it if needed
  if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
    PROFILE_ARG=""
  else
    PROFILE_ARG="--profile $PROFILE"
  fi

  ensure_jq
  set_credentials  # sets CREDS_FROM_METADATA=true when sourced from CloudShell/EC2

  if [ "${CREDS_FROM_METADATA:-false}" = "false" ]; then
    if ! aws sts get-caller-identity $PROFILE_ARG --region "$REGION" >/dev/null 2>&1; then
      log_error "AWS credentials invalid. Run 'aws configure' or set AWS_ACCESS_KEY_ID/SECRET."
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
# Ensure jq is available — install from private bucket if missing
# =============================================================================
ensure_jq() {
  command -v jq &>/dev/null && return 0
  log_info "jq not found — downloading from s3://${BUCKET}/scripts/jq ..."
  local tmp_jq
  tmp_jq=$(mktemp)
  aws s3 cp "s3://${BUCKET}/scripts/jq" "$tmp_jq" \
    --region "$REGION" $PROFILE_ARG --quiet
  sudo mv -f "$tmp_jq" /usr/local/bin/jq
  sudo chmod +x /usr/local/bin/jq
  log_success "jq installed from private bucket"
}

# =============================================================================
# Credentials — probe CloudShell, EC2 IMDSv2, then fall back to profile/env
# =============================================================================
set_credentials() {
  local creds
  CREDS_FROM_METADATA=false

  # CloudShell (local metadata endpoint 127.0.0.1:1338)
  if curl -s --connect-timeout 1 http://127.0.0.1:1338/latest/meta-data/ >/dev/null 2>&1; then
    log_info "Detected AWS CloudShell environment"
    local token
    token=$(curl -sX PUT "http://127.0.0.1:1338/latest/api/token" \
      -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
    creds=$(curl -s -H "Authorization: $token" \
      "http://127.0.0.1:1338/latest/meta-data/container/security-credentials")
    export AWS_ACCESS_KEY_ID=$(echo "$creds"     | jq -r .AccessKeyId)
    export AWS_SECRET_ACCESS_KEY=$(echo "$creds" | jq -r .SecretAccessKey)
    export AWS_SESSION_TOKEN=$(echo "$creds"     | jq -r .Token)
    PROFILE_ARG=""
    CREDS_FROM_METADATA=true
    return 0
  fi

  # EC2 IMDSv2
  if curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/ >/dev/null 2>&1; then
    log_info "Detected EC2 environment"
    local token role
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

  # Fall back to env vars or named profile
  if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
    PROFILE_ARG=""
  else
    PROFILE_ARG="--profile $PROFILE"
  fi
}

# =============================================================================
# Download artifacts — online (curl from public S3) or airgap (aws s3 cp)
# =============================================================================
curl_download() {
  local url="$1" dest="$2"
  curl -fsSL "$url" -o "$dest"
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
    for f in "$FILE_FUNCTIONS" "$FILE_README" "$FILE_EXFMT" "$FILE_ADVIS" "$FILE_ADVISBIND"; do
      s3_download "scripts/${f}" "${work_dir}/${f}"
    done
    s3_download "ssl/${REGION}-bundle.pem"    "${work_dir}/${REGION}-bundle.pem"
    s3_download "drivers/${DRIVER_RT}"        "${work_dir}/${DRIVER_RT}"
    s3_download "scripts/${SCRIPT_CONFIGURE}" "${work_dir}/${SCRIPT_CONFIGURE}"
  else
    # --- Online mode: pull from public S3 via curl ---
    for f in "$FILE_FUNCTIONS" "$FILE_README" "$FILE_EXFMT" "$FILE_ADVIS" "$FILE_ADVISBIND"; do
      curl_download "${SOURCE_URL}/${f}" "${work_dir}/${f}"
    done
    curl_download \
      "https://truststore.pki.rds.amazonaws.com/${REGION}/${REGION}-bundle.pem" \
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

  while getent group "$start_id" >/dev/null; do ((start_id++)); done
  local gid=$start_id
  while getent passwd "$start_id" >/dev/null; do ((start_id++)); done
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
  log_info "Deploying Db2 11.5.9 Runtime client"
  tar -xf "${work_dir}/${driver_pkg}" -C "$work_dir" &>/dev/null

  if id "$DB2USER_NAME" &>/dev/null; then
    log_info "User $DB2USER_NAME already exists. Skipping user creation."
  else
    create_db2_user
  fi

  # db2_install only if not already done
  if [ ! -d "/opt/ibm/db2/V11.5" ]; then
    log_info "Installing Db2 runtime client"
    (cd "${work_dir}/rtcl" && sudo ./db2_install -f sysreq -y -b /opt/ibm/db2 &>/dev/null) || true
    if [ ! -d "/opt/ibm/db2" ]; then
      log_error "db2_install failed — /opt/ibm/db2 not found."
      return 1
    fi
  else
    log_info "Db2 software already installed at /opt/ibm/db2/V11.5 — skipping db2_install"
  fi
  rm -rf "${work_dir}/rtcl"

  # Always remove sqllib before db2icrt so it can recreate it cleanly
  sudo rm -rf "/home/$DB2USER_NAME/sqllib" &>/dev/null || true
  local icrt_out
  # Run db2icrt with a clean PATH to avoid DS client binary conflicts (clp_api symbol error)
  icrt_out=$(sudo env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
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

  # Place db2advisbind.zip into sqllib/bnd and unzip
  sudo mv -f "${work_dir}/${FILE_ADVISBIND}" "/home/$DB2USER_NAME/sqllib/bnd/"
  sudo bash -c "
    cd /home/$DB2USER_NAME/sqllib/bnd
    rm -f db2adv*.bnd
    unzip -o ${FILE_ADVISBIND} &>/dev/null
    chown -R bin:bin db2adv*.bnd
    rm -f ${FILE_ADVISBIND}
  "

  # Place db2exfmt and db2advis into /opt/ibm/db2/bin (matches db2-driver.sh)
  for bin in "$FILE_EXFMT" "$FILE_ADVIS"; do
    sudo mv -f "${work_dir}/${bin}" /opt/ibm/db2/bin/
    sudo chown bin:bin "/opt/ibm/db2/bin/${bin}"
    sudo chmod +x "/opt/ibm/db2/bin/${bin}"
  done

  echo "$DB2USER_NAME ALL=(ALL) NOPASSWD:ALL" | sudo tee "/etc/sudoers.d/$DB2USER_NAME" >/dev/null
  sudo chmod 440 "/etc/sudoers.d/$DB2USER_NAME"

  # Place db2client-configure.sh in db2inst1 home for DSN creation
  sudo mv -f "${work_dir}/${SCRIPT_CONFIGURE}" "/home/$DB2USER_NAME/${SCRIPT_CONFIGURE}"
  sudo chown "$DB2USER_NAME:$DB2USER_NAME" "/home/$DB2USER_NAME/${SCRIPT_CONFIGURE}"
  sudo chmod +x "/home/$DB2USER_NAME/${SCRIPT_CONFIGURE}"

  log_success "Db2 11.5.9 Runtime client installed successfully for user $DB2USER_NAME"
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
  echo
  echo "  AIRGAP mode (no internet — private subnet):"
  echo "    Step 1: On any machine WITH internet, download all artifacts:"
  echo "      ./$SCRIPT_AIRGAP --mode download --region <region>"
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
  if [ "$CURL_PIPE" = "true" ]; then
    handle_curl_pipe
    return
  fi

  validate

  local work_dir
  work_dir=$(mktemp -d)
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
