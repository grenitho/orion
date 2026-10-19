# Implementasi Tool Executor (v0.1)

## 1. Modul: `backend/app/services/tool_executor.py`
Modul ini berisi kelas `ToolExecutor` yang menerjemahkan perintah AI menjadi aksi fisik pada sistem operasi.

## 2. Daftar Fungsi yang Diimplementasikan
- **`read_file(file_path)`**: Membaca konten file secara utuh menggunakan encoding UTF-8. Mengembalikan error string jika file tidak ditemukan.
- **`write_file(file_path, content)`**: Menulis konten ke file. Otomatis membuat folder induk jika belum ada (`os.makedirs`).
- **`run_terminal(command)`**: Menjalankan perintah shell menggunakan `subprocess`. Output `stdout` dan `stderr` ditangkap dan dikembalikan ke AI agar bisa membaca error atau hasil instalasi.

## 3. Keamanan Dasar
- Semua operasi file dibatasi hanya di dalam `self.project_path` (mencegah AI mengedit file sistem di luar project).
- Perintah terminal dijalankan dengan `shell=True` di dalam direktori project (`cwd=self.project_path`).