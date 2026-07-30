"""
quotly_render.py - Render de la burbuja estilo Telegram/Quotly con Pillow.

Reemplaza al render con Puppeteer/Chromium del bot original en Node: dibuja la
burbuja directamente con Pillow (+ pilmoji para los emojis), sin navegador, para
que corra liviano en el Render nativo de Python.

API pública:
    render_quote(messages) -> bytes   # PNG (fondo transparente)
    to_sticker_webp(png_bytes) -> bytes  # normaliza a 512 y devuelve WebP

`messages` es una lista de dicts:
    { "user_id": int, "name": str, "text": str,
      "avatar": bytes|None, "title": str }
Mensajes consecutivos del mismo user_id se agrupan (avatar/nombre una sola vez).
"""
import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
from pilmoji import Pilmoji

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "fonts")
_FONT_REG_PATH = os.path.join(_FONT_DIR, "DejaVuSans.ttf")
_FONT_BOLD_PATH = os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf")

# Paleta oficial de colores de nombre de Telegram.
_NAME_COLORS = [
    (225, 112, 118), (250, 167, 116), (166, 149, 231), (123, 200, 98),
    (110, 201, 203), (101, 170, 221), (238, 122, 174),
]

_BUBBLE_BG = (24, 37, 51)        # #182533
_TEXT_COLOR = (242, 244, 245)
_TITLE_COLOR = (109, 127, 143)

SCALE = 2  # supersampling para bordes/texto más nítidos

# --- Medidas base (en px lógicos, se multiplican por SCALE al dibujar) ---
_PAD_X, _PAD_Y = 16, 10        # padding del "card"
_AVATAR = 40
_GAP = 7                        # avatar ↔ burbuja
_ROW_GAP = 2                    # entre burbujas del mismo grupo
_GROUP_GAP = 8                  # entre grupos de distinto usuario
_B_PAD_L, _B_PAD_R = 12, 12
_B_PAD_T, _B_PAD_B = 6, 7
_NAME_SIZE = 15
_TITLE_SIZE = 12
_MAX_TEXT_W = 340              # ancho máximo del texto antes de wrap
_MIN_BUBBLE_W = 54
_RADIUS = 16


def _name_color(uid):
    s = str(uid)
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return _NAME_COLORS[h % len(_NAME_COLORS)]


def _text_size(text):
    n = len(text)
    if n <= 5:
        return 24
    if n <= 20:
        return 19
    if n <= 60:
        return 17
    if n <= 120:
        return 16
    return 15


def _font(path, size):
    return ImageFont.truetype(path, size * SCALE)


def _measure(pilmoji, text, font):
    """Ancho/alto de un texto (con emojis) en px reales (ya escalados)."""
    if not text:
        return 0, 0
    w, h = pilmoji.getsize(text, font)
    return w, h


def _wrap(pilmoji, text, font, max_w):
    """Envuelve respetando saltos de línea y ancho máximo (px reales)."""
    lines = []
    for raw in text.split("\n"):
        if raw == "":
            lines.append("")
            continue
        words = raw.split(" ")
        cur = ""
        for word in words:
            candidate = word if cur == "" else cur + " " + word
            w, _ = _measure(pilmoji, candidate, font)
            if w <= max_w or cur == "":
                cur = candidate
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def _circle_avatar(avatar_bytes, name, color, size_px):
    """Devuelve una imagen RGBA circular: foto recortada o inicial sobre color."""
    img = Image.new("RGBA", (size_px, size_px), (0, 0, 0, 0))
    if avatar_bytes:
        try:
            photo = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
            # recorte centrado tipo "cover"
            pw, ph = photo.size
            scale = max(size_px / pw, size_px / ph)
            photo = photo.resize((max(1, int(pw * scale)), max(1, int(ph * scale))))
            pw, ph = photo.size
            left = (pw - size_px) // 2
            top = (ph - size_px) // 2
            photo = photo.crop((left, top, left + size_px, top + size_px))
            img.paste(photo, (0, 0))
        except Exception:
            avatar_bytes = None
    if not avatar_bytes:
        draw = ImageDraw.Draw(img)
        draw.ellipse((0, 0, size_px - 1, size_px - 1), fill=color + (255,))
        letter = (name[:1] or "?").upper()
        f = _font(_FONT_BOLD_PATH, 18)
        bbox = draw.textbbox((0, 0), letter, font=f)
        lw, lh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((size_px - lw) / 2 - bbox[0], (size_px - lh) / 2 - bbox[1]),
                  letter, font=f, fill=(255, 255, 255, 255))
    # máscara circular
    mask = Image.new("L", (size_px, size_px), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size_px - 1, size_px - 1), fill=255)
    img.putalpha(mask)
    return img


def render_quote(messages):
    """Renderiza los mensajes a un PNG (bytes) con fondo transparente."""
    s = SCALE
    name_font = _font(_FONT_BOLD_PATH, _NAME_SIZE)
    title_font = _font(_FONT_REG_PATH, _TITLE_SIZE)

    # scratch para medir
    scratch = Image.new("RGBA", (10, 10), (0, 0, 0, 0))

    rows = []  # cada row: dict con layout calculado
    with Pilmoji(scratch) as pm:
        for i, msg in enumerate(messages):
            uid = msg.get("user_id")
            name = msg.get("name") or "Usuario"
            text = (msg.get("text") or "")
            title = msg.get("title") or ""
            color = _name_color(uid)

            prev = messages[i - 1] if i > 0 else None
            nxt = messages[i + 1] if i + 1 < len(messages) else None
            first = (prev is None) or (prev.get("user_id") != uid)
            last = (nxt is None) or (nxt.get("user_id") != uid)

            tf = _font(_FONT_REG_PATH, _text_size(text))
            lines = _wrap(pm, text, tf, _MAX_TEXT_W * s)

            # alto de línea de texto
            ascent, descent = tf.getmetrics()
            line_h = ascent + descent
            text_h = line_h * len(lines) if lines else line_h
            text_w = max([_measure(pm, ln, tf)[0] for ln in lines] + [0])

            head_h = 0
            head_w = 0
            if first:
                na, nd = name_font.getmetrics()
                ta, td = title_font.getmetrics()
                head_h = max(na + nd, ta + td) + 1 * s
                nw = _measure(pm, name, name_font)[0]
                tw = (_measure(pm, "  " + title, title_font)[0] if title else 0)
                head_w = nw + tw

            content_w = max(text_w, head_w)
            bubble_w = max(_MIN_BUBBLE_W * s, content_w + (_B_PAD_L + _B_PAD_R) * s)
            bubble_h = (_B_PAD_T + _B_PAD_B) * s + head_h + text_h

            rows.append({
                "msg": msg, "name": name, "text": text, "title": title,
                "color": color, "first": first, "last": last,
                "tf": tf, "lines": lines, "line_h": line_h,
                "head_h": head_h, "bubble_w": int(bubble_w), "bubble_h": int(bubble_h),
            })

    # dimensiones del canvas
    left_col = (_PAD_X + _AVATAR + _GAP) * s
    total_w = 0
    total_h = _PAD_Y * s
    for idx, r in enumerate(rows):
        if idx > 0:
            total_h += (_GROUP_GAP if r["first"] else _ROW_GAP) * s
        total_h += r["bubble_h"]
        total_w = max(total_w, left_col + r["bubble_w"] + _PAD_X * s)
    total_h += _PAD_Y * s

    canvas = Image.new("RGBA", (int(total_w), int(total_h)), (0, 0, 0, 0))

    with Pilmoji(canvas) as pm:
        y = _PAD_Y * s
        for idx, r in enumerate(rows):
            if idx > 0:
                y += (_GROUP_GAP if r["first"] else _ROW_GAP) * s
            bx = left_col
            bw, bh = r["bubble_w"], r["bubble_h"]

            # burbuja redondeada (la esquina inf-izq queda recta en el último del grupo)
            draw = ImageDraw.Draw(canvas)
            radius = _RADIUS * s
            draw.rounded_rectangle(
                (bx, y, bx + bw, y + bh), radius=radius,
                corners=(True, True, True, not r["last"]), fill=_BUBBLE_BG,
            )
            if r["last"]:
                bottom = y + bh
                # fin/cola apuntando hacia el avatar (abajo-izquierda)
                draw.polygon([
                    (bx, bottom - 13 * s),
                    (bx, bottom),
                    (bx - 9 * s, bottom),
                ], fill=_BUBBLE_BG)
                # redondear la punta de la cola
                draw.pieslice(
                    (bx - 9 * s, bottom - 9 * s, bx + 9 * s, bottom + 9 * s),
                    90, 180, fill=_BUBBLE_BG,
                )

            # avatar (solo en el último del grupo)
            if r["last"]:
                av = _circle_avatar(r["msg"].get("avatar"), r["name"], r["color"], _AVATAR * s)
                canvas.alpha_composite(av, (_PAD_X * s, int(y + bh - _AVATAR * s)))

            # contenido
            cx = bx + _B_PAD_L * s
            cy = y + _B_PAD_T * s
            if r["first"]:
                pm.text((int(cx), int(cy)), r["name"], fill=r["color"] + (255,), font=name_font)
                if r["title"]:
                    nw = _measure(pm, r["name"], name_font)[0]
                    pm.text((int(cx + nw + 6 * s), int(cy + 2 * s)), r["title"],
                            fill=_TITLE_COLOR + (255,), font=title_font)
                cy += r["head_h"]

            for ln in r["lines"]:
                pm.text((int(cx), int(cy)), ln, fill=_TEXT_COLOR + (255,), font=r["tf"])
                cy += r["line_h"]

            y += bh

    out = BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def to_sticker_webp(png_bytes):
    """Normaliza a 512 en el lado mayor y devuelve WebP (para enviar como sticker)."""
    img = Image.open(BytesIO(png_bytes)).convert("RGBA")
    w, h = img.size
    if w >= h:
        nw, nh = 512, max(1, round(512 * h / w))
    else:
        nh, nw = 512, max(1, round(512 * w / h))
    img = img.resize((nw, nh), Image.LANCZOS)
    out = BytesIO()
    img.save(out, format="WEBP", quality=90)
    return out.getvalue()
