# Project Orion - Fase 3: Backend Automation (v0.2)

## 1. Pencapaian
- Extension kini secara otomatis mengecek dan menyalakan backend Python (`uvicorn`) saat diaktifkan.
- User tidak perlu lagi membuka terminal manual untuk menjalankan backend.
- Menggunakan `child_process` (`exec`) dan pengecekan port (`net`) untuk mencegah duplikasi server dan menangani path dengan spasi.
- Mekanisme *wait & retry* memastikan extension hanya menerima chat setelah port 8000 benar-benar siap.

## 2. Konfigurasi
- File baru: `src/services/backendManager.ts`
- Otomatisasi dipanggil menggunakan `await` di dalam fungsi `activate()` pada `extension.ts`.