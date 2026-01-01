import os
import discord
from discord.ext import tasks
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from supabase import create_client, Client
import uvicorn
import hmac
import hashlib
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
## BOT COMBINADO: TELEGRAM + DISCORD
## ===============================

## ====================
## CONFIGURACIÓN TELEGRAM
## ====================
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'TU_TOKEN_AQUI')
CHANNEL_ID = os.environ.get('CHANNEL_ID', '-1003465544020') 
CHANNEL_LINK = os.environ.get('CHANNEL_LINK', 'https://t.me/monkey_videos')
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
DISCORD_ROLE_ID = int(os.environ.get("DISCORD_ROLE_ID", "0"))
ADMIN_LOG_CHANNEL_ID = int(os.environ.get("ADMIN_LOG_CHANNEL_ID", "0"))

stripe.api_key = STRIPE_SECRET_KEY
ACTIVE_STATUSES = ["active", "trialing"]

## ====================
## SUPABASE
## ====================
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
TABLE_NAME = "subscriptions_discord"

## ====================
## FUNCIÓN HELPER DE STRIPE
## ====================
def get_customer_aggregated_status(customer_id: str):
    try:
        subscriptions = stripe.Subscription.list(customer=customer_id, status='all', limit=20) 
        if not subscriptions.data:
            return "canceled"
        for sub in subscriptions.data:
            if sub.status in ACTIVE_STATUSES:
                return sub.status
        return subscriptions.data[0].status
    except Exception as e:
        print(f"🚨 Error en get_customer_aggregated_status para {customer_id}: {e}")
        return None

## ====================
## FASTAPI APP (WEBHOOK STRIPE + HEALTHCHECK)
## ====================
app = FastAPI()

@app.get("/")
async def home():
    return {"status": "Combined Bot Active (Telegram + Discord)"}

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        print(f"Webhook Error: Invalid payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        print(f"Webhook Error: Invalid signature: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    event_type = event["type"]
    data_object = event["data"]["object"]
    customer_id = data_object.get("customer")

    if not customer_id and "customer" in data_object:
        customer_id = data_object["customer"]
    elif not customer_id and event_type.startswith("customer."):
        customer_id = data_object.get("id")

    if not customer_id:
        return JSONResponse(status_code=200, content={"message": "Event ignored (No customer ID)."})

    print(f"Received Stripe event: {event_type} for customer {customer_id}")

    if event_type in ["customer.subscription.updated", "customer.subscription.created", "customer.subscription.deleted"]:
        final_status = get_customer_aggregated_status(customer_id)
        if final_status is None:
            raise HTTPException(status_code=500, detail="Error retrieving aggregated status from Stripe.")

        try:
            response = supabase.table(TABLE_NAME).select("*").eq("stripe_customer_id", customer_id).execute()
            if response.data:
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
            return JSONResponse(status_code=200, content={"message": f"Status updated for customer {customer_id}."})
        except Exception as e:
            print(f"🚨 Supabase Error: {e}")
            raise HTTPException(status_code=500, detail=f"Supabase processing error: {e}")

    return JSONResponse(status_code=200, content={"message": "Event handled."})

## ====================
## TELEGRAM BOT
## ====================
telegram_bot = telebot.TeleBot(TELEGRAM_TOKEN)

def check_membership(user_id):
    clean_id = str(CHANNEL_ID).strip().replace("'", "").replace('"', "")
    if not clean_id.startswith("-100"):
        clean_id = "-100" + clean_id
    print(f"DEBUG: Checking {user_id} in {clean_id}", flush=True)
    try:
        chat_id_to_check = int(clean_id)
        member = telegram_bot.get_chat_member(chat_id_to_check, user_id)
        if member.status in ['creator', 'administrator', 'member', 'restricted']:
            return True
        print(f"DEBUG: Rejected. Status: '{member.status}'", flush=True)
        return False
    except Exception as e:
        print(f"ERROR: {e}", flush=True)
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
    user_id = message.from_user.id
    if check_membership(user_id):
        telegram_bot.reply_to(message, "✅ **Access Granted**\nSelect an available server below:", reply_markup=get_main_menu(), parse_mode="Markdown")
    else:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("👉 Join Channel First", url=CHANNEL_LINK))
        markup.add(InlineKeyboardButton("🔄 I Joined, Verify Me", callback_data="check_again"))
        telegram_bot.reply_to(message, "⛔ **Access Restricted**\n\nTo use our bots, you must join our official channel first.", reply_markup=markup, parse_mode="Markdown")

@telegram_bot.callback_query_handler(func=lambda call: call.data == "whats_new")
def show_updates(call):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu"))
    telegram_bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=WHATS_NEW_TEXT,
        parse_mode="Markdown",
        reply_markup=markup
    )

@telegram_bot.callback_query_handler(func=lambda call: call.data == "show_gallery")
def show_gallery_menu(call):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu"))
    telegram_bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=GALLERY_TEXT,
        disable_web_page_preview=False, 
        reply_markup=markup
    )

@telegram_bot.callback_query_handler(func=lambda call: call.data == "back_to_menu")
def back_to_main(call):
    telegram_bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="✅ **Access Granted**\nSelect an available server below:",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )

@telegram_bot.callback_query_handler(func=lambda call: call.data == "check_again")
def callback_verify(call):
    if check_membership(call.from_user.id):
        telegram_bot.edit_message_text("✅ **Verified!** Choose your bot:", call.message.chat.id, call.message.message_id, reply_markup=get_main_menu(), parse_mode="Markdown")
    else:
        telegram_bot.answer_callback_query(call.id, "❌ You are not in the channel yet. Join and try again.", show_alert=True)

@telegram_bot.message_handler(commands=['setlink'])
def admin_setlink(message):
    if message.from_user.id == TELEGRAM_ADMIN_ID:
        try:
            parts = message.text.split()
            bot_num = parts[1]
            new_url = parts[2]
            if bot_num in telegram_links:
                telegram_links[bot_num] = new_url
                telegram_bot.reply_to(message, f"✅ Link {bot_num} updated.")
            else:
                telegram_bot.reply_to(message, "❌ Only 1, 2, 3.")
        except:
            telegram_bot.reply_to(message, "⚠️ Error.")

## ====================
## DISCORD BOT
## ====================
intents = discord.Intents.default()
intents.members = True
intents.messages = True
intents.message_content = True
discord_client = discord.Client(intents=intents)

guild = None
role = None
admin_log_channel = None

@discord_client.event
async def on_ready():
    global guild, role, admin_log_channel
    print(f"✅ Discord logged in as {discord_client.user}")
    guild = discord_client.get_guild(DISCORD_GUILD_ID)
    if guild:
        role = guild.get_role(DISCORD_ROLE_ID)
        admin_log_channel = discord_client.get_channel(ADMIN_LOG_CHANNEL_ID)
        print(f"Guild: {guild.name}, Role: {role.name if role else 'Not found'}")
    else:
        print(f"❌ Guild {DISCORD_GUILD_ID} not found.")
    
    if not check_subscriptions.is_running():
        check_subscriptions.start()

@discord_client.event
async def on_message(message):
    if message.author == discord_client.user:
        return

    if isinstance(message.channel, discord.DMChannel):
        if message.content.lower().startswith("!link"):
            parts = message.content.split()
            if len(parts) != 2:
                await message.channel.send("❌ Use: `!link youremail@example.com`")
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
                stripe_status = get_customer_aggregated_status(customer_id)

                if stripe_status not in ACTIVE_STATUSES:
                    await message.channel.send("⚠️ Email found, but no active subscription.")
                    return
                
                response = supabase.table(TABLE_NAME).select("*").eq("stripe_customer_id", customer_id).execute()

                update_data = {
                    "discord_user_id": str(message.author.id), 
                    "subscription_status": stripe_status,
                    "updated_at": discord.utils.utcnow().isoformat()
                }

                if response.data:
                    existing_discord_id = response.data[0].get("discord_user_id")
                    if existing_discord_id and existing_discord_id != str(message.author.id):
                        await message.channel.send("⚠️ This subscription is linked to another Discord account.")
                        if admin_log_channel:
                            await admin_log_channel.send(f"🟡 **Intento re-link fallido:** {message.author.mention} intentó usar el email de {existing_discord_id}.")
                        return
                    
                    supabase.table(TABLE_NAME).update(update_data).eq("stripe_customer_id", customer_id).execute()
                else:
                    insert_data = {"stripe_customer_id": customer_id}
                    insert_data.update(update_data)
                    supabase.table(TABLE_NAME).insert(insert_data).execute()

                await message.channel.send("✅ Account linked! Premium access activated.")
                
                if admin_log_channel:
                    await admin_log_channel.send(f"🟢 **Vínculo exitoso:** {message.author.mention} (`{user_email}`).")

            except Exception as e:
                print(f"Link error: {e}")
                await message.channel.send("❌ Error linking account.")

@tasks.loop(minutes=10)
async def check_subscriptions():
    print("🔄 Checking subscriptions...")
    if not guild or not role:
        return
    
    try:
        response = supabase.table(TABLE_NAME).select("*").neq("discord_user_id", "None").execute()
        user_access_map = {}

        for user_data in response.data:
            discord_user_id = user_data.get("discord_user_id")
            customer_id = user_data.get("stripe_customer_id")
            db_status = user_data.get("subscription_status")

            if not discord_user_id or not customer_id:
                continue

            final_status = get_customer_aggregated_status(customer_id)
            if final_status is None:
                final_status = db_status 

            if final_status != db_status:
                supabase.table(TABLE_NAME).update({
                    "subscription_status": final_status, 
                    "updated_at": discord.utils.utcnow().isoformat()
                }).eq("stripe_customer_id", customer_id).execute()

            is_active_now = final_status in ACTIVE_STATUSES
            
            if discord_user_id not in user_access_map:
                user_access_map[discord_user_id] = is_active_now
            else:
                if is_active_now:
                    user_access_map[discord_user_id] = True

        for discord_user_id, should_have_access in user_access_map.items():
            try:
                member = guild.get_member(int(discord_user_id))
                if not member:
                    continue

                if should_have_access:
                    if role not in member.roles:
                        await member.add_roles(role, reason="Suscripción activa validada")
                        print(f"✅ Rol AGREGADO a {member.display_name}")
                        if admin_log_channel:
                            await admin_log_channel.send(f"🟢 **Rol agregado/mantenido:** {member.mention}")
                else:
                    if role in member.roles:
                        await member.remove_roles(role, reason="Sin suscripciones activas")
                        print(f"❌ Rol REMOVIDO a {member.display_name}")
                        if admin_log_channel:
                            await admin_log_channel.send(f"🔴 **Rol removido:** {member.mention} (Suscripción inactiva).")
                
                await asyncio.sleep(0.1)

            except Exception as e:
                print(f"Error gestionando rol para {discord_user_id}: {e}")

    except Exception as e:
        print(f"Error crítico en check_subscriptions: {e}")

## ====================
## FUNCIONES DE INICIO
## ====================
def start_fastapi():
    """Inicia el servidor FastAPI"""
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

def start_telegram():
    """Inicia el bot de Telegram con auto-reinicio"""
    print("🤖 Iniciando Telegram Bot...")
    while True:
        try:
            telegram_bot.infinity_polling(skip_pending=True, timeout=90, long_polling_timeout=5)
        except Exception as e:
            print(f"⚠️ Telegram bot se cayó: {e}")
            print("🔄 Reiniciando en 5 segundos...")
            time.sleep(5)

def start_discord():
    """Inicia el bot de Discord"""
    print("🤖 Iniciando Discord Bot...")
    discord_client.run(DISCORD_BOT_TOKEN)

## ====================
## MAIN
## ====================
if __name__ == "__main__":
    print("=" * 50)
    print("🚀 INICIANDO BOT COMBINADO")
    print("=" * 50)
    
    # Iniciar FastAPI en un thread
    fastapi_thread = threading.Thread(target=start_fastapi, daemon=True)
    fastapi_thread.start()
    print("✅ FastAPI iniciado")
    
    # Iniciar Telegram en un thread
    telegram_thread = threading.Thread(target=start_telegram, daemon=True)
    telegram_thread.start()
    print("✅ Telegram Bot iniciado")
    
    # Iniciar Discord en el thread principal
    try:
        start_discord()
    except KeyboardInterrupt:
        print("\n👋 Bot detenido por el usuario")
    except Exception as e:
        print(f"❌ Error crítico: {e}")
