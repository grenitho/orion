# Project Orion - Spesifikasi Arsitektur & Tools (v0.1)

## 1. Keputusan Arsitektur Final
- **Frontend/UI:** Visual Studio Code Extension (TypeScript).
- **Backend/Orchestrator:** Python (FastAPI). Berjalan otomatis di latar belakang saat VS Code aktif.
- **AI Gateway:** LiteLLM (Python) untuk menormalisasi format Function Calling dan menangani Auto-Fallback (jika 1 model limit, otomatis ganti ke model lain dengan konteks yang sama).
- **Context Management:** AI tidak membaca seluruh project secara mentah. Backend mengekstrak "Dependency Skeleton" (hanya baris import dan nama fungsi) untuk membuat "Peta Project", lalu AI meminta izin untuk membaca file spesifik yang dibutuhkan.

## 2. Keputusan UI/UX (Opsi B: Visual & Transparan)
- Antarmuka tidak hanya mengandalkan panel Chat.
- Akan ada **Panel "Orion Activity"** (menggunakan VS Code Tree View / Webview terpisah) yang menampilkan status eksekusi *tools* secara real-time.
- Status visual akan menggunakan indikator: 
  - 🟢 (Reading/Processing)
  - 🟡 (Preparing Diff/Action)
  - 🔴 (Waiting for User Approval)
  - 🔵 (Executing Terminal)
- Tujuannya: Memberikan transparansi penuh agar user tahu AI sedang bekerja dan tidak mengira aplikasi *freeze*.

## 3. Daftar Tools (Function Calling) untuk AI
AI diberikan 5 "tangan" utama untuk berinteraksi dengan sistem. Backend Python akan menerjemahkan output JSON ini menjadi aksi VS Code API.

1. **`list_files`**: Membaca struktur folder (Peta). Hemat token.
2. **`read_file`**: Membaca isi penuh file spesifik (Wilayah).
3. **`write_file`**: Membuat/menimpa file. Akan memicu UI *Diff Viewer* untuk persetujuan user.
4. **`run_terminal`**: Menjalankan perintah shell. Memiliki parameter `requires_approval` untuk perintah berisiko (install, delete).
5. **`ask_user`**: Menghentikan *loop* AI dan meminta input/konfirmasi langsung dari user di panel chat jika instruksi ambigu atau butuh keputusan arsitektur.

## 4. Aturan Keamanan & Error Handling
- Backend Python akan memblokir perintah terminal berbahaya (seperti `rm -rf /`) secara hardcoded sebelum dieksekusi.
- Jika AI memanggil tool yang gagal (misal: file tidak ditemukan), backend tidak mengembalikan *error traceback* mentah, melainkan pesan ramah: `"Error: File tidak ditemukan. Silakan cek path."` agar AI bisa mengoreksi dirinya sendiri.