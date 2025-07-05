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
import re # Importar para validación de email
from dotenv import load_dotenv
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
ADMIN_LOG_CHANNEL_ID = int(os.environ.get("ADMIN_LOG_CHANNEL_ID")) # Nuevo: Para notificaciones de admin

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
    except ValueError as e:
        # Invalid payload
        print(f"Webhook Error: Invalid payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        print(f"Webhook Error: Invalid signature: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    event_type = event["type"]
    data_object = event["data"]["object"]
    customer_id = data_object.get("customer")
    subscription_id = data_object.get("id") # Para eventos de suscripción

    # Si es un customer.created o customer.updated, el customer_id puede venir en data_object.id
    if not customer_id and "customer" in data_object:
        customer_id = data_object["customer"]
    elif not customer_id and event_type.startswith("customer."):
        customer_id = data_object.get("id")


    print(f"Received Stripe event: {event_type} for customer {customer_id}")

    # Manejo de eventos de suscripción
    if event_type in ["customer.subscription.updated", "customer.subscription.created", "customer.subscription.deleted"]:
        status = data_object.get("status", "canceled") # 'canceled' por defecto si es deleted
        
        # Buscar el registro existente por stripe_customer_id
        response = supabase.table(TABLE_NAME).select("*").eq("stripe_customer_id", customer_id).execute()
        
        if response.data:
            # Actualizar registro existente
            print(f"Updating existing subscription for customer {customer_id} to status {status}")
            supabase.table(TABLE_NAME).update({"subscription_status": status}).eq("stripe_customer_id", customer_id).execute()
            return JSONResponse(status_code=200, content={"message": f"Subscription status updated for customer {customer_id}."})
        else:
            # Crear nuevo registro si no existe (útil para nuevos pagos o si se eliminó previamente)
            print(f"Creating new subscription record for customer {customer_id} with status {status}")
            supabase.table(TABLE_NAME).insert({"stripe_customer_id": customer_id, "subscription_status": status}).execute()
            return JSONResponse(status_code=200, content={"message": f"New subscription record created for customer {customer_id}."})
            
    # Manejo de otros eventos relevantes (ej. checkout.session.completed si se usa Payment Links)
    elif event_type == "checkout.session.completed":
        # Este evento ocurre cuando una sesión de checkout es completada (un pago exitoso)
        # Es útil si creas suscripciones a través de Stripe Payment Links o Checkout Sessions
        session = event["data"]["object"]
        customer_id = session.get("customer")
        subscription_id = session.get("subscription")

        if customer_id and subscription_id:
            # Recuperar el estado de la suscripción directamente de Stripe para asegurar
            try:
                subscription = stripe.Subscription.retrieve(subscription_id)
                status = subscription.status

                response = supabase.table(TABLE_NAME).select("*").eq("stripe_customer_id", customer_id).execute()
                if response.data:
                    print(f"Updating existing subscription from checkout for customer {customer_id} to status {status}")
                    supabase.table(TABLE_NAME).update({"subscription_status": status}).eq("stripe_customer_id", customer_id).execute()
                else:
                    print(f"Creating new subscription from checkout for customer {customer_id} with status {status}")
                    supabase.table(TABLE_NAME).insert({"stripe_customer_id": customer_id, "subscription_status": status}).execute()
                return JSONResponse(status_code=200, content={"message": f"Subscription record updated/created from checkout for customer {customer_id}."})
            except Exception as e:
                print(f"Error retrieving subscription {subscription_id} after checkout: {e}")
                return JSONResponse(status_code=500, content={"message": "Error processing checkout session."})
        else:
            print(f"Checkout session completed event received without customer_id or subscription_id: {session.id}")


    return JSONResponse(status_code=200, content={"message": "Event ignored or handled by specific logic."})

## ====================
## DISCORD BOT
## ====================
intents = discord.Intents.default()
intents.members = True
intents.messages = True
intents.message_content = True # Asegúrate de tener esto para leer el contenido de los mensajes
client = discord.Client(intents=intents)

guild = None
role = None
admin_log_channel = None # Canal para logs de admin

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


## ============ Comando '!vincular' por DM ============
@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if isinstance(message.channel, discord.DMChannel):
        if message.content.lower().startswith("!vincular"): # Usar .lower() para ser más flexible
            parts = message.content.split()
            if len(parts) != 2:
                await message.channel.send("❌ Usa el comando correctamente:\n`!vincular tuemail@correo.com`")
                return

            user_email = parts[1].lower() # Convertir a minúsculas para consistencia

            # Validar formato del email
            if not re.match(r"[^@]+@[^@]+\.[^@]+", user_email):
                await message.channel.send("❌ El formato del correo electrónico no es válido. Por favor, asegúrate de escribirlo correctamente.")
                return
            
            # Verificar si el usuario ya está vinculado
            existing_link = supabase.table(TABLE_NAME).select("discord_user_id").eq("discord_user_id", str(message.author.id)).single().execute()
            if existing_link.data and existing_link.data["discord_user_id"] == str(message.author.id):
                await message.channel.send("ℹ️ Parece que tu cuenta de Discord ya está vinculada con una suscripción. Si crees que hay un error, contacta al soporte.")
                return


            try:
                # Buscar cliente en Stripe por email
                customers = stripe.Customer.list(email=user_email, limit=1)
                if not customers.data:
                    await message.channel.send("❌ No se encontró un cliente con ese correo en Stripe. Verifica que sea **el mismo** que usaste al pagar.")
                    return

                customer_id = customers.data[0].id
                
                # Buscar la suscripción activa del cliente en Stripe
                subscriptions = stripe.Subscription.list(customer=customer_id, status='active', limit=1)
                if not subscriptions.data:
                    # El cliente existe, pero no tiene suscripción activa
                    await message.channel.send("⚠️ Se encontró tu correo en Stripe, pero no tienes una suscripción activa. Si acabas de pagar, espera unos minutos o contacta al soporte.")
                    print(f"User {message.author} tried to link with email {user_email} but no active subscription found for customer {customer_id}.")
                    return

                # Actualizar o insertar en Supabase
                # Verificar si ya existe un registro con ese stripe_customer_id
                response = supabase.table(TABLE_NAME).select("*").eq("stripe_customer_id", customer_id).execute()
                if response.data:
                    # Si ya existe, actualiza el discord_user_id
                    supabase.table(TABLE_NAME).update(
                        {"discord_user_id": str(message.author.id), "subscription_status": subscriptions.data[0].status} # Actualiza también el status por si acaso
                    ).eq("stripe_customer_id", customer_id).execute()
                else:
                    # Si no existe, crea un nuevo registro
                    supabase.table(TABLE_NAME).insert(
                        {"stripe_customer_id": customer_id, "discord_user_id": str(message.author.id), "subscription_status": subscriptions.data[0].status}
                    ).execute()


                await message.channel.send("✅ Tu cuenta de Discord ha sido vinculada correctamente con tu suscripción. **Tu acceso premium se activará pronto.**")
                print(f"✅ Vinculado {message.author} ({message.author.id}) con Stripe Customer ID: {customer_id}")
                if admin_log_channel:
                    await admin_log_channel.send(f"🟢 **Nuevo vínculo:** {message.author.mention} (`{message.author.id}`) ha vinculado su cuenta con Stripe Customer ID: `{customer_id}` (Email: `{user_email}`).")


            except Exception as e:
                print(f"Error vinculando para {message.author} ({user_email}): {e}")
                await message.channel.send("❌ Ocurrió un error al intentar vincular tu cuenta. Por favor, asegúrate de que el correo es el correcto y, si persiste el problema, contacta al soporte.")
                if admin_log_channel:
                    await admin_log_channel.send(f"🔴 **Error de vínculo:** {message.author.mention} (`{message.author.id}`) intentó vincular su cuenta con email `{user_email}` pero ocurrió un error: `{e}`")

    else:
        # Ignorar mensajes en canales públicos (o añadir lógica de comandos si es necesario)
        return

## ============ Asignación automática de roles ============
@tasks.loop(minutes=10) # Puedes ajustar este tiempo si lo consideras necesario
async def check_subscriptions():
    print("🔄 Checking subscriptions...")
    if not guild or not role:
        print("🟡 Guild or Role not initialized. Skipping subscription check.")
        if admin_log_channel:
            await admin_log_channel.send("⚠️ **Advertencia:** El bot se está ejecutando pero `guild` o `role` no están inicializados. Las revisiones de suscripciones no se están realizando.")
        return

    try:
        # Solo trae los que tienen un discord_user_id asignado para procesar roles
        response = supabase.table(TABLE_NAME).select("discord_user_id, subscription_status").neq("discord_user_id", None).execute()

        
        for user_data in response.data:
            discord_user_id = user_data.get("discord_user_id")
            status = user_data.get("subscription_status")

            if not discord_user_id: # Doble verificación, aunque la consulta ya lo filtra
                continue

            member = guild.get_member(int(discord_user_id))
            
            if not member:
                # Si el miembro no está en el servidor, registra y opcionalmente limpia
                print(f"ℹ️ User with Discord ID {discord_user_id} not found in guild. Skipping role check. Consider cleaning DB.")
                # Opcional: limpiar la base de datos si el usuario no está en el servidor (CUIDADO con esto)
                # response_delete = supabase.table(TABLE_NAME).update({"discord_user_id": None}).eq("discord_user_id", discord_user_id).execute()
                # print(f"Cleaned discord_user_id for {discord_user_id} as member not found.")
                continue

            if status == "active":
                if role not in member.roles:
                    await member.add_roles(role, reason="Subscription active in Stripe/Supabase")
                    print(f"✅ Added role '{role.name}' to {member.display_name} ({member.id})")
                    if admin_log_channel:
                        await admin_log_channel.send(f"🟢 **Rol asignado:** Se ha asignado el rol `{role.name}` a {member.mention} (`{member.id}`) por suscripción activa.")
                # else:
                #     print(f"User {member.display_name} already has the role. No action needed.")
            else: # Status is not active (e.g., canceled, past_due, unpaid, trialing, incomplete)
                if role in member.roles:
                    await member.remove_roles(role, reason="Subscription inactive or canceled in Stripe/Supabase")
                    print(f"❌ Removed role '{role.name}' from {member.display_name} ({member.id}) - Status: {status}")
                    if admin_log_channel:
                        await admin_log_channel.send(f"🔴 **Rol removido:** Se ha removido el rol `{role.name}` a {member.mention} (`{member.id}`) por suscripción inactiva (`{status}`).")
                # else:
                #     print(f"User {member.display_name} does not have the role. No action needed.")

    except Exception as e:
        print(f"Error in check_subscriptions: {e}")
        if admin_log_channel:
            await admin_log_channel.send(f"🚨 **Error crítico en check_subscriptions:** `{e}`. Por favor, revisa los logs del bot.")

## ====================
## INICIO UVICORN PARA FASTAPI
## ====================
if __name__ == "__main__":
    import threading

    def start_fastapi():
        # Uvicorn bound to 0.0.0.0 means it listens on all available network interfaces.
        # This is necessary for webhooks to reach it from external services like Stripe.
        # The port is taken from environment variable 'PORT' or defaults to 8000.
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

    # Start FastAPI in a separate thread
    threading.Thread(target=start_fastapi).start()
    
    # Run the Discord bot
    client.run(DISCORD_BOT_TOKEN)