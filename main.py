import os
import discord
from discord.ext import tasks
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from supabase import create_client, Client
import uvicorn
import stripe
import re
from dotenv import load_dotenv
import asyncio
import threading
import time
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

load_dotenv()

## ===============================
## CONFIGURACIÓN GENERAL
## ===============================

# --- CONFIGURACIÓN DE TIERS ---
# Mapeo: Product ID de Stripe -> Role ID de Discord
TIER_MAPPING = {
    "prod_SZ9dmrnfH9AwhO": 1459004030381592606, # Tier 1
    "prod_SZ9eQne47KPluz": 1459004119711879372, # Tier 2
    "prod_SZ9ezfEZ3OhuFC": 1459004146970787861  # Tier 3 (El más alto)
}

# Definimos cuál es el ID del producto Tier 3 para lógica especial
TIER_3_PRODUCT_ID = "prod_SZ9ezfEZ3OhuFC"
TIER_3_ROLE_ID = TIER_MAPPING[TIER_3_PRODUCT_ID]

# --- CONFIGURACIÓN DISCORD ---
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0"))
DEFAULT_ROLE_ID = int(os.environ.get("DISCORD_ROLE_ID", "0")) # Rol Legacy/Premium
ADMIN_LOG_CHANNEL_ID = int(os.environ.get("ADMIN_LOG_CHANNEL_ID", "0"))

# Roles Gestionados: Todos los tiers + el legacy
MANAGED_ROLES = list(TIER_MAPPING.values())
if DEFAULT_ROLE_ID:
    MANAGED_ROLES.append(DEFAULT_ROLE_ID)

# --- STRIPE & SUPABASE ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

stripe.api_key = STRIPE_SECRET_KEY
ACTIVE_STATUSES = ["active", "trialing", "past_due"] # 'past_due' da un margen de gracia

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
TABLE_NAME = "subscriptions_discord"

# --- TELEGRAM CONFIG ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID', '-100...') 
CHANNEL_LINK = os.environ.get('CHANNEL_LINK', 'https://t.me/...')
TELEGRAM_ADMIN_ID = int(os.environ.get('TELEGRAM_ADMIN_ID', '0'))

telegram_links = {
    "1": os.environ.get('LINK_BOT1', 'https://t.me/bot1'),
    "2": os.environ.get('LINK_BOT2', 'https://t.me/bot2'),
    "3": os.environ.get('LINK_BOT3', 'https://t.me/bot3')
}

WHATS_NEW_TEXT = """
📢 **WEEKLY UPDATES** 🚀
... (Tu texto original) ...
"""

GALLERY_TEXT = """
🤖 Our Bots and Exclusive Galleries ✨
... (Tu texto original) ...
"""

## ====================
## LÓGICA DE NEGOCIO (CORE)
## ====================

def get_customer_subscription_data(customer_id: str):
    """
    Busca suscripciones. Prioriza las ACTIVAS.
    Si un usuario tiene una cancelada y una activa, devolverá la activa.
    """
    try:
        # Traemos hasta 20 suscripciones expandiendo el producto
        subscriptions = stripe.Subscription.list(customer=customer_id, status='all', limit=20, expand=['data.plan.product']) 
        if not subscriptions.data:
            return "canceled", None
        
        # 1. BARRIDO: Buscar la primera que esté ACTIVA
        for sub in subscriptions.data:
            if sub.status in ACTIVE_STATUSES:
                prod_obj = sub.plan.product
                p_id = prod_obj.get('id') if isinstance(prod_obj, dict) else prod_obj
                return sub.status, p_id
        
        # 2. Si ninguna está activa, devolvemos la última (que estará cancelada)
        latest = subscriptions.data[0]
        prod_obj = latest.plan.product
        p_id = prod_obj.get('id') if isinstance(prod_obj, dict) else prod_obj
        return latest.status, p_id

    except Exception as e:
        print(f"🚨 Error Stripe Helper: {e}")
        return None, None

def calculate_roles_to_assign(product_id):
    """
    Determina qué roles debe tener el usuario basándose en su producto.
    REGLA: Si es Tier 3 O es un producto desconocido (Legacy) -> Tier 3 + Legacy Role.
    """
    roles_to_give = []
    
    # Verificamos si es un producto nuevo conocido
    tier_role = TIER_MAPPING.get(product_id)

    if tier_role:
        # Es Tier 1, 2 o 3
        roles_to_give.append(tier_role)
        
        # REGLA: Si es Tier 3, TAMBIÉN damos el Legacy Role (Premium)
        if tier_role == TIER_3_ROLE_ID:
            roles_to_give.append(DEFAULT_ROLE_ID)
    else:
        # Es un producto desconocido (Legacy / Antiguo)
        # REGLA: "Usuarios actuales entran a Tier 3"
        roles_to_give.append(TIER_3_ROLE_ID)
        roles_to_give.append(DEFAULT_ROLE_ID)
        
    # Limpiamos duplicados y 0s
    return list(set([r for r in roles_to_give if r != 0]))

## ====================
## FASTAPI
## ====================
app = FastAPI()

@app.get("/")
async def home():
    return {"status": "Combined Bot Active (Tier 3 Logic Applied)"}

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload/sig")
    
    event_type = event["type"]
    data_object = event["data"]["object"]
    customer_id = data_object.get("customer")

    if not customer_id and "customer" in data_object:
        customer_id = data_object["customer"]
    elif not customer_id and event_type.startswith("customer."):
        customer_id = data_object.get("id")

    if event_type in ["customer.subscription.updated", "customer.subscription.created", "customer.subscription.deleted"]:
        if customer_id:
            status, _ = get_customer_subscription_data(customer_id)
            if status:
                try:
                    exists = supabase.table(TABLE_NAME).select("*").eq("stripe_customer_id", customer_id).execute()
                    now = discord.utils.utcnow().isoformat()
                    if exists.data:
                        supabase.table(TABLE_NAME).update({"subscription_status": status, "updated_at": now}).eq("stripe_customer_id", customer_id).execute()
                    else:
                        supabase.table(TABLE_NAME).insert({"stripe_customer_id": customer_id, "subscription_status": status, "updated_at": now}).execute()
                except Exception as e:
                    print(f"DB Error: {e}")

    return JSONResponse(status_code=200, content={"message": "Handled"})

## ====================
## TELEGRAM BOT
## ====================
telegram_bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ... (MANTENEMOS TU CÓDIGO DE TELEGRAM EXACTAMENTE IGUAL) ...
# Para ahorrar espacio aquí, asumo que usas las mismas funciones 
# check_membership, get_main_menu, handlers, etc. que me pasaste antes.
# Copia y pega tu sección de Telegram aquí.

def check_membership(user_id):
    clean_id = str(CHANNEL_ID).strip().replace("'", "").replace('"', "")
    if not clean_id.startswith("-100"): clean_id = "-100" + clean_id
    try:
        member = telegram_bot.get_chat_member(int(clean_id), user_id)
        return member.status in ['creator', 'administrator', 'member', 'restricted']
    except:
        return False

def get_main_menu():
    markup = InlineKeyboardMarkup(row_width=1)
    btn1 = InlineKeyboardButton("🔥 Img to Video Bot 1 monkeyvideos", url=telegram_links["1"])
    btn2 = InlineKeyboardButton("🤖 Img to Video Bot 2 videos69", url=telegram_links["2"])
    btn3 = InlineKeyboardButton("🤖 Nudify videos", url=telegram_links["3"])
    btn_news = InlineKeyboardButton("✨ What's New? (Updates) 🆕", callback_data="whats_new")
    btn_gallery = InlineKeyboardButton("🔥 Gallery 🔥", callback_data="show_gallery")
    markup.add(btn1, btn2, btn3, btn_news, btn_gallery)
    return markup

@telegram_bot.message_handler(commands=['start'])
def send_welcome(message):
    if check_membership(message.from_user.id):
        telegram_bot.reply_to(message, "✅ **Access Granted**\nSelect an available server below:", reply_markup=get_main_menu(), parse_mode="Markdown")
    else:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("👉 Join Channel First", url=CHANNEL_LINK), InlineKeyboardButton("🔄 I Joined, Verify Me", callback_data="check_again"))
        telegram_bot.reply_to(message, "⛔ **Access Restricted**\n\nTo use our bots, you must join our official channel first.", reply_markup=markup, parse_mode="Markdown")

@telegram_bot.callback_query_handler(func=lambda call: call.data == "whats_new")
def show_updates(call):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu"))
    telegram_bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=WHATS_NEW_TEXT, parse_mode="Markdown", reply_markup=markup)

@telegram_bot.callback_query_handler(func=lambda call: call.data == "show_gallery")
def show_gallery_menu(call):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu"))
    telegram_bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=GALLERY_TEXT, disable_web_page_preview=False, reply_markup=markup)

@telegram_bot.callback_query_handler(func=lambda call: call.data == "back_to_menu")
def back_to_main(call):
    telegram_bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text="✅ **Access Granted**\nSelect an available server below:", reply_markup=get_main_menu(), parse_mode="Markdown")

@telegram_bot.callback_query_handler(func=lambda call: call.data == "check_again")
def callback_verify(call):
    if check_membership(call.from_user.id):
        telegram_bot.edit_message_text("✅ **Verified!** Choose your bot:", call.message.chat.id, call.message.message_id, reply_markup=get_main_menu(), parse_mode="Markdown")
    else:
        telegram_bot.answer_callback_query(call.id, "❌ You are not in the channel yet.", show_alert=True)

## ====================
## DISCORD BOT
## ====================
intents = discord.Intents.default()
intents.members = True
intents.messages = True
intents.message_content = True
discord_client = discord.Client(intents=intents)

guild = None
admin_log_channel = None

@discord_client.event
async def on_ready():
    global guild, admin_log_channel
    print(f"✅ Discord logged in as {discord_client.user}")
    guild = discord_client.get_guild(DISCORD_GUILD_ID)
    if guild:
        admin_log_channel = discord_client.get_channel(ADMIN_LOG_CHANNEL_ID)
        print(f"Guild: {guild.name}")
    
    if not check_subscriptions.is_running():
        check_subscriptions.start()

@discord_client.event
async def on_message(message):
    if message.author == discord_client.user: return

    # Comando !link (Solo en MD)
    if isinstance(message.channel, discord.DMChannel) and message.content.lower().startswith("!link"):
        parts = message.content.split()
        if len(parts) != 2:
            await message.channel.send("❌ Use: `!link email@example.com`")
            return

        user_email = parts[1].lower()
        if not re.match(r"[^@]+@[^@]+\.[^@]+", user_email):
            await message.channel.send("❌ Invalid email format.")
            return

        try:
            # Buscamos cliente en Stripe
            customers = stripe.Customer.list(email=user_email, limit=1)
            if not customers.data:
                await message.channel.send("❌ No customer found with that email in Stripe.")
                return

            customer_id = customers.data[0].id
            # Obtenemos la suscripción activa (ignora las canceladas duplicadas)
            stripe_status, product_id = get_customer_subscription_data(customer_id)

            if stripe_status not in ACTIVE_STATUSES:
                await message.channel.send("⚠️ Email found, but no active subscription.")
                return
            
            # --- CALCULAR ROLES (Tier 3 + Legacy) ---
            target_role_ids = calculate_roles_to_assign(product_id)
            
            # Guardar en Supabase
            now = discord.utils.utcnow().isoformat()
            response = supabase.table(TABLE_NAME).select("*").eq("stripe_customer_id", customer_id).execute()
            
            update_data = {
                "discord_user_id": str(message.author.id), 
                "subscription_status": stripe_status,
                "updated_at": now
            }

            if response.data:
                existing_user = response.data[0].get("discord_user_id")
                if existing_user and existing_user != str(message.author.id):
                    await message.channel.send("⚠️ Subscription linked to another Discord account.")
                    return
                supabase.table(TABLE_NAME).update(update_data).eq("stripe_customer_id", customer_id).execute()
            else:
                supabase.table(TABLE_NAME).insert(dict(stripe_customer_id=customer_id, **update_data)).execute()

            # ASIGNAR ROLES INMEDIATAMENTE
            if guild:
                member = guild.get_member(message.author.id)
                if member:
                    added_names = []
                    for r_id in target_role_ids:
                        r_obj = guild.get_role(r_id)
                        if r_obj and r_obj not in member.roles:
                            await member.add_roles(r_obj, reason="Linkeo Exitoso")
                            added_names.append(r_obj.name)
                    
                    if added_names:
                        await message.channel.send(f"✅ Linked! Roles added: {', '.join(added_names)}")
                    else:
                        await message.channel.send("✅ Linked! You already have the correct roles.")

                    if admin_log_channel:
                        await admin_log_channel.send(f"🟢 **Link:** {message.author.mention} (`{user_email}`) -> Roles: {target_role_ids}")

        except Exception as e:
            print(f"Link error: {e}")
            await message.channel.send("❌ Error linking account.")

@tasks.loop(minutes=10)
async def check_subscriptions():
    print("🔄 Checking subscriptions (Tier 3 + Double Sub Logic)...")
    if not guild: return
    
    try:
        # Traemos a todos los usuarios linkeados
        response = supabase.table(TABLE_NAME).select("*").neq("discord_user_id", "None").execute()
        
        for user_data in response.data:
            discord_user_id = user_data.get("discord_user_id")
            customer_id = user_data.get("stripe_customer_id")
            
            if not discord_user_id or not customer_id: continue

            # Chequeo en Stripe (devuelve ACTIVE si hay alguna activa, ignorando canceladas)
            final_status, product_id = get_customer_subscription_data(customer_id)
            
            # Actualizar DB si cambió
            if final_status != user_data.get("subscription_status"):
                supabase.table(TABLE_NAME).update({
                    "subscription_status": final_status, 
                    "updated_at": discord.utils.utcnow().isoformat()
                }).eq("stripe_customer_id", customer_id).execute()

            member = guild.get_member(int(discord_user_id))
            if not member: continue

            # === LÓGICA DE ROLES ===
            
            if final_status in ACTIVE_STATUSES:
                # USUARIO ACTIVO
                # Calculamos roles (si es Legacy -> Tier 3 + Legacy)
                roles_to_assign = calculate_roles_to_assign(product_id)
                
                for r_id in roles_to_assign:
                    role_obj = guild.get_role(r_id)
                    # Solo añadimos, NO quitamos (SAFE MODE)
                    if role_obj and role_obj not in member.roles:
                        await member.add_roles(role_obj, reason="Suscripción Activa Check")
                        print(f"✅ Rol {role_obj.name} dado a {member.display_name}")

            else:
                # USUARIO INACTIVO (CANCELADO REALMENTE)
                # Quitamos TODOS los roles gestionados para asegurar que no entren gratis
                roles_removed = []
                for rid in MANAGED_ROLES:
                    r_rem = guild.get_role(rid)
                    if r_rem and r_rem in member.roles:
                        await member.remove_roles(r_rem, reason=f"Baja: {final_status}")
                        roles_removed.append(r_rem.name)
                
                if roles_removed and admin_log_channel:
                    await admin_log_channel.send(f"🔴 **Baja:** {member.mention} perdió: {', '.join(roles_removed)} ({final_status})")
            
            await asyncio.sleep(0.1)

    except Exception as e:
        print(f"Error check_sub: {e}")

## ====================
## RUNNERS
## ====================
def start_fastapi():
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

def start_telegram():
    print("🤖 Iniciando Telegram...")
    while True:
        try:
            telegram_bot.infinity_polling(skip_pending=True, timeout=90)
        except:
            time.sleep(5)

def start_discord():
    print("🤖 Iniciando Discord...")
    discord_client.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    threading.Thread(target=start_fastapi, daemon=True).start()
    threading.Thread(target=start_telegram, daemon=True).start()
    try:
        start_discord()
    except Exception as e:
        print(f"Main Error: {e}")
