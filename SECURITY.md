# KEAMANAN ORION

Dokumen ini untuk Anda yang menjalankan Orion di mesin sendiri, dan untuk
kontributor yang membaca kode ini.

## Model ancaman yang kami akui secara terbuka

Orion adalah agent yang **menjalankan perintah shell dan menulis file** di mesin
tempat ia dipasang. Itu sumber kekuatannya dan sumber risikonya. Berikut batasannya:

| Lapisan | Keadaan |
|---|---|
| Bind jaringan | Backend bind **eksplisit** ke `127.0.0.1:8000`. Tidak terjangkau dari mesin lain |
| CORS | **Tertutup** (`allow_origins=[]`, hanya regex `vscode-webview://`). Halaman web jahat tidak bisa memanggil backend dari browser |
| Perintah shell | `run_terminal` memfilter pola berbahaya + modal konfirmasi di VS Code; `git init/push/pull/clone/reset/rebase` **diblokir di level kode** |
| Path | Semua operasi file dijaga agar tidak keluar dari folder project (path traversal ditolak) |
| Web fetch | Proteksi SSRF: localhost, IP privat, loopback, link-local, dan alamat metadata ditolak |
| Backup | Setiap penimpaan/hapus file meninggalkan salinan di `.orion/backups/` |

## Yang TIDAK kami klaim

- **Tidak ada sandbox proses.** Perintah yang disetujui berjalan dengan hak user
  yang menjalankan Orion. Jangan menjalankan Orion sebagai administrator/root.
- **Tidak ada auth antara extension dan backend** selain CORS + loopback. Proses
  lokal lain di mesin yang sama secara teori bisa memanggil endpoint. Token
  bersama (shared secret) adalah penguatan yang direncanakan berikutnya.
- **Model LLM bisa dibujuk** (prompt injection) lewat isi file atau halaman web
  yang ia baca. Modal konfirmasi adalah pertahanan utama: **jangan matikan**.

## Data yang tidak boleh ikut ke repo / VSIX

`.orion/` (riwayat percakapan, log performa, todo), `*.vsix` lama, `.env`,
`__pycache__/`, `node_modules/`, `dist/`. Semuanya sudah ada di `.gitignore`
dan `.vscodeignore`, dan ada test (`test_security_posture.py`) yang menolak
regresi konfigurasi ini.

## Kalau Anda menemukan lubang

Laporkan lewat issue tanpa menyertakan payload aktif, atau hubungi maintainer
secara privat bila itu lubang yang bisa langsung dipakai menyerang.
