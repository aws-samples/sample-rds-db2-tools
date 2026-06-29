<powershell>
# ===========================================================================
# DC2 - Additional domain controller: join the forest created by DC1.
# Waits for DC1 to be promoted before running Install-ADDSDomainController.
# ===========================================================================
$ErrorActionPreference = "Stop"
Start-Transcript -Path C:\dc-bootstrap.log -Append

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force
Set-PSRepository -Name PSGallery -InstallationPolicy Trusted
Install-Module -Name AWS.Tools.SecretsManager -Force -AllowClobber -SkipPublisherCheck
Import-Module AWS.Tools.SecretsManager

$secret       = (Get-SECSecretValue -SecretId "${secret_id}" -Region "${region}").SecretString | ConvertFrom-Json
$dsrmPassword = ConvertTo-SecureString $secret.dsrm_password -AsPlainText -Force
$domAdminCred = New-Object System.Management.Automation.PSCredential(
  "${domain_netbios}\Administrator",
  (ConvertTo-SecureString $secret.admin_password -AsPlainText -Force)
)

# Point primary DNS at DC1 so the domain is resolvable; keep AmazonProvidedDNS
# (link-local 169.254.169.253) as fallback for AWS endpoint resolution.
$nic = Get-NetAdapter | Where-Object { $_.Status -eq "Up" } | Select-Object -First 1
Set-DnsClientServerAddress -InterfaceIndex $nic.ifIndex -ServerAddresses ("${dc1_ip}", "169.254.169.253")

# Wait for DC1's domain to be reachable (LDAP 389 + DNS resolution), up to ~30 min.
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
  try {
    $tcp = Test-NetConnection -ComputerName "${dc1_ip}" -Port 389 -InformationLevel Quiet
    $dns = Resolve-DnsName -Name "${domain_fqdn}" -Server "${dc1_ip}" -ErrorAction SilentlyContinue
    if ($tcp -and $dns) { $ready = $true; break }
  } catch { }
  Start-Sleep -Seconds 30
}
if (-not $ready) { throw "DC1 ($("${dc1_ip}")) domain ${domain_fqdn} not reachable within timeout." }

# Install AD DS + DNS and promote as an additional DC in the existing domain.
Install-WindowsFeature AD-Domain-Services -IncludeManagementTools
Import-Module ADDSDeployment

Install-ADDSDomainController `
  -DomainName "${domain_fqdn}" `
  -Credential $domAdminCred `
  -SafeModeAdministratorPassword $dsrmPassword `
  -InstallDns `
  -DatabasePath "C:\Windows\NTDS" `
  -SysvolPath "C:\Windows\SYSVOL" `
  -LogPath "C:\Windows\NTDS" `
  -NoRebootOnCompletion:$false `
  -Force

Stop-Transcript
</powershell>
<persist>true</persist>
