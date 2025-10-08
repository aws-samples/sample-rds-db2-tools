#!/usr/bin/env bash

# Script to validate DB2 migration prerequisites for RDS DB2 migration
# This script can run in interactive or non-interactive mode
# Supports AIX and Linux on x86/POWER platforms
# Enhanced with remote DB2 connection capability

set -e

# Cross-platform compatibility function for echo
print_message() {
  # Use printf for better cross-platform color support
  printf "%b\n" "$1"
}

# Check if script is being piped from curl (cross-platform) and not already downloaded
if [ -t 0 ]; then
  PIPED_FROM_CURL=false
else
  PIPED_FROM_CURL=true
fi

# If piped from curl and not already downloaded, download the script and execute immediately
if [ "$PIPED_FROM_CURL" = "true" ] && [ "$SCRIPT_ALREADY_DOWNLOADED" != "true" ]; then
  SCRIPT_NAME="db2_migration_prereq_check.sh"
  SCRIPT_URL="https://aws-blogs-artifacts-public.s3.amazonaws.com/artifacts/DBBLOG-5048/db2_migration_prereq_check.sh"
  
  echo "=============================================================================="
  echo "DB2 Migration Prerequisites Validation Script"
  echo "=============================================================================="
  echo
  echo "Downloading and executing script..."
  
  # Download the script (cross-platform)
  if command -v curl >/dev/null 2>&1; then
    curl -sL "$SCRIPT_URL" -o "$SCRIPT_NAME"
  elif command -v wget >/dev/null 2>&1; then
    wget -q "$SCRIPT_URL" -O "$SCRIPT_NAME"
  else
    echo "Error: Neither curl nor wget is available. Please install one of them."
    exit 1
  fi
  
  # Make it executable
  chmod +x "$SCRIPT_NAME"
  
  echo "Script downloaded successfully. Starting validation..."
  echo "=============================================================================="
  echo
  
  # Execute the downloaded script with environment variables to prevent re-download
  DOWNLOADED_SCRIPT_PATH="$(pwd)/$SCRIPT_NAME" SCRIPT_WAS_DOWNLOADED="true" SCRIPT_ALREADY_DOWNLOADED="true" exec ./"$SCRIPT_NAME" "$@"
fi

# Color codes for better readability
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Global variables
VERBOSE=${VERBOSE:-false}
REPORT_FILE=""
TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0
WARNING_CHECKS=0
INFO_CHECKS=0
INVENTORY_MODE=true
INVENTORY_DETAIL_FILE=""
INVENTORY_SUMMARY_FILE=""
INVENTORY_JSON_FILE=""
DOWNLOADED_SCRIPT_PATH="${DOWNLOADED_SCRIPT_PATH:-}"
SCRIPT_WAS_DOWNLOADED="${SCRIPT_WAS_DOWNLOADED:-false}"
HELP_REQUESTED=false

# Remote connection variables
REMOTE_MODE=false
DB2USER="${DB2USER:-}"
DB2PASSWORD="${DB2PASSWORD:-}"
DBNAME="${DBNAME:-}"

# Check if we're running in remote mode
if [ -n "$DB2USER" ] && [ -n "$DB2PASSWORD" ] && [ -n "$DBNAME" ]; then
  REMOTE_MODE=true
  INTERACTIVE_MODE=false
elif [ -n "$DB2_INSTANCES" ]; then
  INTERACTIVE_MODE=false
else
  INTERACTIVE_MODE=true
fi

# =============================================================================
# Logging Functions (Cross-platform compatible)
# =============================================================================

# Cross-platform date function
get_timestamp() {
  if command -v date >/dev/null 2>&1; then
    date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date
  else
    echo "$(date)"
  fi
}

log_info() {
  local message="[   INFO] $(get_timestamp) - $1"
  print_message "${BLUE}${message}${NC}" >&2
  if [ -n "$REPORT_FILE" ]; then
    echo "$message" >> "$REPORT_FILE"
  fi
}

log_success() {
  local message="[SUCCESS] $(get_timestamp) - $1"
  print_message "${GREEN}${message}${NC}" >&2
  if [ -n "$REPORT_FILE" ]; then
    echo "$message" >> "$REPORT_FILE"
  fi
}

log_warning() {
  local message="[WARNING] $(get_timestamp) - $1"
  print_message "${YELLOW}${message}${NC}" >&2
  if [ -n "$REPORT_FILE" ]; then
    echo "$message" >> "$REPORT_FILE"
  fi
}

log_error() {
  local message="[  ERROR] $(get_timestamp) - $1"
  print_message "${RED}${message}${NC}" >&2
  if [ -n "$REPORT_FILE" ]; then
    echo "$message" >> "$REPORT_FILE"
  fi
}

log_debug() {
  if [ "$VERBOSE" = "true" ]; then
    local message="[ DEBUG ] $(get_timestamp) - $1"
    print_message "${CYAN}${message}${NC}" >&2
    if [ -n "$REPORT_FILE" ]; then
      echo "$message" >> "$REPORT_FILE"
    fi
  fi
}

# Function to execute DB2 queries with proper connection handling
db2_query() {
  local db_name="$1"
  local sql_query="$2"
  local output_var="$3"  # Variable name to store result
  local suppress_headers="${4:-true}"  # Optional: suppress headers (default: true)
  local output_file="/tmp/db2_output_$$.txt"
  
  # Connect to database based on mode
  if [ "$REMOTE_MODE" = "true" ]; then
    # Remote connection using credentials
    db2 connect to "$db_name" user "$DB2USER" using "$DB2PASSWORD" >/dev/null 2>&1
  else
    # Local connection
    db2 connect to "$db_name" >/dev/null 2>&1
  fi
  
  if [ $? -ne 0 ]; then
    log_debug "Failed to connect to database $db_name"
    return 1
  fi
  
  # Execute query with or without -x flag based on suppress_headers parameter
  if [ "$suppress_headers" = "true" ]; then
    # Suppress column headers and formatting for clean data processing
    db2 -x "$sql_query" > "$output_file" 2>/dev/null
  else
    # Include column headers and formatting for detailed output
    db2 "$sql_query" > "$output_file" 2>/dev/null
  fi
  local query_exit_code=$?
  
  # Disconnect from database
  db2 disconnect "$db_name" >/dev/null 2>&1
  
  # Check query execution
  if [ $query_exit_code -ne 0 ]; then
    rm -f "$output_file"
    return 1
  fi
  
  # Read result and store in variable if provided
  if [ -n "$output_var" ]; then
    if [ -s "$output_file" ]; then
      # File has content
      eval "$output_var=\"\$(cat '$output_file')\""
    else
      # File is empty - no results
      eval "$output_var=\"0 record(s) selected.\""
    fi
  fi
  
  # Clean up
  rm -f "$output_file"
  return 0
}

# Function to log test results
log_test_result() {
  local test_name="$1"
  local result="$2"
  local details="$3"
  local recommendation="$4"
  
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  
  case "$result" in
    "PASS")
      PASSED_CHECKS=$((PASSED_CHECKS + 1))
      log_success "[$test_name] PASS - $details"
      ;;
    "FAIL")
      FAILED_CHECKS=$((FAILED_CHECKS + 1))
      log_error "[$test_name] FAIL - $details"
      if [ -n "$recommendation" ]; then
        log_warning "[$test_name] RECOMMENDATION: $recommendation"
      fi
      ;;
    "WARNING")
      WARNING_CHECKS=$((WARNING_CHECKS + 1))
      log_warning "[$test_name] WARNING - $details"
      if [ -n "$recommendation" ]; then
        log_info "[$test_name] RECOMMENDATION: $recommendation"
      fi
      ;;
    "INFO")
      INFO_CHECKS=$((INFO_CHECKS + 1))
      log_info "[$test_name] INFO - $details"
      if [ -n "$recommendation" ]; then
        log_info "[$test_name] RECOMMENDATION: $recommendation"
      fi
      # Don't increment TOTAL_CHECKS for INFO messages
      TOTAL_CHECKS=$((TOTAL_CHECKS - 1))
      ;;
  esac
}

# =============================================================================
# Platform Detection Functions
# =============================================================================

detect_platform() {
  local platform=""
  local arch=""
  
  if command -v uname >/dev/null 2>&1; then
    platform=$(uname -s)
    arch=$(uname -m)
  else
    log_error "Cannot detect platform. uname command not available."
    exit 1
  fi
  
  case "$platform" in
    "AIX")
      PLATFORM="AIX"
      ;;
    "Linux")
      case "$arch" in
        "x86_64"|"amd64")
          PLATFORM="Linux_x86"
          ;;
        "ppc64"|"ppc64le")
          PLATFORM="Linux_POWER"
          ;;
        *)
          log_warning "Unsupported Linux architecture: $arch"
          PLATFORM="Linux_Unknown"
          ;;
      esac
      ;;
    *)
      log_error "Unsupported platform: $platform"
      log_error "This script supports AIX, Linux on x86, and Linux on POWER only."
      exit 1
      ;;
  esac
  
  log_info "Detected platform: $PLATFORM ($arch)"
}

# =============================================================================
# DB2 Environment Functions
# =============================================================================

check_db2_version() {
  log_info "Checking DB2 version information..."
  
  # Get DB2 level information
  if command -v db2level >/dev/null 2>&1; then
    local db2level_output
    db2level_output=$(db2level 2>/dev/null)
    
    if [ $? -eq 0 ] && [ -n "$db2level_output" ]; then
      log_info "DB2 Level Information:"
      # Extract key information from db2level output
      local product_id=$(echo "$db2level_output" | grep "Product is" | head -1)
      local version_info=$(echo "$db2level_output" | grep "DB2 v" | head -1)
      local build_level=$(echo "$db2level_output" | grep "Build level" | head -1)
      
      if [ -n "$product_id" ]; then
        log_info "  $product_id"
      fi
      if [ -n "$version_info" ]; then
        log_info "  $version_info"
      fi
      if [ -n "$build_level" ]; then
        log_info "  $build_level"
      fi
      
      log_debug "Full db2level output: $db2level_output"
    else
      log_warning "Unable to get DB2 level information from db2level command"
    fi
  else
    log_warning "db2level command not found"
  fi
}

check_db2_environment() {
  log_info "Checking DB2 environment..."
  
  # Check if db2 command is available
  if ! command -v db2 >/dev/null 2>&1; then
    log_error "DB2 command not found. Please ensure DB2 is installed and sourced."
    log_error "Try running: . ~db2inst1/sqllib/db2profile"
    exit 1
  fi
  
  # Skip db2ilist check in remote mode
  if [ "$REMOTE_MODE" = "false" ] && ! command -v db2ilist >/dev/null 2>&1; then
    log_error "db2ilist command not found. Please ensure DB2 is properly installed."
    exit 1
  fi
  
  # Check if jq is available for JSON processing (only warn if inventory is enabled)
  if [ "$INVENTORY_MODE" = "true" ] && ! command -v jq >/dev/null 2>&1; then
    log_warning "jq command not found. JSON inventory will use fallback method."
    log_warning "For better JSON formatting, install jq: https://stedolan.github.io/jq/"
  fi
  
  log_success "DB2 environment is properly configured."
  
  # Check DB2 version information
  check_db2_version
}

get_db2_instances() {
  if [ "$REMOTE_MODE" = "true" ]; then
    log_info "Running in remote mode - skipping instance discovery"
    return
  fi
  
  log_info "Discovering DB2 instances..."
  
  local instances_output
  instances_output=$(db2ilist 2>/dev/null)
  
  if [ $? -ne 0 ] || [ -z "$instances_output" ]; then
    log_error "No DB2 instances found or unable to list instances."
    log_error "Please ensure DB2 is properly installed and you have appropriate permissions."
    exit 1
  fi
  
  # Convert to array
  DB2_INSTANCES_ARRAY=()
  while IFS= read -r instance; do
    if [ -n "$instance" ]; then
      DB2_INSTANCES_ARRAY+=("$instance")
    fi
  done <<< "$instances_output"
  
  log_success "Found ${#DB2_INSTANCES_ARRAY[@]} DB2 instance(s): ${DB2_INSTANCES_ARRAY[*]}"
}

get_current_instance() {
  if [ "$REMOTE_MODE" = "true" ]; then
    echo "remote"
    return 0
  fi
  
  log_debug "Determining current DB2 instance..."
  
  # Get current instance from DB2INSTANCE environment variable
  if [ -n "$DB2INSTANCE" ]; then
    echo "$DB2INSTANCE"
    return 0
  fi
  
  # Try to get from whoami if DB2INSTANCE is not set
  local current_user=$(whoami 2>/dev/null)
  if [ -n "$current_user" ]; then
    echo "$current_user"
    return 0
  fi
  
  log_error "Unable to determine current DB2 instance"
  return 1
}

get_databases_for_instance() {
  local instance="$1"
  
  if [ "$REMOTE_MODE" = "true" ]; then
    echo "$DBNAME"
    return 0
  fi
  
  log_debug "Getting databases for instance: $instance"
  
  # Get database list for current instance only
  local db_output
  db_output=$(db2 list db directory 2>/dev/null | grep "Database name" | awk '{print $4}' | sort -u)
  
  if [ -z "$db_output" ]; then
    log_warning "No catalogued databases found in the current instance: $instance"
    DB2DSDRIVER_CFG="$HOME/sqllib/cfg/db2dsdriver.cfg"
    if [ -f "$DB2DSDRIVER_CFG" ]; then
      dsn_entries=$(grep -oP '<dsn alias="\K[^"]+' "$DB2DSDRIVER_CFG" | grep -v -i 'rdsadmin' | xargs)
      if [ -n "$dsn_entries" ]; then
        log_info "List of DSN entries found in $DB2DSDRIVER_CFG are: $dsn_entries"
        log_info "If you are trying to connect to a remote database, please set DB2USER, DB2PASSWORD, and DBNAME environment variables."
        log_info "The environment variable DBNAME will be the name of the DSN alias defined in the db2dsdriver.cfg file."
        log_info "Example:"
        log_info "export DB2USER=<Db2 user name to connect to remote Db2>"
        log_info "export DB2PASSWORD=<Password to connect to remote Db2>"
        log_info "export DBNAME=<Choose name of the DSN alias defined in db2dsdriver.cfg>"
        log_info "Run the script again after setting the above environment variables."
      fi
    fi
    return 1
  fi

  databases=""
  remote_databases=""
  for db_name in $db_output; do
    LocalRmt=$(db2 list db directory | grep -A5 "$db_name" | grep 'Directory entry type' | cut -f2 -d=|sort -u|head -1|awk '{print $1}')
    if [[ "$LocalRmt" == "Indirect" ]] ; then
      if [ -z "$databases" ] ; then
        databases="$db_name"
      else
        databases="$databases $db_name"
      fi
      log_debug "Found database: $db_name"
    else
      if [ -z "$remote_databases" ] ; then
        remote_databases="$db_name"
      else
        remote_databases="$remote_databases $db_name"
      fi
      log_debug "Found remote catalogued database: $db_name. Skipping it ..."
    fi
  done
  if [ -z "$databases" ] ; then
    log_warning "No local catalogued databases found in the current instance: $instance"
    log_info "If you are trying to connect to a remote database, please set DB2USER, DB2PASSWORD, and DBNAME environment variables."
    log_info "The environment variable DBNAME will be the name of the remote catalogued database or the DSN name defined in the db2dsdriver.cfg file."
    log_info "List of remote catalogued databases are: $remote_databases"
    log_info "Example:"
    log_info "export DB2USER=<Db2 user name to connect to remote Db2>"
    log_info "export DB2PASSWORD=<Password to connect to remote Db2>"
    log_info "export DBNAME=<Choose name of the remote catalogued database name>"
    log_info "Run the script again after setting the above environment variables."
    DB2DSDRIVER_CFG="$HOME/sqllib/cfg/db2dsdriver.cfg"
    if [ -f "$DB2DSDRIVER_CFG" ]; then
      dsn_entries=$(grep -oP '<dsn alias="\K[^"]+' "$DB2DSDRIVER_CFG" | grep -v -i 'rdsadmin' | xargs)
      if [ -n "$dsn_entries" ]; then
        log_info "List of DSN entries found in $DB2DSDRIVER_CFG are: $dsn_entries"
        log_info "If you are trying to connect to a remote database, please set DB2USER, DB2PASSWORD, and DBNAME environment variables."
        log_info "The environment variable DBNAME will be the name of the DSN alias defined in the db2dsdriver.cfg file."
        log_info "Example:"
        log_info "export DB2USER=<Db2 user name to connect to remote Db2>"
        log_info "export DB2PASSWORD=<Password to connect to remote Db2>"
        log_info "export DBNAME=<Choose name of the DSN alias defined in db2dsdriver.cfg>"
        log_info "Run the script again after setting the above environment variables."
      fi
    fi
    return 1
  fi
  echo "${databases}"
}

check_database_version() {
  local database="$1"
  
  log_info "Checking database version for: $database"
  
  # Use db2_query function to get version information
  local version_result
  if db2_query "$database" "SELECT VERSIONNUMBER FROM SYSIBM.SYSVERSIONS" "version_result" && [ -n "$version_result" ] && [ "$version_result" != "0 record(s) selected." ]; then
    # Clean up the version result
    version_result=$(echo "$version_result" | head -1 | tr -d ' ')
    log_test_result "DATABASE_VERSION" "INFO" "Database version: $version_result" "Informational - verify compatibility with RDS DB2"
  else
    log_test_result "DATABASE_VERSION" "INFO" "Unable to retrieve database version from SYSIBM.SYSVERSIONS" "Check database connectivity and system catalog access"
  fi
}

# =============================================================================
# Validation Functions
# =============================================================================

validate_db2_update_level() {
  local instance="$1"
  local database="$2"
  
  log_info "Checking DB2 update level for database: $database"
  
  # Check if db2updv115 command exists (skip in remote mode)
  if [ "$REMOTE_MODE" = "false" ] && ! command -v db2updv115 >/dev/null 2>&1; then
    log_test_result "DB2_UPDATE_LEVEL" "INFO" "db2updv115 command not found" "Ensure DB2 v11.5 update utility is available"
    return
  fi
  
  # Run db2updv115 command
  log_test_result "Makes sure that you run db2updv115 -d \"$database\" command" "PASS" "after applying fixpack before migrating to RDS DB2"
}

validate_indoubt_transactions() {
  local instance="$1"
  local database="$2"
  
  log_info "Checking in-doubt transactions for database: $database"
  
  # Use db2_query function to get in-doubt transaction count
  local result
  if ! db2_query "$database" "select NUM_INDOUBT_TRANS from table(mon_get_transaction_log(null))" "result"; then
    log_test_result "INDOUBT_TRANSACTIONS" "FAIL" "Unable to query in-doubt transactions for $database" "Check database connectivity and permissions"
    return
  fi
  
  # Clean up the result
  result=$(echo "$result" | tr -d ' ' | head -1)
  
  # Check if result is numeric and not empty
  if [ -n "$result" ] && [ "$result" != "0 record(s) selected." ]; then
    # Use case statement for numeric check (POSIX compliant)
    case "$result" in
      ''|*[!0-9]*) 
        log_test_result "INDOUBT_TRANSACTIONS" "FAIL" "Invalid result from in-doubt transaction query: $result" "Check query execution"
        ;;
      *)
        if [ "$result" -eq 0 ]; then
          log_test_result "INDOUBT_TRANSACTIONS" "PASS" "No in-doubt transactions found ($result)"
        else
          log_test_result "INDOUBT_TRANSACTIONS" "FAIL" "Found $result in-doubt transaction(s)" "Resolve in-doubt transactions before migration"
          log_warning "RECOMMENDATION: Run command \"db2 list indoubt transactions with prompting\" and follow the prompts to resolve them"
        fi
        ;;
    esac
  else
    log_test_result "INDOUBT_TRANSACTIONS" "FAIL" "Invalid result from in-doubt transactions query: $result" "Check database status and connectivity"
  fi
}

validate_invalid_objects() {
  local instance="$1"
  local database="$2"
  
  log_info "Checking invalid objects for database: $database"
  
  # Use db2_query function for consistent connection handling
  local result
  if ! db2_query "$database" "SELECT 'COUNT:' || count(*) FROM SYSCAT.INVALIDOBJECTS" "result"; then
    log_test_result "INVALID_OBJECTS" "FAIL" "Unable to query invalid objects for $database" "Check database connectivity and permissions"
    return
  fi
  
  # Extract count from result
  local count
  count=$(echo "$result" | grep "COUNT:" | sed 's/COUNT://' | tr -d ' ')
  
  if [[ "$count" =~ ^[0-9]+$ ]]; then
    if [ "$count" -eq 0 ]; then
      log_test_result "INVALID_OBJECTS" "PASS" "No invalid objects found"
    else
      log_test_result "INVALID_OBJECTS" "FAIL" "Found $count invalid object(s)" "Run: db2 \"call SYSPROC.ADMIN_REVALIDATE_DB_OBJECTS()\" (may need multiple runs)"
    fi
  else
    log_test_result "INVALID_OBJECTS" "FAIL" "Invalid result from invalid objects query: $result" "Check database status and connectivity"
  fi
}

validate_tablespace_state() {
  local instance="$1"
  local database="$2"
  
  log_info "Checking tablespace states for database: $database"
  
  # Use db2_query function for consistent connection handling
  local result
  if ! db2_query "$database" "SELECT TBSP_ID, SUBSTR(TBSP_NAME,1,30)TBSP_NAME, SUBSTR(TBSP_STATE,1,40)TBSP_STATE, CASE TBSP_STATE WHEN 'NORMAL' THEN 'PASS' ELSE 'FAIL' END AS TS_RESULT FROM TABLE(sysproc.MON_GET_TABLESPACE('',-1))" "result" "false"; then
    log_test_result "TABLESPACE_STATE" "FAIL" "Unable to query tablespace states for $database" "Check database connectivity and permissions"
    return
  fi
  
  # Check for FAIL results in TS_RESULT column
  local fail_count
  if echo "$result" | grep -q "FAIL"; then
    fail_count=$(echo "$result" | grep -c "FAIL")
  else
    fail_count=0
  fi
  
  if [ "$fail_count" -eq 0 ]; then
    log_test_result "TABLESPACE_STATE" "PASS" "All tablespaces are in NORMAL state"
  else
    log_test_result "TABLESPACE_STATE" "FAIL" "Found $fail_count tablespace(s) not in NORMAL state" "Ensure all tablespaces are in NORMAL state before migration"
    log_debug "Tablespace details: $result"
  fi
}

validate_non_fenced_routines() {
  local instance="$1"
  local database="$2"
  
  log_info "Checking non-fenced routines for database: $database"
  
  # Use db2_query function for consistent connection handling
  local result
  if ! db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.ROUTINES WHERE FENCED='N' AND ROUTINESCHEMA NOT IN ('SQLJ','SYSCAT','SYSFUN','SYSIBM','SYSIBMADM','SYSPROC','SYSTOOLS')" "result"; then
    log_test_result "NON_FENCED_ROUTINES" "FAIL" "Unable to query non-fenced routines for $database" "Check database connectivity and permissions"
    return
  fi
  
  result=$(echo "$result" | tr -d ' ')
  
  if [[ "$result" =~ ^[0-9]+$ ]]; then
    if [ "$result" -eq 0 ]; then
      log_test_result "NON_FENCED_ROUTINES" "PASS" "No non-fenced routines found"
    else
      log_test_result "NON_FENCED_ROUTINES" "FAIL" "Found $result non-fenced routine(s)" "Review and fence non-system routines before migration"
      
      # Get details of non-fenced routines
      local details
      if db2_query "$database" "SELECT substr(ROUTINESCHEMA,1,20)ROUTINESCHEMA, substr(ROUTINEMODULENAME,1,20)ROUTINEMODULENAME, substr(ROUTINENAME,1,20)ROUTINENAME, FENCED, ROUTINETYPE, substr(OWNER,1,20)OWNER, substr(SPECIFICNAME,1,20)SPECIFICNAME from SYSCAT.ROUTINES where fenced='N' and routineschema not in ('SQLJ','SYSCAT','SYSFUN','SYSIBM','SYSIBMADM','SYSPROC','SYSTOOLS')" "details" "false"; then
      log_debug "Non-fenced routines details: $details"
      fi
    fi
  else
    log_test_result "NON_FENCED_ROUTINES" "FAIL" "Invalid result from non-fenced routines query: $result" "Check database status and connectivity"
  fi
}

validate_java_procedures() {
  local instance="$1"
  local database="$2"
  
  log_info "Checking Java stored procedures for database: $database"
  
  # Use db2_query function to check for Java stored procedures
  local result
  if ! db2_query "$database" "select substr(JARSCHEMA,1,20) JARSCHEMA,substr(JAR_ID,1,20) JAR_ID,substr(CLASS,1,40) CLASS from sysibm.sysjarcontents" "result"; then
    log_test_result "JAVA_PROCEDURES" "PASS" "Did not find any Java stored procedures for $database" ""
    return
  fi
  
  # Check if result contains data
  if [ -n "$result" ] && [ "$result" != "0 record(s) selected." ]; then
    log_test_result "JAVA_PROCEDURES" "PASS" "Java stored procedures found in database" ""
    log_debug "Java stored procedure details: $result"
    log_info "RECOMMENDATION: Copy JAR files from self-managed Db2 server and install them on RDS for Db2:"
    log_info "1. Copy all jar files from ~/sqllib/function/jar directory structure to your Db2 client that can connect to RDS for Db2"
    log_info "2. Run command: call sqlj.install_jar('jarfilepath','JAR_ID') to install the jar files to RDS for Db2 server"
    
  else
    log_test_result "JAVA_PROCEDURES" "PASS" "No Java stored procedures found"
  fi
}

validate_autostorage() {
  local instance="$1"
  local database="$2"
  
  log_info "Checking AutoStorage configuration for database: $database"
  
  # Use db2_query function for consistent connection handling
  local result
  if ! db2_query "$database" "SELECT trim(count(*)) FROM TABLE(ADMIN_GET_STORAGE_PATHS('',-1))" "result"; then
    log_test_result "AUTOSTORAGE" "FAIL" "Unable to query AutoStorage configuration for $database" "Check database connectivity and permissions"
    return
  fi
  
  result=$(echo "$result" | tr -d ' ')
  
  if [[ "$result" =~ ^[0-9]+$ ]]; then
    if [ "$result" -ge 1 ]; then
      log_test_result "AUTOSTORAGE" "PASS" "AutoStorage is configured ($result storage path(s) found)"
    else
      log_test_result "AUTOSTORAGE" "FAIL" "No storage paths found for AutoStorage" "Create storage group: db2 \"CREATE STOGROUP <name> ON '<PathName>'\""
    fi
  else
    log_test_result "AUTOSTORAGE" "FAIL" "Invalid result from AutoStorage query: $result" "Check database status and connectivity"
  fi
}

format_size() {
  local size_kb="$1"
  
  if [ "$size_kb" -lt 1024 ]; then
    echo "${size_kb} KB"
  elif [ "$size_kb" -lt 1048576 ]; then
    echo "$((size_kb / 1024)) MB"
  elif [ "$size_kb" -lt 1073741824 ]; then
    echo "$((size_kb / 1048576)) GB"
  else
    echo "$((size_kb / 1073741824)) TB"
  fi
}

validate_database_configuration() {
  local instance="$1"
  local database="$2"
  
  log_info "Checking database configuration for database: $database"
  
  # Get database configuration using db2_query for consistent connection handling
  local db_cfg
  if [ "$REMOTE_MODE" = "true" ]; then
    # Connect and get config in remote mode
    db2 connect to "$database" user "$DB2USER" using "$DB2PASSWORD" > /tmp/connect.log 2>&1
    local_dbname=$(grep "Local database alias" /tmp/connect.log | awk -F"=" '{print $2}')
    if [ -n "$local_dbname" ]; then
      database="$local_dbname"
      db2 get db cfg for "$database" 2>/dev/null > /tmp/db.cfg
      db2 disconnect "$database" >/dev/null 2>&1
      rm -f /tmp/connect.log
    else
      log_test_result "DATABASE_CONFIG" "FAIL" "Unable to connect to remote database $database" "Check DB2USER, DB2PASSWORD, and DBNAME environment variables"
      return
    fi
  else
    # Local mode
    db2 connect to "$database" >/dev/null 2>&1
    db2 get db cfg for "$database" 2>/dev/null > /tmp/db.cfg
    db2 disconnect "$database" >/dev/null 2>&1
  fi
  
  local exit_code=$?
  
  if [ $exit_code -ne 0 ]; then
    log_test_result "DATABASE_CONFIG" "FAIL" "Unable to get database configuration for $database" "Check database connectivity and permissions"
    return
  fi
  db_cfg=$(cat /tmp/db.cfg)
  rm -f /tmp/db.cfg
  # Check specific configuration parameters
  local update_pending=$(echo "$db_cfg" | grep "Update to database level pending" | cut -d'=' -f2 | cut -d'(' -f1 | tr -d ' ')
  local territory=$(echo "$db_cfg" | grep "Database territory" | awk -F'=' '{print $2}' | tr -d ' ')
  local codepage=$(echo "$db_cfg" | grep "Database code page" | awk -F'=' '{print $2}' | tr -d ' ')
  local codeset=$(echo "$db_cfg" | grep "Database code set" | awk -F'=' '{print $2}' | tr -d ' ')
  local country=$(echo "$db_cfg" | grep "Database country" | awk -F'=' '{print $2}' | tr -d ' ')
  local collating=$(echo "$db_cfg" | grep "Database collating sequence" | awk -F'=' '{print $2}' | tr -d ' ')
  local pagesize=$(echo "$db_cfg" | grep "Database page size" | awk -F'=' '{print $2}' | tr -d ' ')
  local backup_pending=$(echo "$db_cfg" | grep "Backup pending" | awk -F'=' '{print $2}' | tr -d ' ')
  local rollforward_pending=$(echo "$db_cfg" | grep "Rollforward pending" | awk -F'=' '{print $2}' | tr -d ' ')
  local restore_pending=$(echo "$db_cfg" | grep "Restore pending" | awk -F'=' '{print $2}' | tr -d ' ')
  local upgrade_pending=$(echo "$db_cfg" | grep "Upgrade pending" | awk -F'=' '{print $2}' | tr -d ' ')
  local self_tuning_mem=$(echo "$db_cfg" | grep "SELF_TUNING_MEM" | awk -F'=' '{print $2}' | tr -d ' ')
  local database_memory=$(echo "$db_cfg" | grep "DATABASE_MEMORY" | awk -F'=' '{print $2}' | tr -d ' ')
  
  # Log configuration parameters
  local config_issues=0
  
  # Check critical parameters
  if [ "$update_pending" = "NO" ]; then
    log_test_result "DB_CONFIG_UPDATE_PENDING" "PASS" "Update to database level pending: $update_pending"
  else
    log_test_result "DB_CONFIG_UPDATE_PENDING" "FAIL" "Update to database level pending: $update_pending" "Complete pending database updates"
    config_issues=$((config_issues + 1))
  fi
  
  if [ "$backup_pending" = "NO" ]; then
    log_test_result "DB_CONFIG_BACKUP_PENDING" "PASS" "Backup pending: $backup_pending"
  else
    log_test_result "DB_CONFIG_BACKUP_PENDING" "FAIL" "Backup pending: $backup_pending" "Complete pending backup operations"
    config_issues=$((config_issues + 1))
  fi
  
  if [ "$rollforward_pending" = "NO" ]; then
    log_test_result "DB_CONFIG_ROLLFORWARD_PENDING" "PASS" "Rollforward pending: $rollforward_pending"
  else
    log_test_result "DB_CONFIG_ROLLFORWARD_PENDING" "FAIL" "Rollforward pending: $rollforward_pending" "Complete pending rollforward operations"
    config_issues=$((config_issues + 1))
  fi
  
  if [ "$restore_pending" = "NO" ]; then
    log_test_result "DB_CONFIG_RESTORE_PENDING" "PASS" "Restore pending: $restore_pending"
  else
    log_test_result "DB_CONFIG_RESTORE_PENDING" "FAIL" "Restore pending: $restore_pending" "Complete pending restore operations"
    config_issues=$((config_issues + 1))
  fi
  
  if [ "$upgrade_pending" = "NO" ]; then
    log_test_result "DB_CONFIG_UPGRADE_PENDING" "PASS" "Upgrade pending: $upgrade_pending"
  else
    log_test_result "DB_CONFIG_UPGRADE_PENDING" "FAIL" "Upgrade pending: $upgrade_pending" "Complete pending upgrade operations"
    config_issues=$((config_issues + 1))
  fi
  
  # Informational parameters
  log_test_result "DB_CONFIG_TERRITORY" "INFO" "Database territory: ${territory:-Unknown}" "Default is US"
  log_test_result "DB_CONFIG_CODEPAGE" "INFO" "Database code page: ${codepage:-Unknown}" "Default is 1208"
  log_test_result "DB_CONFIG_CODESET" "INFO" "Database code set: ${codeset:-Unknown}" "Default is UTF-8"
  log_test_result "DB_CONFIG_COUNTRY" "INFO" "Database country: ${country:-Unknown}" "Default is 1"
  log_test_result "DB_CONFIG_COLLATING" "INFO" "Database collating sequence: ${collating:-Unknown}" "Default is IDENTITY"
  log_test_result "DB_CONFIG_PAGESIZE" "INFO" "Database page size: ${pagesize:-Unknown}" "Informational"
  
  # Recommendations
  if [ "$self_tuning_mem" = "OFF" ]; then
    log_test_result "DB_CONFIG_SELF_TUNING_MEM" "INFO" "SELF_TUNING_MEM: $self_tuning_mem" "Recommendation: Set to ON"
  else
    log_test_result "DB_CONFIG_SELF_TUNING_MEM" "PASS" "SELF_TUNING_MEM: $self_tuning_mem"
  fi
  
  if [[ "$database_memory" == AUTOMATIC* ]]; then
    log_test_result "DB_CONFIG_DATABASE_MEMORY" "PASS" "DATABASE_MEMORY: $database_memory"
  else
    log_test_result "DB_CONFIG_DATABASE_MEMORY" "INFO" "DATABASE_MEMORY: $database_memory" "Recommendation: Set to AUTOMATIC"
  fi
  
  # Check log configuration
  validate_log_configuration "$instance" "$database" "$db_cfg"
}

validate_log_configuration() {
  local instance="$1"
  local database="$2"
  local db_cfg="$3"
  
  log_info "Checking log configuration for database: $database"
  
  # Extract log parameters
  local logfilsiz=$(echo "$db_cfg" | grep "LOGFILSIZ" | awk -F'=' '{print $2}' | tr -d ' ')
  local logprimary=$(echo "$db_cfg" | grep "LOGPRIMARY" | awk -F'=' '{print $2}' | tr -d ' ')
  local logsecond=$(echo "$db_cfg" | grep "LOGSECOND" | awk -F'=' '{print $2}' | tr -d ' ')
  local logarchmeth1=$(echo "$db_cfg" | grep "LOGARCHMETH1" | awk -F'=' '{print $2}' | tr -d ' ')
  local logarchmeth2=$(echo "$db_cfg" | grep "LOGARCHMETH2" | awk -F'=' '{print $2}' | tr -d ' ')
  
  # Check if LOGSECOND is set to -1 (not supported in RDS DB2)
  if [ "$logsecond" = "-1" ]; then
    log_test_result "LOGSECOND_UNLIMITED" "WARNING" "LOGSECOND is set to -1 (unlimited)" "RDS DB2 does not support unlimited secondary log files. Set LOGSECOND to a specific value"
  fi
  
  # Validate log parameters using case statements (POSIX compliant)
  case "$logfilsiz" in
    ''|*[!0-9]*) 
      log_test_result "LOG_SPACE_CALCULATION" "FAIL" "Unable to calculate log space - invalid LOGFILSIZ parameter: $logfilsiz" "Check log configuration parameters"
      return
      ;;
  esac
  
  case "$logprimary" in
    ''|*[!0-9]*) 
      log_test_result "LOG_SPACE_CALCULATION" "FAIL" "Unable to calculate log space - invalid LOGPRIMARY parameter: $logprimary" "Check log configuration parameters"
      return
      ;;
  esac
  
  # Calculate total log space
  local total_space_kb=0
  local log_space_calculated=false
  
  case "$logsecond" in
    "-1")
      # Special case: LOGSECOND is unlimited, calculate only primary logs
      total_space_kb=$(( logfilsiz * logprimary * 4 ))
      log_space_calculated=true
      log_info "Note: LOGSECOND=-1 (unlimited), calculating space for primary logs only"
      ;;
    *[0-9]*)
      # Normal case: LOGSECOND is numeric
      case "$logsecond" in
        ''|*[!0-9]*) 
          log_test_result "LOG_SPACE_CALCULATION" "FAIL" "Unable to calculate log space - invalid LOGSECOND parameter: $logsecond" "Check log configuration parameters"
          return
          ;;
        *)
          total_space_kb=$(( (logfilsiz * logprimary + logfilsiz * logsecond) * 4 ))
          log_space_calculated=true
          ;;
      esac
      ;;
    *)
      log_test_result "LOG_SPACE_CALCULATION" "FAIL" "Unable to calculate log space - invalid LOGSECOND parameter: $logsecond" "Check log configuration parameters"
      return
      ;;
  esac
  
  if [ "$log_space_calculated" = true ]; then
    local formatted_space=$(format_size "$total_space_kb")
    
    if [ "$logsecond" = "-1" ]; then
      log_test_result "LOG_SPACE_CALCULATION" "INFO" "Primary log space: $formatted_space (LOGFILSIZ=$logfilsiz, LOGPRIMARY=$logprimary, LOGSECOND=unlimited)" "Informational"
    else
      log_test_result "LOG_SPACE_CALCULATION" "INFO" "Total log space: $formatted_space (LOGFILSIZ=$logfilsiz, LOGPRIMARY=$logprimary, LOGSECOND=$logsecond)" "Informational"
    fi
    
    # Check log file limits (skip if LOGSECOND=-1)
    if [ "$logsecond" != "-1" ]; then
      local total_logs=$((logprimary + logsecond))
      
      if [ "$logarchmeth1" = "OFF" ] && [ "$logarchmeth2" = "OFF" ]; then
        if [ "$total_logs" -le 254 ]; then
          log_test_result "LOG_FILE_LIMITS" "PASS" "Total log files ($total_logs) within limit for non-archived logging (≤254)"
        else
          log_test_result "LOG_FILE_LIMITS" "FAIL" "Total log files ($total_logs) exceeds limit for non-archived logging (≤254)" "Reduce LOGPRIMARY + LOGSECOND to ≤254 or enable log archiving"
        fi
      else
        if [ "$total_logs" -le 4096 ]; then
          log_test_result "LOG_FILE_LIMITS" "PASS" "Total log files ($total_logs) within limit for archived logging (≤4096)"
        else
          log_test_result "LOG_FILE_LIMITS" "FAIL" "Total log files ($total_logs) exceeds limit for archived logging (≤4096)" "Reduce LOGPRIMARY + LOGSECOND to ≤4096"
        fi
      fi
    else
      log_test_result "LOG_FILE_LIMITS" "INFO" "Log file limits check skipped (LOGSECOND=-1)" "Set specific LOGSECOND value for RDS DB2"
    fi
    
    # Calculate RDS DB2 sizing recommendation
    calculate_rds_sizing_recommendation "$database" "$total_space_kb"
  fi
  
  # Log archiving status
  log_test_result "LOG_ARCHIVING" "INFO" "LOGARCHMETH1: ${logarchmeth1:-OFF}, LOGARCHMETH2: ${logarchmeth2:-OFF}" "Informational"
}

# Function to calculate RDS DB2 sizing recommendation
calculate_rds_sizing_recommendation() {
  local database="$1"
  local log_space_kb="$2"
  
  log_info "Calculating RDS DB2 sizing recommendation for database: $database"
  
  # Try multiple methods to get database size
  local db_size_bytes=""
  
  # Method 1: Try GET_DBSIZE_INFO procedure (corrected name)
  local db_size_result
  if db2_query "$database" "CALL GET_DBSIZE_INFO(?, ?, ?, -1)" "db_size_result"; then
    if [ -n "$db_size_result" ] && [ "$db_size_result" != "0 record(s) selected." ]; then
      log_debug "GET_DBSIZE_INFO raw output: '$db_size_result'"
      
      # Parse the specific output format from GET_DBSIZE_INFO
      # Look for "Parameter Name  : DATABASESIZE" followed by "Parameter Value : NNNNNN"
      db_size_bytes=$(echo "$db_size_result" | awk '
        /Parameter Name.*DATABASESIZE/ { getline; 
          if ($0 ~ /Parameter Value/) { 
            gsub(/[^0-9]/, "", $0); 
            if ($0 > 0) print $0 
          } 
        }')
      
      # If the above parsing didn't work, try alternative approaches
      if [ -z "$db_size_bytes" ]; then
        # Try to find DATABASESIZE value in a more flexible way
        db_size_bytes=$(echo "$db_size_result" | grep -A1 "DATABASESIZE" | grep "Parameter Value" | tr -cd '0-9')
      fi
      
      # If still no result, try extracting the first large number (fallback)
      if [ -z "$db_size_bytes" ]; then
        db_size_bytes=$(echo "$db_size_result" | tr -cd '0-9 \n' | tr -s ' ' '\n' | grep '^[0-9][0-9]*$' | awk '$1 > 1000000 {print $1; exit}')
      fi
      
      log_info "Extracted database size: '$db_size_bytes' bytes"
    fi
  fi
  
  # Method 2: If DB_SIZE_INFO failed, try alternative system catalog approach
  if [ -z "$db_size_bytes" ]; then
    log_info "Trying alternative method using system catalogs"
    local alt_result
    if db2_query "$database" "SELECT SUM(FPAGES) * 4096 FROM SYSCAT.TABLESPACES WHERE TBSPACE NOT LIKE 'TEMP%'" "alt_result"; then
      if [ -n "$alt_result" ] && [ "$alt_result" != "0 record(s) selected." ]; then
        db_size_bytes=$(echo "$alt_result" | tr -cd '0-9' | head -c 20)  # Limit to reasonable length
      fi
    fi
  fi
  
  # Method 3: If still no size, try tablespace usage
  if [ -z "$db_size_bytes" ]; then
    log_info "Trying tablespace usage method"
    local ts_result
    if db2_query "$database" "SELECT SUM(USEDPAGES) * 4096 FROM TABLE(MON_GET_TABLESPACE('', -1))" "ts_result"; then
      if [ -n "$ts_result" ] && [ "$ts_result" != "0 record(s) selected." ]; then
        db_size_bytes=$(echo "$ts_result" | tr -cd '0-9' | head -c 20)
      fi
    fi
  fi
  
  # Validate the extracted size
  case "$db_size_bytes" in
    ''|*[!0-9]*) 
      log_test_result "RDS_SIZING_RECOMMENDATION" "INFO" "Unable to determine database size using available methods" "Consider manual database size calculation"
      log_test_result "RDS_SIZING_MANUAL" "INFO" "Manual calculation: Run 'db2 \"CALL GET_DBSIZE_INFO(?, ?, ?, -1)\"' and check output" "Use the database size value for RDS planning"
      log_test_result "RDS_SIZING_FALLBACK" "INFO" "Log space only: $(format_size "$log_space_kb"), Minimum RDS: 20 GB" "Add database size to this for total requirement"
      return
      ;;
    *)
      # Check if the size is reasonable (between 1KB and 100TB)
      if [ "$db_size_bytes" -lt 1024 ]; then
        log_test_result "RDS_SIZING_RECOMMENDATION" "INFO" "Database appears very small: $(format_size $((db_size_bytes / 1024)))" "Using minimum RDS storage recommendation"
        db_size_bytes=1048576  # Set to 1MB minimum
      elif [ "$db_size_bytes" -gt 109951162777600 ]; then
        log_test_result "RDS_SIZING_RECOMMENDATION" "INFO" "Database size appears unrealistic: $(format_size $((db_size_bytes / 1024)))" "Please verify database size manually"
        log_test_result "RDS_SIZING_MANUAL" "INFO" "Try: db2 \"SELECT SUM(FPAGES)*4096 FROM SYSCAT.TABLESPACES\"" "Alternative database size calculation"
        return
      fi
      ;;
  esac
  
  # Convert database size from bytes to KB
  local db_size_kb=$((db_size_bytes / 1024))
  
  # Calculate total storage requirement
  # Database size + Log space + 25% growth
  local total_base_kb=$((db_size_kb + log_space_kb))
  local growth_kb=$((total_base_kb * 25 / 100))
  local total_recommended_kb=$((total_base_kb + growth_kb))
  
  # Format sizes for display
  local db_size_formatted=$(format_size "$db_size_kb")
  local log_size_formatted=$(format_size "$log_space_kb")
  local total_base_formatted=$(format_size "$total_base_kb")
  local growth_formatted=$(format_size "$growth_kb")
  local total_recommended_formatted=$(format_size "$total_recommended_kb")
  
  # Log the sizing recommendation
  log_test_result "RDS_SIZING_RECOMMENDATION" "INFO" "Database size: $db_size_formatted, Log space: $log_size_formatted" "RDS DB2 sizing calculation"
  log_test_result "RDS_SIZING_BASE" "INFO" "Base storage requirement: $total_base_formatted (DB + Logs)" "Minimum RDS storage needed"
  log_test_result "RDS_SIZING_GROWTH" "INFO" "Recommended with 25% growth: $total_recommended_formatted" "Recommended RDS storage allocation"
  
  # Provide RDS-specific recommendations
  if [ "$total_recommended_kb" -lt 20971520 ]; then  # Less than 20 GB
    log_test_result "RDS_SIZING_TIER" "INFO" "Recommended RDS storage: 20 GB (minimum)" "RDS DB2 minimum storage is 20 GB"
  elif [ "$total_recommended_kb" -lt 67108864000 ]; then  # Less than 64 TB (corrected calculation)
    local recommended_gb=$((total_recommended_kb / 1024 / 1024))
    # Round up to nearest GB
    recommended_gb=$(((recommended_gb + 1)))
    log_test_result "RDS_SIZING_TIER" "INFO" "Recommended RDS storage: ${recommended_gb} GB" "Based on current usage + 25% growth"
  else
    log_test_result "RDS_SIZING_TIER" "INFO" "Database size exceeds RDS DB2 maximum (64 TB)" "Consider data archiving or partitioning"
  fi
}

validate_federation() {
  local instance="$1"
  local database="$2"
  
  log_info "Checking federation configuration for database: $database"
  
  # Use the db2_query function to execute the query
  local result
  if ! db2_query "$database" "SELECT WRAPNAME,LIBRARY FROM SYSCAT.WRAPPERS" "result"; then
    log_test_result "FEDERATION" "PASS" "No federation wrappers found" "Database may not have federation configured"
    return
  fi
  
  # POSIX-compliant check for "0 record(s) selected" using case statement
  case "$result" in
    *"0 record(s) selected"*)
      log_test_result "FEDERATION" "PASS" "No federation wrappers found"
      return
      ;;
    "")
      log_test_result "FEDERATION" "PASS" "No federation wrappers found"
      return
      ;;
  esac
  
  # Check for supported libraries using POSIX-compliant case statements
  case "$result" in
    *"libdb2drda.so"*)
      log_test_result "FEDERATION_WRAPPER" "PASS" "Supported federation wrapper found: libdb2drda.so"
      ;;
    *"libdb2rcodbc.so"*)
      log_test_result "FEDERATION_WRAPPER" "PASS" "Supported federation wrapper found: libdb2rcodbc.so"
      ;;
    *"libdb2"*|*".so"*)
      # Found some wrapper but not supported ones
      log_test_result "FEDERATION_WRAPPER" "FAIL" "Unsupported federation wrapper found" "RDS DB2 only supports libdb2drda.so and libdb2rcodbc.so"
      log_warning "RDS DB2 federation limitations:"
      log_warning "- Supports: DB2 LUW, DB2 iSeries, DB2 z/OS (libdb2drda.so)"
      log_warning "- Does not support: Sybase, Informix, Teradata, JDBC wrappers"
      ;;
    *)
      log_test_result "FEDERATION" "PASS" "No federation wrappers found"
      ;;
  esac
}

# =============================================================================
# Main Execution Functions
# =============================================================================

validate_database() {
  local instance="$1"
  local database="$2"
  
  # Store current counters to calculate database-specific counts
  local db_start_total=$TOTAL_CHECKS
  local db_start_passed=$PASSED_CHECKS
  local db_start_failed=$FAILED_CHECKS
  local db_start_warning=$WARNING_CHECKS
  local db_start_info=$INFO_CHECKS
  
  log_info "=========================================="
  if [ "$REMOTE_MODE" = "true" ]; then
    log_info "Validating database: $database (Remote Connection)"
  else
    log_info "Validating database: $database (Instance: $instance)"
  fi
  log_info "=========================================="
  
  # Check database version first
  check_database_version "$database"
  
  # Run all validation checks
  validate_db2_update_level "$instance" "$database"
  validate_indoubt_transactions "$instance" "$database"
  validate_invalid_objects "$instance" "$database"
  validate_tablespace_state "$instance" "$database"
  validate_non_fenced_routines "$instance" "$database"
  validate_java_procedures "$instance" "$database"
  validate_autostorage "$instance" "$database"
  validate_database_configuration "$instance" "$database"
  validate_federation "$instance" "$database"
  
  log_info "Validation completed for database: $database"
  
  # Perform inventory analysis if enabled
  if [ "$INVENTORY_MODE" = "true" ]; then
    perform_database_inventory "$database"
  fi
  
  # Generate individual database summary
  generate_database_summary "$database" "$db_start_total" "$db_start_passed" "$db_start_failed" "$db_start_warning" "$db_start_info"
  
  # Add extra spacing between databases
  log_info ""
  log_info ""
}

# Function to generate individual database summary
generate_database_summary() {
  local database="$1"
  local start_total="$2"
  local start_passed="$3"
  local start_failed="$4"
  local start_warning="$5"
  local start_info="$6"
  
  # Calculate database-specific counts
  local db_total=$((TOTAL_CHECKS - start_total))
  local db_passed=$((PASSED_CHECKS - start_passed))
  local db_failed=$((FAILED_CHECKS - start_failed))
  local db_warning=$((WARNING_CHECKS - start_warning))
  local db_info=$((INFO_CHECKS - start_info))
  
  log_info "=========================================="
  log_info "DATABASE SUMMARY: $database"
  log_info "=========================================="
  log_info "Checks performed: $db_total"
  log_success "Passed: $db_passed"
  log_info "Warnings: $db_warning"
  log_info "Failed: $db_failed"
  log_info "Informational: $db_info"
  log_info "=========================================="
  
  # Database-specific readiness assessment
  if [ "$db_failed" -gt 0 ]; then
    log_error "DATABASE READINESS: NOT READY"
    log_error "Database $database has $db_failed failed check(s)"
  elif [ "$db_warning" -gt 0 ]; then
    log_warning "DATABASE READINESS: REVIEW REQUIRED"
    log_warning "Database $database has $db_warning warning(s)"
  else
    log_success "DATABASE READINESS: READY"
    log_success "Database $database passed all checks"
  fi
  log_info "=========================================="
}

# =============================================================================
# DB2 INVENTORY FUNCTIONS
# =============================================================================

# Function to setup inventory files
setup_inventory_files() {
  local timestamp=$(date '+%Y%m%d_%H%M%S')
  INVENTORY_DETAIL_FILE="db2_inventory_detail_${timestamp}.txt"
  INVENTORY_SUMMARY_FILE="db2_inventory_summary_${timestamp}.txt"
  INVENTORY_JSON_FILE="db2_inventory_${timestamp}.json"
  
  log_info "Inventory detail file: $INVENTORY_DETAIL_FILE"
  log_info "Inventory summary file: $INVENTORY_SUMMARY_FILE"
  log_info "Inventory JSON file: $INVENTORY_JSON_FILE"
}

# Function to convert DB2 query result to JSON using jq or fallback
db2_result_to_json() {
  local result="$1"
  local object_type="$2"
  
  # Skip empty results
  if [ -z "$result" ] || [ "$result" = "0 record(s) selected." ]; then
    echo "[]"
    return
  fi
  
  # Check if jq is available
  if command -v jq >/dev/null 2>&1; then
    # Use jq for JSON conversion
    case "$object_type" in
      "tables"|"views"|"mqts"|"aliases"|"col_tables"|"shadow_tables")
        # Format: SCHEMA, OBJECT_NAME
        echo "$result" | awk 'NF >= 2 && !/^-/ && !/record.*selected/ {
          gsub(/^[ \t]+|[ \t]+$/, "", $1);
          gsub(/^[ \t]+|[ \t]+$/, "", $2);
          print $1 "\t" $2
        }' | jq -R -s 'split("\n") | map(select(length > 0)) | map(split("\t")) | map({"schema": .[0], "name": .[1]})'
        ;;
      "indexes"|"triggers")
        # Format: OBJECT_SCHEMA, OBJECT_NAME, TABLE_SCHEMA, TABLE_NAME
        echo "$result" | awk 'NF >= 4 && !/^-/ && !/record.*selected/ {
          gsub(/^[ \t]+|[ \t]+$/, "", $1);
          gsub(/^[ \t]+|[ \t]+$/, "", $2);
          gsub(/^[ \t]+|[ \t]+$/, "", $3);
          gsub(/^[ \t]+|[ \t]+$/, "", $4);
          print $1 "\t" $2 "\t" $3 "\t" $4
        }' | jq -R -s 'split("\n") | map(select(length > 0)) | map(split("\t")) | map({"schema": .[0], "name": .[1], "table_schema": .[2], "table_name": .[3]})'
        ;;
      "lob_columns")
        # Format: SCHEMA, TABLE_NAME, COLUMN_NAME, [TYPE], [LENGTH]
        echo "$result" | awk 'NF >= 3 && !/^-/ && !/record.*selected/ {
          gsub(/^[ \t]+|[ \t]+$/, "", $1);
          gsub(/^[ \t]+|[ \t]+$/, "", $2);
          gsub(/^[ \t]+|[ \t]+$/, "", $3);
          line = $1 "\t" $2 "\t" $3;
          if (NF >= 4) {
            gsub(/^[ \t]+|[ \t]+$/, "", $4);
            line = line "\t" $4;
          }
          if (NF >= 5) {
            gsub(/^[ \t]+|[ \t]+$/, "", $5);
            line = line "\t" $5;
          }
          print line
        }' | jq -R -s 'split("\n") | map(select(length > 0)) | map(split("\t")) | map(if length >= 5 then {"schema": .[0], "table_name": .[1], "column_name": .[2], "data_type": .[3], "length": .[4]} elif length >= 4 then {"schema": .[0], "table_name": .[1], "column_name": .[2], "data_type": .[3]} else {"schema": .[0], "table_name": .[1], "column_name": .[2]} end)'
        ;;
      "constraints")
        # Format: CONSTRAINT_NAME, SCHEMA, TABLE_NAME
        echo "$result" | awk 'NF >= 3 && !/^-/ && !/record.*selected/ {
          gsub(/^[ \t]+|[ \t]+$/, "", $1);
          gsub(/^[ \t]+|[ \t]+$/, "", $2);
          gsub(/^[ \t]+|[ \t]+$/, "", $3);
          print $1 "\t" $2 "\t" $3
        }' | jq -R -s 'split("\n") | map(select(length > 0)) | map(split("\t")) | map({"name": .[0], "schema": .[1], "table_name": .[2]})'
        ;;
      "foreign_keys")
        # Format: CONSTRAINT_NAME, SCHEMA, TABLE_NAME, REF_SCHEMA, REF_TABLE
        echo "$result" | awk 'NF >= 5 && !/^-/ && !/record.*selected/ {
          gsub(/^[ \t]+|[ \t]+$/, "", $1);
          gsub(/^[ \t]+|[ \t]+$/, "", $2);
          gsub(/^[ \t]+|[ \t]+$/, "", $3);
          gsub(/^[ \t]+|[ \t]+$/, "", $4);
          gsub(/^[ \t]+|[ \t]+$/, "", $5);
          print $1 "\t" $2 "\t" $3 "\t" $4 "\t" $5
        }' | jq -R -s 'split("\n") | map(select(length > 0)) | map(split("\t")) | map({"name": .[0], "schema": .[1], "table_name": .[2], "ref_schema": .[3], "ref_table": .[4]})'
        ;;
      "grants")
        # Format: GRANTEE, GRANT_COUNT
        echo "$result" | awk 'NF >= 2 && !/^-/ && !/record.*selected/ {
          gsub(/^[ \t]+|[ \t]+$/, "", $1);
          gsub(/^[ \t]+|[ \t]+$/, "", $2);
          print $1 "\t" $2
        }' | jq -R -s 'split("\n") | map(select(length > 0)) | map(split("\t")) | map({"grantee": .[0], "grant_count": (.[1] | tonumber)})'
        ;;
      "datatypes")
        # Format: TYPE_SCHEMA, TYPE_NAME
        echo "$result" | awk 'NF >= 2 && !/^-/ && !/record.*selected/ {
          gsub(/^[ \t]+|[ \t]+$/, "", $1);
          gsub(/^[ \t]+|[ \t]+$/, "", $2);
          print $1 "\t" $2
        }' | jq -R -s 'split("\n") | map(select(length > 0)) | map(split("\t")) | map({"type_schema": .[0], "type_name": .[1]})'
        ;;
      "tablespaces")
        # Format: TABLESPACE_NAME, TYPE
        echo "$result" | awk 'NF >= 2 && !/^-/ && !/record.*selected/ {
          gsub(/^[ \t]+|[ \t]+$/, "", $1);
          gsub(/^[ \t]+|[ \t]+$/, "", $2);
          print $1 "\t" $2
        }' | jq -R -s 'split("\n") | map(select(length > 0)) | map(split("\t")) | map({"name": .[0], "type": .[1]})'
        ;;
      *)
        # Default: simple objects with schema and name or just name
        echo "$result" | awk 'NF >= 1 && !/^-/ && !/record.*selected/ {
          gsub(/^[ \t]+|[ \t]+$/, "", $1);
          if (NF >= 2) {
            gsub(/^[ \t]+|[ \t]+$/, "", $2);
            print $1 "\t" $2
          } else {
            print $1
          }
        }' | jq -R -s 'split("\n") | map(select(length > 0)) | map(if contains("\t") then split("\t") | {"schema": .[0], "name": .[1]} else {"name": .} end)'
        ;;
    esac
  else
    # Fallback to awk-based JSON generation (basic but functional)
    case "$object_type" in
      "tables"|"views"|"mqts"|"aliases"|"col_tables"|"shadow_tables")
        echo "$result" | awk '
          BEGIN { print "["; first=1 }
          NF >= 2 && !/^-/ && !/record.*selected/ {
            if (!first) print ",";
            gsub(/^[ \t]+|[ \t]+$/, "", $1);
            gsub(/^[ \t]+|[ \t]+$/, "", $2);
            gsub(/"/, "\\\"", $1); gsub(/"/, "\\\"", $2);
            printf "  {\"schema\": \"%s\", \"name\": \"%s\"}", $1, $2;
            first=0
          }
          END { print "\n]" }'
        ;;
      *)
        echo "$result" | awk '
          BEGIN { print "["; first=1 }
          NF >= 1 && !/^-/ && !/record.*selected/ {
            if (!first) print ",";
            gsub(/^[ \t]+|[ \t]+$/, "", $1);
            gsub(/"/, "\\\"", $1);
            if (NF >= 2) {
              gsub(/^[ \t]+|[ \t]+$/, "", $2);
              gsub(/"/, "\\\"", $2);
              printf "  {\"schema\": \"%s\", \"name\": \"%s\"}", $1, $2;
            } else {
              printf "  {\"name\": \"%s\"}", $1;
            }
            first=0
          }
          END { print "\n]" }'
        ;;
    esac
  fi
}

# Helper function to extract section data and convert to JSON
extract_and_convert_section() {
  local section_name="$1"
  local object_type="$2"
  
  if [ -f "$INVENTORY_DETAIL_FILE" ]; then
    local section_data
    # Extract data between section header and next empty line, excluding headers and separators
    section_data=$(awk "/^$section_name \([0-9]+\):/{flag=1; next} /^$/{flag=0} flag && NF>0 && !/^-+/ && !/TABSCHEMA.*TABNAME/ && !/VIEWSCHEMA.*VIEWNAME/ && !/INDSCHEMA.*INDNAME/ && !/TRIGSCHEMA.*TRIGNAME/ && !/ROUTINESCHEMA.*ROUTINENAME/ && !/SEQSCHEMA.*SEQNAME/ && !/SCHEMANAME/ && !/CONSTNAME/ && !/SECLABELNAME/ && !/SECPOLICYNAME/ && !/ROLENAME/ && !/GRANTEE/ && !/TYPESCHEMA.*TYPENAME/ && !/COLNAME/ && !/TBSPACE/ && !/SGNAME/" "$INVENTORY_DETAIL_FILE" 2>/dev/null || echo "")
    if [ -n "$section_data" ]; then
      db2_result_to_json "$section_data" "$object_type"
    else
      echo "[]"
    fi
  else
    echo "[]"
  fi
}

# Global variable to track database inventories for multi-database JSON
DATABASE_INVENTORIES="[]"

# Function to generate JSON inventory for a database using jq or fallback
generate_json_inventory() {
  local database="$1"
  local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
  
  if command -v jq >/dev/null 2>&1; then
    # Use jq for proper JSON generation - use stored data variables directly
    local db_inventory
    db_inventory=$(jq -n \
      --arg database "$database" \
      --arg timestamp "$timestamp" \
      --argjson tables "$tables" \
      --argjson views "$views" \
      --argjson mqts "$mqts" \
      --argjson indexes "$indexes" \
      --argjson triggers "$triggers" \
      --argjson functions "$functions" \
      --argjson procedures "$procedures" \
      --argjson methods "$methods" \
      --argjson sequences "$sequences" \
      --argjson schemas "$schemas" \
      --argjson checks "$checks" \
      --argjson pks "$pks" \
      --argjson fks "$fks" \
      --argjson lbac_labels "$lbac_labels" \
      --argjson lbac_policies "$lbac_policies" \
      --argjson rcac_row "$rcac_row" \
      --argjson rcac_col "$rcac_col" \
      --argjson row_permissions "$row_permissions" \
      --argjson column_masks "$column_masks" \
      --argjson aliases "$aliases" \
      --argjson roles "$roles" \
      --argjson grants "$grants" \
      --argjson datatypes "$datatypes" \
      --argjson blob_cols "$blob_cols" \
      --argjson clob_cols "$clob_cols" \
      --argjson dbclob_cols "$dbclob_cols" \
      --argjson xml_cols "$xml_cols" \
      --argjson inline_lobs "$inline_lobs" \
      --argjson inline_xml "$inline_xml" \
      --argjson sms_tbsp "$sms_tbsp" \
      --argjson dms_tbsp "$dms_tbsp" \
      --argjson stogroups "$stogroups" \
      --argjson col_tables "$col_tables" \
      --argjson shadow_tables "$shadow_tables" \
      --argjson tables_items "$(db2_result_to_json "$tables_data" "tables")" \
      --argjson views_items "$(db2_result_to_json "$views_data" "views")" \
      --argjson mqts_items "$(db2_result_to_json "$mqts_data" "mqts")" \
      --argjson indexes_items "$(db2_result_to_json "$indexes_data" "indexes")" \
      --argjson triggers_items "$(db2_result_to_json "$triggers_data" "triggers")" \
      --argjson functions_items "$(db2_result_to_json "$functions_data" "functions")" \
      --argjson procedures_items "$(db2_result_to_json "$procedures_data" "procedures")" \
      --argjson methods_items "$(db2_result_to_json "$methods_data" "methods")" \
      --argjson sequences_items "$(db2_result_to_json "$sequences_data" "sequences")" \
      --argjson schemas_items "$(db2_result_to_json "$schemas_data" "schemas")" \
      '{
        "database": $database,
        "generated": $timestamp,
        "inventory": {
          "basic_objects": {
            "tables": { "count": $tables, "items": $tables_items },
            "views": { "count": $views, "items": $views_items },
            "materialized_query_tables": { "count": $mqts, "items": $mqts_items },
            "indexes": { "count": $indexes, "items": $indexes_items },
            "triggers": { "count": $triggers, "items": $triggers_items },
            "functions": { "count": $functions, "items": $functions_items },
            "procedures": { "count": $procedures, "items": $procedures_items },
            "methods": { "count": $methods, "items": $methods_items },
            "sequences": { "count": $sequences, "items": $sequences_items },
            "schemas": { "count": $schemas, "items": $schemas_items }
          },
          "summary": {
            "total_basic_objects": ($tables + $views + $mqts + $indexes + $triggers + $functions + $procedures + $methods + $sequences),
            "total_schemas": $schemas
          }
        }
      }')
    
    # Add this database inventory to the global collection
    DATABASE_INVENTORIES=$(echo "$DATABASE_INVENTORIES" | jq --argjson db_inv "$db_inventory" '. + [$db_inv]')
  else
    # Fallback: Generate basic JSON manually using stored data
    {
      echo "{"
      echo "  \"database\": \"$database\","
      echo "  \"generated\": \"$timestamp\","
      echo "  \"inventory\": {"
      echo "    \"basic_objects\": {"
      echo "      \"tables\": { \"count\": $tables, \"items\": $(db2_result_to_json "$tables_data" "tables") },"
      echo "      \"views\": { \"count\": $views, \"items\": $(db2_result_to_json "$views_data" "views") },"
      echo "      \"materialized_query_tables\": { \"count\": $mqts, \"items\": $(db2_result_to_json "$mqts_data" "mqts") },"
      echo "      \"indexes\": { \"count\": $indexes, \"items\": $(db2_result_to_json "$indexes_data" "indexes") },"
      echo "      \"triggers\": { \"count\": $triggers, \"items\": $(db2_result_to_json "$triggers_data" "triggers") },"
      echo "      \"functions\": { \"count\": $functions, \"items\": $(db2_result_to_json "$functions_data" "functions") },"
      echo "      \"procedures\": { \"count\": $procedures, \"items\": $(db2_result_to_json "$procedures_data" "procedures") },"
      echo "      \"methods\": { \"count\": $methods, \"items\": $(db2_result_to_json "$methods_data" "methods") },"
      echo "      \"sequences\": { \"count\": $sequences, \"items\": $(db2_result_to_json "$sequences_data" "sequences") },"
      echo "      \"schemas\": { \"count\": $schemas, \"items\": $(db2_result_to_json "$schemas_data" "schemas") }"
      echo "    },"
      echo "    \"summary\": {"
      echo "      \"total_basic_objects\": $((tables + views + mqts + indexes + triggers + functions + procedures + methods + sequences)),"
      echo "      \"total_schemas\": $schemas"
      echo "    }"
      echo "  }"
      echo "}"
    } >> "$INVENTORY_JSON_FILE"
  fi
  
  log_info "JSON inventory generated for database: $database"
}

# Function to finalize multi-database JSON inventory
finalize_json_inventory() {
  local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
  local instance="$1"
  
  if command -v jq >/dev/null 2>&1; then
    # Create the final JSON structure with all databases using jq
    jq -n \
      --arg instance "$instance" \
      --arg timestamp "$timestamp" \
      --argjson databases "$DATABASE_INVENTORIES" \
      '{
        "instance": $instance,
        "generated": $timestamp,
        "database_count": ($databases | length),
        "databases": $databases,
        "instance_summary": {
          "total_databases": ($databases | length),
          "total_basic_objects": ($databases | map(.inventory.summary.total_basic_objects) | add // 0),
          "total_constraints": ($databases | map(.inventory.summary.total_constraints) | add // 0),
          "total_security_features": ($databases | map(.inventory.summary.total_security_features) | add // 0),
          "total_data_complexity": ($databases | map(.inventory.summary.total_data_complexity) | add // 0),
          "total_storage_complexity": ($databases | map(.inventory.summary.total_storage_complexity) | add // 0),
          "total_advanced_features": ($databases | map(.inventory.summary.total_advanced_features) | add // 0),
          "total_schemas": ($databases | map(.inventory.summary.total_schemas) | add // 0)
        }
      }' > "$INVENTORY_JSON_FILE"
  else
    # Fallback: Basic JSON structure without jq
    log_warning "jq not available - generating basic JSON structure"
  fi
  
  log_info "Final JSON inventory generated: $INVENTORY_JSON_FILE"
}

# Function to perform DB2 inventory analysis for a database
perform_database_inventory() {
  local database="$1"
  local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
  
  log_info "=========================================="
  log_info "DB2 INVENTORY ANALYSIS FOR DATABASE: $database"
  log_info "=========================================="
  
  # Initialize inventory counters
  local tables=0 views=0 mqts=0 indexes=0 triggers=0 functions=0 procedures=0 sequences=0 schemas=0
  local methods=0 checks=0 pks=0 fks=0 lbac_labels=0 lbac_policies=0
  local rcac_row=0 rcac_col=0 row_permissions=0 column_masks=0 aliases=0
  local roles=0 grants=0 datatypes=0 blob_cols=0 clob_cols=0
  local dbclob_cols=0 xml_cols=0 inline_lobs=0 inline_xml=0 sms_tbsp=0
  local dms_tbsp=0 stogroups=0 col_tables=0 shadow_tables=0
  
  # Initialize data variables for JSON generation
  local tables_data="" views_data="" mqts_data="" indexes_data="" triggers_data=""
  local functions_data="" procedures_data="" methods_data="" sequences_data="" schemas_data=""
  
  # Write header to detail file
  {
    echo "=========================================="
    echo "DB2 INVENTORY ANALYSIS FOR DATABASE: $database"
    echo "Generated: $timestamp"
    echo "=========================================="
    echo ""
  } >> "$INVENTORY_DETAIL_FILE"
  
  # 1. Tables
  log_info "Analyzing tables..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.TABLES WHERE TYPE = 'T' AND TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    tables=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$tables" ] && tables=0
    
    # Store data for JSON generation
    if db2_query "$database" "SELECT substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME FROM SYSCAT.TABLES WHERE TYPE = 'T' AND TABSCHEMA NOT LIKE 'SYS%'" "tables_data" "true"; then
      # Also write to detail file
      {
        echo "TABLES ($tables):"
        echo "$tables_data"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    else
      tables_data=""
    fi
  fi
  
  # 2. Views
  log_info "Analyzing views..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.VIEWS WHERE VIEWSCHEMA NOT LIKE 'SYS%'" "result"; then
    views=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$views" ] && views=0
    
    if db2_query "$database" "SELECT substr(VIEWSCHEMA,1,20) AS VIEWSCHEMA, substr(VIEWNAME,1,20) AS VIEWNAME FROM SYSCAT.VIEWS WHERE VIEWSCHEMA NOT LIKE 'SYS%'" "views_data" "true"; then
      {
        echo "VIEWS ($views):"
        echo "$views_data"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    else
      views_data=""
    fi
  fi
  
  # 3. Materialized Query Tables
  log_info "Analyzing materialized query tables..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.TABLES WHERE TYPE = 'S' AND TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    mqts=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$mqts" ] && mqts=0
    
    if db2_query "$database" "SELECT substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME FROM SYSCAT.TABLES WHERE TYPE = 'S' AND TABSCHEMA NOT LIKE 'SYS%'" "mqts_data" "true"; then
      {
        echo "MATERIALIZED QUERY TABLES ($mqts):"
        echo "$mqts_data"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    else
      mqts_data=""
    fi
  fi
  
  # 4. Indexes
  log_info "Analyzing indexes..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.INDEXES WHERE TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    indexes=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$indexes" ] && indexes=0
    
    if db2_query "$database" "SELECT substr(INDSCHEMA,1,20) AS INDSCHEMA, substr(INDNAME, 1, 20) AS INDNAME, substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME FROM SYSCAT.INDEXES WHERE TABSCHEMA NOT LIKE 'SYS%'" "indexes_data" "true"; then
      {
        echo "INDEXES ($indexes):"
        echo "$indexes_data"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    else
      indexes_data=""
    fi
  fi
  
  # 5. Triggers
  log_info "Analyzing triggers..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.TRIGGERS WHERE TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    triggers=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$triggers" ] && triggers=0
    
    if db2_query "$database" "SELECT substr(TRIGSCHEMA, 1, 20) AS TRIGSCHEMA, substr(TRIGNAME, 1, 20) AS TRIGNAME, substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME FROM SYSCAT.TRIGGERS WHERE TABSCHEMA NOT LIKE 'SYS%'" "triggers_data" "true"; then
      {
        echo "TRIGGERS ($triggers):"
        echo "$triggers_data"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    else
      triggers_data=""
    fi
  fi
  
  # 6. Functions
  log_info "Analyzing functions..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.ROUTINES WHERE ROUTINETYPE = 'F' AND ROUTINESCHEMA NOT LIKE 'SYS%'" "result"; then
    functions=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$functions" ] && functions=0
    
    if db2_query "$database" "SELECT substr(ROUTINESCHEMA,1,20) AS ROUTINESCHEMA, substr(ROUTINENAME,1,20) AS ROUTINENAME FROM SYSCAT.ROUTINES WHERE ROUTINETYPE = 'F' AND ROUTINESCHEMA NOT LIKE 'SYS%'" "functions_data" "true"; then
      {
        echo "FUNCTIONS ($functions):"
        echo "$functions_data"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    else
      functions_data=""
    fi
  fi
  
  # 7. Procedures
  log_info "Analyzing procedures..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.ROUTINES WHERE ROUTINETYPE = 'P' AND ROUTINESCHEMA NOT LIKE 'SYS%' AND ROUTINESCHEMA NOT LIKE 'SQLJ%'" "result"; then
    procedures=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$procedures" ] && procedures=0
    
    if db2_query "$database" "SELECT substr(ROUTINESCHEMA,1,20) AS ROUTINESCHEMA, substr(ROUTINENAME,1,40) as ROUTINENAME FROM SYSCAT.ROUTINES WHERE ROUTINETYPE = 'P' AND ROUTINESCHEMA NOT LIKE 'SYS%'  AND ROUTINESCHEMA NOT LIKE 'SQLJ%'" "procedures_data" "true"; then
      {
        echo "PROCEDURES ($procedures):"
        echo "$procedures_data"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    else
      procedures_data=""
    fi
  fi
  
  # 8. Sequences
  log_info "Analyzing sequences..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.SEQUENCES WHERE SEQSCHEMA NOT LIKE 'SYS%'" "result"; then
    sequences=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$sequences" ] && sequences=0
    
    if db2_query "$database" "SELECT substr(SEQSCHEMA,1,20) AS SEQSCHEMA, substr(SEQNAME,1,30) AS SEQNAME FROM SYSCAT.SEQUENCES WHERE SEQSCHEMA NOT LIKE 'SYS%'" "sequences_data" "true"; then
      {
        echo "SEQUENCES ($sequences):"
        echo "$sequences_data"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    else
      sequences_data=""
    fi
  fi
  
  # 9. Schemas
  log_info "Analyzing schemas..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.SCHEMATA WHERE SCHEMANAME NOT LIKE 'SYS%' AND SCHEMANAME NOT LIKE 'SQLJ%' AND SCHEMANAME NOT LIKE 'NULLID%'" "result"; then
    schemas=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$schemas" ] && schemas=0
    
    if db2_query "$database" "SELECT substr(SCHEMANAME,1,20) AS SCHEMANAME FROM SYSCAT.SCHEMATA WHERE SCHEMANAME NOT LIKE 'SYS%'  AND SCHEMANAME NOT LIKE 'SQLJ%' AND SCHEMANAME NOT LIKE 'NULLID%'" "schemas_data" "true"; then
      {
        echo "SCHEMAS ($schemas):"
        echo "$schemas_data"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    else
      schemas_data=""
    fi
  fi
  
  # 10. Methods
  log_info "Analyzing methods..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.ROUTINES WHERE ROUTINETYPE = 'M' AND ROUTINESCHEMA NOT LIKE 'SYS%'" "result"; then
    methods=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$methods" ] && methods=0
    
    if db2_query "$database" "SELECT substr(ROUTINESCHEMA,1,20) AS ROUTINESCHEMA, substr(ROUTINENAME,1,30) AS ROUTINENAME FROM SYSCAT.ROUTINES WHERE ROUTINETYPE = 'M' AND ROUTINESCHEMA NOT LIKE 'SYS%'" "methods_data" "true"; then
      {
        echo "METHODS ($methods):"
        echo "$methods_data"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    else
      methods_data=""
    fi
  fi
  
  # 11. Check Constraints
  log_info "Analyzing check constraints..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.CHECKS WHERE TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    checks=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$checks" ] && checks=0
    
    if db2_query "$database" "SELECT substr(CONSTNAME,1,20) AS CONSTNAME, substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME FROM SYSCAT.CHECKS WHERE TABSCHEMA NOT LIKE 'SYS%'" "detail_result" "false"; then
      {
        echo "CHECK CONSTRAINTS ($checks):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 12. Primary Keys
  log_info "Analyzing primary keys..."
  if db2_query "$database" "SELECT COUNT(DISTINCT CONSTNAME) FROM SYSCAT.KEYCOLUSE WHERE KEYTYPE = 'P' AND TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    pks=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$pks" ] && pks=0
    
    if db2_query "$database" "SELECT substr(CONSTNAME,1,30) AS CONSTNAME, substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME FROM SYSCAT.KEYCOLUSE WHERE KEYTYPE = 'P' AND TABSCHEMA NOT LIKE 'SYS%'" "detail_result" "false"; then
      {
        echo "PRIMARY KEYS ($pks):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 13. Foreign Keys
  log_info "Analyzing foreign keys..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.REFERENCES WHERE TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    fks=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$fks" ] && fks=0
    
    if db2_query "$database" "SELECT substr(CONSTNAME,1,30) AS CONSTNAME, substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME, REFTABSCHEMA, REFTABNAME FROM SYSCAT.REFERENCES WHERE TABSCHEMA NOT LIKE 'SYS%'" "detail_result" "false"; then
      {
        echo "FOREIGN KEYS ($fks):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 14. LBAC Objects (Security Labels)
  log_info "Analyzing LBAC security labels..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.SECURITYLABELS" "result"; then
    lbac_labels=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$lbac_labels" ] && lbac_labels=0
    
    if db2_query "$database" "SELECT substr(SECLABELNAME,1,30) AS SECLABELNAME FROM SYSCAT.SECURITYLABELS" "detail_result" "false"; then
      {
        echo "LBAC SECURITY LABELS ($lbac_labels):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 15. LBAC Objects (Security Policies)
  log_info "Analyzing LBAC security policies..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.SECURITYPOLICIES" "result"; then
    lbac_policies=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$lbac_policies" ] && lbac_policies=0
    
    if db2_query "$database" "SELECT substr(SECPOLICYNAME,1,30) AS SECPOLICYNAME FROM SYSCAT.SECURITYPOLICIES" "detail_result" "false"; then
      {
        echo "LBAC SECURITY POLICIES ($lbac_policies):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 16. RCAC Objects (Row-access enabled tables)
  log_info "Analyzing RCAC row-access enabled tables..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.TABLES WHERE ROWACCESSCONTROL = 'Y'" "result"; then
    rcac_row=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$rcac_row" ] && rcac_row=0
    
    if db2_query "$database" "SELECT substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME, ROWACCESSCONTROL FROM SYSCAT.TABLES WHERE ROWACCESSCONTROL = 'Y'" "detail_result" "false"; then
      {
        echo "RCAC ROW-ACCESS ENABLED TABLES ($rcac_row):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 17. RCAC Objects (Column-access enabled tables)
  log_info "Analyzing RCAC column-access enabled tables..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.TABLES WHERE COLACCESSCONTROL = 'Y'" "result"; then
    rcac_col=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$rcac_col" ] && rcac_col=0
    
    if db2_query "$database" "SELECT substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME, COLACCESSCONTROL FROM SYSCAT.TABLES WHERE COLACCESSCONTROL = 'Y'" "detail_result" "false"; then
      {
        echo "RCAC COLUMN-ACCESS ENABLED TABLES ($rcac_col):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 18. Row Permissions
  log_info "Analyzing row permissions..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.ROUTINES WHERE ROUTINETYPE = 'P' AND SECURE = 'Y' AND SPECIFICNAME LIKE 'SQL%'" "result"; then
    row_permissions=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$row_permissions" ] && row_permissions=0
    
    if db2_query "$database" "SELECT substr(ROUTINESCHEMA,1,20) AS ROUTINESCHEMA, substr(ROUTINEMODULENAME,1,30) AS ROUTINEMODULENAME, substr(ROUTINENAME,1,20) AS ROUTINENAME FROM SYSCAT.ROUTINES WHERE ROUTINETYPE = 'P' AND SECURE = 'Y' AND SPECIFICNAME LIKE 'SQL%'" "detail_result" "false"; then
      {
        echo "ROW PERMISSIONS ($row_permissions):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 19. Column Masks
  log_info "Analyzing column masks..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.ROUTINES WHERE ROUTINESCHEMA NOT LIKE 'SYS%' AND ROUTINETYPE = 'F' AND SECURE = 'Y' AND SPECIFICNAME LIKE 'SQL%'" "result"; then
    column_masks=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$column_masks" ] && column_masks=0
    
    if db2_query "$database" "SELECT substr(ROUTINESCHEMA,1,20) AS ROUTINESCHEMA, substr(ROUTINEMODULENAME,1,30) AS ROUTINEMODULENAME, substr(ROUTINENAME,1,20) AS ROUTINENAME FROM SYSCAT.ROUTINES WHERE ROUTINESCHEMA NOT LIKE 'SYS%' AND ROUTINETYPE = 'F' AND SECURE = 'Y' AND SPECIFICNAME LIKE 'SQL%'" "detail_result" "false"; then
      {
        echo "COLUMN MASKS ($column_masks):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 20. Aliases or Synonyms
  log_info "Analyzing aliases..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.TABLES WHERE TYPE = 'A' AND TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    aliases=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$aliases" ] && aliases=0
    
    if db2_query "$database" "SELECT substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME FROM SYSCAT.TABLES WHERE TYPE = 'A' AND TABSCHEMA NOT LIKE 'SYS%'" "detail_result" "false"; then
      {
        echo "ALIASES ($aliases):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 21. Roles
  log_info "Analyzing roles..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.ROLES WHERE ROLENAME NOT LIKE 'SYS%'" "result"; then
    roles=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$roles" ] && roles=0
    
    if db2_query "$database" "SELECT substr(ROLENAME,1,30) AS ROLENAME FROM SYSCAT.ROLES WHERE ROLENAME NOT LIKE 'SYS%'" "detail_result" "false"; then
      {
        echo "ROLES ($roles):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 22. Total Grants on Users, Groups, and Roles
  log_info "Analyzing grants..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.TABAUTH WHERE TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    grants=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$grants" ] && grants=0
    
    if db2_query "$database" "SELECT substr(GRANTEE,1,20) AS GRANTEE, COUNT(*) AS GRANT_COUNT FROM SYSCAT.TABAUTH WHERE TABSCHEMA NOT LIKE 'SYS%' GROUP BY GRANTEE" "detail_result" "false"; then
      {
        echo "GRANTS ($grants):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 23. List of Data Types Used in All Tables
  log_info "Analyzing data types..."
  if db2_query "$database" "SELECT COUNT(DISTINCT TYPENAME) FROM SYSCAT.COLUMNS WHERE TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    datatypes=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$datatypes" ] && datatypes=0
    
    if db2_query "$database" "SELECT DISTINCT substr(TYPESCHEMA,1,20) AS TYPESCHEMA, substr(TYPENAME,1,20) AS TYPENAME FROM SYSCAT.COLUMNS WHERE TABSCHEMA NOT LIKE 'SYS%' ORDER BY TYPESCHEMA, TYPENAME" "detail_result" "false"; then
      {
        echo "DATA TYPES ($datatypes):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 24. Total BLOB Columns
  log_info "Analyzing BLOB columns..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.COLUMNS WHERE TYPENAME = 'BLOB' AND TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    blob_cols=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$blob_cols" ] && blob_cols=0
    
    if db2_query "$database" "SELECT substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME, SUBSTR(COLNAME,1,20) AS COLNAME FROM SYSCAT.COLUMNS WHERE TYPENAME = 'BLOB' AND TABSCHEMA NOT LIKE 'SYS%'" "detail_result" "false"; then
      {
        echo "BLOB COLUMNS ($blob_cols):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 25. Total CLOB Columns
  log_info "Analyzing CLOB columns..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.COLUMNS WHERE TYPENAME = 'CLOB' AND TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    clob_cols=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$clob_cols" ] && clob_cols=0
    
    if db2_query "$database" "SELECT substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME, SUBSTR(COLNAME,1,20) AS COLNAME FROM SYSCAT.COLUMNS WHERE TYPENAME = 'CLOB' AND TABSCHEMA NOT LIKE 'SYS%'" "detail_result" "false"; then
      {
        echo "CLOB COLUMNS ($clob_cols):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 26. Total DBCLOB Columns
  log_info "Analyzing DBCLOB columns..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.COLUMNS WHERE TYPENAME = 'DBCLOB' AND TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    dbclob_cols=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$dbclob_cols" ] && dbclob_cols=0
    
    if db2_query "$database" "SELECT substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME, SUBSTR(COLNAME,1,20) AS COLNAME FROM SYSCAT.COLUMNS WHERE TYPENAME = 'DBCLOB' AND TABSCHEMA NOT LIKE 'SYS%'" "detail_result" "false"; then
      {
        echo "DBCLOB COLUMNS ($dbclob_cols):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 27. Total XML Columns
  log_info "Analyzing XML columns..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.COLUMNS WHERE TYPENAME = 'XML' AND TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    xml_cols=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$xml_cols" ] && xml_cols=0
    
    if db2_query "$database" "SELECT substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME, SUBSTR(COLNAME,1,20) AS COLNAME FROM SYSCAT.COLUMNS WHERE TYPENAME = 'XML' AND TABSCHEMA NOT LIKE 'SYS%'" "detail_result" "false"; then
      {
        echo "XML COLUMNS ($xml_cols):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 28. Total Inline BLOB or CLOB (less than 32KB)
  log_info "Analyzing inline LOB columns..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.COLUMNS WHERE (TYPENAME = 'BLOB' OR TYPENAME = 'CLOB' OR TYPENAME = 'DBCLOB') AND LENGTH < 32768 AND TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    inline_lobs=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$inline_lobs" ] && inline_lobs=0
    
    if db2_query "$database" "SELECT substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME, SUBSTR(COLNAME,1,20) AS COLNAME, TYPENAME, LENGTH FROM SYSCAT.COLUMNS WHERE (TYPENAME = 'BLOB' OR TYPENAME = 'CLOB' OR TYPENAME = 'DBCLOB') AND LENGTH < 32768 AND TABSCHEMA NOT LIKE 'SYS%'" "detail_result" "false"; then
      {
        echo "INLINE LOB COLUMNS ($inline_lobs):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 29. Total Inline XML (less than 32KB)
  log_info "Analyzing inline XML columns..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.COLUMNS WHERE TYPENAME = 'XML' AND LENGTH < 32768 AND TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    inline_xml=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$inline_xml" ] && inline_xml=0
    
    if db2_query "$database" "SELECT substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME, SUBSTR(COLNAME,1,20) AS COLNAME, LENGTH FROM SYSCAT.COLUMNS WHERE TYPENAME = 'XML' AND LENGTH < 32768 AND TABSCHEMA NOT LIKE 'SYS%'" "detail_result" "false"; then
      {
        echo "INLINE XML COLUMNS ($inline_xml):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 30. SMS Tablespaces
  log_info "Analyzing SMS tablespaces..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.TABLESPACES WHERE TBSPACETYPE = 'S' AND TBSPACE NOT LIKE 'SYS%'" "result"; then
    sms_tbsp=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$sms_tbsp" ] && sms_tbsp=0
    
    if db2_query "$database" "SELECT substr(TBSPACE,1,20) AS TBSPACE, TBSPACETYPE FROM SYSCAT.TABLESPACES WHERE TBSPACETYPE = 'S' AND TBSPACE NOT LIKE 'SYS%'" "detail_result" "false"; then
      {
        echo "SMS TABLESPACES ($sms_tbsp):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 31. DMS Tablespaces
  log_info "Analyzing DMS tablespaces..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.TABLESPACES WHERE TBSPACETYPE = 'D' AND TBSPACE NOT LIKE 'SYS%'" "result"; then
    dms_tbsp=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$dms_tbsp" ] && dms_tbsp=0
    
    if db2_query "$database" "SELECT substr(TBSPACE,1,20) AS TBSPACE, TBSPACETYPE FROM SYSCAT.TABLESPACES WHERE TBSPACETYPE = 'D' AND TBSPACE NOT LIKE 'SYS%'" "detail_result" "false"; then
      {
        echo "DMS TABLESPACES ($dms_tbsp):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 32. Storage Groups
  log_info "Analyzing storage groups..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.STOGROUPS WHERE SGNAME NOT LIKE 'SYS%'" "result"; then
    stogroups=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$stogroups" ] && stogroups=0
    
    if db2_query "$database" "SELECT substr(SGNAME,1,20) AS SGNAME FROM SYSCAT.STOGROUPS WHERE SGNAME NOT LIKE 'SYS%'" "detail_result" "false"; then
      {
        echo "STORAGE GROUPS ($stogroups):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 33. Column-Organized Tables
  log_info "Analyzing column-organized tables..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.TABLES WHERE SUBSTR(PROPERTY, 20, 1) = 'Y' AND TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    col_tables=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$col_tables" ] && col_tables=0
    
    if db2_query "$database" "SELECT substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME FROM SYSCAT.TABLES WHERE SUBSTR(PROPERTY, 20, 1) = 'Y' AND TABSCHEMA NOT LIKE 'SYS%'" "detail_result" "false"; then
      {
        echo "COLUMN-ORGANIZED TABLES ($col_tables):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # 34. Shadow Tables
  log_info "Analyzing shadow tables..."
  if db2_query "$database" "SELECT COUNT(*) FROM SYSCAT.TABLES WHERE SUBSTR(PROPERTY, 23, 1) = 'Y' AND TABSCHEMA NOT LIKE 'SYS%'" "result"; then
    shadow_tables=$(echo "$result" | tr -cd '0-9' | head -c 10)
    [ -z "$shadow_tables" ] && shadow_tables=0
    
    if db2_query "$database" "SELECT substr(TABSCHEMA,1,20) AS TABSCHEMA, substr(TABNAME,1,20) AS TABNAME FROM SYSCAT.TABLES WHERE SUBSTR(PROPERTY, 23, 1) = 'Y' AND TABSCHEMA NOT LIKE 'SYS%'" "detail_result" "false"; then
      {
        echo "SHADOW TABLES ($shadow_tables):"
        echo "$detail_result"
        echo ""
      } >> "$INVENTORY_DETAIL_FILE"
    fi
  fi
  
  # Display inventory summary on console
  log_test_result "INVENTORY_TABLES" "INFO" "Tables: $tables" "Database objects inventory"
  log_test_result "INVENTORY_VIEWS" "INFO" "Views: $views" "Database objects inventory"
  log_test_result "INVENTORY_MQTS" "INFO" "Materialized Query Tables: $mqts" "Database objects inventory"
  log_test_result "INVENTORY_INDEXES" "INFO" "Indexes: $indexes" "Database objects inventory"
  log_test_result "INVENTORY_TRIGGERS" "INFO" "Triggers: $triggers" "Database objects inventory"
  log_test_result "INVENTORY_FUNCTIONS" "INFO" "Functions: $functions" "Database objects inventory"
  log_test_result "INVENTORY_PROCEDURES" "INFO" "Procedures: $procedures" "Database objects inventory"
  log_test_result "INVENTORY_SEQUENCES" "INFO" "Sequences: $sequences" "Database objects inventory"
  log_test_result "INVENTORY_SCHEMAS" "INFO" "Schemas: $schemas" "Database objects inventory"
  log_test_result "INVENTORY_METHODS" "INFO" "Methods: $methods" "Database objects inventory"
  log_test_result "INVENTORY_CHECKS" "INFO" "Check Constraints: $checks" "Database constraints inventory"
  log_test_result "INVENTORY_PKS" "INFO" "Primary Keys: $pks" "Database constraints inventory"
  log_test_result "INVENTORY_FKS" "INFO" "Foreign Keys: $fks" "Database constraints inventory"
  log_test_result "INVENTORY_LBAC_LABELS" "INFO" "LBAC Security Labels: $lbac_labels" "Security features inventory"
  log_test_result "INVENTORY_LBAC_POLICIES" "INFO" "LBAC Security Policies: $lbac_policies" "Security features inventory"
  log_test_result "INVENTORY_RCAC_ROW" "INFO" "RCAC Row-Access Tables: $rcac_row" "Security features inventory"
  log_test_result "INVENTORY_RCAC_COL" "INFO" "RCAC Column-Access Tables: $rcac_col" "Security features inventory"
  log_test_result "INVENTORY_ROW_PERMISSIONS" "INFO" "Row Permissions: $row_permissions" "Security features inventory"
  log_test_result "INVENTORY_COLUMN_MASKS" "INFO" "Column Masks: $column_masks" "Security features inventory"
  log_test_result "INVENTORY_ALIASES" "INFO" "Aliases: $aliases" "Database objects inventory"
  log_test_result "INVENTORY_ROLES" "INFO" "Roles: $roles" "Security features inventory"
  log_test_result "INVENTORY_GRANTS" "INFO" "Grants: $grants" "Security features inventory"
  log_test_result "INVENTORY_DATATYPES" "INFO" "Data Types: $datatypes" "Data complexity inventory"
  log_test_result "INVENTORY_BLOB_COLS" "INFO" "BLOB Columns: $blob_cols" "LOB complexity inventory"
  log_test_result "INVENTORY_CLOB_COLS" "INFO" "CLOB Columns: $clob_cols" "LOB complexity inventory"
  log_test_result "INVENTORY_DBCLOB_COLS" "INFO" "DBCLOB Columns: $dbclob_cols" "LOB complexity inventory"
  log_test_result "INVENTORY_XML_COLS" "INFO" "XML Columns: $xml_cols" "LOB complexity inventory"
  log_test_result "INVENTORY_INLINE_LOBS" "INFO" "Inline LOB Columns: $inline_lobs" "LOB complexity inventory"
  log_test_result "INVENTORY_INLINE_XML" "INFO" "Inline XML Columns: $inline_xml" "LOB complexity inventory"
  log_test_result "INVENTORY_SMS_TBSP" "INFO" "SMS Tablespaces: $sms_tbsp" "Storage complexity inventory"
  log_test_result "INVENTORY_DMS_TBSP" "INFO" "DMS Tablespaces: $dms_tbsp" "Storage complexity inventory"
  log_test_result "INVENTORY_STOGROUPS" "INFO" "Storage Groups: $stogroups" "Storage complexity inventory"
  log_test_result "INVENTORY_COL_TABLES" "INFO" "Column-Organized Tables: $col_tables" "Advanced features inventory"
  log_test_result "INVENTORY_SHADOW_TABLES" "INFO" "Shadow Tables: $shadow_tables" "Advanced features inventory"
  
  # Write summary to summary file
  {
    echo "DATABASE: $database"
    echo "Generated: $timestamp"
    echo "=========================================="
    echo "BASIC OBJECTS:"
    echo "  Tables: $tables"
    echo "  Views: $views"
    echo "  Materialized Query Tables: $mqts"
    echo "  Indexes: $indexes"
    echo "  Triggers: $triggers"
    echo "  Functions: $functions"
    echo "  Procedures: $procedures"
    echo "  Methods: $methods"
    echo "  Sequences: $sequences"
    echo "  Schemas: $schemas"
    echo ""
    echo "CONSTRAINTS:"
    echo "  Check Constraints: $checks"
    echo "  Primary Keys: $pks"
    echo "  Foreign Keys: $fks"
    echo ""
    echo "SECURITY FEATURES:"
    echo "  LBAC Security Labels: $lbac_labels"
    echo "  LBAC Security Policies: $lbac_policies"
    echo "  RCAC Row-Access Tables: $rcac_row"
    echo "  RCAC Column-Access Tables: $rcac_col"
    echo "  Row Permissions: $row_permissions"
    echo "  Column Masks: $column_masks"
    echo "  Roles: $roles"
    echo "  Grants: $grants"
    echo ""
    echo "DATA COMPLEXITY:"
    echo "  Aliases: $aliases"
    echo "  Data Types: $datatypes"
    echo "  BLOB Columns: $blob_cols"
    echo "  CLOB Columns: $clob_cols"
    echo "  DBCLOB Columns: $dbclob_cols"
    echo "  XML Columns: $xml_cols"
    echo "  Inline LOB Columns: $inline_lobs"
    echo "  Inline XML Columns: $inline_xml"
    echo ""
    echo "STORAGE COMPLEXITY:"
    echo "  SMS Tablespaces: $sms_tbsp"
    echo "  DMS Tablespaces: $dms_tbsp"
    echo "  Storage Groups: $stogroups"
    echo ""
    echo "ADVANCED FEATURES:"
    echo "  Column-Organized Tables: $col_tables"
    echo "  Shadow Tables: $shadow_tables"
    echo "=========================================="
    echo ""
  } >> "$INVENTORY_SUMMARY_FILE"
  
  log_info "Inventory analysis completed for database: $database"
  
  # Generate JSON format inventory
  generate_json_inventory "$database"
}

validate_instance() {
  local instance="$1"
  
  if [ "$REMOTE_MODE" = "true" ]; then
    log_info "=========================================="
    log_info "Processing remote database: $DBNAME"
    log_info "User: $DB2USER"
    log_info "=========================================="
    log_info ""
    
    # Validate the single remote database
    validate_database "remote" "$DBNAME"
    return
  fi
  
  log_info "=========================================="
  log_info "Processing DB2 instance: $instance"
  log_info "=========================================="
  log_info ""
  
  log_info "Processing current instance: $instance"
  
  # Get databases for this instance
  local databases
  databases=$(get_databases_for_instance "$instance")
  
  if [ $? -ne 0 ] || [ -z "$databases" ]; then
    log_warning "No databases found for instance: $instance"
    return
  fi
  
  # Convert to array
  local db_array=($databases)
  log_info "Found ${#db_array[@]} database(s) in instance $instance: ${db_array[*]}"
  log_info ""
  
  # Validate each database
  for database in "${db_array[@]}"; do
    validate_database "$instance" "$database"
  done
  
  # Finalize JSON inventory if inventory mode is enabled
  if [ "$INVENTORY_MODE" = "true" ]; then
    finalize_json_inventory "$instance"
  fi
}

generate_summary_report() {
  log_info "=========================================="
  log_info "OVERALL VALIDATION SUMMARY REPORT"
  log_info "=========================================="
  log_info "Total checks performed: $TOTAL_CHECKS"
  log_success "Passed: $PASSED_CHECKS"
  log_info "Warnings: $WARNING_CHECKS"
  log_info "Failed: $FAILED_CHECKS"
  log_info "Informational: $INFO_CHECKS"
  log_info "=========================================="
  
  if [ "$FAILED_CHECKS" -gt 0 ]; then
    log_error "OVERALL MIGRATION READINESS: NOT READY"
    log_error "Please address the failed checks before proceeding with migration."
  elif [ "$WARNING_CHECKS" -gt 0 ]; then
    log_warning "OVERALL MIGRATION READINESS: REVIEW REQUIRED"
    log_warning "Please review the warnings and recommendations."
  else
    log_success "OVERALL MIGRATION READINESS: READY"
    log_success "All prerequisite checks passed successfully."
  fi
  
  if [ -n "$REPORT_FILE" ]; then
    log_info "Detailed report saved to: $REPORT_FILE"
  fi
}

select_instances() {
  if [ "$REMOTE_MODE" = "true" ]; then
    CURRENT_INSTANCE="remote"
    return
  fi
  
  # Get current instance
  local current_instance
  current_instance=$(get_current_instance)
  if [ $? -ne 0 ]; then
    log_error "Unable to determine current DB2 instance. Exiting."
    exit 1
  fi
  
  log_info "Current logged-in instance: $current_instance"
  
  # Store current instance for later use
  CURRENT_INSTANCE="$current_instance"
}

setup_report_file() {
  if [ -n "$REPORT_FILE_PATH" ]; then
    REPORT_FILE="$REPORT_FILE_PATH"
  else
    # Generate default report filename
    local timestamp
    if command -v date >/dev/null 2>&1; then
      timestamp=$(date '+%Y%m%d_%H%M%S' 2>/dev/null || date '+%Y%m%d')
    else
      timestamp="$(date | tr ' :' '__')"
    fi
    
    if [ "$REMOTE_MODE" = "true" ]; then
      REPORT_FILE="db2_migration_prereq_report_remote_${timestamp}.log"
    else
      REPORT_FILE="db2_migration_prereq_report_${timestamp}.log"
    fi
  fi
  
  # Create report file
  echo "DB2 Migration Prerequisites Validation Report" > "$REPORT_FILE"
  echo "Generated on: $(get_timestamp)" >> "$REPORT_FILE"
  echo "Platform: $PLATFORM" >> "$REPORT_FILE"
  if [ "$REMOTE_MODE" = "true" ]; then
    echo "Mode: Remote Connection" >> "$REPORT_FILE"
    echo "Database: $DBNAME" >> "$REPORT_FILE"
    echo "User: $DB2USER" >> "$REPORT_FILE"
  else
    echo "Mode: Local Connection" >> "$REPORT_FILE"
  fi
  echo "=========================================" >> "$REPORT_FILE"
  echo "" >> "$REPORT_FILE"
  
  log_info "Report will be saved to: $REPORT_FILE"
}

# Function to display usage information
show_usage() {
  echo "Usage: $0 [options]"
  echo
  echo "Options:"
  echo "  --help                 Show this help message"
  echo "  --verbose              Enable verbose output"
  echo "  --no-inventory         Exclude DB2 inventory analysis (inventory is included by default)"
  echo "  --inventory            Force enable DB2 inventory analysis (default behavior)"
  echo "  --report-file PATH     Specify custom report file path"
  echo
  echo "Environment Variables:"
  echo "  For remote DB2 connection (all three required):"
  echo "    DB2USER              Username for remote DB2 connection"
  echo "    DB2PASSWORD          Password for remote DB2 connection"
  echo "    DBNAME               Database name for remote connection"
  echo
  echo "  For non-interactive mode:"
  echo "    DB2_INSTANCES        Comma-separated list of DB2 instances to validate"
  echo "    INVENTORY            Set to 'false' to disable inventory analysis (enabled by default)"
  echo "    REPORT_FILE_PATH     Custom path for the validation report file"
  echo
  echo "Examples:"
  echo "  Interactive mode (local):"
  echo "    $0"
  echo
  echo "  Remote mode:"
  echo "    export DB2USER=myuser"
  echo "    export DB2PASSWORD=mypassword"
  echo "    export DBNAME=mydatabase"
  echo "    $0"
  echo
  echo "  Non-interactive mode (local):"
  echo "    DB2_INSTANCES=db2inst1,db2inst2 $0"
  echo
  echo "  With custom report file:"
  echo "    REPORT_FILE_PATH=/tmp/my_report.log $0"
  echo
  echo "  Verbose mode:"
  echo "    $0 --verbose"
  echo
  echo "Supported Platforms:"
  echo "  - AIX"
  echo "  - Linux on x86_64"
  echo "  - Linux on POWER (ppc64/ppc64le)"
  echo
  echo "Prerequisites:"
  echo "  Local mode:"
  echo "    - DB2 instance(s) must be running and accessible"
  echo "    - User must have SYSADM or SYSMAINT authority"
  echo "    - DB2 environment must be properly sourced"
  echo "    - Script should be run as the DB2 instance user (not root/sudo)"
  echo
  echo "  Remote mode:"
  echo "    - DB2 client must be installed and configured"
  echo "    - Network connectivity to remote DB2 server"
  echo "    - Valid DB2 user credentials with appropriate privileges such as DBADM or SYSMAINT"
  echo "    - Database must be cataloged or DSN entries available in db2dsdriver.cfg file"
  echo
  echo "Validation Checks:"
  echo "  - DB2 version information (db2level and SYSIBM.SYSVERSIONS)"
  echo "  - DB2 update level validation (db2updv115)"
  echo "  - In-doubt transactions check"
  echo "  - Invalid objects validation"
  echo "  - Tablespace state verification"
  echo "  - Non-fenced routines detection"
  echo "  - Java stored procedures check"
  echo "  - AutoStorage configuration check"
  echo "  - Database configuration validation"
  echo "  - Federation compatibility check"
}

# Parse command line arguments
parse_args() {
  # Check for INVENTORY environment variable (can override default)
  if [ "$INVENTORY" = "false" ]; then
    INVENTORY_MODE=false
  elif [ "$INVENTORY" = "true" ]; then
    INVENTORY_MODE=true
  fi
  
  while [ $# -gt 0 ]; do
    case "$1" in
      --help)
        show_usage
        HELP_REQUESTED=true
        return 0
        ;;
      --verbose)
        VERBOSE=true
        shift
        ;;
      --inventory)
        INVENTORY_MODE=true
        shift
        ;;
      --no-inventory)
        INVENTORY_MODE=false
        shift
        ;;
      --report-file)
        if [ -n "$2" ]; then
          REPORT_FILE_PATH="$2"
          shift 2
        else
          log_error "--report-file requires a path argument"
          return 1
        fi
        ;;
      *)
        log_error "Unknown option: $1"
        show_usage
        return 1
        ;;
    esac
  done
}

# Setup inventory files
setup_inventory_files() {
  local timestamp=$(date '+%Y%m%d_%H%M%S')
  if [ "$REMOTE_MODE" = "true" ]; then
    INVENTORY_DETAIL_FILE="db2_inventory_detail_remote_${timestamp}.txt"
    INVENTORY_SUMMARY_FILE="db2_inventory_summary_remote_${timestamp}.txt"
    INVENTORY_JSON_FILE="db2_inventory_remote_${timestamp}.json"
  else
    INVENTORY_DETAIL_FILE="db2_inventory_detail_${timestamp}.txt"
    INVENTORY_SUMMARY_FILE="db2_inventory_summary_${timestamp}.txt"
    INVENTORY_JSON_FILE="db2_inventory_${timestamp}.json"
  fi
  
  log_info "Inventory detail file: $INVENTORY_DETAIL_FILE"
  log_info "Inventory summary file: $INVENTORY_SUMMARY_FILE"
  log_info "Inventory JSON file: $INVENTORY_JSON_FILE"
}

# Main function
main() {
  parse_args "$@"
  local parse_result=$?
  
  # If help was requested, exit early
  if [ "$HELP_REQUESTED" = "true" ]; then
    return 0
  fi
  
  # If parse_args returned non-zero (error), exit with error
  if [ $parse_result -ne 0 ]; then
    return $parse_result
  fi
  
  # Display mode information
  if [ "$REMOTE_MODE" = "true" ]; then
    if [ "$INVENTORY_MODE" = "true" ]; then
      log_info "=== DB2 Migration Prerequisites Validation Tool (Remote Mode + Inventory) ==="
    else
      log_info "=== DB2 Migration Prerequisites Validation Tool (Remote Mode) ==="
    fi
    log_info "Connecting to database: $DBNAME as user: $DB2USER"
  elif [ "$INTERACTIVE_MODE" = "true" ]; then
    if [ "$INVENTORY_MODE" = "true" ]; then
      log_info "=== DB2 Migration Prerequisites Validation Tool (Interactive Mode + Inventory) ==="
    else
      log_info "=== DB2 Migration Prerequisites Validation Tool (Interactive Mode) ==="
    fi
  else
    if [ "$INVENTORY_MODE" = "true" ]; then
      log_info "=== DB2 Migration Prerequisites Validation Tool (Non-Interactive Mode + Inventory) ==="
    else
      log_info "=== DB2 Migration Prerequisites Validation Tool (Non-Interactive Mode) ==="
    fi
  fi
  
  # Detect platform
  detect_platform
  
  # Check DB2 environment
  check_db2_environment
  
  # Setup report file
  setup_report_file
  
  # Setup inventory files if inventory mode is enabled
  if [ "$INVENTORY_MODE" = "true" ]; then
    setup_inventory_files
  fi
  
  # Get available DB2 instances (skip in remote mode)
  if [ "$REMOTE_MODE" = "false" ]; then
    get_db2_instances
  fi
  
  # Select instances to validate
  select_instances
  
  # Validate selected instances
  validate_instance "$CURRENT_INSTANCE"
  
  # Add spacing before overall summary
  log_info ""
  log_info ""
  
  # Generate summary report
  generate_summary_report
  
  # Show inventory file locations if inventory was performed
  if [ "$INVENTORY_MODE" = "true" ]; then
    log_info ""
    log_info "=========================================="
    log_info "INVENTORY FILES GENERATED"
    log_info "=========================================="
    log_info "Inventory summary: $INVENTORY_SUMMARY_FILE"
    log_info "Inventory details: $INVENTORY_DETAIL_FILE"
    log_info "Inventory JSON: $INVENTORY_JSON_FILE"
    log_info "=========================================="
  fi
  
  # Exit with appropriate code
  if [ "$FAILED_CHECKS" -gt 0 ]; then
    return 1
  else
    return 0
  fi
}

# Function to show download information (called at the very end)
show_download_info() {
  if [ "$SCRIPT_WAS_DOWNLOADED" = "true" ]; then
    local script_name="db2_migration_prereq_check.sh"
    local script_path="${DOWNLOADED_SCRIPT_PATH:-$(pwd)/$script_name}"
    
    echo ""
    echo "=============================================================================="
    echo "SCRIPT DOWNLOAD INFORMATION"
    echo "=============================================================================="
    echo "The validation script has been downloaded and saved as:"
    echo "  $script_path"
    echo ""
    echo "You can run it again anytime with:"
    echo "  ./$script_name"
    echo ""
    echo "For different options:"
    echo "  ./$script_name --help                    # Show all options"
    echo "  ./$script_name --no-inventory            # Skip inventory analysis"
    echo "  ./$script_name --verbose                 # Enable verbose output"
    echo "  INVENTORY=false ./$script_name           # Disable inventory via environment"
    echo ""
    echo "For remote connections:"
    echo "  export DB2USER=username"
    echo "  export DB2PASSWORD=password"
    echo "  export DBNAME=<remote catalogued database name or DSN entry in db2dsdriver.cfg file>"
    echo "  ./$script_name"
    echo "=============================================================================="
  fi
}

# Run the main function and handle download info
main "$@"
EXIT_CODE=$?

# Show download information if script was downloaded
show_download_info

# Exit with the same code as main function
exit $EXIT_CODE