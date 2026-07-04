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


@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except:
        return JSONResponse(status_code=400, content={"error": "invalid"})
    return JSONResponse(status_code=200, content={"message": "ok"})
