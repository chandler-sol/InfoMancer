param(
    [string]$Url = "http://127.0.0.1:8787",
    [string]$Username = "",
    [string]$Output = "showcase/screenshots",
    [string]$Variants = "desktop,social,mobile",
    [string]$Only = "",
    [switch]$Headed
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$toolDir = Join-Path $repoRoot "tools/showcase"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js is required for showcase screenshots. Install Node.js, then run this script again."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm is required for showcase screenshots. Install Node.js with npm, then run this script again."
}

$env:INFOMANCER_SHOWCASE_URL = $Url
$env:INFOMANCER_SHOWCASE_OUTPUT = $Output
$env:INFOMANCER_SHOWCASE_VARIANTS = $Variants
$env:INFOMANCER_SHOWCASE_HEADLESS = if ($Headed) { "0" } else { "1" }
if ($Only) { $env:INFOMANCER_SHOWCASE_ONLY = $Only }

$passwordWasSet = $false
if ($Username) {
    $env:INFOMANCER_SHOWCASE_USERNAME = $Username
    if (-not $env:INFOMANCER_SHOWCASE_PASSWORD) {
        $securePassword = Read-Host "InfoMancer password for $Username" -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
        try {
            $env:INFOMANCER_SHOWCASE_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
            $passwordWasSet = $true
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
    }
}

Push-Location $toolDir
try {
    if (-not (Test-Path "node_modules/playwright")) {
        Write-Host "Installing screenshot tooling..."
        npm install
    }
    Write-Host "Ensuring Chromium for Playwright is installed..."
    npx playwright install chromium
    npm run capture
}
finally {
    Pop-Location
    if ($passwordWasSet) { Remove-Item Env:INFOMANCER_SHOWCASE_PASSWORD -ErrorAction SilentlyContinue }
}
