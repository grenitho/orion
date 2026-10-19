# Project Orion - Fase 2: VS Code Extension Setup (v0.1)

## 1. Teknologi
- Bahasa: TypeScript
- Framework: VS Code Extension API
- Package Manager: npm
- Generator: Yeoman (`yo code`)
- Bundler: esbuild (dipilih karena build hampir instan)

## 2. Pembuatan Boilerplate
Di root project:
- `npm install -g yo generator-code`
- `yo code` -> New Extension (TypeScript), nama "Orion", bundler esbuild.
- Hasil: folder extension (di mesin ini bernama `orion/`).

## 3. Struktur Folder Extension
- `orion/src/extension.ts`: entry point (activate / deactivate).
- `orion/package.json`: manifest (name, contributes, commands).
- `orion/tsconfig.json`: konfigurasi TypeScript.
- `orion/esbuild.js`: konfigurasi bundler.
- `orion/.vscode/`: launch.json & tasks.json untuk debug.

## 4. Perbaikan tasks.json
Generator esbuild menyisipkan `problemMatcher: "$esbuild-watch"` yang tidak dikenal VS Code
(error "Invalid problemMatcher reference"). Diperbaiki menjadi `problemMatcher: []`.

## 5. Cara Menjalankan Mode Development
1. Buka folder `orion/` di VS Code.
2. Build: `node esbuild.js`.
3. Tekan F5 -> membuka jendela Extension Development Host.
4. Di host: Ctrl+Shift+P -> jalankan command Orion.