# Grok Imagine API Gateway

Gateway API proxy untuk pembuatan gambar Grok, membungkus Grok Imagine sebagai REST API yang kompatibel dengan OpenAI.

Menggunakan koneksi langsung WebSocket ke Grok, tanpa memerlukan otomatisasi browser, meminimalkan penggunaan resource.

## Fitur Utama

- **API Kompatibel OpenAI** - Menyediakan endpoint `/v1/images/generations` dan `/v1/chat/completions`
- **Koneksi Langsung WebSocket** - Berkomunikasi langsung dengan layanan Grok, tanpa Playwright/Selenium
- **Verifikasi Usia Otomatis** - Secara otomatis menyelesaikan verifikasi usia dewasa saat pertama kali digunakan, tanpa operasi manual
- **Mode NSFW Otomatis** - Secara otomatis mengaktifkan dukungan pembuatan konten NSFW
- **Manajemen Multi SSO** - Mendukung rotasi multi-akun, dengan berbagai strategi rotasi bawaan
- **Cache Gambar** - Secara otomatis menyimpan gambar yang dihasilkan, mendukung preview galeri
- **Dukungan Redis** - Persistensi sesi terdistribusi opsional
- **Dukungan Proxy** - Mendukung proxy HTTP/HTTPS/SOCKS5

## Mulai Cepat

### 1. Instalasi Dependensi

```bash
pip install -r requirements.txt
```

### 2. Konfigurasi SSO

Buat file `key.txt` di direktori root proyek, satu SSO Token per baris:

```
your-sso-token-1
your-sso-token-2
```

### 3. Mendapatkan cf_clearance (Penting)

`cf_clearance` adalah cookie verifikasi Cloudflare, digunakan untuk menyelesaikan verifikasi usia secara otomatis. Cara mendapatkannya:

1. Gunakan browser untuk mengakses https://grok.com dan login
2. Tekan F12 untuk membuka developer tools
3. Beralih ke tab **Application**
4. Di sisi kiri pilih **Cookies** -> `https://grok.com`
5. Temukan `cf_clearance` dan salin nilainya
6. Masukkan nilai tersebut ke konfigurasi `CF_CLEARANCE` dalam file `.env`

> **Catatan**: `cf_clearance` memiliki waktu kedaluwarsa, jika verifikasi usia gagal perlu diambil ulang.

### 4. Konfigurasi Environment Variables

Salin `.env.example` menjadi `.env` dan edit:

```env
# Konfigurasi Server
HOST=0.0.0.0
PORT=9563
DEBUG=false

# Proteksi API Key
API_KEY=your-secure-api-key-here

# Verifikasi Cloudflare (wajib, untuk verifikasi usia otomatis)
CF_CLEARANCE=your-cf-clearance-cookie-here

# Konfigurasi Proxy (opsional, mendukung HTTP/HTTPS/SOCKS4/SOCKS5)
# PROXY_URL=http://127.0.0.1:7890
# PROXY_URL=socks5://127.0.0.1:1080

# Strategi Rotasi SSO: round_robin / least_used / least_recent / weighted / hybrid
SSO_ROTATION_STRATEGY=hybrid
SSO_DAILY_LIMIT=10
```

### 5. Jalankan Service

```bash
python main.py
```

Service akan berjalan di `http://localhost:9563`.

## Penjelasan Verifikasi Otomatis

Proyek ini mendukung penyelesaian verifikasi berikut secara otomatis:

1. **Verifikasi Usia**: Saat pertama kali menggunakan setiap SSO Token, akan secara otomatis memanggil interface verifikasi usia Grok untuk mengatur tanggal lahir (memerlukan konfigurasi `CF_CLEARANCE`)
2. **Mode NSFW**: Secara otomatis mengaktifkan dukungan pembuatan konten NSFW saat request

Status verifikasi akan di-cache (JSON lokal atau Redis), setiap SSO Token hanya perlu verifikasi sekali.

## Endpoint API

### Pembuatan Gambar

```bash
curl -X POST http://localhost:9563/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "prompt": "A beautiful sunset over mountains",
    "n": 1
  }'
```

### Chat Completions (Kompatibel OpenAI)

```bash
curl -X POST http://localhost:9563/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "grok-imagine",
    "messages": [{"role": "user", "content": "Generate a cat"}]
  }'
```

### Health Check

```bash
curl http://localhost:9563/health
```

## Penjelasan Rute

| Path | Deskripsi |
|------|-----------|
| `/` | Informasi service |
| `/docs` | Dokumentasi API Swagger |
| `/health` | Health check |
| `/gallery` | Galeri gambar |
| `/images/{filename}` | Akses gambar statis |
| `/v1/images/generations` | API pembuatan gambar |
| `/v1/chat/completions` | Chat API |
| `/admin/*` | Endpoint admin |

## Struktur Proyek

```
├── app/
│   ├── api/
│   │   ├── admin.py          # Endpoint admin
│   │   ├── chat.py           # Chat API
│   │   └── imagine.py        # API pembuatan gambar
│   ├── core/
│   │   ├── config.py         # Manajemen konfigurasi
│   │   └── logger.py         # Logging
│   └── services/
│       ├── grok_client.py    # Klien WebSocket Grok
│       ├── sso_manager.py    # Manajemen SSO
│       └── redis_sso_manager.py  # Manajemen SSO Redis
├── data/
│   └── images/               # Cache gambar
├── main.py                   # File entry
├── requirements.txt          # Dependensi
└── key.txt                   # File SSO Token
```

## Penjelasan Konfigurasi

| Environment Variable | Nilai Default | Deskripsi |
|----------------------|---------------|-----------|
| `HOST` | `0.0.0.0` | Alamat listen service |
| `PORT` | `9563` | Port service |
| `DEBUG` | `false` | Mode debug |
| `API_KEY` | - | API access key |
| `CF_CLEARANCE` | - | Cookie Cloudflare (untuk verifikasi usia) |
| `PROXY_URL` | - | Alamat proxy |
| `SSO_FILE` | `key.txt` | Path file SSO |
| `BASE_URL` | - | Alamat akses eksternal |
| `DEFAULT_ASPECT_RATIO` | `2:3` | Rasio aspek default |
| `GENERATION_TIMEOUT` | `120` | Timeout pembuatan (detik) |
| `REDIS_ENABLED` | `false` | Aktifkan Redis |
| `REDIS_URL` | `redis://localhost:6379/0` | Alamat Redis |
| `SSO_ROTATION_STRATEGY` | `hybrid` | Strategi rotasi |
| `SSO_DAILY_LIMIT` | `10` | Batas harian per Key |

## Dependensi

- Python 3.8+
- FastAPI
- uvicorn
- aiohttp + aiohttp-socks (dukungan proxy WebSocket)
- curl_cffi (simulasi browser, untuk verifikasi usia)
- pydantic
- redis (opsional)

## License

MIT
