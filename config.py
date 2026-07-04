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
# ===============================
TIER_MAPPING = {
    "prod_SZ9dmrnfH9AwhO": 1459004030381592606,  # Tier 1
    "prod_SZ9eQne47KPluz": 1459004119711879372,  # Tier 2
    "prod_SZ9ezfEZ3OhuFC": 1459004146970787861   # Tier 3
}
TIER_3_PRODUCT_ID = "prod_SZ9ezfEZ3OhuFC"
TIER_3_ROLE_ID = TIER_MAPPING[TIER_3_PRODUCT_ID]

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

# ===============================
# STRIPE
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
# INSTAGRAM (contenido NSFW/restringido)
# ===============================
IG_USERNAME = os.environ.get('IG_USERNAME', '')
IG_PASSWORD = os.environ.get('IG_PASSWORD', '')
IG_COOKIES_RAW = os.environ.get('IG_COOKIES', '')
