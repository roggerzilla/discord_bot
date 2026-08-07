"""
discord_bot.py - Discord Bot: Manejo de suscripciones y roles.
"""
import asyncio
import discord
from discord.ext import tasks

from config import (
    DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, DEFAULT_ROLE_ID,
    ADMIN_LOG_CHANNEL_ID, MANAGED_ROLES, ACTIVE_STATUSES,
    SAFE_MODE_NO_BAN, TABLE_NAME, supabase
)
# ⚠️ DEPRECADO: stripe_helpers y stripe se eliminarán al completar la migración a Telegram Stars.
from services.stripe_helpers import get_customer_subscription_data, calculate_roles_to_assign
import stripe

# Nuevo sistema de cobro: Telegram Stars.
from config import STARS_TELEGRAM_BOT_USERNAME
from services.telegram_stars_helpers import (
    create_link_code,
    get_active_star_subs,
    get_all_linked_discord_ids,
    roles_for_tier,
    mark_expired_subs,
)

intents = discord.Intents.default()
intents.members = True
intents.messages = True
intents.message_content = True
# chunk_guilds_at_startup=False: con members intent, discord.py descarga TODA la lista
# de miembros antes de disparar on_ready. En servidores grandes eso tarda o se cuelga, y
# como el loop de roles arranca en on_ready, se quedaba sin arrancar nunca (los DMs
# seguían funcionando porque on_message no depende de on_ready, lo que lo hacía difícil
# de detectar). Sin chunking la caché de miembros queda vacía: por eso _get_member()
# cae a fetch_member() cuando get_member() no encuentra a alguien.
discord_client = discord.Client(intents=intents, chunk_guilds_at_startup=False)

guild = None
admin_log_channel = None


async def _get_member(g, user_id: int):
    """Busca un miembro en la caché y, si no está, lo pide a la API.
    Devuelve None si ya no pertenece al servidor."""
    member = g.get_member(user_id)
    if member is not None:
        return member
    try:
        return await g.fetch_member(user_id)
    except discord.NotFound:
        return None
    except discord.HTTPException as e:
        print(f"⚠️ Error buscando al miembro {user_id}: {e}")
        return None


def _resolve_guild():
    """Resuelve el guild y el canal de logs. Se reintenta desde el loop para no
    depender de que on_ready haya corrido con la caché ya poblada."""
    global guild, admin_log_channel
    if guild is None:
        guild = discord_client.get_guild(DISCORD_GUILD_ID)
        if guild is None:
            print(f"⚠️ No encuentro el guild {DISCORD_GUILD_ID}. ¿Es correcto DISCORD_GUILD_ID?")
            return None
        print(f"✅ Guild encontrado: {guild.name}")
    if admin_log_channel is None and ADMIN_LOG_CHANNEL_ID:
        admin_log_channel = discord_client.get_channel(ADMIN_LOG_CHANNEL_ID)
    return guild


@discord_client.event
async def on_ready():
    print(f"✅ Discord Ready. SafeMode: {SAFE_MODE_NO_BAN}")
    _resolve_guild()
    if not check_subscriptions.is_running():
        check_subscriptions.start()
        print("🔁 Loop de suscripciones iniciado")


@discord_client.event
async def on_message(message):
    global guild, admin_log_channel
    if message.author.bot:
        return
    raw_content = message.content.strip()

    # === Telegram Stars: generar deep link de vinculación ===
    if isinstance(message.channel, discord.DMChannel) and raw_content.lower().startswith("!telegram"):
        if not STARS_TELEGRAM_BOT_USERNAME:
            await message.channel.send("⚠️ The Telegram Stars bot isn't configured yet.")
            return
        try:
            code = await asyncio.to_thread(create_link_code, str(message.author.id))
            deep_link = f"https://t.me/{STARS_TELEGRAM_BOT_USERNAME}?start={code}"
            await message.channel.send(
                "⭐ **Telegram Stars subscription**\n\n"
                f"Open this link in Telegram to connect your account:\n{deep_link}\n\n"
                "Already paid? This is the step that gets you your roles — "
                "they'll be assigned within a few minutes.\n\n"
                "_This link expires in 15 minutes. Just send `!telegram` again if it does._"
            )
        except Exception as e:
            print(f"Telegram link Err: {e}")
            await message.channel.send("❌ Something went wrong generating your link. Please try again.")
        return

    # ⚠️ DEPRECADO: vinculación con Stripe. Se eliminará al completar la migración a Telegram Stars.
    if isinstance(message.channel, discord.DMChannel) and raw_content.lower().startswith("!link"):
        try:
            email = raw_content[5:].strip() if not raw_content.lower().startswith("!link ") else raw_content[6:].strip()
            if not email or "@" not in email:
                await message.channel.send("❌ Usa: `!link email@ejemplo.com`")
                return

            query = f"email:'{email}'"
            custs = await asyncio.to_thread(stripe.Customer.search, query=query, limit=1)
            if not custs.data:
                query_lower = f"email:'{email.lower()}'"
                custs = await asyncio.to_thread(stripe.Customer.search, query=query_lower, limit=1)
            if not custs.data:
                await message.channel.send(f"❌ No encontré al cliente `{email}` en Stripe.")
                return

            c_id = custs.data[0].id
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
                supabase.table(TABLE_NAME).update({
                    "discord_user_id": str(message.author.id),
                    "subscription_status": status,
                    "updated_at": now
                }).eq("stripe_customer_id", c_id).execute()
            else:
                supabase.table(TABLE_NAME).insert({
                    "stripe_customer_id": c_id,
                    "discord_user_id": str(message.author.id),
                    "subscription_status": status,
                    "updated_at": now
                }).execute()

            roles = calculate_roles_to_assign(prod)
            if guild:
                mem = guild.get_member(message.author.id)
                if mem:
                    for rid in roles:
                        r = guild.get_role(rid)
                        if r:
                            await mem.add_roles(r)

            await message.channel.send("✅ Linked successfully!")
            if admin_log_channel:
                await admin_log_channel.send(f"🟢 Link: {message.author.mention} ({email})")
        except Exception as e:
            print(f"Link Err: {e}")
            await message.channel.send("❌ Error.")


@tasks.loop(minutes=10)
async def check_subscriptions():
    print("🔄 Checking subscriptions...")
    if _resolve_guild() is None:
        return
    try:
        # --- Fuente 1: Stripe (⚠️ DEPRECADO, se eliminará) ---
        response = supabase.table(TABLE_NAME).select("*").neq("discord_user_id", "None").execute()
        # discord_user_id -> roles que Stripe le da (set)
        stripe_roles_map = {}
        for row in response.data:
            c_id = row.get("stripe_customer_id")
            d_id = row.get("discord_user_id")
            current_db_status = row.get("subscription_status")
            real_status, prod_obj = await get_customer_subscription_data(c_id)
            if real_status is None:
                continue
            if real_status != current_db_status:
                supabase.table(TABLE_NAME).update({
                    "subscription_status": real_status,
                    "updated_at": discord.utils.utcnow().isoformat()
                }).eq("stripe_customer_id", c_id).execute()
            if real_status in ACTIVE_STATUSES:
                stripe_roles_map.setdefault(d_id, set()).update(calculate_roles_to_assign(prod_obj))
            await asyncio.sleep(0.5)

        # --- Fuente 2: Telegram Stars (nuevo sistema) ---
        await asyncio.to_thread(mark_expired_subs)
        star_active = await asyncio.to_thread(get_active_star_subs)
        star_all_ids = await asyncio.to_thread(get_all_linked_discord_ids)
        # discord_user_id -> roles que las Stars le dan (set)
        stars_roles_map = {}
        for sub in star_active:
            d_id = str(sub.get("discord_user_id"))
            stars_roles_map.setdefault(d_id, set()).update(roles_for_tier(sub.get("tier")))
        print(f"⭐ Suscripciones Stars vigentes y vinculadas: {len(stars_roles_map)}")

        # --- Unificar: todo Discord ID visto en cualquiera de las dos fuentes ---
        all_ids = set(stripe_roles_map.keys()) | set(stars_roles_map.keys()) | star_all_ids
        all_ids |= {row.get("discord_user_id") for row in response.data if row.get("discord_user_id")}

        for d_id in all_ids:
            if not d_id:
                continue
            try:
                member = await _get_member(guild, int(d_id))
            except (TypeError, ValueError):
                continue
            if not member:
                # Pagó y vinculó, pero no está en el servidor: sin esto el caso era
                # indistinguible de "todo bien" en los logs.
                if d_id in stars_roles_map:
                    print(f"⚠️ {d_id} tiene suscripción activa pero no está en el servidor")
                continue

            # Roles a los que el usuario TIENE derecho (unión de ambas fuentes).
            entitled = set(stripe_roles_map.get(d_id, set())) | set(stars_roles_map.get(d_id, set()))

            # Otorgar los que le falten.
            for rid in entitled:
                r = guild.get_role(rid)
                if r is None:
                    # Un role ID que no existe en el servidor fallaba en silencio.
                    print(f"⚠️ El rol {rid} no existe en el servidor. Revisa config.py")
                    continue
                if r not in member.roles:
                    try:
                        await member.add_roles(r, reason="Suscripción activa")
                        print(f"➕ Rol {r.name} a {member.display_name}")
                    except discord.Forbidden:
                        print(f"⛔ Sin permisos para dar '{r.name}'. "
                              "El rol del bot debe estar POR ENCIMA en la lista de roles.")
                    except discord.HTTPException as e:
                        print(f"⚠️ Error dando el rol '{r.name}' a {member.display_name}: {e}")

            # Quitar roles gestionados a los que YA NO tiene derecho (baja o downgrade).
            if not SAFE_MODE_NO_BAN:
                roles_removed = []
                for rid in MANAGED_ROLES:
                    if rid in entitled:
                        continue
                    r = guild.get_role(rid)
                    if r and r in member.roles:
                        await member.remove_roles(r, reason="Baja / sin suscripción")
                        roles_removed.append(r.name)
                if roles_removed and admin_log_channel and not entitled:
                    await admin_log_channel.send(f"🔴 **Baja:** {member.mention} perdió roles.")
            await asyncio.sleep(0.1)
    except Exception as e:
        print(f"Error Loop: {e}")
