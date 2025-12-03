import os
import discord
from discord.ext import tasks
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from supabase import create_client, Client
import uvicorn
import json
import hmac
import hashlib
import stripe
import re
from dotenv import load_dotenv
import asyncio
import threading

load_dotenv()

## ===============================
## BOT DE DISCORD + WEBHOOK FASTAPI
## CONTROL DE ROLES PREMIUM POR STRIPE
## ===============================

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
ADMIN_LOG_CHANNEL_ID = int(os.environ.get("ADMIN_LOG_CHANNEL_ID"))

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
    """
    Verifica TODAS las suscripciones de un cliente y devuelve el "mejor" estado.
    Si CUALQUIERA está activa, devuelve "active".
    """
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
## FASTAPI APP PARA WEBHOOK STRIPE
## ====================
app = FastAPI()

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
## DISCORD BOT
## ====================
intents = discord.Intents.default()
intents.members = True
intents.messages = True
intents.message_content = True
client = discord.Client(intents=intents)

guild = None
role = None
admin_log_channel = None

@client.event
async def on_ready():
    global guild, role, admin_log_channel
    print(f"✅ Logged in as {client.user}")
    guild = client.get_guild(DISCORD_GUILD_ID)
    if guild:
        role = guild.get_role(DISCORD_ROLE_ID)
        admin_log_channel = client.get_channel(ADMIN_LOG_CHANNEL_ID)
        print(f"Guild: {guild.name}, Role: {role.name if role else 'Not found'}")
    else:
        print(f"❌ Guild {DISCORD_GUILD_ID} not found.")
    
    if not check_subscriptions.is_running():
        check_subscriptions.start()

## ============ Comando '!link' ============
@client.event
async def on_message(message):
    if message.author == client.user:
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
                
                # Buscar si ya existe registro con este stripe_customer_id
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
    else:
        return

## ============ Asignación Automática (CORREGIDA - SIN PARPADEO) ============
@tasks.loop(minutes=10)
async def check_subscriptions():
    print("🔄 Checking subscriptions...")
    if not guild or not role:
        return
    
    try:
        # 1. Recuperamos TODAS las filas vinculadas
        response = supabase.table(TABLE_NAME).select("*").neq("discord_user_id", "None").execute()
        
        # Mapa para decidir el estado FINAL del usuario
        # Clave: Discord User ID -> Valor: True (tiene acceso) / False (no tiene)
        user_access_map = {}

        # 2. Procesamos filas y consultamos Stripe
        for user_data in response.data:
            discord_user_id = user_data.get("discord_user_id")
            customer_id = user_data.get("stripe_customer_id")
            db_status = user_data.get("subscription_status")

            if not discord_user_id or not customer_id:
                continue

            # Consultamos Stripe
            final_status = get_customer_aggregated_status(customer_id)
            
            # Fallback a DB si falla Stripe
            if final_status is None:
                final_status = db_status 

            # Actualizamos DB si hay discrepancia
            if final_status != db_status:
                supabase.table(TABLE_NAME).update({
                    "subscription_status": final_status, 
                    "updated_at": discord.utils.utcnow().isoformat()
                }).eq("stripe_customer_id", customer_id).execute()

            # LÓGICA DE PRIORIDAD: Si tiene AL MENOS UNA activa, gana el acceso.
            is_active_now = final_status in ACTIVE_STATUSES
            
            if discord_user_id not in user_access_map:
                # Primera vez que vemos al usuario en este ciclo
                user_access_map[discord_user_id] = is_active_now
            else:
                # Ya vimos al usuario (tiene otra fila en la DB)
                # Si la actual es True, sobrescribimos cualquier False anterior.
                if is_active_now:
                    user_access_map[discord_user_id] = True

        # 3. Aplicamos roles UNA VEZ por usuario (basado en el mapa final)
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
                
                await asyncio.sleep(0.1) # Evitar Rate Limits

            except Exception as e:
                print(f"Error gestionando rol para {discord_user_id}: {e}")

    except Exception as e:
        print(f"Error crítico en check_subscriptions: {e}")

## ====================
## INICIO UVICORN PARA FASTAPI
## ====================
if __name__ == "__main__":

    def start_fastapi():
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

    threading.Thread(target=start_fastapi).start()
    
    client.run(DISCORD_BOT_TOKEN)
