"""
telegram_access.py - Telegram Bot 1: Control de acceso al canal.
"""
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import TELEGRAM_TOKEN, CHANNEL_ID, CHANNEL_LINK, TELEGRAM_LINKS

telegram_bot = telebot.TeleBot(TELEGRAM_TOKEN)


def check_membership(user_id):
    """Verifica si un usuario es miembro del canal."""
    clean_id = str(CHANNEL_ID).strip().replace("'", "").replace('"', "")
    if not clean_id.startswith("-100"):
        clean_id = "-100" + clean_id
    try:
        member = telegram_bot.get_chat_member(int(clean_id), user_id)
        return member.status in ['creator', 'administrator', 'member', 'restricted']
    except:
        return False


def get_main_menu():
    """Genera el menú principal con los botones de los bots."""
    markup = InlineKeyboardMarkup(row_width=1)
    btn1 = InlineKeyboardButton("🔥 Img to Video Bot 1 monkeyvideos", url=TELEGRAM_LINKS["1"])
    btn2 = InlineKeyboardButton("🤖 Img to Video Bot 2 videos69", url=TELEGRAM_LINKS["2"])
    btn3 = InlineKeyboardButton("🤖 Nudify videos", url=TELEGRAM_LINKS["3"])
    btn4 = InlineKeyboardButton("🔥 Img to img Bot ", url=TELEGRAM_LINKS["4"])
    markup.add(btn1, btn2, btn3, btn4)
    return markup


@telegram_bot.message_handler(commands=['start'])
def send_welcome(message):
    """Handler del comando /start - verifica membresía del canal."""
    if check_membership(message.from_user.id):
        telegram_bot.reply_to(message, "✅ **Access Granted**", reply_markup=get_main_menu(), parse_mode="Markdown")
    else:
        markup = InlineKeyboardMarkup()
        btn_join = InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)
        markup.add(btn_join)
        telegram_bot.reply_to(
            message,
            "⛔ **Access Denied**\n\nYou must join the channel first to use this bot.",
            reply_markup=markup,
            parse_mode="Markdown"
        )
