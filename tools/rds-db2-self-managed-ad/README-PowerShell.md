# Method 2 — PowerShell procedure

Use [`Grant-ADDomainJoinPrivileges.ps1`](./Grant-ADDomainJoinPrivileges.ps1)
to delegate the AD permissions Amazon RDS for Db2 needs in a single,
repeatable run. For the UI walkthrough see [`README-UI.md`](./README-UI.md).

> **Replace example values before running any command.**

| Example value | What to replace it with |
|---|---|
| `RDSDb2` | The name of the OU you create for RDS for Db2 |
| `DC=company,DC=com` / `DC=corp,DC=com` | The DC components of your AD domain |
| `OU=RDSDb2,DC=company,DC=com` | The full distinguished name of your OU |
| `CORP\rdsdb2svc` | Your AD domain and service account in `DOMAIN\username` format |
| `rdsdb2svc` | The sAMAccountName (logon name) of your service account |

## What the script does

- Creates the target OU if it doesn't exist.
- Creates the AD domain service account if it doesn't exist (prompts for
  password securely; sets **Password never expires** and leaves
  **User must change password at next logon** unchecked — the settings
  required for an RDS service account).
- Grants the seven ACEs listed in [`README.md`](./README.md), including
  `servicePrincipalName` read/write that the wizard doesn't expose.
- Skips ACEs that already match (idempotent — safe to re-run).
- Backs up the OU's current ACL to CSV and SDDL before changes.
- Supports `-WhatIf` and `-Confirm`.
- Prints the final ACEs for the service account so you can verify.

## Prerequisites

- Run on a domain controller, or a domain-joined host with **RSAT: AD DS Tools**.
- Run elevated, as a user with permission to modify the target OU's ACL
  (typically Domain Admin).
- The `ActiveDirectory` PowerShell module must be available; the script
  imports it.

## Preview without changes

```powershell
# Replace CORP\rdsdb2svc with your DOMAIN\serviceaccount
# Replace OU=RDSDb2,DC=corp,DC=com with your OU distinguished name
.\Grant-ADDomainJoinPrivileges.ps1 `
    -ServiceAccount "CORP\rdsdb2svc" `
    -TargetOU       "OU=RDSDb2,DC=corp,DC=com" `
    -WhatIf
```

## Apply

```powershell
# Replace CORP\rdsdb2svc with your DOMAIN\serviceaccount
# Replace OU=RDSDb2,DC=corp,DC=com with your OU distinguished name
.\Grant-ADDomainJoinPrivileges.ps1 `
    -ServiceAccount "CORP\rdsdb2svc" `
    -TargetOU       "OU=RDSDb2,DC=corp,DC=com" `
    -Verbose
```

### ServiceAccount format

The `-ServiceAccount` parameter accepts any of these formats — all are
equivalent, the domain prefix is stripped automatically:

```powershell
-ServiceAccount "COMPANY\rdsdb2svc"      # pre-Windows 2000 / NetBIOS (most common)
-ServiceAccount "rdsdb2svc"              # sAMAccountName only
-ServiceAccount "rdsdb2svc@company.com"  # UPN format
```

The "pre-Windows 2000" label you see in ADUC for the `DOMAIN\username` field
is just the historical name for that format — it is still the standard way
to reference accounts in scripts and command lines today.

## Re-run safely

Each ACE is checked against the existing ACL and skipped if already present.
You can re-run the script to:

- Confirm the SPN ACE is present after a UI-only run.
- Fill in anything missing after a partial UI run.
- Apply the same delegation in another environment.

## Backup files

Before changes are applied, the script writes two timestamped backups in
your `Documents` folder (override with `-BackupPath`):

- `ACL-Backup-<ou>-<timestamp>.csv` — flattened access rules
- `ACL-Backup-<ou>-<timestamp>.sddl.txt` — full SDDL string

Keep these in case you need to compare or restore.

## Verify after run

The script prints the final set of ACEs after applying changes. To check
again at any time, use [`Show-OUDelegation.ps1`](./Show-OUDelegation.ps1)
which resolves GUIDs to human-readable names.

First, confirm the sAMAccountName of the service account:

```powershell
# Replace OU=RDSDb2,DC=company,DC=com with your OU distinguished name
Get-ADUser -Filter * -SearchBase "OU=RDSDb2,DC=company,DC=com" |
    Select-Object Name, SamAccountName
```

Then run:

```powershell
# Replace OU=RDSDb2,DC=company,DC=com with your OU distinguished name
# Replace <your-samaccountname> with the logon name from the command above
.\Show-OUDelegation.ps1 -OU "OU=RDSDb2,DC=company,DC=com" -SamAccountName <your-samaccountname>
```

Expected output:

```
AccessControlType  ActiveDirectoryRights       InheritanceType  AppliesTo (ObjectType)                    InheritedFrom (InheritedObjectType)
-----------------  ---------------------       ---------------  ----------------------                    -----------------------------------
Allow              CreateChild, DeleteChild     All              user                                      (all)
Allow              CreateChild, DeleteChild     All              computer                                  (all)
Allow              ReadProperty, WriteProperty  Descendents      Validated write to service principal name user
Allow              ReadProperty, WriteProperty  Descendents      msDS-SupportedEncryptionTypes             user
Allow              ExtendedRight                Descendents      Reset Password                            user
```

## Common pitfalls

- **Wrong DC order in the DN.** For domain `corp.com` the DN is
  `OU=RDSDb2,DC=corp,DC=com`, not `DC=com,DC=corp`. A reversed DN produces
  `A referral was returned from the server` followed by
  `Cannot find path 'AD:\...' because it does not exist.`
- **AD: drive missing.** The `AD:` PSDrive is registered when the
  `ActiveDirectory` module loads. The script imports the module; running
  on a host without RSAT will fail at that step.
- **Insufficient rights.** Modifying the OU's ACL requires Domain Admin or
  an explicitly delegated administrator. Run elevated.
- **Replication delay.** After applying changes on one DC, give other DCs
  time to replicate before testing the RDS join.
- **Execution policy.** If the script is blocked, run from a session
  started with `powershell -ExecutionPolicy Bypass -File .\Grant-ADDomainJoinPrivileges.ps1 ...`
  or sign the script per your environment's policy.
