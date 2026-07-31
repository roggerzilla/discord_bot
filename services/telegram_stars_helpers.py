"""
telegram_stars_helpers.py - Funciones auxiliares para el cobro con Telegram Stars.

Sistema NUEVO que reemplazará a Stripe: un bot de Telegram independiente cobra
suscripciones recurrentes en Stars (XTR, periodo fijo de 30 días) y, según el estado
del pago, se otorgan o quitan ROLES de Discord.

Requiere vincular la cuenta de Telegram del pagador con su cuenta de Discord
(tabla `telegram_link_codes`) y persiste las suscripciones en `telegram_star_subs`.
"""
import secrets
from datetime import datetime, timezone, timedelta

from telebot.types import LabeledPrice

from config import (
    supabase,
    STAR_TIER_MAPPING,
    STAR_SUBSCRIPTION_PERIOD,
    TELEGRAM_SUBS_TABLE,
    TELEGRAM_LINK_CODES_TABLE,
    DEFAULT_ROLE_ID,
    TIER_3_ROLE_ID,
)

# Los códigos de vinculación expiran a los 15 minutos.
LINK_CODE_TTL_MINUTES = 15


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ===============================
# VINCULACIÓN Telegram <-> Discord
# ===============================
def create_link_code(discord_user_id: str) -> str:
    """Genera un código de un solo uso que vincula a un usuario de Discord.
    Se guarda en Supabase y luego se resuelve cuando el usuario abre el bot de Telegram."""
    code = secrets.token_urlsafe(8)
    try:
        supabase.table(TELEGRAM_LINK_CODES_TABLE).upsert({
            "code": code,
            "discord_user_id": str(discord_user_id),
            "created_at": _now_iso(),
        }).execute()
    except Exception as e:
        print(f"⚠️ Error creando link code: {e}")
    return code


def resolve_link_code(code: str):
    """Devuelve el discord_user_id asociado a un código válido (no expirado) o None."""
    if not code:
        return None
    try:
        res = supabase.table(TELEGRAM_LINK_CODES_TABLE).select("*").eq("code", code).limit(1).execute()
        if not res.data:
            return None
        row = res.data[0]
        created = row.get("created_at")
        if created:
            try:
                created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                if datetime.now(timezone.utc) - created_dt > timedelta(minutes=LINK_CODE_TTL_MINUTES):
                    # Expirado: limpiar y rechazar.
                    supabase.table(TELEGRAM_LINK_CODES_TABLE).delete().eq("code", code).execute()
                    return None
            except Exception:
                pass
        return row.get("discord_user_id")
    except Exception as e:
        print(f"⚠️ Error resolviendo link code: {e}")
        return None


def consume_link_code(code: str) -> None:
    """Elimina el código una vez usado."""
    try:
        supabase.table(TELEGRAM_LINK_CODES_TABLE).delete().eq("code", code).execute()
    except Exception as e:
        print(f"⚠️ Error consumiendo link code: {e}")


def attach_discord_to_subscription(telegram_user_id: int, discord_user_id) -> bool:
    """Escribe el discord_user_id sobre una suscripción YA PAGADA.

    El flujo es pago-primero: se cobra sin conocer la cuenta de Discord y la fila queda
    con discord_user_id en null (invisible para get_active_star_subs, o sea sin roles).
    Cuando el usuario canjea su código, esto completa la fila y el loop de Discord le
    entrega los roles en la siguiente pasada.
    Devuelve True si existía una suscripción que completar."""
    if not get_subscription(telegram_user_id):
        return False
    try:
        supabase.table(TELEGRAM_SUBS_TABLE).update({
            "discord_user_id": str(discord_user_id),
            "updated_at": _now_iso(),
        }).eq("telegram_user_id", int(telegram_user_id)).execute()
        print(f"🔗 Suscripción vinculada: tg={telegram_user_id} → discord={discord_user_id}")
        return True
    except Exception as e:
        print(f"⚠️ Error vinculando suscripción a Discord: {e}")
        return False


# ===============================
# TIERS / ROLES
# ===============================
def roles_for_tier(tier: str):
    """Mapea un tier de Stars a los role IDs de Discord que debe otorgar.
    Análogo a stripe_helpers.calculate_roles_to_assign: el tier 3 incluye el rol por defecto."""
    conf = STAR_TIER_MAPPING.get(tier)
    if not conf:
        return []
    roles = list(conf.get("roles", []))
    if TIER_3_ROLE_ID in roles and DEFAULT_ROLE_ID:
        roles.append(DEFAULT_ROLE_ID)
    return list(set([r for r in roles if r]))


# ===============================
# INVOICE / SUSCRIPCIÓN
# ===============================
def create_subscription_invoice_link(bot, tier: str) -> str:
    """Crea un link de invoice de suscripción recurrente en Stars para un tier.
    Devuelve la URL del invoice (createInvoiceLink)."""
    conf = STAR_TIER_MAPPING[tier]
    prices = [LabeledPrice(label=conf["label"], amount=conf["stars"])]

    # Telegram corta la descripción en 255 caracteres: metemos los perks y recortamos.
    perks = " • ".join(conf.get("perks", []))
    description = f"{perks} • Renews every 30 days, cancel anytime." if perks else \
        "Monthly subscription, renews every 30 days. Cancel anytime."
    if len(description) > 255:
        description = description[:252] + "..."

    return bot.create_invoice_link(
        title=f"{conf['label']} — monthly",
        description=description,
        payload=tier,               # se recupera en successful_payment.invoice_payload
        provider_token="",          # Stars no usa provider token
        currency="XTR",
        prices=prices,
        subscription_period=STAR_SUBSCRIPTION_PERIOD,
    )


# ===============================
# PERSISTENCIA DE SUSCRIPCIONES
# ===============================
def record_payment(telegram_user_id: int, discord_user_id, tier: str,
                   charge_id: str, expiration, is_recurring: bool) -> None:
    """Registra/actualiza una suscripción tras un successful_payment."""
    exp_iso = None
    if expiration:
        try:
            exp_iso = datetime.fromtimestamp(int(expiration), tz=timezone.utc).isoformat()
        except Exception:
            exp_iso = None
    try:
        supabase.table(TELEGRAM_SUBS_TABLE).upsert({
            "telegram_user_id": int(telegram_user_id),
            "discord_user_id": str(discord_user_id) if discord_user_id else None,
            "tier": tier,
            "telegram_payment_charge_id": charge_id,
            "subscription_expiration_date": exp_iso,
            "status": "active",
            "is_recurring": bool(is_recurring),
            "updated_at": _now_iso(),
        }).execute()
        print(f"⭐ Pago registrado: tg={telegram_user_id} discord={discord_user_id} tier={tier}")
    except Exception as e:
        print(f"⚠️ Error registrando pago Stars: {e}")


def get_subscription(telegram_user_id: int):
    """Devuelve la fila de suscripción de un usuario de Telegram, o None."""
    try:
        res = supabase.table(TELEGRAM_SUBS_TABLE).select("*").eq(
            "telegram_user_id", int(telegram_user_id)
        ).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"⚠️ Error leyendo suscripción Stars: {e}")
        return None


def get_active_star_subs():
    """Devuelve las suscripciones vigentes (no expiradas) vinculadas a Discord.
    Incluye las 'canceled' que aún no llegan a su fecha de expiración (el usuario
    apagó la auto-renovación pero conserva acceso hasta el fin del periodo pagado).
    Usado por el loop del bot de Discord para otorgar roles."""
    try:
        res = supabase.table(TELEGRAM_SUBS_TABLE).select("*").in_(
            "status", ["active", "canceled"]
        ).execute()
    except Exception as e:
        print(f"⚠️ Error leyendo suscripciones activas Stars: {e}")
        return []
    now = datetime.now(timezone.utc)
    active = []
    for row in res.data or []:
        if not row.get("discord_user_id"):
            continue
        exp = row.get("subscription_expiration_date")
        if exp:
            try:
                exp_dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
                if exp_dt <= now:
                    continue  # expirada
            except Exception:
                pass
        active.append(row)
    return active


def get_all_linked_discord_ids():
    """Devuelve el conjunto de discord_user_id que alguna vez tuvieron una suscripción Stars
    (cualquier estado). Sirve para que el loop de Discord también evalúe a quién quitarle roles."""
    try:
        res = supabase.table(TELEGRAM_SUBS_TABLE).select("discord_user_id").execute()
        return {str(r["discord_user_id"]) for r in (res.data or []) if r.get("discord_user_id")}
    except Exception as e:
        print(f"⚠️ Error leyendo discord ids de Stars: {e}")
        return set()


def mark_expired_subs() -> None:
    """Marca como 'expired' las suscripciones (activas o canceladas) cuya fecha de expiración ya pasó."""
    try:
        res = supabase.table(TELEGRAM_SUBS_TABLE).select("*").in_(
            "status", ["active", "canceled"]
        ).execute()
    except Exception as e:
        print(f"⚠️ Error consultando para expirar subs: {e}")
        return
    now = datetime.now(timezone.utc)
    for row in res.data or []:
        exp = row.get("subscription_expiration_date")
        if not exp:
            continue
        try:
            exp_dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
        except Exception:
            continue
        if exp_dt <= now:
            try:
                supabase.table(TELEGRAM_SUBS_TABLE).update({
                    "status": "expired",
                    "updated_at": _now_iso(),
                }).eq("telegram_user_id", row["telegram_user_id"]).execute()
                print(f"⏳ Suscripción Stars expirada: tg={row['telegram_user_id']}")
            except Exception as e:
                print(f"⚠️ Error marcando expirada: {e}")


# ===============================
# CANCELAR / REEMBOLSAR
# ===============================
def cancel_subscription(bot, telegram_user_id: int) -> bool:
    """Cancela la auto-renovación de la suscripción de un usuario (editUserStarSubscription).
    La suscripción sigue activa hasta la fecha de expiración; luego el loop de Discord la baja."""
    sub = get_subscription(telegram_user_id)
    if not sub or not sub.get("telegram_payment_charge_id"):
        return False
    try:
        bot.edit_user_star_subscription(
            user_id=int(telegram_user_id),
            telegram_payment_charge_id=sub["telegram_payment_charge_id"],
            is_canceled=True,
        )
        supabase.table(TELEGRAM_SUBS_TABLE).update({
            "status": "canceled",
            "is_recurring": False,
            "updated_at": _now_iso(),
        }).eq("telegram_user_id", int(telegram_user_id)).execute()
        print(f"🚫 Suscripción Stars cancelada (auto-renovación off): tg={telegram_user_id}")
        return True
    except Exception as e:
        print(f"⚠️ Error cancelando suscripción Stars: {e}")
        return False
