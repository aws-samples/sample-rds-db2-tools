# 0-Backend-Setup Troubleshooting

## Bootstrap Script Issues

### Issue: bootstrap-backend.sh fails with "command not found"

**Cause**: Script not executable

**Solution**:
```bash
chmod +x bootstrap-backend.sh
./bootstrap-backend.sh
```

### Issue: Script creates files but terraform fails

**Cause**: Invalid input (special characters in bucket name)

**Solution**:
- Bucket names must be DNS-compliant
- Only lowercase letters, numbers, hyphens
- No underscores, spaces, or special characters
- Must be globally unique

**Re-run**:
```bash
rm terraform.tfvars backend.tf
./bootstrap-backend.sh
```

## S3 Bucket Issues

### Error: "BucketAlreadyExists"

**Cause**: Bucket name taken globally

**Solution**:
```bash
# Use unique prefix
./bootstrap-backend.sh
# Enter: mycompany-terraform-state-12345
```

### Error: "BucketAlreadyOwnedByYou"

**Cause**: Bucket exists in your account

**Solution**: Import existing bucket
```bash
terraform import aws_s3_bucket.terraform_state YOUR_BUCKET_NAME
terraform apply --auto-approve --auto-approve --auto-approve --auto-approve
```

### Issue: Bucket not deleted after cleanup

**Explanation**: Intentional behavior
- `cleanup.sh` preserves bucket
- Only removes `rdsdb2-proxy/*` prefix
- Allows bucket reuse

**To delete manually**:
```bash
aws s3 rb s3://YOUR_BUCKET --force
```

## DynamoDB Issues

### Error: "ResourceInUseException"

**Cause**: Table already exists

**Solution**: Import existing table
```bash
terraform import aws_dynamodb_table.terraform_lock YOUR_TABLE_NAME
terraform apply --auto-approve
```

### Issue: State locking fails

**Symptom**: "Error acquiring the state lock"

**Causes**:
1. Previous operation didn't complete
2. DynamoDB table doesn't exist
3. Insufficient permissions

**Solution**:
```bash
# Check table exists
aws dynamodb describe-table --table-name YOUR_TABLE_NAME

# Force unlock (use lock ID from error)
terraform force-unlock LOCK_ID

# Verify permissions
aws dynamodb put-item --table-name YOUR_TABLE_NAME --item '{"LockID":{"S":"test"}}'
```

## Backend Configuration Issues

### Issue: configure-modules.sh fails

**Symptom**: "Error: No outputs found"

**Cause**: Module 0 not deployed yet

**Solution**:
```bash
# Deploy module 0 first
terraform init
terraform apply --auto-approve

# Then configure other modules
./configure-modules.sh
```

### Issue: Modules still have placeholder values

**Cause**: configure-modules.sh not run after bootstrap

**Solution**:
```bash
./configure-modules.sh
```

### Issue: Backend config doesn't match

**Symptom**: "Backend configuration changed"

**Solution**:
```bash
cd MODULE_DIR
terraform init -reconfigure
```

## State File Issues

### Issue: Module 0 state lost after cleanup

**Explanation**: Should not happen - module 0 uses S3 backend

**Verify**:
```bash
cat backend.tf
# Should show S3 backend, not local

aws s3 ls s3://YOUR_BUCKET/rdsdb2-proxy/0-backend-setup/
# Should show terraform.tfstate
```

**Recovery**:
```bash
# Re-import resources
terraform import aws_s3_bucket.terraform_state YOUR_BUCKET
terraform import aws_dynamodb_table.terraform_lock YOUR_TABLE
```

## Permission Issues

### Error: "AccessDenied" creating S3 bucket

**Required permissions**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket",
        "s3:PutBucketVersioning",
        "s3:PutEncryptionConfiguration",
        "s3:PutBucketPublicAccessBlock"
      ],
      "Resource": "arn:aws:s3:::*"
    }
  ]
}
```

### Error: "AccessDenied" creating DynamoDB table

**Required permissions**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:CreateTable",
        "dynamodb:DescribeTable",
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:DeleteItem"
      ],
      "Resource": "arn:aws:dynamodb:*:*:table/*"
    }
  ]
}
```

## Reset Issues

### Issue: reset-to-template.sh doesn't clean everything

**Explanation**: Only resets module 0 and modules 1-3
- Module 4 (health-check) has no sensitive data
- .terraform directories preserved (contains providers)

**Manual cleanup**:
```bash
# Remove all .terraform directories
find . -type d -name .terraform -exec rm -rf {} +

# Remove lock files
find . -name .terraform.lock.hcl -delete
```

## Verification

### Verify backend setup successful

```bash
# Check S3 bucket
aws s3 ls s3://YOUR_BUCKET/rdsdb2-proxy/

# Check DynamoDB table
aws dynamodb describe-table --table-name YOUR_TABLE

# Check module 0 state
terraform show

# Verify other modules configured
for dir in ../1-prerequisites ../2-infrastructure ../3-mappings; do
  echo "=== $dir ==="
  cat $dir/backend.tf
done
```

### Test state locking

```bash
# In one terminal
terraform apply --auto-approve
# Don't confirm yet

# In another terminal
terraform plan
# Should show: "Error acquiring the state lock"
```
