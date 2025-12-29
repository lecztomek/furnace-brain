#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Repo root = parent directory of script location
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppRoot   = (Resolve-Path (Join-Path $ScriptDir "..")).Path

# Optional env file next to script (KEY=VALUE, optionally "export KEY=VALUE")
$DevEnvPath = Join-Path $ScriptDir "dev.env"
if (Test-Path $DevEnvPath) {
  Get-Content $DevEnvPath | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }

    if ($line.StartsWith("export ")) { $line = $line.Substring(7).Trim() }

    $parts = $line.Split("=", 2)
    if ($parts.Count -ne 2) { return }

    $key = $parts[0].Trim()
    $val = $parts[1].Trim()

    # Strip quotes if present
    if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
      $val = $val.Substring(1, $val.Length - 2)
    }

    [System.Environment]::SetEnvironmentVariable($key, $val, "Process")
  }
}

function Get-EnvOrDefault([string]$name, [string]$defaultValue) {
  $v = [System.Environment]::GetEnvironmentVariable($name, "Process")
  if ([string]::IsNullOrWhiteSpace($v)) { return $defaultValue }
  return $v
}

# Defaults (override via dev.env or env vars)
$FrontDir    = Get-EnvOrDefault "FRONT_DIR"    (Join-Path $AppRoot "frontend")
$BackendHost = Get-EnvOrDefault "BACKEND_HOST" "127.0.0.1"
$BackendPort = Get-EnvOrDefault "BACKEND_PORT" "8000"
$GatewayBind = Get-EnvOrDefault "GATEWAY_BIND" "0.0.0.0"
$GatewayPort = Get-EnvOrDefault "GATEWAY_PORT" "8080"

$PythonBin     = Get-EnvOrDefault "PYTHON_BIN"  "python"
$GatewayScript = Get-EnvOrDefault "GATEWAY_SCRIPT" (Join-Path $ScriptDir "gateway.py")

# Export env vars (for child processes)
$existingPyPath = Get-EnvOrDefault "PYTHONPATH" ""
if ([string]::IsNullOrWhiteSpace($existingPyPath)) {
  [System.Environment]::SetEnvironmentVariable("PYTHONPATH", $AppRoot, "Process")
}

$DataRoot = Get-EnvOrDefault "FURNACE_BRAIN_DATA_ROOT" (Join-Path $AppRoot ".data")
[System.Environment]::SetEnvironmentVariable("FURNACE_BRAIN_DATA_ROOT", $DataRoot, "Process")
[System.Environment]::SetEnvironmentVariable("FURNACE_BRAIN_HW_RPI", (Get-EnvOrDefault "FURNACE_BRAIN_HW_RPI" "0"), "Process")

New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null

# Autodetect uvicorn runner
# Returns: @{ Cmd="..."; ArgsPrefix=@(...) }
function Resolve-UvicornRunner {
  $explicit = Get-EnvOrDefault "UVICORN_BIN" ""
  if ($explicit -and (Test-Path $explicit)) {
    return @{ Cmd=$explicit; ArgsPrefix=@() }
  }

  $candidates = @(
    (Join-Path $AppRoot ".venv\Scripts\uvicorn.exe"),
    (Join-Path $AppRoot ".venv\Scripts\uvicorn"),
    (Join-Path $AppRoot ".venv\bin\uvicorn")
  )

  foreach ($c in $candidates) {
    if (Test-Path $c) { return @{ Cmd=$c; ArgsPrefix=@() } }
  }

  # Try "python -m uvicorn"
  try {
    $p = Start-Process -FilePath $PythonBin -ArgumentList @("-m","uvicorn","--version") -NoNewWindow -PassThru -Wait -ErrorAction Stop
    if ($p.ExitCode -eq 0) {
      return @{ Cmd=$PythonBin; ArgsPrefix=@("-m","uvicorn") }
    }
  } catch {}

  # Try uvicorn from PATH
  $uv = Get-Command uvicorn -ErrorAction SilentlyContinue
  if ($uv) { return @{ Cmd=$uv.Path; ArgsPrefix=@() } }

  return $null
}

$uvRunner = Resolve-UvicornRunner
if (-not $uvRunner) {
  throw "Cannot find uvicorn. Install it (pip install uvicorn) or set UVICORN_BIN, or make sure 'python -m uvicorn --version' works."
}

# Simple validations
if (-not (Test-Path $GatewayScript)) { throw "Missing gateway.py: $GatewayScript" }
if (-not (Test-Path $FrontDir))      { throw "Missing FRONT_DIR: $FrontDir" }

Set-Location $AppRoot

Write-Host "APP_ROOT=$AppRoot"
Write-Host "FRONT_DIR=$FrontDir"
Write-Host ("BACKEND={0}:{1}" -f $BackendHost, $BackendPort)
Write-Host ("GATEWAY={0}:{1}" -f $GatewayBind, $GatewayPort)
Write-Host "DATA_ROOT=$DataRoot"
Write-Host ("UVICORN={0} {1}" -f $uvRunner.Cmd, ($uvRunner.ArgsPrefix -join ' '))

$backendProc = $null
$gatewayProc = $null

function Cleanup {
  Write-Host "Stopping dev processes..."
  if ($backendProc -and -not $backendProc.HasExited) {
    try { Stop-Process -Id $backendProc.Id -Force -ErrorAction SilentlyContinue } catch {}
  }
  if ($gatewayProc -and -not $gatewayProc.HasExited) {
    try { Stop-Process -Id $gatewayProc.Id -Force -ErrorAction SilentlyContinue } catch {}
  }
}

# Best-effort cleanup on shell exit (try/finally below is the main guard)
Register-EngineEvent PowerShell.Exiting -Action { Cleanup } | Out-Null

try {
  # backend: uvicorn backend.main:app --host ... --port ...
  $backendArgs = @() + $uvRunner.ArgsPrefix + @(
    "backend.main:app",
    "--host", $BackendHost,
    "--port", $BackendPort
  )
  $backendProc = Start-Process -FilePath $uvRunner.Cmd -ArgumentList $backendArgs -WorkingDirectory $AppRoot -NoNewWindow -PassThru

  # gateway: python gateway.py FRONT_DIR BACKEND_HOST BACKEND_PORT GATEWAY_PORT GATEWAY_BIND
  $gatewayProc = Start-Process -FilePath $PythonBin -ArgumentList @(
    $GatewayScript,
    $FrontDir,
    $BackendHost,
    $BackendPort,
    $GatewayPort,
    $GatewayBind
  ) -WorkingDirectory $AppRoot -NoNewWindow -PassThru

  Write-Host "OK:"
  Write-Host ("  local: http://localhost:{0}/" -f $GatewayPort)
  Write-Host ("  LAN:   http://<IP_machine>:{0}/" -f $GatewayPort)

  # Wait until one process exits
  while ($true) {
    if ($backendProc.HasExited -or $gatewayProc.HasExited) { break }
    Start-Sleep -Milliseconds 200
  }

  if ($backendProc.HasExited) {
    throw ("Backend (uvicorn) exited (ExitCode={0})." -f $backendProc.ExitCode)
  }
  if ($gatewayProc.HasExited) {
    throw ("Gateway (python gateway.py) exited (ExitCode={0})." -f $gatewayProc.ExitCode)
  }
}
finally {
  Cleanup
}
