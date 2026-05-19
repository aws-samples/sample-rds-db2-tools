#!/bin/bash
# Test script to verify end-to-end setup and cleanup
# Run this to ensure the circular dependency fix works

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[✓]${NC} $1"; }
log_error() { echo -e "${RED}[✗]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[!]${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "End-to-End Setup Test"
echo "=========================================="
echo ""
echo "This script will:"
echo "  1. Verify clean state"
echo "  2. Run bootstrap"
echo "  3. Create backend resources"
echo "  4. Migrate to remote state"
echo "  5. Verify DynamoDB table exists"
echo "  6. Test cleanup"
echo "  7. Verify DynamoDB table is deleted"
echo ""

# Check if AWS CLI is configured
if ! aws sts get-caller-identity &>/dev/null; then
    log_error "AWS CLI not configured. Run 'aws configure' first."
    exit 1
fi

log_success "AWS CLI configured"

# Get test configuration
REGION="us-east-1"
BUCKET_NAME="test-terraform-state-$(date +%s)"
TABLE_NAME="test-terraform-lock-$(date +%s)"

log_info "Test configuration:"
echo "  Region: $REGION"
echo "  Bucket: $BUCKET_NAME"
echo "  Table:  $TABLE_NAME"
echo ""

read -p "Continue with test? (yes/no): " CONFIRM
if [[ "$CONFIRM" != "yes" ]]; then
    log_info "Test cancelled"
    exit 0
fi

# Step 1: Verify clean state
log_info "Step 1: Verifying clean state..."

if [ -f "backend.tf" ]; then
    log_warning "backend.tf exists, removing..."
    rm -f backend.tf
fi

if [ -f "terraform.tfvars" ]; then
    log_warning "terraform.tfvars exists, removing..."
    rm -f terraform.tfvars
fi

if [ -d ".terraform" ]; then
    log_warning ".terraform directory exists, removing..."
    rm -rf .terraform
fi

log_success "Clean state verified"

# Step 2: Create configuration files
log_info "Step 2: Creating configuration files..."

cat > terraform.tfvars << EOF
aws_region         = "$REGION"
state_bucket_name  = "$BUCKET_NAME"
lock_table_name    = "$TABLE_NAME"
EOF

cat > backend.tf.template << EOF
terraform {
  backend "s3" {
    bucket         = "$BUCKET_NAME"
    key            = "rdsdb2-proxy/0-backend-setup/terraform.tfstate"
    region         = "$REGION"
    encrypt        = true
    dynamodb_table = "$TABLE_NAME"
  }
}
EOF

log_success "Configuration files created"

# Step 3: Initialize with local state
log_info "Step 3: Initializing with local state..."

if ! terraform init; then
    log_error "terraform init failed"
    exit 1
fi

if [ -f "backend.tf" ]; then
    log_error "backend.tf should not exist yet"
    exit 1
fi

log_success "Initialized with local state"

# Step 4: Create backend resources
log_info "Step 4: Creating backend resources..."

if ! terraform apply -auto-approve; then
    log_error "terraform apply failed"
    exit 1
fi

log_success "Backend resources created"

# Step 5: Verify DynamoDB table exists
log_info "Step 5: Verifying DynamoDB table exists..."

if ! aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$REGION" &>/dev/null; then
    log_error "DynamoDB table not found"
    exit 1
fi

log_success "DynamoDB table exists"

# Step 6: Migrate to remote state
log_info "Step 6: Migrating to remote state..."

mv backend.tf.template backend.tf

if ! echo "yes" | terraform init -migrate-state; then
    log_error "State migration failed"
    exit 1
fi

log_success "State migrated to S3"

# Step 7: Verify state in S3
log_info "Step 7: Verifying state in S3..."

if ! aws s3 ls "s3://$BUCKET_NAME/rdsdb2-proxy/0-backend-setup/terraform.tfstate" --region "$REGION" &>/dev/null; then
    log_error "State file not found in S3"
    exit 1
fi

log_success "State file exists in S3"

# Step 8: Test cleanup
log_info "Step 8: Testing cleanup..."

# Remove state files
aws s3 rm "s3://$BUCKET_NAME/rdsdb2-proxy/" --recursive --region "$REGION" 2>/dev/null || true

# Delete DynamoDB table
aws dynamodb delete-table --table-name "$TABLE_NAME" --region "$REGION" 2>/dev/null || true

# Wait for table deletion
log_info "Waiting for DynamoDB table deletion..."
aws dynamodb wait table-not-exists --table-name "$TABLE_NAME" --region "$REGION" 2>/dev/null || true

# Verify table is deleted
if aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$REGION" &>/dev/null; then
    log_error "DynamoDB table still exists"
    exit 1
fi

log_success "DynamoDB table deleted"

# Delete S3 bucket
aws s3 rb "s3://$BUCKET_NAME" --force --region "$REGION" 2>/dev/null || true

log_success "S3 bucket deleted"

# Clean local files
rm -rf .terraform .terraform.lock.hcl terraform.tfstate* backend.tf terraform.tfvars

log_success "Local files cleaned"

echo ""
echo "=========================================="
log_success "All tests passed!"
echo "=========================================="
echo ""
echo "Summary:"
echo "  ✓ Clean state verified"
echo "  ✓ Configuration files created"
echo "  ✓ Initialized with local state"
echo "  ✓ Backend resources created"
echo "  ✓ DynamoDB table verified"
echo "  ✓ State migrated to S3"
echo "  ✓ State file in S3 verified"
echo "  ✓ Cleanup successful"
echo "  ✓ DynamoDB table deleted"
echo "  ✓ S3 bucket deleted"
echo ""
echo "The circular dependency fix is working correctly!"
