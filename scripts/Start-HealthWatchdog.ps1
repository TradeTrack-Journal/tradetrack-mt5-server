param([string]$ConfigDirectory = 'C:\TradeTrack\worker\production')
$ErrorActionPreference = 'Stop'
$watchLock = [Threading.Mutex]::new($false, 'Local\TradeTrackHealthWatchdog')
if (-not $watchLock.WaitOne(0)) { exit 0 }
try {
    $secureToken = (Get-Content -LiteralPath (Join-Path $ConfigDirectory 'node-token.dpapi') -Raw).Trim() | ConvertTo-SecureString
    $tokenPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    try { $env:MT5_AGENT_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPtr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPtr); $secureToken.Dispose() }
    $repo = Split-Path -Parent $PSScriptRoot
    Push-Location $repo
    try {
        & (Join-Path $repo '.venv\Scripts\python.exe') -m scripts.watch_mt5_health --config (Join-Path $ConfigDirectory 'agent.json') --log (Join-Path $ConfigDirectory 'health-watchdog.jsonl')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally { Pop-Location }
} finally {
    Remove-Item Env:MT5_AGENT_TOKEN -ErrorAction SilentlyContinue
    $watchLock.ReleaseMutex()
    $watchLock.Dispose()
}
