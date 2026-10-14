<#
Jarvis_Ted — M0 Phase 1: Static Environment Inventory Audit
=============================================================
Read-only. Does NOT install, download, delete, uninstall, or change any
system/GPU/driver setting. Only queries existing state and writes one new
log file under C:\Jarvis_Ted.

Run in a normal (non-administrator) PowerShell window is sufficient.

If script execution is blocked, run this once in that window first:
    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
then re-run this script.
#>

$ErrorActionPreference = 'SilentlyContinue'
$projectRoot = 'C:\Jarvis_Ted'
if (-not (Test-Path $projectRoot)) { New-Item -ItemType Directory -Path $projectRoot | Out-Null }
$logPath = Join-Path $projectRoot ("m0_phase1_audit_{0}.txt" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
Start-Transcript -Path $logPath -NoClobber | Out-Null

function Section($title) {
    Write-Host ""
    Write-Host "===================================================="
    Write-Host $title
    Write-Host "===================================================="
}

Section "0. RUN CONTEXT"
Write-Host "Timestamp: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')"
Write-Host "PowerShell version running this script: $($PSVersionTable.PSVersion) ($($PSVersionTable.PSEdition))"
Write-Host "Is 64-bit process: $([Environment]::Is64BitProcess)"
$pwsh7 = Get-Command pwsh -ErrorAction SilentlyContinue
Write-Host "PowerShell 7+ (pwsh) also installed on this machine: $([bool]$pwsh7)"

Section "1. HARDWARE INVENTORY"
Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer, Model, SystemFamily, TotalPhysicalMemory | Format-List
Get-CimInstance Win32_BIOS | Select-Object Manufacturer, SMBIOSBIOSVersion, ReleaseDate | Format-List
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture, BuildNumber | Format-List

Section "2. RAM"
Get-CimInstance Win32_PhysicalMemory | Select-Object BankLabel, Capacity, Speed, Manufacturer, PartNumber | Format-Table -AutoSize
$os = Get-CimInstance Win32_OperatingSystem
$totalGB = [math]::Round($os.TotalVisibleMemorySize/1MB,2)
$freeGB  = [math]::Round($os.FreePhysicalMemory/1MB,2)
Write-Host "Total visible memory: $totalGB GB"
Write-Host "Free physical memory right now: $freeGB GB"
Write-Host "Memory currently in use: $([math]::Round($totalGB-$freeGB,2)) GB"

Section "3. DISK (relevant to C:\Jarvis_Ted)"
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Select-Object DeviceID, VolumeName, @{N='FreeGB';E={[math]::Round($_.FreeSpace/1GB,2)}}, @{N='TotalGB';E={[math]::Round($_.Size/1GB,2)}} | Format-Table -AutoSize

Section "4. CPU"
Get-CimInstance Win32_Processor | Select-Object Name, Manufacturer, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed, CurrentClockSpeed, Architecture, AddressWidth | Format-List
Write-Host "--- Instruction-set support (must be measured, not inferred from CPU name) ---"
if ($pwsh7) {
    Write-Host "pwsh found. Run the companion check with:"
    Write-Host "    pwsh -File .\jarvis_ted_m0_instrset_check.ps1"
} else {
    Write-Host "No PowerShell 7 (pwsh) found. Windows PowerShell 5.1 (running this script) cannot query AVX2/AVX-512 support directly."
    Write-Host "STATUS: instruction-set support is UNMEASURED at this point. Do not assume AVX2/AVX-512 from the CPU model name alone."
}

Section "5. GPU / DISPLAY ADAPTERS"
Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, DriverDate, AdapterRAM, VideoProcessor | Format-List
Write-Host "--- Vulkan ICD registration (registry, read-only) ---"
$vk = 'HKLM:\SOFTWARE\Khronos\Vulkan\Drivers'
if (Test-Path $vk) { Get-ItemProperty -Path $vk | Format-List }
else { Write-Host "No Vulkan ICD driver registration found at $vk" }
Write-Host "--- OpenVINO environment check ---"
Write-Host "INTEL_OPENVINO_DIR = $env:INTEL_OPENVINO_DIR"
if (-not $env:INTEL_OPENVINO_DIR) { Write-Host "OpenVINO does not appear to be installed/configured (env var not set)." }

Section "6. AUDIO DEVICES"
Get-CimInstance Win32_SoundDevice | Select-Object Name, Manufacturer, Status | Format-Table -AutoSize
Write-Host "Note: default input/output device and supported sample rates are not reliably exposed via CIM."
Write-Host "Please also check manually: Settings > System > Sound, and note the default mic + speaker names, and the sample rate/format shown under each device's 'Advanced' properties."

Section "7. PYTHON / PIP"
if (Get-Command python -ErrorAction SilentlyContinue) { python --version; pip --version } else { Write-Host "python not found in PATH" }
if (Get-Command py -ErrorAction SilentlyContinue) { Write-Host "--- py launcher installed versions ---"; py -0p }

Section "8. GIT"
if (Get-Command git -ErrorAction SilentlyContinue) { git --version } else { Write-Host "git not found in PATH" }

Section "9. FFMPEG"
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { ffmpeg -version | Select-Object -First 3 } else { Write-Host "ffmpeg not found in PATH" }

Section "10. OLLAMA"
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    ollama --version
    Write-Host "--- installed models (ollama list) ---"
    ollama list
} else { Write-Host "ollama not found in PATH" }

Section "11. LM STUDIO"
$lmsPaths = @(
    "$env:LOCALAPPDATA\LM-Studio",
    "$env:USERPROFILE\.lmstudio",
    "$env:USERPROFILE\.cache\lm-studio"
)
foreach ($p in $lmsPaths) {
    if (Test-Path $p) { Write-Host "Found: $p" } else { Write-Host "Not found: $p" }
}
if (Get-Command lms -ErrorAction SilentlyContinue) { lms --version }

Section "12. LLAMA.CPP"
$llamaNames = @('llama-cli','llama-server','main','server')
foreach ($n in $llamaNames) {
    $cmd = Get-Command $n -ErrorAction SilentlyContinue
    if ($cmd) { Write-Host "Found in PATH: $($cmd.Source)" }
}
Write-Host "If nothing above was found in PATH, reply with the folder you originally built/installed llama.cpp into and a targeted check will be given for it."

Section "13. EXISTING MODEL FILE INVENTORY (.gguf / .bin, known locations only)"
$searchDirs = @(
    "$env:USERPROFILE\.ollama",
    "$env:USERPROFILE\.cache\lm-studio",
    "$env:LOCALAPPDATA\LM-Studio",
    $projectRoot
) | Where-Object { Test-Path $_ }
foreach ($d in $searchDirs) {
    Write-Host "--- Searching $d ---"
    Get-ChildItem -Path $d -Recurse -Include *.gguf,*.bin -ErrorAction SilentlyContinue |
        Select-Object FullName, @{N='SizeGB';E={[math]::Round($_.Length/1GB,3)}} | Format-Table -AutoSize
}
Write-Host "(Scoped to known model directories rather than a full C: scan, to keep this quick and non-invasive. Report back if models are kept elsewhere so that path can be added.)"

Section "14. RELEVANT ENVIRONMENT VARIABLES"
'OLLAMA_MODELS','INTEL_OPENVINO_DIR','CUDA_PATH' | ForEach-Object {
    Write-Host "$_ = $([Environment]::GetEnvironmentVariable($_))"
}

Section "15. CURRENT RESOURCE USAGE (normal desktop conditions)"
Write-Host "--- Top 10 processes by working set (RAM) ---"
Get-Process | Sort-Object WS -Descending | Select-Object -First 10 Name, Id, @{N='RAM_MB';E={[math]::Round($_.WS/1MB,1)}}, CPU | Format-Table -AutoSize
Write-Host "--- CPU load snapshot ---"
Get-CimInstance Win32_Processor | Select-Object LoadPercentage

Stop-Transcript | Out-Null
Write-Host ""
Write-Host "Done. Full log saved to: $logPath"
Write-Host "Paste that file's contents back into the chat."
