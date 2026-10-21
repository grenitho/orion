@echo off
title Orion Backend Server
echo [1/3] Masuk ke folder backend...
cd /d "%~dp0backend"

echo [2/3] Mengaktifkan Virtual Environment...
rem UPDATE-010: terima .venv maupun venv (README lama menyebut .venv,
rem skrip lama hanya mengenal venv — sekarang keduanya jalan).
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo.
    echo [ERROR] Tidak ditemukan .venv maupun venv di folder backend.
    echo Jalankan dulu:
    echo     python -m venv .venv
    echo     .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo [3/3] Menjalankan Uvicorn Server...
echo.
echo ==========================================
echo  Orion Backend berjalan di: http://127.0.0.1:8000
echo  Tekan CTRL+C untuk menghentikan server.
echo ==========================================
echo.

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
pause
