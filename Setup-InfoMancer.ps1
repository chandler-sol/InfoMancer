$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Stop-Setup([string]$Message) {
    Write-Host ""
    Write-Host "InfoMancer Server setup stopped: $Message" -ForegroundColor Red
    exit 1
}

function Invoke-DockerCompose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker compose -f compose.yaml -f compose.media.yaml @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose command failed."
    }
}

function Set-EnvValue([string]$Key, [string]$Value) {
    $lines = Get-Content -LiteralPath '.env'
    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -match "^$([regex]::Escape($Key))=") {
            $found = $true
            "$Key=$Value"
        } else {
            $line
        }
    }
    if (-not $found) { $updated += "$Key=$Value" }
    Set-Content -LiteralPath '.env' -Value $updated -Encoding UTF8
}

function Normalize-MediaPath([string]$PathValue) {
    $value = $PathValue.Trim().Trim('"')
    return $value.Replace('\', '/')
}

function Read-MediaPath([string]$Label) {
    while ($true) {
        $value = Normalize-MediaPath (Read-Host "$Label folder (leave blank if you do not have one)")
        if ([string]::IsNullOrWhiteSpace($value)) { return '' }
        if (Test-Path -LiteralPath $value -PathType Container) { return $value }
        $answer = Read-Host "That folder was not found: $value`nUse it anyway? [y/N]"
        if ($answer -match '^(y|yes)$') { return $value }
    }
}

function Quote-Yaml([string]$Value) {
    return "'" + $Value.Replace("'", "''") + "'"
}

Write-Host ""
Write-Host "InfoMancer Server Setup" -ForegroundColor Cyan
Write-Host "=======================" -ForegroundColor Cyan
Write-Host "This helper creates the local config files, connects your media folders,"
Write-Host "starts InfoMancer, and prints the address and one-time setup code."
Write-Host ""

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-Setup 'Docker was not found. Install Docker Desktop first, then run Setup-InfoMancer.cmd again.'
}

& docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Stop-Setup 'Docker Compose was not found. Install or update Docker Desktop, then try again.'
}

& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Stop-Setup 'Docker is installed but is not running. Start Docker Desktop, then try again.'
}

if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host 'Created .env'
} else {
    Write-Host 'Keeping existing .env'
}

if (-not (Test-Path -LiteralPath 'data')) {
    New-Item -ItemType Directory -Path 'data' | Out-Null
}

$reuseMedia = $false
if (Test-Path -LiteralPath 'compose.media.yaml') {
    $answer = Read-Host 'An existing compose.media.yaml was found. Keep it? [Y/n]'
    $reuseMedia = -not ($answer -match '^(n|no)$')
}

if (-not $reuseMedia) {
    do {
        $movies = Read-MediaPath 'Movies'
        $tv = Read-MediaPath 'TV Shows'
        if ([string]::IsNullOrWhiteSpace($movies) -and [string]::IsNullOrWhiteSpace($tv)) {
            Write-Host 'Enter at least one Movies or TV Shows folder. You can add more folders later.' -ForegroundColor Yellow
        }
    } while ([string]::IsNullOrWhiteSpace($movies) -and [string]::IsNullOrWhiteSpace($tv))

    $lines = @(
        'services:'
        '  infomancer:'
        '    volumes:'
    )
    if (-not [string]::IsNullOrWhiteSpace($movies)) {
        $lines += '      - type: bind'
        $lines += "        source: $(Quote-Yaml $movies)"
        $lines += '        target: /media/movies'
    }
    if (-not [string]::IsNullOrWhiteSpace($tv)) {
        $lines += '      - type: bind'
        $lines += "        source: $(Quote-Yaml $tv)"
        $lines += '        target: /media/tv'
    }
    Set-Content -LiteralPath 'compose.media.yaml' -Value $lines -Encoding UTF8
    Write-Host 'Created compose.media.yaml'
}

Write-Host ""
$answer = Read-Host 'Start InfoMancer Server now? [Y/n]'
if ($answer -match '^(n|no)$') {
    Write-Host 'Setup files are ready. Start later with:'
    Write-Host 'docker compose -f compose.yaml -f compose.media.yaml up -d --build'
    exit 0
}

Write-Host ""
Write-Host 'Building and starting InfoMancer. The first build can take a few minutes...'
try {
    Invoke-DockerCompose up -d --build
} catch {
    Stop-Setup 'Docker could not build/start InfoMancer. See START-HERE.txt for the troubleshooting command.'
}

$containerId = @(& docker compose -f compose.yaml -f compose.media.yaml ps -q infomancer)[0]
if ([string]::IsNullOrWhiteSpace($containerId)) {
    Stop-Setup 'The InfoMancer container did not start.'
}
$containerId = $containerId.Trim()

Write-Host 'Waiting for InfoMancer to become ready...'
$health = ''
for ($i = 0; $i -lt 60; $i++) {
    $health = (& docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $containerId 2>$null).Trim()
    if ($health -eq 'healthy') { break }
    if ($health -in @('unhealthy', 'exited', 'dead')) {
        & docker compose -f compose.yaml -f compose.media.yaml logs --tail=80 infomancer
        Stop-Setup 'InfoMancer stopped before it became ready.'
    }
    Start-Sleep -Seconds 2
}

if ($health -ne 'healthy') {
    Stop-Setup 'InfoMancer did not become healthy within two minutes. Run: docker compose -f compose.yaml -f compose.media.yaml logs --tail=200 infomancer'
}

# Visiting /setup causes a brand-new Server to create its protected one-time setup token.
& docker compose -f compose.yaml -f compose.media.yaml exec -T infomancer python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8787/setup", timeout=5).read()' *> $null
Start-Sleep -Seconds 1

$token = ''
$tokenPath = Join-Path $PSScriptRoot 'data\bootstrap-token'
if (Test-Path -LiteralPath $tokenPath) {
    $token = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
}

$lanIp = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
        $_.IPAddress -ne '127.0.0.1' -and
        $_.IPAddress -notlike '169.254.*' -and
        $_.AddressState -eq 'Preferred'
    } |
    Select-Object -ExpandProperty IPAddress -First 1

Write-Host ""
Write-Host 'InfoMancer Server is ready.' -ForegroundColor Green
Write-Host ""
Write-Host 'On this computer:'
Write-Host '  http://127.0.0.1:8787'
if ($lanIp) {
    Write-Host ""
    Write-Host 'From another computer on this network:'
    Write-Host "  http://$lanIp`:8787"
}

if (-not [string]::IsNullOrWhiteSpace($token)) {
    Write-Host ""
    Write-Host 'One-time setup code:' -ForegroundColor Cyan
    Write-Host "  $token" -ForegroundColor White
    Write-Host ""
    Write-Host 'Copy that code into the first Librarian setup screen.'
} else {
    Write-Host ""
    Write-Host 'No one-time setup code was found. If you already created a Librarian account, that is expected.' -ForegroundColor Yellow
}

Write-Host ""
Write-Host 'Do not port-forward port 8787 to the public Internet.' -ForegroundColor Yellow
Write-Host 'Setup complete.' -ForegroundColor Green
