#Requires -Version 5.1
<#
.SYNOPSIS
  Diagnostico rapido del servidor Opencode Mobile (solo lectura, nunca imprime la clave).
.DESCRIPTION
  Chequea en orden: puerto escuchando (netstat, no Get-NetTCPConnection) ->
  health con auth de .env.mobile -> nodos Tailscale visibles.
  Exit 0 = todo OK, 1 = algo falla (ver lineas FAIL).
.EXAMPLE
  .\scripts\check-mobile.ps1
#>
[CmdletBinding()]
param()
$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $RepoRoot ".env.mobile"
$fail = 0

function Get-EnvVal([string]$Name) {
  if (!(Test-Path -LiteralPath $EnvFile)) { return "" }
  foreach ($line in (Get-Content -LiteralPath $EnvFile)) {
    $l = $line.Trim()
    if ($l -eq "" -or $l.StartsWith("#")) { continue }
    $idx = $l.IndexOf("=")
    if ($idx -lt 1) { continue }
    if ($l.Substring(0, $idx).Trim() -eq $Name) { return $l.Substring($idx + 1).Trim() }
  }
  return ""
}

$User = Get-EnvVal "OPENCODE_SERVER_USERNAME"
if ([string]::IsNullOrWhiteSpace($User)) { $User = "opencode" }
$Pass = Get-EnvVal "OPENCODE_SERVER_PASSWORD"
$Port = Get-EnvVal "OPENCODE_MOBILE_PORT"
if ([string]::IsNullOrWhiteSpace($Port)) { $Port = "4096" }
$TailIP = ((& tailscale ip -4 2>$null) -join "").Trim()
if ([string]::IsNullOrWhiteSpace($TailIP)) {
  Write-Host "[FAIL] sin IP Tailscale (daemon caido?): sin chequeo remoto (no miento con loopback)"
  exit 1
}

# 1. Puerto (netstat: Get-NetTCPConnection dio un falso negativo el 2026-09-19)
$listen = netstat -ano | Select-String ("LISTENING") | Select-String (":$Port\s")
if ($listen) { Write-Host "[OK] puerto $Port escuchando: $($listen.Line.Trim())" }
else { Write-Host "[FAIL] nada escucha en $Port (el serve esta caido)"; $fail = 1 }

# 2. Health con auth (sin auth debe dar 401 = auth activa)
try {
  Invoke-RestMethod -Uri "http://${TailIP}:$Port/global/health" -TimeoutSec 8 | Out-Null
  Write-Host "[FAIL] health responde SIN auth (servidor desprotegido)"
  $fail = 1
} catch {
  $code = $null
  try { $code = [int]$_.Exception.Response.StatusCode } catch {}
  if ($code -eq 401) {
    Write-Host "[OK] sin auth responde 401 (auth activa)"
  } else {
    Write-Host "[FAIL] sin 401 (caido/timeout/red: $($_.Exception.Message))"
    $fail = 1
  }
}
if ([string]::IsNullOrWhiteSpace($Pass)) {
  Write-Host "[FAIL] sin OPENCODE_SERVER_PASSWORD en .env.mobile (no se puede probar auth)"
  $fail = 1
} else {
  $b64 = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes(("$User`:$Pass")))
  try {
    $h = Invoke-RestMethod -Uri "http://${TailIP}:$Port/global/health" `
      -Headers @{Authorization = "Basic $b64"} -TimeoutSec 8
    if ($h.healthy -eq $true) { Write-Host "[OK] health con auth: healthy=true (v$($h.version))" }
    else { Write-Host "[FAIL] health con auth devolvio algo raro"; $fail = 1 }
  } catch {
    Write-Host "[FAIL] health con auth fallo (clave de .env.mobile no coincide con el serve)";
    $fail = 1
  }
}

# 3. Tailscale (red, no servidor: solo informativo)
try {
  $ts = tailscale status 2>$null
  if ($ts) { Write-Host "[OK] tailnet visible:"; $ts | Select-Object -First 4 | ForEach-Object { Write-Host "       $_" } }
  else { Write-Host "[WARN] tailscale status vacio (red caida o tailscale apagado)" }
} catch {
  Write-Host "[WARN] tailscale no responde"
}

if ($fail -eq 0) { Write-Host "check-mobile: TODO OK" } else { Write-Host "check-mobile: HAY FALLOS" }
exit $fail
