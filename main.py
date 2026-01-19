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
## BOT COMBINADO: TELEGRAM + DISCORD (TIERED VERSION)
## ===============================

## ====================
## CONFIGURACIÓN TELEGRAM
## ====================
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'TU_TOKEN_AQUI')
CHANNEL_ID = os.environ.get('CHANNEL_ID', '-1003465544020') 
CHANNEL_LINK = os.environ.get('CHANNEL_LINK', 'https://t.me/+gRJVHxFmKXg0ZDVh')
TELEGRAM_ADMIN_ID = int(os.environ.get('TELEGRAM_ADMIN_ID', '0'))

telegram_links = {
    "1": os.environ.get('LINK_BOT1', 'https://t.me/bot1'),
    "2": os.environ.get('LINK_BOT2', 'https://t.me/bot2'),
    "3": os.environ.get('LINK_BOT3', 'https://t.me/bot3')
}

WHATS_NEW_TEXT = """
📢 **WEEKLY UPDATES & NEWS** 🚀
Here is what we have improved for you...
"""

GALLERY_TEXT = """
🤖 Our Bots and Exclusive Galleries ✨
...
"""

## ====================
## CONFIGURACIÓN DISCORD + STRIPE
## ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0"))

# ROL POR DEFECTO (Para usuarios antiguos o fallbacks)
DEFAULT_ROLE_ID = int(os.environ.get("DISCORD_ROLE_ID", "0")) 
ADMIN_LOG_CHANNEL_ID = int(os.environ.get("ADMIN_LOG_CHANNEL_ID", "0"))

stripe.api_key = STRIPE_SECRET_KEY
ACTIVE_STATUSES = ["active", "trialing"]

## ====================
## ⚙️ CONFIGURACIÓN DE TIERS (NUEVO)
## ====================
# Mapea el ID del PRODUCTO de Stripe (prod_XXXX) al ID del ROL de Discord.
# Puedes obtener el prod_ID en tu Dashboard de Stripe -> Catálogo de productos.
TIER_MAPPING = {
    # TIER 1 (Más barato)
    "prod_SZ9dmrnfH9AwhO": 1459004030381592606, 

    # TIER 2
    "prod_SZ9eQne47KPluz": 1459004119711879372,

    # TIER 3 (Más caro)
    "prod_SZ9ezfEZ3OhuFC": 1459004146970787861
}
# Lista de todos los roles que este bot administra (para poder quitarlos si expira)
MANAGED_ROLE_IDS = list(TIER_MAPPING.values())
if DEFAULT_ROLE_ID:
    MANAGED_ROLE_IDS.append(DEFAULT_ROLE_ID)

## ====================
## SUPABASE
## ====================
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
TABLE_NAME = "subscriptions_discord"

## ====================
## FUNCIÓN HELPER DE STRIPE (MODIFICADA)
## ====================
def get_customer_subscription_data(customer_id: str):
    """
    Retorna una tupla: (status, product_id)
    """
    try:
        subscriptions = stripe.Subscription.list(customer=customer_id, status='all', limit=20, expand=['data.plan.product']) 
        if not subscriptions.data:
            return "canceled", None
        
        # Prioridad: Buscar alguna activa
        for sub in subscriptions.data:
            if sub.status in ACTIVE_STATUSES:
                # Obtener el Product ID (prod_XXX)
                product_id = sub.plan.product
                if isinstance(product_id, dict): # A veces Stripe lo expande, a veces es string
                    product_id = product_id.get('id')
                return sub.status, product_id
        
        # Si no hay activas, devolver el status de la más reciente
        latest_sub = subscriptions.data[0]
        p_id = latest_sub.plan.product
        if isinstance(p_id, dict):
            p_id = p_id.get('id')
        return latest_sub.status, p_id

    except Exception as e:
        print(f"🚨 Error en get_customer_subscription_data para {customer_id}: {e}")
        return None, None

## ====================
## FASTAPI APP
## ====================
app = FastAPI()

@app.get("/")
async def home():
    return {"status": "Combined Bot Active (Tiered Version)"}

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook Error: {e}")
    
    event_type = event["type"]
    data_object = event["data"]["object"]
    customer_id = data_object.get("customer")

    if not customer_id and "customer" in data_object:
        customer_id = data_object["customer"]
    elif not customer_id and event_type.startswith("customer."):
        customer_id = data_object.get("id")

    if not customer_id:
        return JSONResponse(status_code=200, content={"message": "Event ignored (No customer ID)."})

    if event_type in ["customer.subscription.updated", "customer.subscription.created", "customer.subscription.deleted"]:
        final_status, _ = get_customer_subscription_data(customer_id) # Solo nos importa el status para actualizar DB
        
        if final_status is None:
            raise HTTPException(status_code=500, detail="Error retrieving status from Stripe.")

        try:
            # Actualizamos o insertamos en Supabase
            existing = supabase.table(TABLE_NAME).select("*").eq("stripe_customer_id", customer_id).execute()
            if existing.data:
                supabase.table(TABLE_NAME).update({
                    "subscription_status": final_status, 
                    "updated_at": discord.utils.utcnow().isoformat()
                }).eq("stripe_customer_id", customer_id).execute()
            else:
                supabase.table(TABLE_NAME).insert({
                    "stripe_customer_id": customer_id, 
                    "subscription_status": final_status, 
                    "updated_at": discord.utils.utcnow().isoformat()
                }).execute()
        except Exception as e:
            print(f"🚨 Supabase Error: {e}")

    return JSONResponse(status_code=200, content={"message": "Event handled."})

## ====================
## TELEGRAM BOT (Sin cambios mayores)
## ====================
telegram_bot = telebot.TeleBot(TELEGRAM_TOKEN)

def check_membership(user_id):
    clean_id = str(CHANNEL_ID).strip().replace("'", "").replace('"', "")
    if not clean_id.startswith("-100"): clean_id = "-100" + clean_id
    try:
        member = telegram_bot.get_chat_member(int(clean_id), user_id)
        return member.status in ['creator', 'administrator', 'member', 'restricted']
    except Exception as e:
        print(f"Tele-Check Error: {e}")
        return False

def get_main_menu():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔥 Img to Video Bot 1", url=telegram_links["1"]),
        InlineKeyboardButton("🤖 Img to Video Bot 2", url=telegram_links["2"]),
        InlineKeyboardButton("🤖 Nudify videos", url=telegram_links["3"]),
        InlineKeyboardButton("✨ What's New?", callback_data="whats_new"),
        InlineKeyboardButton("🔥 Gallery 🔥", callback_data="show_gallery")
    )
    return markup

@telegram_bot.message_handler(commands=['start'])
def send_welcome(message):
    if check_membership(message.from_user.id):
        telegram_bot.reply_to(message, "✅ **Access Granted**", reply_markup=get_main_menu(), parse_mode="Markdown")
    else:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("👉 Join Channel", url=CHANNEL_LINK), InlineKeyboardButton("🔄 Verify Me", callback_data="check_again"))
        telegram_bot.reply_to(message, "⛔ **Access Restricted**", reply_markup=markup, parse_mode="Markdown")

@telegram_bot.callback_query_handler(func=lambda call: call.data in ["whats_new", "show_gallery", "back_to_menu", "check_again"])
def handle_callbacks(call):
    if call.data == "whats_new":
        telegram_bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=WHATS_NEW_TEXT, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")))
    elif call.data == "show_gallery":
        telegram_bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=GALLERY_TEXT, disable_web_page_preview=False, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")))
    elif call.data == "back_to_menu":
        telegram_bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text="✅ **Access Granted**", reply_markup=get_main_menu(), parse_mode="Markdown")
    elif call.data == "check_again":
        if check_membership(call.from_user.id):
            telegram_bot.edit_message_text("✅ **Verified!**", call.message.chat.id, call.message.message_id, reply_markup=get_main_menu(), parse_mode="Markdown")
        else:
            telegram_bot.answer_callback_query(call.id, "❌ Join channel first!", show_alert=True)

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
    
    if not check_subscriptions.is_running():
        check_subscriptions.start()

@discord_client.event
async def on_message(message):
    if message.author == discord_client.user: return
    
    # COMANDO LINKEO
    if isinstance(message.channel, discord.DMChannel) and message.content.lower().startswith("!link"):
        parts = message.content.split()
        if len(parts) != 2:
            await message.channel.send("❌ Use: `!link email@example.com`")
            return

        user_email = parts[1].lower()
        if not re.match(r"[^@]+@[^@]+\.[^@]+", user_email):
            await message.channel.send("❌ Invalid email.")
            return

        try:
            customers = stripe.Customer.list(email=user_email, limit=1)
            if not customers.data:
                await message.channel.send("❌ No Stripe customer found.")
                return

            customer_id = customers.data[0].id
            # Obtenemos STATUS y PRODUCTO
            stripe_status, product_id = get_customer_subscription_data(customer_id)

            if stripe_status not in ACTIVE_STATUSES:
                await message.channel.send("⚠️ Email found, but no active subscription.")
                return
            
            # --- LÓGICA DE TIERS INSTANTÁNEA ---
            # Determinamos qué rol le toca
            target_role_id = TIER_MAPPING.get(product_id, DEFAULT_ROLE_ID)
            
            # Guardamos en Supabase
            response = supabase.table(TABLE_NAME).select("*").eq("stripe_customer_id", customer_id).execute()
            update_data = {
                "discord_user_id": str(message.author.id), 
                "subscription_status": stripe_status,
                "updated_at": discord.utils.utcnow().isoformat()
            }

            if response.data:
                existing_discord = response.data[0].get("discord_user_id")
                if existing_discord and existing_discord != str(message.author.id):
                    await message.channel.send("⚠️ Subscription linked to another Discord account.")
                    return
                supabase.table(TABLE_NAME).update(update_data).eq("stripe_customer_id", customer_id).execute()
            else:
                supabase.table(TABLE_NAME).insert(dict(stripe_customer_id=customer_id, **update_data)).execute()

            # Asignar rol inmediatamente si es posible
            if guild:
                member = guild.get_member(message.author.id)
                if member:
                    role_obj = guild.get_role(target_role_id)
                    if role_obj:
                        await member.add_roles(role_obj)
                        # Opcional: Quitar otros roles de tiers si existieran (limpieza)
                        for rid in MANAGED_ROLE_IDS:
                            if rid != target_role_id:
                                r_rem = guild.get_role(rid)
                                if r_rem and r_rem in member.roles:
                                    await member.remove_roles(r_rem)

            await message.channel.send("✅ Account linked! Access activated.")
            if admin_log_channel:
                await admin_log_channel.send(f"🟢 **Linked:** {message.author.mention} (`{user_email}`) -> Role: {target_role_id}")

        except Exception as e:
            print(f"Link error: {e}")
            await message.channel.send("❌ Error linking account.")

@tasks.loop(minutes=10)
async def check_subscriptions():
    print("🔄 Checking subscriptions (SAFE MODE)...")
    if not guild: return
    
    try:
        response = supabase.table(TABLE_NAME).select("*").neq("discord_user_id", "None").execute()
        
        for user_data in response.data:
            discord_user_id = user_data.get("discord_user_id")
            customer_id = user_data.get("stripe_customer_id")
            
            if not discord_user_id or not customer_id: continue

            # Obtener datos frescos de Stripe
            final_status, product_id = get_customer_subscription_data(customer_id)
            
            # Actualizar DB
            if final_status != user_data.get("subscription_status"):
                supabase.table(TABLE_NAME).update({
                    "subscription_status": final_status, 
                    "updated_at": discord.utils.utcnow().isoformat()
                }).eq("stripe_customer_id", customer_id).execute()

            member = guild.get_member(int(discord_user_id))
            if not member: continue

            if final_status in ACTIVE_STATUSES:
                # USUARIO ACTIVO: SOLO DAR ROL, NO QUITAR NADA
                target_role_id = TIER_MAPPING.get(product_id, DEFAULT_ROLE_ID)
                
                if target_role_id == 0:
                    print(f"⚠️ No role for product {product_id} (Check .env DEFAULT_ROLE_ID)")
                    continue

                target_role = guild.get_role(target_role_id)

                if target_role and target_role not in member.roles:
                    await member.add_roles(target_role, reason="Suscripción activa check")
                    print(f"✅ Rol {target_role.name} dado a {member.name}")

                # ❌ SECCIÓN DE LIMPIEZA ELIMINADA POR SEGURIDAD ❌
                # (Aquí estaba el código que borraba roles si no coincidían exactamente)

            else:
                # USUARIO CANCELADO/IMPAGO: AQUÍ SÍ QUITAMOS ROLES
                roles_removed = []
                for rid in MANAGED_ROLE_IDS:
                    r_obj = guild.get_role(rid)
                    if r_obj and r_obj in member.roles:
                        await member.remove_roles(r_obj, reason="Suscripción inactiva")
                        roles_removed.append(r_obj.name)
                
                if roles_removed and admin_log_channel:
                    await admin_log_channel.send(f"🔴 **Roles removidos (No Pago):** {member.mention} ({', '.join(roles_removed)})")

            await asyncio.sleep(0.1)

    except Exception as e:
        print(f"Error en check_subscriptions: {e}")

## ====================
## INICIO
## ====================
def start_fastapi():
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

def start_telegram():
    while True:
        try:
            telegram_bot.infinity_polling(skip_pending=True, timeout=90)
        except:
            time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=start_fastapi, daemon=True).start()
    threading.Thread(target=start_telegram, daemon=True).start()
    try:
        discord_client.run(DISCORD_BOT_TOKEN)
    except:
        pass
