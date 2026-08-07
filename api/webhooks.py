"""
webhooks.py - FastAPI app y endpoints.
"""
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import stripe

from config import (
    STRIPE_WEBHOOK_SECRET,
    TELEGRAM_TOKEN, MONKEY_TELEGRAM_TOKEN,
    STARS_TELEGRAM_TOKEN, MONKEY_QUOTLY_TOKEN,
)

app = FastAPI()

# Identificador único de ESTE proceso. Si al recargar /debug/status cambia, hay más de
# una instancia corriendo: eso provoca el 409 permanente de Telegram (dos procesos
# peleando por getUpdates) y respuestas incoherentes según a cuál responda el balanceador.
INSTANCE_ID = f"pid{os.getpid()}-{uuid.uuid4().hex[:6]}"
STARTED_AT = time.time()


def _tokens_duplicados():
    """Detecta si dos bots comparten token, que provoca un 409 permanente sin que
    exista una segunda instancia: dos hilos del mismo proceso hacen polling al mismo
    bot y Telegram corta a uno. Devuelve solo NOMBRES de variables, nunca los tokens."""
    tokens = {
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "MONKEY_TELEGRAM_TOKEN": MONKEY_TELEGRAM_TOKEN,
        "STARS_TELEGRAM_TOKEN": STARS_TELEGRAM_TOKEN,
        "MONKEY_QUOTLY_TOKEN": MONKEY_QUOTLY_TOKEN,
    }
    por_valor = {}
    for nombre, valor in tokens.items():
        if not valor or valor == "TOKEN":
            continue
        por_valor.setdefault(valor, []).append(nombre)
    return [nombres for nombres in por_valor.values() if len(nombres) > 1]


def _bot_ids():
    """Prefijo numérico de cada token (el bot_id, que es público: aparece en cada
    mensaje del bot). Sirve para ver de un vistazo si dos apuntan al mismo bot.
    Nunca incluye la parte secreta del token."""
    tokens = {
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "MONKEY_TELEGRAM_TOKEN": MONKEY_TELEGRAM_TOKEN,
        "STARS_TELEGRAM_TOKEN": STARS_TELEGRAM_TOKEN,
        "MONKEY_QUOTLY_TOKEN": MONKEY_QUOTLY_TOKEN,
    }
    salida = {}
    for nombre, valor in tokens.items():
        if not valor or ":" not in valor:
            salida[nombre] = "(sin configurar)"
        else:
            salida[nombre] = valor.split(":", 1)[0]
    return salida


@app.get("/")
async def home():
    return {"status": "Bot Active - All Services Running"}


@app.get("/debug/status")
async def debug_status():
    """Estado en vivo del bot de Discord y del loop de roles.

    Existe porque los logs de Render mezclan la salida de cinco bots y cuesta
    encontrar en ellos si el loop está corriendo. Devuelve solo banderas y conteos:
    ningún ID de usuario ni dato personal.
    """
    # Import diferido: al importarse, bots.discord_bot arranca el cliente de Discord.
    from bots.discord_bot import STATUS

    diagnostico = []
    if not STATUS["discord_ready"]:
        diagnostico.append("El bot de Discord no terminó de conectar (on_ready no corrió).")
    if not STATUS["guild_found"]:
        diagnostico.append("No se encontró el servidor: revisa DISCORD_GUILD_ID.")
    if not STATUS["loop_running"]:
        diagnostico.append("El loop de roles NO está corriendo: nadie recibirá roles.")
    if STATUS["members_missing_last_run"]:
        diagnostico.append(
            f"{STATUS['members_missing_last_run']} suscriptor(es) con pago activo no "
            "están en el servidor de Discord."
        )
    if STATUS["last_check_error"]:
        diagnostico.append(f"Último error: {STATUS['last_check_error']}")
    duplicados = _tokens_duplicados()
    for grupo in duplicados:
        diagnostico.append(
            "⚠️ CAUSA DEL ERROR 409: estas variables tienen el MISMO token y por eso "
            f"pelean por los mensajes del mismo bot: {' y '.join(grupo)}."
        )

    if not diagnostico:
        diagnostico.append("Todo en orden.")

    return {
        **STATUS,
        "instance_id": INSTANCE_ID,
        "uptime_seconds": int(time.time() - STARTED_AT),
        "bot_ids": _bot_ids(),
        "tokens_duplicados": duplicados,
        "pista_instancias": (
            "Recarga esta página varias veces: si instance_id CAMBIA, hay más de un "
            "proceso corriendo y esa es la causa del error 409 de Telegram."
        ),
        "diagnostico": diagnostico,
    }


# ⚠️ DEPRECADO: webhook de Stripe. Se eliminará al completar la migración a Telegram Stars.
# El cobro con Stars no usa este webhook (Telegram entrega los pagos por polling al bot de Stars).
@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except:
        return JSONResponse(status_code=400, content={"error": "invalid"})
    return JSONResponse(status_code=200, content={"message": "ok"})
