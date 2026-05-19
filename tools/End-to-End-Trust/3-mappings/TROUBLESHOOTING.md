# 3-Mappings Troubleshooting

## Mapping Configuration Issues

### Issue: Invalid mapping format

**Symptom**: "Error: Invalid value for variable"

**Correct format**:
```hcl
rds_mappings = {
  "client-domain:client-port" = "rds-endpoint:rds-port"
}
```

**Common mistakes**:
```hcl
# Wrong: Missing port in key
"db1.domain.com" = "rds1.amazonaws.com:1443"

# Wrong: Port not a number
"db1.domain.com:abc" = "rds1.amazonaws.com:1443"

# Wrong: Missing port in value
"db1.domain.com:1443" = "rds1.amazonaws.com"

# Correct:
"db1.domain.com:1443" = "rds1.amazonaws.com:1443"
```

### Issue: Port extraction fails

**Symptom**: "Error: Invalid index" or "Error: Invalid function argument"

**Cause**: Mapping key doesn't contain colon

**Solution**: Ensure all keys have format `domain:port`
```hcl
rds_mappings = {
  "db1.domain.com:1443" = "rds1.amazonaws.com:1443"  # Correct
}
```

## NLB Listener Issues

### Issue: Listener not created

**Symptom**: No listener on NLB for expected port

**Causes**:
1. Mapping not applied yet
2. Port extraction failed
3. Duplicate port (only one listener per port)

**Solution**:
```bash
# Verify mappings applied
terraform output mappings

# Check what ports Terraform extracted
terraform output managed_ports

# Verify listeners
NLB_ARN=$(cd ../2-infrastructure && terraform output -raw nlb_arn)
aws elbv2 describe-listeners --load-balancer-arn $NLB_ARN
```

### Issue: Multiple mappings same port

**Symptom**: Only one mapping works per port

**Explanation**: This is correct behavior
- One NLB listener per port
- Multiple domains can use same port
- SNI routing differentiates by domain name

**Example (correct)**:
```hcl
rds_mappings = {
  "db1.domain.com:1443" = "rds1.amazonaws.com:1443"
  "db2.domain.com:1443" = "rds2.amazonaws.com:50443"  # Same client port, different RDS
}
```

## Target Group Issues

### Issue: Target group unhealthy

**Causes**:
1. Port not listening on EC2 yet (cron runs every 5 min)
2. Nginx config error
3. Security group blocking traffic

**Solution**:
```bash
# Check target group health
TG_ARN=$(terraform output -raw target_groups | jq -r '.["1443"]')
aws elbv2 describe-target-health --target-group-arn $TG_ARN

# Connect to EC2
INSTANCE_ID=$(cd ../2-infrastructure && terraform output -raw ec2_instance_id)
aws ssm start-session --target $INSTANCE_ID

# Force nginx config update
sudo /usr/local/bin/update-nginx-config.sh

# Check port listening
sudo netstat -tlnp | grep :1443

# Check nginx config
sudo openresty -t -c /etc/openresty/proxy.conf

# Restart OpenResty
sudo systemctl restart openresty
```

### Issue: Target not registered

**Symptom**: Target group has no targets

**Cause**: EC2 instance ID changed or attachment failed

**Solution**:
```bash
# Verify EC2 instance exists
cd ../2-infrastructure
terraform output ec2_instance_id

# Re-apply to register target
cd ../3-mappings
terraform apply --auto-approve
```

## SSM Parameter Issues

### Issue: Parameter not created

**Symptom**: `/rds/proxy/mappings/<domain>` doesn't exist

**Solution**:
```bash
# Verify parameter
aws ssm get-parameter --name /rds/proxy/mappings/<domain>

# Re-apply
terraform apply --auto-approve
```

### Issue: Parameter not updating on EC2

**Symptom**: Nginx config doesn't reflect new mappings

**Causes**:
1. Cron job hasn't run (runs every 5 minutes)
2. Update script failing
3. Permissions issue

**Solution**:
```bash
# Connect to EC2
aws ssm start-session --target $INSTANCE_ID

# Check cron job exists
sudo cat /etc/cron.d/nginx-update

# Check update log
sudo tail -f /var/log/nginx-config-update.log

# Force immediate update
sudo /usr/local/bin/update-nginx-config.sh

# Check for errors
echo $?  # Should be 0

# Verify SSM parameter accessible
aws ssm get-parameter --name /rds/proxy/mappings/<domain> --query 'Parameter.Value'
```

### Issue: Update script fails with permission error

**Cause**: EC2 IAM role missing SSM permissions

**Solution**:
```bash
# Check IAM role
INSTANCE_ID=$(cd ../2-infrastructure && terraform output -raw ec2_instance_id)
ROLE_NAME=$(aws ec2 describe-instances --instance-ids $INSTANCE_ID \
  | jq -r '.Reservations[0].Instances[0].IamInstanceProfile.Arn' \
  | cut -d'/' -f2)

# Verify role has SSM permissions
aws iam list-attached-role-policies --role-name $ROLE_NAME

# Should include: AmazonSSMManagedInstanceCore
```

## Port Listening Issues

### Issue: Port not listening after mapping added

**Symptom**: `netstat` doesn't show port

**Causes**:
1. Cron hasn't run yet (wait up to 5 minutes)
2. Nginx config update failed
3. OpenResty not reloaded

**Solution**:
```bash
# Force update
sudo /usr/local/bin/update-nginx-config.sh

# Check nginx config
sudo cat /etc/openresty/proxy.conf | grep -A5 "listen.*1443"

# Test config
sudo openresty -t -c /etc/openresty/proxy.conf

# Reload OpenResty
sudo systemctl reload openresty

# Verify port listening
sudo netstat -tlnp | grep :1443
```

### Issue: Wrong port listening

**Symptom**: Port 8443 listening instead of 1443

**Cause**: Nginx config not updated with mappings

**Solution**:
```bash
# Check SSM parameter
aws ssm get-parameter --name /rds/proxy/mappings/<domain>

# Force update
sudo /usr/local/bin/update-nginx-config.sh

# Verify config updated
sudo grep -c "listen.*1443" /etc/openresty/proxy.conf
# Should be > 0
```

## State Issues

### Issue: Can't read 2-infrastructure outputs

**Symptom**: "Error: Unsupported attribute" for nlb_arn or ec2_instance_id

**Cause**: terraform_remote_state can't access 2-infrastructure state

**Solution**:
```bash
# Verify 2-infrastructure deployed
cd ../2-infrastructure
terraform output

# Verify backend configured
cat backend.tf

# Verify state file exists
BUCKET=$(grep bucket backend.tf | cut -d'"' -f2)
aws s3 ls s3://$BUCKET/rdsdb2-proxy/2-infrastructure/

# Re-initialize 3-mappings
cd ../3-mappings
terraform init -reconfigure
```

## Multi-Port Issues

### Issue: Adding port doesn't create listener

**Symptom**: New port in mapping but no NLB listener

**Cause**: Port not unique or extraction failed

**Debug**:
```bash
# Check managed ports output
terraform output managed_ports
# Should show all unique ports

# Check plan
terraform plan
# Should show listener and target group to be created

# Apply with debug
TF_LOG=DEBUG terraform apply
```

### Issue: Removing mapping doesn't remove listener

**Explanation**: Correct behavior
- Listener removed when ALL mappings for that port removed
- If any mapping uses port, listener remains

**Example**:
```hcl
# Before:
rds_mappings = {
  "db1.domain.com:1443" = "rds1.amazonaws.com:1443"
  "db2.domain.com:1443" = "rds2.amazonaws.com:50443"
}

# After removing db1:
rds_mappings = {
  "db2.domain.com:1443" = "rds2.amazonaws.com:50443"
}
# Listener on 1443 still exists (used by db2)

# After removing db2:
rds_mappings = {}
# Now listener on 1443 removed
```

## Nginx Configuration Issues

### Issue: Nginx config syntax error

**Symptom**: OpenResty fails to reload

**Solution**:
```bash
# Test config
sudo openresty -t -c /etc/openresty/proxy.conf

# Check for syntax errors
sudo tail -50 /var/log/nginx-config-update.log

# View generated config
sudo cat /etc/openresty/proxy.conf

# Manually fix if needed
sudo vim /etc/openresty/proxy.conf

# Reload
sudo systemctl reload openresty
```

### Issue: SNI routing not working

**Symptom**: All domains route to same RDS

**Cause**: Nginx config not using SNI

**Verify**:
```bash
# Check config has ssl_preread on
sudo grep ssl_preread /etc/openresty/proxy.conf

# Should show:
# ssl_preread on;
# map $ssl_preread_server_name $backend_name { ... }
```

## Verification

### Verify mappings deployed successfully

```bash
# Check Terraform outputs
terraform output

# Verify SSM parameter
aws ssm get-parameter --name /rds/proxy/mappings/<domain> --query 'Parameter.Value' --output text | jq

# Verify listeners created
NLB_ARN=$(cd ../2-infrastructure && terraform output -raw nlb_arn)
aws elbv2 describe-listeners --load-balancer-arn $NLB_ARN

# Verify target groups
terraform output target_groups

# Check target health
for port in $(terraform output -json managed_ports | jq -r '.[]'); do
  echo "=== Port $port ==="
  TG_ARN=$(terraform output -json target_groups | jq -r ".\"$port\"")
  aws elbv2 describe-target-health --target-group-arn $TG_ARN
done

# Verify nginx config on EC2
INSTANCE_ID=$(cd ../2-infrastructure && terraform output -raw ec2_instance_id)
aws ssm send-command \
  --instance-ids $INSTANCE_ID \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["cat /etc/openresty/proxy.conf"]'
```

### Test end-to-end connectivity

```bash
# From instance in same VPC
aws ssm start-session --target $INSTANCE_ID

# Test DNS resolution
nslookup db1.domain.com

# Test TCP connectivity
nc -zv db1.domain.com 1443

# Test TLS handshake
openssl s_client -connect db1.domain.com:1443 -servername db1.domain.com
```
