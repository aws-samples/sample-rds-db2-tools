# 4-Health-Check Troubleshooting

## Health Check Execution Issues

### Issue: Health check fails immediately

**Symptom**: "Error: command not found" or "Error: jq not found"

**Cause**: Missing dependencies

**Solution**:
```bash
# Install jq
# macOS:
brew install jq

# Linux:
sudo yum install -y jq  # Amazon Linux
sudo apt-get install -y jq  # Ubuntu

# Verify AWS CLI
aws --version
```

### Issue: Can't find infrastructure outputs

**Symptom**: "Error: No outputs found" or "Error: Unsupported attribute"

**Cause**: Previous modules not deployed

**Solution**:
```bash
# Verify all modules deployed
cd ../0-backend-setup && terraform output
cd ../1-prerequisites && terraform output
cd ../2-infrastructure && terraform output
cd ../3-mappings && terraform output

# If any fail, deploy that module first
```

### Issue: Health check runs but shows no output

**Symptom**: `terraform apply` completes but no health check results

**Cause**: null_resource not triggering

**Solution**:
```bash
# Force re-run
terraform apply -replace=null_resource.health_check

# Or destroy and recreate
terraform destroy
terraform apply --auto-approve
```

## EC2 Health Check Issues

### Issue: EC2 instance not running

**Symptom**: "✗ EC2 instance is not running"

**Solution**:
```bash
# Check instance state
INSTANCE_ID=$(cd ../2-infrastructure && terraform output -raw ec2_instance_id)
aws ec2 describe-instances --instance-ids $INSTANCE_ID \
  | jq -r '.Reservations[0].Instances[0].State.Name'

# Start instance if stopped
aws ec2 start-instances --instance-ids $INSTANCE_ID

# Wait for running state
aws ec2 wait instance-running --instance-ids $INSTANCE_ID
```

### Issue: SSM agent not online

**Symptom**: "✗ SSM agent is not online"

**Causes**:
1. Instance just started (wait 2-3 minutes)
2. SSM agent not installed
3. IAM role missing permissions
4. No internet connectivity

**Solution**:
```bash
# Wait for SSM
sleep 180

# Check SSM status
aws ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=$INSTANCE_ID"

# If still offline, check instance logs
aws ec2 get-console-output --instance-id $INSTANCE_ID

# Verify IAM role
aws ec2 describe-instances --instance-ids $INSTANCE_ID \
  | jq -r '.Reservations[0].Instances[0].IamInstanceProfile'
```

## Service Health Check Issues

### Issue: OpenResty not active

**Symptom**: "✗ OpenResty service is not active"

**Solution**:
```bash
# Connect to EC2
aws ssm start-session --target $INSTANCE_ID

# Check service status
sudo systemctl status openresty

# Check logs
sudo journalctl -u openresty -n 50

# Check user_data log
sudo cat /var/log/user-data.log

# Restart service
sudo systemctl restart openresty
```

## Port Health Check Issues

### Issue: Ports not listening

**Symptom**: "✗ Port 1443 is not listening"

**Causes**:
1. Cron job hasn't run yet (wait 5 minutes)
2. Nginx config not updated
3. OpenResty not reloaded
4. No mappings configured

**Solution**:
```bash
# Connect to EC2
aws ssm start-session --target $INSTANCE_ID

# Force config update
sudo /usr/local/bin/update-nginx-config.sh

# Check ports
sudo netstat -tlnp | grep -E ':(443|[0-9]{4,5})'

# Verify mappings exist
aws ssm get-parameter --name /rds/proxy/mappings/<domain>

# Reload OpenResty
sudo systemctl reload openresty
```

### Issue: Wrong ports reported

**Symptom**: Health check shows ports that don't exist in mappings

**Cause**: Old nginx config or stale target groups

**Solution**:
```bash
# Check current mappings
cd ../3-mappings
terraform output managed_ports

# Force health check refresh
cd ../4-health-check
terraform apply -replace=null_resource.health_check
```

### Issue: Port listening but health check fails

**Symptom**: `netstat` shows port but health check says not listening

**Cause**: Port validation logic issue

**Debug**:
```bash
# Run health check manually
cd 4-health-check

# Get instance ID
INSTANCE_ID=$(cd ../2-infrastructure && terraform output -raw ec2_instance_id)

# Check specific port
aws ssm send-command \
  --instance-ids $INSTANCE_ID \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["netstat -tlnp | grep :1443"]' \
  --query 'Command.CommandId' \
  --output text

# Get command result
COMMAND_ID=<from above>
aws ssm get-command-invocation \
  --command-id $COMMAND_ID \
  --instance-id $INSTANCE_ID
```

## Target Group Health Issues

### Issue: All targets unhealthy

**Symptom**: "✗ Port 1443: unhealthy"

**Causes**:
1. Port not listening on EC2
2. Security group blocking NLB
3. Health check configuration wrong

**Solution**:
```bash
# Check target group health
NLB_ARN=$(cd ../2-infrastructure && terraform output -raw nlb_arn)
aws elbv2 describe-listeners --load-balancer-arn $NLB_ARN

# For each target group
TG_ARN=<from above>
aws elbv2 describe-target-health --target-group-arn $TG_ARN

# Check health check config
aws elbv2 describe-target-groups --target-group-arns $TG_ARN \
  | jq -r '.TargetGroups[0].HealthCheckProtocol, .TargetGroups[0].HealthCheckPort'

# Should be: TCP, traffic-port
```

### Issue: Some targets healthy, some unhealthy

**Symptom**: Mixed health status across ports

**Cause**: Some ports not listening

**Solution**:
```bash
# Check which ports are listening
aws ssm send-command \
  --instance-ids $INSTANCE_ID \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["netstat -tlnp | grep LISTEN"]'

# Compare with expected ports
cd ../3-mappings
terraform output managed_ports

# Update nginx config
aws ssm send-command \
  --instance-ids $INSTANCE_ID \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["/usr/local/bin/update-nginx-config.sh"]'
```

## Configuration Health Check Issues

### Issue: No RDS mappings configured

**Symptom**: "✗ No RDS mappings configured"

**Cause**: Module 3 not deployed or SSM parameter empty

**Solution**:
```bash
# Check SSM parameter
aws ssm get-parameter --name /rds/proxy/mappings/<domain>

# If missing, deploy 3-mappings
cd ../3-mappings
terraform apply --auto-approve
```

### Issue: Nginx config invalid

**Symptom**: "✗ Nginx configuration is invalid"

**Solution**:
```bash
# Connect to EC2
aws ssm start-session --target $INSTANCE_ID

# Test config
sudo openresty -t -c /etc/openresty/proxy.conf

# View errors
sudo tail -50 /var/log/nginx-config-update.log

# Fix config manually if needed
sudo vim /etc/openresty/proxy.conf

# Or regenerate
sudo /usr/local/bin/update-nginx-config.sh
```

## Certificate Health Check Issues

### Issue: Certificates not present

**Symptom**: "✗ Certificates are not present"

**Solution**:
```bash
# Connect to EC2
aws ssm start-session --target $INSTANCE_ID

# Check certificates
sudo ls -la /etc/openresty/certs/

# If missing, download from Secrets Manager
SECRET_ARN=$(cd ../1-prerequisites && terraform output -raw certificate_secret_arn)
aws secretsmanager get-secret-value --secret-id $SECRET_ARN \
  | jq -r '.SecretString | fromjson | .certificate' \
  | sudo tee /etc/openresty/certs/proxy-cert.pem

aws secretsmanager get-secret-value --secret-id $SECRET_ARN \
  | jq -r '.SecretString | fromjson | .private_key' \
  | sudo tee /etc/openresty/certs/proxy-key.pem

sudo chmod 600 /etc/openresty/certs/proxy-key.pem
```

## Cron Job Health Check Issues

### Issue: Cron job not configured

**Symptom**: "✗ Cron job is not configured"

**Solution**:
```bash
# Connect to EC2
aws ssm start-session --target $INSTANCE_ID

# Check cron job
sudo cat /etc/cron.d/nginx-update

# If missing, create it
sudo tee /etc/cron.d/nginx-update > /dev/null <<'EOF'
*/5 * * * * root /usr/local/bin/update-nginx-config.sh >> /var/log/nginx-config-update.log 2>&1
EOF

# Restart crond
sudo systemctl restart crond
```

## False Positive Issues

### Issue: Port shows as listening but isn't

**Symptom**: Health check passes but connection fails

**Cause**: Previous version had regex matching bug (now fixed)

**Explanation**: 
- Old version: Used regex pattern matching
- Problem: Port 1443 matched "50443" in netstat
- Fixed: Now checks exact port matches

**Verify fix**:
```bash
# Health check should query target groups first
# Then check each specific port individually
# No regex patterns used
```

## Timing Issues

### Issue: Health check fails immediately after deployment

**Cause**: Services not fully started yet

**Solution**:
```bash
# Wait 5 minutes after deploying 3-mappings
sleep 300

# Then run health check
cd ../4-health-check
terraform apply --auto-approve
```

### Issue: Intermittent failures

**Cause**: Cron job running during health check

**Solution**:
```bash
# Run health check multiple times
for i in {1..3}; do
  echo "=== Run $i ==="
  terraform apply -replace=null_resource.health_check
  sleep 10
done
```

## Verification

### Manual health check

```bash
# Get instance ID
INSTANCE_ID=$(cd ../2-infrastructure && terraform output -raw ec2_instance_id)

# Check instance running
aws ec2 describe-instances --instance-ids $INSTANCE_ID \
  | jq -r '.Reservations[0].Instances[0].State.Name'

# Check SSM online
aws ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
  | jq -r '.InstanceInformationList[0].PingStatus'

# Check OpenResty
aws ssm send-command \
  --instance-ids $INSTANCE_ID \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["systemctl is-active openresty"]'

# Check ports
aws ssm send-command \
  --instance-ids $INSTANCE_ID \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["netstat -tlnp | grep LISTEN"]'

# Check target groups
NLB_ARN=$(cd ../2-infrastructure && terraform output -raw nlb_arn)
for listener in $(aws elbv2 describe-listeners --load-balancer-arn $NLB_ARN --query 'Listeners[].ListenerArn' --output text); do
  TG_ARN=$(aws elbv2 describe-listeners --listener-arns $listener --query 'Listeners[0].DefaultActions[0].TargetGroupArn' --output text)
  echo "=== Target Group: $TG_ARN ==="
  aws elbv2 describe-target-health --target-group-arn $TG_ARN
done
```

### Complete validation script

```bash
#!/bin/bash
# Run from 4-health-check directory

set -e

echo "=== Infrastructure Check ==="
cd ../2-infrastructure
INSTANCE_ID=$(terraform output -raw ec2_instance_id)
NLB_ARN=$(terraform output -raw nlb_arn)
echo "Instance: $INSTANCE_ID"
echo "NLB: $NLB_ARN"

echo -e "\n=== EC2 State ==="
aws ec2 describe-instances --instance-ids $INSTANCE_ID \
  | jq -r '.Reservations[0].Instances[0] | {State: .State.Name, SSM: .IamInstanceProfile.Arn}'

echo -e "\n=== SSM Status ==="
aws ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
  | jq -r '.InstanceInformationList[0] | {PingStatus, PlatformName, PlatformVersion}'

echo -e "\n=== Mappings ==="
cd ../3-mappings
terraform output managed_ports
terraform output mappings

echo -e "\n=== Health Check ==="
cd ../4-health-check
terraform apply --auto-approve
```
