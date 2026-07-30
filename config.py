"""
config.py - Configuración centralizada del proyecto.
Todas las variables de entorno, constantes, y clientes externos.
"""
import os
import stripe
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# ===============================
# SAFE MODE
# ===============================
SAFE_MODE_NO_BAN = False

# ===============================
# TIER MAPPING (Stripe Product ID → Discord Role ID)
# ⚠️ DEPRECADO: este mapping es solo para Stripe. Estamos migrando el cobro a
#    Telegram Stars (ver STAR_TIER_MAPPING abajo). Se eliminará junto con Stripe.
# ===============================
TIER_MAPPING = {
    "prod_SZ9dmrnfH9AwhO": 1459004030381592606,  # Tier 1
    "prod_SZ9eQne47KPluz": 1459004119711879372,  # Tier 2
    "prod_SZ9ezfEZ3OhuFC": 1459004146970787861   # Tier 3
}
TIER_3_PRODUCT_ID = "prod_SZ9ezfEZ3OhuFC"
TIER_3_ROLE_ID = TIER_MAPPING[TIER_3_PRODUCT_ID]

# ===============================
# TELEGRAM STARS - TIER MAPPING (nuevo sistema de cobro)
# Cada tier: precio en Stars (XTR) + roles de Discord que otorga.
# Reutiliza los role IDs de TIER_MAPPING. AJUSTA los precios (stars) a tu gusto.
# ===============================
STAR_TIER_MAPPING = {
    "tier1": {
        "label": "Tier 1",
        "stars": 100,  # ⚠️ AJUSTAR precio real en Stars
        "roles": [1459004030381592606],
    },
    "tier2": {
        "label": "Tier 2",
        "stars": 250,  # ⚠️ AJUSTAR
        "roles": [1459004119711879372],
    },
    "tier3": {
        "label": "Tier 3",
        "stars": 500,  # ⚠️ AJUSTAR
        # Tier 3 incluye también el rol por defecto (paridad con la lógica de Stripe)
        "roles": [1459004146970787861],
    },
}
# Periodo de suscripción de Telegram Stars: el ÚNICO valor permitido es 30 días.
STAR_SUBSCRIPTION_PERIOD = 2592000  # 30 * 24 * 60 * 60

# ===============================
# DISCORD
# ===============================
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0"))
DEFAULT_ROLE_ID = int(os.environ.get("DISCORD_ROLE_ID", "0"))
ADMIN_LOG_CHANNEL_ID = int(os.environ.get("ADMIN_LOG_CHANNEL_ID", "0"))

MANAGED_ROLES = list(TIER_MAPPING.values())
if DEFAULT_ROLE_ID:
    MANAGED_ROLES.append(DEFAULT_ROLE_ID)

# ===============================
# SUPABASE
# ===============================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
TABLE_NAME = "subscriptions_discord"

# Tablas del nuevo sistema de Telegram Stars
TELEGRAM_SUBS_TABLE = "telegram_star_subs"       # suscripciones activas (telegram → discord)
TELEGRAM_LINK_CODES_TABLE = "telegram_link_codes"  # códigos de vinculación temporales

# ===============================
# STRIPE
# ⚠️ DEPRECADO: en proceso de migración a Telegram Stars. Todo este bloque y sus
#    usos se eliminarán una vez validado el nuevo cobro con Stars.
# ===============================
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
stripe.api_key = STRIPE_SECRET_KEY

ACTIVE_STATUSES = ["active", "trialing", "past_due"]

# ===============================
# TELEGRAM - Bot 1 (Acceso al canal)
# ===============================
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID', '-100...')
CHANNEL_LINK = os.environ.get('CHANNEL_LINK', 'https://t.me/...')
TELEGRAM_ADMIN_ID = int(os.environ.get('TELEGRAM_ADMIN_ID', '0'))
TELEGRAM_LINKS = {
    "1": os.environ.get('LINK_BOT1'),
    "2": os.environ.get('LINK_BOT2'),
    "3": os.environ.get('LINK_BOT3'),
    "4": os.environ.get('LINK_BOT4')
}

# ===============================
# TELEGRAM - Bot 2 (MonkeyDescargar)
# ===============================
MONKEY_TELEGRAM_TOKEN = os.environ.get('MONKEY_TELEGRAM_TOKEN', '8716244791:AAEdLg6RTfdNljLb3UreC9k9wauUk-1te0o')

# ===============================
# TELEGRAM - Bot 3 (Cobro con Stars) - NUEVO, bot independiente
# ===============================
STARS_TELEGRAM_TOKEN = os.environ.get('STARS_TELEGRAM_TOKEN')
# Username del bot (sin @) para armar el deep link t.me/<username>?start=<code>
STARS_TELEGRAM_BOT_USERNAME = os.environ.get('STARS_TELEGRAM_BOT_USERNAME', '')

# ===============================
# TELEGRAM - Bot 4 (Quotly / Monkey stickers)
# Citas → stickers (/mq), creador de packs (/pack) y /monkey_steal.
# ===============================
MONKEY_QUOTLY_TOKEN = os.environ.get('MONKEY_QUOTLY_TOKEN')

# ===============================
# INSTAGRAM (contenido NSFW/restringido)
# ===============================
IG_USERNAME = os.environ.get('IG_USERNAME', '')
IG_PASSWORD = os.environ.get('IG_PASSWORD', '')
IG_COOKIES_RAW = os.environ.get('IG_COOKIES', '')
