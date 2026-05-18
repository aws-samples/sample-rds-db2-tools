# Amazon RDS for DB2 Monitoring Dashboard

## Overview

This package deploys a CloudWatch monitoring dashboard for Amazon RDS for Db2.
It creates a Lambda function, EventBridge schedules, CloudWatch dashboards, and
all required VPC endpoints automatically.

## Scripts

| Script | Description |
|---|---|
| `db2monitor.sh` | Main deployment script (online and airgap modes) |
| `db2mon-airgap.sh` | Artifact downloader/uploader for air-gapped environments |
| `db2mon-diag.sh` | VPC / endpoint / security-group diagnostics |
| `db2mon-cleanup.sh` | Remove all deployed resources |

---

## Online Deployment

> For CloudShell or EC2 instances with internet access.

### Prerequisites

- AWS CLI v2
- `jq`
- Required IAM permissions (run `--check-permissions` to verify)

### Quick Start

**1. Download the script:**
```bash
curl -fsSL https://aws-blogs-artifacts-public.s3.us-east-1.amazonaws.com/artifacts/DBBLOG-3742/db2mon-unified.sh | bash
```
This saves `db2monitor.sh` and companion scripts to the current directory.

**2. (Optional) Pre-configure:**
```bash
vi db2monitor.env
source db2monitor.env
```

**3. Deploy:**
```bash
./db2monitor.sh --region <region>
```

Or with all options inline:
```bash
REGION=us-east-1 DB_INSTANCE_ID=mydb2 DBNAME=DB2DB TAG=PROD \
  ./db2monitor.sh
```

**4. Check permissions before deploying (optional):**
```bash
./db2monitor.sh --check-permissions --region <region>
```

### Modules

```bash
./db2monitor.sh --module install   # Deploy dashboards (default)
./db2monitor.sh --module start     # Enable monitoring schedule
./db2monitor.sh --module stop      # Disable monitoring schedule
./db2monitor.sh --module refresh   # Reload secret values
```

---

## Airgap Deployment

> For private subnets with no internet access. Uses a private S3 bucket pre-populated with all artifacts.

### Step 1 — Download artifacts (machine WITH internet, no AWS needed)

```bash
# Get the airgap script
curl -fsSL https://aws-blogs-artifacts-public.s3.us-east-1.amazonaws.com/artifacts/DBBLOG-3742/db2mon-unified.sh | bash

# Download all artifacts for your target region
./db2mon-airgap.sh --mode download --region <region>
# Saves everything to ./db2mon-artifacts/
```

Then copy to your private-subnet machine (USB, bastion, S3 transfer, etc.):
- `db2monitor.sh`
- `db2mon-airgap.sh`
- `db2mon-artifacts/` (entire directory)

### Step 2 — Upload artifacts to S3 (machine WITH AWS access)

```bash
./db2mon-airgap.sh --mode upload --region <region>
```

Or combine download + upload in one shot (if the machine has both internet and AWS access):
```bash
./db2mon-airgap.sh --mode both --region <region>
```

This creates: `s3://lambda-functions-<account>-<region>/`

### Step 3 — Deploy from the private-subnet EC2 instance

```bash
# Pull the monitor script from your private bucket (uses S3 Gateway endpoint)
aws s3 cp s3://lambda-functions-<account>-<region>/db2monitor.sh . \
  && chmod +x db2monitor.sh

# Deploy
BUCKET=lambda-functions-<account>-<region> \
REGION=<region> \
./db2monitor.sh
```

Or with all options:
```bash
BUCKET=lambda-functions-<account>-<region> \
REGION=<region> \
DB_INSTANCE_ID=mydb2 DBNAME=DB2DB TAG=PROD \
./db2monitor.sh
```

### VPC Requirements for Airgap

The following VPC endpoints must exist **before** running `db2monitor.sh`.
These are created automatically by `launch-ec2.sh` when launching the deployment EC2 instance.

**Minimum (required to run `db2monitor.sh` at all):**

| Type | Service |
|---|---|
| Gateway | `com.amazonaws.<region>.s3` |
| Interface | `com.amazonaws.<region>.ssm` |
| Interface | `com.amazonaws.<region>.ssmmessages` |
| Interface | `com.amazonaws.<region>.ec2messages` |
| Interface | `com.amazonaws.<region>.ec2` |

**Created automatically by `db2monitor.sh` during deployment:**

`sts`, `secretsmanager`, `monitoring`, `logs`, `lambda`, `rds`, `sns`, `sqs`, `scheduler`, `cloudformation`

**If you are NOT using `launch-ec2.sh`**, create the minimum endpoints manually:

```bash
aws ec2 create-vpc-endpoint --vpc-id <vpc-id> \
  --service-name com.amazonaws.<region>.s3 \
  --vpc-endpoint-type Gateway \
  --route-table-ids <rtb-id> --region <region>

for svc in ssm ssmmessages ec2messages ec2; do
  aws ec2 create-vpc-endpoint --vpc-id <vpc-id> \
    --service-name com.amazonaws.<region>.$svc \
    --vpc-endpoint-type Interface \
    --subnet-ids <subnet-id> \
    --security-group-ids <sg-id> \
    --private-dns-enabled --region <region>
done
```

**DNS settings required on the VPC:**
- `enableDnsSupport = true`
- `enableDnsHostnames = true`

> `db2monitor.sh` enables these automatically if the role has `ec2:ModifyVpcAttribute`.

---

## Diagnostics

Run before deploying to catch VPC / SG / endpoint issues:

```bash
./db2mon-diag.sh --region <region>
```

Checks performed:
- AWS credentials and region
- VPC endpoints (all required services)
- Security group rules (inbound DB2 port, outbound 443)
- `nc` connectivity to RDS endpoint
- IAM permissions (with timeout-safe simulate)
- S3 bucket and artifact presence

---

## Cleanup

Remove all resources deployed by this package:

```bash
./db2mon-cleanup.sh --region <region>
```

Removes: EventBridge schedules, Lambda function, Lambda layer, IAM role,
VPC endpoints, CloudWatch dashboard, Secrets Manager secret,
SSM parameter, S3 bucket contents, CloudFormation stacks.

To remove a specific instance only:
```bash
DB_INSTANCE_ID=mydb2 DBNAME=DB2DB TAG=PROD \
  ./db2mon-cleanup.sh --region <region>
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `REGION` | AWS region (required if not in AWS config) |
| `BUCKET` | Private S3 bucket name — triggers airgap mode when set |
| `PROFILE` | AWS CLI profile (default: `"default"`) |
| `DB_INSTANCE_ID` | RDS DB2 instance identifier |
| `DBNAME` | Database name to monitor (default: prompted) |
| `TAG` | Single-word deployment tag, e.g. `PROD`, `ACME` (default: prompted) |
| `PASSWORD` | DB master password (auto-detected from RDS managed secret) |
| `SUBNET_IDS` | Space-separated subnet IDs to use for Lambda (optional) |
| `VERBOSE` | Set to `"true"` for debug output |

---

## Stack and Resource Naming

**CloudFormation stacks:**
- `DB2-Dashboard-<DB_INSTANCE_ID>-<DBNAME>-<TAG>`
- `DB2-Dashboard-EventBridge-<DB_INSTANCE_ID>-<DBNAME>-<TAG>`
- `db2mon-export-cfn-<region>`

**Secrets Manager:**
- `SM-<DB_INSTANCE_ID>-<DBNAME>-<TAG>`

**SSM registry:**
- `/db2mon/instances` — StringList of all registered secret names

**EventBridge schedules:**
- `db2mon-cw-<DB_INSTANCE_ID>-<DBNAME>-<TAG>`
- `db2mon-s3-<DB_INSTANCE_ID>-<DBNAME>-<TAG>`

**Lambda:**
- `DB2Mon-Lambda-Function-<region>`
- `DB2Mon-Lambda-Role-<region>`
- `DB2Mon-Layer-<region>`

**S3 bucket:**
- `lambda-functions-<account>-<region>`

---

## Support

For issues, run diagnostics first:

```bash
./db2mon-diag.sh --region <region>
```

## Optional - Building Lambda Function Archive and Layer

The Lambda function `DB2Mon-Code.zip` and the layer `DB2Mon-Layer.zip` are downloaded through the script and uploaded to your local Amazon S3 bucket. 

If you want to makee changes in the Lambda function, you have the following two choices.

- Open Lambda function in AWS Console, make changes and publish the new code.
- Make changes locally in your development environment, zip the function and publish the new Lambda function either using the console or by using AWS CLI commamd.

```
cd DB2Mon-Code
zip -r ../DB2Mon-Code.zip .
```

### Building Lambda Layer

Building Lamvda layer is a bit tricky as it requires that the build process of the Lambda Layer is done on a AL2023 Linux EC2 and not on your MacBook or Windows Laptop. Install Python3.12 on AL2023 and follow the process below to build a compact Lambda Layer.

```
# --- Layer zip ---
rm -rf python ../DB2Mon-Layer.zip
mkdir -p python
cd python
sudo pip3.12 install pandas -t . 
sudo pip3.12 install ibm_db -t .
# The following is required by db2 client to use PAM
# The following lib is required from AL 2023 for Python 3.12
sudo cp /usr/lib64/libpam.so.0 ../ibm_db.libs/

# Remove directories and files not required in the Layer to keep the size small
rm -rf __pycache__/
rm -rf *.dist-info
find . -name "tests" -type d | xargs -I{} rm -rf {} 
find . -name "docs" -type d | xargs -I{} -rf {} 
find . -name "__pycache__" -type d | xargs -I{} rm -rf {}
# Boto is not required as Lambda runtime includes boto automatically
rm -rf boto*

zip -r ../DB2Mon-Layer.zip python/
rm -rf python
echo "Built: ../DB2Mon-Layer.zip"
```




