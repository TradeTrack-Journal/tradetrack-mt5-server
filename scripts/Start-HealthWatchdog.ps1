param([string]$ConfigDirectory = 'C:\TradeTrack\worker\production', [string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
$watchLock = [Threading.Mutex]::new($false, 'Local\TradeTrackHealthWatchdog')
if (-not $watchLock.WaitOne(0)) { exit 0 }
try {
    $secureToken = (Get-Content -LiteralPath (Join-Path $ConfigDirectory 'node-token.dpapi') -Raw).Trim() | ConvertTo-SecureString
    $tokenPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    try { $env:MT5_AGENT_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPtr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPtr); $secureToken.Dispose() }
    $repo = Split-Path -Parent $PSScriptRoot
    if (-not $PythonPath) { $PythonPath = Join-Path $repo '.venv\Scripts\python.exe' }
    Push-Location $repo
    try {
        while ($true) {
            $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            $childArguments = '-m scripts.watch_mt5_health --config "' + (Join-Path $ConfigDirectory 'agent.json') + '" --log "' + (Join-Path $ConfigDirectory 'health-watchdog.jsonl') + '"'
            $child = Start-Process -FilePath $PythonPath -ArgumentList $childArguments -WorkingDirectory $repo -WindowStyle Hidden -PassThru -RedirectStandardError (Join-Path $ConfigDirectory "$stamp.watchdog.stderr.log") -RedirectStandardOutput (Join-Path $ConfigDirectory "$stamp.watchdog.stdout.log")
            try { $child.WaitForExit() } finally { $child.Dispose() }
            # Preserve diagnostics and recover even when a transport/runtime error
            # escapes the normal bounded API retry. The Python mutex prevents overlap.
            Start-Sleep -Seconds 30
        }
    } finally { Pop-Location }
} finally {
    Remove-Item Env:MT5_AGENT_TOKEN -ErrorAction SilentlyContinue
    $watchLock.ReleaseMutex()
    $watchLock.Dispose()
}
