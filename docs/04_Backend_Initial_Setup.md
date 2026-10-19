# Project Orion - Backend Initial Setup (v0.1)

## 1. Struktur Folder Backend
- `backend/venv/`: Virtual environment Python
- `backend/app/`: Package utama FastAPI
  - `__init__.py`: Inisialisasi package
  - `main.py`: Entry point server & endpoint dasar
- `backend/.env`: Environment variables (API Keys, Model config)
- `backend/requirements.txt`: Dependensi Python

## 2. Dependensi Utama
- FastAPI 0.110.0: Web framework
- Uvicorn 0.29.0: ASGI server
- LiteLLM 1.35.0: Gateway untuk multi-model AI
- python-dotenv 1.0.1: Load environment variables
- pydantic 2.6.4: Data validation

## 3. Endpoint Dasar
- `GET /`: Health check server
- `POST /test-ai`: Test koneksi ke AI provider

## 4. Cara Menjalankan
1. Aktifkan venv: `.\venv\Scripts\activate`
2. Install deps: `pip install -r requirements.txt`
3. Jalankan server: `uvicorn app.main:app --reload --port 8000`
4. Akses docs: http://127.0.0.1:8000/docs