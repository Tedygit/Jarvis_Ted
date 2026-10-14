param(
    [Parameter(Mandatory = $true)][ValidateSet('earphone', 'speakers')][string]$Mode,
    [switch]$Aec,
    [switch]$AecDump,
    [switch]$Tools,
    [string]$ProjectRoot = '',
    [int]$AecMicDelayMs = -1,
    [string[]]$ExtraArgs = @()
)
# start_jarvis.ps1 - one launcher for Jarvis_Ted (Account 3).
#   -Mode earphone : JARVIS_FULL_DUPLEX=1 (voice interruption; earphone must be fully plugged in BEFORE starting)
#   -Mode speakers : default half-duplex (replies play in full; no voice interruption)
#   -Aec           : speakers only: JARVIS_AEC=1 (experimental echo cancellation, see the status file)
#   -AecDump       : with -Aec: JARVIS_AEC_DUMP=1 (writes the live signals to C:\Jarvis_Ted\aec_test\live_dump)
#   -AecMicDelayMs : with -Aec: JARVIS_AEC_MIC_DELAY_MS (default in the app: 96)
# Every JARVIS_* switch is cleared first, so a variable left in the window cannot change the run.
$ErrorActionPreference = 'Stop'
$python     = 'C:\Jarvis_Ted\.venv\Scripts\python.exe'
$app        = 'C:\Jarvis_Ted\tools\stream_asr_test.py'
$whisperExe = 'C:\tools\whisper.cpp\build\bin\Release\whisper-server.exe'
$whisperMdl = 'C:\tools\whisper.cpp\models\ggml-small.en.bin'
$llamaExe   = 'C:\tools\llama.cpp\build\bin\Release\llama-server.exe'
$llamaMdl   = 'C:\Jarvis_Ted\models\Qwen2.5-Coder-3B-Instruct-Q5_K_M.gguf'
$allVars    = @('JARVIS_NO_BARGE_IN', 'JARVIS_HALF_DUPLEX', 'JARVIS_FULL_DUPLEX', 'JARVIS_AEC', 'JARVIS_AEC_MIC_DELAY_MS', 'JARVIS_AEC_DUMP', 'JARVIS_AEC_DUMP_DIR', 'JARVIS_TOOLS', 'JARVIS_PROJECT_ROOT')

foreach ($p in @($python, $app, $whisperExe, $whisperMdl, $llamaExe, $llamaMdl)) {
    if (-not (Test-Path $p)) { Write-Output "NOT FOUND: $p - stop here and report this line."; return }
}

function Test-Port([int]$port) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $c.BeginConnect('127.0.0.1', $port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(300) -and $c.Connected) { return $true }
        return $false
    } catch { return $false } finally { $c.Close() }
}

function Test-LlamaReady {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8081/health' -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

# Messages use Write-Host so the function returns ONLY a boolean to the caller.
function Wait-Ready([string]$name, [scriptblock]$check, [int]$seconds) {
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt $seconds) {
        if (& $check) {
            Write-Host ("{0} ready after {1:N0}s" -f $name, ((Get-Date) - $t0).TotalSeconds)
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "$name NOT READY after $seconds s - stop here and report this line."
    return $false
}

if (Test-Port 8080) {
    Write-Output 'whisper-server: port 8080 already answering, not starting another'
} else {
    Write-Output 'whisper-server: starting'
    Start-Process powershell -ArgumentList '-NoExit', '-Command', ('& "' + $whisperExe + '" -m "' + $whisperMdl + '" -t 8 --host 127.0.0.1 --port 8080 -nfa')  # ACCOUNT1_NFA_V1: flash attention off (flash attention with a reduced audio_ctx gave wrong, slow decodes; status update 20)
    if (-not (Wait-Ready 'whisper-server' { Test-Port 8080 } 120)) { return }
}

if (Test-LlamaReady) {
    Write-Output 'llama-server: /health already OK on 8081, not starting another'
} elseif (Test-Port 8081) {
    Write-Output 'llama-server: port 8081 is open but not healthy yet, waiting'
    if (-not (Wait-Ready 'llama-server' { Test-LlamaReady } 180)) { return }
} else {
    Write-Output 'llama-server: starting'
    Start-Process powershell -ArgumentList '-NoExit', '-Command', ('& "' + $llamaExe + '" -m "' + $llamaMdl + '" -c 4096 -t 8 -np 1 --host 127.0.0.1 --port 8081')
    if (-not (Wait-Ready 'llama-server' { Test-LlamaReady } 180)) { return }
}

foreach ($v in $allVars) { Remove-Item "Env:\$v" -ErrorAction SilentlyContinue }
if ($Mode -eq 'earphone') {
    $env:JARVIS_FULL_DUPLEX = '1'
    Write-Output 'MODE earphone: JARVIS_FULL_DUPLEX=1 (voice interruption on). Earphone must have been fully plugged in BEFORE starting.'
    if ($Aec) { Write-Output 'NOTE: -Aec is for speakers mode and is ignored with the earphone.' }
} elseif ($Aec) {
    $env:JARVIS_AEC = '1'
    if ($AecDump) { $env:JARVIS_AEC_DUMP = '1' }
    if ($AecMicDelayMs -ge 0) { $env:JARVIS_AEC_MIC_DELAY_MS = [string]$AecMicDelayMs }
    Write-Output ('MODE speakers + EXPERIMENTAL echo cancellation: JARVIS_AEC=1, dump={0}, mic delay={1}' -f [bool]$AecDump, $(if ($AecMicDelayMs -ge 0) { "$AecMicDelayMs ms" } else { 'app default' }))
    Write-Output 'WARNING: set the speaker volume to 60 percent or lower. At 80 percent Jarvis answers its own voice (tested, not supported).'  # ACCOUNT1_VOLWARN_V1
} else {
    Write-Output 'MODE speakers: default half-duplex (replies play in full, no voice interruption).'
}
if ($Tools) {
    $env:JARVIS_TOOLS = '1'
    if ($ProjectRoot) { $env:JARVIS_PROJECT_ROOT = $ProjectRoot }
    Write-Output ('TOOLS on: read-only coding tools, project root {0}' -f $(if ($ProjectRoot) { $ProjectRoot } else { 'C:\Jarvis_Ted (default)' }))
}
try {
    & $python $app @ExtraArgs
} finally {
    foreach ($v in $allVars) { Remove-Item "Env:\$v" -ErrorAction SilentlyContinue }
    Write-Output 'Servers were left running in their own windows (close them when finished).'
}