# TerDowngh - Self-Hosted Terabox Direct Downloader

API mandiri untuk mendapatkan direct download link (CDN/dlink) dari share link Terabox/1024TeraBox.

## Fitur

- Mendapatkan direct download link (dlink) dari Terabox share link
- Menggunakan Browserless Function API untuk menjalankan Puppeteer di browser cloud
- Tidak membutuhkan input `fs_id` dari pengguna
- Self-hosted, bisa deploy ke Vercel/Fly.io/Railway/dll
- Endpoint REST sederhana

## Endpoint

### GET /health
Health check.

### POST /api/terabox/direct
Ambil direct download link dari share link Terabox.

**Body (JSON):**
```json
{
  "url": "https://1024terabox.com/s/1pXm84UifuGeghFrOoRYN3g"
}
```

**Response:**
```json
{
  "dlink": "https://d.teraboxcdn.com/...",
  "ok": true
}
```

## Environment Variable

- `BROWSERLESS_TOKEN` - API token dari Browserless

Browserless Function API endpoint yang digunakan:

`https://production-sfo.browserless.io/function?token=$BROWSERLESS_TOKEN`

## Local Development

```bash
pip install -r requirements.txt
python server.py
```

## Deploy ke Vercel

1. Push repo ke GitHub
2. Import ke Vercel
3. Set Environment Variable `BROWSERLESS_TOKEN`
4. Deploy

## License

MIT
