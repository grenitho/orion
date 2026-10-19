# FASE C — Plan Eksekusi: Memindahkan Loop Agent keluar dari main.py

**Status:** BELUM DIKERJAKAN. Dokumen ini adalah satu-satunya sumber kebenaran
untuk melakukannya. Jangan mulai tanpa membaca seluruh halaman ini.

**Prasyarat sebelum mulai (semuanya sudah terpenuhi saat dokumen ini ditulis):**
- [x] 77 test hijau (`python -m pytest backend/app/services/ -q`)
- [x] `tools_schema.py`, `prompts.py`, `tool_dispatch.py` sudah terpisah (Fase A/B)
- [x] Pemeriksa konsistensi ORION-15 melaporkan KONSEN
- [x] Repo memiliki commit bersih sebagai titik kembali
- [ ] **Baru:** buat commit titik-kembali SESUDAH setiap langkah di bawah, bukan hanya di akhir

---

## Mengapa urutan ini, dan bukan "pindah sekaligus"

`_aliran_inti` adalah satu generator dengan ±20 titik `yield` dan ±15 variabel
closure (`context_manager`, `todo_manager`, `tool_executor`, `files_changed_this_turn`,
`analyzed_files`, `pending_system_notices`, `_nudge_count`, `_verifikasi_count`, …).
Memindahkannya sekaligus = memindahkan semua keterkaitan itu dalam satu lompatan.
Rencana ini memecahnya menjadi empat langkah yang masing-masing **bisa di-verify
dan di-revert sendiri**.

---

## Langkah 1 — Pindahkan PEMBUAT EVENT ke `sse.py` (risiko: rendah)

Semua baris berbentuk `yield f"data: {json.dumps({...})}\n\n"` memakai pola yang sama.
Buat `app/services/sse.py`:

```python
def ev(jenis: str, **data) -> str:
    return f"data: {json.dumps({'type': jenis, **data}, ensure_ascii=False)}\n\n"
```

Lalu ganti seluruh pembuatan event di main.py dengan `yield sse.ev("status", text=...)` dst.
**Tidak ada perubahan perilaku**; hanya bentuk pemanggilan.

*Kriteria penerimaan:* 77 test hijau + `git diff` hanya menyentuh baris `yield` +
output SSE identik (bandingkan dengan merekam satu percakapan sebelum/sesudah
bila perlu).

## Langkah 2 — Pindahkan KONTEKS PERCAKAPAN ke `session_state.py` (risiko: sedang)

Kumpulan variabel closure di atas menjadi satu dataclass `SessionState` dengan
method: `catat_file_berubah()`, `tambah_notice()`, `reset_per_turn()`, dsb.
Loop tetap di main.py, tapi tidak lagi membawa 15 variabel lepas.

*Kriteria penerimaan:* 77 test hijau + tambah `test_session_state.py` untuk
perilaku `reset_per_turn` dan pencatatan file.

## Langkah 3 — Pindahkan GERBANG-GERBANG ke `loop_guards.py` (risiko: sedang)

Empat gerbang yang sudah punya fungsi murni di `guard_rules.py` dipanggil dari
empat tempat berbeda di loop. Kumpulkan pemanggilannya jadi satu fungsi
`evaluasi_akhir_iterasi(state, konten, ...) -> Keputusan` dengan enum keputusan:
`LANJUT_TODO | TANTANG_KLAIM | CHECKPOINT | JAWAB_NORMAL | PAUSE_ITERASI`.
Loop tinggal `match keputusan:`.

*Kriteria penerimaan:* test baru yang memberi lima masukan dan memastikan lima
keputusan berbeda — termasuk kasus "klaim tanpa file berubah" dan "todo masih ada".

## Langkah 4 — Pindahkan generator ke `agent_loop.py` (risiko: tinggi, terakhir)

Setelah Langkah 1–3, generator tinggal ±300 baris yang benar-benar alur.
Pindahkan ke `app/services/agent_loop.py` dengan signature:

```python
async def aliran_agent(state: SessionState, deps: Deps) -> AsyncIterator[str]
```

`main.py` menjadi: routing HTTP + membangun `SessionState`/`Deps` +
`StreamingResponse(sse.bungkus_done(aliran_agent(...)))`.

*Kriteria penerimaan:* 77+ test hijau, konsistensi KONSEN, DAN uji manual empat
skenario di VS Code: (a) tugas multi-item, (b) tugas yang memaksa konfirmasi
terminal, (c) tugas dengan file rusak untuk memancing verify_code, (d) pembatalan
tombol Cancel di tengah generasi.

---

## Aturan keras selama Fase C

1. **Satu langkah = satu commit.** Kalau sebuah langkah gagal verify, `git checkout`
   file terkait dan mulai langkah itu ulang; jangan menumpuk perbaikan.
2. Jangan mengubah PERILAKU saat memindah. Perbaikan perilaku adalah pekerjaan
   terpisah yang datang SESUDAH pemindahan selesai.
3. Setiap kali tergoda "sekalian saja diperbaiki", berhenti dan catat di
   `docs/FASE_C_PLAN.md` bagian Backlog di bawah.
4. Jalankan `python -m pytest backend/app/services/ -q` setelah SETIAP penggantian
   pola, bukan hanya di akhir langkah.

## Backlog (catatan godaan yang harus ditahan)

- (kosong — isi di sini bila Anda menunda sebuah perbaikan selama Fase C)
