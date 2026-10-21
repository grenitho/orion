# Panduan Setup Orion (Lengkap)

Panduan ini untuk **pengguna baru total** — tidak perlu pengalaman coding.
Ikuti salah satu rute di bawah; **Rute 1 (Setup Wizard di dalam VS Code,
extension 0.0.9+) paling mudah** — Rute 2 (satu baris PowerShell) hasilnya
identik dan tetap disarankan bila Anda lebih suka terminal.

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

> **Tersedia sejak extension versi 0.0.9** (perintah baru; hasil akhirnya
> identik dengan Rute 2). Butuh extension Orion terpasang lebih dulu —
> bila backend belum ada, dialog error Orion juga menawarkan tombol
> **Run Setup Wizard**.

Cara membuka: `Ctrl+Shift+P` → **"Orion: Setup Wizard (Install Backend)"**,
atau lewat walkthrough "Get Started with Orion" langkah 1 →
**Run Setup Wizard**.

Alur wizard (satu panel di dalam VS Code, tanpa terminal):

1. **Deteksi Python** — badge menunjukkan Python yang ditemukan (syarat
   3.11+). Bila belum ada: tombol **Install Python automatically (winget)**
   di Windows; di macOS/Linux install dulu dari
   <https://www.python.org/downloads/> lalu klik **Re-check**.
2. **Unduh backend** — pilih folder instalasi (default: `<home>\orion`),
   klik **Download & install**. Kode diunduh dari GitHub (zip, tanpa
   `git`), `.env` lama **selalu dipertahankan**, dan setting
   `orion.backendPath` diisi otomatis. Tombol yang sama berubah menjadi
   **Re-download** untuk update kode tanpa kehilangan key.
3. **Install dependensi** — klik **Install dependencies**: `.venv` dibuat
   (bila belum ada) dan `pip install -r requirements.txt` berjalan dengan
   log langsung (±2–5 menit), ditutup uji asap impor backend.
4. **Form API key** — tempel satu atau beberapa key Gemini **gratis**
   (<https://aistudio.google.com/apikey>); **+ Add key** untuk menambah.
   Key yang sudah tersimpan ditampilkan **disamarkan** — biarkan field
   kosong untuk mempertahankan key lama, isi nilai baru untuk mengganti.
   Bagian *Advanced* menambah provider lain sebagai grup
   `PROVIDER_<N>_*` (OpenRouter/OpenAI/Anthropic/DeepSeek/Qwen/Groq).
   **Save keys to backend/.env** menulis file tanpa menyentuh baris lain.
5. **Selesai** — **Restart backend** agar key baru terbaca, lalu
   **Open chat**. Selanjutnya backend hidup otomatis setiap VS Code
   dibuka.

Setiap langkah menampilkan lognya sendiri di dalam kartu, jadi bila ada
yang gagal penyebabnya langsung terlihat tanpa membuka terminal.

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
| Port 8000 sudah dipakai | Sejak ext 0.0.10, **Orion: Start Backend** memeriksa dulu siapa yang menjawab di port 8000: **aplikasi lain** → pesan error meminta aplikasi itu ditutup; **instance Orion basi/yatim** (kode lama, PID tidak cocok, token tidak cocok) → dialog menawarkan **Restart backend now**. Fallback manual: `Get-NetTCPConnection -LocalPort 8000 -State Listen \| ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }` lalu Start Backend |

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
**backend wajib di-restart** (key dibaca sekali saat backend mulai).
Catatan sejak ext 0.0.10: *Orion: Start Backend* memang bisa mendeteksi
backend **basi (kode lama)** dan menawarkan restart — tetapi perubahan
`.env` TIDAK terdeteksi sebagai kebasaan (kode tidak berubah). Jadi setelah
mengubah `.env`: tekan **Restart backend** di Setup Wizard
(`Ctrl+Shift+P` → *Orion: Setup Wizard (Install Backend)*), atau matikan
dulu proses di port 8000 (lihat tabel masalah umum) lalu Start Backend.

### Cara kerja router (4 tier)

1. **Tier 1 — Gemini**: semua `GEMINI_API_KEY_1..N` dirotasi acak per
   request; kena rate-limit → otomatis lompat ke key berikutnya.
2. **Tier 2 — provider first-class** (`PROVIDER_<N>_*`, sejak UPDATE-011):
   satu grup per akun provider (OpenAI, Anthropic, DeepSeek, Qwen, Groq,
   OpenRouter, ...); dicoba urut nomor grup, dan model di dalam grup urut
   sebagaimana Anda tulis — urutan = prioritas, tidak diacak.
3. **Tier 3 — endpoint kompatibel-OpenAI gaya lama** (`FALLBACK_*`):
   OpenRouter, OpenAI, DeepSeek, Qwen, Groq, dsb.; tetap didukung penuh
   untuk `.env` lama.
4. **Tier 4 — Cloudflare AI** (opsional).

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
kompatibel-OpenAI di atas).

**Cara C — beberapa provider sekaligus dengan key terpisah (UPDATE-011):**
satu grup `PROVIDER_<N>_*` per akun provider. Grup dicoba urut nomor
(1 dulu, lalu 2, dst.), jadi urutan nomor = prioritas Anda; model di dalam
grup dicoba urut tulisan. Grup yang tidak lengkap (`API_KEY` atau `MODELS`
kosong) dilewati dengan peringatan di log backend — tidak merusak apa pun.

```ini
# Grup 1 — DeepSeek (key native, nama model ber-prefix deepseek/)
PROVIDER_1_NAME=DeepSeek
PROVIDER_1_API_KEY=sk-...
PROVIDER_1_MODELS=deepseek/deepseek-chat, deepseek/deepseek-reasoner

# Grup 2 — Qwen via endpoint kompatibel-OpenAI (tanpa prefix = openai/)
PROVIDER_2_NAME=Qwen
PROVIDER_2_API_KEY=sk-...
PROVIDER_2_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
PROVIDER_2_MODELS=qwen3-coder-plus, qwen-max

# Grup 3 — Claude via prefix LiteLLM (tanpa BASE_URL)
PROVIDER_3_NAME=Claude
PROVIDER_3_API_KEY=sk-ant-...
PROVIDER_3_MODELS=anthropic/claude-sonnet-4-5
PROVIDER_3_TEMPERATURE=0.3
```

Aturan nama model sama dengan Cara B: tanpa garis miring (`/`) dianggap
`openai/...` (benar untuk endpoint kompatibel-OpenAI, yaitu saat
`BASE_URL` diisi); untuk API native tulis nama lengkap LiteLLM
(`anthropic/...`, `deepseek/...`, `groq/...`). Field opsional: `NAME`
(label di log) dan `TEMPERATURE` (bawaan 0.4). Tier `FALLBACK_*` dan
Cloudflare tetap ada di bawahnya sebagai jaring pengaman. Resep lengkap
empat provider ada di `backend\.env.example`.

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
