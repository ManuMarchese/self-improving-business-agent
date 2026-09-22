#Requires -Version 5.1
<#
.SYNOPSIS
  Inicia opencode serve listo para Opencode Mobile (Android).
.DESCRIPTION
  - Carga credenciales desde .env.mobile (lo crea con clave fuerte si falta).
  - Modo Tailscale (recomendado): escucha en 127.0.0.1 + publica con `tailscale serve`.
  - Modo LAN: escucha en 0.0.0.0 para misma Wi-Fi.
  - Muestra URL, usuario y health-check para cargar en la app movil.
.EXAMPLE
  .\scripts\start-mobile.ps1
  .\scripts\start-mobile.ps1 -Mode LAN
  .\scripts\start-mobile.ps1 -Mode Tailscale -Port 4096
#>
[CmdletBinding()]
param(
  [ValidateSet("Tailscale", "LAN")]
  [string]$Mode = "Tailscale",
  [int]$Port = 4096
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $RepoRoot ".env.mobile"

function Read-DotEnvFile([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return }
  Get-Content -LiteralPath $Path | ForEach-Object {
    $line = $_.Trim()
    if ([string]::IsNullOrWhiteSpace($line)) { return }
    if ($line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    $k = $line.Substring(0, $idx).Trim()
    $v = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
    if ($k -ne "") {
      [System.Environment]::SetEnvironmentVariable($k, $v, "Process")
      # Expone tambien como variable de sesion para uso posterior
      Set-Variable -Name ("env_" + $k) -Value $v -Scope Script -ErrorAction SilentlyContinue
    }
  }
}

function New-StrongPassword([int]$Length = 32) {
  $chars = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_.~".ToCharArray()
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $bytes = New-Object byte[] $Length
    $rng.GetBytes($bytes)
    $sb = New-Object System.Text.StringBuilder
    foreach ($b in $bytes) { [void]$sb.Append($chars[$b % $chars.Length]) }
    return $sb.ToString()
  } finally {
    $rng.Dispose()
  }
}

Read-DotEnvFile -Path $EnvFile

if ([string]::IsNullOrWhiteSpace($env:OPENCODE_SERVER_USERNAME)) {
  $env:OPENCODE_SERVER_USERNAME = "opencode"
}
if ([string]::IsNullOrWhiteSpace($env:OPENCODE_SERVER_PASSWORD)) {
  $generated = New-StrongPassword -Length 32
  $env:OPENCODE_SERVER_PASSWORD = $generated
  $content = @(
    "# Credenciales para Opencode Mobile. NO commitear."
    "OPENCODE_SERVER_USERNAME=$($env:OPENCODE_SERVER_USERNAME)"
    "OPENCODE_SERVER_PASSWORD=$generated"
    "OPENCODE_MOBILE_PORT=$Port"
  ) -join "`r`n"
  Set-Content -LiteralPath $EnvFile -Value ($content + "`r`n") -Encoding UTF8
  Write-Host "Generada clave fuerte y guardada en .env.mobile" -ForegroundColor Yellow
}
if ([string]::IsNullOrWhiteSpace($env:OPENCODE_MOBILE_PORT)) {
  $env:OPENCODE_MOBILE_PORT = "$Port"
} else {
  $Port = [int]$env:OPENCODE_MOBILE_PORT
}

$Username = $env:OPENCODE_SERVER_USERNAME

# Detectar IPs utiles para mostrar
$LanIp = $null
try {
  $LanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.*" -and $_.PrefixOrigin -ne "WellKnown" } |
    Sort-Object { if ($_.IPAddress -like "192.168.*") { 0 } else { 1 } } |
    Select-Object -First 1 -ExpandProperty IPAddress)
} catch { }

$TsIp = $null
$TsDns = $null
try {
  $tsStatus = tailscale status 2>$null
  if ($tsStatus) {
    $line = $tsStatus | Where-Object { $_ -match "desktop-" } | Select-Object -First 1
    if ($line) { $TsIp = ($line -split "\s+")[0] }
  }
  $tsJson = tailscale status --json 2>$null | Out-String
  if ($tsJson -match '"DNSName"\s*:\s*"([^"]+)"') { $TsDns = $Matches[1].TrimEnd(".") }
} catch { }

if ($Mode -eq "Tailscale") {
  $Hostname = "127.0.0.1"
  Write-Host ""
  Write-Host "== Opencode Mobile | modo Tailscale (recomendado) ==" -ForegroundColor Cyan
  try {
    & tailscale serve --bg $Port 2>&1 | Out-String | Write-Host
    Write-Host ""
    & tailscale serve status 2>&1 | Write-Host
  } catch {
    Write-Warning "No se pudo configurar 'tailscale serve'. Seguira funcionando por IP directa de Tailscale. Detalle: $($_.Exception.Message)"
  }
  Write-Host ""
  Write-Host "Servidor API : http://127.0.0.1:$Port" -ForegroundColor Green
  if ($TsIp) { Write-Host "Desde el celu (Tailscale IP directa): http://$TsIp`:$Port" -ForegroundColor Green }
  if ($TsDns) { Write-Host "Desde el celu (MagicDNS HTTPS): https://$TsDns`:443  -> ver 'tailscale serve status' para el puerto exacto" -ForegroundColor Green }
  Write-Host "Health-check : http://127.0.0.1:$Port/global/health" -ForegroundColor DarkGray
} else {
  $Hostname = "0.0.0.0"
  Write-Host ""
  Write-Host "== Opencode Mobile | modo LAN (misma Wi-Fi) ==" -ForegroundColor Cyan
  if ($LanIp) {
    Write-Host "Desde el celu: http://$LanIp`:$Port" -ForegroundColor Green
    Write-Host "Health-check : http://$LanIp`:$Port/global/health" -ForegroundColor DarkGray
  } else {
    Write-Host "Desde el celu: http://<IP-de-esta-PC>:$Port" -ForegroundColor Green
  }
  Write-Host "NOTA: Windows puede pedir permiso de Firewall para el puerto $Port (red privada)." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Usuario      : $Username"
Write-Host "Password     : (tomada de OPENCODE_SERVER_PASSWORD en .env.mobile)"
Write-Host "Proyecto     : $RepoRoot"
Write-Host ""
Write-Host "En la app movil cargá:" -ForegroundColor Cyan
Write-Host "  1) Server URL = la URL de arriba (probá primero /global/health en el navegador del celu)"
Write-Host "  2) Username   = $Username"
Write-Host "  3) Password   = la de .env.mobile"
Write-Host "  4) Si la raíz devuelve HTML o 404, probá agregando /api al final."
Write-Host ""
Write-Host "Iniciando: opencode serve --hostname $Hostname --port $Port  (Ctrl+C para detener)" -ForegroundColor Cyan
Write-Host ""

& opencode serve --hostname $Hostname --port $Port
