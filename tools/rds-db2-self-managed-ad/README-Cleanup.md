# Cleanup — remove the self-managed AD setup for RDS for Db2

Run these steps when you are done testing to avoid ongoing charges and to
remove the principals this walkthrough created in your Active Directory.
Tear resources down in the **reverse order** they were created so that
dependencies are removed cleanly.

> **Replace example values before running any command.**
>
> | Example value | What to replace it with |
> |---|---|
> | `123456789012` | Your 12-digit AWS account ID |
> | `us-east-1` | The AWS Region where your resources live |
> | `<your-profile>` | AWS CLI profile name (omit `--profile` if using the default) |
> | `your-db-instance` | Your RDS for Db2 instance identifier |
> | `i-0123456789abcdef0` | Your EC2 client instance ID |
> | `rds-db2-self-managed-ad-secret` | Your Secrets Manager secret name |
> | `rds-db2-self-managed-ad-key` | Your KMS key alias |
> | `OU=RDSDb2,DC=corp,DC=com` | The delegated OU distinguished name |
> | `rdsdb2svc` | The sAMAccountName of the AD service account |

---

## Cleanup checklist

| # | Resource | Created in | Recurring cost |
|---|----------|------------|----------------|
| 1 | EC2 Db2 client instance | Step 5 (client) | Yes — compute + EBS |
| 2 | RDS for Db2 instance (or just the domain membership) | Step 4 | Yes — compute + storage |
| 3 | Secrets Manager secret | Step 3 | Yes — per secret/month |
| 4 | KMS key + alias | Step 2 | Yes — per key/month |
| 5 | AD OU, service account, delegated ACEs, leftover computer/user objects | Step 1 | No |

---

## Step 1 — Terminate the EC2 Db2 client

If you launched a dedicated EC2 instance to test the connection, leave the
domain first (so it removes its own computer object), then terminate it.

On the EC2 instance:

```bash
# Destroy any Kerberos tickets and leave the domain
kdestroy 2>/dev/null || true
sudo realm leave <your-domain-fqdn>
```

Then terminate the instance:

```bash
aws ec2 terminate-instances \
    --instance-ids "i-0123456789abcdef0" \
    --region "us-east-1" --profile "<your-profile>"
```

> If you created a dedicated IAM instance profile/role for this client
> (with `rds:Describe*` and `secretsmanager:GetSecretValue`), delete it as
> well once the instance is terminated.

---

## Step 2 — Remove self-managed AD from the RDS instance (or delete it)

### Option A — Keep the instance, remove the domain join

Detach the instance from your AD domain without deleting it:

```bash
aws rds modify-db-instance \
    --db-instance-identifier "your-db-instance" \
    --disable-domain \
    --apply-immediately \
    --region "us-east-1" --profile "<your-profile>"

# Reboot for the change to take effect
aws rds reboot-db-instance \
    --db-instance-identifier "your-db-instance" \
    --region "us-east-1" --profile "<your-profile>"
```

### Option B — Delete the instance entirely

> **Destructive and irreversible.** This permanently deletes the database.
> Take a final snapshot first if you may need the data.

```bash
aws rds delete-db-instance \
    --db-instance-identifier "your-db-instance" \
    --final-db-snapshot-identifier "your-db-instance-final" \
    --region "us-east-1" --profile "<your-profile>"

# Or, to skip the snapshot (data is lost permanently):
# aws rds delete-db-instance \
#     --db-instance-identifier "your-db-instance" \
#     --skip-final-snapshot \
#     --region "us-east-1" --profile "<your-profile>"
```

---

## Step 3 — Delete the Secrets Manager secret

```bash
aws secretsmanager delete-secret \
    --secret-id "rds-db2-self-managed-ad-secret" \
    --recovery-window-in-days 7 \
    --region "us-east-1" --profile "<your-profile>"
```

> A 7-day recovery window lets you restore the secret if needed. Add
> `--force-delete-without-recovery` instead to delete immediately (cannot
> be undone).

---

## Step 4 — Schedule KMS key deletion

You cannot delete a KMS key immediately — you schedule it for deletion
(minimum 7 days, default 30). The alias can be removed right away.

```bash
# Delete the alias
aws kms delete-alias \
    --alias-name "alias/rds-db2-self-managed-ad-key" \
    --region "us-east-1" --profile "<your-profile>"

# Find the key ID, then schedule deletion
KEY_ID=$(aws kms describe-key \
    --key-id "alias/rds-db2-self-managed-ad-key" \
    --region "us-east-1" --profile "<your-profile>" \
    --query 'KeyMetadata.KeyId' --output text)

aws kms schedule-key-deletion \
    --key-id "$KEY_ID" \
    --pending-window-in-days 7 \
    --region "us-east-1" --profile "<your-profile>"
```

> Delete the alias **before** scheduling key deletion, or run both in
> either order — but make sure no other resource still uses the key. If
> you used this key for instance storage encryption (`--kms-key-id` on the
> RDS instance), do not delete it until that instance is gone, otherwise
> the encrypted data and snapshots become unrecoverable.

---

## Step 5 — Remove the AD objects

These steps undo the delegation from Step 1. Run them on a domain
controller or a management host with RSAT / the Active Directory module.

> **PowerShell** — removes the service account, then the OU and everything
> RDS created inside it (computer/user objects). Adjust names to match
> your environment.

```powershell
Import-Module ActiveDirectory

# Remove the dedicated service account
Remove-ADUser -Identity "rdsdb2svc" -Confirm:$false

# The OU is protected from accidental deletion by default — clear the flag,
# then delete the OU and all child objects RDS created during domain joins
$ou = "OU=RDSDb2,DC=corp,DC=com"
Set-ADOrganizationalUnit -Identity $ou -ProtectedFromAccidentalDeletion $false
Remove-ADOrganizationalUnit -Identity $ou -Recursive -Confirm:$false
```

If you prefer to keep the OU and only remove the delegated permissions,
reset its ACL instead of deleting it:

```powershell
# Inspect current delegation first
.\Show-OUDelegation.ps1 -OU "OU=RDSDb2,DC=corp,DC=com" -SamAccountName rdsdb2svc

# Then remove the ACEs granted to the service account via dsacls, e.g.
dsacls "OU=RDSDb2,DC=corp,DC=com" /R "CORP\rdsdb2svc"
```

> Deleting the OU recursively also removes any stale computer or user
> objects RDS for Db2 created there during domain joins. Verify with
> `Get-ADObject -SearchBase "OU=RDSDb2,DC=corp,DC=com" -Filter *` before
> and after.

---

## Verify everything is gone

```bash
# RDS instance removed (or domain membership cleared)
aws rds describe-db-instances \
    --db-instance-identifier "your-db-instance" \
    --region "us-east-1" \
    --query 'DBInstances[0].{Status:DBInstanceStatus,Domain:DomainMemberships}' 2>/dev/null \
    || echo "RDS instance deleted"

# Secret scheduled for deletion
aws secretsmanager describe-secret \
    --secret-id "rds-db2-self-managed-ad-secret" \
    --region "us-east-1" \
    --query '{Name:Name,DeletedDate:DeletedDate}'

# KMS key scheduled for deletion (KeyState should be PendingDeletion)
aws kms describe-key \
    --key-id "$KEY_ID" \
    --region "us-east-1" \
    --query 'KeyMetadata.{KeyId:KeyId,KeyState:KeyState}'
```

```powershell
# AD service account and OU removed
Get-ADUser -Identity "rdsdb2svc" -ErrorAction SilentlyContinue
Get-ADOrganizationalUnit -Identity "OU=RDSDb2,DC=corp,DC=com" -ErrorAction SilentlyContinue
```
