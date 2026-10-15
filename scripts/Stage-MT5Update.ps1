# Downloads and installs a candidate ONLY. No production paths or account credentials.
[CmdletBinding()]
param([switch]$Inspect)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$maintenanceRoot = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'TradeTrack MT5 Maintenance'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($Inspect) {
    [ordered]@{ purpose='candidate-staging-only'; productionChanges=$false; root=$maintenanceRoot; isSystem=($identity.User.Value -eq 'S-1-5-18'); installed=(Test-Path -LiteralPath $maintenanceRoot) } | ConvertTo-Json
    exit 0
}
if ($identity.User.Value -ne 'S-1-5-18') { throw 'Run the registered SYSTEM staging task, not this script interactively.' }
if ($PSScriptRoot -ne $maintenanceRoot) { throw 'Unexpected installation path.' }
if ((Get-Item -LiteralPath $maintenanceRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Unsafe staging root.' }
$runRoot = Join-Path $maintenanceRoot ('runs\' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N'))
$statusFile = Join-Path $maintenanceRoot 'status.json'
$phase = 'preflight'
function Write-Status([string]$state, [object]$detail) {
    $value = [ordered]@{ state=$state; at=[DateTime]::UtcNow.ToString('o'); phase=$script:phase; detail=$detail; productionChanges=$false }
    $temporary = Join-Path $maintenanceRoot 'status.pending.json'
    $value | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $statusFile -Force
}
function Assert-Vendor([string]$path) {
    $sig = Get-AuthenticodeSignature -LiteralPath $path
    if ($sig.Status -ne 'Valid' -or $null -eq $sig.SignerCertificate -or $sig.SignerCertificate.Subject -notmatch '(^|,\s*)O=MetaQuotes Ltd\.(,|$)') {
        throw 'Vendor signature validation failed.'
    }
}
try {
    if ([IO.DriveInfo]::new([IO.Path]::GetPathRoot($maintenanceRoot)).AvailableFreeSpace -lt 5GB) { throw 'Insufficient staging disk space.' }
    New-Item -ItemType Directory -Path $runRoot | Out-Null
    $terminalRoot = Join-Path $runRoot 'terminal'
    $setup = Join-Path $runRoot 'mt5setup.exe'
    $phase = 'download'
    Write-Status 'running' $null
    Add-Type -AssemblyName System.Net.Http
    $handler = New-Object Net.Http.HttpClientHandler
    $handler.AllowAutoRedirect = $false
    $client = New-Object Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromMinutes(3)
    $cancel = New-Object Threading.CancellationTokenSource(180000)
    $response = $null; $inputStream = $null; $outputStream = $null
    try {
        # Official Windows link on https://www.metatrader5.com/en/download.
        $url = 'https://download.terminal.free/cdn/web/metaquotes.ltd/mt5/mt5setup.exe'
        $response = $client.GetAsync($url, [Net.Http.HttpCompletionOption]::ResponseHeadersRead, $cancel.Token).GetAwaiter().GetResult()
        if ([int]$response.StatusCode -ne 200) { throw 'Unexpected download response; redirects require review.' }
        if ($response.Content.Headers.ContentLength -gt 128MB) { throw 'Installer exceeds size limit.' }
        $inputStream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $outputStream = [IO.File]::Open($setup, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $buffer = New-Object byte[] 65536
        $total = 0
        while (($count = $inputStream.ReadAsync($buffer, 0, $buffer.Length, $cancel.Token).GetAwaiter().GetResult()) -gt 0) {
            $total += $count
            if ($total -gt 128MB) { throw 'Installer exceeds size limit.' }
            $outputStream.Write($buffer, 0, $count)
        }
        $outputStream.Flush($true)
    } finally {
        if ($outputStream) { $outputStream.Dispose() }
        if ($inputStream) { $inputStream.Dispose() }
        if ($response) { $response.Dispose() }
        $cancel.Dispose(); $client.Dispose(); $handler.Dispose()
    }
    $phase = 'verify-installer'
    Assert-Vendor $setup
    # Start suspended, assign a kill-on-close job, then resume. All installer
    # descendants (including an automatically opened terminal) are bounded.
    Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class TradeTrackInstallerJob {
 [StructLayout(LayoutKind.Sequential)] struct IO { public ulong a,b,c,d,e,f; }
 [StructLayout(LayoutKind.Sequential)] struct Basic { public long a,b; public uint flags; public UIntPtr min,max; public uint count; public UIntPtr affinity; public uint priority,scheduling; }
 [StructLayout(LayoutKind.Sequential)] struct Limits { public Basic basic; public IO io; public UIntPtr process,job,peakProcess,peakJob; }
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct Startup { public uint cb; public string reserved,desktop,title; public uint x,y,xsize,ysize,xchars,ychars,fill,flags; public ushort show,reserved2; public IntPtr reservedPtr,input,output,error; }
 [StructLayout(LayoutKind.Sequential)] struct ProcessInfo { public IntPtr process,thread; public uint pid,tid; }
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr security,string name);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job,int kind,ref Limits limits,uint length);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CreateProcess(string app,StringBuilder command,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string cwd,ref Startup startup,out ProcessInfo info);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
 [DllImport("kernel32.dll")] static extern uint ResumeThread(IntPtr thread);
 [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle,uint ms);
 [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr process,out uint code);
 [DllImport("kernel32.dll")] static extern bool TerminateProcess(IntPtr process,uint code);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
 public static uint Run(string exe,string destination,string cwd) {
  IntPtr job=CreateJobObject(IntPtr.Zero,null); ProcessInfo p=new ProcessInfo(); bool assigned=false;
  if(job==IntPtr.Zero) throw new Exception("JOB_CREATE_FAILED");
  try {
   Limits limits=new Limits(); limits.basic.flags=0x2000;
   if(!SetInformationJobObject(job,9,ref limits,(uint)Marshal.SizeOf(typeof(Limits)))) throw new Exception("JOB_LIMIT_FAILED");
   Startup s=new Startup(); s.cb=(uint)Marshal.SizeOf(typeof(Startup)); s.flags=1; s.show=0;
   if(!CreateProcess(exe,new StringBuilder("\""+exe+"\" /auto /path:\""+destination+"\""),IntPtr.Zero,IntPtr.Zero,false,0x08000004,IntPtr.Zero,cwd,ref s,out p)) throw new Exception("INSTALLER_START_FAILED");
   if(!AssignProcessToJobObject(job,p.process)) throw new Exception("JOB_ASSIGN_FAILED");
   assigned=true;
   if(ResumeThread(p.thread)==0xffffffff) throw new Exception("INSTALLER_RESUME_FAILED");
   if(WaitForSingleObject(p.process,600000)!=0) throw new Exception("INSTALLER_TIMEOUT");
   uint code; if(!GetExitCodeProcess(p.process,out code)) throw new Exception("INSTALLER_EXIT_UNKNOWN");
   return code;
  } finally {
   if(!assigned && p.process!=IntPtr.Zero) TerminateProcess(p.process,1);
   CloseHandle(job);
   if(p.thread!=IntPtr.Zero) CloseHandle(p.thread);
   if(p.process!=IntPtr.Zero) CloseHandle(p.process);
  }
 }
}
'@
    # Keep vendor staging caches separate from the production user's profile.
    $env:APPDATA = Join-Path $runRoot 'profile\Roaming'
    $env:LOCALAPPDATA = Join-Path $runRoot 'profile\Local'
    $env:TEMP = Join-Path $runRoot 'temp'; $env:TMP = $env:TEMP
    foreach ($folder in @($env:APPDATA,$env:LOCALAPPDATA,$env:TEMP)) { New-Item -ItemType Directory -Path $folder -Force | Out-Null }
    $phase = 'install-candidate'
    Write-Status 'running' $null
    $exitCode = [TradeTrackInstallerJob]::Run($setup,$terminalRoot,$runRoot)
    if ($exitCode -ne 0) { throw 'Installer returned nonzero exit code.' }
    $phase = 'verify-candidate'
    $files = @()
    foreach ($name in @('terminal64.exe','MetaEditor64.exe','metatester64.exe')) {
        $path = Join-Path $terminalRoot $name
        Assert-Vendor $path
        $item = Get-Item -LiteralPath $path
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Unsafe candidate file.' }
        $files += [ordered]@{ name=$name; sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash; version=$item.VersionInfo.FileVersion }
    }
    $manifest = [ordered]@{ schemaVersion=1; createdAt=[DateTime]::UtcNow.ToString('o'); state='candidate_ready'; approvedForProduction=$false; directory=$terminalRoot; files=$files }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $runRoot 'candidate.json') -Encoding UTF8
    $pending = Join-Path $maintenanceRoot 'latest-candidate.pending.json'
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $pending -Encoding UTF8
    Move-Item -LiteralPath $pending -Destination (Join-Path $maintenanceRoot 'latest-candidate.json') -Force
    Write-Status 'candidate_ready' ([ordered]@{ directory=$terminalRoot; approvedForProduction=$false })
} catch {
    # No raw exception/installer output or credentials are sent to external services.
    Write-Status 'failed' ([ordered]@{
        reason='Candidate staging failed; production terminals were not modified.'
        exceptionType=$_.Exception.GetType().FullName
        hresult=$_.Exception.HResult
        runDirectory=$runRoot
    })
    exit 1
}
