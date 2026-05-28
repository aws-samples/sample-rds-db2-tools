<#
.SYNOPSIS
    Shows the ACEs on an OU for a given user with human-readable names
    instead of raw GUIDs.

.EXAMPLE
    .\Show-OUDelegation.ps1 -OU "OU=RDSDb2,DC=company,DC=com" -SamAccountName vizkhatri
#>

param(
    [Parameter(Mandatory)] [string]$OU,
    [Parameter(Mandatory)] [string]$SamAccountName
)

Import-Module ActiveDirectory

# ---- Build GUID → name lookup from schema + extended rights -----------------
Write-Host "Building GUID lookup table from schema..." -ForegroundColor DarkGray

$rootDSE    = Get-ADRootDSE
$schemaPath = $rootDSE.schemaNamingContext
$configPath = $rootDSE.configurationNamingContext
$extRights  = "CN=Extended-Rights,$configPath"

$guidMap = @{}

# All-zeros GUID means "any object / no specific attribute"
$guidMap["00000000-0000-0000-0000-000000000000"] = "(all)"

# Schema attributes and classes
Get-ADObject -SearchBase $schemaPath -LDAPFilter "(schemaIDGUID=*)" `
             -Properties lDAPDisplayName, schemaIDGUID |
    ForEach-Object {
        $guid = [Guid]$_.schemaIDGUID
        $guidMap[$guid.ToString()] = $_.lDAPDisplayName
    }

# Extended rights (rightsGuid property)
Get-ADObject -SearchBase $extRights -LDAPFilter "(rightsGuid=*)" `
             -Properties displayName, rightsGuid |
    ForEach-Object {
        $guidMap[$_.rightsGuid.ToString()] = $_.displayName
    }

Write-Host "Lookup table built ($($guidMap.Count) entries).`n" -ForegroundColor DarkGray

# ---- Resolve the user -------------------------------------------------------
$user = Get-ADUser -Identity $SamAccountName -ErrorAction Stop
$svc  = $user.SID.Translate([Security.Principal.NTAccount])

# ---- Pull and decode ACEs ---------------------------------------------------
$acl = Get-Acl -Path "AD:\$OU"
$acl.Access |
    Where-Object { $_.IdentityReference -eq $svc } |
    Select-Object `
        AccessControlType,
        ActiveDirectoryRights,
        InheritanceType,
        @{ Name = "AppliesTo (ObjectType)";
           Expression = {
               $g = $_.ObjectType.ToString()
               if ($guidMap.ContainsKey($g)) { $guidMap[$g] } else { $g }
           }},
        @{ Name = "InheritedFrom (InheritedObjectType)";
           Expression = {
               $g = $_.InheritedObjectType.ToString()
               if ($guidMap.ContainsKey($g)) { $guidMap[$g] } else { $g }
           }} |
    Format-Table -AutoSize
