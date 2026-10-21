# ============================================================================
#  ORION — Installer Satu Baris (Rute 2) / One-line installer
#
#  Apa yang dilakukan skrip ini (semua di folder %USERPROFILE%\orion):
#    1. Cek Python 3.11+ (bila tidak ada, coba install lewat winget)
#    2. Unduh kode Orion dari GitHub (tanpa perlu git)
#    3. Buat virtual environment .venv di folder backend
#    4. Install semua dependensi Python (requirements.txt)
#    5. Buat backend\.env dan minta API key Gemini GRATIS (bisa lebih dari
#       satu — Orion merotasinya otomatis). .env lama TIDAK pernah ditimpa.
#    6. Uji impor backend, lalu tampilkan langkah terakhir di VS Code.
#
#  Cara pakai (PowerShell biasa, TANPA admin):
#    powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/grenitho/orion/main/install.ps1 | iex"
#
#  Atau unduh skrip ini lalu jalankan dengan parameter:
#    powershell -ExecutionPolicy Bypass -File install.ps1 -InstallDir D:\orion
#
#  Panduan lengkap: docs/PANDUAN_SETUP.md di repository ini.
# ============================================================================

param(
    [string]$InstallDir = (Join-Path $env:USERPROFILE 'orion'),
    [string]$RepoZipUrl = 'https://codeload.github.com/grenitho/orion/zip/refs/heads/main'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest jauh lebih cepat di PS 5.1
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

function Write-Step([string]$msg) { Write-Host ''; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Note([string]$msg) { Write-Host "    [!] $msg" -ForegroundColor Yellow }

# Cari interpreter Python >= 3.11; kembalikan command string atau $null.
function Find-Python {
    foreach ($candidate in @('py -3', 'python')) {
        try {
            $out = Invoke-Expression "& $candidate --version 2>&1"
            if ("$out" -match 'Python (\d+)\.(\d+)') {
                $major = [int]$Matches[1]; $minor = [int]$Matches[2]
                if ($major -eq 3 -and $minor -ge 11) {
                    Write-Ok "$candidate = Python $major.$minor"
                    return $candidate
                }
                Write-Note "$candidate = Python $major.$minor (butuh 3.11+, dilewati)"
            }
        } catch { }
    }
    return $null
}

Write-Host '=============================================='
Write-Host '  ORION AI — Installer Otomatis (Windows)'
Write-Host '=============================================='

# --- 1/6 Python -------------------------------------------------------------
Write-Step '1/6  Memeriksa Python 3.11+ ...'
$pythonCmd = Find-Python
if (-not $pythonCmd) {
    Write-Note 'Python 3.11+ belum ada. Mencoba install lewat winget (Python.Python.3.12)...'
    try {
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        # Segarkan PATH untuk sesi ini tanpa perlu membuka PowerShell baru
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                    [Environment]::GetEnvironmentVariable('Path', 'User')
        $pythonCmd = Find-Python
    } catch {
        Write-Note "winget gagal: $($_.Exception.Message)"
    }
}
if (-not $pythonCmd) {
    Write-Host ''
    Write-Host 'GAGAL: Python 3.11+ tidak ditemukan dan winget tidak tersedia.' -ForegroundColor Red
    Write-Host 'Install manual dari https://www.python.org/downloads/ — CENTANG'
    Write-Host '"Add python.exe to PATH" saat instalasi — lalu jalankan ulang skrip ini.'
    exit 1
}

# --- 2/6 Unduh kode Orion ---------------------------------------------------
Write-Step '2/6  Mengunduh Orion dari GitHub ...'
$zipPath     = Join-Path $env:TEMP 'orion-installer.zip'
$extractPath = Join-Path $env:TEMP ('orion-extract-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
Invoke-WebRequest -Uri $RepoZipUrl -OutFile $zipPath -UseBasicParsing
Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force
$inner = Get-ChildItem -Path $extractPath -Directory | Select-Object -First 1
if (-not $inner) { throw 'Hasil unduhan tidak berisi folder repository.' }

# Selamatkan .env lama (berisi API key pengguna) sebelum menimpa kode
$backendDir = Join-Path $InstallDir 'backend'
$envFile    = Join-Path $backendDir '.env'
$savedEnv   = $null
if (Test-Path $envFile) {
    $savedEnv = Get-Content $envFile -Raw
    Write-Ok '.env lama ditemukan — API key Anda akan dipertahankan'
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Path (Join-Path $inner.FullName '*') -Destination $InstallDir -Recurse -Force
Remove-Item -Recurse -Force $extractPath -ErrorAction SilentlyContinue
Remove-Item -Force $zipPath -ErrorAction SilentlyContinue
Write-Ok "Kode Orion terpasang di: $InstallDir"

Push-Location $backendDir
try {
    # --- 3/6 Virtual environment --------------------------------------------
    Write-Step '3/6  Membuat virtual environment (.venv) ...'
    if (Test-Path (Join-Path $backendDir '.venv\Scripts\python.exe')) {
        Write-Ok '.venv sudah ada — dipakai ulang'
    } else {
        Invoke-Expression "& $pythonCmd -m venv .venv"
        if (-not (Test-Path (Join-Path $backendDir '.venv\Scripts\python.exe'))) {
            throw 'Pembuatan .venv gagal.'
        }
        Write-Ok '.venv dibuat'
    }
    $venvPython = Join-Path $backendDir '.venv\Scripts\python.exe'

    # --- 4/6 Dependensi -------------------------------------------------------
    Write-Step '4/6  Install dependensi Python (2-5 menit, hanya sekali) ...'
    & $venvPython -m pip install --upgrade pip --quiet --disable-pip-version-check
    & $venvPython -m pip install -r requirements.txt --quiet --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw 'pip install -r requirements.txt gagal (cek koneksi internet).' }
    Write-Ok 'Semua dependensi terpasang'

    # --- 5/6 .env + API key ----------------------------------------------------
    Write-Step '5/6  Konfigurasi API key (backend\.env) ...'
    if ($savedEnv) {
        Set-Content -Path $envFile -Value $savedEnv -NoNewline
        Write-Ok '.env lama dipulihkan (tidak ditimpa)'
    } elseif (-not (Test-Path $envFile)) {
        Copy-Item (Join-Path $backendDir '.env.example') $envFile
        Write-Host ''
        Write-Host '    Ambil API key Gemini GRATIS (boleh beberapa akun):'
        Write-Host '      https://aistudio.google.com/apikey' -ForegroundColor White
        Write-Host '    Tempel key ke-1, Enter; key ke-2, Enter; dst.'
        Write-Host '    Tekan ENTER kosong untuk selesai / lewati.'
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
            Write-Ok "$($keys.Count) API key tersimpan di backend\.env"
        } else {
            Write-Note 'Tidak ada key dimasukkan. Isi nanti: backend\.env -> GEMINI_API_KEY_1=...'
        }
    } else {
        Write-Ok '.env sudah ada — dibiarkan'
    }

    # --- 6/6 Uji asap -----------------------------------------------------------
    Write-Step '6/6  Uji impor backend ...'
    & $venvPython -c "import app.main; print('    [OK] backend siap')"
    if ($LASTEXITCODE -ne 0) { throw 'Uji impor backend gagal — lihat pesan error di atas.' }
}
finally {
    Pop-Location
}

# --- Langkah terakhir --------------------------------------------------------
Write-Host ''
Write-Host '==============================================' -ForegroundColor Green
Write-Host '  INSTALASI SELESAI!' -ForegroundColor Green
Write-Host '==============================================' -ForegroundColor Green
Write-Host ''
Write-Host 'Langkah terakhir (sekali saja):'
Write-Host ''
Write-Host '  1. Install extension VS Code "Orion - AI Software Engineer"'
Write-Host '     (publisher: grenitho) dari Extensions panel, atau jalankan:'
Write-Host '         code --install-extension grenitho.orion-ai'
Write-Host ''
Write-Host '  2. Beri tahu VS Code lokasi backend:'
Write-Host '     Ctrl+, -> cari "orion.backendPath" -> isi dengan:'
Write-Host "         $backendDir" -ForegroundColor White
Write-Host ''
Write-Host '  3. Reload VS Code (Ctrl+Shift+P -> "Developer: Reload Window").'
Write-Host '     Klik ikon Orion di activity bar -> ikuti walkthrough'
Write-Host '     "Get Started with Orion" -> kirim tugas pertama Anda!'
Write-Host ''
Write-Host 'Panduan lengkap + solusi masalah umum:'
Write-Host '  https://github.com/grenitho/orion/blob/main/docs/PANDUAN_SETUP.md'
Write-Host ''

# Bonus: coba install extension otomatis bila CLI `code` tersedia
try {
    $null = Get-Command code -ErrorAction Stop
    Write-Step 'Bonus: mencoba install extension lewat CLI "code" ...'
    & code --install-extension grenitho.orion-ai
    if ($LASTEXITCODE -eq 0) { Write-Ok 'Extension terpasang' } else { Write-Note 'Install otomatis gagal — pasang manual dari Extensions panel' }
} catch {
    Write-Note 'CLI "code" tidak ada di PATH — install extension lewat Extensions panel VS Code'
}
