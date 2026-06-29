# Self-Managed Active Directory for Amazon RDS for Db2 (Terraform)

This sample provisions a **two-node self-managed Microsoft Active Directory
forest** on Amazon EC2 Windows Server domain controllers and prepares it for
**Amazon RDS for Db2 self-managed AD (Kerberos) integration**. The
organizational unit (OU), the delegated service account, the required access
control entries (ACEs), and the KMS-encrypted AWS Secrets Manager secret are
all created automatically during bootstrap.

The same code runs in **AWS GovCloud (US)** and **commercial** AWS Regions —
only the region, AWS CLI profile, and existing VPC/subnet IDs change.

> **Manual workflow.** This is a step-by-step, operator-driven deployment
> (`terraform init` / `plan` / `apply`), not a CI/CD pipeline.

---

## Architecture

```
                 Existing VPC (you provide)
   ┌───────────────────────────────────────────────────────┐
   │  Private subnet AZ-1            Private subnet AZ-2   │
   │   ┌───────────────┐             ┌───────────────┐     │
   │   │  DC1 (forest) │◄──AD repl──►│  DC2 (replica)│     │
   │   │  10.x.x.10    │             │  10.y.y.10    │     │
   │   └───────┬───────┘             └────────┬──────┘     │
   │           │   both in DC security group  │            │
   │           └──────────────┬───────────────┘            │
   │                          │ AD ports (53/88/389/...)   │
   │                   ┌───────▼────────┐                  │
   │                   │ RDS for Db2    │ (joins later)    │
   │                   └────────────────┘                  │
   └──────────── NAT egress for bootstrap ─────────────────┘

   AWS Secrets Manager (KMS-encrypted)  ── RDS reads service-account creds
```

### What it creates

| Resource | Details |
|---|---|
| EC2 instances | 2 × Windows Server 2022 domain controllers (DC1 = forest root, DC2 = additional DC), one per AZ, static private IPs, IMDSv2 enforced, EBS-encrypted, detailed monitoring enabled |
| Active Directory | Forest (`domain_fqdn`), OU (`ou_name`), delegated service account (`svc_account_name`) with the 7 ACEs RDS for Db2 requires |
| KMS key | Dedicated symmetric multi-Region key with rotation enabled, encrypting both Secrets Manager secrets |
| Secrets Manager | Bootstrap secret (admin/DSRM/service-account passwords) + RDS self-managed AD secret (`SELF_MANAGED_ACTIVE_DIRECTORY_USERNAME`/`_PASSWORD`) with `rds.amazonaws.com` resource policy |
| Security group | DC security group with full AD port matrix |
| IAM | EC2 instance role with SSM core access and least-privilege secret read |

### What it does NOT create

**No networking.** It deploys into an **existing VPC** and two **existing
private subnets** that you supply. You are responsible for the VPC, subnets,
route tables, and NAT gateway.

---

## Prerequisites

- Terraform >= 1.5
- AWS CLI v2
- An existing VPC with **two private subnets in different Availability Zones**,
  each with **outbound internet through a NAT gateway** (required during
  bootstrap to install the AWS Tools PowerShell module and reach Secrets Manager)
- AWS credentials with permission to create the resources listed above

> **GovCloud note.** As of publication, RDS for Db2 self-managed AD is not yet
> generally available in GovCloud and may require your account to be enabled.
> If the join command returns
> `InvalidParameterCombination: Joining self-managed domains is not enabled`,
> contact your AWS account team. The AD forest and all other resources deploy
> normally; only the final RDS join step is affected.

---

## Step 1 — Discover your VPC, subnets, and Availability Zones

Find the existing VPC and two private subnets for the domain controllers.

```bash
export AWS_PROFILE=<your-profile>
export AWS_REGION=<your-region>
export VPC_ID=<your-vpc-id>

# VPC CIDR
aws ec2 describe-vpcs --vpc-ids "$VPC_ID" \
  --query 'Vpcs[0].CidrBlock' --output text

# All subnets in the VPC (ID, CIDR, AZ, Name)
aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" \
  --query 'Subnets[].{ID:SubnetId,CIDR:CidrBlock,AZ:AvailabilityZone,Name:Tags[?Key==`Name`]|[0].Value}' \
  --output table

# Confirm each chosen subnet has a 0.0.0.0/0 route via a NAT gateway
aws ec2 describe-route-tables --filters Name=vpc-id,Values="$VPC_ID" \
  --query 'RouteTables[].{RT:RouteTableId,Assoc:Associations[].SubnetId,Routes:Routes[].{Dest:DestinationCidrBlock,NAT:NatGatewayId}}'
```

### Example

Say your VPC is `10.0.0.0/16` with these subnets:

| Subnet ID | CIDR | AZ | Role |
|-----------|------|----|------|
| `subnet-aaa...` | `10.0.128.0/20` | us-east-1a | private (NAT) → **DC1** |
| `subnet-bbb...` | `10.0.144.0/20` | us-east-1b | private (NAT) → **DC2** |

Pick static DC IPs inside each subnet's CIDR, avoiding AWS-reserved addresses
(the first four and the last IP of each subnet):

- DC1 → `10.0.128.10` (in `10.0.128.0/20`)
- DC2 → `10.0.144.10` (in `10.0.144.0/20`)

---

## Step 2 — Security groups

This sample creates one security group (`<name_prefix>-dc-sg`) for the domain
controllers. It contains:

- A **self-referencing all-traffic rule** so the two DCs replicate freely.
- The **AD client port matrix** from within the VPC CIDR: TCP 53/88/135/389/445/464/636/3268/3269/9389, UDP 53/88/123/389/464, and the RPC dynamic range 49152–65535.
- Optional RDP from `rdp_ingress_cidr` (leave empty to use AWS Systems Manager).

### Connecting RDS for Db2 to the domain controllers

RDS for Db2 must reach the DCs on those AD ports. Choose one approach:

**Option A (recommended) — attach the DC security group to the RDS instance.**
The DC SG's self rule allows all traffic between its members, so adding it to
your RDS instance grants full RDS↔DC connectivity:

```bash
aws rds modify-db-instance --db-instance-identifier <DB_ID> \
  --vpc-security-group-ids <RDS_OWN_SG> <DC_SECURITY_GROUP_ID> \
  --apply-immediately --region <REGION>
```

**Option B — reference the RDS instance SG as a source on the DC SG.** If you
prefer not to share security groups, add an inbound rule to the DC SG referencing
the RDS SG. The DC SG already allows the entire VPC CIDR, so if your RDS instance
is in the same VPC this is typically already satisfied.

Verify:
```bash
aws ec2 describe-security-groups --group-ids <DC_SECURITY_GROUP_ID> \
  --query 'SecurityGroups[0].IpPermissions' --region <REGION>
```

---

## Step 3 — Configure your variables

Copy the example for your partition and edit the values marked `# CHANGE`.
The real `*.tfvars` file is git-ignored so account-specific IDs are never committed.

```bash
# For GovCloud
cp terraform.tfvars.example terraform.tfvars

# For commercial regions
cp commercial.tfvars.example commercial.tfvars
```

### Variables you MUST change

| Variable | What to set | Example |
|---|---|---|
| `aws_region` | Target AWS Region | `us-east-1` |
| `aws_profile` | Your named AWS CLI profile | `default` |
| `vpc_id` | Existing VPC ID (Step 1) | `vpc-0abc1234...` |
| `dc_subnet_ids` | Two private subnet IDs (DC1 first) | `["subnet-0aaa...","subnet-0bbb..."]` |
| `dc1_private_ip` | IP inside `dc_subnet_ids[0]` CIDR | `10.0.128.10` |
| `dc2_private_ip` | IP inside `dc_subnet_ids[1]` CIDR | `10.0.144.10` |
| `domain_fqdn` | Your AD domain name | `corp.example.com` |
| `domain_netbios_name` | NetBIOS name (≤ 15 chars, uppercase) | `CORP` |

### Variables you MAY change

| Variable | Default | Notes |
|---|---|---|
| `name_prefix` | `selfmanaged-ad` | Prefix for all resource names |
| `ou_name` | `RDSDb2` | OU created under the domain root |
| `svc_account_name` | `rdsdb2svc` | Delegated service account (sAMAccountName only, no domain prefix) |
| `instance_type` | `t3.large` | DC EC2 instance type |
| `root_volume_size` | `60` | Root EBS volume size (GiB) |
| `rds_db_arn_pattern` | `""` | Restricts the secret policy to a specific DB ARN; empty = all in account/Region |
| `rdp_ingress_cidr` | `""` | Optional CIDR for RDP; leave empty to use SSM; never `0.0.0.0/0` |

---

## Step 4 — Deploy

### GovCloud (auto-loads `terraform.tfvars`)
```bash
terraform init
terraform validate
terraform plan  -out=govcloud.tfplan
terraform apply govcloud.tfplan
```

### Commercial region (explicit var file)
```bash
terraform init
terraform plan  -var-file=commercial.tfvars -out=commercial.tfplan
terraform apply commercial.tfplan
```

`terraform apply` completes in a few minutes (instances launch quickly). The
**AD forest build then runs inside the instances** for approximately 30–45 minutes:

1. DC1 installs AD DS, runs `Install-ADDSForest`, and reboots automatically.
2. A one-time scheduled task fires after reboot, waits for AD DS to be ready,
   then creates the OU, service account, and the seven ACEs, and unregisters itself.
3. DC2 points its DNS at DC1, waits for the forest, then joins as the second
   domain controller.

---

## Step 5 — Verify (via AWS Systems Manager, no RDP needed)

```bash
export DC1_ID=<dc1_instance_id output>
export DC2_ID=<dc2_instance_id output>

# Confirm both DCs are registered with SSM
aws ssm describe-instance-information \
  --filters Key=InstanceIds,Values="$DC1_ID,$DC2_ID" \
  --query 'InstanceInformationList[].{Id:InstanceId,Ping:PingStatus}' \
  --output table --region <REGION>

# Read the DC1 configuration log (shows OU + service account + ACEs applied)
CID=$(aws ssm send-command \
  --document-name "AWS-RunPowerShellScript" --instance-ids "$DC1_ID" \
  --parameters 'commands=["(Get-Service ADWS).Status","(Get-ADDomain).DNSRoot","Get-Content C:\\ad-rds-configure.log -Tail 30"]' \
  --query 'Command.CommandId' --output text --region <REGION>)
aws ssm get-command-invocation --command-id "$CID" --instance-id "$DC1_ID" \
  --query 'StandardOutputContent' --output text --region <REGION>

# Confirm both DCs promoted (expected output: 2)
CID=$(aws ssm send-command \
  --document-name "AWS-RunPowerShellScript" --instance-ids "$DC1_ID" \
  --parameters 'commands=["(Get-ADDomainController -Filter * | Measure-Object).Count"]' \
  --query 'Command.CommandId' --output text --region <REGION>)
aws ssm get-command-invocation --command-id "$CID" --instance-id "$DC1_ID" \
  --query 'StandardOutputContent' --output text --region <REGION>
```

Use `Show-OUDelegation.ps1` (in `../rds-db2-self-managed-ad/`) via SSM to
display the delegated ACEs for `rdsdb2svc` in human-readable form.

---

## Step 6 — Join Amazon RDS for Db2 to the domain

After apply, Terraform outputs a ready-to-use `rds_join_command_hint`. Ensure
the RDS instance can reach the DCs (Step 2), then:

```bash
aws rds modify-db-instance --db-instance-identifier <DB_ID> \
  --domain-fqdn "<domain_fqdn>" \
  --domain-ou "<domain_ou output>" \
  --domain-auth-secret-arn "<rds_self_managed_ad_secret_arn output>" \
  --domain-dns-ips <dc1_private_ip> <dc2_private_ip> \
  --apply-immediately --region <REGION>
```

Verify:
```bash
aws rds describe-db-instances --db-instance-identifier <DB_ID> \
  --query 'DBInstances[0].DomainMemberships' --region <REGION>
```

A successful join shows `"Status": "kerberos-enabled"` with your FQDN, OU,
secret ARN, and DNS IPs.

---

## Outputs

| Output | Description | How to use |
|---|---|---|
| `dc1_private_ip`, `dc2_private_ip` | Static DC IPs | `--domain-dns-ips` |
| `domain_ou` | Distinguished name of the RDS OU | `--domain-ou` |
| `rds_self_managed_ad_secret_arn` | Secret ARN for the service-account credentials | `--domain-auth-secret-arn` |
| `dc_security_group_id` | DC security group ID | Attach to RDS (Step 2) |
| `ad_secret_kms_key_arn` | KMS key ARN | Reference |
| `service_account_name` | Delegated service account sAMAccountName | Reference |
| `dc1_instance_id`, `dc2_instance_id` | DC instance IDs | SSM / verification |
| `rds_join_command_hint` | Pre-filled `modify-db-instance` command | Run in Step 6 |

---

## Companion scripts

The `../rds-db2-self-managed-ad/` directory contains:

- `Grant-ADDomainJoinPrivileges.ps1` — interactive version of the OU/service-account/ACL setup. Useful for understanding what the automation does, or for re-running manually on an existing domain.
- `Show-OUDelegation.ps1` — displays the delegated ACEs for a given user in human-readable form. Use this via SSM to verify the bootstrap automation completed correctly.

---

## Cleanup

```bash
terraform destroy                                # GovCloud
terraform destroy -var-file=commercial.tfvars    # commercial
```

Both Secrets Manager secrets use `recovery_window_in_days = 0`, so their
names are available immediately after a destroy/re-apply cycle.

---

## Security considerations

- **Terraform state contains generated passwords.** Never commit `*.tfstate*`.
  For team use, configure an encrypted remote backend (Amazon S3 + SSE-KMS +
  DynamoDB state locking).
- **Real `*.tfvars` files contain account-specific IDs.** They are git-ignored;
  only `*.tfvars.example` files (with placeholder values) are committed.
- **No secrets in code or user-data.** Passwords are generated by
  `random_password` resources and read from Secrets Manager at instance boot.
  The bootstrap PowerShell scripts never log password values.
- **Encryption.** EBS root volumes are encrypted. Both Secrets Manager secrets
  are encrypted with a dedicated customer-managed KMS key with rotation enabled.
- **Least privilege.** The EC2 instance role is scoped to
  `AmazonSSMManagedInstanceCore` plus `secretsmanager:GetSecretValue` and
  `kms:Decrypt` on the bootstrap secret only. The RDS AD secret resource policy
  allows only `rds.amazonaws.com` with `aws:SourceAccount` and `aws:SourceArn`
  conditions (confused-deputy protection).
- **IMDSv2 only.** Both DC instances enforce `http_tokens = required`.
- **No public IP.** DCs have `associate_public_ip_address = false` and are
  managed via AWS Systems Manager Fleet Manager. RDP is disabled by default.
- **Egress.** The DC security group allows all outbound traffic so domain
  controllers can reach the NAT gateway during bootstrap (Windows activation,
  PowerShell Gallery, Secrets Manager). In locked-down environments without
  NAT egress, replace with VPC interface endpoints for `secretsmanager`, `ssm`,
  `ssmmessages`, and `ec2messages`, and pre-bake the AWS Tools PowerShell module
  into a custom AMI.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Joining self-managed domains is not enabled` | Self-managed AD not yet available in this Region for your account. Contact your AWS account team. The AD forest deploys normally; only the RDS join step is gated. |
| DC bootstrap stops at `Install-Module` | PowerShell module clobber conflict. This module already passes `-AllowClobber -SkipPublisherCheck`. Check `C:\dc-bootstrap.log` via SSM. |
| DC2 never joins | DC2 polls DC1 on LDAP 389 + DNS. Check `C:\dc-bootstrap.log` on DC2 via SSM and verify DC1 completed promotion first. |
| Secret name conflict on re-apply | Leftover secret in its recovery window from a prior run. Run `aws secretsmanager delete-secret --secret-id <name> --force-delete-without-recovery --region <REGION>`. |

---

## File map

```
smad-terraform/
├── versions.tf                   provider version constraints
├── providers.tf                  provider config, partition/region/AMI data sources
├── variables.tf                  all input variables
├── locals.tf                     computed values (base DN, OU DN, rendered config script)
├── network-data.tf               looks up the existing VPC + DC subnets (creates nothing)
├── security.tf                   DC security group + AD port matrix
├── iam.tf                        EC2 instance role, SSM policy, scoped secret + KMS access
├── secrets.tf                    passwords, bootstrap secret, KMS key, RDS AD secret + policy
├── dc.tf                         DC1 + DC2 EC2 instances
├── outputs.tf                    outputs including rds_join_command_hint
├── templates/
│   ├── dc1_userdata.ps1.tpl      forest creation + registers post-reboot config task
│   ├── dc2_userdata.ps1.tpl      joins as additional domain controller
│   └── configure-rds-ad.ps1.tpl OU + service account + 7 ACEs (non-interactive)
├── terraform.tfvars.example      GovCloud example → copy to terraform.tfvars
└── commercial.tfvars.example     commercial example → copy to commercial.tfvars
```

---

## Related resources

- [Using Kerberos authentication for Amazon RDS for Db2](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/db2-kerberos.html)
- [Authenticate Amazon RDS for Db2 instances using on-premises Microsoft Active Directory](https://aws.amazon.com/blogs/database/authenticate-amazon-rds-for-db2-instances-using-on-premises-microsoft-active-directory-and-kerberos/)
- [Companion: manual self-managed AD setup scripts](../rds-db2-self-managed-ad/)
