[CmdletBinding()]
param([switch]$ValidateOnly, [switch]$Repair)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$taskName = 'TradeTrack MT5 Candidate Staging'
$maintenanceRoot = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'TradeTrack MT5 Maintenance'
$source = Join-Path $PSScriptRoot 'Stage-MT5Update.ps1'
$expectedHash = 'C1149E9B85667853B0A2BAC33548696B02C5504C136EA01D491459154B3E5B33'
$previousHash = '4FD77B990DFCBC6AB1684A76A04FF082C7F5A455139D71B66CE0664217F04542'
if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne $expectedHash) { throw 'Helper hash differs from reviewed package.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($ValidateOnly) {
    [ordered]@{ helperHashVerified=$true; administrator=$isAdmin; schedule='Sunday 10:00 UTC'; purpose='isolated candidate installation only'; root=$maintenanceRoot; task=$taskName } | ConvertTo-Json
    exit 0
}
if (-not $isAdmin) { throw 'One-time setup requires an elevated PowerShell. No password is stored by this installer.' }
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $Repair -and $existing) { throw 'Task already exists; inspect instead of overwriting it.' }
if (-not $Repair -and (Test-Path -LiteralPath $maintenanceRoot)) { throw 'Maintenance directory already exists; inspect instead of reusing it.' }
$parent = Get-Item -LiteralPath (Split-Path -Parent $maintenanceRoot)
if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Unsafe parent directory.' }
$installed = Join-Path $maintenanceRoot 'Stage-MT5Update.ps1'
$powershell = Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
$arguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy RemoteSigned -File "' + $installed + '"'
if ($Repair) {
    if (-not $existing -or $existing.State -in @('Running','Queued')) { throw 'Expected installed, inactive task.' }
    if ($existing.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $existing.Principal.LogonType -ne 'ServiceAccount' -or @($existing.Actions).Count -ne 1 -or $existing.Actions[0].Execute -ne $powershell -or $existing.Actions[0].Arguments -ne $arguments -or $existing.Actions[0].WorkingDirectory -ne $maintenanceRoot) { throw 'Unexpected task definition; refusing repair.' }
    foreach ($path in @($maintenanceRoot,$installed)) {
        if ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Unsafe existing path.' }
        $currentAcl=Get-Acl -LiteralPath $path
        if ($currentAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544')) { throw 'Unexpected existing owner.' }
        $writeMask=[Security.AccessControl.FileSystemRights]::Write -bor [Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership
        foreach($rule in $currentAcl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            if ($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band $writeMask) -ne 0 -and $rule.IdentityReference.Value -notin @('S-1-5-18','S-1-5-32-544')) { throw 'Existing helper is writable by an unexpected principal.' }
        }
    }
    if ((Get-FileHash -LiteralPath $installed).Hash -notin @($previousHash,$expectedHash)) { throw 'Unrecognized installed helper.' }
    Copy-Item -LiteralPath $installed -Destination (Join-Path $maintenanceRoot ('Stage-MT5Update.backup-'+[Guid]::NewGuid().ToString('N')+'.ps1'))
} else {
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
}
Copy-Item -LiteralPath $source -Destination $installed
if ((Get-FileHash -LiteralPath $installed -Algorithm SHA256).Hash -ne $expectedHash) { throw 'Installed helper hash mismatch; task not registered.' }
if (-not $Repair) {
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $maintenanceRoot
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '10:00'
$next = [DateTime]::UtcNow.Date.AddHours(10)
while ($next.DayOfWeek -ne [DayOfWeek]::Sunday -or $next -le [DateTime]::UtcNow) { $next=$next.AddDays(1) }
$trigger.StartBoundary = $next.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$runAs = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $runAs -Description 'Downloads and verifies a MetaQuotes candidate in protected staging; never modifies production terminals or reads their credentials.' | Out-Null
}
# Read-only inspection for the worker. Do not grant execute, write, or change-ACL
# rights. Preserve all existing task access entries, including SYSTEM.
$workerSid=([Security.Principal.NTAccount]::new($env:COMPUTERNAME,'TradeTrackWorker')).Translate([Security.Principal.SecurityIdentifier])
$service=New-Object -ComObject 'Schedule.Service'
$service.Connect()
$registered=$service.GetFolder('\').GetTask($taskName)
$descriptor=[Security.AccessControl.RawSecurityDescriptor]::new($registered.GetSecurityDescriptor(4))
$readMask=0x120089 # FILE_GENERIC_READ
$hasRead=$false
foreach($ace in $descriptor.DiscretionaryAcl) {
    if($ace -is [Security.AccessControl.CommonAce] -and $ace.SecurityIdentifier -eq $workerSid -and ($ace.AccessMask -band $readMask) -eq $readMask -and $ace.AceQualifier -eq 'AccessAllowed'){ $hasRead=$true }
}
if(-not $hasRead) {
    $readAce=[Security.AccessControl.CommonAce]::new([Security.AccessControl.AceFlags]::None,[Security.AccessControl.AceQualifier]::AccessAllowed,$readMask,$workerSid,$false,$null)
    $descriptor.DiscretionaryAcl.InsertAce($descriptor.DiscretionaryAcl.Count,$readAce)
    $registered.SetSecurityDescriptor($descriptor.GetSddlForm([Security.AccessControl.AccessControlSections]::Access),0)
}
Start-ScheduledTask -TaskName $taskName
Write-Output 'Installed/updated. Isolated staging run started. Check status.json. Nonzero installer outcomes remain review-required; no production rollout is performed.'
