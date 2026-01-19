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
## BOT COMBINADO: TELEGRAM + DISCORD (TIERED SAFE MODE)
## ===============================

## ====================
## CONFIGURACIÓN DE TIERS (NUEVO)
## ====================
# Estos son tus tiers nuevos.
TIER_MAPPING = {
    "prod_SZ9dmrnfH9AwhO": 1459004030381592606, # Tier 1
    "prod_SZ9eQne47KPluz": 1459004119711879372, # Tier 2
    "prod_SZ9ezfEZ3OhuFC": 1459004146970787861  # Tier 3
}

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

Here is what we have improved for you:

🤖 **Bot 2 (Img to Video):**
• Added new:
• deepthorat machine
• Footjob
• ALL VERSIONS HD

🤖 **Bot 3 (Video to Video):**
• bg tts

✨ _Stay tuned to our channel for more updates!_
"""

GALLERY_TEXT = """
🤖 Our Bots and Exclusive Galleries ✨

🖼 Image to Video (monkeyvideos 1)
Gallery: https://postimg.cc/gallery/Kx5KSSs

🖼 Image to Video (videos69 2)
Gallery: https://postimg.cc/gallery/z3W9JnW

📹 Nude videos
Gallery: https://postimg.cc/0K6R05tS

Enjoy! 🔥
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

# EL ROL LEGACY (EL QUE USABAS ANTES)
DEFAULT_ROLE_ID = int(os.environ.get("DISCORD_ROLE_ID", "0")) 
ADMIN_LOG_CHANNEL_ID = int(os.environ.get("ADMIN_LOG_CHANNEL_ID", "0"))

stripe.api_key = STRIPE_SECRET_KEY
# AGREGUE 'past_due' PARA QUE NO SAQUE A GENTE CUYO PAGO FALLÓ HOY PERO SE ARREGLA MAÑANA
ACTIVE_STATUSES = ["active", "trialing", "past_due"]

# Juntamos todos los roles posibles (Tiers + Legacy) para saber cuáles administrar
MANAGED_ROLES = list(TIER_MAPPING.values())
if DEFAULT_ROLE_ID:
    MANAGED_ROLES.append(DEFAULT_ROLE_ID)

## ====================
## SUPABASE
## ====================
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
TABLE_NAME = "subscriptions_discord"

## ====================
## FUNCIÓN HELPER DE STRIPE (MEJORADA PARA PRODUCTOS)
## ====================
def get_customer_subscription_data(customer_id: str):
    """
    Devuelve (status, product_id).
    Si hay multiple, prioriza la activa.
    """
    try:
        # Expandimos 'data.plan.product' para obtener el ID real (prod_XXX)
        subscriptions = stripe.Subscription.list(customer=customer_id, status='all', limit=20, expand=['data.plan.product']) 
        if not subscriptions.data:
            return "canceled", None
        
        # 1. Buscar suscripción activa
        for sub in subscriptions.data:
            if sub.status in ACTIVE_STATUSES:
                prod_obj = sub.plan.product
                # A veces stripe devuelve objeto, a veces string ID
                p_id = prod_obj.get('id') if isinstance(prod_obj, dict) else prod_obj
                return sub.status, p_id
        
        # 2. Si no hay activa, devolver la última
        latest = subscriptions.data[0]
        prod_obj = latest.plan.product
        p_id = prod_obj.get('id') if isinstance(prod_obj, dict) else prod_obj
        return latest.status, p_id

    except Exception as e:
        print(f"🚨 Error en Stripe Helper para {customer_id}: {e}")
        return None, None

## ====================
## FASTAPI APP
## ====================
app = FastAPI()

@app.get("/")
async def home():
    return {"status": "Combined Bot Active (Safe Mode)"}

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid payload/sig")
    
    event_type = event["type"]
    data_object = event["data"]["object"]
    customer_id = data_object.get("customer")

    if not customer_id and "customer" in data_object:
        customer_id = data_object["customer"]
    elif not customer_id and event_type.startswith("customer."):
        customer_id = data_object.get("id")

    if not customer_id:
        return JSONResponse(status_code=200, content={"message": "Ignored"})

    if event_type in ["customer.subscription.updated", "customer.subscription.created", "customer.subscription.deleted"]:
        # Solo actualizamos el status en DB
        status, _ = get_customer_subscription_data(customer_id)
        if status:
            try:
                exists = supabase.table(TABLE_NAME).select("*").eq("stripe_customer_id", customer_id).execute()
                if exists.data:
                    supabase.table(TABLE_NAME).update({
                        "subscription_status": status, 
                        "updated_at": discord.utils.utcnow().isoformat()
                    }).eq("stripe_customer_id", customer_id).execute()
                else:
                    supabase.table(TABLE_NAME).insert({
                        "stripe_customer_id": customer_id, 
                        "subscription_status": status, 
                        "updated_at": discord.utils.utcnow().isoformat()
                    }).execute()
            except Exception as e:
                print(f"🚨 Supabase webhook error: {e}")

    return JSONResponse(status_code=200, content={"message": "Handled"})

## ====================
## TELEGRAM BOT (TU CÓDIGO INTACTO)
## ====================
telegram_bot = telebot.TeleBot(TELEGRAM_TOKEN)

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

@telegram_bot.message_handler(commands=['setlink'])
def admin_setlink(message):
    if message.from_user.id == TELEGRAM_ADMIN_ID:
        try:
            parts = message.text.split()
            if parts[1] in telegram_links:
                telegram_links[parts[1]] = parts[2]
                telegram_bot.reply_to(message, f"✅ Link {parts[1]} updated.")
        except: pass

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
    else:
        print(f"❌ Guild {DISCORD_GUILD_ID} not found.")
    
    if not check_subscriptions.is_running():
        check_subscriptions.start()

@discord_client.event
async def on_message(message):
    if message.author == discord_client.user: return

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
            customers = stripe.Customer.list(email=user_email, limit=1)
            if not customers.data:
                await message.channel.send("❌ No customer found with that email in Stripe.")
                return

            customer_id = customers.data[0].id
            # Usamos la nueva función que trae el producto
            stripe_status, product_id = get_customer_subscription_data(customer_id)

            if stripe_status not in ACTIVE_STATUSES:
                await message.channel.send("⚠️ Email found, but no active subscription.")
                return
            
            # --- DETERMINAR ROL ---
            target_role_id = TIER_MAPPING.get(product_id, DEFAULT_ROLE_ID)
            
            response = supabase.table(TABLE_NAME).select("*").eq("stripe_customer_id", customer_id).execute()
            update_data = {
                "discord_user_id": str(message.author.id), 
                "subscription_status": stripe_status,
                "updated_at": discord.utils.utcnow().isoformat()
            }

            if response.data:
                existing = response.data[0].get("discord_user_id")
                if existing and existing != str(message.author.id):
                    await message.channel.send("⚠️ Subscription linked to another Discord account.")
                    return
                supabase.table(TABLE_NAME).update(update_data).eq("stripe_customer_id", customer_id).execute()
            else:
                supabase.table(TABLE_NAME).insert(dict(stripe_customer_id=customer_id, **update_data)).execute()

            # ASIGNAR ROL INMEDIATO
            if guild:
                member = guild.get_member(message.author.id)
                role_obj = guild.get_role(target_role_id)
                if member and role_obj:
                    await member.add_roles(role_obj, reason="Linkeo exitoso")

            await message.channel.send("✅ Account linked! Premium access activated.")
            if admin_log_channel:
                await admin_log_channel.send(f"🟢 **Link:** {message.author.mention} (`{user_email}`) -> RoleID: {target_role_id}")

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

            final_status, product_id = get_customer_subscription_data(customer_id)
            
            # Actualizar DB
            if final_status != user_data.get("subscription_status"):
                supabase.table(TABLE_NAME).update({
                    "subscription_status": final_status, 
                    "updated_at": discord.utils.utcnow().isoformat()
                }).eq("stripe_customer_id", customer_id).execute()

            member = guild.get_member(int(discord_user_id))
            if not member: continue

            # === LÓGICA DE SEGURIDAD MÁXIMA ===
            
            if final_status in ACTIVE_STATUSES:
                # USUARIO ACTIVO:
                # 1. Averiguamos qué rol le toca.
                # Si es un producto nuevo -> Rol del Tier.
                # Si es un producto viejo (desconocido) -> Rol Default (Legacy).
                role_to_give_id = TIER_MAPPING.get(product_id, DEFAULT_ROLE_ID)
                
                if role_to_give_id == 0: continue

                role_obj = guild.get_role(role_to_give_id)

                # 2. SOLO SUMAMOS ROL.
                # NUNCA corremos un 'remove_roles' aquí.
                # Así los Legacy conservan su rol, y si alguien compra Upgrade, se queda con los dos.
                if role_obj and role_obj not in member.roles:
                    await member.add_roles(role_obj, reason="Suscripción activa check")
                    print(f"✅ Rol {role_obj.name} dado a {member.display_name}")

            else:
                # USUARIO INACTIVO (CANCELADO/IMPAGO REAL):
                # Aquí sí limpiamos para que no tengan acceso gratis.
                # Quitamos CUALQUIERA de los roles gestionados (Tiers o Legacy).
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
## MAIN
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
    except: pass
