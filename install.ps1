# ============================================================================
#  ORION — one-line installer (Route 2)
#
#  What this script does (everything under %USERPROFILE%\orion):
#    1. Check Python 3.11+ (if missing, try winget)
#    2. Download the Orion code from GitHub (no git required)
#    3. Create the backend virtual environment (.venv)
#    4. Install all Python dependencies (requirements.txt)
#    5. Create backend\.env and ask for FREE Gemini API keys (several are
#       supported — Orion rotates them). An existing .env is NEVER
#       overwritten. Skipping Gemini is supported: the script then offers
#       one OpenAI-compatible provider group (UPDATE-020), and both can
#       still be added later in the Setup Wizard.
#    6. Smoke-test the backend import, then print the final VS Code steps.
#
#  Usage (plain PowerShell, NO admin):
#    powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/grenitho/orion/main/install.ps1 | iex"
#
#  Or download this script and run it with parameters:
#    powershell -ExecutionPolicy Bypass -File install.ps1 -InstallDir D:\orion
#
#  Full guide (Indonesian): docs/PANDUAN_SETUP.md in this repository.
#  User-facing output is English (repo language rule, Phase D); the
#  Indonesian walkthrough lives in docs/PANDUAN_SETUP.md.
# ============================================================================

param(
    [string]$InstallDir = (Join-Path $env:USERPROFILE 'orion'),
    [string]$RepoZipUrl = 'https://codeload.github.com/grenitho/orion/zip/refs/heads/main'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is much faster on PS 5.1
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

function Write-Step([string]$msg) { Write-Host ''; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Note([string]$msg) { Write-Host "    [!] $msg" -ForegroundColor Yellow }

# Find a Python interpreter >= 3.11; return the command string or $null.
function Find-Python {
    foreach ($candidate in @('py -3', 'python')) {
        try {
            $out = Invoke-Expression "& $candidate --version 2>&1"
            if ("$out" -match 'Python (\d+)\.(\d+)') {
                $major = [int]$Matches[1]; $minor = [int]$Matches[2]
                if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) { return $candidate }
            }
        } catch { }
    }
    return $null
}

Write-Host '=============================================='
Write-Host '  ORION AI — automatic installer (Windows)'
Write-Host '=============================================='

# --- 1/6 Python -------------------------------------------------------------
Write-Step '1/6  Checking Python 3.11+ ...'
$pythonCmd = Find-Python
if (-not $pythonCmd) {
    Write-Note 'Python 3.11+ not found. Trying winget (Python.Python.3.12)...'
    try {
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        # Refresh PATH for this session without reopening PowerShell
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                    [Environment]::GetEnvironmentVariable('Path', 'User')
        $pythonCmd = Find-Python
    } catch {
        Write-Note "winget failed: $($_.Exception.Message)"
    }
}
if (-not $pythonCmd) {
    Write-Host ''
    Write-Host 'FAILED: Python 3.11+ not found and winget is unavailable.' -ForegroundColor Red
    Write-Host 'Install it manually from https://www.python.org/downloads/ — tick'
    Write-Host '"Add python.exe to PATH" during setup — then re-run this script.'
    exit 1
}

# --- 2/6 Download the Orion code ---------------------------------------------
Write-Step '2/6  Downloading Orion from GitHub ...'
$zipPath     = Join-Path $env:TEMP 'orion-installer.zip'
$extractPath = Join-Path $env:TEMP ('orion-extract-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
Invoke-WebRequest -Uri $RepoZipUrl -OutFile $zipPath -UseBasicParsing
Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force
$inner = Get-ChildItem -Path $extractPath -Directory | Select-Object -First 1
if (-not $inner) { throw 'The download does not contain a repository folder.' }

# Rescue the old .env (it holds the user's API keys) before overwriting code
$backendDir = Join-Path $InstallDir 'backend'
$envFile    = Join-Path $backendDir '.env'
$savedEnv   = $null
if (Test-Path $envFile) {
    $savedEnv = Get-Content $envFile -Raw
    Write-Ok 'Existing .env found — your API keys will be preserved'
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Path (Join-Path $inner.FullName '*') -Destination $InstallDir -Recurse -Force
Remove-Item -Recurse -Force $extractPath -ErrorAction SilentlyContinue
Remove-Item -Force $zipPath -ErrorAction SilentlyContinue
Write-Ok "Orion code installed at: $InstallDir"

Push-Location $backendDir
try {
    # --- 3/6 Virtual environment --------------------------------------------
    Write-Step '3/6  Creating the virtual environment (.venv) ...'
    if (Test-Path (Join-Path $backendDir '.venv\Scripts\python.exe')) {
        Write-Ok '.venv already exists — reusing it'
    } else {
        Invoke-Expression "& $pythonCmd -m venv .venv"
        if (-not (Test-Path (Join-Path $backendDir '.venv\Scripts\python.exe'))) {
            throw 'Creating the .venv failed.'
        }
        Write-Ok '.venv created'
    }
    $venvPython = Join-Path $backendDir '.venv\Scripts\python.exe'

    # --- 4/6 Dependencies -----------------------------------------------------
    Write-Step '4/6  Installing Python dependencies (2-5 minutes, once) ...'
    & $venvPython -m pip install --upgrade pip --quiet --disable-pip-version-check
    & $venvPython -m pip install -r requirements.txt --quiet --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw 'pip install -r requirements.txt failed (check your internet connection).' }
    Write-Ok 'All dependencies installed'

    # --- 5/6 .env + API keys ----------------------------------------------------
    Write-Step '5/6  Configuring API keys (backend\.env) ...'
    if ($savedEnv) {
        Set-Content -Path $envFile -Value $savedEnv -NoNewline
        Write-Ok 'Existing .env restored (not overwritten)'
    } elseif (-not (Test-Path $envFile)) {
        Copy-Item (Join-Path $backendDir '.env.example') $envFile
        Write-Host ''
        Write-Host '    Get FREE Gemini API keys (several accounts are fine):'
        Write-Host '      https://aistudio.google.com/apikey' -ForegroundColor White
        Write-Host '    Paste key 1, Enter; key 2, Enter; and so on.'
        Write-Host '    Press EMPTY ENTER to finish / skip Gemini.'
        $keys = @()
        for ($i = 1; $i -le 10; $i++) {
            $k = Read-Host "    Gemini API key #$i"
            if ([string]::IsNullOrWhiteSpace($k)) { break }
            $keys += $k.Trim()
        }
        if ($keys.Count -gt 0) {
            $lines = @(Get-Content $envFile)
            $out = foreach ($line in $lines) {
                if ($line -match '^\s*#?\s*GEMINI_API_KEY_1\s*=') { "GEMINI_API_KEY_1=$($keys[0])" }
                else { $line }
            }
            for ($i = 2; $i -le $keys.Count; $i++) { $out += "GEMINI_API_KEY_$i=$($keys[$i - 1])" }
            Set-Content -Path $envFile -Value $out
            Write-Ok "$($keys.Count) API key(s) saved to backend\.env"
        } else {
            # >>> UPDATE-020: skipping Gemini is a FIRST-CLASS path now.
            # Offer one OpenAI-compatible provider group right here so a
            # provider-only install can chat on day one; both questions
            # accept EMPTY ENTER and everything can still be added later
            # in the Setup Wizard (step 4).
            Write-Note 'No Gemini key entered — skipping Gemini is fine.'
            Write-Host '    Optional: add ONE OpenAI-compatible provider now'
            Write-Host '    (DeepSeek, OpenRouter, Qwen, Groq, ...). Press ENTER'
            Write-Host '    on the first question to skip providers too.'
            $pName = Read-Host '    Provider name (empty = skip)'
            if (-not [string]::IsNullOrWhiteSpace($pName)) {
                $pKey    = Read-Host '    API key'
                $pBase   = Read-Host '    Base URL (empty = https://openrouter.ai/api/v1)'
                $pModels = Read-Host '    Models, comma-separated'
                if (-not [string]::IsNullOrWhiteSpace($pKey) -and -not [string]::IsNullOrWhiteSpace($pModels)) {
                    if ([string]::IsNullOrWhiteSpace($pBase)) { $pBase = 'https://openrouter.ai/api/v1' }
                    Add-Content -Path $envFile -Value "PROVIDER_1_NAME=$($pName.Trim())"
                    Add-Content -Path $envFile -Value "PROVIDER_1_API_KEY=$($pKey.Trim())"
                    Add-Content -Path $envFile -Value "PROVIDER_1_BASE_URL=$($pBase.Trim())"
                    Add-Content -Path $envFile -Value "PROVIDER_1_MODELS=$($pModels.Trim())"
                    Write-Ok "Provider '$($pName.Trim())' saved to backend\.env"
                } else {
                    Write-Note 'Provider skipped (both an API key and a model list are required).'
                }
            }
            Write-Note 'No credentials yet? The backend needs at least one group (Gemini or a provider) before the first chat — Setup Wizard step 4 adds them later.'
        }
    } else {
        Write-Ok '.env already exists — left untouched'
    }

    # --- 6/6 Smoke test -----------------------------------------------------------
    Write-Step '6/6  Backend import smoke test ...'
    & $venvPython -c "import app.main; print('    [OK] backend ready')"
    if ($LASTEXITCODE -ne 0) { throw 'Backend import smoke test failed — see the error above.' }
}
finally {
    Pop-Location
}

# --- Final steps -------------------------------------------------------------
Write-Host ''
Write-Host '==============================================' -ForegroundColor Green
Write-Host '  INSTALLATION COMPLETE!' -ForegroundColor Green
Write-Host '==============================================' -ForegroundColor Green
Write-Host ''
Write-Host 'Final steps (one-time):'
Write-Host ''
Write-Host '  1. Install the VS Code extension "Orion - AI Software Engineer"'
Write-Host '     (publisher: grenitho) from the Extensions panel, or run:'
Write-Host '         code --install-extension grenitho.orion-ai'
Write-Host ''
Write-Host '  2. Tell VS Code where the backend lives:'
Write-Host '     Ctrl+, -> search "orion.backendPath" -> set it to:'
Write-Host "         $backendDir" -ForegroundColor White
Write-Host ''
Write-Host '  3. Reload VS Code (Ctrl+Shift+P -> "Developer: Reload Window").'
Write-Host '     Click the Orion icon in the activity bar -> follow the'
Write-Host '     "Get Started with Orion" walkthrough -> send your first task!'
Write-Host ''
Write-Host 'Full guide + troubleshooting:'
Write-Host '  https://github.com/grenitho/orion/blob/main/docs/PANDUAN_SETUP.md'
Write-Host ''

# Bonus: try installing the extension automatically when the `code` CLI exists
try {
    $null = Get-Command code -ErrorAction Stop
    Write-Step 'Bonus: trying to install the extension via the "code" CLI ...'
    & code --install-extension grenitho.orion-ai
    if ($LASTEXITCODE -eq 0) { Write-Ok 'Extension installed' } else { Write-Note 'Automatic install failed — install it manually from the Extensions panel' }
} catch {
    Write-Note 'The "code" CLI is not on PATH — install the extension from the VS Code Extensions panel'
}
