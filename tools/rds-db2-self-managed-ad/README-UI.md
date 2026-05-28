# Method 1 — UI procedure

Use this procedure to delegate the AD permissions Amazon RDS for Db2 needs
to manage user principals inside your dedicated OU. See [`README.md`](./README.md)
for the full permission list and prerequisites. For the scripted alternative
see [`README-PowerShell.md`](./README-PowerShell.md).

> **Replace example values before following this procedure.**
> Wherever you see `RDSDb2`, `DC=company,DC=com`, or `<your-samaccountname>`,
> substitute the OU name, domain, and service account logon name you chose.
> See the substitution table in [`README.md`](./README.md).

The UI flow has four parts:

- **Part A** creates the dedicated OU and the AD domain service account.
- **Part B** uses the Delegation of Control Wizard scoped to **User objects**
  to grant create/delete, Reset Password, and `msDS-SupportedEncryptionTypes`
  read/write.
- **Part C** uses **ADSI Edit** to grant `servicePrincipalName` read/write
  on **User objects**. The standard Delegation of Control Wizard filters
  `servicePrincipalName` out of the User objects attribute list; ADSI Edit
  exposes the full unfiltered schema and is the correct tool for this step.

- **Part D** verifies the final ACL.

## Part A — Create the OU and service account

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
3. Enter a first name, last name, and logon name for the user. Example: First Name: `RDS`, Last Name: `db2svc`, User Logon Name: `rdsdb2svc`, Click **Next**.
4. Enter a password. Ensure:
   - **User must change password at next login** is **not** selected.
   - **Account is disabled** is **not** selected.
5. Click **Next**, then **OK**. The new user appears inside your OU.

## Part B — Delegation of Control Wizard

1. Open **Active Directory Users and Computers** and select the domain.
2. Right-click the OU created for RDS for Db2 and choose **Delegate Control**.
3. On the wizard, click **Next**.
4. **Users or Groups** → **Add** → enter the AD domain service account → **Check Names** → **OK** → **Next**.
5. **Tasks to Delegate** → **Create a custom task to delegate** → **Next**.
6. **Active Directory Object Type**:
   - Choose **Only the following objects in the folder**
   - Select **User objects**
   - Select **Create selected objects in this folder**
   - Select **Delete selected objects in this folder** → **Next**
7. **Permissions**:
   - Keep **General** selected
   - Select **Property-specific**
   - Select **Creation/deletion of specific child objects**
   - Select **Reset Password**
   - Select **Read msDS-SupportedEncryptionTypes**
   - Select **Write msDS-SupportedEncryptionTypes**
   - Click **Next**
8. **Finish**.

## Part C — Grant SPN read/write using ADSI Edit

The standard Delegation of Control Wizard and the ADUC Security tab both
filter `servicePrincipalName` out of the attribute list when **User objects**
is selected. The previous workaround of using Computer objects scoped the ACE
incorrectly — RDS for Db2 checks for SPN permissions on **User objects**, not
Computer objects, and the join will fail if the scope is wrong.

**ADSI Edit** exposes the full unfiltered AD schema and is the correct tool
for this step.

> **Prerequisite:** ADSI Edit (`adsiedit.msc`) is included with RSAT and is
> available on any domain controller or domain-joined Windows host with
> AD DS Tools installed.

1. Open **ADSI Edit**: press `Win+R`, type `adsiedit.msc`, press Enter.
2. Right-click **ADSI Edit** in the left pane → **Connect to**.
3. In Connection Settings, leave defaults (connects to the domain naming context) → **OK**.
4. Expand the tree to find your OU: `DC=company,DC=com` → `OU=RDSDb2`.
5. Right-click the OU → **Properties**.
6. Click the **Security** tab → **Advanced** → **Add** → **Select a principal**.
7. Enter the service account name → **Check Names** → **OK**.
8. Set **Type** to `Allow`.
9. Set **Applies to** to **Descendant User objects**.
10. Scroll down to the **Properties** section. Because ADSI Edit shows the
    full unfiltered schema, you will now see `servicePrincipalName` in the list.
11. Check both:
    - **Read servicePrincipalName**
    - **Write servicePrincipalName**
12. Click **OK** through all dialogs.

> **Why ADSI Edit and not ADUC?** The ADUC snap-in applies a display filter
> that hides certain attributes from the Security tab property list,
> including `servicePrincipalName` under User objects. ADSI Edit bypasses
> this filter and shows every attribute defined in the schema. The resulting
> ACE is identical — only the tool used to create it differs.

## Part D — Verification

### What you see in the UI

In ADUC (with Advanced Features on), open the OU's **Security → Advanced**.
You will see several entries for your service account. This is expected:

- The entry showing **Reset password** in the Access column is from Part B.
- The entry showing **Create/delete User objects** is from Part B.
- The entry showing **Create/delete Computer objects** is from Part B (the wizard adds this as a side effect of the Computer objects scope used to surface SPN — it is harmless and expected).
- The remaining entries for your service account will show a **blank Access
  column** — this is normal. The UI collapses property-specific ACEs
  (attribute read/write grants) and does not label them in the list view.

To confirm the attribute ACEs are correct, **double-click each blank entry**
for your service account. Each one will open and show the specific attribute
it covers. You should find entries for:

- `msDS-SupportedEncryptionTypes` (Read and Write — two ACEs or one combined)
- `servicePrincipalName` (Read and Write)

### Verify from PowerShell (recommended)

Use [`Show-OUDelegation.ps1`](./Show-OUDelegation.ps1) to see all ACEs for
your service account with human-readable names instead of raw GUIDs.

First, confirm the sAMAccountName of the service account you created in Part A:

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

Replace `<your-samaccountname>` with the logon name you entered in Part A.

Expected output after completing all four parts:

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

- **Security tab not visible.** Enable **View → Advanced Features** in ADUC.
- **Wrong OU selected.** Right-click the OU created for RDS for Db2,
  not the parent domain. Permissions applied at the wrong level either
  over-privilege the account or miss inheritance to the right scope.
- **Replication delay.** After applying changes on one DC, give other DCs
  time to replicate before testing the RDS join.
- **Don't combine with built-in user-object Full Control.** Keep the
  delegation scoped to the listed permissions to follow least privilege.
