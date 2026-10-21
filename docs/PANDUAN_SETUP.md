# Panduan Setup Orion (Lengkap)

Panduan ini untuk **pengguna baru total** — tidak perlu pengalaman coding.
Ikuti salah satu rute di bawah; **Rute 2 (satu baris perintah) paling
disarankan untuk Windows**.

---

## Apa yang akan di-install?

Orion terdiri dari dua bagian:

| Bagian | Bentuk | Tugasnya |
|---|---|---|
| **Extension VS Code** | dari Marketplace (`grenitho.orion-ai`) | tampilan chat, tombol approve, activity log |
| **Backend** | folder Python di komputer Anda | otak agen: perencanaan, edit kode, verifikasi, panggilan LLM |

Keduanya berkomunikasi lewat `127.0.0.1:8000` (loopback) — **tidak ada yang
terbuka ke internet selain panggilan ke penyedia LLM yang Anda pilih sendiri**.
Kode proyek Anda dan API key Anda tetap di komputer Anda.

### Syarat

- Windows 10/11 (Rute 2), atau macOS/Linux (Rute 3)
- Koneksi internet
- VS Code 1.125 atau lebih baru
- Python 3.11+ — **Rute 2 memasang/memeriksanya otomatis**
- Minimal satu API key LLM. Paling mudah: **Gemini, gratis** (lihat bawah)

### Cara mendapatkan API key Gemini GRATIS

1. Buka <https://aistudio.google.com/apikey>
2. Login dengan akun Google
3. Klik **Create API key** → salin key-nya (diawali `AIza...`)
4. Boleh buat beberapa key dari beberapa akun Google — Orion
   **merotasinya otomatis** dan pindah ke key berikutnya saat kena limit.

> Node.js **tidak wajib**. Hanya diperlukan bila Anda memverifikasi proyek
> JavaScript/TypeScript (tool `verify_code` memakai `node --check`) atau
> membangun extension dari source.

---

## Rute 1 — Setup Wizard di dalam VS Code

> **Status: SEDANG DIKEMBANGKAN** — belum tersedia di versi 0.0.8.
> Sementara ini gunakan **Rute 2** (hasil akhirnya identik). Bagian ini
> didokumentasikan lebih dulu agar begitu wizard rilis, panduannya sudah
> siap dan tidak ada yang berubah dari sisi Anda.

Rencana alur wizard (satu panel di dalam VS Code, tanpa terminal):

1. **Deteksi Python** — bila belum ada, wizard menawarkan install otomatis
   (winget) dengan satu klik.
2. **Unduh backend** — dari GitHub, tanpa perlu `git`.
3. **Install dependensi** — venv + `pip install` dengan progress bar.
4. **Form API key** — tempel key Gemini; tombol **"+ Tambah Key"** untuk
   banyak key; pilihan provider lain (OpenRouter/OpenAI/DeepSeek/Qwen).
5. **Selesai** — wizard menulis `backend\.env`, me-restart backend, dan
   langsung membuka chat.

---

## Rute 2 — Installer satu baris (Windows, DISARANKAN)

### Langkah 1 — Jalankan installer

1. Tutup VS Code (bila terbuka).
2. Buka **PowerShell** (cari "PowerShell" di Start Menu — **tidak perlu**
   "Run as administrator").
3. Tempel perintah ini, tekan Enter:

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/grenitho/orion/main/install.ps1 | iex"
```

### Langkah 2 — Apa yang terjadi (±3–7 menit)

Skrip mengerjakan 6 tahap dan melaporkan tiap tahap:

| Tahap | Yang dilakukan |
|---|---|
| 1/6 | Cek Python 3.11+; bila tidak ada → coba install otomatis via winget |
| 2/6 | Unduh kode Orion dari GitHub ke `%USERPROFILE%\orion` (`.env` lama Anda **selalu dipertahankan**) |
| 3/6 | Buat virtual environment `backend\.venv` |
| 4/6 | `pip install -r requirements.txt` (bagian terlama) |
| 5/6 | Buat `backend\.env` lalu **meminta API key**: tempel key ke-1 → Enter, key ke-2 → Enter, dst. Enter kosong = selesai |
| 6/6 | Uji impor backend — `[OK] backend siap` berarti instalasi sehat |

### Langkah 3 — Pasang extension & sambungkan

1. Buka VS Code → panel **Extensions** (`Ctrl+Shift+X`) → cari
   **"Orion — AI Software Engineer"** (publisher `grenitho`) → **Install**.
   (Bila CLI `code` tersedia, installer mencoba melakukan ini otomatis.)
2. `Ctrl+,` (Settings) → cari **`orion.backendPath`** → isi dengan:
   `C:\Users\<NamaAnda>\orion\backend`
3. `Ctrl+Shift+P` → **Developer: Reload Window**.

### Langkah 4 — Uji

1. Buka folder proyek apa pun di VS Code.
2. Klik ikon **Orion** di activity bar (kiri) → walkthrough
   *Get Started with Orion* muncul → ikuti, atau langsung
   `Ctrl+Shift+P` → **Orion: Open Chat Panel**.
3. Kirim: `halo, jelaskan proyek ini`. Notifikasi
   *"Orion: Backend started automatically"* muncul → backend sehat.

### Masalah umum (Rute 2)

| Gejala | Solusi |
|---|---|
| `...cannot be loaded because running scripts is disabled` | Gunakan perintah lengkap dengan `-ExecutionPolicy Bypass` seperti di atas |
| `irm` / `iex` tidak dikenal | PowerShell terlalu tua — buka "Windows PowerShell" bawaan, bukan cmd |
| Python tidak ditemukan & winget gagal | Install manual dari <https://www.python.org/downloads/>, **centang "Add python.exe to PATH"**, jalankan ulang installer |
| `pip install` lama / gagal | Cek internet/proxy; jalankan ulang perintah yang sama — aman diulang, `.env` tidak ditimpa |
| `code` tidak dikenal di akhir skrip | Normal bila VS Code tidak memasang CLI-nya; install extension lewat Extensions panel saja |
| Chat: *Cannot reach the Orion backend* | `Ctrl+Shift+P` → **Orion: Start Backend**; bila *backend folder not found* → periksa isi `orion.backendPath` |
| Port 8000 sudah dipakai | `Get-NetTCPConnection -LocalPort 8000 -State Listen \| ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }` lalu Start Backend |

### Update / install ulang / hapus

- **Update**: jalankan lagi perintah Rute 2 yang sama — kode diperbarui,
  `.env` (key Anda) dipertahankan.
- **Hapus**: uninstall extension dari VS Code, lalu hapus folder
  `%USERPROFILE%\orion`. Selesai; tidak ada yang tersebar di tempat lain
  (memori per-proyek ada di folder `.orion` proyek Anda, aman dihapus).

---

## Rute 3 — Manual (semua OS, untuk yang ingin kontrol penuh)

```bash
# 1. Ambil kode (git, atau unduh ZIP dari GitHub dan ekstrak)
git clone https://github.com/grenitho/orion.git
cd orion/backend

# 2. Virtual environment — nama ".venv" maupun "venv" sama-sama dikenali
python -m venv .venv
#    Windows:  .venv\Scripts\activate
#    Unix:     source .venv/bin/activate

# 3. Dependensi
pip install -r requirements.txt

# 4. Konfigurasi
cp .env.example .env        # Windows: copy .env.example .env
#    buka .env, isi minimal GEMINI_API_KEY_1=...

# 5. Uji jalankan backend (Ctrl+C untuk berhenti)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Lalu di VS Code: install extension `grenitho.orion-ai`, isi
`orion.backendPath` dengan path folder `backend` (langkah opsional bila
workspace Anda memang berisi/berdekatan dengan folder `backend` —
auto-deteksi menemukannya sendiri), reload window.

Alternatif tanpa terminal (Windows): double-click **`start-backend.bat`**
di root repository — jendela terminal backend terbuka dan harus tetap
terbuka selama Orion dipakai.

---

## Konfigurasi LLM: dari Gemini gratis sampai model "lebih cerdas"

Semua di satu file: **`backend\.env`**. Setelah mengubah `.env`,
**backend wajib di-restart** (key dibaca sekali saat backend mulai):
`Ctrl+Shift+P` → *Orion: Start Backend* tidak merestart yang sudah hidup —
matikan dulu proses di port 8000 (lihat tabel masalah umum), atau restart
VS Code, baru Start Backend.

### Cara kerja router (3 tier)

1. **Tier 1 — Gemini**: semua `GEMINI_API_KEY_1..N` dirotasi acak per
   request; kena rate-limit → otomatis lompat ke key berikutnya.
2. **Tier 2 — endpoint kompatibel-OpenAI** (`FALLBACK_*`): OpenRouter,
   OpenAI, DeepSeek, Qwen, Groq, dsb.
3. **Tier 3 — Cloudflare AI** (opsional).

Tier bawah hanya dipanggil bila tier di atasnya gagal/kehabisan kuota.

### Banyak key Gemini (gratis, untuk pengguna awam)

```ini
GEMINI_API_KEY_1=AIza...
GEMINI_API_KEY_2=AIza...
GEMINI_API_KEY_3=AIza...
DEFAULT_MODEL=gemini/gemini-3.5-flash-lite
```

### Model lebih cerdas TANPA ubah kode (level menengah)

Orion memakai **LiteLLM**, jadi ratusan model dari provider mana pun bisa
dipakai. Dua cara:

**Cara A — OpenRouter (paling mudah): satu key untuk semua merek.**
Daftar di <https://openrouter.ai> → buat key (`sk-or-...`):

```ini
FALLBACK_API_KEY=sk-or-...
FALLBACK_BASE_URL=https://openrouter.ai/api/v1
FALLBACK_MODELS=openai/gpt-5, anthropic/claude-sonnet-4-5, deepseek/deepseek-reasoner
```

**Cara B — langsung ke provider (satu endpoint per konfigurasi):**

| Provider | `FALLBACK_BASE_URL` | Contoh `FALLBACK_MODELS` |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `gpt-5, gpt-5-mini` |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat, deepseek-reasoner` |
| Qwen (DashScope, intl) | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | `qwen3-coder-plus, qwen-max` |
| Groq | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |

```ini
FALLBACK_API_KEY=<key dari provider>
FALLBACK_BASE_URL=<url dari tabel>
FALLBACK_MODELS=deepseek-reasoner
```

Nama model tanpa prefix dianggap `openai/...` (benar untuk semua endpoint
kompatibel-OpenAI di atas). Anthropic langsung (tanpa OpenRouter) dan
**beberapa provider sekaligus dengan key terpisah** (`PROVIDER_2_*`,
`PROVIDER_3_*`, ...) sedang direncanakan sebagai update backend berikutnya.

### Ingin model non-Gemini sebagai model UTAMA?

Kosongkan/hapus semua `GEMINI_API_KEY_*` dan isi tier 2 — router hanya
mengantre tier yang punya key, sehingga model tier 2 efektif menjadi
primary. Contoh full-DeepSeek:

```ini
# GEMINI_API_KEY_1=            <- dikosongkan
FALLBACK_API_KEY=sk-...
FALLBACK_BASE_URL=https://api.deepseek.com
FALLBACK_MODELS=deepseek-reasoner
```

> Catatan jujur: prompt dan guardrail Orion diuji paling intensif dengan
> Gemini. Model lain yang tool-calling-nya kuat (Claude, GPT, DeepSeek,
> Qwen-coder) berjalan baik, tetapi model kecil/lemah dalam tool-calling
> bisa membuat agen sering gagal memanggil tools — pilih model kelas
> coding/agent.

---

## Checklist akhir

- [ ] Extension `grenitho.orion-ai` terpasang di VS Code
- [ ] `orion.backendPath` terisi (atau workspace berdekatan dengan `backend/`)
- [ ] Notifikasi *"Backend started automatically"* muncul saat membuka proyek
- [ ] Chat membalas `halo` dengan bahasa yang Anda pakai
- [ ] (Opsional) key cadangan / provider lain terisi di `backend\.env`

Masih ada masalah? Baca tabel *Troubleshooting* di
[README extension](../orion/README.md) atau buka issue di
<https://github.com/grenitho/orion/issues>.
