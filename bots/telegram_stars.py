"""
telegram_stars.py - Telegram Bot 3: Cobro de suscripciones con Telegram Stars.

Bot INDEPENDIENTE (token propio) que:
  1. Vincula al usuario de Telegram con su cuenta de Discord vía un deep link
     generado desde el bot de Discord (/start <code>).
  2. Ofrece varios tiers y cobra una suscripción recurrente en Stars (XTR, 30 días).
  3. Registra los pagos en Supabase. El otorgamiento/quita de ROLES de Discord lo
     hace el loop del bot de Discord (una sola fuente de verdad).

Este sistema reemplazará a Stripe.
"""
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import STARS_TELEGRAM_TOKEN, STAR_TIER_MAPPING
from services.telegram_stars_helpers import (
    resolve_link_code,
    consume_link_code,
    create_subscription_invoice_link,
    record_payment,
    cancel_subscription,
    get_subscription,
)

# Si el token no está configurado usamos un placeholder para no romper el import;
# main.py no arranca el polling cuando STARS_TELEGRAM_TOKEN falta.
stars_bot = telebot.TeleBot(STARS_TELEGRAM_TOKEN or "STARS_TOKEN_NOT_SET")

# Mapa en memoria telegram_user_id -> discord_user_id de la sesión actual.
# La verdad persistente vive en la tabla telegram_star_subs; esto solo cubre el
# hueco entre "vinculó" y "pagó" (antes de que exista fila de suscripción).
_pending_links: dict = {}


def _get_discord_id(telegram_user_id: int):
    """Obtiene el discord_user_id de un usuario: primero de la sesión, luego de la BD."""
    if telegram_user_id in _pending_links:
        return _pending_links[telegram_user_id]
    sub = get_subscription(telegram_user_id)
    if sub and sub.get("discord_user_id"):
        return sub["discord_user_id"]
    return None


def _tiers_menu() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=1)
    for tier_key, conf in STAR_TIER_MAPPING.items():
        markup.add(InlineKeyboardButton(
            f"⭐ {conf['label']} — {conf['stars']} Stars/mes",
            callback_data=f"buy:{tier_key}",
        ))
    return markup


@stars_bot.message_handler(commands=['start'])
def handle_start(message):
    """/start <code>: resuelve el código de vinculación y muestra los tiers."""
    parts = message.text.split(maxsplit=1)
    code = parts[1].strip() if len(parts) > 1 else None

    if not code:
        stars_bot.reply_to(
            message,
            "👋 Para suscribirte, primero genera tu enlace desde el bot de Discord "
            "(mándale `!telegram` por mensaje directo) y ábrelo aquí.",
        )
        return

    discord_id = resolve_link_code(code)
    if not discord_id:
        stars_bot.reply_to(
            message,
            "⛔ Enlace inválido o expirado. Genera uno nuevo con `!telegram` en Discord.",
        )
        return

    _pending_links[message.from_user.id] = str(discord_id)
    consume_link_code(code)
    stars_bot.reply_to(
        message,
        "✅ Cuenta de Discord vinculada.\n\nElige tu plan de suscripción:",
        reply_markup=_tiers_menu(),
    )


@stars_bot.message_handler(commands=['planes'])
def handle_plans(message):
    """Muestra los tiers disponibles (requiere haber vinculado antes)."""
    if not _get_discord_id(message.from_user.id):
        stars_bot.reply_to(
            message,
            "⚠️ Aún no vinculas tu cuenta de Discord. Manda `!telegram` en Discord y abre el enlace.",
        )
        return
    stars_bot.reply_to(message, "Elige tu plan:", reply_markup=_tiers_menu())


@stars_bot.callback_query_handler(func=lambda c: c.data.startswith("buy:"))
def handle_buy(call):
    """Genera y envía el invoice de suscripción del tier elegido."""
    tier = call.data.split(":", 1)[1]
    if tier not in STAR_TIER_MAPPING:
        stars_bot.answer_callback_query(call.id, "Tier inválido.")
        return

    discord_id = _get_discord_id(call.from_user.id)
    if not discord_id:
        stars_bot.answer_callback_query(call.id, "Vincula tu Discord primero (!telegram).", show_alert=True)
        return

    try:
        invoice_url = create_subscription_invoice_link(stars_bot, tier)
    except Exception as e:
        print(f"⚠️ Error creando invoice link: {e}")
        stars_bot.answer_callback_query(call.id, "No se pudo crear el pago. Intenta de nuevo.", show_alert=True)
        return

    conf = STAR_TIER_MAPPING[tier]
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(f"💳 Pagar {conf['stars']} Stars/mes", url=invoice_url))
    stars_bot.answer_callback_query(call.id)
    stars_bot.send_message(
        call.message.chat.id,
        f"Estás por suscribirte a **{conf['label']}** ({conf['stars']} Stars al mes, "
        f"renovación automática).\n\nToca el botón para pagar:",
        reply_markup=markup,
        parse_mode="Markdown",
    )


@stars_bot.pre_checkout_query_handler(func=lambda q: True)
def handle_pre_checkout(pre_checkout_query):
    """Aprobar el pre-checkout (obligatorio dentro de 10s)."""
    stars_bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@stars_bot.message_handler(content_types=['successful_payment'])
def handle_successful_payment(message):
    """Registra el pago (inicial o recurrente). El otorgamiento de roles lo hace el loop de Discord."""
    sp = message.successful_payment
    tier = sp.invoice_payload  # el tier lo pusimos como payload
    telegram_user_id = message.from_user.id
    discord_id = _get_discord_id(telegram_user_id)

    record_payment(
        telegram_user_id=telegram_user_id,
        discord_user_id=discord_id,
        tier=tier,
        charge_id=sp.telegram_payment_charge_id,
        expiration=getattr(sp, "subscription_expiration_date", None),
        is_recurring=getattr(sp, "is_recurring", False),
    )

    if getattr(sp, "is_first_recurring", False) or getattr(sp, "is_recurring", False):
        msg = "✅ ¡Suscripción activa! Tus roles de Discord se asignarán en unos minutos."
    else:
        msg = "✅ ¡Pago recibido! Tus roles de Discord se asignarán en unos minutos."
    stars_bot.reply_to(message, msg)


@stars_bot.message_handler(commands=['cancelar'])
def handle_cancel(message):
    """Cancela la auto-renovación. El acceso se conserva hasta la fecha de expiración."""
    if cancel_subscription(stars_bot, message.from_user.id):
        stars_bot.reply_to(
            message,
            "🚫 Auto-renovación cancelada. Conservas el acceso hasta el fin del periodo ya pagado.",
        )
    else:
        stars_bot.reply_to(message, "No encontré una suscripción activa para cancelar.")
