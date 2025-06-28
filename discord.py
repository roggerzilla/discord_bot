## ===============================
## BOT DE DISCORD + WEBHOOK FASTAPI
## CONTROL DE ROLES PREMIUM POR STRIPE
## CON COMANDO '!vincular' POR DM
## ===============================

import os
import discord
from discord.ext import tasks
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from supabase import create_client, Client
import uvicorn
import json
import hmac
import hashlib
import stripe

## ====================
## CONFIGURACIÓN
## ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID"))
DISCORD_ROLE_ID = int(os.environ.get("DISCORD_ROLE_ID"))

stripe.api_key = STRIPE_SECRET_KEY

## ====================
## SUPABASE
## ====================
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
TABLE_NAME = "subscriptions_discord"

## ====================
## FASTAPI APP PARA WEBHOOK STRIPE
## ====================
app = FastAPI()

@app.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        print(f"Webhook verification failed: {e}")
        return JSONResponse(status_code=400, content={"message": "Webhook verification failed."})
    
    event_type = event["type"]
    data_object = event["data"]["object"]
    customer_id = data_object.get("customer")

    if event_type in ["customer.subscription.updated", "customer.subscription.created"]:
        status = data_object.get("status")
        supabase.table(TABLE_NAME).update(
            {"subscription_status": status}
        ).eq("stripe_customer_id", customer_id).execute()
        return {"message": "Subscription status updated."}
    elif event_type == "customer.subscription.deleted":
        supabase.table(TABLE_NAME).update(
            {"subscription_status": "canceled"}
        ).eq("stripe_customer_id", customer_id).execute()
        return {"message": "Subscription canceled."}

    return {"message": "Event ignored."}

## ====================
## DISCORD BOT
## ====================
intents = discord.Intents.default()
intents.members = True
intents.messages = True
client = discord.Client(intents=intents)

guild = None
role = None

@client.event
async def on_ready():
    global guild, role
    print(f"✅ Logged in as {client.user}")
    guild = client.get_guild(DISCORD_GUILD_ID)
    role = guild.get_role(DISCORD_ROLE_ID)
    check_subscriptions.start()

## ============ Comando '!vincular' por DM ============
@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if isinstance(message.channel, discord.DMChannel):
        if message.content.startswith("!vincular"):
            parts = message.content.split()
            if len(parts) != 2:
                await message.channel.send("❌ Usa el comando correctamente:\n`!vincular tuemail@correo.com`")
                return

            user_email = parts[1]

            try:
                customers = stripe.Customer.list(email=user_email, limit=1)
                if not customers.data:
                    await message.channel.send("❌ No se encontró un cliente con ese correo en Stripe. Verifica que sea el mismo que usaste al pagar.")
                    return

                customer_id = customers.data[0].id

                # Vincular discord_user_id en Supabase
                supabase.table(TABLE_NAME).update(
                    {"discord_user_id": str(message.author.id)}
                ).eq("stripe_customer_id", customer_id).execute()

                await message.channel.send("✅ Tu cuenta de Discord ha sido vinculada correctamente con tu suscripción. Tu acceso premium se activará pronto.")
                print(f"✅ Vinculado {message.author} con {customer_id}")

            except Exception as e:
                print(f"Error vinculando: {e}")
                await message.channel.send("❌ Ocurrió un error al intentar vincular tu cuenta. Contacta al soporte si persiste el problema.")
    else:
        # Ignorar mensajes en canales públicos
        return

## ============ Asignación automática de roles ============
@tasks.loop(minutes=10)
async def check_subscriptions():
    print("🔄 Checking subscriptions...")
    try:
        response = supabase.table(TABLE_NAME).select("discord_user_id, subscription_status").execute()
        for user_data in response.data:
            discord_user_id = user_data.get("discord_user_id")
            status = user_data.get("subscription_status")

            if not discord_user_id:
                continue

            member = guild.get_member(int(discord_user_id))
            if not member:
                continue

            if status == "active":
                if role not in member.roles:
                    await member.add_roles(role, reason="Subscription active")
                    print(f"✅ Added role to {member.display_name}")
            else:
                if role in member.roles:
                    await member.remove_roles(role, reason="Subscription inactive or canceled")
                    print(f"❌ Removed role from {member.display_name}")
    except Exception as e:
        print(f"Error en check_subscriptions: {e}")

## ====================
## INICIO UVICORN PARA FASTAPI
## ====================
if __name__ == "__main__":
    import threading

    def start_fastapi():
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

    threading.Thread(target=start_fastapi).start()
    client.run(DISCORD_BOT_TOKEN)
