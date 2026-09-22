#Requires -Version 5.1
<#
.SYNOPSIS
  Desalojo pre-bind para <TU-TAREA-SERVE>: si el puerto lo tiene un opencode
  ajeno a la tarea (okupa), lo desaloja. Si es instancia gestionada u otro programa, no toca nada.
.DESCRIPTION
  Regla: camina los ancestros del proceso que escucha buscando serve-mobile-task.ps1.
  Gestionada = no tocar. opencode-serve sin ancestro gestionado = okupa -> Stop-Process.
  Otro programa = se deja (el serve fallara a gritos en el log, no se mata ajeno).
  Exit siempre 0 (esto es higiene, no gate).
#>
[CmdletBinding()]
param([int]$Port = 4096)
$ErrorActionPreference = "Continue"
$hit = netstat -ano | Select-String "LISTENING" | Select-String ":$Port\s"
if (-not $hit) {
  Write-Host "PREBIND: puerto $Port libre, nada que desalojar"
  exit 0
}
foreach ($ln in $hit) {
  $targetPid = ($ln.Line.Trim() -split "\s+")[-1]
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$targetPid" -ErrorAction SilentlyContinue
  if (-not $proc) { continue }
  $managed, $cur, $depth = $false, $proc, 0
  while ($cur -and $depth -lt 5) {
    if ($cur.CommandLine -match "serve-mobile-task\.ps1") { $managed = $true; break }
    if (-not $cur.ParentProcessId) { break }
    $cur = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $cur.ParentProcessId) -ErrorAction SilentlyContinue
    $depth++
  }
  if ($managed) {
    Write-Host "PREBIND: PID $targetPid es instancia gestionada, no se toca"
    continue
  }
  if ($proc.CommandLine -match "opencode.*serve") {
    Write-Host "PREBIND: okupa PID $targetPid -> desalojando"
    Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
  } else {
    $cmd = if ($proc.CommandLine) { $proc.CommandLine.Substring(0, [Math]::Min(100, $proc.CommandLine.Length)) } else { "(sin cmdline)" }
    Write-Host "PREBIND: PID $targetPid no es opencode, se deja: $cmd"
  }
}
exit 0
