# TerDowngh - Self-Hosted Terabox Direct Downloader

API mandiri untuk mendapatkan direct download link (CDN/dlink) dari share link Terabox/1024TeraBox tanpa bergantung pihak ketiga API berbayar.

## Fitur

- Mendapatkan direct download link (dlink) dari Terabox share link
- Menggunakan browser cloud (Browser Use) untuk mengeksekusi JS Terabox asli (sign dihitung otomatis)
- Self-hosted, bisa deploy ke Vercel/Fly.io/Railway/dll
- Endpoint REST sederhana

## Endpoint

### GET /health
Health check

### POST /api/terabox/direct
Ambil direct download link

**Body (JSON):**
```json
{
  "url": "https://1024terabox.com/s/1pXm84UifuGeghFrOoRYN3g",
  "fs_id": "784992143151960"
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

- `BROWSER_USE_API_KEY` - API Key dari [browser-use.com](https://browser-use.com/)

## Local Development

```bash
pip install -r requirements.txt
python server.py
```

## Deploy ke Vercel

1. Push repo ke GitHub
2. Import ke Vercel
3. Set Environment Variable `BROWSER_USE_API_KEY`
4. Deploy

## License

MIT
