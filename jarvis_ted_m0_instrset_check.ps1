<#
Jarvis_Ted — M0 instruction-set verification (optional, companion to Phase 1)
================================================================================
This requires PowerShell 7+ (pwsh), because it relies on .NET's
System.Runtime.Intrinsics.X86 API, which is not available in the
Windows PowerShell 5.1 runtime.

Run with:
    pwsh -File .\jarvis_ted_m0_instrset_check.ps1

If pwsh is not installed on this machine, do not install it just for this
check — report that it is unavailable and instruction-set support will be
marked UNMEASURED / Unknown rather than assumed.
#>

Write-Host "PowerShell edition: $($PSVersionTable.PSEdition), version $($PSVersionTable.PSVersion)"

try {
    Add-Type -TypeDefinition @"
using System.Runtime.Intrinsics.X86;
public class CpuFeat {
    public static bool HasSse2     => Sse2.IsSupported;
    public static bool HasAvx      => Avx.IsSupported;
    public static bool HasAvx2     => Avx2.IsSupported;
    public static bool HasFma      => Fma.IsSupported;
    public static bool HasAvx512F  => Avx512F.IsSupported;
    public static bool HasAvx512BW => Avx512BW.IsSupported;
    public static bool HasAvx512VL => Avx512F.VL.IsSupported;
}
"@
    Write-Host "SSE2:      $([CpuFeat]::HasSse2)"
    Write-Host "AVX:       $([CpuFeat]::HasAvx)"
    Write-Host "AVX2:      $([CpuFeat]::HasAvx2)"
    Write-Host "FMA:       $([CpuFeat]::HasFma)"
    Write-Host "AVX512F:   $([CpuFeat]::HasAvx512F)"
    Write-Host "AVX512BW:  $([CpuFeat]::HasAvx512BW)"
    Write-Host "AVX512VL:  $([CpuFeat]::HasAvx512VL)"
} catch {
    Write-Host "Could not query instruction-set intrinsics: $_"
}
