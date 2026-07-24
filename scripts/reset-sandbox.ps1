param(
  [ValidateSet("Blank", "Sample")]
  [string]$Mode = "Blank"
)

$ErrorActionPreference = "Stop"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw "Docker is not installed or is not available in this terminal. Run this reset on the machine that hosts InfoMancer, or install Docker first."
}
$Workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not (Test-Path (Join-Path $Workspace "compose.sandbox.yaml")) -or -not (Test-Path (Join-Path $Workspace "Dockerfile"))) {
  throw "The sandbox reset must run from the InfoMancer repository."
}

function Remove-SandboxDirectory([string]$Name) {
  $Target = [IO.Path]::GetFullPath((Join-Path $Workspace $Name))
  if (-not $Target.StartsWith($Workspace + [IO.Path]::DirectorySeparatorChar)) {
    throw "Refusing to remove a path outside the InfoMancer workspace: $Target"
  }
  if (Test-Path -LiteralPath $Target) {
    Remove-Item -LiteralPath $Target -Recurse -Force
  }
}

Set-Location $Workspace
docker compose -p infomancer-sandbox -f compose.sandbox.yaml down --remove-orphans
Remove-SandboxDirectory "data-sandbox"
Remove-SandboxDirectory "sandbox-media"

if (-not (Test-Path .env.sandbox)) {
  Copy-Item .env.sandbox.example .env.sandbox
  $Bytes = New-Object byte[] 48
  $Generator = [Security.Cryptography.RandomNumberGenerator]::Create()
  $Generator.GetBytes($Bytes)
  $Generator.Dispose()
  $Secret = [Convert]::ToBase64String($Bytes)
  (Get-Content .env.sandbox -Raw).Replace("replace-with-a-random-sandbox-value", $Secret) | Set-Content .env.sandbox
}

$Python = if (Test-Path .venv\Scripts\python.exe) { ".venv\Scripts\python.exe" } else { "python" }
& $Python scripts\create_sandbox_media.py sandbox-media
docker compose -p infomancer-sandbox -f compose.sandbox.yaml up -d --build
if ($Mode -eq "Sample") {
  docker compose -p infomancer-sandbox -f compose.sandbox.yaml exec -T infomancer python -m app.sandbox_seed
}

Write-Host "InfoMancer sandbox ($Mode) is ready at http://127.0.0.1:8788"
if ($Mode -eq "Sample") { Write-Host "Sign in with: sandbox / sandbox librarian password" }
