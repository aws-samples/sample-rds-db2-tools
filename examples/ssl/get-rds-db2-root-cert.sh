#!/usr/bin/env bash
#
# get-rds-db2-root-cert.sh
#
# Connects to an RDS for Db2 SSL endpoint, retrieves the full certificate
# chain, and writes the ROOT certificate (the last cert in the chain) to a
# PEM file. Optionally writes the full chain as well.
#
# Usage:
#   ./get-rds-db2-root-cert.sh <host> <port> [output-file]
#
# Examples:
#   ./get-rds-db2-root-cert.sh my-host.rds.amazonaws.com 50443
#   ./get-rds-db2-root-cert.sh my-host.rds.amazonaws.com 50443 root.pem
#

set -euo pipefail

# ---- Arguments (host and port are required) ---------------------------------
HOST="${1:-}"
PORT="${2:-}"

if [[ -z "${HOST}" || -z "${PORT}" ]]; then
  echo "ERROR: host and port are required." >&2
  echo "Usage: $0 <host> <port> [output-file]" >&2
  exit 1
fi

OUTPUT="${3:-${HOST%%.*}-root-ca.pem}"
CHAIN_OUTPUT="${HOST%%.*}-full-chain.pem"

# ---- Pre-flight checks ------------------------------------------------------
if ! command -v openssl >/dev/null 2>&1; then
  echo "ERROR: openssl is not installed or not on PATH." >&2
  exit 1
fi

echo ">> Connecting to ${HOST}:${PORT} ..."

# ---- Fetch the certificate chain -------------------------------------------
# -showcerts returns every cert the server presents (leaf -> intermediates -> root).
CHAIN="$(echo -n | openssl s_client -showcerts -connect "${HOST}:${PORT}" 2>/dev/null)"

if [[ -z "${CHAIN}" ]]; then
  echo "ERROR: No data returned. Check the host, port, and network/SG access." >&2
  exit 1
fi

# ---- Save the full chain (all PEM blocks) -----------------------------------
echo "${CHAIN}" \
  | awk '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/' \
  > "${CHAIN_OUTPUT}"

if [[ ! -s "${CHAIN_OUTPUT}" ]]; then
  echo "ERROR: No certificates found in the server response." >&2
  rm -f "${CHAIN_OUTPUT}"
  exit 1
fi

# ---- Extract the ROOT certificate (last cert block in the chain) ------------
# Use csplit to break the chain into one file per certificate, then keep the last.
TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

awk -v dir="${TMPDIR}" '
  /-----BEGIN CERTIFICATE-----/ { n++; f = sprintf("%s/cert-%02d.pem", dir, n) }
  n > 0 { print > f }
' "${CHAIN_OUTPUT}"

LAST_CERT="$(ls "${TMPDIR}"/cert-*.pem 2>/dev/null | sort | tail -n 1)"

if [[ -z "${LAST_CERT}" ]]; then
  echo "ERROR: Could not isolate the root certificate." >&2
  exit 1
fi

cp "${LAST_CERT}" "${OUTPUT}"

# ---- Report -----------------------------------------------------------------
CERT_COUNT="$(ls "${TMPDIR}"/cert-*.pem | wc -l | tr -d ' ')"

echo
echo ">> Certificates in chain : ${CERT_COUNT}"
echo ">> Full chain written to : ${CHAIN_OUTPUT}"
echo ">> Root cert written to  : ${OUTPUT}"
echo
echo ">> Root certificate details:"
openssl x509 -in "${OUTPUT}" -noout -subject -issuer -dates
