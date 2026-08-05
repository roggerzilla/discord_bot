"""
monkey_quotly.py - Telegram Bot: clon de Quotly (citas → stickers) + creador de packs.

Portado desde el bot original en Node (quotly-clone). Funciones:
  /mq          → cita el mensaje respondido como sticker (render con Pillow).
  /pack        → crea packs de stickers desde fotos/videos (privado).
  /monkey_steal→ en grupos, respondé a un sticker y lo guarda en tu pack automático.

Persistencia de packs en Supabase (services/quotly_store.py).
"""
import os
import time
import secrets
import tempfile
import subprocess
from io import BytesIO
from collections import OrderedDict

import telebot
from telebot import types

from config import MONKEY_QUOTLY_TOKEN
from services import quotly_store as store
from services import quotly_render as render

# Placeholder con ':' válido para telebot>=4.36 (valida el token al construir).
# main.py no arranca el polling si MONKEY_QUOTLY_TOKEN no está configurado.
bot = telebot.TeleBot(MONKEY_QUOTLY_TOKEN or "0:disabled")

MAX_TEXT = 300
STEAL_LIMIT = 120

# ---- Estado en memoria ----
_sticker_store = {}                 # key -> {file_id, ts}   (para el botón Guardar de /mq)
_pack_sessions = {}                 # user_id -> dict de sesión de /pack
_msg_cache = {}                     # chat_id -> OrderedDict(message_id -> entry)

HELP_TEXT = (
    "👋 *¿Qué puedo hacer?*\n\n"
    "💬 */mq* — respondé a un mensaje para convertirlo en sticker con su cita.\n"
    "   (usá `/mq r 2` para incluir varios mensajes)\n\n"
    "🎨 */pack* — mandame fotos o videos por acá y te armo un pack de stickers.\n\n"
    "🫳 */monkey\\_steal* — en cualquier grupo, respondé a un sticker con esto y lo "
    "guardo en uno de tus packs con un toque."
)


# =====================================================================
#  Helpers generales
# =====================================================================
def _is_peer_error(msg=""):
    m = (msg or "").upper()
    return any(x in m for x in ("PEER_ID_INVALID", "CHAT NOT FOUND", "BOT WAS BLOCKED",
                                "FORBIDDEN", "USER_IS_BLOCKED", "CAN'T INITIATE"))


def _is_full_error(msg=""):
    m = (msg or "").upper()
    return any(x in m for x in ("TOO_MUCH", "TOO MUCH", "STICKERS_TOO_MUCH", "MAXIMUM"))


def _download(file_id):
    f = bot.get_file(file_id)
    return bot.download_file(f.file_path)


def _sticker_field_format(st):
    if st.is_video:
        return "video"
    if st.is_animated:
        return "animated"
    return "static"


def _input_sticker(value, emoji, fmt):
    """value: str file_id (estáticos) o bytes (video/animado). Devuelve InputSticker."""
    ext = {"video": "webm", "animated": "tgs"}.get(fmt, "png")
    if isinstance(value, (bytes, bytearray)):
        sticker = types.InputFile(BytesIO(value), file_name=f"sticker.{ext}")
    else:
        sticker = value
    return types.InputSticker(sticker=sticker, emoji_list=[emoji], format=fmt)


def _create_set(user_id, name, title, value, emoji, fmt):
    bot.create_new_sticker_set(user_id, name, title, stickers=[_input_sticker(value, emoji, fmt)])


def _add_to_set(user_id, name, value, emoji, fmt):
    # telebot 4.36 exige `emojis` posicional aunque uses `sticker=InputSticker`
    # (lo ignora cuando `sticker` está presente, pero es obligatorio en la firma).
    bot.add_sticker_to_set(user_id, name, emoji, sticker=_input_sticker(value, emoji, fmt))


def _set_size(name):
    try:
        s = bot.get_sticker_set(name)
        return len(s.stickers)
    except Exception:
        return None


def _bot_username():
    return bot.get_me().username


# =====================================================================
#  Teclados
# =====================================================================
def quick_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(types.KeyboardButton("🎨 Crear pack"), types.KeyboardButton("📋 Mis packs"))
    kb.row(types.KeyboardButton("❓ Ayuda"))
    return kb


def main_menu():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✨ Crear pack nuevo", callback_data="pk:new"))
    kb.add(types.InlineKeyboardButton("📦 Agregar a un pack", callback_data="pk:add"))
    kb.add(types.InlineKeyboardButton("📋 Mis packs", callback_data="pk:mypacks"))
    return kb


def emoji_grid():
    kb = types.InlineKeyboardMarkup()
    for row in (["😂", "😍", "🔥", "❤️", "👍"], ["😭", "🥺", "💀", "✨", "😈"], ["🤨", "👀", "💅", "🗿", "🙏"]):
        kb.row(*[types.InlineKeyboardButton(e, callback_data=f"pk:e:{e}") for e in row])
    kb.add(types.InlineKeyboardButton("✏️ Escribir otro", callback_data="pk:e:type"))
    return kb


def more_or_done():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Agregar otro", callback_data="pk:more"))
    kb.add(types.InlineKeyboardButton("✏️ Cambiar título", callback_data="pk:rename"))
    kb.add(types.InlineKeyboardButton("✅ Finalizar", callback_data="pk:done"))
    return kb


PACK_PAGE = 6


def pack_list_keyboard(user_id, page, mode):
    packs = store.get_packs(user_id)
    total = max(1, (len(packs) + PACK_PAGE - 1) // PACK_PAGE)
    p = min(max(0, page), total - 1)
    chunk = packs[p * PACK_PAGE:p * PACK_PAGE + PACK_PAGE]
    item = "view" if mode == "view" else "pick"
    pg = "vpg" if mode == "view" else "pg"

    kb = types.InlineKeyboardMarkup()
    for i in range(0, len(chunk), 2):
        row = []
        for j in range(i, min(i + 2, len(chunk))):
            gi = p * PACK_PAGE + j
            row.append(types.InlineKeyboardButton(f"📦 {chunk[j]['title']}"[:30], callback_data=f"pk:{item}:{gi}"))
        kb.row(*row)

    nav = []
    if p > 0:
        nav.append(types.InlineKeyboardButton("◀️", callback_data=f"pk:{pg}:{p - 1}"))
    if total > 1:
        nav.append(types.InlineKeyboardButton(f"{p + 1}/{total}", callback_data="pk:noop"))
    if p < total - 1:
        nav.append(types.InlineKeyboardButton("▶️", callback_data=f"pk:{pg}:{p + 1}"))
    if nav:
        kb.row(*nav)

    if mode == "pick":
        kb.add(types.InlineKeyboardButton("✏️ Escribir nombre", callback_data="pk:type"))
    return kb


def pack_detail_keyboard(idx):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✏️ Cambiar título", callback_data=f"pk:rn:{idx}"))
    kb.add(types.InlineKeyboardButton("➕ Agregar stickers", callback_data=f"pk:pick:{idx}"))
    kb.add(types.InlineKeyboardButton("⬅️ Volver", callback_data="pk:mypacks"))
    return kb


def _private_markup(message, markup):
    """Adjunta teclados únicamente en chats privados."""
    return {"reply_markup": markup} if message.chat.type == "private" else {}


def _private_reply_keyboard(message):
    """Muestra el teclado persistente solo en privado y lo quita en grupos."""
    return quick_keyboard() if message.chat.type == "private" else types.ReplyKeyboardRemove()


def fmt_label(f):
    return {"static": "estático", "animated": "animado", "video": "video"}.get(f, f)


# =====================================================================
#  Caché de mensajes (para /mq)
# =====================================================================
def _entry_from_msg(m):
    fu = getattr(m, "from_user", None)
    sc = getattr(m, "sender_chat", None)
    uid = fu.id if fu else (sc.id if sc else 0)
    return {
        "message_id": m.message_id,
        "user_id": uid,
        "first_name": fu.first_name if fu else None,
        "last_name": fu.last_name if fu else None,
        "chat_title": sc.title if sc else None,
        "text": (m.text or m.caption or ""),
        "reply_to_id": m.reply_to_message.message_id if m.reply_to_message else None,
    }


def _cache_message(m):
    if not m.chat:
        return
    cache = _msg_cache.setdefault(m.chat.id, OrderedDict())
    cache[m.message_id] = _entry_from_msg(m)
    if m.reply_to_message:
        cache.setdefault(m.reply_to_message.message_id, _entry_from_msg(m.reply_to_message))
    while len(cache) > 200:
        cache.popitem(last=False)


def _display_name(e):
    if e.get("first_name"):
        return " ".join(x for x in [e["first_name"], e.get("last_name")] if x)
    return e.get("chat_title") or "Usuario"


def _avatar_bytes(user_id):
    if not user_id or user_id < 0:
        return None
    try:
        photos = bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count == 0:
            return None
        return _download(photos.photos[0][0].file_id)
    except Exception:
        return None


def _custom_title(chat_id, user_id, chat_type):
    if chat_type == "private" or not user_id or user_id < 0:
        return ""
    try:
        mem = bot.get_chat_member(chat_id, user_id)
        return getattr(mem, "custom_title", "") or ""
    except Exception:
        return ""


# =====================================================================
#  /start y ayuda
# =====================================================================
@bot.message_handler(commands=["start", "help"])
def handle_start(message):
    bot.send_message(message.chat.id, HELP_TEXT, parse_mode="Markdown",
                     reply_markup=_private_reply_keyboard(message))


# =====================================================================
#  /mq  (cita → sticker)
# =====================================================================
@bot.message_handler(commands=["mq"])
def handle_mq(message):
    reply = message.reply_to_message
    chat_cache = _msg_cache.get(message.chat.id, OrderedDict())

    if reply:
        primary = _entry_from_msg(reply)
    else:
        # sin reply: el mensaje reciente de otra persona
        primary = None
        cmd_uid = message.from_user.id if message.from_user else 0
        for mid in reversed(chat_cache):
            e = chat_cache[mid]
            if e["message_id"] == message.message_id:
                continue
            if e["user_id"] and e["user_id"] != cmd_uid:
                primary = e
                break
        if not primary:
            bot.reply_to(message, "ℹ️ Respondé a un mensaje con /mq para citarlo.")
            return

    parts = (message.text or "").split()
    depth = 0
    if "r" in parts:
        i = parts.index("r")
        if i + 1 < len(parts) and parts[i + 1].isdigit():
            depth = int(parts[i + 1])

    entries = [primary]
    current = primary
    for _ in range(depth):
        pid = current.get("reply_to_id")
        parent = chat_cache.get(pid) if pid else None
        if not parent:
            break
        entries.insert(0, parent)
        current = parent

    chat_type = message.chat.type
    messages = []
    for e in entries:
        uid = e["user_id"]
        messages.append({
            "user_id": uid,
            "name": _display_name(e),
            "text": (e["text"] or "")[:MAX_TEXT],
            "avatar": _avatar_bytes(uid),
            "title": _custom_title(message.chat.id, uid, chat_type),
        })

    try:
        png = render.render_quote(messages)
        webp = render.to_sticker_webp(png)
    except Exception as e:
        bot.reply_to(message, f"❌ No pude generar el sticker: {e}")
        return

    key = secrets.token_urlsafe(6)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Guardar en mis stickers ❤️", callback_data=f"save:{key}"))
    sent = bot.send_sticker(message.chat.id, types.InputFile(BytesIO(webp), "quote.webp"),
                            **_private_markup(message, kb))
    _sticker_store[key] = {"file_id": sent.sticker.file_id, "ts": time.time()}
    _gc_sticker_store()


def _gc_sticker_store():
    if len(_sticker_store) <= 500:
        return
    for k in sorted(_sticker_store, key=lambda k: _sticker_store[k]["ts"])[:100]:
        _sticker_store.pop(k, None)


# =====================================================================
#  Guardar (callback save:)
# =====================================================================
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("save:"))
def handle_save(call):
    key = call.data[5:]
    info = _sticker_store.get(key)
    if not info:
        bot.answer_callback_query(call.id, "❌ Este sticker ya no está disponible.", show_alert=True)
        return

    user_id = call.from_user.id
    username = _bot_username()
    set_name = f"u_{user_id}_by_{username}"
    need_start = f"Primero escribime por privado (@{username}) y volvé a tocar Guardar 🙏"

    exists = _set_size(set_name) is not None
    try:
        if exists:
            _add_to_set(user_id, set_name, info["file_id"], "❤️", "static")
        else:
            _create_set(user_id, set_name, "Mis stickers guardados", info["file_id"], "❤️", "static")
    except Exception as e:
        if _is_peer_error(str(e)):
            bot.answer_callback_query(call.id, need_start, show_alert=True)
        else:
            bot.answer_callback_query(call.id, f"❌ Error: {e}", show_alert=True)
        return

    store.add_pack(user_id, set_name, "Mis stickers guardados", "static")
    bot.answer_callback_query(call.id, "✅ Guardado en tu pack personal.", show_alert=True)

    # Link por privado (no ensucia el grupo). Si no inició el bot, se ignora.
    try:
        bot.send_message(user_id, f"✅ Sticker guardado.\n📦 Tu pack: https://t.me/addstickers/{set_name}",
                         disable_web_page_preview=True)
    except Exception:
        pass

    try:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("✅ Guardado en mis stickers", callback_data="pk:noop"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=kb)
    except Exception:
        pass
    _sticker_store.pop(key, None)


# =====================================================================
#  /pack  (creador de packs)
# =====================================================================
S_IDLE = "idle"
S_FILE = "file"
S_EMOJI = "emoji"
S_EXISTING = "existing_name"
S_MORE = "more_or_done"
S_RENAME = "rename"
S_NEW_TITLE = "new_title"


def _session(user_id):
    s = _pack_sessions.get(user_id)
    if not s:
        s = {"user_id": user_id, "state": S_IDLE, "count": 0, "pack_name": "",
             "pack_title": "", "temp": None, "format": "static"}
        _pack_sessions[user_id] = s
    return s


def _default_title(from_user):
    who = (from_user.first_name if from_user else None) or "mis stickers"
    return f"Stickers de {who}"[:64]


@bot.message_handler(commands=["pack"])
def handle_pack(message):
    start_pack(message)


def start_pack(message):
    if message.chat.type != "private":
        bot.reply_to(message, "✋ Los packs se crean por privado. Escribime a mí directamente.\n\n"
                              "En grupos: respondé a un sticker con /monkey_steal para guardarlo.")
        return
    s = _session(message.from_user.id)
    s.update(state=S_IDLE, count=0, pack_name="", pack_title="", temp=None, format="static")
    bot.send_message(message.chat.id, "🎨 *Creador de Packs de Stickers*\n\nElegí una opción:",
                     parse_mode="Markdown", reply_markup=main_menu())


def show_my_packs(message):
    if message.chat.type != "private":
        return start_pack(message)
    packs = store.get_packs(message.from_user.id)
    if not packs:
        bot.send_message(message.chat.id, "📭 Todavía no tenés packs. Creá uno con 🎨 *Crear pack*.",
                         parse_mode="Markdown", reply_markup=quick_keyboard())
        return
    bot.send_message(message.chat.id, "📋 *Tus packs* — tocá uno para ver opciones:",
                     parse_mode="Markdown", reply_markup=pack_list_keyboard(message.from_user.id, 0, "view"))


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("pk:"))
def handle_pack_callback(call):
    data = call.data
    s = _session(call.from_user.id)
    uid = call.from_user.id
    bot.answer_callback_query(call.id)

    def edit(text, **kw):
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, **kw)
        except Exception:
            pass

    if data == "pk:noop":
        return
    if data == "pk:cancel":
        _pack_sessions.pop(uid, None)
        return edit("❌ Cancelado.")

    if data == "pk:new":
        s.update(state=S_NEW_TITLE, count=0, pack_name="", pack_title="", temp=None, format="static")
        deft = _default_title(call.from_user)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(f'✅ Usar "{deft}"', callback_data="pk:deftitle"))
        return edit("✏️ *¿Qué título le ponés?*\n(Es lo que ve la gente al abrir el pack.)\n\n"
                    "Escribime uno, o usá el de siempre:", parse_mode="Markdown", reply_markup=kb)

    if data == "pk:deftitle":
        s["pack_title"] = _default_title(call.from_user)
        s["state"] = S_FILE
        return edit(f'📸 Título: *{s["pack_title"]}*\n\nAhora mandame el primer sticker, foto o video.\n'
                    "El formato se detecta solo. 🙂", parse_mode="Markdown")

    if data == "pk:mypacks":
        packs = store.get_packs(uid)
        if not packs:
            return edit("📭 Todavía no tenés packs. Creá uno con ✨ *Crear pack nuevo*.", parse_mode="Markdown")
        return edit("📋 *Tus packs* — tocá uno para ver opciones:", parse_mode="Markdown",
                    reply_markup=pack_list_keyboard(uid, 0, "view"))

    if data.startswith("pk:vpg:"):
        page = int(data[7:] or 0)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                          reply_markup=pack_list_keyboard(uid, page, "view"))
        except Exception:
            pass
        return

    if data.startswith("pk:view:"):
        packs = store.get_packs(uid)
        idx = int(data[8:])
        if idx >= len(packs):
            return edit("❌ Ese pack ya no está disponible.")
        pk = packs[idx]
        return edit(f"📦 {pk['title']} ({fmt_label(pk.get('format', 'static'))})\n"
                    f"https://t.me/addstickers/{pk['name']}",
                    disable_web_page_preview=True, reply_markup=pack_detail_keyboard(idx))

    if data.startswith("pk:rn:"):
        packs = store.get_packs(uid)
        idx = int(data[6:])
        if idx >= len(packs):
            return edit("❌ Ese pack ya no está disponible.")
        pk = packs[idx]
        s.update(pack_name=pk["name"], pack_title=pk["title"], format=pk.get("format", "static"), state=S_RENAME)
        return edit(f"✏️ Mandame el *nuevo título* para *{pk['title']}*. Máx 64 caracteres.", parse_mode="Markdown")

    if data == "pk:add":
        packs = store.get_packs(uid)
        if not packs:
            s["state"] = S_EXISTING
            return edit("📦 No tengo registrados packs tuyos. Mandame el *nombre* del pack:\n\n"
                        "`mis_stickers_by_monkeyquobot`", parse_mode="Markdown")
        return edit("📦 Elegí a qué pack agregar:", reply_markup=pack_list_keyboard(uid, 0, "pick"))

    if data == "pk:type":
        s["state"] = S_EXISTING
        return edit("📦 Mandame el *nombre* del pack existente.\n\nEjemplo:\n`mis_stickers_by_monkeyquobot`",
                    parse_mode="Markdown")

    if data.startswith("pk:pg:"):
        page = int(data[6:] or 0)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                          reply_markup=pack_list_keyboard(uid, page, "pick"))
        except Exception:
            pass
        return

    if data.startswith("pk:pick:"):
        packs = store.get_packs(uid)
        idx = int(data[8:])
        if idx >= len(packs):
            return edit("❌ Ese pack ya no está disponible.")
        pk = packs[idx]
        s.update(pack_name=pk["name"], format=pk.get("format", "static"), count=0, state=S_FILE)
        return edit(f"📦 *{pk['title']}* ({fmt_label(s['format'])})\n\nMandame el sticker o imagen que querés agregar:",
                    parse_mode="Markdown")

    if data.startswith("pk:e:"):
        raw = data[5:]
        if raw == "type":
            s["state"] = S_EMOJI if s.get("temp") is not None else S_FILE
            return edit("✏️ *Escribí el emoji* que quieras usar:", parse_mode="Markdown")
        return _process_emoji(call.message.chat.id, s, raw, from_user=call.from_user, via_call=call)

    if data == "pk:more":
        s["state"] = S_FILE
        return edit("📸 *Mandame el siguiente sticker o imagen:*", parse_mode="Markdown")

    if data == "pk:rename":
        if not s.get("pack_name"):
            return
        s["state"] = S_RENAME
        return edit(f"✏️ Mandame el *nuevo título* del pack. Máx 64 caracteres.\n\nActual: *{s.get('pack_title', '')}*",
                    parse_mode="Markdown")

    if data == "pk:done":
        name = s.get("pack_name")
        _pack_sessions.pop(uid, None)
        return edit(f"✅ ¡Pack listo!\n\n📦 https://t.me/addstickers/{name}", disable_web_page_preview=True)


def _process_emoji(chat_id, s, emoji, from_user, via_call=None):
    value = s.get("temp")
    s["temp"] = None
    s["count"] = s.get("count", 0) + 1
    fmt = s["format"]

    def say(text, **kw):
        if via_call:
            try:
                bot.edit_message_text(text, chat_id, via_call.message.message_id, **kw)
                return
            except Exception:
                pass
        bot.send_message(chat_id, text, **kw)

    # Pack nuevo
    if not s.get("pack_name"):
        username = _bot_username()
        name = f"p{secrets.token_hex(4)}_by_{username}"
        title = (s.get("pack_title") or _default_title(from_user))[:64]
        try:
            _create_set(s["user_id"], name, title, value, emoji, fmt)
        except Exception as e:
            if _is_peer_error(str(e)):
                return say("✋ Primero escribime /start por privado y volvé a intentar.")
            return say(f"❌ Error al crear el pack: {e}")
        s["pack_name"] = name
        s["pack_title"] = title
        store.add_pack(s["user_id"], name, title, fmt)
        s["state"] = S_MORE
        return say(f'✅ ¡Pack creado! "{title}"\nhttps://t.me/addstickers/{name}\n\n'
                   "Podés agregar más stickers o cambiar el título 👇",
                   reply_markup=more_or_done(), disable_web_page_preview=True)

    # Agregar a existente
    try:
        _add_to_set(s["user_id"], s["pack_name"], value, emoji, fmt)
    except Exception as e:
        return say(f"❌ Error al agregar: {e}")
    s["state"] = S_MORE
    return say(f"✅ ¡Sticker agregado con {emoji}!", reply_markup=more_or_done())


def _to_webp_static(buf):
    from PIL import Image
    img = Image.open(BytesIO(buf)).convert("RGBA")
    w, h = img.size
    scale = 512 / max(w, h)  # Telegram exige un lado = 512
    img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    out = BytesIO()
    img.save(out, format="WEBP", quality=90)
    return out.getvalue()


def _ffmpeg_exe():
    """Binario de ffmpeg. Usa el que trae imageio-ffmpeg (sirve en Render nativo)."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _to_webm_video(buf):
    tmpdir = tempfile.mkdtemp(prefix="qsticker-")
    inp = os.path.join(tmpdir, "in.mp4")
    outp = os.path.join(tmpdir, "out.webm")
    with open(inp, "wb") as f:
        f.write(buf)
    cmd = [
        _ffmpeg_exe(), "-i", inp,
        "-vf", "scale=512:512:force_original_aspect_ratio=decrease,pad=512:512:(ow-iw)/2:(oh-ih)/2",
        "-t", "3", "-c:v", "libvpx-vp9", "-b:v", "200k", "-maxrate", "200k", "-bufsize", "400k",
        "-an", "-y", outp,
    ]
    subprocess.run(cmd, timeout=25, check=True, capture_output=True)
    with open(outp, "rb") as f:
        data = f.read()
    try:
        for fn in (inp, outp):
            os.remove(fn)
        os.rmdir(tmpdir)
    except Exception:
        pass
    return data


def _pack_incoming_format(m):
    if m.content_type == "sticker":
        return _sticker_field_format(m.sticker)
    if m.content_type in ("photo",) or (m.content_type == "document" and (m.document.mime_type or "").startswith("image/")):
        return "static"
    if m.content_type in ("video", "animation", "video_note"):
        return "video"
    return None


def _handle_pack_message(message):
    s = _session(message.from_user.id)
    st = s["state"]

    if st == S_NEW_TITLE:
        title = (message.text or "").strip()
        if not title or len(title) > 64:
            return bot.reply_to(message, "❌ El título debe tener entre 1 y 64 caracteres. Probá de nuevo:")
        s["pack_title"] = title
        s["state"] = S_FILE
        return bot.send_message(message.chat.id, f"📸 Título: *{title}*\n\nAhora mandame el primer sticker, foto o video.",
                                parse_mode="Markdown")

    if st == S_RENAME:
        title = (message.text or "").strip()
        if not title or len(title) > 64:
            return bot.reply_to(message, "❌ El título debe tener entre 1 y 64 caracteres. Probá de nuevo:")
        try:
            bot.set_sticker_set_title(s["pack_name"], title)
        except Exception as e:
            return bot.reply_to(message, f"❌ No pude cambiar el título: {e}")
        s["pack_title"] = title
        store.update_title(s["pack_name"], title)
        s["state"] = S_MORE
        return bot.send_message(message.chat.id, f'✅ Título cambiado a "{title}"', reply_markup=more_or_done())

    if st == S_EXISTING:
        name = (message.text or "").strip()
        if not name or not name.replace("_by_", "").replace("_", "").isalnum() or not name[0].isalpha():
            return bot.reply_to(message, "❌ Debe empezar con letra. Ejemplo: `mis_stickers_by_monkeyquobot`",
                                parse_mode="Markdown")
        username = _bot_username()
        s["pack_name"] = name if "_by_" in name else f"{name}_by_{username}"
        s["count"] = 0
        s["state"] = S_FILE
        return bot.send_message(message.chat.id, f"✅ Pack: `{s['pack_name']}`\n\nAhora mandame el sticker o imagen:",
                                parse_mode="Markdown")

    if st == S_EMOJI:
        emoji = (message.text or "").strip()
        if not emoji:
            return bot.reply_to(message, "❌ Mandame un emoji válido.")
        return _process_emoji(message.chat.id, s, emoji, from_user=message.from_user)

    if st == S_FILE:
        incoming = _pack_incoming_format(message)
        if incoming is None:
            return bot.reply_to(message, "❌ Mandame un sticker, foto, video o imagen.")
        # coherencia de formato al agregar a existente / mezclar
        if s.get("pack_name") and incoming != s["format"]:
            return bot.reply_to(message, f"❌ Este pack es {fmt_label(s['format'])}. Mandame un archivo {fmt_label(s['format'])}.")
        if s["count"] == 0 and not s.get("pack_name"):
            s["format"] = incoming
        elif incoming != s["format"]:
            return bot.reply_to(message, f"❌ El pack es de tipo {fmt_label(s['format'])}. No podés mezclar tipos.")

        try:
            value = _extract_sticker_value(message, incoming)
        except Exception as e:
            return bot.reply_to(message, f"❌ Error al procesar el archivo: {e}")

        s["temp"] = value
        s["state"] = S_EMOJI
        return bot.send_message(message.chat.id, "✅ Recibido. Elegí un emoji:", reply_markup=emoji_grid())


def _extract_sticker_value(message, fmt):
    """Devuelve file_id (estático) o bytes (imagen procesada / video convertido)."""
    ct = message.content_type
    if ct == "sticker":
        st = message.sticker
        if fmt == "static":
            return st.file_id
        return _download(st.file_id)  # video/animado → bytes (InputFile)
    if ct == "photo":
        return _to_webp_static(_download(message.photo[-1].file_id))
    if ct == "document":
        return _to_webp_static(_download(message.document.file_id))
    if ct in ("video", "animation", "video_note"):
        src = message.video or message.animation or message.video_note
        return _to_webm_video(_download(src.file_id))
    raise ValueError("tipo no soportado")


# =====================================================================
#  /monkey_steal
# =====================================================================
def _steal_title(n):
    return "monkey_steal" if n <= 1 else f"monkey_steal {n}"


def _unified_name(user_id, n, botuser):
    """Pack único del usuario: mezcla imágenes, videos y animados."""
    suffix = "" if n <= 1 else f"_{n}"
    return f"ms_{user_id}{suffix}_by_{botuser}"


def _legacy_name(user_id, fmt, n, botuser):
    """Serie vieja separada por formato (se sigue reusando para no dejarla huérfana)."""
    f = fmt[0]  # s / v / a
    suffix = "" if n <= 1 else f"_{n}"
    return f"ms_{user_id}_{f}{suffix}_by_{botuser}"


def _is_format_error(msg=""):
    m = (msg or "").upper()
    return "FORMAT" in m or "MISMATCH" in m


def _steal_source(message_sticker, fmt):
    if fmt == "static":
        return message_sticker.file_id
    return _download(message_sticker.file_id)


def _resolve_steal_pack(user_id, botuser, fmt=None):
    """Dónde guardar el sticker robado.

    fmt=None  → pack unificado (todo junto). Reusa primero cualquier pack de robo
                que ya exista con lugar, para no crear packs de más.
    fmt=...   → serie separada por formato (fallback si Telegram no deja mezclar).
    """
    candidates = []
    if fmt is None:
        for p in store.get_packs(user_id):
            if p.get("steal"):
                candidates.append((p["name"], p.get("title") or _steal_title(1)))
        candidates.append((_unified_name(user_id, 1, botuser), _steal_title(1)))
        # packs de la serie vieja por formato: los reusamos antes de crear uno nuevo
        for f in ("static", "video", "animated"):
            candidates.append((_legacy_name(user_id, f, 1, botuser), _steal_title(1)))
    else:
        candidates.append((_legacy_name(user_id, fmt, 1, botuser), _steal_title(1)))

    seen = set()
    for name, title in candidates:
        if name in seen:
            continue
        seen.add(name)
        size = _set_size(name)
        if size is not None and size < STEAL_LIMIT:
            return {"name": name, "title": title, "exists": True}

    # Nada con lugar → seguimos la serie correspondiente (monkey_steal 2, 3, ...)
    n = 1
    while True:
        name = _unified_name(user_id, n, botuser) if fmt is None else _legacy_name(user_id, fmt, n, botuser)
        size = _set_size(name)
        if size is None:
            return {"name": name, "title": _steal_title(n), "exists": False}
        if size < STEAL_LIMIT:
            return {"name": name, "title": _steal_title(n), "exists": True}
        n += 1


def _save_sticker_into(user_id, target, value, emoji, fmt):
    if target["exists"]:
        _add_to_set(user_id, target["name"], value, emoji, fmt)
    else:
        _create_set(user_id, target["name"], target["title"], value, emoji, fmt)


@bot.message_handler(commands=["monkey_steal"])
def handle_monkey_steal(message):
    reply = message.reply_to_message
    if not reply or not reply.sticker:
        bot.reply_to(message, "ℹ️ Respondé a un *sticker* con /monkey\\_steal para guardarlo en tu pack.",
                     parse_mode="Markdown")
        return

    st = reply.sticker
    fmt = _sticker_field_format(st)
    user_id = message.from_user.id
    emoji = st.emoji or "⭐"

    try:
        value = _steal_source(st, fmt)
    except Exception as e:
        return bot.reply_to(message, f"❌ No pude leer el sticker: {e}")

    username = _bot_username()
    target = _resolve_steal_pack(user_id, username)

    try:
        _save_sticker_into(user_id, target, value, emoji, fmt)
    except Exception as e:
        if _is_peer_error(str(e)):
            return bot.reply_to(
                message,
                f"✋ Para guardarte stickers necesito que primero me escribas por privado.\n\n"
                f"👉 https://t.me/{username}\n\nTocá \"Iniciar\" y volvé a intentar el /monkey_steal.",
                disable_web_page_preview=True)
        if not _is_format_error(str(e)):
            return bot.reply_to(message, f"❌ No pude guardar el sticker: {e}")
        # Telegram no aceptó mezclar formatos en ese pack → usamos la serie por formato.
        target = _resolve_steal_pack(user_id, username, fmt=fmt)
        try:
            _save_sticker_into(user_id, target, value, emoji, fmt)
        except Exception as e2:
            return bot.reply_to(message, f"❌ No pude guardar el sticker: {e2}")

    store.add_pack(user_id, target["name"], target["title"], fmt, steal=True)
    bot.reply_to(message, f'✅ Guardado en "{target["title"]}"\nhttps://t.me/addstickers/{target["name"]}',
                 disable_web_page_preview=True)


# =====================================================================
#  Botones del teclado persistente + ruteo de mensajes de contenido
# =====================================================================
@bot.message_handler(func=lambda m: m.text == "🎨 Crear pack")
def kb_create(message):
    start_pack(message)


@bot.message_handler(func=lambda m: m.text == "📋 Mis packs")
def kb_mypacks(message):
    show_my_packs(message)


@bot.message_handler(func=lambda m: m.text == "❓ Ayuda")
def kb_help(message):
    bot.send_message(message.chat.id, HELP_TEXT, parse_mode="Markdown",
                     reply_markup=_private_reply_keyboard(message))


@bot.message_handler(content_types=["text", "photo", "video", "animation", "sticker", "document", "video_note"])
def handle_content(message):
    # Flujo de /pack activo (solo privado)
    if message.chat.type == "private":
        s = _pack_sessions.get(message.from_user.id)
        if s and s["state"] != S_IDLE:
            try:
                _handle_pack_message(message)
            except Exception as e:
                bot.reply_to(message, f"❌ Error: {e}")
            return
    # Si no, cacheamos para /mq
    _cache_message(message)


def get_bot():
    return bot
