# 2-Infrastructure Troubleshooting

## configure-infrastructure.sh Issues

### Issue: Script takes several minutes on first run

**Explanation**: Normal behavior
- Initializes Terraform in helpers/ directory
- Downloads AWS provider (~656MB)
- Queries AWS for VPCs, subnets, security groups
- Subsequent runs are faster

### Issue: Script shows no resources

**Causes**:
1. No VPCs in account
2. Wrong AWS region
3. Insufficient permissions

**Solution**:
```bash
# Verify AWS credentials
aws sts get-caller-identity

# Check VPCs exist
aws ec2 describe-vpcs --region us-east-1

# Run discovery manually
cd helpers
terraform init
terraform apply --auto-approve
terraform output vpc_list
```

### Issue: Script fails with "No outputs found"

**Cause**: Terraform apply in helpers/ failed

**Solution**:
```bash
cd helpers
terraform init
terraform apply --auto-approve
# Check for errors

# View outputs manually
terraform output
```

### Issue: Index selection doesn't work

**Symptom**: "Invalid selection" error

**Solution**: Enter just the number (0, 1, 2), not the full ID
```bash
# Correct:
Select VPC: 0

# Incorrect:
Select VPC: vpc-12345
```

## EC2 Instance Issues

### Issue: Instance fails to start

**Causes**:
1. Insufficient capacity in AZ
2. Invalid AMI ID
3. Security group blocking SSM

**Solution**:
```bash
# Check instance status
INSTANCE_ID=$(terraform output -raw ec2_instance_id)
aws ec2 describe-instance-status --instance-ids $INSTANCE_ID

# Check system log
aws ec2 get-console-output --instance-id $INSTANCE_ID

# Try different subnet/AZ
# Edit terraform.tfvars: ec2_subnet_id = "subnet-xxxxx"
terraform apply --auto-approve
```

### Issue: SSM agent not online

**Symptom**: Can't connect via SSM

**Causes**:
1. Instance just started (wait 2-3 minutes)
2. Security group blocking HTTPS to SSM endpoints
3. No internet/NAT gateway for SSM endpoints
4. IAM role missing permissions

**Solution**:
```bash
# Wait for SSM agent
aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$INSTANCE_ID"

# Check IAM role attached
aws ec2 describe-instances --instance-ids $INSTANCE_ID \
  | jq -r '.Reservations[0].Instances[0].IamInstanceProfile'

# Verify security group allows HTTPS outbound
aws ec2 describe-security-groups --group-ids sg-xxxxx

# Check VPC has NAT gateway or VPC endpoints for SSM
aws ec2 describe-nat-gateways --filter "Name=vpc-id,Values=vpc-xxxxx"
```

## User Data Issues

### Issue: OpenResty not installed

**Symptom**: `systemctl status openresty` shows "not found"

**Cause**: user_data script failed

**Solution**:
```bash
# Connect to instance
aws ssm start-session --target $INSTANCE_ID

# Check user_data log
sudo cat /var/log/user-data.log

# Look for errors
sudo grep -i error /var/log/user-data.log

# Re-run user_data manually
sudo bash /var/lib/cloud/instance/scripts/part-001
```

### Issue: Certificates not downloaded

**Symptom**: `/etc/openresty/certs/` empty

**Causes**:
1. IAM role missing Secrets Manager permissions
2. Secret doesn't exist
3. AWS CLI not installed

**Solution**:
```bash
# Check AWS CLI
aws --version

# Test Secrets Manager access
SECRET_ARN=$(cd ../1-prerequisites && terraform output -raw certificate_secret_arn)
aws secretsmanager get-secret-value --secret-id $SECRET_ARN

# Manually download certificates
sudo mkdir -p /etc/openresty/certs
aws secretsmanager get-secret-value --secret-id $SECRET_ARN \
  | jq -r '.SecretString | fromjson | .certificate' \
  | sudo tee /etc/openresty/certs/proxy-cert.pem

aws secretsmanager get-secret-value --secret-id $SECRET_ARN \
  | jq -r '.SecretString | fromjson | .private_key' \
  | sudo tee /etc/openresty/certs/proxy-key.pem

sudo chmod 600 /etc/openresty/certs/proxy-key.pem
```

### Issue: Cron job not created

**Symptom**: `/etc/cron.d/nginx-update` doesn't exist

**Solution**:
```bash
# Manually create cron job
sudo tee /etc/cron.d/nginx-update > /dev/null <<'EOF'
*/5 * * * * root /usr/local/bin/update-nginx-config.sh >> /var/log/nginx-config-update.log 2>&1
EOF

# Verify crond running
sudo systemctl status crond
sudo systemctl restart crond
```

## NLB Issues

### Issue: NLB not accessible

**Causes**:
1. Internal NLB, accessing from outside VPC
2. Security group blocking traffic (internal NLB only)
3. Subnets in wrong AZs

**Solution**:
```bash
# Check NLB scheme
terraform output nlb_dns_name
aws elbv2 describe-load-balancers --names nlb-db-* | jq -r '.LoadBalancers[0].Scheme'
# Should show: internal or internet-facing

# For internal NLB, test from within VPC
# Launch test instance in same VPC
aws ec2 run-instances --subnet-id subnet-xxxxx ...

# Check NLB subnets
aws elbv2 describe-load-balancers --names nlb-db-* \
  | jq -r '.LoadBalancers[0].AvailabilityZones'
```

### Issue: No listeners on NLB

**Explanation**: Normal - listeners managed by 3-mappings module
- Deploy 3-mappings to create listeners
- Listeners created automatically from RDS mappings

**Verify**:
```bash
cd ../3-mappings
terraform plan
# Should show listeners to be created
```

## Route53 Issues

### Issue: DNS not resolving

**Causes**:
1. Querying from outside VPC (private hosted zone)
2. VPC not associated with hosted zone
3. Wrong domain name

**Solution**:
```bash
# Check hosted zone
ZONE_ID=$(terraform output -raw hosted_zone_id)
aws route53 get-hosted-zone --id $ZONE_ID

# Verify VPC association
aws route53 get-hosted-zone --id $ZONE_ID | jq -r '.VPCs'

# Test from within VPC
aws ssm start-session --target $INSTANCE_ID
nslookup db1.db.mydomain.com
# Should resolve to NLB IP
```

### Issue: Wildcard record not working

**Symptom**: Specific domains don't resolve

**Verify record**:
```bash
aws route53 list-resource-record-sets --hosted-zone-id $ZONE_ID \
  | jq -r '.ResourceRecordSets[] | select(.Type=="A")'

# Should show: *.db.mydomain.com pointing to NLB
```

## Security Group Issues

### Issue: Can't connect to EC2

**Cause**: Security group too restrictive

**Solution**:
```bash
# Security group must allow:
# - Inbound from NLB on all ports (or specific ports)
# - Outbound HTTPS to SSM endpoints
# - Outbound HTTPS to Secrets Manager
# - Outbound to RDS endpoints

# Check current rules
aws ec2 describe-security-groups --group-ids sg-xxxxx
```

### Issue: Target group unhealthy

**Cause**: Security group blocking NLB health checks

**Solution**:
```bash
# Allow inbound from NLB subnets
# Get NLB subnet CIDRs
aws ec2 describe-subnets --subnet-ids subnet-xxxxx subnet-yyyyy \
  | jq -r '.Subnets[].CidrBlock'

# Add rules to EC2 security group
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxx \
  --protocol tcp \
  --port 0-65535 \
  --cidr 10.0.1.0/24  # NLB subnet CIDR
```

## Permission Issues

### Error: "AccessDenied" creating EC2

**Required permissions**: See [minimum-iam-permissions.json](../docs/minimum-iam-permissions.json)

Key permissions:
- ec2:RunInstances
- ec2:CreateTags
- iam:CreateRole
- iam:AttachRolePolicy
- elasticloadbalancing:CreateLoadBalancer
- route53:CreateHostedZone

## State Issues

### Issue: Can't read 1-prerequisites outputs

**Symptom**: "Error: Unsupported attribute" for certificate_arn

**Cause**: Module 1 not deployed or backend not configured

**Solution**:
```bash
# Verify module 1 deployed
cd ../1-prerequisites
terraform output

# Verify backend configured
cat backend.tf

# Re-initialize
terraform init
```

## Verification

### Verify infrastructure deployed successfully

```bash
# Check all outputs
terraform output

# Verify EC2 running
INSTANCE_ID=$(terraform output -raw ec2_instance_id)
aws ec2 describe-instances --instance-ids $INSTANCE_ID \
  | jq -r '.Reservations[0].Instances[0].State.Name'
# Should show: running

# Verify SSM online
aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$INSTANCE_ID"

# Verify OpenResty running
aws ssm send-command \
  --instance-ids $INSTANCE_ID \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["systemctl is-active openresty"]'

# Check NLB
NLB_ARN=$(terraform output -raw nlb_arn)
aws elbv2 describe-load-balancers --load-balancer-arns $NLB_ARN

# Check Route53
ZONE_ID=$(terraform output -raw hosted_zone_id)
aws route53 list-resource-record-sets --hosted-zone-id $ZONE_ID
```

### Test connectivity

```bash
# From instance in same VPC
aws ssm start-session --target $INSTANCE_ID

# Test DNS
nslookup test.db.mydomain.com
# Should resolve to NLB IP

# Test TCP connectivity (after 3-mappings deployed)
nc -zv test.db.mydomain.com 443
```
