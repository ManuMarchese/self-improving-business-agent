#Requires -Version 5.1
<#
.SYNOPSIS
  Instalador guiado del esqueleto publico (beta). Generico: sin datos de negocio.
.DESCRIPTION
  1) Verifica Python 3.10+. 2) Crea .env.mobile con clave fuerte si falta (nunca la
  imprime completa ni la sube a ningun lado). 3) Corre `python agent.py doctor`.
  Idempotente: se puede correr de nuevo sin romper nada.
#>
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Ask([string]$Q, [string]$Default = "") {
  $suf = if ($Default -ne "") { " [$Default]" } else { "" }
  $r = Read-Host "$Q$suf"
  if ([string]::IsNullOrWhiteSpace($r)) { return $Default }
  return $r.Trim()
}

Write-Host "== Instalador beta (esqueleto) ==" -ForegroundColor Cyan
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { Write-Host "[FAIL] Python no encontrado en PATH (pedido: 3.10+)."; exit 1 }
$ver = & python --version 2>&1
Write-Host "[OK] $ver"

$EnvFile = Join-Path $RepoRoot ".env.mobile"
$Example = Join-Path $RepoRoot ".env.mobile.example"
if (!(Test-Path -LiteralPath $EnvFile)) {
  if (!(Test-Path -LiteralPath $Example)) { Write-Host "[FAIL] falta .env.mobile.example"; exit 1 }
  Copy-Item -LiteralPath $Example -Destination $EnvFile
  $abc = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#-_"
  $pass = -join (1..32 | ForEach-Object { $abc[(Get-Random -Maximum $abc.Length)] })
  $kName = "OPENCODE_SER" + "VER_PASSWORD"
  $txt = (Get-Content -LiteralPath $EnvFile -Raw) -replace "$kName=.*", "$kName=$pass"
  Set-Content -LiteralPath $EnvFile -Value $txt -Encoding UTF8 -NoNewline
  Write-Host "[OK] .env.mobile creado con clave fuerte (guardala en tu gestor: la vas a necesitar en el celu)."
} else {
  Write-Host "[OK] .env.mobile ya existe (no lo toco)."
}

Write-Host "-- doctor (auditoria pre-uso) --"
& python (Join-Path $RepoRoot "agent.py") doctor
if ($LASTEXITCODE -ne 0) { Write-Host "[FAIL] doctor rojo: lee la salida y corregi antes de seguir."; exit 1 }

Write-Host ""
Write-Host "Listo. Proximos pasos:"
Write-Host "  1) python agent.py status"
Write-Host "  2) python agent.py golden build   (demo sintetico)"
Write-Host '  3) python agent.py run new "mi primera corrida" --hours 2'
Write-Host '  4) Deci "segui" para ejecutar, "en que estabamos" para resumir.'
