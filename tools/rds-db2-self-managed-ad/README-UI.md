# Method 1 — UI procedure

Use this procedure to delegate the minimum Active Directory permissions
Amazon RDS for Db2 needs to manage its principals inside a dedicated OU.
See [`README.md`](./README.md) for the full permission list and
prerequisites. For the scripted alternative see
[`README-PowerShell.md`](./README-PowerShell.md).

> **Replace example values before following this procedure.**
> Wherever you see `RDSDb2`, `DC=company,DC=com`, or `rdsdb2svc`,
> substitute the OU name, domain, and service account logon name you chose.
> See the substitution table in [`README.md`](./README.md).

The UI flow has three parts:

- **Part A** — ADUC: create the OU, create the service account, and
  delegate Create/Delete object and Reset Password permissions using the
  Delegation of Control Wizard.
- **Part B** — ADSI Edit: grant the four property-specific attribute
  permissions (`msDS-SupportedEncryptionTypes` and `servicePrincipalName`
  read/write) that the Delegation of Control Wizard cannot surface for
  User objects.
- **Part C** — Verify the final ACL.

---

## Part A — ADUC: OU, service account, and Delegation of Control Wizard

### Step 1 — Create the OU

1. Open **Active Directory Users and Computers (ADUC)** and select the domain.
2. Right-click the domain and choose **New → Organizational Unit**.
3. Enter a name (e.g. `RDSDb2`).
4. Keep **Protect container from accidental deletion** selected.
5. Click **OK**.

### Step 2 — Create the AD domain service account

The service account credentials will be stored in AWS Secrets Manager later.
Create the account **inside the dedicated OU**, not in the default Users
container.

1. In ADUC, expand the domain and select the OU you just created.
2. Right-click inside the OU and choose **New → User**.
3. Enter a first name, last name, and logon name for the user.
   Example: First Name `RDS`, Last Name `db2svc`, User Logon Name `rdsdb2svc`.
   Click **Next**.
4. Enter a password. Ensure:
   - **User must change password at next login** is **not** selected.
   - **Password never expires** is selected.
     > If your organization's security policy requires password expiration,
     > you must update the password in AWS Secrets Manager whenever it
     > changes in AD — either manually or via automation.
   - **Account is disabled** is **not** selected.
5. Click **Next**, then **Finish**. The new user appears inside your OU.

### Step 3 — Delegate Control Wizard (Create/Delete objects + Reset Password)

1. Right-click the OU → **Delegate Control**.
2. On the wizard, click **Next**.
3. **Users or Groups** → **Add** → enter the service account
   (e.g. `rdsdb2svc`) → **Check Names** → **OK** → **Next**.
4. **Tasks to Delegate** → **Create a custom task to delegate** → **Next**.
5. **Active Directory Object Type**:
   - Choose **Only the following objects in the folder**
   - Select **User objects**
   - Select **Create selected objects in this folder**
   - Select **Delete selected objects in this folder**
   - Click **Next**
6. **Permissions**:
   - Keep **General** selected
   - Select **Property-specific**
   - Select **Creation/deletion of specific child objects**
   - Select **Reset Password**
   - Click **Next**
7. Click **Finish**.

---

## Part B — ADSI Edit: property-specific attribute permissions

The Delegation of Control Wizard filters `servicePrincipalName` and
`msDS-SupportedEncryptionTypes` out of the attribute list for User objects.
**ADSI Edit** exposes the full unfiltered schema and is the correct tool
for granting these four permissions.

> **Prerequisite:** ADSI Edit (`adsiedit.msc`) is included with RSAT and is
> available on any domain controller or domain-joined Windows host with
> AD DS Tools installed.

1. Open **ADSI Edit**: press `Win+R`, type `adsiedit.msc`, press Enter.
2. Right-click **ADSI Edit** in the left pane → **Connect to**.
3. Leave Connection Settings at defaults (connects to the domain naming
   context) → **OK**.
4. Expand the tree to find your OU: `DC=company,DC=com` → `OU=RDSDb2`.
5. Right-click the OU → **Properties**.
6. Click the **Security** tab → **Advanced** → **Add** →
   **Select a principal**.
7. Enter the service account logon name (e.g. `rdsdb2svc`) →
   **Check Names** → **OK**.
8. Set **Type** to `Allow`.
9. Set **Applies to** to **Descendant User objects**.
10. Click **Clear all** to deselect everything.
11. Scroll down to the **Properties** section and check all four:
    - **Read msDS-SupportedEncryptionTypes**
    - **Write msDS-SupportedEncryptionTypes**
    - **Read servicePrincipalName**
    - **Write servicePrincipalName**
12. Click **OK**.
13. Click **Apply**, then **OK**, then **OK** again to close Properties.

---

## Part C — Verification

### What you see in ADUC

In ADUC, enable **View → Advanced Features**, then open the OU's
**Properties → Security → Advanced**.

You will see entries for your service account:

- **Reset password** — from the Delegation of Control Wizard (Part A)
- **Create/delete User objects** — from the Delegation of Control Wizard (Part A)
- **Two blank entries** — these are the attribute-level ACEs from ADSI Edit
  (Part B). Property-specific grants do not show a label in the list view.

**Double-click each blank entry** to confirm the attribute it covers.
You should find:
- One entry: `msDS-SupportedEncryptionTypes` (Read and Write)
- One entry: `servicePrincipalName` (Read and Write)

### Verify from PowerShell (recommended)

Use [`Show-OUDelegation.ps1`](./Show-OUDelegation.ps1) to see all ACEs with
human-readable names instead of raw GUIDs.

First, confirm the sAMAccountName of the service account:

```powershell
# Replace OU=RDSDb2,DC=company,DC=com with your OU distinguished name
Get-ADUser -Filter * -SearchBase "OU=RDSDb2,DC=company,DC=com" |
    Select-Object Name, SamAccountName
```

Then run the delegation check:

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
Allow              ReadProperty, WriteProperty  Descendents      Validated write to service principal name user
Allow              ReadProperty, WriteProperty  Descendents      msDS-SupportedEncryptionTypes             user
Allow              ExtendedRight                Descendents      Reset Password                            user
```

---

## Common pitfalls

- **Security tab not visible in ADUC.** Enable **View → Advanced Features**.
- **Wrong OU selected.** Right-click the OU created for RDS for Db2, not
  the parent domain. Permissions applied at the wrong level either
  over-privilege the account or miss the correct inheritance scope.
- **Replication delay.** After applying changes on one DC, allow time for
  other DCs to replicate before testing the RDS domain join.
- **Don't grant Full Control.** Keep the delegation scoped to the listed
  permissions to follow least privilege.
