# Terraform Modular Configuration for RDS Proxy

Enterprise-grade Terraform setup with full audit trail through Git.

## Key Features

✅ **Multi-Port Support** - Clients use their existing connection strings (any port)

✅ **Zero Application Changes** - No need to modify Db2 client configurations

✅ **SNI-Based Routing** - Multiple databases per port using domain names

✅ **Dynamic Configuration** - Add/remove databases without redeploying infrastructure

✅ **GitOps Ready** - All changes via Git commits with full audit trail

✅ **Modular Design** - Separate concerns for easier management

## Quick Links

### Setup Guides
- [Service Account Setup](docs/SERVICE_ACCOUNT_SETUP.md) - Use IAM roles with SSO or OIDC instead of personal credentials

### Modules
- [0-backend-setup](0-backend-setup/README.md) - S3 + DynamoDB for state management
- [1-prerequisites](1-prerequisites/README.md) - SSL certificates
- [2-infrastructure](2-infrastructure/README.md) - EC2, NLB, Route53
- [3-mappings](3-mappings/README.md) - RDS for Db2 endpoint mappings and NLB targets
- [4-health-check](4-health-check/README.md) - Validates deployment (EC2, ports, NLB health, certificates)

### Troubleshooting
- [Main Troubleshooting](TROUBLESHOOTING.md)
- [0-backend-setup](0-backend-setup/TROUBLESHOOTING.md)
- [1-prerequisites](1-prerequisites/TROUBLESHOOTING.md)
- [2-infrastructure](2-infrastructure/TROUBLESHOOTING.md)
- [3-mappings](3-mappings/TROUBLESHOOTING.md)
- [4-health-check](4-health-check/TROUBLESHOOTING.md)


## Architecture

```
0-backend-setup/    → S3 + DynamoDB (one-time setup)
1-prerequisites/    → Certificates & Secrets (one-time setup)
2-infrastructure/   → EC2, NLB, Route53 (main infrastructure)
3-mappings/         → RDS domain mappings (frequent updates to proxy.conf through cron and NLB targets)
```

## Quick Start

### Prerequisites

**Recommended**: Set up a service account for Terraform operations instead of using personal credentials. See [docs/SERVICE_ACCOUNT_SETUP.md](docs/SERVICE_ACCOUNT_SETUP.md) for detailed instructions.

```bash
# Quick setup with service account
export AWS_PROFILE=terraform-rds-proxy
```

### One-Time Setup: Enable Plugin Cache

```bash
./setup-plugin-cache.sh
```

Configures Terraform plugin cache for faster deployments:
- **First run**: Downloads providers (~656MB AWS provider) - takes 30-60s
- **Subsequent runs**: Uses symlinks from cache - takes ~5s
- **Benefit**: Saves time across all 5 modules (150-300s → 25s total)
- Run once before deploying any modules

### Deployment

### Step 1: Backend Setup (One-time)

```bash
cd 0-backend-setup
./bootstrap-backend.sh
# Prompts for bucket name, region, and DynamoDB table name
# Creates terraform.tfvars and backend.tf.template

terraform init          # Uses local state initially
terraform plan
terraform apply

# Migrate to remote state after resources are created
mv backend.tf.template backend.tf
terraform init -migrate-state
```

**Note**: This two-phase approach avoids the circular dependency where DynamoDB table is needed for state locking but doesn't exist yet. See [0-backend-setup/SETUP_GUIDE.md](0-backend-setup/SETUP_GUIDE.md) for details.

### Step 2: Auto-Configure All Modules

```bash
./configure-modules.sh
```

This automatically configures backend settings in all modules.

### Step 3: Deploy Prerequisites

```bash
cd ../1-prerequisites
cp terraform.tfvars.example terraform.tfvars
# Edit with your domain name
terraform init
terraform plan
terraform apply
```

### Step 4: Deploy Infrastructure

```bash
cd ../2-infrastructure
./configure-infrastructure.sh
terraform init
terraform plan
terraform apply
```

### Step 5: Configure RDS Mappings

```bash
cd ../3-mappings
cp terraform.tfvars.example terraform.tfvars
# Edit with RDS endpoints
```

## Managing RDS Mappings

### Add New Mapping

Edit `3-mappings/terraform.tfvars`:

```hcl
rds_mappings = {
  "db1.db.mydomain.com" = "rds-1.region.rds.amazonaws.com:3306"
  "db2.db.mydomain.com" = "rds-2.region.rds.amazonaws.com:5432"
  "db3.db.mydomain.com" = "rds-3.region.rds.amazonaws.com:1443"  # New
}
```

Apply:
```bash
terraform init
terraform plan
terraform apply
```

## Cleanup

```bash
./cleanup.sh
```

**What it does**:
- Destroys all resources in reverse order (mappings → infrastructure → prerequisites)
- Removes S3 state files (only `rdsdb2-proxy/*` prefix)
- Deletes DynamoDB table
- Cleans local Terraform files (.terraform, state files, tfvars)
- Resets backend configs to template state

**What it preserves**:
- S3 bucket (only removes rdsdb2-proxy/* prefix)
- Other data in the bucket remains untouched

**Use case**: Clean slate for redeployment or GitHub commits

## Multi-Port Architecture

The proxy supports multiple client ports simultaneously:

**Mapping Format**: `"domain:port": "endpoint:port"`

```hcl
rds_mappings = {
  "db1.domain.com:1443"  = "rds1.amazonaws.com:1443"
  "db2.domain.com:50443" = "rds2.amazonaws.com:50443"
  "db3.domain.com:443"   = "rds3.amazonaws.com:50000"
}
```

**How It Works**:
1. Terraform extracts unique ports from mapping keys (1443, 50443, 443)
2. Automatically creates NLB listener + target group for each port
3. No infrastructure changes needed when adding new ports
4. Just add mapping and run `terraform plan` then `terraform apply` in 3-mappings

## Why Modular?

1. **Audit Trail**: All changes via Git commits
2. **Separation of Concerns**: Different update frequencies
3. **No Manual Editing**: Scripts handle configuration
4. **GitOps Ready**: CI/CD pipeline friendly
5. **Clean Repository**: Template files with placeholders
6. **Dynamic Ports**: Infrastructure adapts to mappings automatically

## State File Structure

```
s3://YOUR_BUCKET/
└── rdsdb2-proxy/
    ├── 0-backend-setup/terraform.tfstate
    ├── 1-prerequisites/terraform.tfstate
    ├── 2-infrastructure/terraform.tfstate
    └── 3-mappings/terraform.tfstate
```

## Benefits

- **Automated Configuration**: No manual file editing
- **Team Collaboration**: Shared state in S3
- **State Locking**: Prevents conflicts
- **Versioning**: Rollback capability
- **Audit Trail**: Full Git history
- **GitHub Ready**: Clean templates for public repos
