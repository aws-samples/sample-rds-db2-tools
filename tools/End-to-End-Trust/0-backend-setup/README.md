# Backend Setup Module

This module creates the S3 bucket and DynamoDB table required for Terraform remote state management.

## ⚠️ Important: Circular Dependency Fix

This module now uses a **two-phase setup** to avoid the circular dependency where the DynamoDB table is needed for state locking but doesn't exist yet.

**See [SETUP_GUIDE.md](SETUP_GUIDE.md) for complete instructions.**

## Quick Start

### Step 1: Bootstrap Configuration

```bash
cd 0-backend-setup
./bootstrap-backend.sh
```

The script will prompt for:
- **AWS Region** (default: us-east-1)
- **S3 Bucket Name** (required)
- **DynamoDB Table Name** (default: terraform-state-lock)
- **Terraform Principal ARN** (required) — the IAM role or user that runs Terraform. Get it with: `aws sts get-caller-identity --query Arn --output text`

It generates:
- `terraform.tfvars` - Resource configuration
- `backend.tf.template` - Backend config (inactive until resources exist)

### Step 2: Create Resources with Local State

```bash
terraform init          # Uses local state
terraform plan
terraform apply
```

### Step 3: Migrate to Remote State

```bash
mv backend.tf.template backend.tf
terraform init -migrate-state
```

### Step 4: Auto-Configure All Modules

```bash
./configure-modules.sh
```

This script automatically updates backend configuration in modules 1-3 with your settings.

## Scripts

### bootstrap-backend.sh
Interactive script that prompts for configuration and generates both `terraform.tfvars` and `backend.tf`.

**Usage:**
```bash
./bootstrap-backend.sh
```

**What it does:**
- Prompts for AWS region, bucket name, table name
- Generates terraform.tfvars (for resource creation)
- Generates backend.tf.template (for state storage - inactive initially)
- Uses local state first, then migrates to remote state
- Avoids circular dependency with DynamoDB table

### configure-modules.sh
Auto-configures all modules with backend settings from Terraform outputs.

**Usage:**
```bash
./configure-modules.sh
```

### reset-to-template.sh
Resets all backend configuration files to pristine template state with placeholders.

**Usage (before committing to GitHub):**
```bash
./reset-to-template.sh
```

This ensures your repository doesn't contain actual bucket names or regions.

## What Gets Created

- **S3 Bucket**: Stores Terraform state files
  - Versioning enabled
  - Encryption enabled with customer-managed KMS key (SSE-KMS)
  - Public access blocked
  - Bucket policy enforces TLS-only access and restricts GetObject to the Terraform service account
- **KMS Key**: Customer-managed key for state file encryption
  - Key rotation enabled
  - Access scoped to the Terraform service account only
- **DynamoDB Table**: State locking to prevent conflicts
  - Pay-per-request billing
  - Protected from accidental deletion

## Backend Strategy

**Why S3 Backend for Module 0?**

Module 0 uses S3 backend (not local) to survive cleanup operations:
- State persists even after `cleanup.sh reset`
- Allows redeployment without losing backend configuration
- Backend resources (bucket/table) remain intact during cleanup

**Cleanup Behavior**:
- `cleanup.sh` only removes `s3://bucket/rdsdb2-proxy/*` prefix
- S3 bucket and DynamoDB table are preserved
- Other data in bucket remains untouched
- Module 0 state file survives cleanup

## State File Structure

```
s3://YOUR_BUCKET/
└── rdsdb2-proxy/
    ├── 0-backend-setup/terraform.tfstate
    ├── 1-prerequisites/terraform.tfstate
    ├── 2-infrastructure/terraform.tfstate
    └── 3-mappings/terraform.tfstate
```

## Re-running Setup

The setup is idempotent. If resources already exist:

```bash
# Import existing resources
terraform import aws_s3_bucket.terraform_state YOUR_BUCKET_NAME
terraform import aws_dynamodb_table.terraform_lock YOUR_TABLE_NAME
terraform plan
terraform apply
```

## Benefits

- **No Manual Editing**: Script configures everything
- **Team Collaboration**: Shared state in S3
- **State Locking**: Prevents conflicts
- **Versioning**: Rollback capability
- **Audit Trail**: S3 access logs
- **GitHub Ready**: Reset script for clean commits
