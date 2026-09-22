#Requires -Version 5.1
<#
.SYNOPSIS
  Accion de la tarea programada <TU-TAREA-SERVE>: opencode serve persistente, sin ventana.
.DESCRIPTION
   Lee credenciales de .env.mobile (no las imprime), levanta `opencode serve` SOLO en la IP
   Tailscale de esta PC (sin exposicion LAN) mas tunel `tailscale serve` si el tailnet lo permite,
   y vuelca su salida a logs/serve-mobile.log.
   La tarea programada lo corre oculto, lo arranca al iniciar sesion y lo reintenta si muere.
   Sin ventanitas.
#>
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$RepoRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $RepoRoot ".env.mobile"
$LogDir = Join-Path $RepoRoot "logs"
$LogFile = Join-Path $LogDir "serve-mobile.log"

if (!(Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
# Rotacion simple: si pasa 5 MB, se archiva y se empieza de nuevo.
if ((Test-Path -LiteralPath $LogFile) -and ((Get-Item -LiteralPath $LogFile).Length -gt 5MB)) {
  Move-Item -LiteralPath $LogFile -Destination ($LogFile + ".1") -Force
}

function Write-Log([string]$Msg) {
  Add-Content -LiteralPath $LogFile -Value ("[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Msg") -Encoding UTF8
}

Write-Log "=== arranque serve-mobile (tarea programada) ==="

foreach ($line in (Get-Content -LiteralPath $EnvFile -ErrorAction SilentlyContinue)) {
  $l = $line.Trim()
  if ($l -eq "" -or $l.StartsWith("#")) { continue }
  $idx = $l.IndexOf("=")
  if ($idx -lt 1) { continue }
  [System.Environment]::SetEnvironmentVariable($l.Substring(0, $idx).Trim(), $l.Substring($idx + 1).Trim(), "Process")
}
if ([string]::IsNullOrWhiteSpace($env:OPENCODE_SERVER_USERNAME)) { $env:OPENCODE_SERVER_USERNAME = "opencode" }
if ([string]::IsNullOrWhiteSpace($env:OPENCODE_SERVER_PASSWORD)) {
  Write-Log "FALTA OPENCODE_SERVER_PASSWORD en .env.mobile: no arranco (servidor quedaria desprotegido)."
  exit 1
}
$Port = $env:OPENCODE_MOBILE_PORT
if ([string]::IsNullOrWhiteSpace($Port)) { $Port = "4096" }
Write-Log "desalojo pre-bind (si hay okupa no-gestionado)..."
& (Join-Path $PSScriptRoot "serve-prebind.ps1") -Port $Port | ForEach-Object { Write-Log "prebind: $_" }

Write-Log "tunel tailscale serve: OMITIDO (requiere aprobacion admin del tailnet + el comando se cuelga; ver log). Acceso remoto = IP Tailscale directa."

$TailIP = ""
for ($i = 1; $i -le 12 -and [string]::IsNullOrWhiteSpace($TailIP); $i++) {
  $TailIP = ((& tailscale ip -4 2>$null) -join "").Trim()
  if ([string]::IsNullOrWhiteSpace($TailIP)) { Start-Sleep -Seconds 5 }
}
if ([string]::IsNullOrWhiteSpace($TailIP)) {
  Write-Log "SIN IP Tailscale tras 60s (daemon caido?): no arranco; reintento en 5 min."
  exit 1
}
Write-Log "escuchando en ${TailIP}:$Port (usuario: $($env:OPENCODE_SERVER_USERNAME))"
# opencode es shim .ps1 de npm: se invoca como comando, no como exe.
& opencode serve --hostname $TailIP --port $Port >> $LogFile 2>&1
$rc = $LASTEXITCODE
Write-Log "serve termino con codigo $rc (la tarea programada lo reintenta segun politica)."
exit $rc
