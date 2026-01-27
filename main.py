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
## CONFIGURACIÓN
## ===============================

# --- SAFE MODE ---
SAFE_MODE_NO_BAN = False 

# Tiers
TIER_MAPPING = {
    "prod_SZ9dmrnfH9AwhO": 1459004030381592606, # Tier 1
    "prod_SZ9eQne47KPluz": 1459004119711879372, # Tier 2
    "prod_SZ9ezfEZ3OhuFC": 1459004146970787861  # Tier 3
}
TIER_3_PRODUCT_ID = "prod_SZ9ezfEZ3OhuFC"
TIER_3_ROLE_ID = TIER_MAPPING[TIER_3_PRODUCT_ID]

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0"))
DEFAULT_ROLE_ID = int(os.environ.get("DISCORD_ROLE_ID", "0"))
ADMIN_LOG_CHANNEL_ID = int(os.environ.get("ADMIN_LOG_CHANNEL_ID", "0"))

MANAGED_ROLES = list(TIER_MAPPING.values())
if DEFAULT_ROLE_ID: MANAGED_ROLES.append(DEFAULT_ROLE_ID)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

stripe.api_key = STRIPE_SECRET_KEY
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
TABLE_NAME = "subscriptions_discord"

ACTIVE_STATUSES = ["active", "trialing", "past_due"]

# Telegram Config
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID', '-100...') 
CHANNEL_LINK = os.environ.get('CHANNEL_LINK', 'https://t.me/...')
TELEGRAM_ADMIN_ID = int(os.environ.get('TELEGRAM_ADMIN_ID', '0'))
telegram_links = { "1": os.environ.get('LINK_BOT1'), "2": os.environ.get('LINK_BOT2'), "3": os.environ.get('LINK_BOT3'), "4": os.environ.get('LINK_BOT4') }

## ====================
## HELPER STRIPE (ASÍNCRONO - SOLUCIÓN AL CRASH)
## ====================
async def get_customer_subscription_data(customer_id: str):
    """
    Ejecuta las llamadas a Stripe en un hilo separado para NO BLOQUEAR a Discord.
    """
    def _blocking_stripe_call():
        try:
            # Prioridad 1: Activa
            active = stripe.Subscription.list(customer=customer_id, status='active', limit=1, expand=['data.plan.product'])
            if active.data: return "active", active.data[0].plan.product
            
            # Prioridad 2: Trialing
            trial = stripe.Subscription.list(customer=customer_id, status='trialing', limit=1, expand=['data.plan.product'])
            if trial.data: return "trialing", trial.data[0].plan.product

            # Prioridad 3: Past Due
            past = stripe.Subscription.list(customer=customer_id, status='past_due', limit=1, expand=['data.plan.product'])
            if past.data: return "past_due", past.data[0].plan.product

            return "canceled", None
        except Exception as e:
            print(f"🚨 Stripe Error {customer_id}: {e}")
            return None, None

    # AQUÍ ESTÁ LA MAGIA: asyncio.to_thread evita que el bot se congele
    return await asyncio.to_thread(_blocking_stripe_call)

def calculate_roles_to_assign(product_obj):
    product_id = product_obj.get('id') if isinstance(product_obj, dict) else product_obj
    roles_to_give = []
    tier_role = TIER_MAPPING.get(product_id)

    if tier_role:
        roles_to_give.append(tier_role)
        if tier_role == TIER_3_ROLE_ID:
            roles_to_give.append(DEFAULT_ROLE_ID)
    else:
        roles_to_give.append(TIER_3_ROLE_ID)
        roles_to_give.append(DEFAULT_ROLE_ID)
        
    return list(set([r for r in roles_to_give if r != 0]))

## ====================
## FASTAPI
## ====================
app = FastAPI()

@app.get("/")
async def home():
    return {"status": "Bot Active - Async Fix Applied"}

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except:
        return JSONResponse(status_code=400, content={"error": "invalid"})
    return JSONResponse(status_code=200, content={"message": "ok"})

## ====================
## TELEGRAM 
## ====================
telegram_bot = telebot.TeleBot(TELEGRAM_TOKEN)
# ... (Pega aquí tus funciones de Telegram intactas) ...
# check_membership, get_main_menu, handlers, etc.
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
    btn4 = InlineKeyboardButton("🔥 Img to img Bot ", url=telegram_links["4"])
    markup.add(btn1, btn2, btn3, btn4)
    return markup

@telegram_bot.message_handler(commands=['start'])
def send_welcome(message):
    if check_membership(message.from_user.id):
        telegram_bot.reply_to(message, "✅ **Access Granted**", reply_markup=get_main_menu(), parse_mode="Markdown")
    else:
        # User is NOT a member, show "Join Channel" button
        markup = InlineKeyboardMarkup()
        # We use the CHANNEL_LINK variable you defined at the top of your script
        btn_join = InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)
        markup.add(btn_join)

        telegram_bot.reply_to(
            message, 
            "⛔ **Access Denied**\n\nYou must join the channel first to use this bot.", 
            reply_markup=markup,
            parse_mode="Markdown"
        )

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
    print(f"✅ Discord Ready. SafeMode: {SAFE_MODE_NO_BAN}")
    guild = discord_client.get_guild(DISCORD_GUILD_ID)
    if guild: admin_log_channel = discord_client.get_channel(ADMIN_LOG_CHANNEL_ID)
    if not check_subscriptions.is_running():
        check_subscriptions.start()

@discord_client.event
async def on_message(message):
    if message.author.bot: return
    
    if isinstance(message.channel, discord.DMChannel) and message.content.lower().startswith("!link"):
        try:
            parts = message.content.split()
            if len(parts) != 2:
                await message.channel.send("❌ Use: `!link email@example.com`")
                return
            email = parts[1].lower()
            
            # Esto es ligero, puede ir síncrono o envolverlo también, pero stripe.Customer.list suele ser rápido
            # Lo envolvemos por seguridad
            custs = await asyncio.to_thread(stripe.Customer.list, email=email, limit=1)
            
            if not custs.data:
                await message.channel.send("❌ No customer found.")
                return
            
            c_id = custs.data[0].id
            
            # AWAIT AQUI ES CRUCIAL
            status, prod = await get_customer_subscription_data(c_id)
            
            if status not in ACTIVE_STATUSES:
                await message.channel.send("⚠️ Found account, but no active subscription.")
                return

            now = discord.utils.utcnow().isoformat()
            row = supabase.table(TABLE_NAME).select("*").eq("stripe_customer_id", c_id).execute()
            if row.data:
                exist_u = row.data[0].get("discord_user_id")
                if exist_u and exist_u != str(message.author.id):
                    await message.channel.send("⚠️ Account linked to another Discord user.")
                    return
                supabase.table(TABLE_NAME).update({"discord_user_id": str(message.author.id), "subscription_status": status, "updated_at": now}).eq("stripe_customer_id", c_id).execute()
            else:
                supabase.table(TABLE_NAME).insert({"stripe_customer_id": c_id, "discord_user_id": str(message.author.id), "subscription_status": status, "updated_at": now}).execute()

            roles = calculate_roles_to_assign(prod)
            if guild:
                mem = guild.get_member(message.author.id)
                if mem:
                    for rid in roles:
                        r = guild.get_role(rid)
                        if r: await mem.add_roles(r)
            
            await message.channel.send("✅ Linked successfully!")
            if admin_log_channel: await admin_log_channel.send(f"🟢 Link: {message.author.mention} ({email})")
            
        except Exception as e:
            print(f"Link Err: {e}")
            await message.channel.send("❌ Error.")

@tasks.loop(minutes=10) # Aumentado a 10 min para reducir carga
async def check_subscriptions():
    print("🔄 Checking subscriptions (ASYNC FIXED)...")
    if not guild: return
    
    try:
        response = supabase.table(TABLE_NAME).select("*").neq("discord_user_id", "None").execute()
        user_active_map = {}

        # 1. ACTUALIZAR CON STRIPE (Ahora con await para no bloquear)
        for row in response.data:
            c_id = row.get("stripe_customer_id")
            d_id = row.get("discord_user_id")
            current_db_status = row.get("subscription_status")

            # AWAIT ES OBLIGATORIO AQUI
            real_status, prod_obj = await get_customer_subscription_data(c_id)
            
            if real_status is None: continue 

            if real_status != current_db_status:
                supabase.table(TABLE_NAME).update({
                    "subscription_status": real_status,
                    "updated_at": discord.utils.utcnow().isoformat()
                }).eq("stripe_customer_id", c_id).execute()

            if d_id not in user_active_map: user_active_map[d_id] = False
            if real_status in ACTIVE_STATUSES: user_active_map[d_id] = True
            
            # Pequeña pausa para dejar respirar a la CPU entre usuarios
            await asyncio.sleep(0.5)

        # 2. PROCESAR ROLES
        processed_users = set()
        for row in response.data:
            d_id = row.get("discord_user_id")
            if d_id in processed_users: continue
            processed_users.add(d_id)

            member = guild.get_member(int(d_id))
            if not member: continue

            is_user_safe = user_active_map.get(d_id, False)

            if is_user_safe:
                active_row = next((r for r in response.data if r["discord_user_id"] == d_id and r["subscription_status"] in ACTIVE_STATUSES), None)
                if active_row:
                    # AWAIT OBLIGATORIO TAMBIEN AQUI
                    _, prod_obj = await get_customer_subscription_data(active_row["stripe_customer_id"])
                    roles_to_add = calculate_roles_to_assign(prod_obj)
                    
                    for rid in roles_to_add:
                        r = guild.get_role(rid)
                        if r and r not in member.roles:
                            await member.add_roles(r, reason="Sub Activa")
                            print(f"➕ Rol {r.name} a {member.display_name}")

            else:
                if SAFE_MODE_NO_BAN:
                    pass
                else:
                    roles_removed = []
                    for rid in MANAGED_ROLES:
                        r = guild.get_role(rid)
                        if r and r in member.roles:
                            await member.remove_roles(r, reason="Baja")
                            roles_removed.append(r.name)
                    if roles_removed and admin_log_channel:
                        await admin_log_channel.send(f"🔴 **Baja:** {member.mention} perdió roles.")

            await asyncio.sleep(0.1)

    except Exception as e:
        print(f"Error Loop: {e}")

## ====================
## RUNNERS
## ====================
def start_fastapi():
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

def start_telegram():
    while True:
        try: telegram_bot.infinity_polling(skip_pending=True, timeout=90)
        except: time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=start_fastapi, daemon=True).start()
    threading.Thread(target=start_telegram, daemon=True).start()
    try: discord_client.run(DISCORD_BOT_TOKEN)
    except: pass
