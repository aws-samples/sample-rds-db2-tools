#!/usr/bin/env bash
# configure-modules.sh
# Reads 0-backend-setup outputs and writes backend.tf in all modules.
# Run once after deploying 0-backend-setup.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/0-backend-setup"

echo "Reading backend configuration from 0-backend-setup..."
BUCKET=$(terraform output -raw state_bucket_name)
REGION=$(terraform output -raw aws_region)
TABLE=$(terraform output -raw lock_table_name)

MODULES=(
  "1-networking"
  "2-iam"
  "3-kms"
  "4-parameter-group"
  "5-rds"
  "6-license-manager"
)

for module in "${MODULES[@]}"; do
  KEY="rds-db2/${module}/terraform.tfstate"
  BACKEND_FILE="$SCRIPT_DIR/$module/backend.tf"
  cat > "$BACKEND_FILE" <<EOF
terraform {
  backend "s3" {
    bucket         = "$BUCKET"
    key            = "$KEY"
    region         = "$REGION"
    dynamodb_table = "$TABLE"
    encrypt        = true
  }
}
EOF
  echo "  ✓ Configured $module/backend.tf"
done

echo ""
echo "All modules configured. Next steps:"
echo "  1. Copy terraform.tfvars.example to terraform.tfvars in each module"
echo "  2. Fill in your values"
echo "  3. Deploy in order: 1-networking → 2-iam → 3-kms → 4-parameter-group → 5-rds → 6-license-manager"
