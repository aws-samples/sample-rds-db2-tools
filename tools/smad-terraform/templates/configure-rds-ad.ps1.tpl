# ===========================================================================
# configure-rds-ad.ps1  (rendered by Terraform; runs once on DC1 post-promotion)
# Creates the OU + delegated service account and applies the 7 ACEs that
# Amazon RDS for Db2 self-managed AD requires. Non-interactive adaptation of
# Grant-ADDomainJoinPrivileges.ps1 (reads the password from Secrets Manager).
# ===========================================================================
$ErrorActionPreference = "Stop"
Start-Transcript -Path C:\ad-rds-configure.log -Append

$Region   = "${region}"
$SecretId = "${secret_id}"

# --- Wait for AD DS / AD Web Services to be ready (up to ~20 min) ---
$adReady = $false
for ($i = 0; $i -lt 40; $i++) {
  try {
    Import-Module ActiveDirectory -ErrorAction Stop
    $svc = Get-Service ADWS -ErrorAction Stop
    if ($svc.Status -eq "Running") {
      Get-ADDomain -ErrorAction Stop | Out-Null
      $adReady = $true
      break
    }
  } catch { }
  Start-Sleep -Seconds 30
}
if (-not $adReady) { throw "AD DS / ADWS not ready within timeout." }

# --- Fetch service-account details from Secrets Manager ---
if (-not (Get-Module -ListAvailable -Name AWS.Tools.SecretsManager)) {
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force
  Set-PSRepository -Name PSGallery -InstallationPolicy Trusted
  Install-Module -Name AWS.Tools.SecretsManager -Force -AllowClobber -SkipPublisherCheck -Scope AllUsers
}
Import-Module AWS.Tools.SecretsManager

$secret  = (Get-SECSecretValue -SecretId $SecretId -Region $Region).SecretString | ConvertFrom-Json
$samName = $secret.svc_account_name
$ouDn    = $secret.ou_dn
$ouName  = $secret.ou_name
$svcPass = ConvertTo-SecureString $secret.svc_password -AsPlainText -Force

$domainDns = (Get-ADDomain).DNSRoot
Write-Host "Domain: $domainDns  OU: $ouDn  Account: $samName"

# --- Well-known GUIDs ---
$UserClassGuid          = [Guid]"bf967aba-0de6-11d0-a285-00aa003049e2"
$ResetPasswordRightGuid = [Guid]"00299570-246d-11d0-a768-00aa006e0529"

function Get-SchemaAttributeGuid {
  param([string]$LdapDisplayName, [string]$SchemaPath)
  $obj = Get-ADObject -SearchBase $SchemaPath -Filter "lDAPDisplayName -eq '$LdapDisplayName'" -Properties schemaIDGUID -ErrorAction Stop
  if (-not $obj) { throw "Schema attribute '$LdapDisplayName' not found." }
  return [Guid]$obj.schemaIDGUID
}

function New-AdAce {
  param(
    [Security.Principal.SecurityIdentifier]$Identity,
    [System.DirectoryServices.ActiveDirectoryRights]$Rights,
    [System.Security.AccessControl.AccessControlType]$Type = "Allow",
    [Guid]$ObjectType = [Guid]::Empty,
    [System.DirectoryServices.ActiveDirectorySecurityInheritance]$Inheritance = "None",
    [Guid]$InheritedObjectType = [Guid]::Empty
  )
  if ($ObjectType -ne [Guid]::Empty -and $InheritedObjectType -ne [Guid]::Empty) {
    return New-Object System.DirectoryServices.ActiveDirectoryAccessRule($Identity, $Rights, $Type, $ObjectType, $Inheritance, $InheritedObjectType)
  } elseif ($ObjectType -ne [Guid]::Empty) {
    return New-Object System.DirectoryServices.ActiveDirectoryAccessRule($Identity, $Rights, $Type, $ObjectType, $Inheritance)
  } else {
    return New-Object System.DirectoryServices.ActiveDirectoryAccessRule($Identity, $Rights, $Type, $Inheritance)
  }
}

# --- Ensure OU exists ---
try {
  Get-ADOrganizationalUnit -Identity $ouDn -ErrorAction Stop | Out-Null
  Write-Host "OU already exists: $ouDn"
} catch {
  $parentPath = ($ouDn -split ",", 2)[1]
  New-ADOrganizationalUnit -Name $ouName -Path $parentPath -ProtectedFromAccidentalDeletion $false -ErrorAction Stop
  Write-Host "Created OU: $ouDn"
}

# --- Ensure service account exists ---
try {
  $account = Get-ADUser -Identity $samName -ErrorAction Stop
  Write-Host "Service account already exists: $samName"
} catch {
  $account = New-ADUser -Name $samName -SamAccountName $samName `
    -UserPrincipalName "$samName@$domainDns" -AccountPassword $svcPass `
    -Enabled $true -PasswordNeverExpires $true -Path $ouDn -PassThru
  Write-Host "Created service account: $samName"
}
$sid       = $account.SID
$ntAccount = $sid.Translate([Security.Principal.NTAccount])

# --- Resolve schema attribute GUIDs ---
$schemaPath = (Get-ADRootDSE).schemaNamingContext
$spnGuid = Get-SchemaAttributeGuid -LdapDisplayName "servicePrincipalName"          -SchemaPath $schemaPath
$encGuid = Get-SchemaAttributeGuid -LdapDisplayName "msDS-SupportedEncryptionTypes" -SchemaPath $schemaPath

# --- Read ACL + backup ---
$adPath = "AD:\$ouDn"
$acl = Get-Acl -Path $adPath
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$acl.Sddl | Out-File -FilePath ("C:\ou-acl-backup-" + $stamp + ".sddl.txt") -Encoding utf8

$rwBoth = [System.DirectoryServices.ActiveDirectoryRights]"ReadProperty, WriteProperty"
$aces = @(
  @{ Description = "1-2. Create/Delete User objects in OU";
     Ace = New-AdAce -Identity $sid -Rights ([System.DirectoryServices.ActiveDirectoryRights]"CreateChild, DeleteChild") -ObjectType $UserClassGuid -Inheritance "All" },
  @{ Description = "3. Reset Password on descendant User objects";
     Ace = New-AdAce -Identity $sid -Rights ([System.DirectoryServices.ActiveDirectoryRights]"ExtendedRight") -ObjectType $ResetPasswordRightGuid -Inheritance "Descendents" -InheritedObjectType $UserClassGuid },
  @{ Description = "4-5. Read/Write msDS-SupportedEncryptionTypes on descendant User objects";
     Ace = New-AdAce -Identity $sid -Rights $rwBoth -ObjectType $encGuid -Inheritance "Descendents" -InheritedObjectType $UserClassGuid },
  @{ Description = "6-7. Read/Write servicePrincipalName on descendant User objects";
     Ace = New-AdAce -Identity $sid -Rights $rwBoth -ObjectType $spnGuid -Inheritance "Descendents" -InheritedObjectType $UserClassGuid }
)

$applied = 0
foreach ($entry in $aces) {
  $c = $entry.Ace
  $existing = $acl.Access | Where-Object {
    $_.IdentityReference     -eq $ntAccount            -and
    $_.ActiveDirectoryRights -eq $c.ActiveDirectoryRights -and
    $_.ObjectType            -eq $c.ObjectType          -and
    $_.InheritanceType       -eq $c.InheritanceType     -and
    $_.InheritedObjectType   -eq $c.InheritedObjectType
  }
  if ($existing) { Write-Host "skip  $($entry.Description)"; continue }
  $acl.AddAccessRule($c)
  $applied++
  Write-Host "add   $($entry.Description)"
}
if ($applied -gt 0) {
  Set-Acl -Path $adPath -AclObject $acl
  Write-Host "Applied $applied ACE(s) for $samName on $ouDn."
} else {
  Write-Host "All required ACEs already present."
}

# --- Marker + remove the bootstrap scheduled task so this runs only once ---
"configured $(Get-Date -Format o)" | Out-File -FilePath C:\ad-rds-configure.done -Encoding utf8
try { Unregister-ScheduledTask -TaskName "ConfigureRdsAd" -Confirm:$false -ErrorAction SilentlyContinue } catch { }

Write-Host "RDS for Db2 AD configuration complete."
Stop-Transcript
