#!/usr/bin/env bash
# =============================================================================
# db2client-configure.sh  —  Configure db2dsdriver.cfg for RDS DB2 RT client
# =============================================================================
# Run as db2inst1 after db2-driver.sh has installed the RT client:
#
#   sudo su - db2inst1
#   REGION=<region> source db2client-configure.sh                                    # online
#   BUCKET=db2client-artifacts-<account>-<region> REGION=<region> source db2client-configure.sh  # airgap
#
# Optional env vars:
#   DB_INSTANCE_ID=<id>   target a specific RDS instance
#   PROFILE=<profile>     AWS CLI profile (default: default)
# =============================================================================

if [ -z "$BASH_VERSION" ]; then exec bash "$0" "$@"; fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'
log_info()    { echo -e "${BLUE}[   INFO]${NC} $(date '+%H:%M:%S') - $1" >&2; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $(date '+%H:%M:%S') - $1" >&2; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $(date '+%H:%M:%S') - $1" >&2; }
log_error()   { echo -e "${RED}[  ERROR]${NC} $(date '+%H:%M:%S') - $1" >&2; }

# --- Defaults ---
PROFILE=${PROFILE:-"default"}
DB2USER_NAME=${DB2USER_NAME:-"db2inst1"}
declare -a HELP_COMMANDS=()
declare -a DB_INSTANCES=()
declare -a MASTER_USER_NAMES=()
declare -a MASTER_USER_PASSWORDS=()
declare -a DB_NAMES=()

# =============================================================================
# Validation
# =============================================================================
validate() {
  if [ -z "$REGION" ]; then
    log_error "REGION is required. Example: BUCKET=... REGION=us-east-1 source db2client-configure.sh"
    return 1
  fi
  # BUCKET is optional — only needed for airgap SSL cert download
  if [ "$(whoami)" != "$DB2USER_NAME" ]; then
    log_error "This script must be run as $DB2USER_NAME. Run: sudo su - $DB2USER_NAME"
    return 1
  fi
  if [ ! -d "$HOME/sqllib" ]; then
    log_error "RT client not installed — $HOME/sqllib not found. Run db2-driver.sh as root first."
    return 1
  fi
}

# =============================================================================
# Credentials
# =============================================================================
set_credentials() {
  # CloudShell
  if curl -s --connect-timeout 1 http://127.0.0.1:1338/latest/meta-data/ >/dev/null 2>&1; then
    log_info "Detected AWS CloudShell environment"
    local token creds
    token=$(curl -sX PUT "http://127.0.0.1:1338/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
    creds=$(curl -s -H "Authorization: $token" "http://127.0.0.1:1338/latest/meta-data/container/security-credentials")
    export AWS_ACCESS_KEY_ID=$(echo "$creds"     | jq -r .AccessKeyId)
    export AWS_SECRET_ACCESS_KEY=$(echo "$creds" | jq -r .SecretAccessKey)
    export AWS_SESSION_TOKEN=$(echo "$creds"     | jq -r .Token)
    log_success "AWS credentials set from CloudShell"
    return
  fi
  # EC2 IMDSv2
  if curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/ >/dev/null 2>&1; then
    log_info "Detected EC2 environment"
    local token role creds
    token=$(curl -sX PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
    role=$(curl -s -H "X-aws-ec2-metadata-token: $token" http://169.254.169.254/latest/meta-data/iam/security-credentials/)
    creds=$(curl -s -H "X-aws-ec2-metadata-token: $token" "http://169.254.169.254/latest/meta-data/iam/security-credentials/$role")
    export AWS_ACCESS_KEY_ID=$(echo "$creds"     | jq -r .AccessKeyId)
    export AWS_SECRET_ACCESS_KEY=$(echo "$creds" | jq -r .SecretAccessKey)
    export AWS_SESSION_TOKEN=$(echo "$creds"     | jq -r .Token)
    log_success "AWS credentials set from EC2 instance role"
    return
  fi
  # Fall back to configured profile
  if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
    log_info "Using AWS credentials from environment variables"
  else
    log_info "Using AWS CLI profile: $PROFILE"
    export AWS_PROFILE="$PROFILE"
  fi
}

# =============================================================================
# Instance discovery
# =============================================================================
list_db_instances() {
  local query='DBInstances[?starts_with(Engine, `db2`)].DBInstanceIdentifier'
  local aws_output
  aws_output=$(aws rds describe-db-instances \
    --region "$REGION" \
    --query "$query" \
    --output text 2>/dev/null)

  local existing_instances=($aws_output)
  if [ ${#existing_instances[@]} -eq 0 ]; then
    log_error "No DB2 instances found in region $REGION"
    return 1
  fi

  if [ -n "${DB_INSTANCE_ID:-}" ]; then
    if [ "$DB_INSTANCE_ID" = "ALL" ]; then
      DB_INSTANCES=("${existing_instances[@]}")
      log_info "Processing ALL DB2 instances: ${DB_INSTANCES[*]}"
      return 0
    fi
    DB_INSTANCES=("$DB_INSTANCE_ID")
    log_info "Using specified instance: $DB_INSTANCE_ID"
    return 0
  fi

  if [ ${#existing_instances[@]} -eq 1 ]; then
    DB_INSTANCES=("${existing_instances[0]}")
    log_info "Auto-selected only available instance: ${existing_instances[0]}"
    return 0
  fi

  # Interactive selection — one instance only
  local choice=-1
  while [ "$choice" -lt 1 ] || [ "$choice" -gt ${#existing_instances[@]} ]; do
    echo "Available DB2 instances:" >&2
    for i in "${!existing_instances[@]}"; do
      echo "$((i+1)). ${existing_instances[$i]}" >&2
    done
    read -p "Select instance (1-${#existing_instances[@]}): " choice
    if [ "$choice" -ge 1 ] && [ "$choice" -le ${#existing_instances[@]} ]; then
      DB_INSTANCES=("${existing_instances[$((choice-1))]}")
    else
      log_warning "Invalid choice"
      choice=-1
    fi
  done
}

# =============================================================================
# Master user names and passwords
# =============================================================================
get_all_master_user_names() {
  MASTER_USER_NAMES=()
  for db_instance in "${DB_INSTANCES[@]}"; do
    local name
    name=$(aws rds describe-db-instances \
      --db-instance-identifier "$db_instance" \
      --region "$REGION" \
      --query "DBInstances[0].MasterUsername" \
      --output text 2>/dev/null)
    [ "$name" = "None" ] && name=""
    MASTER_USER_NAMES+=("$name")
    log_info "Master user for $db_instance: ${name:-<not found>}"
  done
}

get_all_master_passwords() {
  MASTER_USER_PASSWORDS=()
  local password_file="$HOME/.need_password"

  for db_instance in "${DB_INSTANCES[@]}"; do
    local secret_arn
    secret_arn=$(aws rds describe-db-instances \
      --db-instance-identifier "$db_instance" \
      --region "$REGION" \
      --query "DBInstances[0].MasterUserSecret.SecretArn" \
      --output text 2>/dev/null)

    if [ -n "$secret_arn" ] && [ "$secret_arn" != "None" ]; then
      local secret_json password
      secret_json=$(aws secretsmanager get-secret-value \
        --secret-id "$secret_arn" \
        --region "$REGION" \
        --query "SecretString" \
        --output text 2>/dev/null)
      password=$(jq -r '.password' <<< "$secret_json")
      if [ -n "$password" ]; then
        log_info "Retrieved password from Secrets Manager for $db_instance"
        MASTER_USER_PASSWORDS+=("$password")
        continue
      fi
    fi

    # Fall back to .need_password file
    local file_password=""
    if [ -f "$password_file" ]; then
      file_password=$(grep "^$db_instance " "$password_file" 2>/dev/null | cut -d' ' -f2-)
    fi

    if [ -n "$file_password" ] && [ "$file_password" != "replace this with the master user password" ]; then
      log_info "Using password from $password_file for $db_instance"
      MASTER_USER_PASSWORDS+=("$file_password")
    else
      log_warning "No password found for $db_instance — prompting"
      read -rsp "Password for $db_instance: " entered_password; echo
      MASTER_USER_PASSWORDS+=("${entered_password:-}")
    fi
  done
}

# =============================================================================
# Database name discovery
# =============================================================================
get_all_database_names() {
  local db_instance_id="$1" master_user="$2" master_password="$3" temp_dsn="${4:-RDSADMIN}"
  DB_NAMES=()

  local default_dbname
  default_dbname=$(aws rds describe-db-instances \
    --db-instance-identifier "$db_instance_id" \
    --region "$REGION" \
    --query "DBInstances[0].DBName" \
    --output text 2>/dev/null)
  [ "$default_dbname" = "None" ] && default_dbname=""

  if [ -n "$default_dbname" ]; then
    log_info "Default database: $default_dbname"
    DB_NAMES=("$default_dbname")
    return 0
  fi

  log_info "No default database — querying RDSADMIN for database list"
  local db_names
  mapfile -t db_names < <(
    db2 "connect to $temp_dsn user $master_user using '$master_password'" >/dev/null 2>&1
    db2 -x "SELECT database_name FROM TABLE(rdsadmin.list_databases()) WHERE UPPER(database_name) <> 'RDSADMIN'" 2>/dev/null
    db2 connect reset >/dev/null 2>&1
  )

  for dbname in "${db_names[@]}"; do
    dbname="$(echo "$dbname" | xargs)"
    [[ -n "$dbname" && ! "$dbname" =~ ^SQL ]] && DB_NAMES+=("$dbname")
  done

  if [ ${#DB_NAMES[@]} -eq 0 ]; then
    log_warning "No user databases found for $db_instance_id"
    return 1
  fi
  log_info "Found ${#DB_NAMES[@]} database(s): ${DB_NAMES[*]}"
}

# =============================================================================
# DSN helpers
# =============================================================================
generate_alias() {
  local name="${1^^}"
  name="${name:0:8}"
  local len=${#name}
  (( len == 0 )) && echo "" && return
  if (( len < 8 )); then
    echo "${name}S"
  else
    local prefix="${name:0:7}" last="${name: -1}"
    [ "$last" = "S" ] && echo "${prefix}U" || echo "${prefix}S"
  fi
}

writecfg_tcp() {
  local dsn=$1 dbname=$2 host=$3 port=$4
  db2cli writecfg add -dsn "$dsn" -database "$dbname" -host "$host" -port "$port" \
    -parameter "Authentication=SERVER_ENCRYPT"
}

writecfg_ssl() {
  local dsn=$1 dbname=$2 host=$3 port=$4
  db2cli writecfg add -dsn "$dsn" -database "$dbname" -host "$host" -port "$port" \
    -parameter "SSLServerCertificate=$HOME/$REGION-bundle.pem;SecurityTransportMode=SSL;TLSVersion=TLSV12"
}

get_ssl_port() {
  local param_group ssl_port
  param_group=$(aws rds describe-db-instances \
    --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
    --region "$REGION" \
    --query "DBInstances[0].DBParameterGroups[0].DBParameterGroupName" \
    --output text 2>/dev/null)
  [ -z "$param_group" ] && echo "" && return
  ssl_port=$(aws rds describe-db-parameters \
    --db-parameter-group-name "$param_group" \
    --region "$REGION" \
    --query "Parameters[?ParameterName=='ssl_svcename'].ParameterValue" \
    --output text 2>/dev/null)
  [ "$ssl_port" = "None" ] && ssl_port=""
  echo "$ssl_port"
}

download_pem_file() {
  local pem_file="$HOME/$REGION-bundle.pem"
  if [ -f "$pem_file" ]; then
    log_info "SSL certificate already present: $pem_file"
    return 0
  fi
  if [ -n "${BUCKET:-}" ]; then
    log_info "Downloading SSL certificate from s3://$BUCKET/ssl/$REGION-bundle.pem ..."
    aws s3 cp "s3://$BUCKET/ssl/$REGION-bundle.pem" "$pem_file" \
      --region "$REGION" --quiet
  else
    local url="https://truststore.pki.rds.amazonaws.com/$REGION/$REGION-bundle.pem"
    log_info "Downloading SSL certificate from $url ..."
    curl -sL "$url" -o "$pem_file"
  fi
  if [ $? -ne 0 ]; then
    log_error "Failed to download SSL certificate"
    return 1
  fi

  # Reorder certificates so RSA2048 is first.
  # Db2 CLP picks the first cert in the bundle for the TLS handshake.
  # RDS for Db2 only has RSA2048 — if RSA4096 is first (e.g. us-west-1)
  # the CLP connection fails. Python/JCC drivers iterate all certs so
  # they are unaffected. This reorder is a no-op for regions where
  # RSA2048 is already first (e.g. us-east-1).
  if command -v openssl &>/dev/null; then
    local tmp_pem; tmp_pem=$(mktemp)
    # Split PEM into individual certs, write RSA2048 first then the rest
    awk '
      /-----BEGIN CERTIFICATE-----/ { cert=""; in_cert=1 }
      in_cert { cert = cert $0 "\n" }
      /-----END CERTIFICATE-----/ { certs[++n] = cert; in_cert=0 }
      END {
        first=""; rest=""
        for (i=1; i<=n; i++) {
          # identify RSA2048 cert by its CN in the subject
          cmd = "echo \"" certs[i] "\" | openssl x509 -noout -subject 2>/dev/null"
          cmd | getline subj; close(cmd)
          if (subj ~ /RSA2048/) { first = certs[i] }
          else { rest = rest certs[i] }
        }
        printf "%s%s", first, rest
      }
    ' "$pem_file" > "$tmp_pem"
    # Only replace if reorder produced a non-empty result
    if [ -s "$tmp_pem" ]; then
      mv -f "$tmp_pem" "$pem_file"
      log_info "SSL cert reordered: RSA2048 first (Db2 CLP compatibility)"
    else
      rm -f "$tmp_pem"
      log_warning "SSL cert reorder skipped — openssl subject parse returned empty"
    fi
  else
    log_warning "openssl not found — skipping cert reorder (Db2 CLP may fail on regions where RSA2048 is not first)"
  fi

  log_success "SSL certificate saved to $pem_file"
}

build_connect_help_rt() {
  local alias_name=$1 db_name=$2
  HELP_COMMANDS+=("db2 \"connect to ${alias_name} user ${MASTER_USER_NAME} using '\$MASTER_USER_PASSWORD'\"  # ${db_name}")
}

print_all_help() {
  [ ${#HELP_COMMANDS[@]} -eq 0 ] && return
  echo ""
  echo "  ========================="
  echo "  db2 terminate"
  for c in "${HELP_COMMANDS[@]}"; do echo "  $c"; done
  echo "  ========================="
  echo ""
}

# =============================================================================
# Main DSN configuration
# =============================================================================
configure_dsn() {
  log_info "============================================================================"
  log_info "Creating DB2 RT DSN entries for RDS DB2 instance(s)"
  log_info "Region: $REGION"
  log_info "============================================================================"

  list_db_instances || return 1
  get_all_master_user_names
  get_all_master_passwords

  # Clean slate before writing any DSN entries
  rm -f "$HOME/sqllib/cfg/db2dsdriver.cfg"

  for i in "${!DB_INSTANCES[@]}"; do
    local DB_INSTANCE_IDENTIFIER="${DB_INSTANCES[$i]}"
    local MASTER_USER_NAME="${MASTER_USER_NAMES[$i]}"
    local MASTER_USER_PASSWORD="${MASTER_USER_PASSWORDS[$i]}"
    local SUFFIX; [ ${#DB_INSTANCES[@]} -eq 1 ] && SUFFIX="" || SUFFIX="$i"

    log_info "============================================================================"
    log_info "Processing: $DB_INSTANCE_IDENTIFIER"

    [ -z "$MASTER_USER_NAME" ]     && log_error "No master user for $DB_INSTANCE_IDENTIFIER — skipping" && continue
    [ -z "$MASTER_USER_PASSWORD" ] && log_warning "No password for $DB_INSTANCE_IDENTIFIER — skipping"  && continue

    local DB_ADDRESS DB_TCP_IP_PORT
    DB_ADDRESS=$(aws rds describe-db-instances \
      --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
      --region "$REGION" \
      --query "DBInstances[0].Endpoint.Address" \
      --output text 2>/dev/null)
    DB_TCP_IP_PORT=$(aws rds describe-db-instances \
      --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
      --region "$REGION" \
      --query "DBInstances[0].Endpoint.Port" \
      --output text 2>/dev/null)

    [ -z "$DB_ADDRESS" ] && log_error "No endpoint for $DB_INSTANCE_IDENTIFIER — skipping" && continue

    # Write temp DSN for this instance to enable database name query
    writecfg_tcp "RDSADMIN${SUFFIX}" "RDSADMIN" "$DB_ADDRESS" "$DB_TCP_IP_PORT" >/dev/null 2>&1

    # Fetch database names BEFORE writing final config
    get_all_database_names "$DB_INSTANCE_IDENTIFIER" "$MASTER_USER_NAME" "$MASTER_USER_PASSWORD" "RDSADMIN${SUFFIX}" || true
    log_info "Databases to register: ${DB_NAMES[*]:-<none found>}"

    if [ -n "$DB_TCP_IP_PORT" ]; then
      local admin_dsn="RDSADMIN${SUFFIX}"
      log_info "Creating TCP DSN: $admin_dsn"
      writecfg_tcp "$admin_dsn" "RDSADMIN" "$DB_ADDRESS" "$DB_TCP_IP_PORT"
      build_connect_help_rt "$admin_dsn" "RDSADMIN"
      for dbname in "${DB_NAMES[@]}"; do
        local aliasname="${dbname}${SUFFIX}"
        log_info "Registering $dbname as $aliasname (TCP)"
        writecfg_tcp "$aliasname" "$dbname" "$DB_ADDRESS" "$DB_TCP_IP_PORT"
        build_connect_help_rt "$aliasname" "$dbname"
      done
    fi

    # SSL entries
    local SSL_PORT
    SSL_PORT=$(get_ssl_port)
    if [ -n "$SSL_PORT" ]; then
      download_pem_file || continue
      log_info "SSL port: $SSL_PORT"
      local ssl_admin_dsn="RDSDBSSL${SUFFIX}"
      log_info "Creating SSL DSN: $ssl_admin_dsn"
      writecfg_ssl "$ssl_admin_dsn" "RDSADMIN" "$DB_ADDRESS" "$SSL_PORT"
      build_connect_help_rt "$ssl_admin_dsn" "RDSADMIN SSL"

      for dbname in "${DB_NAMES[@]}"; do
        local aliasname="$(generate_alias "$dbname")${SUFFIX}"
        log_info "Registering $dbname as $aliasname (SSL)"
        writecfg_ssl "$aliasname" "$dbname" "$DB_ADDRESS" "$SSL_PORT"
        build_connect_help_rt "$aliasname" "$dbname SSL"
      done
    else
      log_info "No SSL port configured for $DB_INSTANCE_IDENTIFIER — skipping SSL entries"
    fi
  done
}

# =============================================================================
# Entry point
# =============================================================================
main() {
  unset DB_INSTANCE_ID
  validate || return 1
  set_credentials
  configure_dsn || return 1
  print_all_help | tee "$HOME/CONN_HELP_README.txt" >&2
  log_info "Run 'db2 terminate' then use the commands above (also saved to ~/CONN_HELP_README.txt)"

  # Write instance registry (instance→DSN mapping, no passwords)
  local registry="$HOME/.db2instances"
  # Append or create entry for each instance
  touch "$registry"
  for i in "${!DB_INSTANCES[@]}"; do
    local suffix; [ ${#DB_INSTANCES[@]} -eq 1 ] && suffix="" || suffix="$i"
    local tcp_dsn="RDSADMIN${suffix}" ssl_dsn="RDSDBSSL${suffix}"
    # Remove existing entry for this instance then re-add
    sed -i "/^${DB_INSTANCES[$i]}|/d" "$registry"
    echo "${DB_INSTANCES[$i]}|${MASTER_USER_NAMES[$i]}|${tcp_dsn}|${ssl_dsn}|${REGION}" >> "$registry"
  done
  chmod 600 "$registry"
  log_success "Instance registry saved to $registry"

  # Persist credentials for the last processed instance to ~/.db2env
  # Uses printf %q to safely escape special characters in the password.
  local last=$((${#DB_INSTANCES[@]} - 1))
  export MASTER_USER_NAME="${MASTER_USER_NAMES[$last]}"
  export MASTER_USER_PASSWORD="${MASTER_USER_PASSWORDS[$last]}"
  export DB_DSN="RDSADMIN"
  {
    echo "export REGION=$(printf '%q' "$REGION")"
    echo "export DB_INSTANCE_ID=$(printf '%q' "${DB_INSTANCES[$last]}")"
    echo "export DB_DSN=$(printf '%q' 'RDSADMIN')"
    echo "export MASTER_USER_NAME=$(printf '%q' "${MASTER_USER_NAMES[$last]}")"
    echo "export MASTER_USER_PASSWORD=$(printf '%q' "${MASTER_USER_PASSWORDS[$last]}")"
  } > "$HOME/.db2env"
  chmod 600 "$HOME/.db2env"
  log_success "Credentials saved to ~/.db2env — auto-loaded by functions.sh"
  log_success "DSN configuration complete. Connection help saved to ~/CONN_HELP_README.txt"
  # Add source functions.sh to shell profile files if not already there
  local source_line='source ~/functions.sh'
  local comment='# DB2 helper functions'
  for profile in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; do
    [ -f "$profile" ] || continue
    if ! grep -q 'source ~/functions.sh' "$profile" 2>/dev/null; then
      echo '' >> "$profile"
      echo "$comment" >> "$profile"
      echo "$source_line" >> "$profile"
      log_success "Added 'source ~/functions.sh' to $profile"
    fi
  done
  log_info "Run 'source ~/.bashrc' or log out and back in to activate. Then run 'db2_help' to see available helper functions."
  echo "" >&2
  echo "  ============================" >&2
  echo "  source ~/.bashrc"            >&2
  echo "  db2_help"                    >&2
  echo "  ============================" >&2
  echo "" >&2
}

main
