# Troubleshooting Guide

## Plugin Cache

### Issue: Terraform still "Installing" providers

**Symptom**: `terraform init` shows "Installing hashicorp/aws..." even with plugin cache

**Explanation**: This is normal behavior with plugin cache:
- Terraform displays "Installing" message
- Actually creates symlinks from cache (~5s)
- Not re-downloading (~30-60s)

**Verify cache is working**:
```bash
ls -la .terraform/providers/registry.terraform.io/hashicorp/aws/
# Should show symlinks (l) pointing to ~/.terraform.d/plugin-cache/
```

## Cleanup Script

### Issue: S3 bucket not deleted after cleanup

**Explanation**: This is intentional behavior:
- `cleanup.sh` only removes `s3://bucket/rdsdb2-proxy/*` prefix
- Bucket and other data preserved
- Allows bucket reuse for other projects

**To delete bucket manually**:
```bash
aws s3 rb s3://YOUR_BUCKET --force
```

### Issue: DynamoDB table still exists

**Explanation**: 
- `cleanup.sh` deletes DynamoDB table
- If it still exists, cleanup may have failed

**Manual deletion**:
```bash
aws dynamodb delete-table --table-name YOUR_TABLE_NAME
```

## State Management

### Issue: Resource already exists error

**Symptom**: `Error: resource already exists` during apply

**Solution**: Import existing resource
```bash
terraform import RESOURCE_TYPE.NAME RESOURCE_ID

# Example:
terraform import aws_s3_bucket.terraform_state my-bucket-name
```

### Issue: State file out of sync

**Solution**: Refresh state
```bash
terraform refresh
terraform plan  # Verify changes
```

## Module Dependencies

### Issue: Module can't find outputs from previous module

**Symptom**: `Error: Unsupported attribute` when reading terraform_remote_state

**Causes**:
1. Previous module not deployed
2. Backend not configured
3. State file doesn't exist

**Solution**:
```bash
# Verify previous module deployed
cd ../PREVIOUS_MODULE
terraform output

# Verify backend configured
cat backend.tf

# Re-run configure-modules.sh
cd ../0-backend-setup
./configure-modules.sh
```

## Multi-Port Issues

### Issue: Port not listening after adding mapping

**Symptom**: Health check shows port missing

**Causes**:
1. Cron job hasn't run yet (runs every 5 minutes)
2. Nginx config update failed

**Solution**:
```bash
# Connect to EC2
INSTANCE_ID=$(cd 2-infrastructure && terraform output -raw ec2_instance_id)
aws ssm start-session --target $INSTANCE_ID

# Force immediate update
sudo /usr/local/bin/update-nginx-config.sh

# Check logs
sudo tail -f /var/log/nginx-config-update.log

# Verify port listening
sudo netstat -tlnp | grep :PORT
```

### Issue: Target group unhealthy for new port

**Causes**:
1. Port not listening on EC2
2. Security group blocking traffic
3. Nginx config error

**Solution**:
```bash
# Check nginx config
sudo openresty -t -c /etc/openresty/proxy.conf

# Check security group allows traffic
# EC2 SG must allow inbound from NLB on all ports

# Restart OpenResty
sudo systemctl restart openresty
```

## Certificate Issues

### Issue: Certificate not found on EC2

**Symptom**: OpenResty fails to start, certificate errors in logs

**Solution**:
```bash
# Check certificates exist
sudo ls -la /etc/openresty/certs/

# If missing, re-run user_data
sudo bash /var/lib/cloud/instance/scripts/part-001

# Or manually fetch from Secrets Manager
SECRET_ARN=$(cd ../1-prerequisites && terraform output -raw certificate_secret_arn)
aws secretsmanager get-secret-value --secret-id $SECRET_ARN
```

## Performance

### Issue: terraform init takes 30-60 seconds

**Cause**: Plugin cache not configured

**Solution**:
```bash
./setup-plugin-cache.sh
```

### Issue: configure-infrastructure.sh takes several minutes

**Explanation**: First run initializes Terraform and queries AWS
- Subsequent runs are faster
- Uses Terraform data sources (not AWS CLI)
- Normal behavior

## Common Errors

### Error: "Backend configuration changed"

**Solution**:
```bash
terraform init -reconfigure
```

### Error: "Error locking state"

**Cause**: Previous terraform operation didn't complete

**Solution**:
```bash
# Get lock ID from error message
terraform force-unlock LOCK_ID
```

### Error: "No valid credential sources found"

**Cause**: AWS credentials not configured

**Solution**:
```bash
aws configure
# Or set environment variables:
export AWS_ACCESS_KEY_ID=xxx
export AWS_SECRET_ACCESS_KEY=xxx
export AWS_SESSION_TOKEN=xxx  # If using temporary credentials
```

## Getting Help

1. Check module-specific TROUBLESHOOTING.md
2. Review module outputs: `terraform output`
3. Check AWS Console for resource state
4. Review Terraform state: `terraform show`
5. Enable debug logging: `export TF_LOG=DEBUG`
