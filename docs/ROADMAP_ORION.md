# Peta Kesenjangan ORION — Fitur yang Belum Ada & Yang Layak Ditambahkan

**Dasar analisis:** repo `grenitho/orion-review` (49 file), diperiksa langsung — bukan dari dokumen.

---

## Bagian 1 — Kabar baik: roadmap Anda ternyata SUDAH SELESAI SEMUA

`docs/21_Orion_Pending_Roadmap.md` dan `docs/22_Orion_Current_Checkpoint.md` mencatat empat
item "Pending". Saya cek satu per satu di kode, dan **keempatnya sudah terpasang**:

| Item roadmap | Status di dokumen | Status sebenarnya di kode | Bukti |
|---|---|---|---|
| Pending 1 — Auto-Summarize Context Window | "SEDANG DIKERJAKAN" | ✅ **Selesai** | `execute_summarize()` main.py:758, `prune_history_safely()` main.py:794 |
| Pending 2 — Configurable Max Iterations | "SEDANG DIKERJAKAN" | ✅ **Selesai** | `load_orion_config()` main.py, `.orion/orion_config.json` |
| Pending 3 — Self-Correction Loop | "belum" | ✅ **Selesai** | `is_terminal_failure()`, blok `[SYSTEM AUTO-CORRECTION MODE]` |
| Pending 4 — Rich Rendering (markdown) | "belum" | ✅ **Selesai** | `renderMarkdown()` di `ChatPanel.ts`, 6 rujukan marked/highlight |

**Tindakan:** perbarui kedua dokumen itu, atau lebih baik — karena `.orion/master_context.md`
dipakai Orion sendiri sebagai konteks, dokumen basi akan menyesatkan Orion juga.
Aturan keras nomor 4 Anda berbunyi *"Jika user meng-attach file ini, BACA DULU"* — kalau isinya
ketinggalan zaman, Orion akan ikut keliru.

**Yang juga sudah ada dan layak diapresiasi** (banyak proyek seumur ORION belum punya ini):

- Backup otomatis sebelum menimpa file → `.orion/backups/<path>.bak`
- `permission_manager.py` + konfirmasi terminal lewat modal VS Code
- `post_edit_verifier.py` — verifikasi setelah edit
- `dependency_scanner.py` → `.orion/dependency_map.json`
- Hard guard `is_long_running_server()` — menolak `npm run dev` dll. di level kode, bukan cuma imbauan prompt
- Validasi orphaned `tool_calls` dua arah di `context_manager.py`
- `safe_split_index()` supaya pemotongan riwayat tidak memutus pasangan assistant↔tool
- Rotasi multi API key Gemini + fallback 3 tier

---

## Bagian 2 — 13 tool yang sudah ada

```
list_files   read_file   read_file_range   read_json_path   search_code
write_file   edit_file   run_terminal      ask_user         update_todos
git_status   git_diff    web_search
```

Untuk agent coding, ini fondasi yang sudah lengkap. Yang hilang bukan "tool dasar",
melainkan **tool yang menutup putaran umpan balik** dan **tool yang memberi Orion mata**.

---

## Bagian 3 — Yang belum ada, diurutkan (dampak ÷ usaha)

### 🔴 Prioritas 1 — dampak besar, usaha kecil

| # | Fitur | Kenapa penting | Usaha |
|---|---|---|---|
| 1 | **Lampiran gambar & file** | Ini yang Anda tanyakan. `gemini-3.5-flash-lite` **mendukung input gambar**. Screenshot error → "perbaiki ini" adalah alur paling natural. Implementasi lengkap ada di folder ini. | Sedang |
| 2 | **`web_fetch`** (baca isi halaman) | Anda punya `web_search` (ddgs) tapi hanya dapat **cuplikan**. Orion tidak bisa membaca dokumentasi/halaman StackOverflow utuh. Untuk debugging error asing, ini pembeda terbesar. | Kecil |
| 3 | **Streaming token dari LLM** | `stream=True` **tidak dipakai sama sekali** (0 kemunculan). Respons datang sekaligus, jadi selama 30–60 detik UI benar-benar senyap. Ini sisa penyebab "terasa berhenti diam-diam" yang belum tertangani. Anda sudah punya heartbeat untuk terminal — tinggal lakukan hal yang sama untuk LLM. | Sedang |
| 4 | **`git_commit` / `git_branch`** | Git Anda **hanya baca** (`git_status`, `git_diff`). Orion tidak bisa menyimpan pekerjaannya sendiri. Tanpa ini tidak ada checkpoint yang bisa di-rollback. | Kecil |
| 5 | **Test — rasio Anda 0%** | Bukan fitur produk, tapi ini yang paling menghambat. Setiap patch yang saya berikan tidak bisa Anda verifikasi otomatis. Mulai dari 3 test untuk `attachments.py` (sudah saya sertakan polanya) dan PATCH ORION-1. | Kecil |

### 🟠 Prioritas 2 — dampak besar, usaha sedang

| # | Fitur | Kenapa penting |
|---|---|---|
| 6 | **Kirim diagnostics VS Code ke backend** | Extension punya akses ke `vscode.languages.getDiagnostics()` — daftar error merah di panel *Problems*. Orion **tidak pernah melihatnya**. Padahal itu sinyal paling akurat tentang apakah editannya berhasil. Ini "mata" yang sudah tersedia gratis di VS Code. |
| 7 | **Konteks editor aktif** | Orion tidak tahu file mana yang sedang Anda buka atau teks mana yang Anda sorot. Menambahkan "file aktif + seleksi" ke prompt membuat jawaban jauh lebih relevan. |
| 8 | **`restore_backup`** | Backup sudah dibuat otomatis di `.orion/backups/`, tapi **tidak ada tool untuk memulihkannya**. Setengah fitur. Kalau Orion merusak file, Anda harus manual ke folder itu. |
| 9 | **`apply_patch` (unified diff)** | `edit_file` SEARCH/REPLACE gagal kalau blok yang dicari muncul lebih dari sekali, atau kalau model salah menyalin whitespace persis. Format diff lebih tahan banting untuk edit besar. |
| 10 | **`delete_file` / `move_file`** | Sekarang harus lewat `run_terminal` → memicu modal konfirmasi → memutus alur agent. |

### 🟡 Prioritas 3 — berharga untuk proyek yang tumbuh

| # | Fitur | Catatan |
|---|---|---|
| 11 | **Semantic search (embedding + RAG)** | 0 kemunculan `embedding`/`vector`. `search_code` (grep) cukup untuk proyek kecil, tapi untuk codebase besar grep tidak menemukan "fungsi yang menangani autentikasi" — hanya teks persis. Gemini Embedding ada di free tier. |
| 12 | **`run_tests` khusus** | Wrapper yang menjalankan test lalu **mengurai hasilnya** (lulus/gagal/nama test yang gagal), bukan membuang output mentah ke model. Menghemat token banyak. |
| 13 | **Dukungan MCP** | Model Context Protocol memungkinkan Orion memakai server tool eksternal (database, browser, Figma, dll.) tanpa Anda menulis tiap integrasi. Ekosistemnya sudah besar. |
| 14 | **Subagent / tugas paralel** | Pola yang justru disarankan Google untuk Flash-Lite: satu model ringan mengerjakan banyak tugas kecil terisolasi. Cocok dengan `todo_manager` yang sudah Anda punya. |
| 15 | **Mode "review only"** | Orion menganalisis tanpa boleh menulis file. Berguna untuk audit kode — dan jauh lebih aman. |

### ⚪ Yang sengaja TIDAK saya sarankan sekarang

- **Sandbox container untuk `run_terminal`** — idealnya ya, tapi Anda jalan di laptop sendiri
  dengan modal konfirmasi. Nilainya baru terasa kalau ORION dipakai orang lain.
- **Multi-user / auth / database** — arsitektur Anda single-user lokal. Menambah ini sekarang
  hanya memperlambat.
- **Menulis ulang `main.py`** — 2.153 baris memang terlalu besar, tapi tanpa test, memecahnya
  sekarang berisiko tinggi. Buat test dulu (item 5), baru pecah.

---

## Bagian 4 — Soal pertanyaan Anda: upload file & gambar

**Jawabannya: ya, dan stack Anda sudah siap untuk itu.** Tidak perlu ganti model, tidak perlu
tambah provider.

| Syarat | Status |
|---|---|
| Model mendukung input gambar | ✅ `gemini-3.5-flash-lite` — dokumentasi Google: *"Image — input only"*, maks 3.000 gambar/prompt, 7 MB/file, format png/jpeg/webp/heic/heif |
| litellm bisa meneruskan gambar | ✅ format OpenAI `image_url` berisi data URI base64 → diterjemahkan ke `inline_data` Gemini |
| VS Code webview bisa pilih file | ✅ `<input type="file">` + drag-drop + paste dari clipboard |
| Perubahan arsitektur | ❌ **Tidak ada.** Tetap FastAPI + SSE + TS extension (menghormati aturan keras Anda nomor 2) |

Yang perlu diubah hanya tiga titik:
1. `ChatRequest` — tambah field `attachments`
2. `main.py` — suntikkan gambar ke `api_messages` **hanya pada iterasi pertama**
3. `ChatPanel.ts` — tombol lampir + baca file jadi base64

**Implementasi lengkapnya ada di folder ini:**

| File | Isi |
|---|---|
| `backend/app/services/attachments.py` | Modul backend. **Sudah saya uji 8 skenario** — semua lulus |
| `PANDUAN_FITUR_ATTACHMENT.md` | Patch persis untuk `main.py` dan `ChatPanel.ts`, dengan nomor baris |
| `test_attachments.py` | 8 test siap pakai — sekaligus memulai rasio test Anda dari 0% |

### Keputusan desain yang perlu Anda tahu

**Gambar TIDAK disimpan ke riwayat.** Base64 satu screenshot 1 MB menjadi ±1,4 juta karakter.
Kalau masuk `.orion/session_history.json`, maka:
- setiap iterasi agent mengirim ulang gambar itu → kuota free tier habis dalam beberapa pesan
- file riwayat membengkak permanen dan makin lambat dibaca

Jadi: riwayat menyimpan **placeholder teks** (`[LAMPIRAN GAMBAR: error.png (image/png, 412 KB)]`),
dan gambar asli hanya disuntikkan ke permintaan pertama giliran itu.

**File teks diperlakukan berbeda** — isinya dibaca dan ditempel sebagai teks biasa, karena murah
dan tetap berguna di iterasi berikutnya.

**Batas yang saya pasang** (semua bisa diubah di atas `attachments.py`):

```python
MAX_GAMBAR = 4                    # per pesan (batas API 3.000, tapi ini boros kuota)
MAX_BYTE_GAMBAR = 7 * 1024 * 1024  # batas inline Gemini
MAX_BYTE_TEKS  = 512 * 1024
MAX_CHAR_TEKS  = 60_000            # potong isi teks agar konteks tidak meledak
```

Kalau ada lampiran yang ditolak, model **diberi tahu secara eksplisit** bahwa ia tidak bisa
melihatnya — supaya Orion tidak mengarang isi gambar yang tidak pernah ia terima.

---

## Bagian 5 — Urutan yang saya sarankan

Kalau saya di posisi Anda, urutannya begini:

```
Minggu ini    1. Lampiran gambar          <- sudah siap dipasang, kode + test tersedia
              2. web_fetch                <- ±40 baris, dampak besar untuk debugging
              3. 3 test pertama           <- mulai dari attachments.py
              4. Perbarui docs/21 & 22    <- supaya Orion tidak membaca roadmap basi

Minggu depan  5. git_commit + restore_backup
              6. Diagnostics VS Code -> backend
              7. Streaming token LLM

Bulan depan   8. Pecah main.py (setelah ada test)
              9. apply_patch (unified diff)
             10. Semantic search
```

Alasan lampiran gambar di urutan pertama bukan karena paling penting secara teknis, tapi karena
**sudah jadi dan teruji** — Anda tinggal memasang. Dan fitur yang terlihat hasilnya cenderung
membuat fitur berikutnya lebih mudah dikerjakan.
