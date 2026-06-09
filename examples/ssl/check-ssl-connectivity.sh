#!/usr/bin/env bash
#
# test-db2-ssl-connection.sh
#
# Tests an SSL connection to an RDS for Db2 instance using the IBM Db2 JDBC
# driver (com.ibm.db2.jcc.DB2Jcc).
#
# Usage:
#   ./test-db2-ssl-connection.sh \
#       -j /path/to/db2jcc4.jar \
#       -H egdetx-rds-db2-env2.cwtsk5sbk7pc.us-east-2.rds.amazonaws.com:50443 \
#       -d EDGETXE1 \
#       -u <user> \
#       -p <password> \
#       -c /path/to/certchain.pem \
#       [-t TLSv1.2]
#
# Flags:
#   -j  Path to the Db2 JDBC driver jar (db2jcc4.jar)   [required]
#   -H  Db2 host in the form host:port                  [required]
#   -d  Database name                                   [required]
#   -u  Database user                                   [required]
#   -p  Database password                               [required]
#   -c  Path to the SSL certificate chain (PEM)         [required]
#   -t  TLS version (default: TLSv1.2)                  [optional]
#   -h  Show this help message
#

set -euo pipefail

# ---- Defaults ---------------------------------------------------------------
SSL_VERSION="TLSv1.2"

usage() {
  sed -n '2,33p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

# ---- Parse arguments --------------------------------------------------------
DRIVER="" HOST="" DB="" DBUSER="" DBPASS="" CERT=""

while getopts ":j:H:d:u:p:c:t:h" opt; do
  case "${opt}" in
    j) DRIVER="${OPTARG}" ;;
    H) HOST="${OPTARG}" ;;
    d) DB="${OPTARG}" ;;
    u) DBUSER="${OPTARG}" ;;
    p) DBPASS="${OPTARG}" ;;
    c) CERT="${OPTARG}" ;;
    t) SSL_VERSION="${OPTARG}" ;;
    h) usage 0 ;;
    :) echo "ERROR: -${OPTARG} requires an argument." >&2; usage 1 ;;
    \?) echo "ERROR: unknown option -${OPTARG}." >&2; usage 1 ;;
  esac
done

# ---- Validate required arguments --------------------------------------------
MISSING=""
[[ -z "${DRIVER}" ]] && MISSING+=" -j(driver)"
[[ -z "${HOST}"   ]] && MISSING+=" -H(host:port)"
[[ -z "${DB}"     ]] && MISSING+=" -d(database)"
[[ -z "${DBUSER}" ]] && MISSING+=" -u(user)"
[[ -z "${DBPASS}" ]] && MISSING+=" -p(password)"
[[ -z "${CERT}"   ]] && MISSING+=" -c(certchain)"

if [[ -n "${MISSING}" ]]; then
  echo "ERROR: missing required argument(s):${MISSING}" >&2
  usage 1
fi

# ---- Pre-flight checks ------------------------------------------------------
if ! command -v java >/dev/null 2>&1; then
  echo "ERROR: java is not installed or not on PATH." >&2
  exit 1
fi
if [[ ! -f "${DRIVER}" ]]; then
  echo "ERROR: JDBC driver not found: ${DRIVER}" >&2
  exit 1
fi
if [[ ! -f "${CERT}" ]]; then
  echo "ERROR: certificate chain not found: ${CERT}" >&2
  exit 1
fi

# ---- Run the connection test ------------------------------------------------
echo ">> Testing SSL connection to ${HOST}/${DB} (TLS: ${SSL_VERSION}) ..."

STATUS=0
java -cp "${DRIVER}" com.ibm.db2.jcc.DB2Jcc \
  -url "jdbc:db2://${HOST}/${DB}:sslConnection=true;securityMechanism=9;sslCertLocation=${CERT};sslVersion=${SSL_VERSION};" \
  -user "${DBUSER}" -password "${DBPASS}" || STATUS=$?

echo
if [[ ${STATUS} -eq 0 ]]; then
  echo ">> SSL connection test SUCCEEDED."
else
  echo ">> SSL connection test FAILED (exit code ${STATUS})." >&2
fi
exit ${STATUS}
