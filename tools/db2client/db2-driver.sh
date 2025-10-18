#!/usr/bin/env bash

# =============================================================================
# AWS RDS DB2 Client Setup - Unified Cross-Platform Script
# =============================================================================
# 🚀 One-liner execution: curl -sL https://bit.ly/getdb2driver | bash
# =============================================================================

# =============================================================================
# Cross-Platform Shell Compatibility Check
# =============================================================================
# Ensure script runs with bash even if default shell is zsh (macOS) or other shells

# Check if we're running with bash
if [ -z "$BASH_VERSION" ]; then
  # Try to re-execute with bash if available
  if command -v bash >/dev/null 2>&1; then
    echo "Re-executing with bash for compatibility..." >&2
    exec bash "$0" "$@"
  else
    echo "Error: This script requires bash, but bash is not available." >&2
    echo "Please install bash or run with: bash $0 $*" >&2
    exit 1
  fi
fi

detect_platform() {  
  # Detect platform for platform-specific behaviors
  PLATFORM="unknown"
  DISTRO=""
  DISTRO_VERSION=""
  
  case "$(uname -s)" in
  Linux*)
    PLATFORM="linux"
    # Check for specific Linux distributions
    if [ -f /etc/os-release ]; then
      . /etc/os-release
      DISTRO=$ID
      DISTRO_VERSION=$VERSION_ID
      log_debug "Detected Linux distribution: $DISTRO $DISTRO_VERSION"
      
      # Set distribution family for package management
      if [[ "$DISTRO" == "ubuntu" || "$DISTRO" == "debian" || "$DISTRO" == "linuxmint" ]]; then
        DISTRO_FAMILY="debian"
      elif [[ "$DISTRO" == "rhel" || "$DISTRO" == "centos" || "$DISTRO" == "fedora" || "$DISTRO" == "amzn" ]]; then
        DISTRO_FAMILY="redhat"
      elif [[ "$DISTRO" == "sles" || "$DISTRO" == "opensuse-leap" ]]; then
        DISTRO_FAMILY="suse"
      elif [[ "$DISTRO" == "arch" || "$DISTRO" == "manjaro" ]]; then
        DISTRO_FAMILY="arch"
      else
        DISTRO_FAMILY="unknown"
      fi
      log_debug "Distribution family: $DISTRO_FAMILY"
    else
      # Fallback detection for older systems
      if [ -f /etc/redhat-release ]; then
        DISTRO="rhel"
        DISTRO_FAMILY="redhat"
      elif [ -f /etc/debian_version ]; then
        DISTRO="debian"
        DISTRO_FAMILY="debian"
      elif [ -f /etc/SuSE-release ]; then
        DISTRO="suse"
        DISTRO_FAMILY="suse"
      fi
      log_debug "Fallback detection: $DISTRO (family: $DISTRO_FAMILY)"
    fi
    
    # Check if running in WSL
    if grep -qi microsoft /proc/version 2>/dev/null; then
      PLATFORM="wsl"
      log_debug "Detected Windows Subsystem for Linux"
    fi
    ;;
  Darwin*)
    PLATFORM="macos"
    ;;
  CYGWIN* | MINGW* | MSYS*)
    PLATFORM="windows"
    ;;
  esac
  
  log_debug "Platform detection complete: $PLATFORM"
}

# =============================================================================
# DB2 Client Setup Script
# =============================================================================
# This script provides setup of AWS RDS DB2 clients
# and creates DSN entries for connecting to RDS DB2 instances.
# Supports both DS Driver (thin client) and RT Client (runtime client).
#
# Version: 1.0
# Platform Support: Linux (Amazon Linux, CentOS, RHEL, etc.)
# Download: curl -sL https://bit.ly/db2client | bash
# =============================================================================

set -eo pipefail

# Default values and configuration
PROFILE=${PROFILE:-"default"}
REGION=${REGION:-""}
VERBOSE=${VERBOSE:-false}
CHECK_PERMISSIONS=${CHECK_PERMISSIONS:-false}
INTERACTIVE_MODE=${INTERACTIVE_MODE:-true}
CLIENT_TYPE=${CLIENT_TYPE:-"RT"}  # Default to Runtime Driver (thick client)
DB2USER_NAME=${DB2USER_NAME:-"db2inst1"}  # Default DB2 user name
DSN_CHECK_FILE=".install-dsn-entries"

# Initialize other variables that might be referenced
AWS_REGION=${AWS_REGION:-""}
DB_INSTANCE_ID=${DB_INSTANCE_ID:-"ALL"}

# Array to store DB instances when processing multiple instances
declare -a DB_INSTANCES=()

# Array to store database names for each instance
declare -a DB_NAMES=()

# Arrays to store master user names and passwords for each instance
declare -a MASTER_USER_NAMES=()
declare -a MASTER_USER_PASSWORDS=()

# S3 URIs for resources - will be set based on CLIENT_TYPE
S3_BUCKET_URI=""
SCRIPT_URI=""
EXFMT_URI="s3://aws-blogs-artifacts-public/artifacts/DBBLOG-4900/db2exfmt"
FUNCTION_URI="s3://aws-blogs-artifacts-public/artifacts/DBBLOG-4900/functions.sh"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
PURPLE='\033[0;35m'
NC='\033[0m' # No Color

# Declare a global array to collect all help commands.
declare -a HELP_COMMANDS=()

# Detect if script is being run via curl pipe (one-liner execution)
CURL_PIPE_EXECUTION=false

# Timing
start_time=$(date +%s)

# =============================================================================
# Platform Check
# =============================================================================

check_supported_platform() {
  log_info "Checking platform compatibility..."
  
  case "$PLATFORM" in
    "linux")
      log_success "Detected supported platform: Linux"
      return 0
      ;;
    "wsl" | "cygwin" | "mingw" | "msys" | "windows")
      log_error "Db2 clients are supported for Windows but this script is applicable only for Linux platforms."
      log_info "Use either an AWS CloudShell with VPC enabled or an EC2 instance in same VPC with RDS for Db2 instances."
      log_info "You can also use a Linux machine in on-prenises environment having direct connectivity to RDS for Db2 instances."
      return 1
      ;;
    "macos")
      log_error "Db2 clients are supported for Mac but this script is applicable only for Linux platforms."
      log_info "Use either an AWS CloudShell with VPC enabled or an EC2 instance in same VPC with RDS for Db2 instances."
      log_info "You can also use a Linux machine in on-prenises environment having direct connectivity to RDS for Db2 instances."
      return 1
      ;;
    *)
      log_error "Unknown platform: $PLATFORM"
      log_error "This script only supports Linux platforms (Amazon Linux, CentOS, RHEL, etc.)"
      log_error "Use a supported Linux distribution"
      return 1
      ;;
  esac
}

# =============================================================================
# Curl Pipe Detection
# =============================================================================

detect_curl_pipe_execution() {
  # Handle case where BASH_SOURCE might not be set or accessible
  local source_path="${BASH_SOURCE[0]:-}"

  # Multiple ways to detect curl pipe execution
  if [[ -z "$source_path" ]] || \
     [[ "$source_path" == "/dev/fd/"* ]] || \
     [[ "$source_path" == "/proc/self/fd/"* ]] || \
     [[ "$source_path" == "/dev/stdin" ]] || \
     [[ ! -f "$source_path" ]] || \
     [[ ! -t 0 ]]; then
    CURL_PIPE_EXECUTION=true
    log_debug "Detected execution via curl pipe (one-liner)"
    log_debug "Source path: '$source_path'"
    log_debug "Environment variables: CLIENT_TYPE='$CLIENT_TYPE', DB_INSTANCE_ID='$DB_INSTANCE_ID', INTERACTIVE_MODE='$INTERACTIVE_MODE'"
    
    # Check if environment variables are provided for non-interactive execution
    if [[ -n "$CLIENT_TYPE" ]] || [[ -n "$DB_INSTANCE_ID" ]] || [[ "$INTERACTIVE_MODE" == "false" ]]; then
      log_info "Curl pipe execution with environment variables detected - proceeding with non-interactive execution"
      # Set non-interactive mode for curl pipe execution with env vars
      INTERACTIVE_MODE="false"
      log_debug "Set INTERACTIVE_MODE to false for curl pipe execution"
      return 0
    else
      # No environment variables provided, download script and provide instructions
      log_info "Curl pipe execution without environment variables - downloading script for interactive use"
      handle_curl_pipe_download
      exit 0
    fi
  fi
}

handle_curl_pipe_download() {
  echo
  echo "============================================================================="
  echo "                    DB2 CLIENT - SCRIPT DOWNLOAD"
  echo "============================================================================="
  echo
  log_info "Curl pipe execution detected - downloading script for interactive use"
  
  # Script URL and target file configuration
  local script_url="https://bit.ly/db2client"
  local script_name="db2client.sh"
  local script_path=""
  
  # Determine the best location to save the script - prioritize current directory
  if [[ -w "." ]]; then
    script_path="./$script_name"
  elif [[ -w "$HOME" ]]; then
    script_path="$HOME/$script_name"
  elif [[ -w "/tmp" ]]; then
    script_path="/tmp/$script_name"
  else
    script_path="./$script_name"
  fi
  
  log_info "Downloading script from: $script_url"
  log_info "Saving to: $script_path"
  
  # Download the script using curl
  if curl -sL "$script_url" -o "$script_path"; then
    log_success "Script downloaded successfully: $script_path"
    
    # Make executable on Unix-like systems
    if [[ "$PLATFORM" == "macos" ]] || [[ "$PLATFORM" == "linux" ]] || [[ "$PLATFORM" == "wsl" ]]; then
      if chmod +x "$script_path"; then
        log_success "Script made executable"
      else
        log_warning "Could not make script executable, but you can still run it with 'bash $script_path'"
      fi
    fi
  else
    log_error "Failed to download script from $script_url"
    return 1
  fi
  
  echo
  echo "============================================================================="
  echo "                        PLATFORM REQUIREMENTS"
  echo "============================================================================="
  echo
  echo "⚠️  IMPORTANT: This script only supports Linux platforms."
  echo "   It is NOT compatible with:"
  echo "   - macOS"
  echo "   - Windows"
  echo "   - Windows Subsystem for Linux (WSL)"
  echo
  echo "   For Windows, please download the DB2 client directly from IBM."
  echo
  echo "============================================================================="
  echo
  echo "🐧 Linux Instructions:"
  echo
  echo "The script has been downloaded to the current directory and made executable."
  echo "You can run it in two ways:"
  echo
  echo "Method 1 - Direct execution (recommended):"
  echo "  ./$(basename "$script_path")"
  echo
  echo "Method 2 - Source the script:"
  echo "  source $(basename "$script_path")"
  echo
  echo "With environment variables:"
  echo "  CLIENT_TYPE=DS DB_INSTANCE_ID=mydb ./$(basename "$script_path")"
  echo "  CLIENT_TYPE=RT DB_INSTANCE_ID=mydb ./$(basename "$script_path")"
  echo
  echo "============================================================================="
  
  echo
  echo "============================================================================="
  echo "                           QUICK START EXAMPLES"
  echo "============================================================================="
  echo
  echo "🚀 Most common usage patterns:"
  echo
  echo "1. Install DB2 DS Driver (thin client):"
  echo "   CLIENT_TYPE=DS ./$(basename "$script_path")"
  echo
  echo "2. Install DB2 Runtime Client:"
  echo "   CLIENT_TYPE=RT ./$(basename "$script_path")"
  echo
  echo "3. Install with specific instance:"
  echo "   CLIENT_TYPE=DS DB_INSTANCE_ID=mydb ./$(basename "$script_path")"
  echo
  echo
  echo "5. Non-interactive installation:"
  echo "   CLIENT_TYPE=DS DB_INSTANCE_ID=mydb INTERACTIVE_MODE=false ./$(basename "$script_path")"
  echo
  echo "6. Verbose installation with custom region:"
  echo "   CLIENT_TYPE=RT DB_INSTANCE_ID=mydb REGION=us-west-2 VERBOSE=true ./$(basename "$script_path")"
  echo
  echo "============================================================================="
  echo "                           ENVIRONMENT VARIABLES"
  echo "============================================================================="
  echo
  echo "Set these to avoid interactive prompts:"
  echo
  echo "• CLIENT_TYPE=DS|RT               Client type (DS=Driver, RT=Runtime)"
  echo "• DB2USER_NAME=your-db2-username   DB2 user name (default: db2inst1)"
  echo "• DB_INSTANCE_ID=your-db-instance-id"
  echo "• REGION=us-west-2"
  echo "• PROFILE=your-aws-profile"
  echo "• VERBOSE=true|false"
  echo "• INTERACTIVE_MODE=true|false"
  echo
  echo "============================================================================="
  
  echo
  echo "🎉 Setup complete! The script is ready to use."
  echo "   Run the commands above to get started with DB2 client setup."
  echo
  echo "============================================================================="
  echo "                     💡 CURL PIPE EXECUTION MODES"
  echo "============================================================================="
  echo
  echo "You have two ways to use curl pipe execution:"
  echo
  echo "1️⃣  DOWNLOAD MODE (what just happened):"
  echo "   curl -sL https://bit.ly/db2client | bash"
  echo "   → Downloads script for interactive use"
  echo
  echo "2️⃣  NON-INTERACTIVE MODE (direct execution):"
  echo "   curl -sL https://bit.ly/db2client | CLIENT_TYPE=DS DB_INSTANCE_ID=your-instance bash"
  echo "   → Runs immediately without downloading"
  echo
  echo "Examples of non-interactive mode:"
  echo "   curl -sL https://bit.ly/db2client | CLIENT_TYPE=DS DB_INSTANCE_ID=mydb bash"
  echo "   curl -sL https://bit.ly/db2client | CLIENT_TYPE=RT DB_INSTANCE_ID=mydb VERBOSE=true bash"
  echo
  echo "Environment variables must be placed AFTER the pipe (|) and BEFORE bash"
  echo "============================================================================="
  echo
}

# =============================================================================
# Logging Functions
# =============================================================================

log_info() {
  echo -e "${BLUE}[   INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
}

log_success() {
  echo -e "${GREEN}[SUCCESS]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
}

log_warning() {
  echo -e "${YELLOW}[WARNING]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
}

log_error() {
  echo -e "${RED}[  ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
}

log_debug() {
  if [[ "$VERBOSE" == "true" ]]; then
    echo -e "${CYAN}[ DEBUG ]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
  fi
}

# =============================================================================
# Permission and IAM Functions
# =============================================================================

detect_current_iam_identity() {
  set_credentials

  log_info "Detecting current AWS identity..."

  local arn account_id user_id role_name source_type
  arn=$(aws sts get-caller-identity --region "$REGION" --query "Arn" --output text 2>/dev/null)
  account_id=$(aws sts get-caller-identity  --region "$REGION" --query "Account" --output text 2>/dev/null)
  user_id=$(aws sts get-caller-identity --region "$REGION" --query "UserId" --output text 2>/dev/null)
  if [[ -z "$arn" || -z "$account_id" ]]; then
    log_error "Failed to retrieve AWS identity. Are credentials configured?"
    return 1
  fi
  
  if [[ "$arn" == arn:aws:sts::*:assumed-role/*/* ]]; then
    role_name=$(echo "$arn" | cut -d'/' -f2)
    arn="arn:aws:iam::$account_id:role/$role_name"
  fi

  if [[ "$arn" == *":user/"* ]]; then
    source_type="IAM User"
  elif [[ "$arn" == *":assumed-role/"* ]]; then
    role_name=$(echo "$arn" | cut -d'/' -f2)
    source_type="Assumed Role ($role_name)"
  elif [[ "$arn" == *":role/"* ]]; then
    role_name=$(echo "$arn" | cut -d'/' -f2)
    source_type="IAM Role ($role_name)"
  else
    source_type="Unknown Type"
  fi

  log_info "AWS Identity Detected:"
  echo " - ARN        : $arn"
  echo " - Account ID : $account_id"
  echo " - User ID    : $user_id"
  echo " - Source     : $source_type"

  cat <<EOF >identity_report.txt
# AWS Identity Summary

ARN         : $arn
Account ID  : $account_id
User ID     : $user_id
Source Type : $source_type

Date        : $(date)

This identity was used to run the DB2 client setup script. Missing permissions (if any) will be listed separately.
EOF

  export CURRENT_IAM_ARN="$arn"
  export CURRENT_ACCOUNT_ID="$account_id"
  return 0
}

check_permissions() {
  if [[ "$CHECK_PERMISSIONS" != "true" ]]; then
    log_info "Skipping permission checks (--check-permissions not specified)"
    return 0
  fi
  
  # Check required permissions
  if ! check_missing_permissions; then
    log_error "Insufficient permissions to proceed with DB2 client setup."
    log_info "Options to resolve this:"
    echo "1. Send the generated files to your AWS administrator:"
    echo "   - missing_permissions.tf (if generated)"
    echo "   - missing_permissions_iam_policy.json (if generated)"
    echo "   - identity_report.txt"
    echo
    echo "2. Run this script again without permission checks: $0 (without --check-permissions)"
    echo
    echo "3. If you don't have iam:SimulatePrincipalPolicy permission, ensure you have:"
    echo "   - S3 permissions (s3:GetObject)"
    echo "   - EC2 permissions (ec2:DescribeVpcs, ec2:DescribeSubnets)"
    echo "   - RDS permissions (rds:DescribeDBInstances)"
    echo "   - Secrets Manager permissions (secretsmanager:GetSecretValue)"
    return 1
  else
    log_info "Permission check is complete. Rerun the program without --check-permissions to proceed if no issues are found related to permissions."
  fi
  return 0
}

check_missing_permissions() {
  detect_current_iam_identity || return 1
  
  log_info "Checking for required IAM permissions for DB2 client setup..."

  local required_actions=(
    # STS Operations
    "sts:GetCallerIdentity"
    # IAM Operations
    "iam:SimulatePrincipalPolicy"
    # Secrets Manager Operations
    "secretsmanager:GetSecretValue"
    "secretsmanager:DescribeSecret"
    # RDS Operations
    "rds:DescribeDBInstances"
    "rds:DescribeDBParameters"
    # EC2 Operations (for VPC endpoints and networking)
    "ec2:DescribeVpcs"
    "ec2:DescribeSubnets"
    "ec2:DescribeSecurityGroups"
  )

  # Check if iam:SimulatePrincipalPolicy is available first
  local simulate_available=false
  if aws iam simulate-principal-policy \
    --policy-source-arn "$CURRENT_IAM_ARN" \
    --region "$REGION" \
    --action-names "iam:SimulatePrincipalPolicy" \
    --output text \
    --query 'EvaluationResults[0].EvalDecision' 2>/dev/null | grep -q "allowed"; then
    simulate_available=true
    log_info "Permission simulation available - checking individual permissions..."
  else
    log_warning "iam:SimulatePrincipalPolicy not available - performing basic checks..."
  fi

  if [ "$simulate_available" = true ]; then
    log_info "Checking for missing permissions..."
    local missing_actions=()
    for action in "${required_actions[@]}"; do
      local result
      result=$(aws iam simulate-principal-policy \
        --policy-source-arn "$CURRENT_IAM_ARN" \
        --region "$REGION" \
        --action-names "$action" \
        --output text \
        --query 'EvaluationResults[0].EvalDecision' 2>/dev/null)
      log_debug " - $action: $result"
      if [[ "$result" != "allowed" ]]; then
        missing_actions+=("$action")
      fi
    done
    
    echo "============================================================================"
    if [ ${#missing_actions[@]} -eq 0 ]; then
      log_success "All required permissions are present."
      return 0
    fi

    log_warning "Missing permissions detected."
    log_info "Generating policy files and identity report..."

    local tf_file="missing_permissions.tf"
    local json_file="missing_permissions_iam_policy.json"

    # === Terraform Policy File ===
    cat <<EOF >"$tf_file"
# Terraform Policy: $tf_file
# Modify the "Resource" value to scope down to specific ARNs when possible.

resource "aws_iam_policy" "db2client_minimal" {
  name        = "db2client-minimal-policy"
  description = "Minimum permissions for DB2 client setup script"
  policy      = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow",
        Action   = [
EOF

    for action in "${missing_actions[@]}"; do
      echo "          \"$action\"," >>"$tf_file"
    done | sed '$s/,$//'

    cat <<EOF >>"$tf_file"
        ],
        Resource = "*"
      }
    ]
  })
}
EOF

    # === JSON IAM Policy File ===
    cat <<EOF >"$json_file"
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
EOF

    for action in "${missing_actions[@]}"; do
      echo "      \"$action\"," >>"$json_file"
    done | sed '$s/,$//'

    cat <<EOF >>"$json_file"
    ],
    "Resource": "*"
  }]
}
EOF

    echo
    log_info "Output files generated:"
    echo " - Terraform:           $tf_file"
    echo " - IAM JSON policy:     $json_file"
    echo " - Identity report:     identity_report.txt"
    echo
    log_warning "Share these with your AWS administrator."
    log_warning "Encourage scoping down 'Resource' values for least privilege."
    log_warning "Please contact your administrator to grant these permissions before proceeding."
    echo "============================================================================"
    return 1
  else
    # Fallback to basic permission checks
    log_info "Performing basic permission checks..."

    # Test S3 access
    if ! aws s3 ls --profile "$PROFILE" --region "$REGION" &>/dev/null; then
      log_error "The IAM role lacks permission to use S3 APIs."
      log_error "Required: s3:ListBucket and related S3 permissions."
      return 1
    fi

    # Test Secrets Manager access
    if ! aws secretsmanager list-secrets --profile "$PROFILE" --region "$REGION" --max-results 1 &>/dev/null; then
      log_error "The IAM role lacks permission to use Secrets Manager APIs."
      log_error "Required: secretsmanager:ListSecrets and related Secrets Manager permissions."
      return 1
    fi

    # Test basic EC2 access
    if ! aws ec2 describe-vpcs --profile "$PROFILE" --region "$REGION" --max-items 1 &>/dev/null; then
      log_error "The IAM role lacks permission to describe VPCs."
      log_error "Required: ec2:DescribeVpcs and related EC2 permissions."
      return 1
    fi

    # Test RDS access
    if ! aws rds describe-db-instances --profile "$PROFILE" --region "$REGION" --max-items 1 &>/dev/null; then
      log_error "The IAM role lacks permission to describe RDS instances."
      log_error "Required: rds:DescribeDBInstances permission."
      return 1
    fi

    # Test RDS db parameters access
    if ! aws rds describe-db-describe-db-parameters --db-parameter-group-name test --profile "$PROFILE" --region "$REGION" --max-items 1 &>/dev/null; then
      log_error "The IAM role lacks permission to describe RDS DB parameters."
      log_error "Required: rds:DescribeDBParameters permission."
      return 1
    fi

    log_success "Basic permission checks passed."
    log_warning "Note: Detailed permission analysis not available without iam:SimulatePrincipalPolicy."
    return 0
  fi
}

getInput() {
  # if env var var_name exists, it will take that value otherwise it will ask.
  # Make sure that the var_name do not conflict with other names in the script
  local var_name=$1
  local default_value=$2
  local prompt_message=$3

  # Use indirect expansion to get the value of the variable, with safe fallback
  local var_value=""
  if [[ -n "${!var_name:-}" ]]; then
    var_value="${!var_name}"
  fi

  if [ -n "${var_value}" ]; then
    # If set, use its value
    echo "${var_value}"
  elif [[ "$INTERACTIVE_MODE" == "false" ]]; then
    # Non-interactive mode, use default or fail
    if [ -n "$default_value" ]; then
      echo "$default_value"
    else
      log_error "Non-interactive mode: Required parameter $var_name not set"
      exit 1
    fi
  else
    # Interactive mode
    if [ -z "$default_value" ]; then
      read -p "$prompt_message: " user_input
      echo "${user_input}"
    else
      read -p "$prompt_message (default $default_value): " user_input
      echo "${user_input:-$default_value}"
    fi
  fi
}

report_elapsed_time() {
  local end_time=$(date +%s)
  local elapsed_time=$((end_time - start_time))

  # Calculate hours, minutes, and seconds
  local hours=$((elapsed_time / 3600))
  local minutes=$(((elapsed_time % 3600) / 60))
  local seconds=$((elapsed_time % 60))
  
  log_info "Elapsed time: ${hours}h ${minutes}m ${seconds}s"
  start_time=$(date +%s) # Reset for next operation
}
# =============================================================================
# Client Type Selection
# =============================================================================

set_client_type() {
  # Validate CLIENT_TYPE
  if [[ "$CLIENT_TYPE" != "DS" && "$CLIENT_TYPE" != "RT" ]]; then
    if [[ "$INTERACTIVE_MODE" == "true" ]]; then
      log_warning "Invalid CLIENT_TYPE: $CLIENT_TYPE. Must be 'DS' or 'RT'."
      
      local choice=0
      while [ "$choice" -lt 1 ] || [ "$choice" -gt 2 ]; do
        echo "=============================================================================" >&2
        echo "Please select DB2 client type to install:" >&2
        echo "1) DS Driver (thin client) (default)" >&2
        echo "2) Runtime Client" >&2
        read -p "Enter your choice (1 or 2): " user_input
        choice=${user_input:-1}
        if [ "$choice" -eq 1 ]; then
          CLIENT_TYPE="DS"
        elif [ "$choice" -eq 2 ]; then
          CLIENT_TYPE="RT"
        else
          echo "Invalid choice. Please enter 1 or 2." >&2
          choice=0 # Reset choice to continue the loop
        fi
      done
    else
      log_warning "Invalid CLIENT_TYPE: $CLIENT_TYPE. Defaulting to 'DS' (Driver)."
      CLIENT_TYPE="DS"
    fi
  fi
  
  # Set S3 URIs based on client type
  if [[ "$CLIENT_TYPE" == "DS" ]]; then
    S3_BUCKET_URI="s3://aws-blogs-artifacts-public/artifacts/DBBLOG-4900/v11.5.9_linuxx64_dsdriver.tar.gz"
    SCRIPT_URI="s3://aws-blogs-artifacts-public/artifacts/DBBLOG-4900/db2-driver.sh"
    log_info "Selected DB2 DS Driver (thin client)"
  else
    S3_BUCKET_URI="s3://aws-blogs-artifacts-public/artifacts/DBBLOG-4900/v11.5.9_linuxx64_rtcl.tar"
    SCRIPT_URI="s3://aws-blogs-artifacts-public/artifacts/DBBLOG-4900/db2-driver.sh"
    log_info "Selected DB2 Runtime Client"
  fi
}

# =============================================================================
# Prerequisite Checks
# =============================================================================

prereq_checks() {
  log_info "Checking prerequisites on $PLATFORM platform..."

  local REQUIRED_MAJOR=2
  local REQUIRED_MINOR=27
  
  # Check if running with sudo privileges
  if ! sudo -n true 2>/dev/null; then
    log_error "This user does NOT have sudo privileges."
    return 1
  fi  
  
  # Detect package manager
  PKG_MANAGER=""
  if command -v apt-get &>/dev/null; then
    PKG_MANAGER="apt"
    log_debug "Detected apt package manager (Debian/Ubuntu)"
  elif command -v yum &>/dev/null; then
    PKG_MANAGER="yum"
    log_debug "Detected yum package manager (RHEL/CentOS/Amazon Linux)"
  elif command -v dnf &>/dev/null; then
    PKG_MANAGER="dnf"
    log_debug "Detected dnf package manager (Fedora/newer RHEL)"
  elif command -v zypper &>/dev/null; then
    PKG_MANAGER="zypper"
    log_debug "Detected zypper package manager (SUSE)"
  elif command -v pacman &>/dev/null; then
    PKG_MANAGER="pacman"
    log_debug "Detected pacman package manager (Arch Linux)"
  else
    log_warning "Could not detect package manager. You may need to install dependencies manually."
    PKG_MANAGER="unknown"
  fi
  
  # Check for jq
  if ! command -v jq &>/dev/null; then
    log_info "jq is required but not installed. Installing..."
    case "$PKG_MANAGER" in
      apt)
        sudo apt-get update -qq && sudo apt-get install -y jq 2>&1 >/dev/null
        ;;
      yum)
        sudo yum install jq -y 2>&1 >/dev/null
        ;;
      dnf)
        sudo dnf install jq -y 2>&1 >/dev/null
        ;;
      zypper)
        sudo zypper --non-interactive install jq 2>&1 >/dev/null
        ;;
      pacman)
        sudo pacman -S --noconfirm jq 2>&1 >/dev/null
        ;;
      *)
        log_error "Unsupported package manager. Please install jq manually."
        return 1
        ;;
    esac
    
    if [ $? -ne 0 ]; then
      log_error "Error installing jq. Exiting ...."
      return 1
    fi
  fi
  
  # Check for Java
  if ! command -v java &>/dev/null; then
    log_info "java is required but not installed. Installing..."
    case "$PKG_MANAGER" in
      apt)
        sudo apt-get update -qq && sudo apt-get install -y default-jre 2>&1 >/dev/null
        ;;
      yum)
        sudo yum install java -y 2>&1 >/dev/null
        ;;
      dnf)
        sudo dnf install java-latest-openjdk -y 2>&1 >/dev/null
        ;;
      zypper)
        sudo zypper --non-interactive install java-11-openjdk 2>&1 >/dev/null
        ;;
      pacman)
        sudo pacman -S --noconfirm jre-openjdk 2>&1 >/dev/null
        ;;
      *)
        log_error "Unsupported package manager. Please install Java manually."
        return 1
        ;;
    esac
    
    if [ $? -ne 0 ]; then
      log_error "Error installing java. Exiting ...."
      return 1
    fi
  fi
  
  # Check for javac
  # if ! command -v javac &>/dev/null; then
  #   log_info "javac is required but not installed. Installing..."
  #   case "$PKG_MANAGER" in
  #     apt)
  #       sudo apt-get update -qq && sudo apt-get install -y default-jdk 2>&1 >/dev/null
  #       ;;
  #     yum)
  #       sudo yum install java-devel -y 2>&1 >/dev/null
  #       ;;
  #     dnf)
  #       sudo dnf install java-latest-openjdk-devel -y 2>&1 >/dev/null
  #       ;;
  #     zypper)
  #       sudo zypper --non-interactive install java-11-openjdk-devel 2>&1 >/dev/null
  #       ;;
  #     pacman)
  #       sudo pacman -S --noconfirm jdk-openjdk 2>&1 >/dev/null
  #       ;;
  #     *)
  #       log_error "Unsupported package manager. Please install Java Development Kit manually."
  #       return 1
  #       ;;
  #   esac
    
  #   if [ $? -ne 0 ]; then
  #     log_error "Error installing java-devel. Exiting ...."
  #     return 1
  #   fi
  # fi
  
  install_latest_aws_cli() {
    log_info "Installing AWS CLI..."
    for i in {1..5}; do
      if curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" && \
        unzip -q awscliv2.zip && \
        sudo ./aws/install && \
        rm -rf aws awscliv2.zip; then
        log_info "AWS CLI installed successfully"
        break
      else
        log_info "AWS CLI installation attempt $i failed, retrying in 10 seconds..."
        sleep 10
      fi
    done
  }

  # Check for AWS CLI
  if ! command -v aws &>/dev/null; then
    log_error "aws is not installed. Exiting script."
    return 1
  else
    INSTALLED_VERSION=$(aws --version 2>&1 | awk '{print $1}' | cut -d/ -f2)
    INSTALLED_MAJOR=$(echo "$INSTALLED_VERSION" | cut -d. -f1)
    INSTALLED_MINOR=$(echo "$INSTALLED_VERSION" | cut -d. -f2)
    if [[ "$INSTALLED_MAJOR" -lt "$REQUIRED_MAJOR" ]] || \
      { [[ "$INSTALLED_MAJOR" -eq "$REQUIRED_MAJOR" ]] && [[ "$INSTALLED_MINOR" -lt "$REQUIRED_MINOR" ]]; }; then
      log_info "AWS CLI version $INSTALLED_VERSION is too old. Installing latest version..."
      install_latest_aws_cli
    fi
  fi
  
  # Check if DB2 is already installed
  # if command -v db2ls &>/dev/null; then
  #   log_error "An installation of Db2 is already installed. Uninstall it. Exiting script ...."
  #   return 1
  # fi
  
  log_success "Prerequisites check completed on $PLATFORM"
  return 0
}

# =============================================================================
# Region Validation
# =============================================================================

region_validation() {
  # Initialize AWS_REGION if not set
  AWS_REGION=${AWS_REGION:-"$REGION"}
  
  # Detect if Cloudshell
  if [[ "$AWS_TOOLING_USER_AGENT" == *"AWS-CloudShell"* ]]; then
    log_info "Detecting region using AWS CloudShell ..."
    REGION="${AWS_REGION}"
    if curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/ >/dev/null; then
      log_warning "CloudShell is not attached to a VPC. Use CloudShell with VPC to run this script."
      log_error "Exiting script."
      return 1
    fi
    log_info "Detected region from CloudShell environment: ${REGION:-'Could not detect'}"
  fi

  # Detect region if EC2
  if [[ "$PLATFORM" != "macos" ]] && curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/ >/dev/null; then
    log_info "Detecting region using EC2 metadata..."
    local token
    token=$(curl -sX PUT "http://169.254.169.254/latest/api/token" \
      -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" 2>/dev/null)
    if [[ -n "$token" ]]; then
      REGION=$(curl -s -H "X-aws-ec2-metadata-token: $token" \
        http://169.254.169.254/latest/dynamic/instance-identity/document 2>/dev/null |
        jq -r .region 2>/dev/null)
      log_info "Detected region from EC2 metadata (platform: $PLATFORM): ${REGION:-'Could not detect'}"
    fi
  fi

  if [ -z "$REGION" ]; then
    # If REGION is still empty, prompt user
    if [[ "$INTERACTIVE_MODE" == "true" ]]; then
      log_warning "No region detected. Please enter your AWS region."
      REGION=$(getInput "REGION" "us-east-1" "Enter AWS region")
    else
      log_info "use 'export REGION=your-region' and run command again."
      log_error "No region detected and not in interactive mode. Exiting script."
      return 1
    fi
  fi
  
  # Persist region based on platform
  case "$PLATFORM" in
  "linux")
    if echo "export REGION=\"$REGION\"" | sudo tee /etc/profile.d/region.sh >/dev/null 2>&1; then
      log_debug "REGION environment variable persisted to /etc/profile.d/region.sh"
    else
      log_debug "Failed to persist REGION in /etc/profile.d/. It will only be available in this session."
    fi
    ;;
  "macos")
    local shell_rc="$HOME/.zshrc" # Default macOS shell
    if [[ -n "$BASH_VERSION" ]]; then
      shell_rc="$HOME/.bash_profile"
    fi

    if grep -q 'export REGION=' "$shell_rc" 2>/dev/null; then
      # Replace existing REGION
      sed -i '' "s|^export REGION=.*|export REGION=\"$REGION\"|" "$shell_rc"
    else
      echo "export REGION=\"$REGION\"" >>"$shell_rc"
    fi
    log_debug "REGION exported to $shell_rc"
    ;;
  "wsl")
    local shell_rc="$HOME/.bashrc"
    if [[ -n "${ZSH_VERSION:-}" ]]; then
      shell_rc="$HOME/.zshrc"
    fi
    if grep -q 'export REGION=' "$shell_rc" 2>/dev/null; then
      # Replace existing
      sed -i "s|^export REGION=.*|export REGION=\"$REGION\"|" "$shell_rc"
    else
      echo "export REGION=\"$REGION\"" >>"$shell_rc"
    fi
    log_debug "REGION exported to $shell_rc (WSL)"
    ;;
  esac

  export REGION="$REGION"
  
  # Write ~/.aws/config if missing
  if [ ! -f ~/.aws/config ]; then
    mkdir -p ~/.aws
    echo -e "[default]\nregion = ${REGION}" >~/.aws/config
    log_debug "Created ~/.aws/config with region: $REGION"
  fi

  log_info "Region validation completed. Running on region: ${REGION:-'Could not detect'}"
  return 0
}

# =============================================================================
# Credentials Management
# =============================================================================

parse_and_export_creds() {
    local creds_json="$1"
    local source="$2"
    local profile="${PROFILE:-default}"
    
    # Extract credentials from JSON
    export AWS_ACCESS_KEY_ID=$(echo "$creds_json" | jq -r .AccessKeyId)
    export AWS_SECRET_ACCESS_KEY=$(echo "$creds_json" | jq -r .SecretAccessKey)
    export AWS_SESSION_TOKEN=$(echo "$creds_json" | jq -r .Token)
    
    # Detect environment
    local is_cloud_environment=false
    if [[ "$source" == "CloudShell" || "$source" == "EC2" ]]; then
        is_cloud_environment=true
    fi
    
    # Only modify AWS credentials file in cloud environments (CloudShell, EC2)
    # For on-premises Linux, just use environment variables
    if [[ "$is_cloud_environment" == "true" ]]; then
        # Ensure AWS directory exists
        mkdir -p ~/.aws
        
        # Check if credentials file exists
        if [[ ! -f ~/.aws/credentials ]]; then
            touch ~/.aws/credentials
            chmod 600 ~/.aws/credentials
        fi
        
        # Check if profile exists in credentials file
        if grep -q "^\[$profile\]" ~/.aws/credentials; then
            # Update existing profile
            log_debug "Updating existing profile '$profile' in ~/.aws/credentials"
            
            # Create a temporary file for the updated credentials
            local temp_file=$(mktemp)
            
            # Process the credentials file
            local in_profile=false
            while IFS= read -r line; do
                if [[ "$line" == "[$profile]" ]]; then
                    # Found the profile section
                    echo "$line" >> "$temp_file"
                    echo "aws_access_key_id = $AWS_ACCESS_KEY_ID" >> "$temp_file"
                    echo "aws_secret_access_key = $AWS_SECRET_ACCESS_KEY" >> "$temp_file"
                    echo "aws_session_token = $AWS_SESSION_TOKEN" >> "$temp_file"
                    in_profile=true
                elif [[ "$line" =~ ^\[.+\]$ ]]; then
                    # Found a new profile section
                    in_profile=false
                    echo "$line" >> "$temp_file"
                elif [[ "$in_profile" == "true" ]]; then
                    # Skip existing profile settings
                    continue
                else
                    # Copy other lines
                    echo "$line" >> "$temp_file"
                fi
            done < ~/.aws/credentials
            
            # Replace the original file with the updated one
            mv "$temp_file" ~/.aws/credentials
            chmod 600 ~/.aws/credentials
        else
            # Add new profile
            log_debug "Adding new profile '$profile' to ~/.aws/credentials"
            echo -e "\n[$profile]" >> ~/.aws/credentials
            echo "aws_access_key_id = $AWS_ACCESS_KEY_ID" >> ~/.aws/credentials
            echo "aws_secret_access_key = $AWS_SECRET_ACCESS_KEY" >> ~/.aws/credentials
            echo "aws_session_token = $AWS_SESSION_TOKEN" >> ~/.aws/credentials
        fi
        
        # Ensure config file exists with region information
        if [[ ! -f ~/.aws/config ]]; then
            echo "[default]" > ~/.aws/config
            echo "region = ${REGION:-us-east-1}" >> ~/.aws/config
            chmod 600 ~/.aws/config
        fi
        
        # Check if profile exists in config file
        if ! grep -q "^\[profile $profile\]" ~/.aws/config && [[ "$profile" != "default" ]]; then
            echo -e "\n[profile $profile]" >> ~/.aws/config
            echo "region = ${REGION:-us-east-1}" >> ~/.aws/config
        fi
        
        log_info "AWS credentials set from $source and written to ~/.aws/credentials for profile '$profile'"
    else
        # For on-premises Linux, just use environment variables
        log_info "AWS credentials set from $source as environment variables (not modifying ~/.aws/credentials)"
        
        # Create a temporary credentials file for this session only
        local temp_creds_file="/tmp/aws_creds_$$.env"
        echo "export AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID" > "$temp_creds_file"
        echo "export AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY" >> "$temp_creds_file"
        echo "export AWS_SESSION_TOKEN=$AWS_SESSION_TOKEN" >> "$temp_creds_file"
        echo "export AWS_DEFAULT_REGION=${REGION:-us-east-1}" >> "$temp_creds_file"
        chmod 600 "$temp_creds_file"
        
        # Export the path to the temporary credentials file
        export AWS_CREDS_FILE="$temp_creds_file"
        log_info "Temporary credentials file created at $temp_creds_file"
        log_info "You can source this file with: source $temp_creds_file"
    fi
    
    log_debug "AWS_ACCESS_KEY_ID: ${AWS_ACCESS_KEY_ID:0:5}..."
}

set_credentials() {
  local creds_source="unknown"
  local is_cloud_environment=false
  
  # Try CloudShell (local endpoint 127.0.0.1:1338)
  if curl -s --connect-timeout 1 http://127.0.0.1:1338/latest/meta-data/ >/dev/null; then
    log_info "Detected AWS CloudShell environment"
    TOKEN=$(curl -sX PUT "http://127.0.0.1:1338/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
    URI="http://127.0.0.1:1338/latest/meta-data/container/security-credentials"
    CREDS=$(curl -s -H "Authorization: $TOKEN" "$URI")
    parse_and_export_creds "$CREDS" "CloudShell"
    is_cloud_environment=true
    creds_source="CloudShell"
    return 0
  fi
  
  # Try EC2 IMDSv2
  if curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/ >/dev/null; then
    log_info "Detected EC2 environment"
    TOKEN=$(curl -sX PUT "http://169.254.169.254/latest/api/token" \
            -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
    ROLE_NAME=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
                http://169.254.169.254/latest/meta-data/iam/security-credentials/)
    if [[ -z "$ROLE_NAME" ]]; then
      log_warning "No IAM role attached to EC2 instance."
    else
      CREDS=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
                  http://169.254.169.254/latest/meta-data/iam/security-credentials/$ROLE_NAME)
      parse_and_export_creds "$CREDS" "EC2"
      is_cloud_environment=true
      creds_source="EC2"
      return 0
    fi
  fi
  
  # If we get here, we're likely in an on-premises environment
  log_info "Not running in AWS cloud environment, checking for existing AWS credentials"
  
  # Check if AWS credentials are already set in environment variables
  if [[ -n "$AWS_ACCESS_KEY_ID" && -n "$AWS_SECRET_ACCESS_KEY" ]]; then
    log_info "Using existing AWS credentials from environment variables"
    creds_source="environment"
    return 0
  fi
  
  # Check if credentials exist in ~/.aws/credentials
  if [[ -f ~/.aws/credentials ]]; then
    log_info "Found AWS credentials file at ~/.aws/credentials"
    
    # Check if the specified profile exists
    if grep -q "^\[${PROFILE:-default}\]" ~/.aws/credentials; then
      log_info "Using credentials from profile '${PROFILE:-default}' in ~/.aws/credentials"
      # We don't need to do anything here as AWS CLI will use the profile
      creds_source="profile"
      return 0
    fi
  fi
  
  # If we get here, we need to prompt for credentials in interactive mode
  if [[ "$INTERACTIVE_MODE" == "true" ]]; then
    log_info "No AWS credentials found. Please enter your AWS credentials:"
    read -p "AWS Access Key ID: " AWS_ACCESS_KEY_ID
    read -sp "AWS Secret Access Key: " AWS_SECRET_ACCESS_KEY
    echo
    read -p "AWS Session Token (leave empty if not using temporary credentials): " AWS_SESSION_TOKEN
    
    # Create a JSON structure for parse_and_export_creds
    local creds_json=$(cat <<EOF
{
  "AccessKeyId": "$AWS_ACCESS_KEY_ID",
  "SecretAccessKey": "$AWS_SECRET_ACCESS_KEY",
  "Token": "$AWS_SESSION_TOKEN"
}
EOF
)
    parse_and_export_creds "$creds_json" "user-input"
    creds_source="user-input"
    return 0
  else
    log_error "No AWS credentials found and not in interactive mode. Cannot proceed."
    log_error "Please set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and optionally AWS_SESSION_TOKEN"
    log_error "Or run in interactive mode to be prompted for credentials."
    return 1
  fi
}

# =============================================================================
# DB2 Client Installation Functions
# =============================================================================

create_db2_user() {
    local username="$DB2USER_NAME"
    local home="/home/$username"
    local start_id=1001

    # Find next available GID
    while getent group "$start_id" >/dev/null; do
        ((start_id++))
    done
    local gid=$start_id

    # Reuse that ID to check for UID
    while getent passwd "$start_id" >/dev/null; do
        ((start_id++))
    done
    local uid=$start_id

    log_info "Creating group $username with GID $gid"
    sudo groupadd -g "$gid" "$username"

    log_info "Creating user $username with UID $uid, GID $gid, and home $home"
    sudo useradd -u "$uid" -g "$gid" -d "$home" -m -s /bin/bash "$username"

    log_success "User $username created with UID=$uid, GID=$gid"

    user_home=$(eval echo ~$username)
    bashrc_path="$user_home/.bashrc"
    functions_path="$user_home/functions.sh"
    if ! sudo grep -Fxq "source $home/functions.sh" "$bashrc_path"; then
      log_info "Appending source line to $bashrc_path"
      echo "if [ -f $home/functions.sh ]; then" | sudo tee -a "$bashrc_path" > /dev/null
      echo "   source $home/functions.sh" | sudo tee -a "$bashrc_path" > /dev/null
      echo "fi" | sudo tee -a "$bashrc_path" > /dev/null
      sudo chown $username:$username "$bashrc_path"
    else
      log_info "Line already exists in $bashrc_path"
    fi
}

install_ds_driver() {
  log_info "============================================================================"
  log_info "Deploying Db2 11.5.9 DS Driver thin client"
  aws s3 cp $S3_BUCKET_URI . --quiet --region ${REGION} --profile ${PROFILE} &> /dev/null
  if [ $? -eq 0 ]; then
    log_info "============================================================================"
    log_info "Extracting the archive"
    tar -xzf $(basename $S3_BUCKET_URI) &> /dev/null
    /bin/rm -f $(basename $S3_BUCKET_URI) &> /dev/null
    
    # Check if DB2 user exists, if not create it
    if id "$DB2USER_NAME" &>/dev/null; then
      log_info "User $DB2USER_NAME already exists. Skipping user creation."
      if [[ -d "/home/$DB2USER_NAME/dsdriver" ]]; then
          log_info "Removing existing /home/$DB2USER_NAME/dsdriver directory..."
          sudo rm -rf "/home/$DB2USER_NAME/dsdriver" &> /dev/null
      fi
    else
      create_db2_user
    fi
    
    # Move dsdriver to user's home directory
    log_info "Moving dsdriver to /home/$DB2USER_NAME/"
    sudo rm -fr "/home/$DB2USER_NAME/dsdriver" &> /dev/null
    sudo mv -f dsdriver "/home/$DB2USER_NAME/"
    sudo chown -R "$DB2USER_NAME:$DB2USER_NAME" "/home/$DB2USER_NAME/dsdriver"
    
    # Fix the installDSDriver script
    sudo sed -i '1s|#! */bin/ksh -p|#! /bin/bash -p|' "/home/$DB2USER_NAME/dsdriver/installDSDriver"
    
    log_info "============================================================================"
    log_info "Installing the driver"
    
    # Run the installation as the DB2 user
    sudo -u "$DB2USER_NAME" bash -c "cd /home/$DB2USER_NAME/dsdriver && ./installDSDriver" &> /dev/null
    
    log_info "============================================================================"
    
    # Add db2profile to user's .bashrc
    user_bashrc="/home/$DB2USER_NAME/.bashrc"
    if sudo grep -qE "^source.*/dsdriver/db2profile" "$user_bashrc"; then
      log_info "db2profile is already added to $user_bashrc"
    else
      log_info "Adding db2profile to $user_bashrc"
      echo "source /home/$DB2USER_NAME/dsdriver/db2profile" | sudo tee -a "$user_bashrc" > /dev/null
      sudo chown "$DB2USER_NAME:$DB2USER_NAME" "$user_bashrc"
    fi
    
    # Download and set up the DSN entry script
    aws s3 cp ${SCRIPT_URI} . --quiet --region ${REGION} --profile ${PROFILE} &> /dev/null    
    sudo mv -f $(basename $SCRIPT_URI) "/home/$DB2USER_NAME/"
    sudo chown -R "$DB2USER_NAME:$DB2USER_NAME" "/home/$DB2USER_NAME/$(basename $SCRIPT_URI)"
    sudo chmod -x "/home/$DB2USER_NAME/$(basename $SCRIPT_URI)"
    
    # Add sudo privileges for the DB2 user
    echo "$DB2USER_NAME ALL=(ALL) NOPASSWD:ALL" | sudo tee "/etc/sudoers.d/$DB2USER_NAME" > /dev/null
    sudo chmod 440 "/etc/sudoers.d/$DB2USER_NAME"
    
    log_info "============================================================================"
    log_success "Db2 11.5.9 DS Driver thin client installed successfully for user $DB2USER_NAME"
    log_info "============================================================================"
    return 0
  else
    log_error "============================================================================"
    log_error "Copy of file from S3 was unsuccessful"
    log_error "============================================================================"
    return 1
  fi
}

check_if_rt_installed () {
  local installed=1
  if [[ -d "/opt/ibm/db2/V11.5" ]]; then
      installed=1
  fi
  if [[ -d "/home/$DB2USER_NAME/sqllib" ]]; then
      installed=1
  fi
  return $installed
}

install_rt_client() {
  log_info "============================================================================"
  log_info "Deploying Db2 11.5.9 Runtime client"
  aws s3 cp $S3_BUCKET_URI . --quiet --region ${REGION} --profile ${PROFILE} &> /dev/null
  if [ $? -eq 0 ]; then
    log_info "============================================================================"
    log_info "Extracting the archive"
    tar -xf $(basename $S3_BUCKET_URI) &> /dev/null
    /bin/rm -f $(basename $S3_BUCKET_URI) &> /dev/null
    if id "$DB2USER_NAME" &>/dev/null; then
      log_info "User $DB2USER_NAME already exists. Skipping user creation."
      if [[ -d "/home/$DB2USER_NAME/sqllib" ]]; then
          log_info "Removing existing /home/$DB2USER_NAME/sqllib directory..."
          sudo rm -rf "/home/$DB2USER_NAME/sqllib" &> /dev/null
      fi
    else
      create_db2_user
    fi
    if check_if_rt_installed; then
      log_info "============================================================================"  
      log_info "Db2 runtime client is already installed in user $DB2USER_NAME"
      log_info "============================================================================"  
      return 0
    fi
    cd rtcl
    sudo ./db2_install -f sysreq -y -b /opt/ibm/db2 &> /dev/null
    log_info "============================================================================"
    log_info "Installing Db2 runtime client"
    sudo /opt/ibm/db2/instance/db2icrt -s client "$DB2USER_NAME" &> /dev/null
    log_info "============================================================================"
    log_success "Db2 runtime client installed successfully in user $DB2USER_NAME"
    cd ..
    /bin/rm -fr rtcl &> /dev/null
    aws s3 cp ${SCRIPT_URI} . --quiet --region ${REGION} --profile ${PROFILE} &> /dev/null   
    sudo rm -rf "/home/$DB2USER_NAME/$(basename $SCRIPT_URI)" &> /dev/null 
    sudo mv -f $(basename $SCRIPT_URI) "/home/$DB2USER_NAME/"
    aws s3 cp ${FUNCTION_URI} . --quiet --region ${REGION} --profile ${PROFILE} &> /dev/null   
    sudo rm -rf "/home/$DB2USER_NAME/$(basename $FUNCTION_URI)" &> /dev/null 
    sudo mv -f $(basename $FUNCTION_URI) "/home/$DB2USER_NAME/"
    aws s3 cp ${EXFMT_URI} . --quiet --region ${REGION} --profile ${PROFILE} &> /dev/null    
    sudo rm -rf "/opt/ibm/db2/bin/$(basename $EXFMT_URI)" &> /dev/null
    sudo mv -f $(basename $EXFMT_URI) /opt/ibm/db2/bin
    sudo chown -R "$DB2USER_NAME:$DB2USER_NAME" "/home/$DB2USER_NAME/$(basename $FUNCTION_URI)"
    sudo chown -R "$DB2USER_NAME:$DB2USER_NAME" "/home/$DB2USER_NAME/$(basename $SCRIPT_URI)"
    sudo chown -R bin:bin /opt/ibm/db2/bin/$(basename $EXFMT_URI)
    sudo chmod +x /opt/ibm/db2/bin/$(basename $EXFMT_URI)
    echo "$DB2USER_NAME ALL=(ALL) NOPASSWD:ALL" | sudo tee "/etc/sudoers.d/$DB2USER_NAME" > /dev/null
    sudo chmod 440 "/etc/sudoers.d/$DB2USER_NAME"
    log_info "============================================================================"
    # log_info "1. Copy/Paste and Run command 'sudo su - $DB2USER_NAME' to change user to $DB2USER_NAME"
    # log_info "2. Copy/Paste and Run command 'source $(basename $SCRIPT_URI)' to re-create the DB2 DSN entries, if necessary"
    # log_info "============================================================================"
    return 0
  else
    log_error "============================================================================"
    log_error "Copy of file from S3 was unsuccessful"
    log_error "============================================================================"
    return 1
  fi
}

install_client() {
  if [[ "$CLIENT_TYPE" == "DS" ]]; then
    install_ds_driver
  else
    install_rt_client
  fi
}

# =============================================================================
# DSN Entry Creation Functions
# =============================================================================

has_special_chars() {
  local s="$1"
  # If $s contains any character other than A–Z, a–z, or 0–9, return 0 (true).
  if [[ $s =~ [^a-zA-Z0-9] ]]; then
    return 0
  else
    return 1
  fi
}

generate_alias() {
  local raw="$1"
  local name="${raw^^}"
  name="${name:0:8}"
  local len=${#name}
  if (( len ==0 )); then
    echo ""
    return
  fi
  local new_alias
  if (( len < 8 )); then
    new_alias="${name}S"
  else
    local prefix="${name:0:7}"
    local last="${name: -1}"
    if [[ "$last" == "S" ]]; then
      new_alias="${prefix}U"
    else
      new_alias="${prefix}S"
    fi
  fi
  echo "$new_alias"
}

list_db_instances() {
  # Ensure credentials are set
  set_credentials
  
  local aws_output
  
  # Define the query separately to avoid quoting issues
  local query='DBInstances[?starts_with(Engine, `db2`)].DBInstanceIdentifier'
  
  # Check if we're in an on-premises environment with a temporary credentials file
  if [[ -n "$AWS_CREDS_FILE" && -f "$AWS_CREDS_FILE" ]]; then
    log_debug "Using temporary credentials file for AWS command"
    aws_output=$(source "$AWS_CREDS_FILE" && aws rds describe-db-instances \
      --profile "$PROFILE" \
      --region "$REGION" "$URL" \
      --query "$query" \
      --output text 2>/tmp/list_db_instances.error)
  else
    # Use standard approach with profile
    aws_output=$(aws rds describe-db-instances \
      --profile "$PROFILE" \
      --region "$REGION" "$URL" \
      --query "$query" \
      --output text 2>/tmp/list_db_instances.error)
  fi
  
  local existing_instances=($aws_output)

  if [ -s /tmp/list_db_instances.error ]; then
    log_error "Error listing DB instances:"
    cat /tmp/list_db_instances.error >&2
    
    # Try alternative approach with direct environment variables
    log_info "Trying alternative approach to list DB instances..."
    if [[ -n "$AWS_CREDS_FILE" && -f "$AWS_CREDS_FILE" ]]; then
      # Source the credentials file and run the command
      aws_output=$(source "$AWS_CREDS_FILE" && \
                  AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
                  AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
                  AWS_SESSION_TOKEN="$AWS_SESSION_TOKEN" \
                  aws rds describe-db-instances \
                  --region "$REGION" "$URL" \
                  --query "$query" \
                  --output text 2>/tmp/list_db_instances.error2)
    else
      # Use environment variables directly
      aws_output=$(AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
                  AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
                  AWS_SESSION_TOKEN="$AWS_SESSION_TOKEN" \
                  aws rds describe-db-instances \
                  --region "$REGION" "$URL" \
                  --query "$query" \
                  --output text 2>/tmp/list_db_instances.error2)
    fi
    
    if [ -s /tmp/list_db_instances.error2 ]; then
      log_error "Alternative approach also failed:"
      cat /tmp/list_db_instances.error2 >&2
      return 1
    else
      existing_instances=($aws_output)
    fi
  fi

  if [ ${#existing_instances[@]} -eq 0 ]; then
    log_info "No DB2 instances found in region $REGION"
    return 1
  else
    log_debug "INTERACTIVE_MODE=$INTERACTIVE_MODE DB_INSTANCE_ID=$DB_INSTANCE_ID"
    if [[ "$INTERACTIVE_MODE" == "false" ]]; then
      # Non-interactive mode - check if DB_INSTANCE_ID environment variable is provided
      if [[ -n "$DB_INSTANCE_ID" ]]; then
        # Special case for "ALL" - return all instances
        if [[ "$DB_INSTANCE_ID" == "ALL" ]]; then
          log_info "Processing ALL DB2 instances: ${existing_instances[*]}"
          # Store all instances in a global array for later use
          DB_INSTANCES=("${existing_instances[@]}")
          return 0
        fi
        
        # Validate the provided DB_INSTANCE_ID against available instances
        local instance_found=false
        for instance in "${existing_instances[@]}"; do
          if [[ "$instance" == "$DB_INSTANCE_ID" ]]; then
            instance_found=true
            break
          fi
        done
        
        if [[ "$instance_found" == "true" ]]; then
          log_info "Non-interactive mode: Using provided DB2 instance: $DB_INSTANCE_ID"
          # Store in array for consistency with ALL case
          DB_INSTANCES=("$DB_INSTANCE_ID")
          return 0
        else
          log_warning "Non-interactive mode: Provided DB_INSTANCE_ID '$DB_INSTANCE_ID' not found in available instances"
          log_info "Available DB2 instances: ${existing_instances[*]}"
          log_info "Falling back to first available instance: ${existing_instances[0]}"
          DB_INSTANCE_ID="${existing_instances[0]}"
          DB_INSTANCES=("$DB_INSTANCE_ID")
          return 0
        fi
      else
        # No DB_INSTANCE_ID provided, use first instance
        DB_INSTANCE_ID="${existing_instances[0]}"
        DB_INSTANCES=("$DB_INSTANCE_ID")
        log_info "Non-interactive mode: No DB_INSTANCE_ID specified, using first DB2 instance: $DB_INSTANCE_ID"
        log_info "💡 Tip: To specify a specific instance, use: DB_INSTANCE_ID=your-instance-id"
        log_info "💡 Tip: To process all instances, use: DB_INSTANCE_ID=ALL"
        log_info "Available instances: ${existing_instances[*]}"
        return 0
      fi
    fi

    # Interactive mode
    if [ ${#existing_instances[@]} -eq 1 ]; then
      # Only one instance available, automatically select it
      DB_INSTANCE_ID="${existing_instances[0]}"
      DB_INSTANCES=("$DB_INSTANCE_ID")
      log_info "Only one DB2 instance found: $DB_INSTANCE_ID"
      log_info "Automatically selected this instance."
      return 0
    fi
    local choice=-1  # Initialize to -1 to ensure the loop runs at least once
    while [ "$choice" -lt 0 ] || [ "$choice" -gt ${#existing_instances[@]} ]; do
      # Display the available DB instances
      echo "Available DB2 instances:" >&2
      echo "0. ALL instances (process all DB2 instances)" >&2
      for i in "${!existing_instances[@]}"; do
        echo "$((i + 1)). ${existing_instances[$i]}" >&2
      done

      # Prompt the user to select an instance type
      read -p "Select the instance identifier by number (0-${#existing_instances[@]}): " choice
      choice=${choice:-0}
      # Validate the selection and return the chosen type
      if [[ $choice -eq 0 ]]; then
        DB_INSTANCE_ID="ALL"
        DB_INSTANCES=("${existing_instances[@]}")
        log_info "Selected ALL DB2 instances"
      elif [[ $choice -ge 1 && $choice -le ${#existing_instances[@]} ]]; then
        selected_type="${existing_instances[$((choice - 1))]}"
        DB_INSTANCE_ID="$selected_type"
        DB_INSTANCES=("$DB_INSTANCE_ID")
      else
        echo "Invalid choice. Please enter a value between 0 and ${#existing_instances[@]}" >&2
        choice=-1 # Reset choice to continue the loop
      fi
    done
  fi
  return 0
}

get_default_database_name() {
  # Get the default database name from the DB instance
  local db_name=($(aws rds describe-db-instances \
    --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
    --region "$REGION" "$URL" \
    --query "DBInstances[0].DBName" \
    --output text))
  
  if [ "$db_name" = "None" ]; then
    echo ""
  else
    echo "$db_name"
  fi
}

get_db2_databases_from_rdsadmin() {
  local user="$1"
  local pass="$2"
  local alias=RDSADMIN
  
  if [[ "$CLIENT_TYPE" == "DS" ]]; then
    # DS Driver uses clpplus with password in command
#region    
    "$HOME/dsdriver/bin/clpplus" -nw "${user}/${pass}@${alias}" <<EOF | sed -e '/^$/d' -e '1,/^Port.*/d'
set pagesize 0;
set feedback off;
set verify off;
set heading off;
set echo off;
SELECT database_name
  FROM TABLE(rdsadmin.list_databases())
  WHERE UPPER(database_name) <> 'RDSADMIN';
EXIT;
EOF
  else
    # RT Client uses clpplus with password prompt
    "$HOME/sqllib/bin/clpplus" -nw "${user}@${alias}" <<EOF | sed -e '/^$/d' -e '1,/^Port.*/d'
${pass}
set pagesize 0;
set feedback off;
set verify off;
set heading off;
set echo off;
SELECT database_name
  FROM TABLE(rdsadmin.list_databases())
  WHERE UPPER(database_name) <> 'RDSADMIN';
EXIT;
EOF
#endregion
  fi
}

get_all_database_names() {
  local db_instance_id="$1"
  local master_user="$2"
  local master_password="$3"
  
  # Clear the array before populating
  DB_NAMES=()
  
  # First try to get the default database name
  local default_dbname=$(get_default_database_name)
  
  # If default database name exists, add it to the array
  if [[ -n "$default_dbname" && "$default_dbname" != "None" ]]; then
    log_info "Default database name found: $default_dbname"
    DB_NAMES+=("$default_dbname")
    return 0
  fi
  
  # Get additional databases from RDSADMIN
  log_info "Getting list of databases from RDSADMIN database, if no default database name was found"
  local db_names
  mapfile -t db_names < <(
    get_db2_databases_from_rdsadmin "$master_user" "$master_password"
  )
  
  # Add unique database names to the array
  if [ ${#db_names[@]} -gt 0 ]; then
    for dbname in "${db_names[@]}"; do
      dbname="$(echo $dbname | xargs)"
      
      # Check if this database is already in the array
      local already_exists=false
      for existing_db in "${DB_NAMES[@]}"; do
        if [[ "$existing_db" == "$dbname" ]]; then
          already_exists=true
          break
        fi
      done
      
      # Add if not already in the array
      if [[ "$already_exists" == "false" && -n "$dbname" ]]; then
        DB_NAMES+=("$dbname")
      fi
    done
  fi
  
  # If no databases found, log a warning
  if [ ${#DB_NAMES[@]} -eq 0 ]; then
    log_warning "No databases found for instance $db_instance_id"
    return 1
  fi
  
  log_info "Found ${#DB_NAMES[@]} database(s) for instance $db_instance_id: ${DB_NAMES[*]}"
  return 0
}

writecfg_tcp() {
  local dsn=$1
  local dbname=$2
  local db_address=$3
  local db_port=$4
  db2cli writecfg add \
    -dsn $dsn \
    -database $dbname \
    -host $db_address \
    -port $db_port \
    -parameter "Authentication=SERVER_ENCRYPT"
}

writecfg_ssl() {
  local dsn=$1
  local dbname=$2
  local db_address=$3
  local db_port=$4
  db2cli writecfg add \
    -dsn $dsn \
    -database $dbname \
    -host $db_address \
    -port $db_port \
    -parameter "SSLServerCertificate=$PWD/$REGION-bundle.pem;SecurityTransportMode=SSL;TLSVersion=TLSV12"
}

get_ssl_port() {
  SSL_PORT=""
  DB_PARAM_GROUP_NAME=$(aws rds describe-db-instances \
      --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
      --region "$REGION" "$URL" \
      --query "DBInstances[0].DBParameterGroups[0].DBParameterGroupName" \
      --output text)
  if [ "$DB_PARAM_GROUP_NAME" != "" ]; then
    SSL_PORT=$(aws rds describe-db-parameters \
        --db-parameter-group-name "$DB_PARAM_GROUP_NAME" \
        --region "$REGION" "$URL" \
        --query "Parameters[?ParameterName=='ssl_svcename'].ParameterValue" \
        --output text)
    if [ "$SSL_PORT" = "None" ]; then
      SSL_PORT=""
    fi
  fi
  echo "$SSL_PORT"
}

download_pem_file() {
  log_info "Downloading AWS SSL certificate from AWS RDS..."
  curl -sS "https://truststore.pki.rds.amazonaws.com/us-east-1/$REGION-bundle.pem" -o $REGION-bundle.pem
  if [ $? -ne 0 ]; then
    log_error "Failed to download SSL certificate. Exiting..."
    return 1
  fi
}

build_password_help () {
  local cmd
  cmd="To set master user password in env var MASTER_USER_PASSWORD, run command 'get_master_user_password'"
  HELP_COMMANDS+=("$cmd")
  cmd="The command 'get_master_user_password' is in functions.sh script. You can run it with 'source functions.sh' to load the functions."
  HELP_COMMANDS+=("$cmd")
  cmd="============================================================================"
  HELP_COMMANDS+=("$cmd")
}

build_connect_help_ds() {
  local ALIAS_NAME=$1
  local DB_NAME=$2
  local CTYPE=$3
  local DB_HOST=$4
  local DB_PORT=$5
  local index=$6
  local cmd="For $DB_NAME $CTYPE, copy and paste to run the command \"echo \$MASTER_USER_PASSWORDS[$index] | clpplus -nw ${MASTER_USER_NAMES[$index]}@${ALIAS_NAME}\" to test the connection."
  HELP_COMMANDS+=("$cmd")
  if has_special_chars "$MASTER_USER_PASSWORD"; then
    if [ "$CTYPE" = "TCP" ]; then 
      cmd="copy and paste to run the command \"clpplus -nw ${MASTER_USER_NAMES[$index]}/\'\${MASTER_USER_PASSWORDS[$index]}\'@${DB_HOST}:${DB_PORT}/${DB_NAME}\" to open the CLPPlus shell using all parameters."
      HELP_COMMANDS+=("$cmd")
    else
      cmd="copy and paste to run the command \"clpplus -nw ${MASTER_USER_NAMES[$index]}@${ALIAS_NAME}\" to open the CLPPlus shell and specify the password when prompted."
      HELP_COMMANDS+=("$cmd")
      cmd="Copy and paste the Password = $MASTER_USER_PASSWORDS[$index] when prompted for the password."
      HELP_COMMANDS+=("$cmd")
    fi  
  else
    cmd="For $DB_NAME $CTYPE, copy and paste to run the command \"clpplus -nw ${MASTER_USER_NAMES[$index]}/\${MASTER_USER_PASSWORDS[$index]}@${ALIAS_NAME}\" to open the CLPPlus shell."
    HELP_COMMANDS+=("$cmd")  
  fi
  cmd="============================================================================"
  HELP_COMMANDS+=("$cmd")
}

build_connect_help_rt() {
  local ALIAS_NAME=$1
  local DB_NAME=$2
  local index=$3
  local cmd
  cmd="For $DB_NAME, copy and paste to run the command \"db2 connect to ${ALIAS_NAME} user ${MASTER_USER_NAMES[$index]} using \'\${MASTER_USER_PASSWORDS[$index]}\'\" to connect to the database"
  HELP_COMMANDS+=("$cmd")
}

print_all_help() {
  if (( ${#HELP_COMMANDS[@]} == 0 )); then
    log_warning "No commands collected."
    return
  fi

  log_info "============================================================================"
  for c in "${HELP_COMMANDS[@]}"; do
    echo "  $c"
  done
  log_info "============================================================================"
}

get_all_master_user_names() {
  # Clear the array before populating
  MASTER_USER_NAMES=()
  
  # Process each DB instance
  for db_instance in "${DB_INSTANCES[@]}"; do
    local master_user_name=$(aws rds describe-db-instances \
      --db-instance-identifier "$db_instance" \
      --region "$REGION" "$URL" \
      --query "DBInstances[0].MasterUsername" \
      --output text)

    if [ "$master_user_name" = "None" ]; then
      log_warning "No master user name found for instance $db_instance"
      MASTER_USER_NAMES+=("")
    else
      log_info "Master user name for instance $db_instance: $master_user_name"
      MASTER_USER_NAMES+=("$master_user_name")
    fi
  done
  
  log_info "Found ${#MASTER_USER_NAMES[@]} master user names for ${#DB_INSTANCES[@]} instances"
  return 0
}

get_all_master_passwords() {
  # Clear the array before populating
  MASTER_USER_PASSWORDS=()
  
  # Path to the password file
  local password_file="$HOME/.need_password"
  local need_to_create_file=false
  local actual_password_count=0
  
  # Process each DB instance
  for db_instance in "${DB_INSTANCES[@]}"; do
    # First try to get password from AWS Secrets Manager (highest priority)
    local secret_arn=$(aws rds describe-db-instances \
      --db-instance-identifier "$db_instance" \
      --region "$REGION" "$URL" \
      --query "DBInstances[0].MasterUserSecret.SecretArn" \
      --output text)
      
    if [[ -n "$secret_arn" && "$secret_arn" != "None" ]]; then
      # Try to get password from Secrets Manager
      local secret_json=$(aws secretsmanager get-secret-value \
        --secret-id "$secret_arn" \
        --query "SecretString" \
        --region $REGION \
        --output text)
      local master_password=$(jq -r '.password' <<< "$secret_json")
      
      if [[ -n "$master_password" ]]; then
        log_info "Retrieved password from secret manager for instance $db_instance"
        MASTER_USER_PASSWORDS+=("$master_password")
        ((actual_password_count++))
        continue  # Password found, move to next instance
      else
        log_warning "Failed to get password from secret manager for instance $db_instance"
        # Fall through to check .need_password file
      fi
    else
      log_warning "No MasterUserSecret found for instance $db_instance"
      # Fall through to check .need_password file
    fi
    
    # If we get here, we need to check the .need_password file
    local file_password=""
    if [[ -f "$password_file" ]]; then
      # Look for the db_instance in the password file
      file_password=$(grep "^$db_instance " "$password_file" 2>/dev/null | cut -d' ' -f2-)
    fi
    
    if [[ -n "$file_password" && "$file_password" != "replace this with the master user password" ]]; then
      log_info "Using password from $password_file for instance $db_instance"
      MASTER_USER_PASSWORDS+=("$file_password")
      ((actual_password_count++))
    else
      # No password found in either Secrets Manager or .need_password file
      
      # If in interactive mode, prompt for password
      if [[ "$INTERACTIVE_MODE" == "true" ]]; then
        log_info "No password found for instance $db_instance. Please enter password:"
        read -rsp "Password for $db_instance: " entered_password
        echo  # Add a newline after password input
        
        if [[ -n "$entered_password" ]]; then
          MASTER_USER_PASSWORDS+=("$entered_password")
          ((actual_password_count++))
          
          # Ask if user wants to save the password to .need_password file
          read -p "Save this password to $password_file for future use? (y/n): " save_response
          if [[ "$save_response" == "y" || "$save_response" == "Y" ]]; then
            # Ensure password file exists with proper permissions
            if [[ ! -f "$password_file" ]]; then
              touch "$password_file" 2>/dev/null
              chmod 600 "$password_file" 2>/dev/null  # Secure the file
            fi
            
            # Remove any existing entry for this instance
            if grep -q "^$db_instance " "$password_file" 2>/dev/null; then
              # Create a temporary file for the updated content
              local temp_file=$(mktemp)
              grep -v "^$db_instance " "$password_file" > "$temp_file"
              mv "$temp_file" "$password_file"
              chmod 600 "$password_file"
            fi
            
            # Add the new entry
            echo "$db_instance $entered_password" >> "$password_file"
            log_info "Password saved to $password_file"
          fi
          
          continue  # Password entered, move to next instance
        fi
      fi
      
      # If we get here, either we're in non-interactive mode or the user didn't enter a password
      log_warning "No password found for instance $db_instance"
      MASTER_USER_PASSWORDS+=("")
      need_to_create_file=true
      
      # Add entry to .need_password file for manual update
      if [[ ! -f "$password_file" ]]; then
        touch "$password_file" 2>/dev/null
        chmod 600 "$password_file" 2>/dev/null  # Secure the file
      fi
      
      # Check if entry already exists
      if ! grep -q "^$db_instance " "$password_file" 2>/dev/null; then
        echo "$db_instance 'replace this with the master user password'" >> "$password_file" 2>/dev/null
      fi
    fi
  done
  
  if [[ "$need_to_create_file" == "true" ]]; then
    log_warning "Some passwords were not found. Please edit $password_file"
    log_warning "and replace the placeholder text with actual passwords."
    log_warning "Then run this script again."
  fi
  
  # Provide accurate count of actual passwords found
  if [[ $actual_password_count -eq ${#DB_INSTANCES[@]} ]]; then
    log_info "Found passwords for all ${#DB_INSTANCES[@]} instances"
  else
    log_warning "Found $actual_password_count passwords for ${#DB_INSTANCES[@]} instances"
    if [[ $actual_password_count -lt ${#DB_INSTANCES[@]} ]]; then
      log_warning "Missing passwords for some instances. Check $password_file for details."
    fi
  fi
  
  return 0
}

get_url() {
  if [[ -n "$ENDPOINT_URL" && "$ENDPOINT_URL" =~ ^https:// ]]; then
    URL="--endpoint-url $ENDPOINT_URL --no-verify-ssl"
  else
    URL=""
  fi
}

create_dsn_entries_ds() {
  log_info "============================================================================"
  log_info "Creating DB2 DS DSN entries for RDS DB instance(s) with db2 engine"
  
  # Source the db2profile for the DB2 user
  if [[ "$USER" == "$DB2USER_NAME" ]]; then
    source "/home/$DB2USER_NAME/dsdriver/db2profile"
  else
    log_info "You need to run this as user $DB2USER_NAME to create DSN entries"
    log_info "run: 'sudo su - $DB2USER_NAME'"
    log_info "run: 'source $(basename $SCRIPT_URI)'"
    return 0
  fi
  
  get_url
  log_info "============================================================================"
  log_info "The AWS region chosen is $REGION"
  log_info "============================================================================"
  log_info "Getting the DB instance identifier(s)..."
  
  if [[ -n "$DB_INSTANCE_ID" ]]; then
    if [[ "$DB_INSTANCE_ID" == "ALL" ]]; then
      # Get all DB2 instances
      list_db_instances
      if [ ${#DB_INSTANCES[@]} -eq 0 ]; then
        log_error "No DB instances found with db2 engine. Exiting..."
        log_error "============================================================================"
        return 1
      fi
      log_info "Processing all DB2 instances: ${DB_INSTANCES[*]}"
    else
      # Single instance specified
      DB_INSTANCES=("$DB_INSTANCE_ID")
      log_info "Using DB instance identifier from environment: $DB_INSTANCE_ID"
    fi
  else
    # No instance specified, get all instances
    list_db_instances
    if [ ${#DB_INSTANCES[@]} -eq 0 ]; then
      log_error "No DB instances found with db2 engine. Exiting..."
      log_error "============================================================================"
      return 1
    fi
  fi

  # Get all master user names and passwords
  get_all_master_user_names
  get_all_master_passwords
  
  # Process each DB instance
  for i in "${!DB_INSTANCES[@]}"; do
    local DB_INSTANCE_IDENTIFIER="${DB_INSTANCES[$i]}"
    local MASTER_USER_NAME="${MASTER_USER_NAMES[$i]}"
    local MASTER_USER_PASSWORD="${MASTER_USER_PASSWORDS[$i]}"
    local SUFFIX
    
    if [ ${#DB_INSTANCES[@]} -eq 1 ]; then
      SUFFIX=""
    else
      SUFFIX="$i"
    fi
    
    log_info "============================================================================"
    log_info "Processing DB instance: $DB_INSTANCE_IDENTIFIER"
    log_info "============================================================================"
    
    # Skip if no master user name or password
    if [[ -z "$MASTER_USER_NAME" ]]; then
      log_error "No master user name found for $DB_INSTANCE_IDENTIFIER. Skipping..."
      continue
    fi
    
    if [[ -z "$MASTER_USER_PASSWORD" ]]; then
      log_warning "No password available for $DB_INSTANCE_IDENTIFIER. Skipping..."
      continue
    fi
    
    DB_ADDRESS=$(aws rds describe-db-instances \
          --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
          --region "$REGION" "$URL" \
          --query "DBInstances[0].Endpoint.Address" \
          --output text
    )
    if [ "$DB_ADDRESS" = "" ]; then
      log_error "No database endpoint URL found for $DB_INSTANCE_IDENTIFIER. Skipping..."
      continue
    fi
    
    DB_TCP_IP_PORT=$(aws rds describe-db-instances \
          --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
          --region "$REGION" "$URL"
          --query "DBInstances[0].Endpoint.Port" \
          --output text)
    if [ "$DB_TCP_IP_PORT" = "" ]; then
      log_error "No TCP/IP port found for $DB_INSTANCE_IDENTIFIER. Skipping TCP/IP DSN entry creation."
    else
      build_password_help
      log_info "============================================================================"
      log_info "Creating RDSADMIN DSN entry in db2dsdriver.cfg for TCPIP connection..."
      # Use instance ID in DSN name to make it unique
      local admin_dsn="RDSADMIN${SUFFIX}"
      writecfg_tcp "$admin_dsn" "RDSADMIN" "$DB_ADDRESS" "$DB_TCP_IP_PORT"
      build_connect_help_ds "$admin_dsn" "RDSADMIN" "TCP" "$DB_ADDRESS" "$DB_TCP_IP_PORT" "$i"
      # Get all database names for this instance using RDSADMIN
      if ! get_all_database_names "$DB_INSTANCE_IDENTIFIER" "$MASTER_USER_NAME" "$MASTER_USER_PASSWORD"; then
        log_warning "No databases found for instance $DB_INSTANCE_IDENTIFIER. Creating only RDSADMIN entries."
      fi
      # Process each database in DB_NAMES
      for dbname in "${DB_NAMES[@]}"; do
        # Use instance ID in DSN name to make it unique
        aliasname="${dbname}${SUFFIX}"
        log_info "============================================================================"
        log_info "Registering $dbname as DSN $aliasname using TCP/IP connection..."
        writecfg_tcp "$aliasname" "$dbname" "$DB_ADDRESS" "$DB_TCP_IP_PORT"
        build_connect_help_ds "$aliasname" "$dbname" "TCP" "$DB_ADDRESS" "$DB_TCP_IP_PORT" "$i"
      done
    fi
    
    log_info "============================================================================"
    SSL_PORT=$(get_ssl_port)
    if [ "$SSL_PORT" = "" ]; then
      log_info "No SSL port found for $DB_INSTANCE_IDENTIFIER. Skipping SSL DSN entry creation."
    else
      download_pem_file
      log_info "============================================================================"
      log_info "SSL port found: $SSL_PORT"
      log_info "Creating RDSADMIN DSN entry in db2dsdriver.cfg for SSL connection..."
      # Use instance ID in DSN name to make it unique
      local ssl_admin_dsn="RDSDBSSL${SUFFIX}"
      writecfg_ssl "$ssl_admin_dsn" "RDSADMIN" "$DB_ADDRESS" "$SSL_PORT"
      build_connect_help_ds "$ssl_admin_dsn" "RDSADMIN" "SSL" "$DB_ADDRESS" "$SSL_PORT" "$i"
      
      # Process each database in DB_NAMES
      for dbname in "${DB_NAMES[@]}"; do
        # Use instance ID in DSN name to make it unique
        aliasname="$(generate_alias "${dbname}")${SUFFIX}"
        log_info "============================================================================"
        log_info "Registering $dbname as $aliasname using SSL"
        writecfg_ssl "$aliasname" "$dbname" "$DB_ADDRESS" "$SSL_PORT"
        build_connect_help_ds "$aliasname" "$dbname" "SSL" "$DB_ADDRESS" "$SSL_PORT" "$i"
      done
    fi
  done
}

create_dsn_entries_rt() {
  log_info "============================================================================"
  log_info "Creating DB2 RT DSN entries for RDS DB instance(s) with db2 engine"
  
  # Check if running as the DB2 user
  if [[ "$USER" != "$DB2USER_NAME" ]]; then
    log_info "You need to run this as user $DB2USER_NAME to create DSN entries"
    log_info "Run: 'sudo su - $DB2USER_NAME'"
    log_info "Run: 'source $(basename $SCRIPT_URI)'"
    return 0
  fi
  
  get_url
  log_info "============================================================================"
  log_info "The AWS region chosen is $REGION"
  log_info "============================================================================"
  
  if [[ -n "$DB_INSTANCE_ID" ]]; then
    if [[ "$DB_INSTANCE_ID" == "ALL" ]]; then
      # Get all DB2 instances
      list_db_instances
      if [ ${#DB_INSTANCES[@]} -eq 0 ]; then
        log_error "No DB instances found with db2 engine. Exiting..."
        log_error "============================================================================"
        return 1
      fi
      log_info "Processing all DB2 instances: ${DB_INSTANCES[*]}"
    else
      # Single instance specified
      DB_INSTANCES=("$DB_INSTANCE_ID")
      log_info "Using DB instance identifier from environment: $DB_INSTANCE_ID"
    fi
  else
    # No instance specified, get all instances
    log_info "Getting the DB instance identifier(s)..."
    list_db_instances
    if [ ${#DB_INSTANCES[@]} -eq 0 ]; then
      log_error "No DB instances found with db2 engine. Exiting..."
      log_error "============================================================================"
      return 1
    fi
  fi
  
    # Get all master user names and passwords
  get_all_master_user_names
  get_all_master_passwords

  # Process each DB instance
  for i in "${!DB_INSTANCES[@]}"; do
    local DB_INSTANCE_IDENTIFIER="${DB_INSTANCES[$i]}"
    local MASTER_USER_NAME="${MASTER_USER_NAMES[$i]}"
    local MASTER_USER_PASSWORD="${MASTER_USER_PASSWORDS[$i]}"
    local SUFFIX
    
    if [ ${#DB_INSTANCES[@]} -eq 1 ]; then
      SUFFIX=""
    else
      SUFFIX="$i"
    fi

    log_info "============================================================================"
    log_info "Processing DB instance: $DB_INSTANCE_IDENTIFIER"
    log_info "============================================================================"
    
    # Skip if no master user name or password
    if [[ -z "$MASTER_USER_NAME" ]]; then
      log_error "No master user name found for $DB_INSTANCE_IDENTIFIER. Skipping..."
      continue
    fi
    
    if [[ -z "$MASTER_USER_PASSWORD" ]]; then
      log_warning "No password available for $DB_INSTANCE_IDENTIFIER. Skipping..."
      continue
    fi
    
    DB_ADDRESS=$(aws rds describe-db-instances \
          --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
          --region "$REGION" "$URL" \
          --query "DBInstances[0].Endpoint.Address" \
          --output text
    )
    if [ "$DB_ADDRESS" = "" ]; then
      log_error "No database endpoint URL found for $DB_INSTANCE_IDENTIFIER. Skipping..."
      continue
    fi
    
    DB_TCP_IP_PORT=$(aws rds describe-db-instances \
          --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
          --region "$REGION" "$URL" \
          --query "DBInstances[0].Endpoint.Port" \
          --output text)
    if [ "$DB_TCP_IP_PORT" = "" ]; then
      log_error "No TCP/IP port found for $DB_INSTANCE_IDENTIFIER. Skipping TCP/IP DSN entry creation."
    else
      if [ -f "$HOME/sqllib/cfg/db2dsdriver.cfg" ]; then
        rm -f "$HOME/sqllib/cfg/db2dsdriver.cfg"
      fi
      build_password_help
      log_info "============================================================================"
      log_info "Creating RDSADMIN DSN entry in db2dsdriver.cfg for TCPIP connection..."
      # Use instance ID in DSN name to make it unique
      local admin_dsn="RDSADMIN${SUFFIX}"
      writecfg_tcp "$admin_dsn" "RDSADMIN" "$DB_ADDRESS" "$DB_TCP_IP_PORT"
      build_connect_help_rt "$admin_dsn" "RDSADMIN" "$i"
      # Get all database names for this instance
      if ! get_all_database_names "$DB_INSTANCE_IDENTIFIER" "$MASTER_USER_NAME" "$MASTER_USER_PASSWORD"; then
        log_warning "No databases found for instance $DB_INSTANCE_IDENTIFIER. Creating only RDSADMIN entries."
      fi
      # Process each database in DB_NAMES
      for dbname in "${DB_NAMES[@]}"; do
        # Use instance ID in DSN name to make it unique
        aliasname="${dbname}${SUFFIX}"
        log_info "============================================================================"
        log_info "Registering $dbname as DSN $aliasname using TCP/IP connection..."
        writecfg_tcp "$aliasname" "$dbname" "$DB_ADDRESS" "$DB_TCP_IP_PORT"
        build_connect_help_rt "$aliasname" "$dbname" "$i"
      done
    fi
    
    log_info "============================================================================"
    SSL_PORT=$(get_ssl_port)
    if [ "$SSL_PORT" = "" ]; then
      log_info "No SSL port found for $DB_INSTANCE_IDENTIFIER. Skipping SSL DSN entry creation."
    else
      download_pem_file
      log_info "============================================================================"
      log_info "SSL port found: $SSL_PORT"
      log_info "Creating RDSADMIN DSN entry in db2dsdriver.cfg for SSL connection..."
      # Use instance ID in DSN name to make it unique
      local ssl_admin_dsn="RDSDBSSL${SUFFIX}"
      writecfg_ssl "$ssl_admin_dsn" "RDSADMIN" "$DB_ADDRESS" "$SSL_PORT"
      build_connect_help_rt "$ssl_admin_dsn" "RDSADMIN SSL" "$i"
      
      # Process each database in DB_NAMES
      for dbname in "${DB_NAMES[@]}"; do
        # Use instance ID in DSN name to make it unique
        aliasname="$(generate_alias "${dbname}")${SUFFIX}"
        log_info "============================================================================"
        log_info "Registering $dbname as $aliasname using SSL"
        writecfg_ssl "$aliasname" "$dbname" "$DB_ADDRESS" "$SSL_PORT"
        build_connect_help_rt "$aliasname" "$dbname SSL" "$i"
      done
    fi
  done
}

prepare_db2user_for_dsn_entries () {
  # Run the script as DB2USER_NAME using a temporary script
  log_info "Executing DSN creation through root as user $DB2USER_NAME..."
  
  # Create a temporary script file
  local temp_script="/tmp/db2_dsn_setup_$$.sh"
  
  # Write the script content to the temporary file
  cat > "$temp_script" << EOF
#!/usr/bin/env bash

echo "Client Type: $CLIENT_TYPE"

# Create $DSN_CHECK_FILE file
cat << EOFINNER > "/home/$DB2USER_NAME/$DSN_CHECK_FILE"
export CLIENT_TYPE=$CLIENT_TYPE
export DB2USER_NAME=$DB2USER_NAME
export SCRIPT_URI=$SCRIPT_URI
EOFINNER
EOF

  # Make the script executable
  chmod +x "$temp_script"
  
  # Run the script as DB2USER_NAME
  log_info "Executing script $temp_script as user $DB2USER_NAME..."
  sudo -u "$DB2USER_NAME" bash -c "bash $temp_script"
  
  # Capture the result
  local result=$?
  
  # Clean up temporary files
  rm -f "$temp_script"
  
  if [ $result -eq 0 ]; then
    log_success "DB2 $CLIENT_TYPE client DSN entries startup file $DSN_CHECK_FILE created successfully as $DB2USER_NAME!"
    return 0
  else
    log_error "Failed to create DSN entries startup file $DSN_CHECK_FILE as $DB2USER_NAME"
    return 1
  fi
}

create_dsn_entries_as_db2user () {
  # Check if we're running as the DB2 user
  if [[ "$USER" != "$DB2USER_NAME" ]]; then
    log_error "This function must be run as $DB2USER_NAME user"
    return 1
  fi
  
  log_info "Executing DSN creation as $DB2USER_NAME..."
  if [ "$CLIENT_TYPE" == "DS" ]; then
    create_dsn_entries_ds
  else
    create_dsn_entries_rt
  fi
}

# =============================================================================
# Usage and Help Functions
# =============================================================================

usage() {
  #region
  cat <<EOF
Usage: $0 [OPTIONS]

AWS RDS DB2 Client Setup Script
Platform Support: Linux only (Amazon Linux, CentOS, RHEL, etc.)

QUICK START:
    # Download script with platform-specific instructions:
    curl -sL https://bit.ly/db2client | bash

    # Or download directly and run:
    curl -sL https://bit.ly/db2client -o db2client.sh
    chmod +x db2client.sh
    ./db2client.sh

    # Run with environment variables:
    CLIENT_TYPE=DS DB_INSTANCE_ID=<your-db-instance-id> ./db2client.sh
    CLIENT_TYPE=RT DB_INSTANCE_ID=<your-db-instance-id> ./db2client.sh
    CLIENT_TYPE=DS DB_INSTANCE_ID=ALL ./db2client.sh  # Process all instances

OPTIONS:
    -r, --region REGION         AWS region (default: from AWS CLI config)
    -p, --profile PROFILE       AWS profile (default: default)
    --verbose                   Enable verbose output
    --check-permissions         Check for required permissions
    --non-interactive           Disable interactive prompts (for automation)
    -h, --help                  Show this help message

EXAMPLES:
    $(basename $0)                                         # Interactive installation
    $(basename $0) --region us-west-2 --verbose            # Install with verbose output
    $(basename $0) --non-interactive                       # Skip prompts (automation)

PLATFORM REQUIREMENTS:
    This script only supports Linux platforms. It is not compatible with:
    - macOS
    - Windows
    - Windows Subsystem for Linux (WSL)
    
    For Windows, please download the DB2 client directly from IBM.

ENVIRONMENT VARIABLES:
    CLIENT_TYPE                 Client type (DS=Driver, RT=Runtime)
    DB2USER_NAME                DB2 user name (default: db2inst1)

    PROFILE                     AWS profile to use
    REGION                      AWS region to use
    DB_INSTANCE_ID              Specific DB2 instance identifier to use (use "ALL" to process all instances)
    VERBOSE                     Enable verbose output (true/false)
    INTERACTIVE_MODE            Enable interactive mode (true/false)
    CHECK_PERMISSIONS           Check for required permissions (true/false)

EOF
  #endregion
}

# =============================================================================
# Argument Parsing
# =============================================================================

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case $1 in
    -r | --region)
      REGION="$2"
      shift 2
      ;;
    -p | --profile)
      PROFILE="$2"
      shift 2
      ;;
    --verbose)
      VERBOSE=true
      shift
      ;;
    --check-permissions)
      CHECK_PERMISSIONS=true
      VERBOSE=true
      log_info "Permission check mode enabled. Will check permissions and exit."
      shift
      ;;
    --non-interactive)
      INTERACTIVE_MODE=false
      shift
      ;;
    -h | --help)
      usage
      return 1
      ;;
    *)
      log_error "Unknown option: $1"
      usage
      exit 1
      ;;
    esac
  done
}

# =============================================================================
# Common Setup Function
# =============================================================================

common_setup() {
  # Parse command line arguments
  parse_arguments "$@" || return 1

  # Detect platform  
  detect_platform

  # Detect curl pipe execution
  detect_curl_pipe_execution
  
  # Check if platform is supported
  if ! check_supported_platform; then
    log_error "Unsupported platform. Exiting..."
    return 1
  fi
  
  # Set client type (DS or RT)
  set_client_type

  # Run prerequisite checks
  if ! prereq_checks; then
    log_error "Prerequisite checks failed. Exiting..."
    return 1
  fi

  # Validate region
  if ! region_validation; then
    log_error "Region validation failed. Exiting..."
    return 1
  fi
  
  # Check permissions if requested
  if [[ "$CHECK_PERMISSIONS" == "true" ]]; then
    if ! check_permissions; then
      log_error "Permission check failed. Exiting..."
      return 1
    fi
    return 1 # We want to exit here
  fi  
  return 0
}

install_db2_driver_and_prepare_dsn_entries() {

  # Set credentials if earlier STS has expired
  set_credentials
  # Use common setup function
  if ! common_setup "$@"; then
    return 1
  fi

  # Install DB2 client
  if ! install_client; then
    log_error "DB2 client installation failed. Exiting..."
    return 1
  fi

  if ! prepare_db2user_for_dsn_entries; then
    log_error "DB2 DSN installation failed as root. Exiting..."
    return 1
  fi

  # Report elapsed time
  report_elapsed_time

  log_success "DB2 $CLIENT_TYPE - File $DSN_CHECK_FILE created successfully!"  
  log_info "============================================================================"
  log_warning "You must complete the DSN creation process as given below to be able to connect to DB2 instance:"
  log_info "1. Switch to the $DB2USER_NAME user: 'sudo su - $DB2USER_NAME'"
  log_info "2. Run the script: 'source $(basename $SCRIPT_URI)'"
  log_info "============================================================================"
}

install_dsn_entries_as_db2user() {
  # Set credentials since we are running as DB2 user
  set_credentials

  # Use common setup function
  if ! common_setup "$@"; then
    return 1
  fi
  
  # Check if client is actually installed
  if [[ "$CLIENT_TYPE" == "DS" && ! -d "$HOME/dsdriver" ]]; then
    log_error "DS Driver not found at $HOME/dsdriver. Client installation appears incomplete."
    log_error "Please run the script as root/sudo first to complete the installation."
    return 1
  elif [[ "$CLIENT_TYPE" == "RT" && ! -d "$HOME/sqllib" ]]; then
    log_error "Runtime Client not found at $HOME/sqllib. Client installation appears incomplete."
    log_error "Please run the script as root/sudo first to complete the installation."
    return 1
  fi
  
  # Create DSN entries
  if ! create_dsn_entries_as_db2user; then
    log_error "DSN entry creation failed. Exiting..."
    return 1
  fi

  print_all_help | tee $HOME/CONN_HELP_README.txt

  # Report elapsed time
  report_elapsed_time
  log_success "DB2 $CLIENT_TYPE dsn entries completed successfully!"
  log_info "============================================================================"
}

# =============================================================================
# Main Function
# =============================================================================

main() {
  # Check if we're running as the DB2 user and $DSN_CHECK_FILE exists
  if [[ "$USER" == "$DB2USER_NAME" && -f "$HOME/$DSN_CHECK_FILE" ]]; then
    log_info "Running in DSN-only mode as $DB2USER_NAME user"
    source "$HOME/$DSN_CHECK_FILE"
    install_dsn_entries_as_db2user "$@"  || return 0
  # Check if we're running as the DB2 user but $DSN_CHECK_FILE doesn't exist
  elif [[ "$USER" == "$DB2USER_NAME" && ! -f "$HOME/$DSN_CHECK_FILE" ]]; then
    log_warning "Running as $DB2USER_NAME but $DSN_CHECK_FILE file not found"
    log_info "Creating $DSN_CHECK_FILE file and proceeding with DSN creation"
    cat << EOFINNER > "/home/$DB2USER_NAME/$DSN_CHECK_FILE"
export CLIENT_TYPE=$CLIENT_TYPE
export DB2USER_NAME=$DB2USER_NAME
export SCRIPT_URI=$SCRIPT_URI
EOFINNER
    install_dsn_entries_as_db2user "$@"  || return 0
  # Running as non-DB2 user (likely with sudo)
  else
    log_info "Running in full installation mode"
    install_db2_driver_and_prepare_dsn_entries "$@" || return 0
  fi
}

# =============================================================================
# Script Entry Point
# =============================================================================

# Trap to ensure clean exit
trap 'echo "Script interrupted"; exit 130' INT TERM

# Run common setup first to ensure prerequisites are met
if common_setup "$@"; then
  main "$@"
fi

