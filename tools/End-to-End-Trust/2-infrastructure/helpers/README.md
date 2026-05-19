# Resource Discovery Helper

This helper uses Terraform data sources to discover AWS resources.

## Why Terraform Instead of AWS CLI?

- **Same credentials**: Uses Terraform service account (same as deployment)
- **No user AWS CLI access needed**: Works in restricted environments
- **GitOps friendly**: Runs in CI/CD pipelines
- **Consistent**: Same auth method as actual deployment

## Usage

Run from parent directory:
```bash
cd 2-infrastructure
./configure-infrastructure.sh
```

The script will:
1. Run `terraform apply` in helpers/ to discover resources
2. Parse outputs to show VPCs, subnets, security groups
3. Prompt for selections
4. Generate terraform.tfvars

## Manual Usage

You can also run the discovery manually:

```bash
cd helpers
terraform init
terraform plan
terraform apply

# View all VPCs
terraform output vpc_list

# View all subnets
terraform output subnet_list

# View all security groups
terraform output sg_list
```

## Cleanup

The helper creates no resources, only reads data. No cleanup needed.
