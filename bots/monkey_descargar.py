"""
monkey_descargar.py - Telegram Bot 2: MonkeyDescargar (Descargador de medios).
Incluye la "puerta de aceptación del Monkey": la primera vez que un usuario
envía un link, debe aceptar que el Monkey tiene la razón antes de poder descargar.
"""
import os
import re

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo

from config import MONKEY_TELEGRAM_TOKEN, IG_USERNAME, IG_COOKIES_RAW
from services.downloader import (
    descargar_media, detectar_plataforma, IL, IG_TEST_URL
)
from services.user_store import has_accepted, mark_accepted, remove_accepted

monkey_bot = telebot.TeleBot(MONKEY_TELEGRAM_TOKEN)

# Links pendientes de aceptación: {user_id: {"url": str, "chat_id": int}}
pending_links = {}

# Redes soportadas
REDES_SOPORTADAS = [
    "youtube.com", "youtu.be",
    "tiktok.com",
    "instagram.com",
    "facebook.com", "fb.watch", "fb.gg",
    "x.com", "twitter.com",
]

EMOJI_PLATAFORMA = {
    'youtube': '🎬', 'instagram': '📸', 'tiktok': '🎵',
    'twitter': '🐦', 'facebook': '📘', 'desconocida': '🔗'
}


# =============================================
# COMANDO: /monkeyperdon y /monkey_perdon
# =============================================
@monkey_bot.message_handler(commands=['monkeyperdon', 'monkey_perdon'])
def monkey_perdon(message):
    """Resetea la aceptación del usuario. La próxima vez que envíe un link,
    se le pedirá aceptar de nuevo."""
    user_id = message.from_user.id
    if has_accepted(user_id):
        remove_accepted(user_id)
        monkey_bot.reply_to(
            message,
            "🐵 Tu aceptación ha sido reseteada.\n"
            "La próxima vez que envíes un link, el Monkey te pedirá que aceptes de nuevo."
        )
    else:
        monkey_bot.reply_to(
            message,
            "🐵 Aún no has aceptado al Monkey. Envía un link para empezar."
        )


# =============================================
# COMANDO: /testig
# =============================================
@monkey_bot.message_handler(commands=['testig', 'test_ig'])
def monkey_test_ig(message):
    """Diagnostica el estado de las credenciales de Instagram."""
    chat_id = message.chat.id
    msg = monkey_bot.reply_to(message, "🔍 Diagnosticando credenciales de Instagram...")

    lineas = []
    lineas.append("🔍 **Diagnóstico de Instagram**\n")

    # 1) Cookies
    from config import IG_COOKIES_RAW as _ig_cookies
    from services.downloader import IG_COOKIES_FILE

    if _ig_cookies:
        lineas.append(f"✅ `IG_COOKIES` configurado ({len(_ig_cookies)} chars)")
        if IG_COOKIES_FILE and os.path.exists(IG_COOKIES_FILE):
            size = os.path.getsize(IG_COOKIES_FILE)
            lineas.append(f"✅ Archivo `instagram_cookies.txt` existe ({size} bytes)")
        else:
            lineas.append("❌ Archivo `instagram_cookies.txt` no se pudo escribir")
    else:
        lineas.append("❌ `IG_COOKIES` NO configurado")

    # 2) Usuario/contraseña
    from config import IG_USERNAME as _ig_user, IG_PASSWORD as _ig_pass
    if _ig_user and _ig_pass:
        lineas.append(f"✅ `IG_USERNAME` = `{_ig_user[:3]}***` configurado")
        if IL.context.is_logged_in:
            lineas.append(f"✅ instaloader logueado como `{IL.context.username}`")
        else:
            lineas.append("❌ instaloader NO está logueado (login falló: 2FA o credenciales inválidas)")
    else:
        lineas.append("❌ `IG_USERNAME`/`IG_PASSWORD` NO configurados")

    # 3) Test funcional: intentar descargar un reel público
    lineas.append("\n🧪 **Test funcional** (descargando reel público de prueba)...")
    try:
        info, archivos, err = descargar_media(IG_TEST_URL, max_reintentos=0)
        if archivos:
            for arch in archivos:
                try:
                    os.remove(arch)
                except:
                    pass
            lineas.append("✅ Reel público descargado correctamente")
            lineas.append("   → yt-dlp e instaloader funcionan con la config actual")
        elif err:
            err_low = err.lower()
            if 'not available to everyone' in err_low:
                lineas.append("❌ El test falló con `not available to everyone`")
                lineas.append("   → La cuenta/cookies no satisfacen los requisitos del post")
                if not (_ig_user and _ig_pass):
                    lineas.append("   → Solución: configura `IG_USERNAME`/`IG_PASSWORD` con una cuenta con edad verificada")
                else:
                    lineas.append("   → Solución: usa una cuenta IG con más privilegios o que siga al autor")
            elif 'login required' in err_low:
                lineas.append("❌ El test falló: Instagram requiere login")
                lineas.append("   → Las cookies no tienen `sessionid` válido o expiró")
            else:
                lineas.append(f"❌ El test falló: `{err[:150]}`")
        else:
            lineas.append("❌ El test no descargó nada (sin error explícito)")
    except Exception as e:
        lineas.append(f"❌ Excepción durante el test: `{str(e)[:150]}`")

    try:
        monkey_bot.edit_message_text("\n".join(lineas), chat_id, msg.message_id, parse_mode='Markdown')
    except Exception:
        monkey_bot.edit_message_text("\n".join(lineas).replace('*', '').replace('`', ''), chat_id, msg.message_id)


# =============================================
# CALLBACK: Botón "Lo Monkey Acepto"
# =============================================
@monkey_bot.callback_query_handler(func=lambda call: call.data == 'monkey_accept')
def callback_monkey_accept(call):
    """Se ejecuta cuando el usuario presiona el botón 'Lo Monkey Acepto 🐵'."""
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    # Marcar como aceptado en Supabase
    mark_accepted(user_id)

    # Eliminar el mensaje de aceptación
    try:
        monkey_bot.delete_message(chat_id, call.message.message_id)
    except:
        pass

    # Verificar si hay un link pendiente
    if user_id in pending_links:
        link_data = pending_links.pop(user_id)
        url = link_data["url"]

        # Confirmar aceptación y proceder a descargar
        monkey_bot.answer_callback_query(call.id, "🐵 ¡El Monkey te lo agradece!")

        # Procesar la descarga
        _procesar_descarga(chat_id, url, user_id)
    else:
        monkey_bot.answer_callback_query(call.id, "🐵 ¡Aceptado! Ahora envía un link.")
        monkey_bot.send_message(
            chat_id,
            "🐵 ¡Gracias por aceptar! Ahora puedes enviar links y te los descargo."
        )


# =============================================
# HANDLER PRINCIPAL: Mensajes con links
# =============================================
@monkey_bot.message_handler(func=lambda msg: True, content_types=['text'])
def monkey_procesar_mensaje(message):
    """Handler principal del bot descargador."""
    texto = message.text.strip()
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not any(red in texto.lower() for red in REDES_SOPORTADAS):
        return  # No es un link soportado, ignorar

    # YouTube Community Posts no son videos, yt-dlp no los soporta
    if re.search(r'youtube\.com/post/', texto.lower()):
        monkey_bot.reply_to(
            message,
            "⚠️ Ese es un **Community Post** de YouTube (texto/imágenes), no un video. "
            "Solo puedo descargar videos, shorts y reels.",
            parse_mode='Markdown'
        )
        return

    # =============================================
    # PUERTA DE ACEPTACIÓN DEL MONKEY
    # =============================================
    if not has_accepted(user_id):
        # Guardar el link pendiente
        pending_links[user_id] = {
            "url": texto,
            "chat_id": chat_id,
        }

        # Enviar mensaje de aceptación con botón
        markup = InlineKeyboardMarkup()
        btn_accept = InlineKeyboardButton("🐵 Lo Monkey Acepto", callback_data='monkey_accept')
        markup.add(btn_accept)

        monkey_bot.reply_to(
            message,
            "🐵 **¿Admites que el Monkey tiene la razón y pides perdón por todas las cosas "
            "malas que pudiste hacer así como llevarle la contraria?**\n\n"
            "Presiona el botón para continuar con la descarga.",
            reply_markup=markup,
            parse_mode='Markdown'
        )
        return

    # =============================================
    # DESCARGA NORMAL (ya aceptó)
    # =============================================
    _procesar_descarga(chat_id, texto, user_id)


# =============================================
# FUNCIÓN INTERNA: Procesar descarga
# =============================================
def _procesar_descarga(chat_id, texto, user_id):
    """Procesa la descarga de un link. Se usa tanto desde el handler principal
    como desde el callback de aceptación."""
    plataforma = detectar_plataforma(texto)
    emoji = EMOJI_PLATAFORMA.get(plataforma, '🔗')

    msg_espera = monkey_bot.send_message(
        chat_id,
        f"{emoji} Monkey Descargando de {plataforma.capitalize()} en monkey HD... dame un monkey momento."
    )

    try:
        info, archivos_nuevos, dl_error = descargar_media(texto)

        print(f"\n🔍 MONKEY ARCHIVOS DESCARGADOS: {archivos_nuevos}")
        if dl_error:
            print(f"📛 MONKEY ERROR DE DESCARGA: {dl_error}")

        if archivos_nuevos:
            _enviar_archivos(chat_id, archivos_nuevos)

            # Limpiar archivos descargados
            for arch in archivos_nuevos:
                try:
                    os.remove(arch)
                except:
                    pass

            # Borrar mensaje de "Descargando..."
            try:
                monkey_bot.delete_message(chat_id, msg_espera.message_id)
            except:
                pass

        else:
            # No se descargó nada - mostrar error amigable
            if dl_error:
                user_msg = _generar_mensaje_error(dl_error, plataforma)
                monkey_bot.edit_message_text(
                    user_msg, chat_id, msg_espera.message_id, parse_mode='Markdown'
                )
            else:
                monkey_bot.edit_message_text(
                    "❌ No se pudo descargar. El post puede ser privado o la plataforma bloqueó la descarga.",
                    chat_id, msg_espera.message_id
                )

    except Exception as e:
        error_msg = str(e)[:800]
        try:
            monkey_bot.edit_message_text(
                f"❌ Error al descargar:\n`{error_msg}`",
                chat_id, msg_espera.message_id,
                parse_mode='Markdown'
            )
        except:
            pass


def _enviar_archivos(chat_id, archivos_nuevos):
    """Envía los archivos descargados al chat."""
    if len(archivos_nuevos) == 1:
        # Un solo archivo
        archivo = archivos_nuevos[0]
        if archivo.lower().endswith('.mp4'):
            with open(archivo, 'rb') as f:
                monkey_bot.send_video(chat_id, f, supports_streaming=True)
        else:
            with open(archivo, 'rb') as f:
                monkey_bot.send_photo(chat_id, f)
    else:
        # Múltiples archivos → media group (max 10 por lote)
        for i in range(0, len(archivos_nuevos), 10):
            lote = archivos_nuevos[i:i+10]
            media_group = []
            for archivo in lote:
                if archivo.lower().endswith('.mp4'):
                    media_group.append(InputMediaVideo(open(archivo, 'rb')))
                else:
                    media_group.append(InputMediaPhoto(open(archivo, 'rb')))

            if len(media_group) == 1:
                archivo = lote[0]
                if archivo.lower().endswith('.mp4'):
                    with open(archivo, 'rb') as f:
                        monkey_bot.send_video(chat_id, f, supports_streaming=True)
                else:
                    with open(archivo, 'rb') as f:
                        monkey_bot.send_photo(chat_id, f)
            else:
                try:
                    monkey_bot.send_media_group(chat_id, media_group)
                except Exception as mg_err:
                    print(f"⚠️ Error media_group, enviando uno por uno: {mg_err}")
                    for archivo in lote:
                        try:
                            if archivo.lower().endswith('.mp4'):
                                with open(archivo, 'rb') as f:
                                    monkey_bot.send_video(chat_id, f)
                            else:
                                with open(archivo, 'rb') as f:
                                    monkey_bot.send_photo(chat_id, f)
                        except Exception as ind_err:
                            print(f"❌ Error enviando {archivo}: {ind_err}")


def _generar_mensaje_error(dl_error, plataforma):
    """Genera un mensaje de error amigable basado en el tipo de error."""
    dl_lower = dl_error.lower()

    if 'empty media response' in dl_lower or 'not available to everyone' in dl_lower or 'login required' in dl_lower:
        if not IG_USERNAME and not IG_COOKIES_RAW:
            return (
                "❌ **Contenido Restringido en Instagram**\n\n"
                "Este post requiere inicio de sesión para verse (NSFW o restricción de edad).\n"
                "El administrador debe configurar las credenciales de Instagram (`IG_COOKIES` o `IG_USERNAME`).\n\n"
                "Usa /testig para verificar el estado de las credenciales."
            )
        elif 'not available to everyone' in dl_lower and IL.context.is_logged_in:
            return (
                "❌ **Tu cuenta de Instagram no tiene acceso a este post**\n\n"
                "El bot está autenticado correctamente, pero la cuenta configurada no cumple los requisitos para ver este contenido:\n"
                "• El post puede ser de una cuenta que la cuenta IG no sigue\n"
                "• El post puede tener restricción de edad/región que la cuenta IG no supera\n\n"
                "Configura una cuenta con edad verificada en `IG_USERNAME`/`IG_PASSWORD`."
            )
        else:
            return (
                "❌ **Error de Autenticación en Instagram**\n\n"
                "Las credenciales actuales de Instagram parecen haber expirado o son inválidas.\n"
                "Por favor, actualiza las cookies (`IG_COOKIES`) y verifica `IG_USERNAME`/`IG_PASSWORD`.\n\n"
                "Usa /testig para diagnosticar el estado de las credenciales."
            )
    elif 'no video in this post' in dl_lower or 'no video formats found' in dl_lower:
        if plataforma == 'instagram':
            return (
                "❌ **No se encontró video en el post de Instagram**\n\n"
                "Este post parece contener solo imágenes o un formato no compatible. "
                "Si es un carrusel público, debería haberse descargado automáticamente. "
                "Si no fue así, el post puede ser privado o las credenciales/cookies no tienen acceso."
            )
        else:
            return (
                "❌ **No se encontró video en este post**\n\n"
                "La URL apunta a un post que no contiene videos descargables "
                "(puede ser un carrusel de imágenes o un formato no soportado por esta plataforma)."
            )
    elif 'bad guest token' in dl_lower or ('twitter' in dl_lower and 'api' in dl_lower):
        return (
            "❌ Twitter/X requiere autenticación para descargar.\n"
            "El administrador necesita configurar las cookies de Twitter."
        )
    elif 'unsupported url' in dl_lower and 'tiktok' in dl_lower:
        return (
            "❌ Esta URL de TikTok no es compatible.\n"
            "Verifica que el link sea válido y que el post no haya sido eliminado."
        )
    elif 'timeout' in dl_lower or 'connection' in dl_lower or 'timed out' in dl_lower:
        return (
            "❌ Error de conexión.\n"
            "El servidor tardó demasiado en responder.\n"
            "Intenta enviar el link de nuevo en unos minutos."
        )
    elif 'requested format is not available' in dl_lower:
        return (
            "❌ No se pudo descargar este video en ningún formato disponible.\n"
            "Puede ser una restricción regional o del contenido."
        )
    else:
        short_err = dl_error[:300]
        return f"❌ Error al descargar:\n`{short_err}`"
