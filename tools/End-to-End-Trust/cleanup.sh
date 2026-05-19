#!/bin/bash
# Cleanup script for Terraform-managed RDS Proxy infrastructure
# Destroys all resources and resets to pristine state

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log_info "RDS Proxy Terraform Cleanup"
echo "=========================================="
echo "This will:"
echo "  1. Destroy all resources (mappings, infrastructure, prerequisites)"
echo "  2. Remove S3 state files (rdsdb2-proxy/* prefix only)"
echo "  3. Delete DynamoDB table"
echo "  4. Clean local Terraform files"
echo "  5. Reset backend configs to template state"
echo ""
echo "Note: S3 bucket will be preserved (only rdsdb2-proxy/* removed)"
echo "=========================================="
echo ""

read -p "Continue? (yes/no): " CONFIRM

if [[ "$CONFIRM" != "yes" ]]; then
    log_info "Operation cancelled"
    exit 0
fi

# Destroy infrastructure in reverse order
log_info "Step 1/4: Destroying RDS mappings..."
if [ -d "$SCRIPT_DIR/3-mappings" ]; then
    cd "$SCRIPT_DIR/3-mappings"
    if [ -f "backend.tf" ] && terraform state list &>/dev/null 2>&1; then
        terraform destroy -auto-approve || log_warning "Failed to destroy mappings (may not exist)"
    else
        log_warning "No state found for mappings, skipping"
    fi
fi

log_info "Step 2/4: Destroying infrastructure..."
if [ -d "$SCRIPT_DIR/2-infrastructure" ]; then
    cd "$SCRIPT_DIR/2-infrastructure"
    if [ -f "backend.tf" ] && terraform state list &>/dev/null 2>&1; then
        terraform destroy -auto-approve || log_warning "Failed to destroy infrastructure"
    else
        log_warning "No state found for infrastructure, skipping"
    fi
fi

log_info "Step 3/4: Destroying prerequisites..."
if [ -d "$SCRIPT_DIR/1-prerequisites" ]; then
    cd "$SCRIPT_DIR/1-prerequisites"
    if [ -f "backend.tf" ] && terraform state list &>/dev/null 2>&1; then
        terraform destroy -auto-approve || log_warning "Failed to destroy prerequisites"
    else
        log_warning "No state found for prerequisites, skipping"
    fi
fi


log_info "Step 4/4: Cleaning backend resources..."
if [ -d "$SCRIPT_DIR/0-backend-setup" ]; then
    cd "$SCRIPT_DIR/0-backend-setup"
    
    # Get resource names from outputs or state
    BUCKET_NAME=$(terraform output -raw s3_bucket_name 2>/dev/null || echo "")
    TABLE_NAME=$(terraform output -raw dynamodb_table_name 2>/dev/null || echo "")
    REGION=$(terraform output -raw aws_region 2>/dev/null || grep 'region' backend.tf 2>/dev/null | grep -v '#' | sed 's/.*"\(.*\)".*/\1/' | head -1 || echo "us-east-1")
    
    if [ -n "$BUCKET_NAME" ] && [ -n "$TABLE_NAME" ] && [ -n "$REGION" ]; then
        log_info "Removing state files from: s3://$BUCKET_NAME/rdsdb2-proxy/"
        aws s3 rm "s3://$BUCKET_NAME/rdsdb2-proxy/" --recursive --region "$REGION" 2>/dev/null || log_warning "Failed to remove S3 state files"
        
        log_info "Deleting DynamoDB table: $TABLE_NAME"
        aws dynamodb delete-table --table-name "$TABLE_NAME" --region "$REGION" 2>/dev/null || log_warning "Failed to delete DynamoDB table (may not exist)"
        
        # Wait for table deletion
        log_info "Waiting for DynamoDB table deletion..."
        aws dynamodb wait table-not-exists --table-name "$TABLE_NAME" --region "$REGION" 2>/dev/null || true
        
        log_success "State files removed from s3://$BUCKET_NAME/rdsdb2-proxy/"
        log_success "DynamoDB table '$TABLE_NAME' deleted"
        log_warning "S3 bucket '$BUCKET_NAME' preserved (may contain other data)"
    else
        log_warning "Could not retrieve bucket/table names from Terraform outputs"
        log_info "To manually clean up:"
        log_info "  aws s3 rm s3://YOUR_BUCKET/rdsdb2-proxy/ --recursive"
        log_info "  aws dynamodb delete-table --table-name YOUR_TABLE"
    fi
fi

log_info "Cleaning local Terraform files..."
cd "$SCRIPT_DIR"

# Remove state files and .terraform directories from all modules
for dir in 0-backend-setup 1-prerequisites 2-infrastructure 3-mappings 4-health-check; do
    if [ -d "$dir" ]; then
        log_info "Cleaning $dir..."
        rm -rf "$dir/terraform.tfstate"* "$dir/.terraform" "$dir/.terraform.lock.hcl"
        rm -f "$dir/terraform.tfvars" "$dir/backend.tf.template"
        
        # Verify cleanup
        if [ -d "$dir/.terraform" ]; then
            log_warning "Failed to remove $dir/.terraform"
        fi
    fi
done

# Clean helpers directory
if [ -d "2-infrastructure/helpers" ]; then
    log_info "Cleaning 2-infrastructure/helpers..."
    rm -rf 2-infrastructure/helpers/terraform.tfstate* \
           2-infrastructure/helpers/.terraform \
           2-infrastructure/helpers/.terraform.lock.hcl \
           2-infrastructure/helpers/terraform.tfvars
    
    # Verify cleanup
    if [ -d "2-infrastructure/helpers/.terraform" ]; then
        log_warning "Failed to remove 2-infrastructure/helpers/.terraform"
    fi
fi

log_success "Local Terraform files cleaned"

log_info "Resetting backend configurations to template state..."
cd "$SCRIPT_DIR/0-backend-setup"
./reset-to-template.sh

log_success "Cleanup complete!"
echo ""
echo "Repository is now in pristine state."
echo ""
echo "To re-deploy:"
echo "  cd 0-backend-setup"
echo "  ./bootstrap-backend.sh"
echo "  terraform init                    # Uses local state"
echo "  terraform apply --auto-approve"
echo "  mv backend.tf.template backend.tf"
echo "  terraform init -migrate-state     # Migrates to remote state"
echo "  ./configure-modules.sh"
echo "  # Then deploy modules 1-4"

