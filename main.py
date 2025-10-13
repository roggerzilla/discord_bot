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
import asyncio # 🚨 Importación necesaria para el manejo de rate limit

load_dotenv()

## ===============================
## BOT DE DISCORD + WEBHOOK FASTAPI
## CONTROL DE ROLES PREMIUM POR STRIPE
## CON COMANDO '!vincular' POR DM
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

## ====================
## SUPABASE
## ====================
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
TABLE_NAME = "subscriptions_discord"

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

    # Determinar customer_id
    if not customer_id and "customer" in data_object:
        customer_id = data_object["customer"]
    elif not customer_id and event_type.startswith("customer."):
        customer_id = data_object.get("id")

    if not customer_id:
        print(f"Warning: Event {event_type} received without recognizable customer ID. Ignoring.")
        return JSONResponse(status_code=200, content={"message": "Event ignored (No customer ID)."})

    print(f"Received Stripe event: {event_type} for customer {customer_id}")

    # Manejo de eventos de suscripción (updated, created, deleted)
    if event_type in ["customer.subscription.updated", "customer.subscription.created", "customer.subscription.deleted"]:
        status = data_object.get("status", "canceled") 
        
        try:
            # Buscar el registro existente por stripe_customer_id
            response = supabase.table(TABLE_NAME).select("*").eq("stripe_customer_id", customer_id).execute()
            
            if response.data:
                # Actualizar registro existente
                print(f"Updating existing subscription for customer {customer_id} to status {status}")
                supabase.table(TABLE_NAME).update({"subscription_status": status, "updated_at": discord.utils.utcnow().isoformat()}).eq("stripe_customer_id", customer_id).execute()
                
            else:
                # Crear nuevo registro si no existe (puede ser sin discord_user_id)
                print(f"Creating new subscription record for customer {customer_id} with status {status}")
                supabase.table(TABLE_NAME).insert({"stripe_customer_id": customer_id, "subscription_status": status, "updated_at": discord.utils.utcnow().isoformat()}).execute()
                
            return JSONResponse(status_code=200, content={"message": f"Subscription status updated for customer {customer_id}."})
        
        except Exception as e:
            print(f"🚨 Supabase Error during subscription webhook processing for {customer_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Supabase processing error: {e}")

    # Manejo de checkout.session.completed (para vincular nuevos clientes rápidamente)
    elif event_type == "checkout.session.completed":
        session = event["data"]["object"]
        customer_id = session.get("customer")
        subscription_id = session.get("subscription")

        # ... (La lógica de checkout session se mantiene igual) ...
        # (Se omite por brevedad, pero se debe mantener la lógica original aquí)
        # ...

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
        # Usar fetch_role si get_role retorna None y el rol es crítico.
        role = guild.get_role(DISCORD_ROLE_ID)
        admin_log_channel = client.get_channel(ADMIN_LOG_CHANNEL_ID)
        print(f"Guild: {guild.name}, Role: {role.name if role else 'Not found'}, Admin Log Channel: {admin_log_channel.name if admin_log_channel else 'Not found'}")
    else:
        print(f"❌ Guild with ID {DISCORD_GUILD_ID} not found. Please check DISCORD_GUILD_ID.")
    
    if not check_subscriptions.is_running():
        check_subscriptions.start()


## ============ Comando '!vincular' por DM ============
@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if isinstance(message.channel, discord.DMChannel):
        # Usamos lower() y startswith("!link") para manejar !Link o !linkear (si se cambia)
        if message.content.lower().startswith("!link"):
            # ... (La lógica del comando se mantiene igual) ...
            parts = message.content.split()
            if len(parts) != 2:
                await message.channel.send("❌ Use the command correctly:\n`!link youremail@example.com`")
                return

            user_email = parts[1].lower()

            if not re.match(r"[^@]+@[^@]+\.[^@]+", user_email):
                await message.channel.send("❌ The email format is invalid. Please make sure you type it correctly.")
                return

            # Verificar si el usuario de Discord ya está vinculado
            try:
                response = supabase.table(TABLE_NAME).select("discord_user_id").eq("discord_user_id", str(message.author.id)).single().execute()
                if response.data:
                    await message.channel.send("ℹ️ It seems your Discord account is already linked to a subscription. If you believe this is an error, please contact support.")
                    return
            except Exception:
                pass 

            try:
                # Buscar cliente en Stripe por email
                customers = stripe.Customer.list(email=user_email, limit=1)
                if not customers.data:
                    await message.channel.send("❌ No customer with that email was found in Stripe. Please verify it is the **same** email you used when paying.")
                    return

                customer_id = customers.data[0].id

                # Buscar la suscripción activa del cliente en Stripe
                subscriptions = stripe.Subscription.list(customer=customer_id, status='active', limit=1)
                if not subscriptions.data:
                    await message.channel.send("⚠️ Your email was found in Stripe, but there is no active subscription. If you just paid, please wait a few minutes or contact support.")
                    print(f"User {message.author} tried to link with email {user_email} but no active subscription found for customer {customer_id}.")
                    return
                
                # Obtener estado de Stripe
                stripe_status = subscriptions.data[0].status

                # Verificar y actualizar/crear registro en Supabase con ambos IDs
                response = supabase.table(TABLE_NAME).select("*").eq("stripe_customer_id", customer_id).execute()

                update_data = {
                    "discord_user_id": str(message.author.id), 
                    "subscription_status": stripe_status,
                    "updated_at": discord.utils.utcnow().isoformat()
                }

                if response.data:
                    supabase.table(TABLE_NAME).update(update_data).eq("stripe_customer_id", customer_id).execute()
                else:
                    insert_data = {"stripe_customer_id": customer_id}
                    insert_data.update(update_data)
                    supabase.table(TABLE_NAME).insert(insert_data).execute()

                await message.channel.send("✅ Your Discord account has been successfully linked to your subscription. **Your premium access will be activated soon.**")

                print(f"✅ Vinculado {message.author} ({message.author.id}) con Stripe Customer ID: {customer_id}")
                if admin_log_channel:
                    await admin_log_channel.send(f"🟢 **Nuevo vínculo:** {message.author.mention} (`{message.author.id}`) ha vinculado su cuenta con Stripe Customer ID: `{customer_id}` (Email: `{user_email}`).")

            except Exception as e:
                print(f"Error vinculando para {message.author} ({user_email}): {e}")
                # Si hay rate limit activo, este mensaje puede fallar, pero se mantiene la lógica de informar al usuario.
                await message.channel.send("❌ An error occurred while trying to link your account. Please ensure your email is correct, and if the problem persists, contact support.")
                if admin_log_channel:
                    await admin_log_channel.send(f"🔴 **Error de vínculo:** {message.author.mention} (`{message.author.id}`) intentó vincular su cuenta con email `{user_email}` pero ocurrió un error: `{e}`")

    else:
        return

## ============ Asignación automática de roles (CORRECCIÓN CLAVE) ============
@tasks.loop(minutes=10)
async def check_subscriptions():
    print("🔄 Checking subscriptions...")
    if not guild or not role:
        print("🟡 Guild or Role not initialized. Skipping subscription check.")
        if admin_log_channel:
            # Envío de mensaje de advertencia aquí, con el riesgo de rate limit si la IP está bloqueada.
            await admin_log_channel.send("⚠️ **Advertencia:** El bot se está ejecutando pero `guild` o `role` no están inicializados.")
        return
    
    ACTIVE_STATUSES = ["active", "trialing"]

    try:
        # Consulta la base de datos
        response = supabase.table(TABLE_NAME).select("discord_user_id, subscription_status, stripe_customer_id").neq("discord_user_id", None).execute()
        
        for user_data in response.data:
            discord_user_id = user_data.get("discord_user_id")
            db_status = user_data.get("subscription_status")
            customer_id = user_data.get("stripe_customer_id")

            if not discord_user_id or not customer_id:
                continue

            # Usar fetch_member() si get_member() (caché) no es suficiente, aunque get_member es más rápido.
            member = guild.get_member(int(discord_user_id))
            
            if not member:
                print(f"ℹ️ User {discord_user_id} not found in guild. Skipping.")
                continue

            final_status = db_status

            # DOBLE VERIFICACIÓN (Llamadas a Stripe)
            if db_status == "active":
                try:
                    subscriptions = stripe.Subscription.list(customer=customer_id, status='all', limit=1)
                    if subscriptions.data:
                        stripe_status = subscriptions.data[0].status
                        
                        if stripe_status not in ACTIVE_STATUSES:
                            final_status = stripe_status
                            supabase.table(TABLE_NAME).update({
                                "subscription_status": final_status, 
                                "updated_at": discord.utils.utcnow().isoformat()
                            }).eq("discord_user_id", discord_user_id).execute()
                            print(f"🚨 Discrepancia corregida. DB: {db_status} -> Stripe: {final_status}")

                except Exception as e:
                    print(f"Error checking Stripe API for customer {customer_id}: {e}")

            # ---------------- Lógica de Rol Final ----------------
            if final_status in ACTIVE_STATUSES:
                if role not in member.roles:
                    await member.add_roles(role, reason=f"Subscription active/trialing ({final_status})")
                    print(f"✅ Added role '{role.name}' to {member.display_name}")
                    if admin_log_channel:
                        # Añadir manejo de errores para el envío de logs
                        try:
                            await admin_log_channel.send(f"🟢 **Rol asignado:** {member.mention} por suscripción activa (`{final_status}`).")
                        except Exception as log_e:
                            print(f"Error sending admin log (add role): {log_e}")
            
            else: # El estado final NO es activo
                if role in member.roles:
                    await member.remove_roles(role, reason=f"Subscription inactive or canceled (Status: {final_status})")
                    print(f"❌ Removed role '{role.name}' from {member.display_name} - Status: {final_status}")
                    if admin_log_channel:
                        try:
                            await admin_log_channel.send(f"🔴 **Rol removido:** {member.mention} por suscripción inactiva (`{final_status}`).")
                        except Exception as log_e:
                            print(f"Error sending admin log (remove role): {log_e}")
                        
            # 🔑 CORRECCIÓN CLAVE: Pausa obligatoria entre llamadas a la API de Discord
            # Recomendación: 0.5 a 1.0 segundos por usuario en el bucle.
            await asyncio.sleep(0.5) 
            
    except Exception as e:
        print(f"Error in check_subscriptions: {e}")
        if admin_log_channel:
            # Añadir manejo de errores para el envío de logs
            try:
                await admin_log_channel.send(f"🚨 **Error crítico en check_subscriptions:** `{e}`.")
            except Exception as log_e:
                print(f"Error sending critical admin log: {log_e}")

## ====================
## INICIO UVICORN PARA FASTAPI
## ====================
if __name__ == "__main__":
    import threading

    def start_fastapi():
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

    # Start FastAPI in a separate thread
    threading.Thread(target=start_fastapi).start()
    
    # Run the Discord bot
    client.run(DISCORD_BOT_TOKEN)
