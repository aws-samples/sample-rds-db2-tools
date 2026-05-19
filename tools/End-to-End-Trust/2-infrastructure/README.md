# 2-Infrastructure Module

Deploys the main proxy infrastructure: EC2 instance, NLB, and Route53 private hosted zone.

## Purpose

Creates the core infrastructure for SSL SNI-based proxy routing to RDS instances.

## What It Creates

- **EC2 Instance**: Running OpenResty 1.25.3.1 with SNI routing
- **Network Load Balancer**: TCP passthrough on configured ports with access logging
- **Route53 Private Zone**: Wildcard DNS (`*.yourdomain.com`) with target health evaluation
- **IAM Roles**: EC2 permissions scoped to SSM (AmazonSSMManagedInstanceCore) and specific Secrets Manager secret
- **Security Groups**: Scoped NLB and EC2 proxy SGs with no circular dependency

**Note**: Target groups and listeners are managed by 3-mappings module (not here).

## Prerequisites

- `0-backend-setup` completed
- `configure-modules.sh` script executed
- `1-prerequisites` completed

- VPC with subnets
- Security groups for EC2

## Preparation for Deployment

### Option 1: Interactive Helper (Recommended)

```bash
cd 2-infrastructure
./configure-infrastructure.sh
```

**How It Works**:
- Uses Terraform data sources (not AWS CLI) to discover resources
- Same service account credentials as deployment
- Works in restricted environments (no user AWS CLI access needed)
- Displays resources with index numbers (0, 1, 2) for easy selection
- Generates terraform.tfvars automatically

**First run may take several minutes** to initialize Terraform and query AWS.

See [helpers/README.md](helpers/README.md) for details.

### Option 2: Manual Configuration

```bash
cd 2-infrastructure
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your VPC/subnet details
```

## Configuration

Edit `terraform.tfvars`:

```hcl
aws_region = "us-east-1"
vpc_id     = "vpc-xxxxx"
subnet_ids = ["subnet-xxxxx", "subnet-yyyyy"]  # For NLB (2+ AZs)
ec2_subnet_id = "subnet-xxxxx"
security_group_ids = ["sg-xxxxx"]

# S3 bucket for NLB access logs (must be globally unique)
nlb_logs_bucket_name = "my-company-nlb-logs-123456789012-us-east-1"

# Certificate ARNs auto-populated from 1-prerequisites
# Leave these empty or omit them

nlb_scheme = "internal"  # or "internet-facing"
project_tag = "rdsdb2-proxy"
```

## Architecture Decision: Target Groups in 3-Mappings

Target groups and listeners are managed in 3-mappings module because:
- **Dynamic ports**: Extracted from RDS mapping keys automatically
- **No coordination**: Adding new port in mappings creates listener/target group
- **Stable infrastructure**: This module remains unchanged when mappings change
- **Separation of concerns**: Infrastructure vs configuration

## Deployment
```bash
terraform init
terraform plan
terraform apply
```

## Verify EC2 User Data Execution

After deployment, verify that the user_data script ran successfully:

### Connect to EC2 Instance

```bash
# Get instance ID from outputs
INSTANCE_ID=$(terraform output -raw ec2_instance_id)

# Connect via SSM
aws ssm start-session --target $INSTANCE_ID
```

### Check User Data Logs

```bash
# View user-data execution log
sudo cat /var/log/user-data.log

# Check for errors
sudo grep -i error /var/log/user-data.log
```

### Verify Services and Files

```bash
# 1. Check OpenResty service
sudo systemctl status openresty
# Should show: active (running)

# 2. Verify certificates were downloaded
sudo ls -la /etc/openresty/certs/
# Should show: proxy-cert.pem and proxy-key.pem

# 3. Check Nginx configuration
sudo cat /etc/openresty/proxy.conf
# Should have proper stream configuration

# 4. Verify cron job exists
sudo cat /etc/cron.d/nginx-update
# Should show: */5 * * * * root /usr/local/bin/update-nginx-config.sh

# 5. Check update script
sudo ls -la /usr/local/bin/update-nginx-config.sh
# Should exist and be executable (755)

# 6. Verify crond is running
sudo systemctl status crond
# Should show: active (running)

# 7. Test Nginx configuration
sudo openresty -t -c /etc/openresty/proxy.conf
# Should show: syntax is ok, test is successful
```

### Quick Health Check

```bash
# Run all checks at once
sudo bash -c '
echo "=== OpenResty Status ===";
systemctl is-active openresty;
echo "";
echo "=== Certificates ===";
ls -lh /etc/openresty/certs/;
echo "";
echo "=== Cron Job ===";
cat /etc/cron.d/nginx-update;
echo "";
echo "=== Nginx Config Test ===";
openresty -t -c /etc/openresty/proxy.conf 2>&1;
'
```

## Troubleshooting

### If User Data Failed

1. **Check what failed:**
   ```bash
   sudo tail -50 /var/log/user-data.log
   ```

2. **Common issues:**
   - Missing `unzip`: Install with `sudo dnf install -y unzip`
   - AWS CLI not installed: Check `/usr/local/bin/aws --version`
   - Certificate retrieval failed: Check IAM permissions
   - OpenResty failed to start: Check `/var/log/messages`

### Recreate EC2 Instance

If user_data script failed and you've fixed the issue in user_data.sh:

```bash
# Option 1: Taint and recreate just the EC2 instance
terraform taint aws_instance.proxy
terraform plan
terraform apply

# Option 2: Destroy and recreate everything
terraform destroy -target=aws_instance.proxy
terraform plan
terraform apply
```

### Manual Script Execution

If you want to re-run the user_data script manually:

```bash
# On the EC2 instance
sudo bash /var/lib/cloud/instance/scripts/part-001
```

## Outputs

```
ec2_instance_id    = "i-xxxxx"
ec2_private_ip     = "10.0.x.x"
nlb_dns_name       = "nlb-xxx.elb.amazonaws.com"
hosted_zone_id     = "Z123456"
```

## Verify Infrastructure Components

### 1. Network Load Balancer (NLB)

**Via AWS Console (Read-only access):**
1. Go to **EC2 → Load Balancers**
2. Search for: `nlb-db-yourdomain-com`
3. Check:
   - **State**: Active
   - **Scheme**: internal (or internet-facing)
   - **Availability Zones**: Should show 2+ zones
   - **Listeners**: Port 443 (TCP) → Target Group

**Via Terraform Outputs:**
```bash
# Get NLB details
terraform output nlb_dns_name
terraform output nlb_arn
```

### 2. Target Group Health

**Via AWS Console (Read-only access):**
1. Go to **EC2 → Target Groups**
2. Search for: `tg-db-yourdomain-com`
3. Click on the target group
4. Go to **Targets** tab
5. Check:
   - **Status**: Should be `Healthy`
   - **Target**: Your EC2 instance ID
   - **Port**: 8443

**What "healthy" means:**
- NLB can successfully connect to EC2 on port 8443
- OpenResty is listening and accepting connections

**If status is "unhealthy":**
Login to EC2 and 
- Check if OpenResty is running: `sudo systemctl status openresty`
- Check if port 8443 is listening: `sudo netstat -tlnp | grep 8443`
- Check security group allows traffic from NLB

### 3. Route53 Private Hosted Zone

**Via AWS Console (Read-only access):**
1. Go to **Route 53 → Hosted zones**
2. Search for: `db.yourdomain.com`
3. Check:
   - **Type**: Private hosted zone
   - **Hosted Zone ID**: Should show your VPC ID
   - **Records**: Should have a wildcard record `*.db.yourdomain.com`

4. Click on the wildcard record:
   - **Type**: A (Alias)
   - **Alias target**: Your NLB DNS name
   - **Routing policy**: Simple

**Via Terraform Outputs:**
```bash
# Get hosted zone details
terraform output hosted_zone_id
terraform output hosted_zone_name_servers
```

### 4. End-to-End Connectivity Test

**From an EC2 instance in the same VPC:**

```bash
# Test DNS resolution
nslookup db1.db.yourdomain.com
# Should resolve to NLB private IP

# Test TCP connectivity to NLB
nc -zv db1.db.yourdomain.com 443
# Should show: Connection succeeded

# Test with OpenSSL (if you have an RDS endpoint configured)
openssl s_client -connect db1.db.yourdomain.com:443 -servername db1.db.yourdomain.com
# Should establish TLS connection
```

### 5. Verification Checklist

✅ **EC2 Instance**
- [ ] Instance is running
- [ ] OpenResty service is active
- [ ] Certificates exist in /etc/openresty/certs/
- [ ] Nginx config is valid
- [ ] Cron job is configured

✅ **Network Load Balancer**
- [ ] NLB state is Active
- [ ] Listener on port 443 exists
- [ ] Correct subnets/AZs configured

✅ **Target Group**
- [ ] EC2 instance is registered
- [ ] Health status is "healthy"
- [ ] Health check is TCP on port 8443

✅ **Route53**
- [ ] Private hosted zone exists
- [ ] Associated with correct VPC
- [ ] Wildcard record points to NLB

✅ **Connectivity**
- [ ] DNS resolves correctly
- [ ] TCP connection to port 443 succeeds
- [ ] TLS handshake works (after RDS mappings configured)

### 6. Common Issues

**Target Group shows "unhealthy":**
- OpenResty not running → Check user_data logs
- Port 8443 not listening → Check nginx config
- Security group blocking traffic → Check SG rules

**DNS not resolving:**
- Wrong VPC association → Check Route53 hosted zone
- Query from outside VPC → Private zone only works within VPC

**Connection timeout:**
- NLB security group blocking → Check NLB SG (internal NLB only)
- EC2 security group blocking → Check EC2 SG allows traffic from NLB
- Wrong subnet → Ensure EC2 and NLB in correct subnets

## How It Works

1. **Client** connects to `db1.yourdomain.com:443`
2. **Route53** resolves to NLB IP
3. **NLB** forwards TCP to EC2:443
4. **EC2 Proxy** reads SNI, routes to correct RDS endpoint
5. **RDS** receives connection with end-to-end TLS

## Port Management

Ports are managed automatically by 3-mappings module:

**Workflow for Adding New Port**:

1. **Update 3-mappings** (no changes needed here):
   ```hcl
   rds_mappings = {
     "db1.domain.com:1443"  = "rds1.amazonaws.com:1443"
     "db3.domain.com:50001" = "rds3.amazonaws.com:50001"  # New port
   }
   ```

2. **Run terraform apply** in 3-mappings:
   - ✓ Extracts port 50001 from mapping key
   - ✓ Creates NLB listener on port 50001
   - ✓ Creates target group for port 50001
   - ✓ Registers EC2 to target group

3. **Verify** with health check:
   ```bash
   cd ../4-health-check
   terraform plan
   terraform apply
   ```

**No changes needed in 2-infrastructure module when adding ports.**

## Next Step

Proceed to [3-mappings](../3-mappings/README.md) to configure RDS endpoints.
