"""
webhooks.py - FastAPI app y endpoints.
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import stripe

from config import STRIPE_WEBHOOK_SECRET

app = FastAPI()


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
    if not diagnostico:
        diagnostico.append("Todo en orden.")

    return {**STATUS, "diagnostico": diagnostico}


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
