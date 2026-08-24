# SMM reklam yayincisi

Render'da ayri calisan Telegram reklam yayincisi.

Gerekli Render ortam degiskenleri:

- `SMM_STRING_SESSION`
- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `SMM_MESSAGE`
- `SMM_TARGET_GROUPS` (virgulle ayrilmis kullanici adlari)
- `SMM_INTERVAL_MINUTES` (en az 60)
- `SMM_JOIN_BATCH_LIMIT` (varsayilan 20; bir blast turundaki toplam katilim limiti)
- `SMM_JOIN_DELAY_MIN` / `SMM_JOIN_DELAY_MAX` (varsayilan 15–30 saniye)

Ana reklam servisinde Telegram üzerinden onaylanan dinamik hedefler Firestore
`reklam/target_registry` kaydından otomatik okunur. Geçiş döneminde eski
`reklam/state.auto_groups_list` alanı da desteklenir. SMM yalnız onaylı hedefi
kendi katılım limiti ve cooldown kuralları içinde işler.

Mesaj veya hedef grup listesi yoksa servis yalnizca bekleme modunda calisir.
