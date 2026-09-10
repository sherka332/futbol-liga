# ⚽ eFootball La Liga

Telegram Mini App + Flask backend.

## Fayllar
- `newfile_release.py` — backend, SQLite baza, Telegram Mini App API va bot polling.
- `index.html` — Telegram Mini App interfeysi, o'yinlar, jadval, jamoalar, profil, sovrindorlar va DM chat.
- `requirements.txt` — Python kutubxonalari.
- `.env.example` — kerakli Environment Variables namunasi.
- `render.yaml` — Render uchun tayyor konfiguratsiya.

## Render sozlash
Environment Variables:
- `BOT_TOKEN` — BotFather bergan token.
- `ADMIN_ID` — o'zingizning Telegram numeric ID'ingiz.
- `WEB_APP_URL` — Render'dagi HTTPS manzil, masalan `https://efootball-liga.onrender.com/`.

Deploy bo'lgach botga `/start` yuboring. Bot `⚽ eFootball Ligani ochish 🚀` tugmasini beradi.

## Muhim
SQLite oddiy test/deploy uchun ishlaydi. Doimiy production ma'lumotlari uchun persistent disk yoki tashqi database kerak bo'lishi mumkin.
