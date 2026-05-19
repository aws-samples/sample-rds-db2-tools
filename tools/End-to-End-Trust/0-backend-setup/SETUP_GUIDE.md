# Backend Setup - Complete Guide

## Problem: Circular Dependency

The DynamoDB table is needed for state locking, but Terraform needs to initialize before it can create the table. This creates a "chicken and egg" problem.

## Solution: Two-Phase Setup

1. **Phase 1**: Use local state to create resources
2. **Phase 2**: Migrate to remote state after resources exist

---

## Fresh Setup (End-to-End)

### Step 1: Bootstrap Configuration

```bash
cd 0-backend-setup
./bootstrap-backend.sh
```

**Prompts:**
- AWS Region: `us-east-1` (or your region)
- S3 Bucket Name: `your-unique-bucket-name`
- DynamoDB Table Name: `terraform-state-lock`

**What it creates:**
- `terraform.tfvars` - Resource configuration
- `backend.tf.template` - Backend config (not active yet)

### Step 2: Initialize with Local State

```bash
terraform init
```

This uses **local state** (no backend configured yet).

### Step 3: Create Backend Resources

```bash
terraform apply --auto-approve
```

Creates:
- S3 bucket for state storage
- DynamoDB table for state locking

### Step 4: Migrate to Remote State

```bash
mv backend.tf.template backend.tf
terraform init -migrate-state
```

Answer `yes` when prompted to migrate state from local to S3.

### Step 5: Configure Other Modules

```bash
./configure-modules.sh
```

Updates backend configuration in modules 1-3.

### Step 6: Deploy Infrastructure

```bash
cd ../1-prerequisites
terraform init
terraform apply --auto-approve

cd ../2-infrastructure
terraform init
terraform apply --auto-approve

cd ../3-mappings
terraform init
terraform apply --auto-approve
```

---

## Complete Cleanup (For Blog Post Testing)

```bash
cd /path/to/Terraform
./cleanup.sh
```

**What it does:**
1. Destroys all resources (mappings → infrastructure → prerequisites)
2. Removes S3 state files (`rdsdb2-proxy/*` prefix only)
3. **Deletes DynamoDB table** ✅
4. Cleans local Terraform files
5. Resets backend configs to template state

**What it preserves:**
- S3 bucket itself (only removes `rdsdb2-proxy/*` prefix)
- Other data in the bucket

---

## Verification Commands

### Check if DynamoDB table exists

```bash
aws dynamodb describe-table --table-name terraform-state-lock --region us-east-1
```

**Expected after cleanup:**
```
An error occurred (ResourceNotFoundException) when calling the DescribeTable operation: 
Requested resource not found
```

### Check if S3 state files exist

```bash
aws s3 ls s3://YOUR_BUCKET/rdsdb2-proxy/ --recursive
```

**Expected after cleanup:** Empty (no output)

### Check local state files

```bash
find . -name "terraform.tfstate*" -o -name ".terraform"
```

**Expected after cleanup:** Empty (no output)

---

## Troubleshooting

### Error: "Requested resource not found" (DynamoDB)

**Cause:** DynamoDB table doesn't exist yet, but backend.tf references it.

**Solution:**
```bash
cd 0-backend-setup

# Remove backend.tf if it exists
rm -f backend.tf

# Re-run bootstrap (creates backend.tf.template instead)
./bootstrap-backend.sh

# Initialize with local state
terraform init

# Create resources
terraform apply --auto-approve

# Now migrate to remote state
mv backend.tf.template backend.tf
terraform init -migrate-state
```

### Error: "Backend initialization required"

**Cause:** Backend configuration changed but not re-initialized.

**Solution:**
```bash
terraform init -reconfigure
```

### Error: "State lock already held"

**Cause:** Previous operation didn't release the lock.

**Solution:**
```bash
# Get lock ID from error message
terraform force-unlock LOCK_ID
```

---

## For Blog Post Authors

### Clean Setup Workflow

```bash
# 1. Complete cleanup
./cleanup.sh

# 2. Verify clean state
aws dynamodb describe-table --table-name terraform-state-lock --region us-east-1
# Should return: ResourceNotFoundException

# 3. Fresh setup
cd 0-backend-setup
./bootstrap-backend.sh
terraform init
terraform apply --auto-approve
mv backend.tf.template backend.tf
terraform init -migrate-state
./configure-modules.sh

# 4. Deploy modules
cd ../1-prerequisites && terraform init && terraform apply --auto-approve --auto-approve
cd ../2-infrastructure && terraform init && terraform apply --auto-approve
cd ../3-mappings && terraform init && terraform apply --auto-approve
```

### What to Document

1. **Bootstrap phase** - Creating backend resources with local state
2. **Migration phase** - Moving from local to remote state
3. **Why this approach** - Avoids circular dependency
4. **Cleanup verification** - Ensuring DynamoDB table is deleted

---

## Key Changes from Previous Version

| Aspect | Old Behavior | New Behavior |
|--------|-------------|--------------|
| **bootstrap-backend.sh** | Created `backend.tf` immediately | Creates `backend.tf.template` |
| **Initial state** | Tried to use S3 backend | Uses local state |
| **Migration** | Manual | Explicit `mv` + `init -migrate-state` |
| **Cleanup** | Complex Terraform config | Direct AWS CLI commands |
| **DynamoDB deletion** | Not guaranteed | Explicitly deleted + verified |

---

## Summary

✅ **No circular dependency** - Local state used initially  
✅ **Clean migration** - Explicit state migration step  
✅ **Complete cleanup** - DynamoDB table properly deleted  
✅ **Blog-ready** - Repeatable end-to-end setup  
✅ **Clear instructions** - Step-by-step guide for users
