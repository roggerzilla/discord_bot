# Bot Quotly / Monkey stickers (Bot 4)

Portado desde el bot en Node (`quotly-clone`) a Python, integrado en este proyecto
multi-bot. Comandos: `/mq` (cita → sticker), `/pack` (creador de packs) y
`/monkey_steal` (robar sticker a tu pack).

## Puesta en marcha

### 1. Tabla en Supabase
Ejecutá el SQL de `quotly_packs_table.sql` en el SQL Editor de Supabase (crea la
tabla `quotly_packs` donde se registran los packs de cada usuario).

### 2. Variable de entorno (Render → Environment)
```
MONKEY_QUOTLY_TOKEN=<token de tu bot>
```
Podés **reusar el mismo token** del bot en Node (`@monkeyquoubot`).
⚠️ Si reusás ese token, **apagá el bot viejo en Node**: dos procesos con el mismo
token dan error `409 Conflict`.

### 3. Modo privacidad en BotFather
Para que `/mq` sin responder (y la caché de mensajes) funcione en grupos:
`@BotFather → /setprivacy → <tu bot> → Disable`.
(Respondiendo a un mensaje o sticker funciona igual con privacidad activada, porque
los comandos y su contexto de reply siempre se entregan.)

### 4. Deploy
`git push` a la rama conectada a Render. Se instala solo lo nuevo de
`requirements.txt` (Pillow, pilmoji, imageio-ffmpeg) y el bot arranca en su hilo
desde `main.py`.

## Notas técnicas
- **Render sin la burbuja del navegador**: la cita se dibuja con Pillow + pilmoji
  (emojis a color), sin Chromium/Puppeteer. Fuente empaquetada en `assets/fonts/`.
- **ffmpeg**: los stickers de video (`/pack` con un video) usan el binario que trae
  `imageio-ffmpeg`, así que no hace falta instalarlo por apt. Robar un sticker de
  video ya existente no necesita ffmpeg.
- **Persistencia**: el registro de packs vive en Supabase (`quotly_packs`), así
  sobrevive a los deploys/reinicios del filesystem efímero de Render.
- `/monkey_steal` usa nombres de pack deterministas (`ms_<uid>_<formato>[_n]_by_bot`)
  y consulta a Telegram si ya existen, así que nunca duplica packs aunque se resetee.

## Archivos
- `bots/monkey_quotly.py` — el bot (handlers).
- `services/quotly_render.py` — render de la burbuja con Pillow.
- `services/quotly_store.py` — registro de packs sobre Supabase.
- `assets/fonts/DejaVuSans*.ttf` — fuente empaquetada.
- `quotly_packs_table.sql` — esquema de la tabla.
