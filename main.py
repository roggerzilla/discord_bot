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
    Si no, devuelve el estado de la más reciente (ej. "canceled", "past_due").
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
        print(f"Warning: Event {event_type} received without recognizable customer ID. Ignoring.")
        return JSONResponse(status_code=200, content={"message": "Event ignored (No customer ID)."})

    print(f"Received Stripe event: {event_type} for customer {customer_id}")

    if event_type in ["customer.subscription.updated", "customer.subscription.created", "customer.subscription.deleted"]:
        
        final_status = get_customer_aggregated_status(customer_id)

        if final_status is None:
            raise HTTPException(status_code=500, detail="Error retrieving aggregated status from Stripe.")

        print(f"Webhook {event_type}. Customer {customer_id}. Aggregated status is: {final_status}")

        try:
            response = supabase.table(TABLE_NAME).select("*").eq("stripe_customer_id", customer_id).execute()
            
            if response.data:
                print(f"Updating existing subscription for customer {customer_id} to aggregated status {final_status}")
                supabase.table(TABLE_NAME).update({
                    "subscription_status": final_status, 
                    "updated_at": discord.utils.utcnow().isoformat()
                }).eq("stripe_customer_id", customer_id).execute()
            
            else:
                print(f"Creating new subscription record for customer {customer_id} with aggregated status {final_status}")
                supabase.table(TABLE_NAME).insert({
                    "stripe_customer_id": customer_id, 
                    "subscription_status": final_status, 
                    "updated_at": discord.utils.utcnow().isoformat()
                }).execute()
                
            return JSONResponse(status_code=200, content={"message": f"Aggregated subscription status updated for customer {customer_id}."})
        
        except Exception as e:
            print(f"🚨 Supabase Error during subscription webhook processing for {customer_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Supabase processing error: {e}")

    elif event_type == "checkout.session.completed":
        session = event["data"]["object"]
        customer_id = session.get("customer")
        subscription_id = session.get("subscription")

    return JSONResponse(status_code=200, content={"message": "Event ignored or handled by specific logic."})

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
        print(f"Guild: {guild.name}, Role: {role.name if role else 'Not found'}, Admin Log Channel: {admin_log_channel.name if admin_log_channel else 'Not found'}")
    else:
        print(f"❌ Guild with ID {DISCORD_GUILD_ID} not found. Please check DISCORD_GUILD_ID.")
    
    if not check_subscriptions.is_running():
        check_subscriptions.start()


## ============ Comando '!link' por DM (CORREGIDO PARA RE-VINCULACIÓN) ============
@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if isinstance(message.channel, discord.DMChannel):
        if message.content.lower().startswith("!link"):
            parts = message.content.split()
            if len(parts) != 2:
                await message.channel.send("❌ Use the command correctly:\n`!link youremail@example.com`")
                return

            user_email = parts[1].lower()

            if not re.match(r"[^@]+@[^@]+\.[^@]+", user_email):
                await message.channel.send("❌ The email format is invalid. Please make sure you type it correctly.")
                return

            try:
                # Buscar cliente en Stripe por email
                customers = stripe.Customer.list(email=user_email, limit=1)
                if not customers.data:
                    await message.channel.send("❌ No customer with that email was found in Stripe. Please verify it is the **same** email you used when paying.")
                    return

                customer_id = customers.data[0].id

                # Verificar estado de la suscripción en Stripe
                stripe_status = get_customer_aggregated_status(customer_id)

                if stripe_status not in ACTIVE_STATUSES:
                    await message.channel.send("⚠️ Your email was found in Stripe, but there is no active subscription. If you just paid, please wait a few minutes or contact support.")
                    print(f"User {message.author} tried to link with email {user_email} but no active subscription found for customer {customer_id}.")
                    return
                
                # 🔑 CORRECCIÓN PRINCIPAL: Verificar si YA existe un vínculo con OTRO usuario de Discord
                response = supabase.table(TABLE_NAME).select("*").eq("stripe_customer_id", customer_id).execute()

                update_data = {
                    "discord_user_id": str(message.author.id), 
                    "subscription_status": stripe_status,
                    "updated_at": discord.utils.utcnow().isoformat()
                }

                if response.data:
                    # Ya existe un registro para este customer_id
                    existing_discord_id = response.data[0].get("discord_user_id")
                    
                    # Si el discord_user_id existente es diferente al actual, alertamos
                    if existing_discord_id and existing_discord_id != str(message.author.id):
                        print(f"⚠️ Customer {customer_id} ya está vinculado a Discord ID {existing_discord_id}, pero {message.author.id} intenta vincularse.")
                        await message.channel.send(
                            "⚠️ This subscription is already linked to another Discord account. "
                            "If you need to transfer it, please contact support."
                        )
                        if admin_log_channel:
                            await admin_log_channel.send(
                                f"🟡 **Intento de re-vinculación:** {message.author.mention} (`{message.author.id}`) "
                                f"intentó vincular Stripe Customer `{customer_id}` que ya está vinculado a Discord ID `{existing_discord_id}`."
                            )
                        return
                    
                    # Si es el mismo usuario o no había discord_id, actualizamos
                    supabase.table(TABLE_NAME).update(update_data).eq("stripe_customer_id", customer_id).execute()
                    action_msg = "re-vinculado" if existing_discord_id else "vinculado"
                    print(f"✅ {action_msg.capitalize()} {message.author} ({message.author.id}) con Stripe Customer ID: {customer_id}")
                    
                else:
                    # No existe registro, creamos uno nuevo
                    insert_data = {"stripe_customer_id": customer_id}
                    insert_data.update(update_data)
                    supabase.table(TABLE_NAME).insert(insert_data).execute()
                    action_msg = "vinculado"
                    print(f"✅ Vinculado {message.author} ({message.author.id}) con Stripe Customer ID: {customer_id}")

                await message.channel.send("✅ Your Discord account has been successfully linked to your subscription. **Your premium access will be activated soon.**")
                
                if admin_log_channel:
                    await admin_log_channel.send(
                        f"🟢 **Vínculo exitoso:** {message.author.mention} (`{message.author.id}`) "
                        f"ha {'re-' if response.data and response.data[0].get('discord_user_id') else ''}vinculado su cuenta "
                        f"con Stripe Customer ID: `{customer_id}` (Email: `{user_email}`)."
                    )

            except Exception as e:
                print(f"Error vinculando para {message.author} ({user_email}): {e}")
                await message.channel.send("❌ An error occurred while trying to link your account. Please ensure your email is correct, and if the problem persists, contact support.")
                if admin_log_channel:
                    await admin_log_channel.send(f"🔴 **Error de vínculo:** {message.author.mention} (`{message.author.id}`) intentó vincular su cuenta con email `{user_email}` pero ocurrió un error: `{e}`")

    else:
        return

## ============ Asignación automática de roles ============
@tasks.loop(minutes=10)
async def check_subscriptions():
    print("🔄 Checking subscriptions...")
    if not guild or not role:
        print("🟡 Guild or Role not initialized. Skipping subscription check.")
        if admin_log_channel:
            try:
                await admin_log_channel.send("⚠️ **Advertencia:** El bot se está ejecutando pero `guild` o `role` no están inicializados.")
            except Exception as log_e:
                print(f"Error sending admin log (init check): {log_e}")
        return
    
    try:
        response = supabase.table(TABLE_NAME).select("discord_user_id, subscription_status, stripe_customer_id").neq("discord_user_id", None).execute()
        
        for user_data in response.data:
            discord_user_id = user_data.get("discord_user_id")
            db_status = user_data.get("subscription_status")
            customer_id = user_data.get("stripe_customer_id")

            if not discord_user_id or not customer_id:
                continue

            member = guild.get_member(int(discord_user_id))
            
            if not member:
                print(f"ℹ️ User {discord_user_id} not found in guild. Skipping.")
                continue

            # Siempre consultamos a Stripe cuál es el estado real agregado
            final_status = get_customer_aggregated_status(customer_id)
            
            if final_status is None:
                print(f"⚠️ Error checking Stripe for {customer_id}. Skipping user {discord_user_id}.")
                continue

            # Si el estado real de Stripe es diferente al que tenemos en la BDD, lo corregimos
            if final_status != db_status:
                print(f"🚨 Corrigiendo discrepancia para {customer_id}. DB: {db_status} -> Stripe: {final_status}")
                supabase.table(TABLE_NAME).update({
                    "subscription_status": final_status, 
                    "updated_at": discord.utils.utcnow().isoformat()
                }).eq("stripe_customer_id", customer_id).execute()

            # Lógica de Rol Final
            if final_status in ACTIVE_STATUSES:
                if role not in member.roles:
                    await member.add_roles(role, reason=f"Subscription active/trialing ({final_status})")
                    print(f"✅ Added role '{role.name}' to {member.display_name}")
                    if admin_log_channel:
                        try:
                            await admin_log_channel.send(f"🟢 **Rol asignado:** {member.mention} por suscripción activa (`{final_status}`).")
                        except Exception as log_e:
                            print(f"Error sending admin log (add role): {log_e}")
            
            else:
                if role in member.roles:
                    await member.remove_roles(role, reason=f"Subscription inactive or canceled (Status: {final_status})")
                    print(f"❌ Removed role '{role.name}' from {member.display_name} - Status: {final_status}")
                    if admin_log_channel:
                        try:
                            await admin_log_channel.send(f"🔴 **Rol removido:** {member.mention} por suscripción inactiva (`{final_status}`).")
                        except Exception as log_e:
                            print(f"Error sending admin log (remove role): {log_e}")
                            
            await asyncio.sleep(0.5) 
            
    except Exception as e:
        print(f"Error in check_subscriptions: {e}")
        if admin_log_channel:
            try:
                await admin_log_channel.send(f"🚨 **Error crítico en check_subscriptions:** `{e}`.")
            except Exception as log_e:
                print(f"Error sending critical admin log: {log_e}")

## ====================
## INICIO UVICORN PARA FASTAPI
## ====================
if __name__ == "__main__":

    def start_fastapi():
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

    threading.Thread(target=start_fastapi).start()
    
    client.run(DISCORD_BOT_TOKEN)
