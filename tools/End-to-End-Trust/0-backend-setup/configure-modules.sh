#!/bin/bash
# Auto-configure backend settings in all modules after backend-setup

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Get values from Terraform outputs
BUCKET_NAME=$(terraform output -raw s3_bucket_name 2>/dev/null)
REGION=$(terraform output -raw aws_region 2>/dev/null)
DYNAMODB_TABLE=$(terraform output -raw dynamodb_table_name 2>/dev/null)

if [ -z "$BUCKET_NAME" ] || [ -z "$REGION" ] || [ -z "$DYNAMODB_TABLE" ]; then
    echo "❌ Error: Could not read Terraform outputs. Run 'terraform apply' first."
    exit 1
fi

echo "📝 Configuring modules with:"
echo "   Bucket: $BUCKET_NAME"
echo "   Region: $REGION"
echo "   DynamoDB: $DYNAMODB_TABLE"
echo "   Prefix: rdsdb2-proxy/"
echo ""

# Update each module (NOT 0-backend-setup, it's managed by bootstrap-backend.sh)
for module in 1-prerequisites 2-infrastructure 3-mappings; do
    echo "Updating $module..."
    
    # Update backend.tf
    sed -i.bak "s/bucket[[:space:]]*=[[:space:]]*\"REPLACE_BUCKET_NAME\"/bucket         = \"$BUCKET_NAME\"/" "../$module/backend.tf"
    sed -i.bak "s/region[[:space:]]*=[[:space:]]*\"REPLACE_REGION\"/region         = \"$REGION\"/" "../$module/backend.tf"
    sed -i.bak "s/dynamodb_table[[:space:]]*=[[:space:]]*\"REPLACE_DYNAMODB_TABLE\"/dynamodb_table = \"$DYNAMODB_TABLE\"/" "../$module/backend.tf"
    
    # Update backend-config.tf (if exists)
    if [ -f "../$module/backend-config.tf" ]; then
        sed -i.bak "s/bucket[[:space:]]*=[[:space:]]*\"REPLACE_BUCKET_NAME\"/bucket = \"$BUCKET_NAME\"/" "../$module/backend-config.tf"
        sed -i.bak "s/region[[:space:]]*=[[:space:]]*\"REPLACE_REGION\"/region = \"$REGION\"/" "../$module/backend-config.tf"
    fi
    
    # Update data.tf (if exists)
    if [ -f "../$module/data.tf" ]; then
        sed -i.bak "s/bucket[[:space:]]*=[[:space:]]*\"REPLACE_BUCKET_NAME\"/bucket = \"$BUCKET_NAME\"/" "../$module/data.tf"
        sed -i.bak "s/region[[:space:]]*=[[:space:]]*\"REPLACE_REGION\"/region = \"$REGION\"/" "../$module/data.tf"
    fi
    
    # Remove backup files
    rm -f "../$module/backend.tf.bak" "../$module/backend-config.tf.bak" "../$module/data.tf.bak"
done

echo ""
echo "✅ All modules configured successfully!"
echo ""
echo "Next steps:"
echo "  cd ../1-prerequisites"
echo "  terraform init"
echo "  terraform apply --auto-approve"
