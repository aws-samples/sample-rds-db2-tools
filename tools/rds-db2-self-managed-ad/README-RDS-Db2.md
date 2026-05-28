# Step 6 — Create or modify the RDS for Db2 instance with self-managed AD

This step joins the RDS for Db2 instance to your self-managed Active
Directory domain using the Secret ARN from
[`README-KMS-Secret.md`](./README-KMS-Secret.md).

> **Replace example values before running any command.**
>
> | Example value | What to replace it with |
> |---|---|
> | `<your-db-instance-identifier>` | A unique name for your DB instance |
> | `<your-aws-account-id>` | Your 12-digit AWS account ID |
> | `<your-region>` | AWS Region (e.g. `us-east-1`) |
> | `<your-profile>` | AWS CLI profile name (omit `--profile` if using the default) |
> | `<your-sg-id>` | Security group ID for the RDS instance |
> | `<your-subnet-group>` | DB subnet group name |
> | `<your-parameter-group>` | DB parameter group name |
> | `<your-kms-key-arn>` | KMS key ARN used to encrypt the instance |
> | `<your-monitoring-role-arn>` | IAM role ARN for Enhanced Monitoring |
> | `<your-secret-arn>` | Secrets Manager secret ARN from Step 5 |
> | `<dc-ip-1>` `<dc-ip-2>` | Private IP addresses of your domain controllers |
> | `company.com` | Your AD domain FQDN |
> | `OU=RDSDb2,DC=company,DC=com` | Distinguished name of the OU created in Step 1 |

---

## Basic command

Minimum flags required to join an existing RDS for Db2 instance to
self-managed AD. Use this to modify an instance that is already running:

```bash
aws rds modify-db-instance \
    --region         "<your-region>" \
    --profile        "<your-profile>" \
    --db-instance-identifier "<your-db-instance-identifier>" \
    --domain-fqdn    "company.com" \
    --domain-ou      "OU=RDSDb2,DC=company,DC=com" \
    --domain-auth-secret-arn "<your-secret-arn>" \
    --domain-dns-ips "<dc-ip-1>" "<dc-ip-2>"
```

> After modifying an existing instance, reboot it for the domain join to
> take effect.

---

## Detailed command — create a new instance with self-managed AD

Use this to create a new RDS for Db2 instance already joined to your
self-managed AD domain from the start. All parameters are shown explicitly
so you can see and adjust every setting.

```bash
aws rds create-db-instance \
    --region                        "<your-region>" \
    --profile                       "<your-profile>" \
    \
    # --- Instance identity ---
    --db-instance-identifier        "<your-db-instance-identifier>" \
    --db-instance-class             db.r7i.large \
    --engine                        db2-se \
    --engine-version                11.5.9.0.sb00075854.r1 \
    # ^^^ This was the latest available engine version at the time of writing.
    # Use the latest version available when you run this command:
    # aws rds describe-db-engine-versions --engine db2-se --query 'DBEngineVersions[*].EngineVersion'
    --license-model                 bring-your-own-license \
    \
    # --- Storage ---
    --allocated-storage             40 \
    --storage-type                  gp3 \
    --storage-encrypted \
    --kms-key-id                    "<your-kms-key-arn>" \
    \
    # --- Master credentials ---
    --master-username               admin \
    --manage-master-user-password \
    \
    # --- Network ---
    --vpc-security-group-ids        "<your-sg-id>" \
    --db-subnet-group-name          "<your-subnet-group>" \
    --availability-zone             "<your-region>a" \
    --no-multi-az \
    --no-publicly-accessible \
    --port                          50000 \
    \
    # --- Configuration ---
    --db-parameter-group-name       "<your-parameter-group>" \
    --backup-retention-period       1 \
    --no-deletion-protection \
    \
    # --- Monitoring ---
    --monitoring-interval           15 \
    --monitoring-role-arn           "<your-monitoring-role-arn>" \
    --enable-cloudwatch-logs-exports diag.log notify.log \
    \
    # --- Self-managed AD ---
    --domain-fqdn                   "company.com" \
    --domain-ou                     "OU=RDSDb2,DC=company,DC=com" \
    --domain-auth-secret-arn        "<your-secret-arn>" \
    --domain-dns-ips                "<dc-ip-1>" "<dc-ip-2>" \
    \
    # --- Tags ---
    --tags \
        Key=project,Value=AD \
        Key=zone,Value="<your-region>"
```

### Parameter notes

| Parameter | Notes |
|---|---|
| `--engine-version` | `11.5.9.0.sb00075854.r1` was the latest version at the time of writing. Always use the latest available: `aws rds describe-db-engine-versions --engine db2-se --query 'DBEngineVersions[*].EngineVersion'` |
| `--port` | Default Db2 port is `50000`. Change only if your environment requires a non-standard port |
| `--manage-master-user-password` | RDS manages the master password in Secrets Manager automatically. Omit if you prefer to supply `--master-user-password` directly |
| `--storage-encrypted` + `--kms-key-id` | Use the KMS key created in Step 4, or a separate key for instance storage. These are independent of the AD secret KMS key |
| `--domain-dns-ips` | Supply the private IP addresses of at least two domain controllers for redundancy |
| `--domain-auth-secret-arn` | The Secret ARN output from Step 5 |
| `--no-multi-az` | Change to `--multi-az` for production workloads |
| `--monitoring-interval 15` | Enhanced Monitoring at 15-second granularity. Set to `0` to disable |

---

## Verify the domain join

After the instance reaches `available` status:

```bash
# Check domain join status
aws rds describe-db-instances \
    --db-instance-identifier "<your-db-instance-identifier>" \
    --region "<your-region>" \
    --query 'DBInstances[0].{Status:DBInstanceStatus,Domain:DomainMemberships}'
```

A successful join shows `DomainMemberships` with `Status: joined`.

---

## Modify an existing instance

To add self-managed AD to an instance that was created without it:

```bash
aws rds modify-db-instance \
    --region                 "<your-region>" \
    --db-instance-identifier "<your-db-instance-identifier>" \
    --domain-fqdn            "company.com" \
    --domain-ou              "OU=RDSDb2,DC=company,DC=com" \
    --domain-auth-secret-arn "<your-secret-arn>" \
    --domain-dns-ips         "<dc-ip-1>" "<dc-ip-2>" \
    --apply-immediately
```

Then reboot:

```bash
aws rds reboot-db-instance \
    --db-instance-identifier "<your-db-instance-identifier>" \
    --region "<your-region>"
```
