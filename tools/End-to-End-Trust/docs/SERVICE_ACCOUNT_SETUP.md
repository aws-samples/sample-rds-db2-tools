# Service Account Setup for Terraform

This guide covers credential management for running Terraform against AWS.
Options are listed from most secure to least secure. Use the highest option
your environment supports.

> **Never use** `PowerUserAccess` or `AdministratorAccess` managed policies.
> Use the scoped policy in `terraform-service-account-policy.json` which grants
> only the permissions this deployment requires.

---

## Option 1: AWS IAM Identity Center / SSO (Recommended for Local Development)

IAM Identity Center issues short-lived credentials (typically 1–8 hours) that
are automatically refreshed. No static keys are stored on disk.

### Prerequisites

- Your AWS organization must have IAM Identity Center enabled.
- An administrator must assign you a permission set that includes the scoped
  policy from `terraform-service-account-policy.json`.

### Setup

```bash
# One-time configuration
aws configure sso

# Prompts:
#   SSO session name: terraform-rds-proxy
#   SSO start URL:    https://your-org.awsapps.com/start
#   SSO region:       us-east-1
#   Account ID:       (select from list)
#   Permission set:   TerraformRDSProxy (or equivalent)
#   Profile name:     terraform-rds-proxy
```

This writes a profile to `~/.aws/config` with no credentials — credentials are
fetched on demand via the SSO token.

### Daily Usage

```bash
# Authenticate (opens browser, valid for the session duration)
aws sso login --profile terraform-rds-proxy

# Use for all Terraform operations
export AWS_PROFILE=terraform-rds-proxy

cd 0-backend-setup && terraform init && terraform plan && terraform apply
cd ../1-prerequisites && terraform init && terraform plan && terraform apply
cd ../2-infrastructure && terraform init && terraform plan && terraform apply
cd ../3-mappings && terraform init && terraform plan && terraform apply
```

### Verify Identity

```bash
AWS_PROFILE=terraform-rds-proxy aws sts get-caller-identity
```

---

## Option 2: IAM Role with AssumeRole (Local Development without SSO)

If IAM Identity Center is not available, assume a role from your existing
personal credentials. Role sessions expire (default 1 hour, max 12 hours).

### Step 1: Create the IAM Role

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
YOUR_USER_ARN=$(aws sts get-caller-identity --query Arn --output text)

cat > trust-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "AWS": "${YOUR_USER_ARN}" },
    "Action": "sts:AssumeRole",
    "Condition": {
      "BoolIfExists": { "aws:MultiFactorAuthPresent": "true" }
    }
  }]
}
EOF

aws iam create-role \
  --role-name TerraformRDSProxyRole \
  --assume-role-policy-document file://trust-policy.json \
  --max-session-duration 3600

aws iam create-policy \
  --policy-name TerraformRDSProxyPolicy \
  --policy-document file://terraform-service-account-policy.json

aws iam attach-role-policy \
  --role-name TerraformRDSProxyRole \
  --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/TerraformRDSProxyPolicy
```

### Step 2: Configure AWS CLI Profile

```bash
cat >> ~/.aws/config << EOF

[profile terraform-rds-proxy]
role_arn = arn:aws:iam::${ACCOUNT_ID}:role/TerraformRDSProxyRole
source_profile = default
region = us-east-1
duration_seconds = 3600
mfa_serial = arn:aws:iam::${ACCOUNT_ID}:mfa/YOUR_MFA_DEVICE
EOF
```

### Step 3: Use the Role

```bash
export AWS_PROFILE=terraform-rds-proxy
# AWS CLI will prompt for MFA token and cache temporary credentials
terraform plan
terraform apply
```

---

## Option 3: OIDC Role Federation (CI/CD — GitHub Actions)

No secrets stored in GitHub. The OIDC provider mints short-lived credentials
per job using the GitHub Actions JWT token.

### Step 1: Create the OIDC Provider (one-time per account)

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

### Step 2: Create the IAM Role

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
GITHUB_ORG="your-github-org"
GITHUB_REPO="your-repo-name"

cat > oidc-trust-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": "repo:${GITHUB_ORG}/${GITHUB_REPO}:*"
      }
    }
  }]
}
EOF

aws iam create-role \
  --role-name TerraformRDSProxyRole \
  --assume-role-policy-document file://oidc-trust-policy.json \
  --max-session-duration 3600

aws iam attach-role-policy \
  --role-name TerraformRDSProxyRole \
  --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/TerraformRDSProxyPolicy
```

### Step 3: GitHub Actions Workflow

```yaml
name: Terraform Deploy

on:
  push:
    branches: [main]

permissions:
  id-token: write   # required for OIDC
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials via OIDC
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::ACCOUNT_ID:role/TerraformRDSProxyRole
          aws-region: us-east-1

      - name: Terraform Init and Apply
        working-directory: Terraform/2-infrastructure
        run: |
          terraform init
          terraform plan
          terraform apply
```

### GitLab CI (OIDC)

```yaml
terraform:
  image: hashicorp/terraform:latest
  id_tokens:
    AWS_TOKEN:
      aud: https://gitlab.com
  variables:
    AWS_DEFAULT_REGION: us-east-1
  before_script:
    - export $(aws sts assume-role-with-web-identity
        --role-arn arn:aws:iam::ACCOUNT_ID:role/TerraformRDSProxyRole
        --role-session-name gitlab-ci
        --web-identity-token $AWS_TOKEN
        --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]'
        --output text | awk '{print "AWS_ACCESS_KEY_ID="$1"\nAWS_SECRET_ACCESS_KEY="$2"\nAWS_SESSION_TOKEN="$3}')
  script:
    - terraform plan
    - terraform apply
```

---

## Option 4: IAM User with Static Keys (Last Resort — Not Recommended)

> **Warning**: Static access keys do not expire. If leaked (e.g., accidentally
> committed to source control), they provide persistent access until manually
> rotated. Use this option only when Options 1–3 are not available, and apply
> all mitigations below.

### Mitigations if you must use static keys

- Enable MFA and enforce it via the IAM policy condition
- Rotate keys every 90 days
- Never store keys in source-controlled files
- Use `~/.aws/credentials` only — never environment variables in shell profiles
- Enable CloudTrail and alert on usage from unexpected IPs

### Setup

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

aws iam create-user --user-name terraform-rds-proxy

aws iam create-policy \
  --policy-name TerraformRDSProxyPolicy \
  --policy-document file://terraform-service-account-policy.json

aws iam attach-user-policy \
  --user-name terraform-rds-proxy \
  --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/TerraformRDSProxyPolicy

# Create key — store output securely, never commit it
aws iam create-access-key --user-name terraform-rds-proxy
```

```ini
# ~/.aws/credentials  (never commit this file)
[terraform-rds-proxy]
aws_access_key_id     = AKIA...
aws_secret_access_key = ...
```

```ini
# ~/.aws/config
[profile terraform-rds-proxy]
region = us-east-1
output = json
```

### Key Rotation

```bash
# Create new key first, update credentials file, then delete old key
aws iam create-access-key --user-name terraform-rds-proxy
# Update ~/.aws/credentials with new key
aws iam delete-access-key \
  --user-name terraform-rds-proxy \
  --access-key-id AKIA_OLD_KEY_ID
```

### Cleanup

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

aws iam detach-user-policy \
  --user-name terraform-rds-proxy \
  --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/TerraformRDSProxyPolicy

aws iam list-access-keys --user-name terraform-rds-proxy
aws iam delete-access-key \
  --user-name terraform-rds-proxy --access-key-id AKIA...

aws iam delete-user --user-name terraform-rds-proxy
aws iam delete-policy \
  --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/TerraformRDSProxyPolicy
```

---

## Troubleshooting

**"Access Denied" on a specific action**

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
PRINCIPAL_ARN=$(aws sts get-caller-identity --query Arn --output text)

aws iam simulate-principal-policy \
  --policy-source-arn ${PRINCIPAL_ARN} \
  --action-names ec2:RunInstances route53:CreateHostedZone \
  --resource-arns "*"
```

If a new Terraform resource requires an additional action, add it to
`terraform-service-account-policy.json` with the narrowest resource scope
possible, then update the policy:

```bash
aws iam create-policy-version \
  --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/TerraformRDSProxyPolicy \
  --policy-document file://terraform-service-account-policy.json \
  --set-as-default
```

**SSO token expired**

```bash
aws sso login --profile terraform-rds-proxy
```

**Role session expired**

```bash
# Re-export the profile — AWS CLI will re-assume the role automatically
export AWS_PROFILE=terraform-rds-proxy
aws sts get-caller-identity
```
