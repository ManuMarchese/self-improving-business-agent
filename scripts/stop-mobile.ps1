#Requires -Version 5.1
<#
.SYNOPSIS
  Detiene la exposicion via Tailscale Serve (no detiene opencode).
#>
[CmdletBinding()]
param()
$ErrorActionPreference = "Continue"
& tailscale serve off 2>&1 | Write-Host
& tailscale serve status 2>&1 | Write-Host
Write-Host "Tailscale Serve apagado. El servidor opencode (si seguia corriendo) ya no es accesible por tailnet." -ForegroundColor Cyan
