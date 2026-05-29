<#
.SYNOPSIS
    Provisions an OU and a delegated AD service account for Amazon RDS for Db2
    self-managed Active Directory integration.

.DESCRIPTION
    Creates the target OU and AD service account if they don't exist, then
    delegates the minimum permissions required for RDS for Db2 to manage the
    user-class principals it provisions in that OU.

    Permissions granted (matches the AWS RDS for Db2 self-managed AD
    Delegation of Control Wizard procedure, plus the missing SPN read/write):

      1. Create User objects in the OU
      2. Delete User objects in the OU
      3. Reset Password (extended right) on descendant User objects
      4. Read  msDS-SupportedEncryptionTypes on descendant User objects
      5. Write msDS-SupportedEncryptionTypes on descendant User objects
      6. Read  servicePrincipalName              on descendant User objects
      7. Write servicePrincipalName              on descendant User objects

    The script is idempotent (skips ACEs that already exist), supports
    -WhatIf / -Confirm, validates inputs, and writes a timestamped backup of
    the current ACL (CSV + SDDL) before applying changes.

.PARAMETER ServiceAccount
    The service account to delegate to. Accepts any of these formats:
      DOMAIN\username   (e.g. COMPANY\rdsdb2svc)  — pre-Windows 2000 / NetBIOS format
      username          (e.g. rdsdb2svc)           — sAMAccountName only
      username@domain   (e.g. rdsdb2svc@company.com) — UPN format
    The domain prefix is stripped automatically; only the sAMAccountName is used.
    The account is created inside the target OU if it does not already exist.

.PARAMETER TargetOU
    Distinguished name of the OU to delegate on
    (e.g. OU=RDSDb2,DC=corp,DC=com). Created if missing.

.PARAMETER BackupPath
    Directory for ACL backup files. Defaults to the current user's Documents.

.EXAMPLE
    # Preview without changing anything
    .\Grant-ADDomainJoinPrivileges.ps1 `
        -ServiceAccount "CORP\rdsdb2svc" `
        -TargetOU       "OU=RDSDb2,DC=corp,DC=com" -WhatIf

.EXAMPLE
    # Apply with verbose output
    .\Grant-ADDomainJoinPrivileges.ps1 `
        -ServiceAccount "CORP\rdsdb2svc" `
        -TargetOU       "OU=RDSDb2,DC=corp,DC=com" -Verbose

.NOTES
    Run on a domain controller or a domain-joined host with RSAT: AD DS Tools
    installed. Requires permission to create OUs, create users, and modify the
    ACL on the target OU (typically Domain Admin or an explicitly delegated
    administrator).
#>

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ServiceAccount,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$TargetOU,

    [Parameter()]
    [string]$BackupPath = (Join-Path $env:USERPROFILE 'Documents')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---- Well-known constants ---------------------------------------------------
# schemaIDGUID for the 'user' object class
$UserClassGuid = [Guid]'bf967aba-0de6-11d0-a285-00aa003049e2'
# schemaIDGUID for the 'computer' object class
$ComputerClassGuid = [Guid]'bf967a86-0de6-11d0-a285-00aa003049e2'
# rightsGuid for the 'User-Force-Change-Password' extended right (Reset Password)
$ResetPasswordRightGuid = [Guid]'00299570-246d-11d0-a768-00aa006e0529'

function Test-Elevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-SchemaAttributeGuid {
    param(
        [Parameter(Mandatory)] [string]$LdapDisplayName,
        [Parameter(Mandatory)] [string]$SchemaPath
    )
    $obj = Get-ADObject -SearchBase $SchemaPath `
                        -Filter   "lDAPDisplayName -eq '$LdapDisplayName'" `
                        -Properties schemaIDGUID -ErrorAction Stop
    if (-not $obj) {
        throw "Schema attribute '$LdapDisplayName' not found under $SchemaPath."
    }
    return [Guid]$obj.schemaIDGUID
}

function New-AdAce {
    param(
        [Parameter(Mandatory)] [Security.Principal.SecurityIdentifier]$Identity,
        [Parameter(Mandatory)] [System.DirectoryServices.ActiveDirectoryRights]$Rights,
        [System.Security.AccessControl.AccessControlType]$Type = 'Allow',
        [Guid]$ObjectType                                       = [Guid]::Empty,
        [System.DirectoryServices.ActiveDirectorySecurityInheritance]$Inheritance = 'None',
        [Guid]$InheritedObjectType                              = [Guid]::Empty
    )
    if ($ObjectType -ne [Guid]::Empty -and $InheritedObjectType -ne [Guid]::Empty) {
        return New-Object System.DirectoryServices.ActiveDirectoryAccessRule(
            $Identity, $Rights, $Type, $ObjectType, $Inheritance, $InheritedObjectType)
    } elseif ($ObjectType -ne [Guid]::Empty) {
        return New-Object System.DirectoryServices.ActiveDirectoryAccessRule(
            $Identity, $Rights, $Type, $ObjectType, $Inheritance)
    } else {
        return New-Object System.DirectoryServices.ActiveDirectoryAccessRule(
            $Identity, $Rights, $Type, $Inheritance)
    }
}

try {
    # ---- Pre-flight ---------------------------------------------------------
    if (-not (Test-Elevated)) {
        Write-Warning 'Not running elevated. Modifying AD ACLs typically requires elevation.'
    }
    Write-Verbose 'Importing ActiveDirectory module...'
    Import-Module ActiveDirectory -ErrorAction Stop

    $domainDns = (Get-ADDomain).DNSRoot
    Write-Verbose "Connected to domain: $domainDns"

    # ---- Ensure OU exists ---------------------------------------------------
    Write-Verbose "Validating OU: $TargetOU"
    try {
        Get-ADOrganizationalUnit -Identity $TargetOU -ErrorAction Stop | Out-Null
    } catch {
        $ouName     = ($TargetOU -split ',')[0]   -replace '^OU=', ''
        $parentPath = ($TargetOU -split ',', 2)[1]
        if ($PSCmdlet.ShouldProcess($TargetOU, 'Create OU')) {
            New-ADOrganizationalUnit -Name $ouName -Path $parentPath
            Write-Host "Created OU: $TargetOU" -ForegroundColor Yellow
        } else {
            Write-Warning "WhatIf: skipping OU creation. Subsequent steps will fail because the OU does not exist."
            return
        }
    }
    $adPath = "AD:\$TargetOU"
    if (-not (Test-Path -LiteralPath $adPath)) {
        throw "OU path not resolvable: $adPath. Verify the DN (spelling and DC order, e.g. DC=corp,DC=com)."
    }

    # ---- Ensure service account exists --------------------------------------
    # Strip DOMAIN\ prefix (pre-Win2000 format) or @domain suffix (UPN format)
    $samAccountName = $ServiceAccount -replace '.*\\', '' -replace '@.*', ''
    Write-Verbose "Resolving service account: $samAccountName"
    try {
        $account = Get-ADUser -Identity $samAccountName -ErrorAction Stop
    } catch {
        if (-not $PSCmdlet.ShouldProcess($ServiceAccount, 'Create AD user')) {
            Write-Warning "WhatIf: skipping user creation. ACL steps will be skipped."
            return
        }
        $password = Read-Host "Enter password for new account '$samAccountName'" -AsSecureString
        $account = New-ADUser -Name                 $samAccountName `
                              -SamAccountName       $samAccountName `
                              -UserPrincipalName    "$samAccountName@$domainDns" `
                              -AccountPassword      $password `
                              -Enabled              $true `
                              -PasswordNeverExpires $true `
                              -Path                 $TargetOU `
                              -PassThru
        Write-Host "Created service account: $ServiceAccount" -ForegroundColor Yellow
    }
    $sid       = $account.SID
    $ntAccount = $sid.Translate([Security.Principal.NTAccount])

    # ---- Resolve schema attribute GUIDs ------------------------------------
    Write-Verbose 'Resolving schema attribute GUIDs...'
    $schemaPath = (Get-ADRootDSE).schemaNamingContext
    $spnGuid    = Get-SchemaAttributeGuid -LdapDisplayName 'servicePrincipalName'         -SchemaPath $schemaPath
    $encGuid    = Get-SchemaAttributeGuid -LdapDisplayName 'msDS-SupportedEncryptionTypes' -SchemaPath $schemaPath

    # ---- Read & back up current ACL -----------------------------------------
    Write-Verbose 'Reading current ACL...'
    $acl = Get-Acl -Path $adPath

    if (-not (Test-Path -LiteralPath $BackupPath)) {
        New-Item -ItemType Directory -Path $BackupPath -Force | Out-Null
    }
    $stamp      = Get-Date -Format 'yyyyMMdd-HHmmss'
    $safeOu     = ($TargetOU -replace '[^A-Za-z0-9]', '_')
    $csvBackup  = Join-Path $BackupPath "ACL-Backup-$safeOu-$stamp.csv"
    $sddlBackup = Join-Path $BackupPath "ACL-Backup-$safeOu-$stamp.sddl.txt"
    $acl.Access | Export-Csv -Path $csvBackup -NoTypeInformation
    $acl.Sddl   | Out-File   -FilePath $sddlBackup -Encoding utf8
    Write-Host "ACL backup written:" -ForegroundColor DarkGray
    Write-Host "  $csvBackup"        -ForegroundColor DarkGray
    Write-Host "  $sddlBackup"       -ForegroundColor DarkGray

    # ---- Build target ACEs --------------------------------------------------
    $rwBoth = [System.DirectoryServices.ActiveDirectoryRights]'ReadProperty, WriteProperty'

    $aces = @(
        @{
            Description = '1-2. Create/Delete User objects in OU'
            Ace = New-AdAce -Identity    $sid `
                            -Rights      ([System.DirectoryServices.ActiveDirectoryRights]'CreateChild, DeleteChild') `
                            -ObjectType  $UserClassGuid `
                            -Inheritance 'All'
        },
        @{
            Description = '3. Create/Delete Computer objects in OU (required to surface SPN in delegation wizard)'
            Ace = New-AdAce -Identity    $sid `
                            -Rights      ([System.DirectoryServices.ActiveDirectoryRights]'CreateChild, DeleteChild') `
                            -ObjectType  $ComputerClassGuid `
                            -Inheritance 'All'
        },
        @{
            Description = '4. Reset Password on descendant User objects'
            Ace = New-AdAce -Identity            $sid `
                            -Rights              ([System.DirectoryServices.ActiveDirectoryRights]'ExtendedRight') `
                            -ObjectType          $ResetPasswordRightGuid `
                            -Inheritance         'Descendents' `
                            -InheritedObjectType $UserClassGuid
        },
        @{
            Description = '5-6. Read/Write msDS-SupportedEncryptionTypes on descendant User objects'
            Ace = New-AdAce -Identity            $sid `
                            -Rights              $rwBoth `
                            -ObjectType          $encGuid `
                            -Inheritance         'Descendents' `
                            -InheritedObjectType $UserClassGuid
        },
        @{
            Description = '7-8. Read/Write servicePrincipalName on descendant User objects'
            Ace = New-AdAce -Identity            $sid `
                            -Rights              $rwBoth `
                            -ObjectType          $spnGuid `
                            -Inheritance         'Descendents' `
                            -InheritedObjectType $UserClassGuid
        }
    )

    # ---- Idempotency & apply ------------------------------------------------
    $applied = 0
    foreach ($entry in $aces) {
        $candidate = $entry.Ace
        $existing  = $acl.Access | Where-Object {
            $_.IdentityReference     -eq $ntAccount                       -and
            $_.AccessControlType     -eq $candidate.AccessControlType     -and
            $_.ActiveDirectoryRights -eq $candidate.ActiveDirectoryRights -and
            $_.ObjectType            -eq $candidate.ObjectType            -and
            $_.InheritanceType       -eq $candidate.InheritanceType       -and
            $_.InheritedObjectType   -eq $candidate.InheritedObjectType
        }
        if ($existing) {
            Write-Host "  skip   $($entry.Description) (already present)" -ForegroundColor DarkGray
            continue
        }
        if ($PSCmdlet.ShouldProcess($TargetOU, "Add ACE: $($entry.Description) for $ntAccount")) {
            $acl.AddAccessRule($candidate)
            $applied++
            Write-Host "  add    $($entry.Description)" -ForegroundColor Green
        }
    }

    if ($applied -eq 0) {
        Write-Host 'All required ACEs already present. Nothing to apply.' -ForegroundColor Yellow
        return
    }

    if ($PSCmdlet.ShouldProcess($TargetOU, "Apply $applied new ACE(s) for $ntAccount")) {
        Set-Acl -Path $adPath -AclObject $acl
        Write-Host "Applied $applied ACE(s) for $ServiceAccount on $TargetOU." -ForegroundColor Green
    }

    # ---- Verify -------------------------------------------------------------
    Write-Verbose 'Verifying...'
    $finalAcl = Get-Acl -Path $adPath
    $found    = $finalAcl.Access | Where-Object { $_.IdentityReference -eq $ntAccount }

    # Build the same GUID → name lookup used by Show-OUDelegation.ps1
    Write-Verbose 'Building GUID lookup table for human-readable output...'
    $rootDSE    = Get-ADRootDSE
    $schemaBase = $rootDSE.schemaNamingContext
    $configBase = $rootDSE.configurationNamingContext
    $extRights  = "CN=Extended-Rights,$configBase"

    $guidMap = @{}
    $guidMap['00000000-0000-0000-0000-000000000000'] = '(all)'

    Get-ADObject -SearchBase $schemaBase -LDAPFilter '(schemaIDGUID=*)' `
                 -Properties lDAPDisplayName, schemaIDGUID |
        ForEach-Object {
            $guidMap[([Guid]$_.schemaIDGUID).ToString()] = $_.lDAPDisplayName
        }

    Get-ADObject -SearchBase $extRights -LDAPFilter '(rightsGuid=*)' `
                 -Properties displayName, rightsGuid |
        ForEach-Object {
            $guidMap[$_.rightsGuid.ToString()] = $_.displayName
        }

    Write-Host ''
    Write-Host "ACEs on $TargetOU for $ntAccount" -ForegroundColor Cyan
    $found |
        Select-Object `
            AccessControlType,
            ActiveDirectoryRights,
            InheritanceType,
            @{ Name = 'AppliesTo (ObjectType)';
               Expression = {
                   $g = $_.ObjectType.ToString()
                   if ($guidMap.ContainsKey($g)) { $guidMap[$g] } else { $g }
               }},
            @{ Name = 'InheritedFrom (InheritedObjectType)';
               Expression = {
                   $g = $_.InheritedObjectType.ToString()
                   if ($guidMap.ContainsKey($g)) { $guidMap[$g] } else { $g }
               }} |
        Format-Table -AutoSize
}
catch {
    Write-Error "Failed: $($_.Exception.Message)"
    throw
}
