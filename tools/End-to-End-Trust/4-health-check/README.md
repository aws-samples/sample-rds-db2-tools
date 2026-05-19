# 4-health-check: Deployment Health Validation

## Overview

This module validates the entire RDS DB2 proxy deployment by checking:
- EC2 instance state and SSM connectivity
- OpenResty service status
- Listening ports (dynamically from target groups)
- NLB target group health
- SSM parameter configuration
- Nginx configuration validity
- Certificate presence
- Cron job configuration

## Port Validation Method

The health check validates ports accurately by:
1. **Querying NLB target groups** to get exact ports
2. **Running netstat on EC2** for each specific port
3. **Checking exact port matches** (not regex patterns)

This eliminates false positives from similar port numbers (e.g., 1443 vs 50443).

**What Was Fixed**:
- Previous version used regex pattern matching
- Port 1443 would match "50443" in netstat output
- Now checks each port individually with exact matching

## Prerequisites

- Modules 0, 1, 2, and 3 must be deployed
- AWS CLI configured
- `jq` installed locally

## Usage

```bash
cd 4-health-check

# Initialize
terraform init

# Run health check
terraform plan
terraform apply

# The output will show:
# ✓ = Pass
# ❌ = Fail
# ⚠ = Warning
```

## What It Checks

### 1. Infrastructure
- EC2 instance is running
- SSM agent is online
- Infrastructure outputs are available

### 2. Services
- OpenResty service is active
- All expected ports are listening (443, 1443, 50001, 50443)

### 3. NLB Health
- Target groups exist for all ports
- Targets are healthy

### 4. Configuration
- SSM parameter `/rds/proxy/mappings/<domain>` exists
- RDS mappings are configured
- Nginx configuration is valid

### 5. Certificates
- Certificate files exist in `/etc/openresty/certs/`
- Proper permissions set

### 6. Automation
- Cron job is configured for auto-updates

## Example Output

```
==========================================
RDS DB2 Proxy Health Check
==========================================

✓ Infrastructure outputs found
  EC2 Instance: i-0123456789abcdef0
  NLB ARN: arn:aws:elasticloadbalancing:...
  NLB DNS: nlb-db-company-com-xxx.elb.us-east-1.amazonaws.com

✓ EC2 instance is running
✓ SSM agent is online
✓ OpenResty service is active

Checking listening ports...
  Listening ports: 443 1443 50001 50443
  ✓ Port 443 is listening
  ✓ Port 1443 is listening
  ✓ Port 50001 is listening
  ✓ Port 50443 is listening

Checking NLB target group health...
  ✓ Port 443 target is healthy
  ✓ Port 1443 target is healthy
  ✓ Port 50001 target is healthy
  ✓ Port 50443 target is healthy

✓ RDS mappings configured (3 entries)
  - db1.example.com:1443 -> rds1.amazonaws.com:1443
  - db2.example.com:50443 -> rds2.amazonaws.com:50443
  - db3.example.com:443 -> rds3.amazonaws.com:443

✓ Nginx configuration is valid
✓ Certificates are present
✓ Cron job is configured

==========================================
Health Check Complete
==========================================
```

## Troubleshooting

If health check fails:

1. **EC2 not running**: Check 2-infrastructure deployment
2. **SSM not online**: Wait 2-3 minutes after EC2 creation
3. **OpenResty not active**: Check `/var/log/user-data.log` on EC2
4. **Ports not listening**: Run `/usr/local/bin/update-nginx-config.sh` on EC2
5. **Targets unhealthy**: Check security groups and nginx config
6. **No mappings**: Deploy 3-mappings module

## Re-running Health Check

```bash
# Force re-run
terraform apply -replace=null_resource.health_check

# Or simply
terraform plan
terraform apply
```

## Notes

- Health check runs locally using AWS CLI
- Requires SSM permissions to query EC2
- Does not modify any resources
- Safe to run multiple times
