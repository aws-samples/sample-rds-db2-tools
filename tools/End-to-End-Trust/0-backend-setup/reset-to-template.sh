#!/bin/bash
# Reset all backend configuration files to pristine template state
# Run this before committing to GitHub

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "🔄 Resetting backend configuration files to template state..."
echo ""

# Reset 0-backend-setup (remove generated files)
rm -f backend.tf backend.tf.template terraform.tfvars cleanup_backend.tf
rm -rf .terraform .terraform.lock.hcl terraform.tfstate terraform.tfstate.backup

echo "✓ Removed 0-backend-setup/backend.tf (regenerate with bootstrap-backend.sh)"
echo "✓ Removed 0-backend-setup/backend.tf.template (regenerate with bootstrap-backend.sh)"
echo "✓ Removed 0-backend-setup/terraform.tfvars (regenerate with bootstrap-backend.sh)"
echo "✓ Removed 0-backend-setup/cleanup_backend.tf (if exists)"
echo "✓ Removed 0-backend-setup/.terraform directory (if exists)"
echo "✓ Removed 0-backend-setup/terraform.tfstate* (if exists)"

cd ..

# Reset 1-prerequisites
cat > 1-prerequisites/backend.tf << 'EOF'
# Backend configuration for Terraform state
# Auto-configured by 0-backend-setup/configure-modules.sh

terraform {
  backend "s3" {
    bucket         = "REPLACE_BUCKET_NAME"
    key            = "rdsdb2-proxy/1-prerequisites/terraform.tfstate"
    region         = "REPLACE_REGION"
    encrypt        = true
    dynamodb_table = "REPLACE_DYNAMODB_TABLE"
  }
}
EOF

cat > 1-prerequisites/backend-config.tf << 'EOF'
# Fetch configuration from backend-setup module
# Auto-configured by 0-backend-setup/configure-modules.sh

data "terraform_remote_state" "backend_setup" {
  backend = "s3"

  config = {
    bucket = "REPLACE_BUCKET_NAME"
    key    = "rdsdb2-proxy/0-backend-setup/terraform.tfstate"
    region = "REPLACE_REGION"
  }
}
EOF

# Reset 2-infrastructure
cat > 2-infrastructure/backend.tf << 'EOF'
# Backend configuration for Terraform state
# Auto-configured by 0-backend-setup/configure-modules.sh

terraform {
  backend "s3" {
    bucket         = "REPLACE_BUCKET_NAME"
    key            = "rdsdb2-proxy/2-infrastructure/terraform.tfstate"
    region         = "REPLACE_REGION"
    encrypt        = true
    dynamodb_table = "REPLACE_DYNAMODB_TABLE"
  }
}
EOF

cat > 2-infrastructure/backend-config.tf << 'EOF'
# Fetch configuration from backend-setup module
# Auto-configured by 0-backend-setup/configure-modules.sh

data "terraform_remote_state" "backend_setup" {
  backend = "s3"

  config = {
    bucket = "REPLACE_BUCKET_NAME"
    key    = "rdsdb2-proxy/0-backend-setup/terraform.tfstate"
    region = "REPLACE_REGION"
  }
}
EOF

cat > 2-infrastructure/data.tf << 'EOF'
# Data source to fetch outputs from prerequisites module
# Auto-configured by 0-backend-setup/configure-modules.sh

data "terraform_remote_state" "prerequisites" {
  backend = "s3"

  config = {
    bucket = "REPLACE_BUCKET_NAME"
    key    = "rdsdb2-proxy/1-prerequisites/terraform.tfstate"
    region = "REPLACE_REGION"
  }
}
EOF

# Reset 3-mappings
cat > 3-mappings/backend.tf << 'EOF'
# Backend configuration for Terraform state
# Auto-configured by 0-backend-setup/configure-modules.sh

terraform {
  backend "s3" {
    bucket         = "REPLACE_BUCKET_NAME"
    key            = "rdsdb2-proxy/3-mappings/terraform.tfstate"
    region         = "REPLACE_REGION"
    encrypt        = true
    dynamodb_table = "REPLACE_DYNAMODB_TABLE"
  }
}
EOF

cat > 3-mappings/backend-config.tf << 'EOF'
# Fetch configuration from backend-setup module
# Auto-configured by 0-backend-setup/configure-modules.sh

data "terraform_remote_state" "backend_setup" {
  backend = "s3"

  config = {
    bucket = "REPLACE_BUCKET_NAME"
    key    = "rdsdb2-proxy/0-backend-setup/terraform.tfstate"
    region = "REPLACE_REGION"
  }
}
EOF

echo ""
echo "✅ All backend configuration files reset to template state"
echo ""
echo "Files updated:"
echo "  - 0-backend-setup/backend.tf (REMOVED - regenerate with bootstrap-backend.sh)"
echo "  - 0-backend-setup/terraform.tfvars (REMOVED - regenerate with bootstrap-backend.sh)"
echo "  - 1-prerequisites/backend.tf"
echo "  - 1-prerequisites/backend-config.tf"
echo "  - 2-infrastructure/backend.tf"
echo "  - 2-infrastructure/backend-config.tf"
echo "  - 2-infrastructure/data.tf"
echo "  - 3-mappings/backend.tf"
echo "  - 3-mappings/backend-config.tf"
echo ""
echo "Ready to commit to GitHub!"
echo "To redeploy: Run ./bootstrap-backend.sh in 0-backend-setup"
