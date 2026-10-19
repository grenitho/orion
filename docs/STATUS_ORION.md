> **INTERNAL / AGENT-CONTEXT (Indonesian).** Dokumen ini dibaca Orion sendiri dari
> `.orion/`. Dokumentasi pengguna yang kanonik berbahasa Inggris ada di `README.md`,
> `SECURITY.md`, dan `docs/DEVELOPMENT_ROADMAP.md` di root repo.

# STATUS ORION — Fase OPERASIONAL

> **Dokumen ini menggantikan `docs/21_Orion_Pending_Roadmap.md` dan
> `docs/22_Orion_Current_Checkpoint.md`. HAPUS kedua file itu.**
>
> Keduanya sudah tidak akurat: keempat item "Pending" di dalamnya **sudah selesai
> dikerjakan**. Kalau Orion membaca dokumen lama itu, ia akan mengira dirinya masih
> dalam masa pembangunan dan bisa mencoba "menyelesaikan" fitur yang sudah ada.

**Versi:** 0.4.1 → **0.5.0** (fitur lampiran + perbaikan tool mati)
**Fase:** 🟢 **OPERASIONAL** — bukan dalam pembangunan
**Terakhir diverifikasi:** seluruh backend lolos `py_compile` + `ruff (F/E9/B)`;
extension lolos `tsc --noEmit`; 14/14 test `attachments.py` lulus.

---

## Pesan untuk Orion (bacalah ini dulu)

**Kamu sudah jadi.** Kamu bukan prototipe yang sedang dibangun — kamu alat kerja yang
dipakai sungguhan. Artinya:

1. **JANGAN** menawarkan "membangun" fitur yang sudah terdaftar di bawah. Semua sudah ada.
2. **JANGAN** menulis ulang arsitektur (FastAPI + SSE + TypeScript extension). Itu keputusan
   yang sudah final.
3. **JANGAN** mengaktifkan streaming token dari LLM. Arsitektur ini sengaja memakai
   `stream=False` lalu mengirim event SSE sendiri — supaya setiap langkah agent bisa
   disisipi status, konfirmasi, dan todo update.
4. Kalau diminta memperbaiki bug, **periksa dulu daftar "Sudah Diperbaiki" di bawah**.
   Beberapa bug yang mungkin kamu "temukan" sudah ditangani secara eksplisit.
5. Fokus pada **tugas user**, bukan pada merapikan dirimu sendiri, kecuali diminta.

---

## Kemampuan yang SUDAH ada (jangan dibangun ulang)

### 22 tool aktif

```
list_files   read_file   read_file_range   read_json_path   search_code
write_file   edit_file   run_terminal      ask_user         update_todos
web_search   web_fetch   git_status        git_diff         verify_code
verify_html  restore_backup  list_backups  delete_file      move_file
git_commit   run_tests
```

(`verify_html` adalah alias dari `verify_code`, dipertahankan supaya aturan lama tetap berlaku.)

Ke-22 tool ini **terverifikasi punya cabang dispatch**. Pemeriksa otomatis
(`periksa_konsistensi_tools()`) berjalan saat startup dan mencatat ke log kalau
skema TOOLS dan dispatch menyimpang. Angka di log (`Konsisten: N tool`) adalah
cara tercepat memastikan proses yang berjalan memuat kode versi mana.

### Kemampuan sistem

| Kemampuan | Implementasi |
|---|---|
| Manajemen konteks | `context_manager.py` — riwayat persisten, validasi orphaned `tool_calls` dua arah, atomic write, backup otomatis |
| Auto-summarize | `execute_summarize()` + `prune_history_safely()` — riwayat panjang diringkas otomatis saat mendekati batas token |
| Pemotongan riwayat aman | `safe_split_index()` — tidak pernah memutus pasangan assistant↔tool |
| Daftar tugas | `todo_manager.py` — model memanggil `update_todos`; **loop melanjutkan diri sendiri** selama masih ada todo pending |
| Perbaikan diri | `is_terminal_failure()` + `[SYSTEM AUTO-CORRECTION MODE]` — error terminal diperbaiki otomatis tanpa disuruh |
| Verifikasi pasca-edit | `post_edit_verifier.py` |
| Intelijen proyek | `dependency_scanner.py` → `.orion/dependency_map.json` |
| Keamanan terminal | `permission_manager.py` + modal konfirmasi VS Code (`confirm_terminal`) + `DANGEROUS_PATTERNS` |
| Guard proses menggantung | `is_long_running_server()` — menolak `npm run dev` dll. di level kode, bukan imbauan prompt |
| Backup file | `.orion/backups/<path>.bak` dibuat otomatis sebelum menimpa |
| Anti-loop | Berhenti paksa setelah tool yang sama gagal 3× berturut-turut |
| Router model | `llm_router.py` — rotasi multi API key Gemini → OpenRouter → Cloudflare |
| Batas iterasi | `.orion/orion_config.json` → `max_iterations` (default 20, maksimum keras 200) |
| Rendering | Markdown + syntax highlighting di panel chat |
| Lampiran | **Gambar (png/jpg/webp/heic/heif) + file teks** — lewat tombol 📎, drag-drop, atau paste screenshot |
| Logging | `.orion/logs/backend.log` (rotasi 5 MB × 5) + `~/orion_stdout.log` |
| Performa | `.orion/performance_log.jsonl` + endpoint `/performance` + panel dashboard |

---

## Sudah diperbaiki (jangan "perbaiki" ulang)

| Kode | Masalah | Solusi yang terpasang |
|---|---|---|
| ORION-1 | **Tugas multi-item berhenti setelah item pertama.** `is_task_complete` dicocokkan dari kata ("selesai", "berhasil"), lalu loop `break` walau todo masih ada | Kalau masih ada todo pending, loop menyuntik pesan sistem yang menunjuk item berikutnya lalu `continue`. Batas 10× nudge (`ORION_MAX_TODO_NUDGE`) |
| ORION-2 | `except` mengirim event `error` **tanpa** `done` → UI menggantung | `done` selalu dikirim setelah `error` |
| ORION-3 | `except: pass` menelan semua error | exception spesifik |
| ORION-4 | `max_iterations` default tidak konsisten (20 vs 500) | disamakan + dibatasi 200 |
| ORION-5 | log tanpa rotasi/level | `logging_setup.py` → RotatingFileHandler |
| ORION-6 | **`reasoning_effort` tidak pernah dikirim.** litellm lalu tidak mengirim `thinking_level`, sehingga `gemini-3.5-flash-lite` jalan di **MINIMAL** → *premature tool termination* | `reasoning_effort="high"` untuk model Gemini |
| ORION-7 | retry instan tanpa jeda → untuk 429 pasti gagal lagi | exponential backoff + jitter |
| ORION-8 | `requirements.txt`: litellm 1.35.0 (sebelum Gemini 3 ada); `tiktoken` & `ddgs` diimpor tapi tak dideklarasikan | litellm ≥ 1.105.1 + dependensi lengkap |
| ORION-9 | **Bug nyata:** lambda menangkap `cmd_to_run` *by reference* → bisa menjalankan perintah terminal yang salah | diikat sebagai default argument |
| ORION-10/11 | backend mengirim `done` & `todo_update`, extension **mengabaikannya** | ditangani |
| ORION-12 | **UI melapor "Selesai / Sukses" setiap kali stream menutup** — termasuk kalau backend mati di tengah jalan | hanya lapor sukses kalau backend benar-benar mengirim sinyal selesai |
| ORION-13 | daftar todo tidak pernah tampil di panel | `renderTodos()` di webview |
| ORION-14 | **`web_search`, `git_status`, `git_diff` dideklarasikan & method-nya ada, tapi tidak punya cabang dispatch** → selalu kembali `"Tool tidak dikenal."` Padahal system prompt **mewajibkan** `web_search` untuk error asing | dispatch ditambahkan; pesan error sekarang menyebut tool yang benar-benar tersedia |
| ORION-15 | penyimpangan skema↔dispatch tidak terdeteksi | `periksa_konsistensi_tools()` saat startup |
| — | `temperature=1.0` dikirim ke Gemini | tidak dikirim lagi — `gemini-3.5-flash-lite` **mengabaikan** nilai temperature kustom. Dugaan lama bahwa "1.0 wajib agar tidak hang" tidak benar; penyebab hang yang sebenarnya adalah banjir stdout, dan itu sudah ditangani |

---

## Batasan yang DISADARI (bukan bug — keputusan desain)

| Batasan | Alasan |
|---|---|
| Single user, lokal, tanpa auth | ORION memang alat pribadi di laptop sendiri |
| `run_terminal` pakai `shell=True` tanpa container | dijalankan di mesin user sendiri, dengan modal konfirmasi. Lihat catatan sandbox di bawah |
| Tidak ada streaming token dari LLM | disengaja — lihat pesan untuk Orion poin 3 |
| Gambar hanya terlihat pada giliran pertama | disengaja — base64 di riwayat akan menghabiskan kuota free tier |
| `max_tokens` 32768 | di bawah batas model (65536) untuk menghemat kuota free tier |

---

## Yang masih boleh dikerjakan (kalau user meminta)

Diurutkan dari dampak ÷ usaha. **Jangan memulai ini tanpa diminta.**

1. **Test** — rasio sekarang 14 test / 11 file source, semuanya untuk `attachments.py`.
   Yang paling berharga berikutnya: test untuk PATCH ORION-1 (todo nudge) dan
   `context_manager.safe_split_index()`.
2. **Diagnostics VS Code → backend.** Extension bisa membaca
   `vscode.languages.getDiagnostics()` (daftar error di panel *Problems*) tapi belum
   mengirimnya. Itu sinyal paling akurat apakah sebuah edit berhasil.
3. **Konteks editor aktif** — file yang sedang dibuka + teks yang disorot.
4. **`restore_backup`** — backup sudah dibuat otomatis, tapi belum ada tool untuk memulihkannya.
5. **`delete_file` / `move_file`** — sekarang harus lewat `run_terminal` yang memicu modal.
6. **`apply_patch` (unified diff)** — lebih tahan banting daripada SEARCH/REPLACE untuk edit besar.
7. **Pecah `main.py` (2.200+ baris)** — endpoint `/chat` membentang ±1.200 baris.
   **Syarat: buat test dulu.** Tanpa test, ini berisiko tinggi.
8. **Semantic search (embedding)** — `search_code` (grep) tidak cukup untuk codebase besar.
9. **Dukungan MCP** — memakai server tool eksternal tanpa menulis tiap integrasi.

---

## Catatan keamanan

- `.env` **tidak pernah** boleh di-commit. `.gitignore` sudah menanganinya.
- `web_fetch` punya **proteksi SSRF**: URL yang menuju localhost, IP privat, loopback,
  link-local, atau alamat reserved **ditolak** — karena alamat tujuan dipilih oleh model.
- `.orion` ada di `.gitignore`. Pertimbangkan mengecualikan `*.md`-nya
  (`git add -f .orion/master_context.md`) supaya dokumen konteks ikut ter-version,
  sementara `session_history.json` tetap diabaikan.
- Sebelum membagikan kode ke pihak mana pun, jalankan `prescan.py`.


---

## PERUBAHAN SEJAK v0.5.0 (jangan dibangun ulang, sudah terpasang)

| Kode | Isi |
|---|---|
| ORION-18 | Perbaikan layout kotak ketik (sempat menyempit karena regresi flex) |
| ORION-19/22 | Lampiran gambar: dicoba, lalu **dihapus dari UI** karena webview VS Code membatasi clipboard. Field backend tetap ada tapi tidak aktif |
| ORION-20 | **Mata Orion**: diagnostics panel Problems + file aktif + sorotan dikirim tiap pesan |
| ORION-21 | `.vscodeignore` — VSIX tidak lagi membocorkan `.orion/session_history.json` |
| ORION-23/25 | `verify_html` lalu `verify_code`: pemeriksa NYATA 16 jenis file + mode folder |
| ORION-24 | Gerbang verifikasi diperluas: 23 kata kunci klaim, lubang iterasi-pertama ditutup |
| ORION-26 | Jaring pengaman: `restore_backup`, `list_backups`, `delete_file`, `move_file`, `git_commit` |
| ORION-27 | `git init/push/pull/clone/reset/rebase` **diblokir di run_terminal** (pernah hang dan mematikan backend) |
| ORION-28 | Wrapper `done`: 10 jalur keluar stream yang dulu mengakhiri tanpa sinyal kini ditutup benar. Menghilangkan alarm palsu "koneksi terputus" |
| ORION-30 | `run_tests`: jalankan pytest/npm test/go test otomatis, laporan TERURAI (lulus/gagal + penyebab per kegagalan) |
| ORION-29 | Heartbeat "🤔 Masih berpikir... N detik" tiap 15 detik selama menunggu LLM |
| ORION-32 | `verify_code` memeriksa impor lewat interpreter MILIK project, bukan backend (menghilangkan positif palsu) |
| ORION-33 | Test regresi `test_loop_counter_guard.py`: melarang `_` dipakai sebagai variabel buang di loop agent (bug produksi ORION-29 v1) |
| ORION-34 | Logika klaim & gerbang verifikasi dipindah ke `guard_rules.py` (fungsi murni) + 19 test memakai kalimat halusinasi SUNGGUHAN dari transkrip produksi |
| ORION-35 | Riset otomatis saat mentok: pada anti-loop, backend mencari web SENDIRI dan menyuntikkan temuannya; ekstrak web_fetch membuang menu navigasi |
| ORION-36 | Wrapper `done` diangkat ke `stream_utils.jamin_done()` + test untuknya dan untuk guard git (ORION-27/28 kini tertest) |

### Perilaku yang sekarang NORMAL (bukan bug)

- Orion **berhenti untuk bertanya** (`ask_user`) kini berakhir bersih, tanpa pesan error.
- Orion **menolak** menjalankan `git init/push/pull` dan menyuruh Anda melakukannya manual. Itu sengaja.
- Orion **menantang dirinya sendiri** di log (`[KLAIM TANPA BUKTI]`) kalau mengklaim selesai tanpa mengubah file. Kalau Anda melihat baris itu di log, sistem bekerja.
- Saat berpikir lama, panel menampilkan "🤔 Masih berpikir... 15 detik", dst.

---

## FASE REFAKTORING (v0.6.0)

| Fase | Isi | Status |
|---|---|---|
| A | `tools_schema.py` (skema 22 tool) + `prompts.py` (system prompt) keluar dari main.py | ✅ selesai |
| B | `tool_dispatch.py`: 14 tool murni jadi tabel; 8 tool berguard tetap di main.py dengan alasan tercatat | ✅ selesai |
| C | Loop agent (`_aliran_inti`) ke `agent_loop.py` dalam 4 langkah ber-commit | ⏳ **lihat `docs/FASE_C_PLAN.md`** |

Ukuran `main.py`: 2.769 → 2.115 baris. Test: 70 → **77**.
`docs/FASE_C_PLAN.md` adalah satu-satunya sumber kebenaran untuk Fase C:
empat langkah ber-commit terpisah, kriteria penerimaan per langkah, dan aturan
"satu langkah = satu commit, jangan mengubah perilaku saat memindah".
