[CmdletBinding()]
param([switch]$ValidateOnly)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$taskName = 'TradeTrack MT5 Candidate Staging'
$maintenanceRoot = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'TradeTrack MT5 Maintenance'
$source = Join-Path $PSScriptRoot 'Stage-MT5Update.ps1'
$expectedHash = '4FD77B990DFCBC6AB1684A76A04FF082C7F5A455139D71B66CE0664217F04542'
if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne $expectedHash) { throw 'Helper hash differs from reviewed package.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($ValidateOnly) {
    [ordered]@{ helperHashVerified=$true; administrator=$isAdmin; schedule='Sunday 10:00 UTC'; purpose='isolated candidate installation only'; root=$maintenanceRoot; task=$taskName } | ConvertTo-Json
    exit 0
}
if (-not $isAdmin) { throw 'One-time setup requires an elevated PowerShell. No password is stored by this installer.' }
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw 'Task already exists; inspect instead of overwriting it.' }
if (Test-Path -LiteralPath $maintenanceRoot) { throw 'Maintenance directory already exists; inspect instead of reusing it.' }
$parent = Get-Item -LiteralPath (Split-Path -Parent $maintenanceRoot)
if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Unsafe parent directory.' }
New-Item -ItemType Directory -Path $maintenanceRoot | Out-Null
# Only SYSTEM and Administrators may change executable code, downloads or manifests.
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true,$false)
$admins = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
$system = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
$users = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-545')
$acl.SetOwner($admins)
foreach ($sid in @($admins,$system)) {
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
}
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($users,'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow'))
Set-Acl -LiteralPath $maintenanceRoot -AclObject $acl
$installed = Join-Path $maintenanceRoot 'Stage-MT5Update.ps1'
Copy-Item -LiteralPath $source -Destination $installed
if ((Get-FileHash -LiteralPath $installed -Algorithm SHA256).Hash -ne $expectedHash) { throw 'Installed helper hash mismatch; task not registered.' }
$powershell = Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
$action = New-ScheduledTaskAction -Execute $powershell -Argument ('-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy RemoteSigned -File "' + $installed + '"') -WorkingDirectory $maintenanceRoot
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '10:00'
$next = [DateTime]::UtcNow.Date.AddHours(10)
while ($next.DayOfWeek -ne [DayOfWeek]::Sunday -or $next -le [DateTime]::UtcNow) { $next=$next.AddDays(1) }
$trigger.StartBoundary = $next.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$runAs = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $runAs -Description 'Downloads and verifies a MetaQuotes candidate in protected staging; never modifies production terminals or reads their credentials.' | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output 'Installed. First isolated staging run started. Check status.json in the maintenance directory. Production rollout is a separate compatibility-gated step.'
