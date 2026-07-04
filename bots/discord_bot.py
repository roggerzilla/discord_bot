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
from services.stripe_helpers import get_customer_subscription_data, calculate_roles_to_assign
import stripe

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
    if guild:
        admin_log_channel = discord_client.get_channel(ADMIN_LOG_CHANNEL_ID)
    if not check_subscriptions.is_running():
        check_subscriptions.start()


@discord_client.event
async def on_message(message):
    global guild, admin_log_channel
    if message.author.bot:
        return
    raw_content = message.content.strip()

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
    if not guild:
        return
    try:
        response = supabase.table(TABLE_NAME).select("*").neq("discord_user_id", "None").execute()
        user_active_map = {}
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
            if d_id not in user_active_map:
                user_active_map[d_id] = False
            if real_status in ACTIVE_STATUSES:
                user_active_map[d_id] = True
            await asyncio.sleep(0.5)

        processed_users = set()
        for row in response.data:
            d_id = row.get("discord_user_id")
            if d_id in processed_users:
                continue
            processed_users.add(d_id)
            member = guild.get_member(int(d_id))
            if not member:
                continue
            is_user_safe = user_active_map.get(d_id, False)
            if is_user_safe:
                active_row = next((r for r in response.data if r["discord_user_id"] == d_id and r["subscription_status"] in ACTIVE_STATUSES), None)
                if active_row:
                    _, prod_obj = await get_customer_subscription_data(active_row["stripe_customer_id"])
                    roles_to_add = calculate_roles_to_assign(prod_obj)
                    for rid in roles_to_add:
                        r = guild.get_role(rid)
                        if r and r not in member.roles:
                            await member.add_roles(r, reason="Sub Activa")
                            print(f"➕ Rol {r.name} a {member.display_name}")
            else:
                if not SAFE_MODE_NO_BAN:
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
