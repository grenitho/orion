# Project Orion - Extension Milestone: Hello World & Pelajaran Debug (v0.1)

## 1. Pencapaian
Boilerplate VS Code Extension (TypeScript + esbuild) berhasil di-activate.
Command `orion.helloWorld` terbukti berjalan: notifikasi "Hello World from Orion!" (Source: Orion)
muncul di jendela Extension Development Host.

## 2. Dua Pelajaran Teknis Penting
### a. Dua Jendela VS Code
Saat menekan F5, VS Code membuka jendela kedua: **Extension Development Host**.
Extension HANYA hidup di jendela host, BUKAN di jendela tempat kita mengetik kode.
Semua pengujian command/UI wajib dilakukan di jendela host.

### b. Kebijakan Editing package.json
Untuk menghindari kerusakan format (karakter tak terlihat / spasi dalam key-value) yang
membuat `contributes` tidak terbaca, setiap perubahan `package.json` dilakukan lewat
**skrip Node.js** (JSON.stringify), lalu diverifikasi via terminal (`Get-Content`),
bukan lewat editor.

## 3. Konfigurasi launch.json
- `preLaunchTask` dihilangkan: task `watch` bersifat tak-berakhir dan membuat
  peluncuran jendela host menggantung. Build dilakukan terpisah (`node esbuild.js` / watch).
- args diberi `"${workspaceFolder}"` agar host otomatis membuka folder project,
  sehingga extension punya workspace untuk dipindai saat diuji.