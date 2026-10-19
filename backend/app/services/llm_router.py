# backend/app/services/llm_router.py
import asyncio
import os
import random
import re

import litellm

# >>> FIX KRITIS: _turn_on_debug() sebelumnya AKTIF TANPA SYARAT.
# Ini membuat litellm mencetak raw request/response PENUH (termasuk seluruh
# histori messages) ke stdout untuk SETIAP panggilan acompletion() — dan
# dengan max_iterations bisa sampai ratusan per task, ini bisa membanjiri
# pipe stdout yang ditangkap proses induk (mis. ekstensi VS Code). Kalau
# proses induk tidak membaca cukup cepat, backend bisa macet menunggu
# buffer kosong (backpressure), lalu di-kill paksa oleh watchdog/health-check
# proses induk — inilah salah satu penyebab paling mungkin dari
# "terminal process terminated" yang muncul acak.
# Sekarang hanya aktif jika eksplisit di-set via .env, untuk debugging manual.
if os.getenv("LITELLM_DEBUG", "false").lower() in ("1", "true", "yes"):
    litellm._turn_on_debug()

from litellm import APIConnectionError, BadRequestError, RateLimitError, Timeout

# ==========================================
# 1. KONFIGURASI UTAMA: GEMINI (N API KEYS - ROTATION)
# ==========================================
# >>> FIX: sebelumnya hardcode PERSIS 3 baris (GEMINI_API_KEY_1/2/3) -- setiap
# kali user menambah/mengurangi jumlah key di .env, file ini WAJIB diedit
# manual lagi. Sekarang otomatis scan SEMUA env var berpola GEMINI_API_KEY_<N>
# (N = angka berapa pun, tidak harus berurutan/tidak harus mulai dari 1),
# diurutkan berdasarkan N, supaya menambah GEMINI_API_KEY_4, _5, dst di .env
# langsung terpakai tanpa perlu menyentuh kode ini lagi.
_gemini_key_pattern = re.compile(r"^GEMINI_API_KEY_(\d+)$")
_found_gemini_keys = []
for _env_name, _env_value in os.environ.items():
    _match = _gemini_key_pattern.match(_env_name)
    if _match and _env_value and _env_value.strip():
        _found_gemini_keys.append((int(_match.group(1)), _env_value.strip()))

# Urutkan berdasarkan angka index (GEMINI_API_KEY_1 duluan, lalu _2, dst) --
# urutan ini tidak memengaruhi cara pakainya (tetap di-random per request),
# hanya supaya konsisten/predictable di log.
_found_gemini_keys.sort(key=lambda pair: pair[0])
GEMINI_API_KEYS = [key for _, key in _found_gemini_keys]

print(f"[LLM_ROUTER] Terdeteksi {len(GEMINI_API_KEYS)} Gemini API key dari .env.")

PRIMARY_MODEL = os.getenv("DEFAULT_MODEL", "gemini/gemini-3.5-flash")

# ==========================================
# 2. KONFIGURASI FALLBACK 1 (OpenRouter)
# ==========================================
FALLBACK_API_KEY = os.getenv("FALLBACK_API_KEY")
FALLBACK_BASE_URL = os.getenv("FALLBACK_BASE_URL", "https://openrouter.ai/api/v1")
raw_fallback_models = os.getenv("FALLBACK_MODELS", "")
FALLBACK_MODELS = [m.strip() for m in raw_fallback_models.split(",") if m.strip()]

# ==========================================
# 3. KONFIGURASI FALLBACK 2: CLOUDFLARE
# ==========================================
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
raw_cf_models = os.getenv("CLOUDFLARE_MODELS", "")
CLOUDFLARE_MODELS = [m.strip() for m in raw_cf_models.split(",") if m.strip()]

# ==========================================
# 4. KONFIGURASI RETRY & TIMEOUT
# ==========================================
TIMEOUT_SEC = int(os.getenv("LLM_TIMEOUT", "60"))
MAX_RETRIES = max(1, int(os.getenv("LLM_MAX_RETRIES_PER_MODEL", "2")))

# >>> PATCH ORION-6b: konfigurasi baru (semua punya default aman)
# minimal | low | medium | high   -- pakai "high" untuk agent coding
REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "high").strip().lower()
if REASONING_EFFORT not in {"minimal", "low", "medium", "high"}:
    REASONING_EFFORT = "high"
MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "32768"))
KIRIM_TEMPERATURE_GEMINI = os.getenv("LLM_SEND_TEMPERATURE", "0").lower() in ("1", "true", "yes")
BACKOFF_BASE = float(os.getenv("LLM_BACKOFF_BASE", "1.5"))
BACKOFF_CAP = float(os.getenv("LLM_BACKOFF_CAP", "20.0"))

# ==========================================
# 5. TEMPERATURE PER TIER
# ==========================================
# >>> FIX KRITIS: sebelumnya temperature=1.0 di-HARDCODE untuk SEMUA model di
# SEMUA tier (Gemini, OpenRouter, Cloudflare) sekaligus. Angka 1.0 itu memang
# perlu KHUSUS untuk Gemini 3.x (supaya tidak hang/timeout — lihat komentar di
# acompletion() di bawah), TAPI model fallback tier 2/3 biasanya jauh lebih
# kecil/lemah. Dipaksa temperature setinggi itu membuat model-model kecil ini
# mengeluarkan output tidak koheren — kata ngawur, tata bahasa rusak, bahkan
# kata dari bahasa lain nyasar (persis gejala yang dilaporkan user: jawaban
# "tidak nyambung"). Sekarang tiap tier punya temperature sendiri yang lebih
# aman untuk fallback, dan tetap bisa dioverride lewat .env kalau perlu.
GEMINI_TEMPERATURE = float(os.getenv("GEMINI_TEMPERATURE", "1.0"))
FALLBACK_TEMPERATURE = float(os.getenv("FALLBACK_TEMPERATURE", "0.4"))
CLOUDFLARE_TEMPERATURE = float(os.getenv("CLOUDFLARE_TEMPERATURE", "0.4"))


async def get_ai_response(messages: list, tools: list = None) -> dict:
    """
    Hybrid Router: Gemini (3-key rotation, temp=1.0) → OpenRouter → Cloudflare
    """
    execution_queue = []

    # --- TIER 1: GEMINI (3 API KEYS - DIACAK UNTUK LOAD BALANCING) ---
    if GEMINI_API_KEYS and PRIMARY_MODEL:
        gemini_models = []
        for i, api_key in enumerate(GEMINI_API_KEYS):
            gemini_models.append(
                {
                    "model": PRIMARY_MODEL,
                    "api_key": api_key,
                    "base_url": None,
                    "provider": f"Gemini Key {i+1} (Primary)",
                    "temperature": GEMINI_TEMPERATURE,
                }
            )
        random.shuffle(gemini_models)
        execution_queue.extend(gemini_models)

    # --- TIER 2: OPENROUTER (DIACAK) ---
    tier2_models = []
    if FALLBACK_API_KEY:
        for fb_model in FALLBACK_MODELS:
            safe_model_name = fb_model
            if not any(
                fb_model.startswith(prefix)
                for prefix in [
                    "openai/",
                    "anthropic/",
                    "openrouter/",
                    "groq/",
                    "gemini/",
                ]
            ):
                safe_model_name = f"openai/{fb_model}"

            tier2_models.append(
                {
                    "model": safe_model_name,
                    "api_key": FALLBACK_API_KEY,
                    "base_url": FALLBACK_BASE_URL,
                    "provider": "OpenRouter (Fallback 1)",
                    "temperature": FALLBACK_TEMPERATURE,
                }
            )
        random.shuffle(tier2_models)
        execution_queue.extend(tier2_models)

    # --- TIER 3: CLOUDFLARE (DIACAK) ---
    tier3_models = []
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        cf_base_url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1"
        for cf_model in CLOUDFLARE_MODELS:
            tier3_models.append(
                {
                    "model": f"cloudflare/{cf_model}",
                    "api_key": CLOUDFLARE_API_TOKEN,
                    "base_url": cf_base_url,
                    "provider": "Cloudflare (Fallback 2)",
                    "temperature": CLOUDFLARE_TEMPERATURE,
                }
            )
        random.shuffle(tier3_models)
        execution_queue.extend(tier3_models)

    if not execution_queue:
        raise Exception(
            "[LLM_ROUTER] Tidak ada model yang dikonfigurasi. Cek .env Anda."
        )

    last_error = None

    for target in execution_queue:
        model_name = target["model"]
        provider_name = target["provider"]
        # >>> FIX: pakai temperature milik tier ini, bukan 1.0 untuk semua.
        target_temperature = target.get("temperature", 0.4)

        # Set konfigurasi LiteLLM secara dinamis
        if "gemini" in model_name.lower():
            os.environ["GEMINI_API_KEY"] = target["api_key"]
            litellm.api_key = target["api_key"]
            litellm.api_base = None
        else:
            litellm.api_key = target["api_key"]
            litellm.api_base = target["base_url"]

        print("[LLM_ROUTER] ========================================")
        print(f"[LLM_ROUTER] >> Mencoba: {model_name}")
        print(f"[LLM_ROUTER] >> Provider: {provider_name}")
        print(f"[LLM_ROUTER] >> Temperature: {target_temperature}")
        print("[LLM_ROUTER] ========================================")

        for attempt in range(MAX_RETRIES):
            try:
                # >>> Temperature 1.0 WAJIB khusus untuk Gemini 3.x agar tidak
                # hang/timeout. Model fallback (OpenRouter/Cloudflare) pakai
                # temperature lebih rendah (default 0.4) karena model-model
                # itu umumnya lebih kecil dan gampang mengeluarkan output
                # tidak koheren kalau temperature-nya dipaksa tinggi. <<<
                # >>> PATCH ORION-6 (KRITIS) ==============================
                # `reasoning_effort` adalah satu-satunya cara mengirim
                # `thinking_level` ke Gemini 3+ lewat litellm. Dokumentasi litellm:
                #   "If you omit reasoning_effort, LiteLLM does NOT send a default
                #    thinking_level, so the request uses the Gemini API defaults."
                # Dan default API untuk gemini-3.5-flash-lite adalah MINIMAL, yang
                # menurut dokumentasi Google menyebabkan
                #   "premature tool termination on multi-step tasks"
                # -- persis keluhan "hanya mengerjakan tugas pertama".
                #
                # Hanya dikirim untuk model Gemini; provider lain bisa menolak.
                # =================================================================
                extra_kwargs = {}
                if "gemini" in model_name.lower():
                    extra_kwargs["reasoning_effort"] = REASONING_EFFORT
                    # gemini-3.5-flash-lite MENGABAIKAN nilai temperature kustom,
                    # jadi tidak dikirim kecuali diminta eksplisit lewat .env
                    if not KIRIM_TEMPERATURE_GEMINI:
                        target_temperature = None

                call_kwargs = {
                    "model": model_name,
                    "messages": messages,
                    "tools": tools,
                    "timeout": TIMEOUT_SEC,
                    "max_tokens": MAX_OUTPUT_TOKENS,
                    **extra_kwargs,
                }
                if target_temperature is not None:
                    call_kwargs["temperature"] = target_temperature

                response = await litellm.acompletion(**call_kwargs)

                print(
                    f"[LLM_ROUTER] [SUCCESS] Berhasil menggunakan: {model_name} ({provider_name})"
                )
                return response

            except RateLimitError as e:
                print(
                    f"[LLM_ROUTER] [WARNING] {model_name} RATE LIMIT terdeteksi! Mencoba key/model lain..."
                )
                last_error = e
                break

            except BadRequestError as e:
                err_str = str(e).lower()
                if (
                    "failed to call a function" in err_str
                    or "invalid schema" in err_str
                ):
                    print(
                        f"[LLM_ROUTER] [CRITICAL] {model_name} menolak schema tools. Berhenti mencoba."
                    )
                    last_error = e
                    break
                else:
                    print(
                        f"[LLM_ROUTER] [WARNING] {model_name} menolak request (400): {str(e)[:100]}"
                    )
                    last_error = e
                    break

            except (Timeout, APIConnectionError) as e:
                last_error = e
                # >>> FIX: sebelumnya selalu `break` walau MAX_RETRIES > 1,
                # jadi retry per-model tidak pernah benar-benar terjadi.
                # Error transient (timeout/koneksi) layak dicoba ulang di
                # MODEL YANG SAMA dulu sebelum menyerah dan pindah provider.
                if attempt < MAX_RETRIES - 1:
                    # >>> PATCH ORION-7: sebelumnya `continue` langsung (retry instan).
                    # Untuk timeout/429, menembak ulang seketika hampir pasti gagal lagi
                    # dan cuma membakar kuota free tier. Sekarang ada jeda + jitter.
                    _jeda = min(BACKOFF_BASE ** (attempt + 1), BACKOFF_CAP) * random.uniform(0.7, 1.3)
                    print(
                        f"[LLM_ROUTER] [WARNING] {model_name} TIMEOUT/KONEKSI GAGAL! "
                        f"Retry percobaan {attempt + 2}/{MAX_RETRIES} dalam {_jeda:.1f} dtk..."
                    )
                    await asyncio.sleep(_jeda)
                    continue
                print(
                    f"[LLM_ROUTER] [WARNING] {model_name} TIMEOUT/KONEKSI GAGAL setelah {MAX_RETRIES}x percobaan. Pindah model lain."
                )
                break

            except Exception as e:
                # TAMBAHKAN INI: Cetak error mentah dari LiteLLM
                print(f"[LLM_ROUTER] [DEBUG] FULL ERROR: {e!r}")
                err_str = str(e).lower()
                if (
                    "connection" in err_str
                    or "timeout" in err_str
                    or "disconnect" in err_str
                    or "incomplete" in err_str
                ):
                    last_error = e
                    if attempt < MAX_RETRIES - 1:
                        print(
                            f"[LLM_ROUTER] [WARNING] {model_name} koneksi tidak stabil. "
                            f"Retry percobaan {attempt + 2}/{MAX_RETRIES} pada model yang sama..."
                        )
                        continue
                    print(
                        f"[LLM_ROUTER] [WARNING] {model_name} koneksi tidak stabil setelah {MAX_RETRIES}x. Pindah model lain."
                    )
                    break
                else:
                    print(
                        f"[LLM_ROUTER] [WARNING] {model_name} error: {str(e)[:100]}. Mencoba berikutnya..."
                    )
                    last_error = e
                    break

    print("[LLM_ROUTER] [CRITICAL] Semua model AI gagal merespons.")
    raise Exception(
        f"Semua model AI sedang sibuk/gagal. Error terakhir: {last_error!s}"
    )
