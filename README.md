# TerDowngh - Self-Hosted Terabox Direct Downloader

API mandiri untuk mendapatkan direct download link (CDN/dlink) dari share link Terabox/1024TeraBox.

## Fitur

- Mendapatkan direct download link (dlink) dari Terabox share link
- Menggunakan Browserless Function API (Puppeteer di cloud)
- Fallback ke public worker jika Browserless gagal
- Mendukung multi-file share (mengembalikan array `files`)
- Tidak membutuhkan input `fs_id` dari pengguna
- Self-hosted: Vercel / Fly.io / Railway / dll
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

**Response sukses:**
```json
{
  "ok": true,
  "dlink": "https://d.teraboxcdn.com/...",
  "filename": "video.ts",
  "files": [
    {
      "filename": "video.ts",
      "size": 123456789,
      "dlink": "https://d.teraboxcdn.com/..."
    }
  ]
}
```

**Response error:**
```json
{
  "error": "pesan kesalahan"
}
```

## Perbaikan (v2)

Masalah sebelumnya: endpoint Terabox `/api/shorturlinfo` sering mengembalikan non-JSON
(verifikasi anti-bot / "need verify"), sehingga Browserless gagal dengan error
`Terabox shorturlinfo bukan JSON`.

Perbaikan yang diterapkan:
1. Intercept response network dari halaman share (capture shorturlinfo / share/list / download)
2. Fallback ke endpoint `/share/list` bila shorturlinfo gagal
3. Coba beberapa origin domain Terabox (terabox.com, terabox.app, 1024tera.com, …)
4. Ekstraksi `jsToken` lebih agresif (banyak pattern)
5. Normalisasi `surl` (buang prefix `1` bila perlu)
6. Fallback ke public Cloudflare Worker bila Browserless gagal total
7. Response multi-file
8. **v2.2:** Fix `errno: 2` — kirim `sekey`/`randsk`, inject cookie `TERABOX_NDUS`

## Environment Variable

- `BROWSERLESS_TOKEN` — API token dari [Browserless](https://www.browserless.io/) **(wajib)**
- `TERABOX_NDUS` — (opsional tapi **sangat disarankan**) nilai cookie `ndus` dari akun Terabox yang sudah login. Tanpa ini, banyak share mengembalikan `errno: 2` / dlink kosong.

Cara ambil `ndus`:
1. Login di https://www.terabox.com
2. DevTools → Application → Cookies → salin value cookie `ndus`
3. Set di Vercel: `TERABOX_NDUS=YSj9BCeteHui...` (tanpa prefix `ndus=`)

Endpoint Browserless:
`https://production-sfo.browserless.io/function?token=$BROWSERLESS_TOKEN`

## Deploy ke Vercel

1. Push repo ke GitHub
2. Import project di Vercel
3. Set Environment Variable `BROWSERLESS_TOKEN` dan (disarankan) `TERABOX_NDUS`
4. Deploy

## Local Development

```bash
export BROWSERLESS_TOKEN=your_token_here
export TERABOX_NDUS=your_ndus_cookie_value
# Jalankan via Vercel CLI atau server yang memanggil api/index.py
```

## Catatan

- Link download CDN bersifat sementara (signed + expiry).
- Share yang dilindungi password / verifikasi ekstra tetap bisa gagal.
- Public fallback worker tidak dijamin selalu up.
- **errno 2** = hampir selalu butuh cookie `ndus` (set `TERABOX_NDUS`).

## License

MIT
