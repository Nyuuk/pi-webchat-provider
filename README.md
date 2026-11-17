# Pi Agent Connector

Jembatan dua arah (bidirectional bridge) antara **AI Chatbot Web** (Claude, ChatGPT, DeepSeek, Google Gemini, Grok, Kimi, Mistral, dll.) dengan **Pi Agent (Harness)** (`@mariozechner/pi-coding-agent`).

Proyek ini memungkinkan AI di browser menggunakan tool bawaan Pi Agent langsung di sistem Anda, sekaligus memungkinkan Anda (atau Pi Agent) mengirimkan prompt ke browser AI dari terminal.

---

## Fitur Utama

1. **Toolset Asli Pi Agent (Harness)**:
   - `bash`: Menjalankan perintah shell/bash di workspace.
   - `read`: Membaca file dengan nomor baris, offset, dan limit.
   - `write`: Menulis / membuat file lengkap.
   - `edit`: Melakukan penggantian teks terarah secara presisi (`oldText` -> `newText`).
   - `grep`: Pencarian regex di seluruh file workspace.
   - `find`: Pencarian file berdasarkan pola glob.
   - `ls`: Melihat daftar direktori dan ukuran file.

2. **Prompting 2 Arah (Bidirectional)**:
   - **Arah 1 (Browser ➔ Pi)**: Chatbot web memanggil tool Pi Agent di komputer Anda untuk memeriksa kode, menjalankan command, dan mengedit file.
   - **Arah 2 (Pi ➔ Browser)**: Anda (atau Pi Agent CLI) dapat mengirimkan prompt dari terminal ke tab chatbot browser yang sedang terbuka via CLI `send_prompt.py` atau ekstensi Pi Agent (`/browser <prompt>`), lalu menerima jawaban AI langsung di terminal.

3. **Multi-Provider Web**:
   Mendukung Claude, ChatGPT, DeepSeek, Google Gemini, Grok, Kimi, GLM (Z.ai), Qwen, Meta AI, Mistral (Vibe Code), Ptero, dan Arena.

---

## Arsitektur

```text
  [ Arah 1: Browser -> Pi ]
  Browser (Claude / ChatGPT) ──▶ Tool Call (JSON) ──▶ Extension ──▶ WebSocket (17613) ──▶ Bridge ──▶ Pi Tools MCP
                                                                                                        │
  Browser (Claude / ChatGPT) ◀── Hasil Output ◀────── Extension ◀── WebSocket ◀────────── Bridge ◀─────┘

  ─────────────────────────────────────────────────────────────────────────────────────────────────────────

  [ Arah 2: Pi -> Browser ]
  Terminal / Pi Agent CLI ──▶ HTTP POST (17614) ──▶ Bridge ──▶ WebSocket ──▶ Extension
                                                                                │
                                                                   (P.typeAndSend ketik ke chat web)
                                                                                │
                                                                                ▼
  Terminal / Pi Agent CLI ◀── Jawaban Akhir ◀── HTTP ◀── Bridge ◀── WebSocket ◀─┘
```

---

## Struktur Direktori

```text
pi-agent-connector/
├── extension/             # Browser Extension (Chrome/Edge Manifest V3)
│   ├── manifest.json
│   ├── background.js      # WebSocket client + remote prompt handler
│   ├── overlay.css
│   ├── popup.html / .js   # Status popup
│   ├── core/              # System prompt, parser, & agentic loop
│   └── providers/         # Adapter DOM untuk masing-masing web AI
├── pi_tools_mcp.py        # MCP Server yang menyediakan 7 tool bawaan Pi Agent
├── pi_bridge.py           # Local WebSocket (17613) & HTTP REST (17614) Server
├── pi-extension.ts        # Ekstensi resmi untuk Pi Agent CLI (/browser command)
├── send_prompt.py         # CLI pengirim prompt dari terminal Pi ke browser
├── config.json            # Konfigurasi server MCP
├── start.sh               # Launcher Linux / macOS
└── start.bat              # Launcher Windows
```

---

## Panduan Pemasangan

### 1. Pasang Extension di Browser (Chrome / Edge)
1. Buka browser dan kunjungi `chrome://extensions` atau `edge://extensions`.
2. Aktifkan **Developer mode** (di pojok kanan atas).
3. Klik tombol **Load unpacked**.
4. Pilih folder `extension/` dari repositori ini.
5. Ikon **Pi Agent Connector** akan muncul di toolbar browser Anda.

### 2. Jalankan Bridge di Komputer / Host
Di terminal:
```bash
./start.sh
```
*(Atau `python3 pi_bridge.py`)*

Bridge akan mendengarkan di:
- WebSocket: `ws://127.0.0.1:17613` (koneksi extension)
- HTTP API: `http://127.0.0.1:17614` (koneksi CLI / Pi Agent)

---

## Cara Penggunaan

### Arah 1: Menggunakan Tool dari Browser
1. Buka salah satu AI web chat yang didukung (misal: [Claude.ai](https://claude.ai) atau [ChatGPT](https://chatgpt.com)).
2. Bar **Pi Agent** akan muncul di halaman chat.
3. Klik **Start** pada bar tersebut.
4. Tulis instruksi seperti biasa di chat web, contoh:
   - *"Tolong cek isi direktori ini dan periksa apakah ada bug di main.py"*
   - *"Jalankan perintah bash `git status` dan jelaskan perubahannya"*
   - *"Edit file config.json untuk mengganti port menjadi 8080"*
5. AI di browser akan otomatis memanggil tool Pi Agent, bridge mengeksekusinya di host Anda, dan hasilnya langsung diteruskan kembali ke chat.

---

### Arah 2: Mengirim Prompt dari Pi / Terminal ke Browser

#### Menggunakan CLI `send_prompt.py`
Anda bisa mengetikkan prompt dari terminal ke tab chat browser yang sedang aktif:
```bash
python3 send_prompt.py "Jelaskan arsitektur proyek ini secara singkat"
```

Bisa juga menggunakan pipeline dari perintah shell:
```bash
git diff | python3 send_prompt.py "Tolong review perubahan kode berikut:"
```

#### Menggunakan Ekstensi Pi Agent CLI (`pi-extension.ts`)
Jika Anda menggunakan Pi Agent CLI (`@mariozechner/pi-coding-agent`), salin `pi-extension.ts`:
```bash
mkdir -p ~/.pi/agent/extensions
cp pi-extension.ts ~/.pi/agent/extensions/browser-bridge.ts
```

Setelah itu, saat Anda menjalankan `pi` di terminal:
- Ketik perintah:
  ```text
  /browser Tolong berikan ide solusi untuk error timeout pada database
  ```
  Prompt akan otomatis terkirim ke tab browser AI Anda, diproses di sana, dan hasilnya langsung tercetak di terminal Pi Anda.
- Model di Pi Agent juga otomatis mendapatkan tool `ask_browser` sehingga Pi Agent bisa berkonsultasi ke model web AI saat mengerjakan task.

---

## Catatan Penting
- Pastikan tab AI chat web tetap terbuka saat Anda mengirimkan remote prompt dari Pi.
- Tidak ada unit test lokal yang dijalankan secara otomatis; seluruh alur dapat Anda uji langsung di lingkungan Anda.
