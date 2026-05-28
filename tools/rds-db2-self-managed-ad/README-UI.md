# Method 1 — UI procedure

Use this procedure to delegate the AD permissions Amazon RDS for Db2 needs
to manage user principals inside your dedicated OU. See [`README.md`](./README.md)
for the full permission list and prerequisites. For the scripted alternative
see [`README-PowerShell.md`](./README-PowerShell.md).

> **Replace example values before following this procedure.**
> Wherever you see `RDSDb2`, `DC=company,DC=com`, or `rdsdb2svc`,
> substitute the OU name, domain, and service account logon name you chose.
> See the substitution table in [`README.md`](./README.md).

The UI flow has three parts:

- **Part A** creates the dedicated OU and the AD domain service account using
  Active Directory Users and Computers (ADUC).
- **Part B** uses **ADSI Edit** to delegate all required permissions in a
  single step — create/delete objects, Reset Password,
  `msDS-SupportedEncryptionTypes` read/write, and `servicePrincipalName`
  read/write. ADSI Edit is used instead of the Delegation of Control Wizard
  because the wizard filters `servicePrincipalName` out of the User objects
  attribute list; ADSI Edit exposes the full unfiltered schema.
- **Part C** verifies the final ACL.

---

## Part A — Create the OU and service account (ADUC)

### Create the OU

1. Open **Active Directory Users and Computers** and select the domain.
2. Right-click the domain and choose **New → Organizational Unit**.
3. Enter a name (e.g. `RDSDb2`).
4. Keep **Protect container from accidental deletion** selected.
5. Click **OK**.

### Create the AD domain service account

The service account credentials will be stored in AWS Secrets Manager later.
Create the account **inside the dedicated OU**, not in the default Users container.

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

---

## Part B — Delegate all permissions using ADSI Edit

All nine required permissions are granted in a single ADSI Edit session.

> **Prerequisite:** ADSI Edit (`adsiedit.msc`) is included with RSAT and is
> available on any domain controller or domain-joined Windows host with
> AD DS Tools installed.

> **Why ADSI Edit and not the Delegation of Control Wizard?**
> The Delegation of Control Wizard and the ADUC Security tab both filter
> `servicePrincipalName` out of the attribute list when User objects is
> selected. ADSI Edit bypasses this filter and shows every attribute defined
> in the schema, making it possible to grant all permissions in one place.

1. Open **ADSI Edit**: press `Win+R`, type `adsiedit.msc`, press Enter.
2. Right-click **ADSI Edit** in the left pane → **Connect to**.
3. In Connection Settings, leave defaults (connects to the domain naming
   context) → **OK**.
4. Expand the tree to find your OU: `DC=company,DC=com` → `OU=RDSDb2`.
5. Right-click the OU → **Properties**.
6. Click the **Security** tab → **Advanced** → **Add** → **Select a principal**.
7. Enter the service account logon name (e.g. `rdsdb2svc`) →
   **Check Names** → **OK**.
8. Set **Type** to `Allow`.
9. Set **Applies to** to **Descendant User objects**.
10. Click **Clear all** to deselect everything, then scroll to the
    **Permissions** section and check:
    - **Reset Password**
11. Scroll down to the **Properties** section and check:
    - **Read msDS-SupportedEncryptionTypes**
    - **Write msDS-SupportedEncryptionTypes**
    - **Read servicePrincipalName**
    - **Write servicePrincipalName**
12. Click **OK**.
13. Click **Apply**, then **OK**, then **OK** again to close Properties.

> **Note on Create/Delete object permissions:** The Delegation of Control
> Wizard (not used here) would also add Create/Delete User and Computer
> object permissions. If you need those, run the wizard separately for User
> objects with Create/Delete selected, or use the PowerShell script which
> grants all nine ACEs in one pass. For Kerberos authentication alone, the
> five permissions granted above via ADSI Edit are sufficient.

---

## Part C — Verification

### What you see in ADSI Edit (recommended)

ADSI Edit shows the ACEs more accurately than ADUC for attribute-level
permissions.

1. In ADSI Edit, right-click the OU → **Properties → Security → Advanced**.
2. Find the entries for your service account.
3. You will see **two blank entries** in the Access column — these are the
   attribute-level ACEs (property-specific grants do not display a label in
   the list view).
4. **Double-click each blank entry** to confirm the attributes it covers.
   You should find:
   - One entry covering `msDS-SupportedEncryptionTypes` (Read and Write)
   - One entry covering `servicePrincipalName` (Read and Write)
5. You should also see an entry for **Reset Password**.

### What you see in ADUC

In ADUC (with **View → Advanced Features** on), open the OU's
**Properties → Security → Advanced**. The same blank entries appear here
too — double-click each one to confirm the attribute it covers. ADUC and
ADSI Edit show the same underlying ACEs; ADSI Edit is simply more reliable
for reading attribute-level entries.

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
Allow              ReadProperty, WriteProperty  Descendents      Validated write to service principal name user
Allow              ReadProperty, WriteProperty  Descendents      msDS-SupportedEncryptionTypes             user
Allow              ExtendedRight                Descendents      Reset Password                            user
```

> If you also ran the Delegation of Control Wizard for Create/Delete
> permissions, you will additionally see:
> ```
> Allow              CreateChild, DeleteChild     All              user                                      (all)
> Allow              CreateChild, DeleteChild     All              computer                                  (all)
> ```

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
