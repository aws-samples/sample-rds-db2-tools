<powershell>
# ===========================================================================
# DC1 - First domain controller: create a NEW AD forest, then (post-reboot)
# create the RDS for Db2 OU, service account, and delegated ACLs.
# ===========================================================================
$ErrorActionPreference = "Stop"
Start-Transcript -Path C:\dc-bootstrap.log -Append

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# AWS Tools for PowerShell (Secrets Manager) - outbound via NAT.
Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force
Set-PSRepository -Name PSGallery -InstallationPolicy Trusted
Install-Module -Name AWS.Tools.SecretsManager -Force -AllowClobber -SkipPublisherCheck -Scope AllUsers
Import-Module AWS.Tools.SecretsManager

# Fetch credentials from Secrets Manager (instance role authorizes this).
$secret       = (Get-SECSecretValue -SecretId "${secret_id}" -Region "${region}").SecretString | ConvertFrom-Json
$dsrmPassword = ConvertTo-SecureString $secret.dsrm_password -AsPlainText -Force

# Make the local Administrator password match the secret (becomes domain admin).
$adminUser = [ADSI]"WinNT://./Administrator,user"
$adminUser.SetPassword($secret.admin_password)
$adminUser.SetInfo()

# --- Stage the post-promotion configuration script (OU + svc account + ACLs) ---
$cfgB64 = "${configure_script_b64}"
[IO.File]::WriteAllText("C:\configure-rds-ad.ps1",
  [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($cfgB64)))

# Register a one-time scheduled task that runs after the promotion reboot,
# waits for AD DS to be ready, then configures the OU/account/ACLs. The script
# unregisters this task when it finishes.
$action    = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-ExecutionPolicy Bypass -NonInteractive -File C:\configure-rds-ad.ps1"
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "ConfigureRdsAd" -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force

# --- Install AD DS + DNS and create the forest (reboots automatically) ---
Install-WindowsFeature AD-Domain-Services -IncludeManagementTools
Import-Module ADDSDeployment

Install-ADDSForest `
  -DomainName "${domain_fqdn}" `
  -DomainNetbiosName "${domain_netbios}" `
  -SafeModeAdministratorPassword $dsrmPassword `
  -InstallDns `
  -DomainMode "WinThreshold" `
  -ForestMode "WinThreshold" `
  -DatabasePath "C:\Windows\NTDS" `
  -SysvolPath "C:\Windows\SYSVOL" `
  -LogPath "C:\Windows\NTDS" `
  -NoRebootOnCompletion:$false `
  -Force

Stop-Transcript
</powershell>
<persist>true</persist>
