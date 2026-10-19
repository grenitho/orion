@echo off
title Orion Backend Server
echo [1/3] Masuk ke folder backend...
cd /d "%~dp0backend"

echo [2/3] Mengaktifkan Virtual Environment...
call venv\Scripts\activate

echo [3/3] Menjalankan Uvicorn Server...
echo.
echo ==========================================
echo  Orion Backend berjalan di: http://127.0.0.1:8000
echo  Tekan CTRL+C untuk menghentikan server.
echo ==========================================
echo.

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
pause