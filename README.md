# SMM reklam yayincisi

Render'da ayri calisan Telegram reklam yayincisi.

Gerekli Render ortam degiskenleri:

- `SMM_STRING_SESSION`
- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `SMM_MESSAGE`
- `SMM_TARGET_GROUPS` (virgulle ayrilmis kullanici adlari)
- `SMM_INTERVAL_MINUTES` (en az 60)

Mesaj veya hedef grup listesi yoksa servis yalnizca bekleme modunda calisir.
