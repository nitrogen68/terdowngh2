# TerDowngh - Self-Hosted Terabox Direct Downloader

API mandiri untuk mendapatkan direct download link (dlink) dari Terabox share link menggunakan Browser Use.

## Fitur

- Input hanya share link Terabox.
- Tidak membutuhkan `fs_id` dari pengguna.
- Browser Use menerima payload minimal sesuai schema: `{ "task": "..." }`.
- UI sederhana, clean, dan tetap memiliki animasi loading.
- Endpoint REST sederhana.

## Endpoint

### GET /health

Health check.

### POST /api/terabox/direct

Ambil direct download link dari share link Terabox.

Request:

```json
{
  "url": "https://1024terabox.com/s/1pXm84UifuGeghFrOoRYN3g"
}
```

Response:

```json
{
  "dlink": "https://d.teraboxcdn.com/...",
  "ok": true
}
```

## Environment Variable

Set `BROWSER_USE_API_KEY` pada environment Vercel. Jangan menyimpan API key di source code.

## Deploy

Import repository ke Vercel dan set environment variable `BROWSER_USE_API_KEY`, lalu deploy.
