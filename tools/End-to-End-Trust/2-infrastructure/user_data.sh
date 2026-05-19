#!/bin/bash
exec > >(tee /var/log/user-data.log|logger -t user-data -s 2>/dev/console) 2>&1

AWS_REGION="${aws_region}"
CERTIFICATE_SECRET_ARN="${certificate_secret_arn}"

# Install AWS CLI v2
dnf install -y unzip
curl -sS "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscliv2.zip"
curl -sS "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip.sha256" -o "/tmp/awscliv2.zip.sha256"
cd /tmp && sha256sum -c awscliv2.zip.sha256
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install --update
aws --version

# Cleanup and prepare dnf
dnf clean all && rm -rf /var/cache/dnf
for i in {1..15}; do
  if dnf makecache; then
    echo "DNF cache created successfully"
    break
  else
    echo "Attempt $i failed for dnf makecache, retrying..."
    sleep 5
  fi
done

# Import OpenResty GPG key
rpm --import https://openresty.org/package/pubkey.gpg

# Add OpenResty repository
cat > /etc/yum.repos.d/openresty.repo << 'REPOEOF'
[openresty]
name=Official OpenResty Open Source Repository for Amazon Linux
baseurl=https://openresty.org/package/amazon/2023/$basearch
skip_if_unavailable=False
gpgcheck=1
repo_gpgcheck=1
enabled=1
gpgkey=https://openresty.org/package/pubkey.gpg
REPOEOF

# Install dependencies with retry
for i in {1..3}; do
  if dnf install -y jq openssl nmap-ncat cronie rsyslog openresty-1.25.3.1 openresty-resty-1.25.3.1; then
    echo "Packages installed successfully"
    break
  else
    echo "Package installation attempt $i failed, cleaning cache and retrying..."
    dnf clean all && rm -rf /var/cache/dnf
    sleep 10
  fi
done

# Enable and start crond
systemctl enable crond
systemctl start crond

# Enable and start rsyslog for /var/log/messages
systemctl enable rsyslog
systemctl start rsyslog

# Configure OpenResty service
mkdir -p /etc/systemd/system/openresty.service.d
cat > /etc/systemd/system/openresty.service.d/override.conf << 'EOF'
[Service]
ExecStart=
ExecStart=/usr/local/openresty/bin/openresty -g 'daemon on; master_process on;' -c /etc/openresty/proxy.conf
ExecReload=
ExecReload=/usr/local/openresty/bin/openresty -s reload -c /etc/openresty/proxy.conf
EOF

systemctl daemon-reload
systemctl enable openresty

# Setup certificates
mkdir -p /etc/openresty/certs
chmod 700 /etc/openresty/certs

# Retrieve certificate from Secrets Manager
SECRET=$(aws secretsmanager get-secret-value --secret-id "$CERTIFICATE_SECRET_ARN" --region "$AWS_REGION" --query SecretString --output text)
if [ -n "$SECRET" ] && echo "$SECRET" | jq empty >/dev/null 2>&1; then
  echo "$SECRET" | jq -r '.certificate' > /etc/openresty/certs/proxy-cert.pem
  echo "$SECRET" | jq -r '.privateKey' > /etc/openresty/certs/proxy-key.pem
  chmod 600 /etc/openresty/certs/proxy-cert.pem /etc/openresty/certs/proxy-key.pem
else
  echo "Failed to retrieve or parse secret." >&2
  exit 1
fi

# Create initial minimal Nginx configuration
cat > /etc/openresty/proxy.conf << 'EOF'
events {
    worker_connections 1024;
}

stream {
    resolver 169.254.169.253;
    
    # Initial placeholder - will be updated by cron job
    server {
        listen 443;
        proxy_pass 127.0.0.1:65535;
    }
}
EOF

# Create dedicated non-root service user for the config update script
useradd --system --no-create-home --shell /sbin/nologin openresty-updater

# Grant openresty-updater only the permissions it needs:
#   - write nginx config and reload openresty
#   - write to the log file
#   - run openresty -t and openresty -s reload via sudo (no password)
cat > /etc/sudoers.d/openresty-updater << 'SUDOEOF'
openresty-updater ALL=(root) NOPASSWD: /usr/local/openresty/bin/openresty -t -c /etc/openresty/proxy.conf
openresty-updater ALL=(root) NOPASSWD: /usr/local/openresty/bin/openresty -s reload -c /etc/openresty/proxy.conf
SUDOEOF
chmod 440 /etc/sudoers.d/openresty-updater

# openresty-updater needs to write the nginx config and log
chown root:openresty-updater /etc/openresty
chmod 775 /etc/openresty

touch /var/log/nginx-config-update.log
chown openresty-updater:openresty-updater /var/log/nginx-config-update.log
chmod 644 /var/log/nginx-config-update.log

# Create update script BEFORE calling it
cat > /usr/local/bin/update-nginx-config.sh << 'UPDATEEOF'
#!/bin/bash
set -euo pipefail

LOG_TAG="update-nginx-config"
PROXY_CONF="/etc/openresty/proxy.conf"
PROXY_CONF_TMP="/etc/openresty/proxy.conf.tmp"

log()  { logger -t "$LOG_TAG" -- "$*"; echo "$*"; }
warn() { logger -t "$LOG_TAG" -- "WARN: $*"; echo "WARN: $*" >&2; }
die()  { logger -t "$LOG_TAG" -- "ERROR: $*"; echo "ERROR: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

# Domain: labels separated by dots, each label [a-zA-Z0-9-], no leading/trailing dash
validate_domain() {
    local domain="$1"
    if [[ ! "$domain" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$ ]]; then
        warn "Rejected invalid domain: '$domain'"
        return 1
    fi
    # Reject anything containing nginx directive characters
    if [[ "$domain" =~ [';{}\\$`|&<>'] ]]; then
        warn "Rejected domain with forbidden characters: '$domain'"
        return 1
    fi
    return 0
}

# Port: numeric, 1-65535
validate_port() {
    local port="$1"
    if [[ ! "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
        warn "Rejected invalid port: '$port'"
        return 1
    fi
    return 0
}

# Endpoint: strict host:port — host is a valid hostname or IPv4, port is numeric
validate_endpoint() {
    local endpoint="$1"
    local host port
    # Must be exactly host:port with no extra colons (not IPv6)
    if [[ ! "$endpoint" =~ ^([a-zA-Z0-9]([a-zA-Z0-9.-]{0,253}[a-zA-Z0-9])?):([0-9]+)$ ]]; then
        warn "Rejected invalid endpoint format: '$endpoint'"
        return 1
    fi
    host="${BASH_REMATCH[1]}"
    port="${BASH_REMATCH[3]}"
    if ! validate_port "$port"; then
        warn "Rejected endpoint with invalid port: '$endpoint'"
        return 1
    fi
    # Reject nginx directive characters in host
    if [[ "$host" =~ [';{}\\$`|&<>'] ]]; then
        warn "Rejected endpoint with forbidden characters: '$endpoint'"
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Fetch and parse SSM mapping
# ---------------------------------------------------------------------------
MAPPING=$(aws ssm get-parameter \
    --name /rds/proxy/mappings/${domain_name} \
    --region ${aws_region} \
    --query Parameter.Value \
    --output text 2>/dev/null || echo '{}')

# Validate it is parseable JSON
if ! echo "$MAPPING" | jq empty 2>/dev/null; then
    die "SSM parameter is not valid JSON — aborting config update"
fi

# Extract and validate all keys (domain:port) and values (endpoint:port)
declare -A VALID_MAPPINGS
REJECTED=0

while IFS=$'\t' read -r key value; do
    # Split key into domain and client_port
    client_domain="${key%%:*}"
    client_port="${key##*:}"

    if ! validate_domain "$client_domain"; then
        REJECTED=$((REJECTED + 1))
        continue
    fi
    if ! validate_port "$client_port"; then
        REJECTED=$((REJECTED + 1))
        continue
    fi
    if ! validate_endpoint "$value"; then
        REJECTED=$((REJECTED + 1))
        continue
    fi

    VALID_MAPPINGS["$key"]="$value"
done < <(echo "$MAPPING" | jq -r 'to_entries[] | [.key, .value] | @tsv')

if (( REJECTED > 0 )); then
    warn "$REJECTED mapping(s) rejected due to validation failures — they will not appear in config"
fi

if (( ${#VALID_MAPPINGS[@]} == 0 )); then
    log "No valid mappings found — skipping config update"
    exit 0
fi

# Extract unique ports from validated keys only
PORTS=$(for key in "${!VALID_MAPPINGS[@]}"; do echo "${key##*:}"; done | sort -un)

# ---------------------------------------------------------------------------
# Build nginx config into a temp file — never write partial config directly
# ---------------------------------------------------------------------------
cat > "$PROXY_CONF_TMP" << 'NGINX_START'
events {
    worker_connections 1024;
}

stream {
    resolver 169.254.169.253;
NGINX_START

for PORT in $PORTS; do
    echo ""                                                          >> "$PROXY_CONF_TMP"
    echo "    # Mappings for port $PORT"                            >> "$PROXY_CONF_TMP"
    echo "    map \$ssl_preread_server_name \$rds_endpoint_$PORT {" >> "$PROXY_CONF_TMP"
    echo "        default 127.0.0.1:65535;"                         >> "$PROXY_CONF_TMP"

    for key in "${!VALID_MAPPINGS[@]}"; do
        key_port="${key##*:}"
        if [[ "$key_port" == "$PORT" ]]; then
            key_domain="${key%%:*}"
            echo "        $key_domain ${VALID_MAPPINGS[$key]};"     >> "$PROXY_CONF_TMP"
        fi
    done

    echo "    }"                                                     >> "$PROXY_CONF_TMP"
    echo ""                                                          >> "$PROXY_CONF_TMP"
    echo "    server {"                                              >> "$PROXY_CONF_TMP"
    echo "        listen $PORT;"                                     >> "$PROXY_CONF_TMP"
    echo "        ssl_preread on;"                                   >> "$PROXY_CONF_TMP"
    echo "        proxy_pass \$rds_endpoint_$PORT;"                 >> "$PROXY_CONF_TMP"
    echo "        proxy_timeout 3600s;"                              >> "$PROXY_CONF_TMP"
    echo "        proxy_responses 1;"                                >> "$PROXY_CONF_TMP"
    echo "    }"                                                     >> "$PROXY_CONF_TMP"
done

echo "}" >> "$PROXY_CONF_TMP"

# ---------------------------------------------------------------------------
# Test the temp config before replacing the live one (atomic swap)
# ---------------------------------------------------------------------------
if sudo /usr/local/openresty/bin/openresty -t -c "$PROXY_CONF_TMP"; then
    mv "$PROXY_CONF_TMP" "$PROXY_CONF"
    sudo /usr/local/openresty/bin/openresty -s reload -c "$PROXY_CONF"
    log "Nginx configuration updated and reloaded successfully. Active ports: $PORTS"
else
    rm -f "$PROXY_CONF_TMP"
    die "Nginx config test failed — keeping existing config"
fi
UPDATEEOF

chmod 750 /usr/local/bin/update-nginx-config.sh
chown root:openresty-updater /usr/local/bin/update-nginx-config.sh

# Run initial config update as root (openresty-updater not yet fully set up in this session)
/usr/local/bin/update-nginx-config.sh || echo "Initial config update will run via cron (waiting for SSM parameter)"

# Test and start Nginx
openresty -t -c /etc/openresty/proxy.conf
systemctl start openresty

# Setup cron job — runs as openresty-updater, not root
cat > /etc/cron.d/nginx-update << 'EOF'

*/5 * * * * openresty-updater /usr/local/bin/update-nginx-config.sh >> /var/log/nginx-config-update.log 2>&1

EOF
chmod 644 /etc/cron.d/nginx-update

echo "EC2 proxy setup completed successfully"
