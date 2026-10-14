# vad_resource_monitor.ps1 — run in a second terminal while stream_vad_test.py runs in the first.
# -ExecutionPolicy Bypass -File .\vad_resource_monitor.ps1
param(
    [int]$IntervalSeconds = 2,
    [string]$LogPath = "C:\Jarvis_Ted\logs\vad_resource_$(Get-Date -Format 'yyyyMMdd_HHmmss').csv"
)
$logDir = Split-Path $LogPath -Parent
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

Write-Host "Waiting for stream_vad_test.py process..."
$targetProc = $null
while (-not $targetProc) {
    $candidates = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -like '*stream_vad_test.py*' })

    if ($candidates.Count -gt 0) {
        $targetProc = $candidates
    }
    else {
        Start-Sleep -Milliseconds 500
    }
}

$procIds = @($targetProc | ForEach-Object { $_.ProcessId })
Write-Host "Found PID(s) $($procIds -join ', '). Logging to $LogPath every $IntervalSeconds s."

"Timestamp,ElapsedSeconds,CPUPercent,WorkingSetMB,PrivateMemoryMB" | Out-File $LogPath -Encoding utf8
$startTime = Get-Date
$prevCpuTime = @{}

while ($true) {
    Start-Sleep -Seconds $IntervalSeconds

    $processes = @(
        Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -like '*stream_vad_test.py*' }
    )

    if ($processes.Count -eq 0) {
        Write-Host "Process exited. Log saved to $LogPath"
        break
    }

    $now = Get-Date
    $elapsed = [math]::Round(($now - $startTime).TotalSeconds, 1)
    $intervalWallTime = ($now - $startTime).TotalSeconds

    $cpuPercent = 0.0
    $workingSetMB = 0.0
    $privateMemMB = 0.0

    foreach ($proc in $processes) {
        $procId = $proc.ProcessId
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue

        if ($p) {
            $currentCpuTime = $p.CPU
            $previousCpuTime = if ($prevCpuTime.ContainsKey($procId)) { $prevCpuTime[$procId] } else { $currentCpuTime }

            $cpuPercent += if ($intervalWallTime -gt 0) {
                (($currentCpuTime - $previousCpuTime) / $IntervalSeconds) * 100
            }
            else {
                0
            }

            $workingSetMB += $p.WorkingSet64 / 1MB
            $privateMemMB += $p.PrivateMemorySize64 / 1MB

            $prevCpuTime[$procId] = $currentCpuTime
        }
    }

    $cpuPercent = [math]::Round($cpuPercent, 1)
    $workingSetMB = [math]::Round($workingSetMB, 1)
    $privateMemMB = [math]::Round($privateMemMB, 1)

    "$now,$elapsed,$cpuPercent,$workingSetMB,$privateMemMB" | Out-File $LogPath -Append -Encoding utf8
    Write-Host "[$elapsed s] CPU $cpuPercent% | WS $workingSetMB MB | Private $privateMemMB MB"

}