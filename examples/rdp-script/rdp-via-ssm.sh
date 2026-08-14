#!/usr/bin/env bash
#
# rdp-via-ssm.sh
#
# Opens an RDP session to a Windows EC2 instance running in a PRIVATE subnet
# from macOS, tunnelling RDP (TCP 3389) through AWS Systems Manager Session
# Manager. No inbound security group rule, no public IP, no bastion host and no
# VPN are required.
#
# The script:
#   1. Lists running Windows instances that are managed by SSM.
#   2. Starts an AWS-StartPortForwardingSession tunnel to 127.0.0.1:<local-port>.
#   3. Writes a locked-down, credential-free .rdp file to a private temp dir.
#   4. Launches the macOS RDP client and tears everything down on exit.
#
# Usage:
#   ./rdp-via-ssm.sh [-r region] [-p profile] [-l local_port] [-i instance_id]
#                    [-c] [-h]
#
# Examples:
#   ./rdp-via-ssm.sh
#   ./rdp-via-ssm.sh -r us-west-2 -p my-sso-profile
#   ./rdp-via-ssm.sh -i i-0123456789abcdef0 -l 13390
#
# Requirements:
#   - AWS CLI v2 and the Session Manager plugin
#   - Microsoft "Windows App" (or legacy "Microsoft Remote Desktop")
#   - Target instance: SSM Agent running + instance profile with
#     AmazonSSMManagedInstanceCore (see README.md)
#
# See README.md for the least-privilege IAM policy and the security notes.
#

set -euo pipefail

# ---- Defaults ---------------------------------------------------------------
DEFAULT_REGION="us-east-1"
DEFAULT_PROFILE="default"
DEFAULT_LOCAL_PORT="13389"
REMOTE_PORT="3389"

REGION=""
PROFILE=""
LOCAL_PORT=""
INSTANCE_ID=""
# Clipboard redirection is a data movement path in both directions, so it is
# opt-in (-c) rather than on by default.
REDIRECT_CLIPBOARD=0

SSM_PID=""
RDP_DIR=""

usage() {
  cat >&2 <<USAGE
Usage: $0 [-r region] [-p profile] [-l local_port] [-i instance_id] [-c] [-h]
    -r region       AWS region (default: ${DEFAULT_REGION})
    -p profile      AWS CLI profile (default: ${DEFAULT_PROFILE})
    -l local_port   Local loopback port for the tunnel (default: ${DEFAULT_LOCAL_PORT})
    -i instance_id  Target instance ID; skips the interactive picker
    -c              Enable clipboard redirection (off by default)
    -h              Show this help
USAGE
  exit 1
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

# ---- Argument parsing -------------------------------------------------------
while getopts ":r:p:l:i:ch" opt; do
  case "${opt}" in
    r) REGION="${OPTARG}" ;;
    p) PROFILE="${OPTARG}" ;;
    l) LOCAL_PORT="${OPTARG}" ;;
    i) INSTANCE_ID="${OPTARG}" ;;
    c) REDIRECT_CLIPBOARD=1 ;;
    h) usage ;;
    \?) echo "Invalid option: -${OPTARG}" >&2; usage ;;
    :) echo "Option -${OPTARG} requires an argument." >&2; usage ;;
  esac
done

REGION="${REGION:-${DEFAULT_REGION}}"
PROFILE="${PROFILE:-${DEFAULT_PROFILE}}"
LOCAL_PORT="${LOCAL_PORT:-${DEFAULT_LOCAL_PORT}}"

# ---- Input validation -------------------------------------------------------
# Every value below is interpolated into an AWS CLI argument list (and the port
# into a JSON document), so each one is allow-listed before use.
validate_port() {
  local port="$1"
  [[ "${port}" =~ ^[0-9]{1,5}$ ]] || return 1
  # Unprivileged ports only: binding <1024 would need root.
  (( port >= 1024 && port <= 65535 )) || return 1
}

[[ "${REGION}" =~ ^[a-z0-9-]{1,32}$ ]] \
  || die "Invalid region: '${REGION}'"
[[ "${PROFILE}" =~ ^[A-Za-z0-9._-]{1,64}$ ]] \
  || die "Invalid profile name: '${PROFILE}'"
validate_port "${LOCAL_PORT}" \
  || die "Invalid local port: '${LOCAL_PORT}' (expected 1024-65535)"
if [[ -n "${INSTANCE_ID}" ]]; then
  [[ "${INSTANCE_ID}" =~ ^i-[0-9a-f]{8,17}$ ]] \
    || die "Invalid instance ID: '${INSTANCE_ID}'"
fi

# ---- Preflight checks -------------------------------------------------------
command -v aws >/dev/null 2>&1 \
  || die "AWS CLI not found. Install AWS CLI v2 and try again."
command -v session-manager-plugin >/dev/null 2>&1 \
  || die "session-manager-plugin not found. See README.md for install steps."

# ---- Instance discovery -----------------------------------------------------
# Only instances that are both running and registered with SSM can be reached,
# so the SSM inventory is intersected with the EC2 Windows instance list.
get_ssm_managed_ids() {
  aws ssm describe-instance-information \
    --filters "Key=PingStatus,Values=Online" \
    --query "InstanceInformationList[].InstanceId" \
    --output text \
    --region "${REGION}" \
    --profile "${PROFILE}" 2>/dev/null | tr '\t' '\n' | sort -u
}

get_running_windows_instances() {
  aws ec2 describe-instances \
    --filters "Name=instance-state-name,Values=running" \
              "Name=platform,Values=windows" \
    --query "Reservations[].Instances[].[InstanceId, (Tags[?Key=='Name'].Value | [0])]" \
    --output text \
    --region "${REGION}" \
    --profile "${PROFILE}"
}

if [[ -z "${INSTANCE_ID}" ]]; then
  managed_ids="$(get_ssm_managed_ids || true)"
  instances="$(get_running_windows_instances)" \
    || die "Could not list EC2 instances. Check your credentials for profile '${PROFILE}'."

  declare -a ids=() names=()
  while IFS=$'\t' read -r id name; do
    [[ -n "${id}" ]] || continue
    ids+=("${id}")
    names+=("${name:-Unnamed}")
  done <<< "$(printf '%s\n' "${instances}" | sort -t $'\t' -k2)"

  (( ${#ids[@]} > 0 )) \
    || die "No running Windows instances found in ${REGION} for profile '${PROFILE}'."

  echo "Running Windows instances in ${REGION}:"
  for i in "${!ids[@]}"; do
    ssm_state="not SSM-managed"
    if grep -qx "${ids[$i]}" <<< "${managed_ids}"; then
      ssm_state="SSM online"
    fi
    printf '%3d) %-22s %-14s %s\n' \
      "$(( i + 1 ))" "${ids[$i]}" "[${ssm_state}]" "${names[$i]}"
  done

  while true; do
    read -r -p "Select an instance number: " choice
    if [[ "${choice}" =~ ^[0-9]+$ ]] \
      && (( choice >= 1 && choice <= ${#ids[@]} )); then
      INSTANCE_ID="${ids[$(( choice - 1 ))]}"
      INSTANCE_NAME="${names[$(( choice - 1 ))]}"
      break
    fi
    echo "Invalid selection. Enter a number from the list above."
  done
fi
INSTANCE_NAME="${INSTANCE_NAME:-Unnamed}"

# ---- Free local port --------------------------------------------------------
while lsof -nP -iTCP:"${LOCAL_PORT}" -sTCP:LISTEN >/dev/null 2>&1; do
  echo "Local port ${LOCAL_PORT} is already in use."
  read -r -p "Enter a different local port (1024-65535): " LOCAL_PORT
  validate_port "${LOCAL_PORT}" || die "Invalid local port: '${LOCAL_PORT}'"
done

# ---- Cleanup ----------------------------------------------------------------
# Runs on normal exit and on Ctrl+C so the tunnel is never left listening and
# the .rdp file never outlives the session.
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if [[ -n "${SSM_PID}" ]]; then
    echo ""
    echo "Closing SSM tunnel..."
    # Kill the whole process group so session-manager-plugin goes down with the
    # aws wrapper and no orphaned listener is left behind.
    kill -TERM -- -"${SSM_PID}" 2>/dev/null || kill -TERM "${SSM_PID}" 2>/dev/null || true
    wait "${SSM_PID}" 2>/dev/null || true
  fi
  [[ -n "${RDP_DIR}" ]] && rm -rf "${RDP_DIR}"
  echo "Done."
  exit "${rc}"
}
trap cleanup EXIT INT TERM

# ---- Start the tunnel -------------------------------------------------------
echo ""
echo "Starting SSM port forwarding session"
echo "  Target:  ${INSTANCE_ID} (${INSTANCE_NAME})"
echo "  Region:  ${REGION}"
echo "  Profile: ${PROFILE}"
echo "  Tunnel:  127.0.0.1:${LOCAL_PORT} -> ${REMOTE_PORT}/tcp on the instance"
echo ""

# Job control is enabled so the background job gets its own process group,
# which lets cleanup() terminate the plugin along with the CLI wrapper.
set -m
aws ssm start-session \
  --target "${INSTANCE_ID}" \
  --document-name AWS-StartPortForwardingSession \
  --parameters "{\"portNumber\":[\"${REMOTE_PORT}\"],\"localPortNumber\":[\"${LOCAL_PORT}\"]}" \
  --region "${REGION}" \
  --profile "${PROFILE}" &
SSM_PID=$!
set +m

echo "Waiting for the tunnel to come up on 127.0.0.1:${LOCAL_PORT} ..."
tunnel_up=0
for _ in $(seq 1 20); do
  if nc -z 127.0.0.1 "${LOCAL_PORT}" >/dev/null 2>&1; then
    tunnel_up=1
    break
  fi
  kill -0 "${SSM_PID}" 2>/dev/null \
    || die "SSM session exited. Confirm the instance is SSM-managed and your IAM permissions allow ssm:StartSession."
  sleep 1
done
(( tunnel_up == 1 )) || die "Tunnel did not become ready in time."
echo "Tunnel is up."

# Confirm the listener is bound to loopback only. If it is bound to 0.0.0.0,
# anyone who can reach this Mac could ride the tunnel into the VPC.
if lsof -nP -iTCP:"${LOCAL_PORT}" -sTCP:LISTEN 2>/dev/null \
    | grep -Eq '(\*|0\.0\.0\.0):'"${LOCAL_PORT}"; then
  echo "WARNING: the tunnel is listening on all interfaces, not just 127.0.0.1." >&2
  echo "         Make sure your macOS firewall blocks inbound connections." >&2
fi

# ---- Build a hardened, credential-free .rdp file ----------------------------
# 0700 directory + 0600 file, created before any content is written, so the
# profile is never briefly world-readable on a shared Mac.
RDP_DIR="$(umask 077 && mktemp -d "${TMPDIR:-/tmp}/ssm-rdp.XXXXXXXX")"
RDP_FILE="${RDP_DIR}/${INSTANCE_ID}.rdp"
(umask 077 && : > "${RDP_FILE}")
chmod 600 "${RDP_FILE}"

cat > "${RDP_FILE}" <<EOF
full address:s:localhost:${LOCAL_PORT}
prompt for credentials:i:1
promptcredentialonce:i:0
enablecredsspsupport:i:1
administrative session:i:0
authentication level:i:2
redirectclipboard:i:${REDIRECT_CLIPBOARD}
audiomode:i:2
audiocapturemode:i:0
redirectprinters:i:0
redirectsmartcards:i:0
redirectcomports:i:0
drivestoredirect:s:
devicestoredirect:s:
EOF

# Notes on the settings above:
#   prompt for credentials:1  - credentials are typed into the client each time;
#                               no username or password blob is stored on disk.
#   authentication level:2    - warn on server authentication failure and let
#                               the user decide. The certificate name will not
#                               match "localhost" over a tunnel, so 0 ("connect
#                               without warning") is commonly used here; that
#                               silently accepts any server, so 2 is used
#                               instead. Verify the thumbprint on first connect.
#   redirect*/drivestoredirect - clipboard, drive, printer, smart card and COM
#                               redirection are off so nothing crosses the RDP
#                               channel unless explicitly enabled with -c.

# ---- Launch the client ------------------------------------------------------
echo "Launching the RDP client..."
if open -a "Windows App" "${RDP_FILE}" 2>/dev/null; then
  echo "Opened in Windows App."
elif open -a "Microsoft Remote Desktop" "${RDP_FILE}" 2>/dev/null; then
  echo "Opened in Microsoft Remote Desktop."
else
  echo "Neither Windows App nor Microsoft Remote Desktop was found." >&2
  echo "Install one of them, or connect manually to localhost:${LOCAL_PORT}." >&2
fi

cat <<EOF

RDP session launched. Keep this terminal open to hold the tunnel open.
Press Ctrl+C when you are finished to close the tunnel and delete the .rdp file.

EOF

# Block until the tunnel exits or the user interrupts.
wait "${SSM_PID}" 2>/dev/null || true
