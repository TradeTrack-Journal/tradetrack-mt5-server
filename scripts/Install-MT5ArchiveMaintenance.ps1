param([Parameter(Mandatory=$true)][string]$ReleaseDirectory)
$ErrorActionPreference = 'Stop'
$release = (Resolve-Path -LiteralPath $ReleaseDirectory).Path.TrimEnd('\')
if ($release -notmatch '^C:\\TradeTrack\\releases\\mt5-[a-f0-9]{7,40}$' -or
    -not (Test-Path -LiteralPath (Join-Path $release 'scripts\maintain_update_archives.py'))) {
    throw 'An immutable, tested MT5 release is required.'
}
$name = 'TradeTrack MT5 Archive Maintenance'
$python = 'C:\TradeTrack\projects-restored\tradetrack-mt5-server\.venv\Scripts\pythonw.exe'
$arguments = '-m scripts.maintain_update_archives --store C:\TradeTrack\worker\update-archives --max-seconds 240'
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing) {
    if (@($existing.Actions).Count -ne 1 -or $existing.Actions[0].Execute -ne $python -or
        $existing.Actions[0].Arguments -ne $arguments -or $existing.Principal.RunLevel -ne 'Limited' -or
        ($existing.Principal.UserId.Split('\')[-1] -ne $user.Split('\')[-1])) {
        throw 'Existing task does not match the expected standard-user housekeeping task.'
    }
    if ($existing.State -eq 'Running') { throw 'Wait for the current archive pass to finish.' }
}
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $release
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$trigger = New-ScheduledTaskTrigger -Once -At ([datetime]::Now.AddMinutes(15)) -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 8) -StartWhenAvailable
if ($existing) {
    Set-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings | Out-Null
} else {
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description 'Lossless SHA-256 deduplication of closed MT5 update archives; preserve all unique content and original paths.' | Out-Null
}
Write-Output 'Installed standard-user archive housekeeping every 15 minutes. No password or administrator elevation is used.'
