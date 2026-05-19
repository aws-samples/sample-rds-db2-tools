# 3-Mappings Module

Manages RDS domain-to-endpoint mappings via SSM Parameter Store.

## Purpose

Maps client domain names to actual RDS endpoints. This is the module you'll update most frequently as you add/remove/change RDS instances.

## What It Creates

- SSM Parameter `/rds/proxy/mappings/<domain>` with JSON mapping
- **NLB Listeners** for each unique port in mappings (dynamic)
- **Target Groups** for each port (dynamic)
- **Target Group Attachments** to EC2 instance (dynamic)

## How Multi-Port Works

**Port Extraction**: Terraform extracts unique ports from mapping keys
```hcl
# From: "db1.domain.com:1443" = "rds1.amazonaws.com:1443"
# Extracts: 1443 using split(":", key)[1]
```

**Dynamic Resource Creation**: For each unique port, creates:
- NLB listener (port → target group)
- Target group (health checks on EC2:port)
- Target group attachment (registers EC2)

**State Integration**: Reads 2-infrastructure outputs via terraform_remote_state
- Gets NLB ARN, EC2 instance ID, VPC ID
- No manual coordination needed
- Uses S3 backend to access shared state

## Prerequisites

- `0-backend-setup` completed
- `configure-modules.sh` script executed
- `1-prerequisites` completed
- `2-infrastructure` completed

## Prepare for Deployment

```bash
cd 3-mappings
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your RDS mappings

```

## Configuration

Edit `terraform.tfvars`:

```hcl
aws_region = "us-east-1"

rds_mappings = {
  "db1.db.mydomain.com:1443"  = "rds-instance-1.us-east-1.rds.amazonaws.com:1443"
  "db2.db.mydomain.com:50443" = "rds-instance-2.us-east-1.rds.amazonaws.com:50443"
}
```

**Format:** `"client-domain:client-port" = "rds-endpoint:rds-port"`

**Key Points:**
- **Fully automatic**: NLB listeners and target groups created automatically from mappings
- **No coordination needed**: Just add mappings, infrastructure adapts
- **Client port** (left): Port your application connects to
- **RDS port** (right): Actual RDS endpoint port
- **Ports can differ**: Client port 443 can route to RDS port 50443

## Deployment

After defining mapping between client domain names to the RDS for Db2 endpoints, run the following. 
```bash
terraform init
terraform plan
terraform apply
```

## How It Works

1. Terraform stores mappings in SSM Parameter Store
2. EC2 proxy has a cron job (every 5 minutes)
3. Cron job fetches mappings and updates Nginx config
4. Nginx reloads automatically

## Adding New Mapping

1. Edit `terraform.tfvars`, add new entry:
   ```hcl
   rds_mappings = {
     "db1.domain.com:1443"  = "rds1.amazonaws.com:1443"
     "db2.domain.com:50443" = "rds2.amazonaws.com:50443"
     "db3.domain.com:50001" = "rds3.amazonaws.com:50001"  # New
   }
   ```
2. Commit to Git (creates audit trail)
3. Run `terraform apply`
4. **Terraform automatically creates**:
   - NLB listener on port 50001
   - Target group for port 50001
   - Registers EC2 to target group
   - Updates SSM parameter
5. Wait up to 5 minutes for proxy to pick up changes

## Example: Complete Workflow

**Scenario**: Add database on port 50001

```bash
cd 3-mappings

# Edit terraform.tfvars
vim terraform.tfvars
# Add: "db3.domain.com:50001" = "rds3.amazonaws.com:50001"

# Apply changes
terraform plan
terraform apply

# Verify
cd ../4-health-check
terraform plan
terraform apply
# Should show: ✓ Port 50001 is listening
#              ✓ Port 50001: healthy
```

**What happened automatically**:
1. Port 50001 extracted from mapping key
2. NLB listener created on port 50001
3. Target group created for port 50001
4. EC2 registered to target group
5. SSM parameter updated
6. Proxy cron job updates nginx config (within 5 min)
7. OpenResty starts listening on port 50001

## Removing Mapping

1. Edit `terraform.tfvars`, remove entry
2. Commit to Git
3. Run `terraform plan` then `terraform apply`

## Updating Mapping

1. Edit `terraform.tfvars`, change endpoint
2. Commit to Git
3. Run `terraform plan` then `terraform apply`

## Verify Changes

### Option 1: Automated Health Check (Recommended)

```bash
cd ../4-health-check
terraform plan
terraform apply
```

This checks:
- ✓ Nginx config updated
- ✓ Ports listening
- ✓ Target groups healthy
- ✓ Mappings configured

### Option 2: Manual Verification

SSH to EC2 and check:

```bash
# View current mappings
sudo cat /etc/openresty/proxy.conf

# Check listening ports
sudo netstat -tlnp | grep -E ':(443|[0-9]{4,5})'

# Check update log
sudo tail -f /var/log/nginx-config-update.log

# Force immediate update (don't wait 5 min)
sudo /usr/local/bin/update-nginx-config.sh
```

## GitOps Workflow

This module is designed for frequent updates via Pull Requests:

1. Developer creates branch
2. Updates `terraform.tfvars`
3. Creates PR
4. CI runs `terraform plan` (shows changes)
5. Reviewer approves
6. CI runs `terraform apply`
7. Full audit trail in Git history

## Outputs

```
parameter_name = "/rds/proxy/mappings/<domain>"
parameter_arn  = "arn:aws:ssm:..."
mappings       = { ... }
```
