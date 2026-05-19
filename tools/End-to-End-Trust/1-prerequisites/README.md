# 1-Prerequisites Module

Generates self-signed certificates and stores them in AWS Secrets Manager and ACM.

## Purpose

Creates the SSL certificates needed for the proxy to terminate TLS connections from the NLB.

## What It Creates

- Self-signed TLS certificate using ECDSA P-256 (wildcard for `*.yourdomain.com`)
- Private key
- AWS Secrets Manager secret (stores certificate + key, 7-day recovery window)
- ACM certificate (for NLB)

## Prerequisites

- `0-backend-setup` completed
- `configure-modules.sh` script executed

## Before Deployment

```bash
cd 1-prerequisites
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your domain name
```

## Configuration

Edit `terraform.tfvars`:

```hcl
aws_region   = "us-east-1"
domain_name  = "db.mydomain.com"  # Your domain
organization = "MyCompany"
```

After changing the name of the `domain_name` in `terraform.tfvars`, deploy using:

## Deployment
```bash
terraform init
terraform plan
terraform apply
```

## Outputs

After successful apply:

```
certificate_arn        = "arn:aws:acm:..."
certificate_secret_arn = "arn:aws:secretsmanager:..."
domain_name            = "db.mydomain.com"
```

These outputs are automatically used by `2-infrastructure` module.

## Next Step

Proceed to [2-infrastructure](../2-infrastructure/README.md)
