# Self-managed AD delegation for Amazon RDS for Db2

This folder contains the procedure and tooling to delegate the minimum
Active Directory permissions Amazon RDS for Db2 needs to manage its
principals inside a dedicated OU on a customer-managed AD domain.

Two methods are provided. Choose either UI or PowerShell script. Both methods produce equivalent ACLs — pick whichever method
fits your operations model.

| Method | Doc | When to use |
|---|---|---|
| UI (Delegation of Control Wizard + Security tab) | [`README-UI.md`](./README-UI.md) | Ad-hoc setup, easier to walk through with an AD administrator |
| PowerShell (`Grant-ADDomainJoinPrivileges.ps1`) | [`README-PowerShell.md`](./README-PowerShell.md) | Repeatable, idempotent, scriptable across environments |

## Before you start — replace example values

All commands and scripts in this folder use example values. **Replace them
with your own before running anything.**

| Example value | What to replace it with |
|---|---|
| `RDSDb2` | The name of the OU you create for RDS for Db2 |
| `DC=company,DC=com` | The DC components of your AD domain (e.g. `DC=corp,DC=example,DC=com` for `corp.example.com`) |
| `OU=RDSDb2,DC=company,DC=com` | The full distinguished name of your OU |
| `CORP\rdsdb2svc` | Your AD domain and service account name in `DOMAIN\username` format |
| `rdsdb2svc` | The sAMAccountName (logon name) of your service account |

## Permissions granted

RDS for Db2 represents the principals it provisions as **user** objects
under your OU. The service account therefore needs:

| # | Permission                                                 | Scope                                |
|---|------------------------------------------------------------|--------------------------------------|
| 1 | Create User objects                                        | This object only (the OU)            |
| 2 | Delete User objects                                        | This object only (the OU)            |
| 3 | Create Computer objects                                    | This object only (the OU)            |
| 4 | Delete Computer objects                                    | This object only (the OU)            |
| 5 | Reset Password (extended right)                            | Descendant User objects              |
| 6 | Read  `msDS-SupportedEncryptionTypes`                      | Descendant User objects              |
| 7 | Write `msDS-SupportedEncryptionTypes`                      | Descendant User objects              |
| 8 | Read  `servicePrincipalName`                               | Descendant User objects              |
| 9 | Write `servicePrincipalName`                               | Descendant User objects              |

> **Why ADSI Edit for SPN?** The standard ADUC Delegation of Control Wizard
> and Security tab filter `servicePrincipalName` out of the attribute list
> for User objects. ADSI Edit (`adsiedit.msc`) exposes the full unfiltered
> schema and is the only UI tool that can grant this permission correctly
> scoped to User objects. RDS for Db2 checks SPN permissions on User objects
> — granting it on Computer objects causes the domain join to fail.

## Prerequisites

### 1. Create a dedicated OU

Create a dedicated OU scoped to RDS for Db2 (recommended for least privilege):

1. Open **Active Directory Users and Computers** and select the domain.
2. Right-click the domain and choose **New → Organizational Unit**.
3. Enter a name (e.g. `RDSDb2`).
4. Keep **Protect container from accidental deletion** selected.
5. Click **OK**.

### 2. Create the AD domain service account

The service account credentials will be stored in AWS Secrets Manager later.
Create the account **inside the dedicated OU**, not in the default Users container.

1. In ADUC, expand the domain and select the OU you just created.
2. Right-click inside the OU and choose **New → User**.
3. Enter a first name, last name, and logon name for the user. Click **Next**.
4. Enter a password. Ensure:
   - **User must change password at next login** is **not** selected.
   - **Account is disabled** is **not** selected.
5. Click **Next**, then **Finish**. The new user appears inside your OU.

### 3. Other requirements

- Run on a domain controller, or a domain-joined host with **RSAT: AD DS Tools**.
- Run as a user with permission to modify the OU's ACL (typically Domain Admin).

## Files in this folder

- [`README.md`](./README.md) — this overview
- [`README-UI.md`](./README-UI.md) — Method 1, the UI procedure (AD delegation)
- [`README-PowerShell.md`](./README-PowerShell.md) — Method 2, the PowerShell procedure (AD delegation)
- [`README-KMS-Secret.md`](./README-KMS-Secret.md) — Steps 4 & 5: KMS key and Secrets Manager entry (UI + AWS CLI)
- [`README-RDS-Db2.md`](./README-RDS-Db2.md) — Step 6: Create or modify the RDS for Db2 instance with self-managed AD
- [`README-Networking.md`](./README-Networking.md) — Network connectivity requirements (same VPC, Azure AD, cross-account VPC)
- [`Grant-ADDomainJoinPrivileges.ps1`](./Grant-ADDomainJoinPrivileges.ps1) — the script Method 2 uses
- [`Show-OUDelegation.ps1`](./Show-OUDelegation.ps1) — verification script (shows ACEs with human-readable names)

## Next steps

After delegation is in place, proceed with the AWS-side configuration:

1. [Create the AWS KMS key for the secret](./README-KMS-Secret.md#step-4--create-the-aws-kms-key)
2. [Store the service account credentials in AWS Secrets Manager](./README-KMS-Secret.md#step-5--create-the-aws-secrets-manager-secret)
3. [Create or modify the RDS for Db2 instance and select self-managed AD](./README-RDS-Db2.md)

Both steps 1 and 2 are documented with UI and AWS CLI methods in
[`README-KMS-Secret.md`](./README-KMS-Secret.md).
