"""
telegram_stars.py - Telegram Bot 3: Cobro de suscripciones con Telegram Stars.

Bot INDEPENDIENTE (token propio) con flujo PAGO-PRIMERO:
  1. El usuario abre el bot y ve el catálogo de tiers al instante (cero fricción).
  2. Paga en Stars (XTR, suscripción recurrente de 30 días) sin necesidad de haber
     vinculado nada todavía. La fila en Supabase queda con discord_user_id en null.
  3. Recién DESPUÉS del pago se le pide vincular su Discord con /link. Al canjear el
     código se completa la fila y el loop del bot de Discord le entrega los roles.

El otorgamiento/quita de ROLES lo hace siempre el loop del bot de Discord
(una sola fuente de verdad). Este sistema reemplaza a Stripe.
"""
import html

import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)

from config import STARS_TELEGRAM_TOKEN, STAR_TIER_MAPPING
from services.telegram_stars_helpers import (
    resolve_link_code,
    consume_link_code,
    attach_discord_to_subscription,
    create_subscription_invoice_link,
    record_payment,
    cancel_subscription,
    get_subscription,
)

# Si el token no está configurado usamos un placeholder con ':' válido (telebot>=4.36
# valida el token al construir). main.py no arranca el polling si el token falta.
stars_bot = telebot.TeleBot(STARS_TELEGRAM_TOKEN or "0:disabled")

# Mapa en memoria telegram_user_id -> discord_user_id de la sesión actual.
# La verdad persistente vive en la tabla telegram_star_subs; esto solo cubre el
# hueco entre "vinculó" y "pagó" (antes de que exista fila de suscripción).
_pending_links: dict = {}

# Comprar Stars dentro de las apps de iOS/Android carga la comisión de tienda (~30%).
# En Telegram Desktop / Web el mismo paquete de Stars sale más barato para el usuario.
APPLE_WARNING = (
    "⚠️ <b>Read this before you pay</b>\n"
    "Buying Stars inside the iPhone/iPad app costs about <b>30% more</b> — that's "
    "Apple's cut, not mine. Top up your Stars on <b>Telegram Desktop or Telegram Web</b> "
    "and the exact same plan gets noticeably cheaper."
)


# Botones del teclado persistente (los que aparecen bajo el campo de texto).
# OJO: envían TEXTO, no comandos, así que cada handler debe reconocer ambos.
BTN_PLANS = "⭐ Plans"
BTN_STATUS = "📋 My subscription"
BTN_LINK = "🔗 Link Discord"
BTN_CANCEL = "🚫 Cancel renewal"


def _main_keyboard() -> ReplyKeyboardMarkup:
    """Teclado fijo bajo el campo de texto. Queda pegado hasta que se reemplace."""
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton(BTN_PLANS), KeyboardButton(BTN_STATUS))
    kb.row(KeyboardButton(BTN_LINK), KeyboardButton(BTN_CANCEL))
    return kb


def _get_discord_id(telegram_user_id: int):
    """Obtiene el discord_user_id de un usuario: primero de la sesión, luego de la BD."""
    if telegram_user_id in _pending_links:
        return _pending_links[telegram_user_id]
    sub = get_subscription(telegram_user_id)
    if sub and sub.get("discord_user_id"):
        return sub["discord_user_id"]
    return None


def _tiers_menu() -> InlineKeyboardMarkup:
    """Botones de compra, uno por tier."""
    markup = InlineKeyboardMarkup(row_width=1)
    for tier_key, conf in STAR_TIER_MAPPING.items():
        markup.add(InlineKeyboardButton(
            f"{conf.get('emoji', '⭐')} {conf['label']} — {conf['stars']} ⭐ /month",
            callback_data=f"buy:{tier_key}",
        ))
    return markup


# Diferenciador principal del tier más caro: va arriba del todo, antes de los planes,
# para que se lea aunque el usuario no baje a comparar tier por tier.
PREMIUM_HIGHLIGHT = "🔊 <b>Only in Tier 3: every NDE dance video comes with sound.</b>"


def _catalog_text(header: str) -> str:
    """Arma el catálogo con los beneficios de cada tier.
    Usa HTML porque los perks los edita el dueño del bot a mano y un '_' o un '*'
    suelto rompería el parseo con Markdown."""
    blocks = [header, "", PREMIUM_HIGHLIGHT, ""]
    for conf in STAR_TIER_MAPPING.values():
        blocks.append(
            f"{conf.get('emoji', '⭐')} <b>{html.escape(conf['label'])}</b> — "
            f"<b>{conf['stars']} ⭐</b> /month"
        )
        for perk in conf.get("perks", []):
            blocks.append(f"   ✓ {html.escape(perk)}")
        blocks.append("")
    blocks.append(APPLE_WARNING)
    blocks.append("")
    blocks.append("<i>Auto-renews every 30 days. Cancel anytime with /cancel.</i>")
    return "\n".join(blocks)


# Instrucciones de vinculación: se reutilizan tras el pago y desde /link.
LINK_INSTRUCTIONS = (
    "🔗 <b>Link your Discord account</b>\n\n"
    "This is how your roles get assigned:\n\n"
    "1️⃣ Open Discord and send <code>!telegram</code> as a direct message to the bot.\n"
    "2️⃣ It replies with a link — tap it and it brings you back here, already linked.\n\n"
    "That's it. Your roles show up within a few minutes.\n\n"
    "<i>The link expires after 15 minutes — just send !telegram again if it does.</i>"
)


def _link_button() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔗 How do I link my Discord?", callback_data="howtolink"))
    return markup


@stars_bot.message_handler(commands=['start'])
def handle_start(message):
    """/start [code]: muestra el catálogo. Con código, además vincula la cuenta."""
    print(f"⭐ /start recibido de tg={message.from_user.id} chat={message.chat.id}")
    parts = message.text.split(maxsplit=1)
    code = parts[1].strip() if len(parts) > 1 else None

    # El teclado persistente va en su propio mensaje: Telegram solo acepta un
    # reply_markup por mensaje y el catálogo necesita los botones inline de compra.
    stars_bot.send_message(
        message.chat.id,
        "👋 Welcome! Use the buttons below to get around.",
        reply_markup=_main_keyboard(),
    )

    if code:
        _redeem_code(message, code)
        return

    stars_bot.send_message(
        message.chat.id,
        _catalog_text("⭐ <b>Choose your plan</b>"),
        reply_markup=_tiers_menu(),
        parse_mode="HTML",
    )


def _redeem_code(message, code: str) -> None:
    """Canjea un código de vinculación. Si ya hay una suscripción pagada, la completa."""
    discord_id = resolve_link_code(code)
    if not discord_id:
        stars_bot.reply_to(
            message,
            _catalog_text("⛔ <b>That link is invalid or expired.</b>\nHere are the plans anyway:"),
            reply_markup=_tiers_menu(),
            parse_mode="HTML",
        )
        return

    _pending_links[message.from_user.id] = str(discord_id)
    consume_link_code(code)

    # Si ya pagó (flujo pago-primero), completamos la fila y los roles salen solos.
    if attach_discord_to_subscription(message.from_user.id, discord_id):
        stars_bot.reply_to(
            message,
            "✅ <b>You're all set!</b>\n\nYour Discord account is linked to your active "
            "subscription. Your roles will be assigned within a few minutes.",
            parse_mode="HTML",
        )
        return

    # Vinculó antes de pagar: al catálogo, y el pago ya sale vinculado.
    stars_bot.reply_to(
        message,
        _catalog_text("✅ <b>Discord account linked.</b>\nNow pick your plan:"),
        reply_markup=_tiers_menu(),
        parse_mode="HTML",
    )


@stars_bot.message_handler(commands=['plans'])
@stars_bot.message_handler(func=lambda m: m.text == BTN_PLANS)
def handle_plans(message):
    """Catálogo público: no exige vinculación, solo muestra qué se puede comprar."""
    stars_bot.reply_to(
        message,
        _catalog_text("⭐ <b>Available plans</b>"),
        reply_markup=_tiers_menu(),
        parse_mode="HTML",
    )


@stars_bot.message_handler(commands=['link'])
@stars_bot.message_handler(func=lambda m: m.text == BTN_LINK)
def handle_link(message):
    """/link [code]: canjea un código, o explica cómo obtenerlo."""
    # Solo se busca código si vino como comando: el botón del teclado manda
    # "🔗 Link Discord", y partirlo dejaría "Link Discord" como falso código.
    code = None
    if message.text.startswith("/"):
        parts = message.text.split(maxsplit=1)
        code = parts[1].strip() if len(parts) > 1 else None

    if code:
        _redeem_code(message, code)
        return

    if _get_discord_id(message.from_user.id):
        stars_bot.reply_to(
            message,
            "✅ Your Discord account is already linked. Nothing else to do.",
            parse_mode="HTML",
        )
        return

    stars_bot.reply_to(message, LINK_INSTRUCTIONS, parse_mode="HTML")


@stars_bot.message_handler(commands=['status'])
@stars_bot.message_handler(func=lambda m: m.text == BTN_STATUS)
def handle_status(message):
    """Estado de la suscripción del usuario."""
    sub = get_subscription(message.from_user.id)
    if not sub:
        stars_bot.reply_to(
            message,
            _catalog_text("You don't have an active subscription yet.\nHere's what's available:"),
            reply_markup=_tiers_menu(),
            parse_mode="HTML",
        )
        return

    conf = STAR_TIER_MAPPING.get(sub.get("tier"), {})
    lines = [
        f"📋 <b>Your subscription</b>\n",
        f"Plan: <b>{html.escape(conf.get('label', sub.get('tier') or 'unknown'))}</b>",
        f"Status: <b>{html.escape(str(sub.get('status', 'unknown')))}</b>",
        f"Auto-renew: <b>{'on' if sub.get('is_recurring') else 'off'}</b>",
    ]
    if sub.get("subscription_expiration_date"):
        lines.append(f"Renews/expires: <b>{html.escape(str(sub['subscription_expiration_date'])[:10])}</b>")

    if not sub.get("discord_user_id"):
        lines.append(
            "\n⚠️ <b>Your Discord isn't linked yet</b> — that's why you don't have your "
            "roles. Send /link to fix it in under a minute."
        )
        stars_bot.reply_to(message, "\n".join(lines), reply_markup=_link_button(), parse_mode="HTML")
        return

    lines.append("\n🔗 Discord: <b>linked</b>")
    stars_bot.reply_to(message, "\n".join(lines), parse_mode="HTML")


@stars_bot.callback_query_handler(func=lambda c: c.data == "howtolink")
def handle_howtolink(call):
    stars_bot.answer_callback_query(call.id)
    stars_bot.send_message(call.message.chat.id, LINK_INSTRUCTIONS, parse_mode="HTML")


@stars_bot.callback_query_handler(func=lambda c: c.data.startswith("buy:"))
def handle_buy(call):
    """Genera y envía el invoice del tier elegido.

    NO exige vinculación con Discord: el objetivo es que el pago sea inmediato.
    La cuenta se vincula después, en handle_successful_payment."""
    tier = call.data.split(":", 1)[1]
    if tier not in STAR_TIER_MAPPING:
        stars_bot.answer_callback_query(call.id, "Unknown plan.")
        return

    try:
        invoice_url = create_subscription_invoice_link(stars_bot, tier)
    except Exception as e:
        print(f"⚠️ Error creando invoice link: {e}")
        stars_bot.answer_callback_query(
            call.id, "Couldn't create the payment. Please try again.", show_alert=True
        )
        return

    conf = STAR_TIER_MAPPING[tier]
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(f"💳 Pay {conf['stars']} ⭐ /month", url=invoice_url))

    perks = "\n".join(f"   ✓ {html.escape(p)}" for p in conf.get("perks", []))
    stars_bot.answer_callback_query(call.id)
    stars_bot.send_message(
        call.message.chat.id,
        f"{conf.get('emoji', '⭐')} <b>{html.escape(conf['label'])}</b> — "
        f"<b>{conf['stars']} ⭐</b> /month\n\n"
        f"{perks}\n\n"
        f"{APPLE_WARNING}\n\n"
        "Tap below to pay. Renews automatically every 30 days, cancel anytime.",
        reply_markup=markup,
        parse_mode="HTML",
    )


@stars_bot.pre_checkout_query_handler(func=lambda q: True)
def handle_pre_checkout(pre_checkout_query):
    """Aprobar el pre-checkout (obligatorio dentro de 10s)."""
    stars_bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@stars_bot.message_handler(content_types=['successful_payment'])
def handle_successful_payment(message):
    """Registra el pago y, si aún no hay Discord vinculado, lo pide AHORA."""
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

    if discord_id:
        stars_bot.reply_to(
            message,
            "✅ <b>Payment received — you're subscribed!</b>\n\n"
            "Your Discord roles will be assigned within a few minutes.",
            parse_mode="HTML",
        )
        return

    # Pago-primero: cobrado pero sin destino para los roles. Este es el único paso
    # que le queda al usuario, así que se pide de forma directa y sin rodeos.
    print(f"⭐ Pago sin Discord vinculado: tg={telegram_user_id} tier={tier}")
    stars_bot.reply_to(
        message,
        "✅ <b>Payment received — you're subscribed!</b>\n\n"
        "One last step: I still don't know which Discord account to give the roles to.\n\n"
        + LINK_INSTRUCTIONS,
        reply_markup=_link_button(),
        parse_mode="HTML",
    )


@stars_bot.message_handler(commands=['cancel'])
@stars_bot.message_handler(func=lambda m: m.text == BTN_CANCEL)
def handle_cancel(message):
    """Cancela la auto-renovación. El acceso se conserva hasta la fecha de expiración."""
    if cancel_subscription(stars_bot, message.from_user.id):
        stars_bot.reply_to(
            message,
            "🚫 <b>Auto-renew is off.</b>\n\nYou keep your access until the end of the "
            "period you already paid for.",
            parse_mode="HTML",
        )
    else:
        stars_bot.reply_to(message, "I couldn't find an active subscription to cancel.")


# Catch-all: debe quedar SIEMPRE al final (telebot evalúa los handlers en orden de
# registro). Solo captura lo que ningún handler anterior atendió; sirve para ver en
# los logs si Telegram está entregando updates cuando el bot parece "mudo".
@stars_bot.message_handler(func=lambda m: True)
def handle_unknown(message):
    print(f"⭐ Mensaje sin handler de tg={message.from_user.id}: {message.text!r}")
    stars_bot.reply_to(
        message,
        "I didn't catch that. Use /plans to see the subscriptions, /link to connect "
        "your Discord, /status to check your subscription, or /cancel to stop renewals.",
        reply_markup=_tiers_menu(),
    )
