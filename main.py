"""
main.py - Entrypoint del proyecto.
Inicia todos los servicios: Discord, Telegram (acceso), MonkeyDescargar, y FastAPI.
"""
import os
import sys
import time
import signal
import threading

import uvicorn

# Importar los bots (los handlers se registran al importar)
from bots.discord_bot import discord_client
from bots.telegram_access import telegram_bot
from bots.monkey_descargar import monkey_bot

# Importar la app FastAPI
from api.webhooks import app


## ====================
## SIGNAL HANDLING
## ====================
def graceful_shutdown(signum, frame):
    """Detiene los bots de forma limpia al recibir señales del sistema (SIGTERM/SIGINT)."""
    print(f"\n🛑 Señal {signum} recibida. Deteniendo servicios...")
    try:
        telegram_bot.stop_polling()
        monkey_bot.stop_polling()
    except:
        pass
    print("👋 Servicios detenidos. Saliendo...")
    sys.exit(0)


# Registrar señales de terminación (importante para Render/Docker)
signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)


## ====================
## RUNNERS
## ====================
def start_discord():
    """Hilo para Discord con auto-reconnect."""
    from config import DISCORD_BOT_TOKEN
    while True:
        try:
            print("🎮 Iniciando Discord Bot...")
            discord_client.run(DISCORD_BOT_TOKEN)
        except Exception as e:
            print(f"⚠️ Discord Bot error: {e}")
            time.sleep(10)


def start_telegram_access():
    """Hilo para el bot de acceso al canal."""
    while True:
        try:
            print("🤖 Telegram Bot 1 (Acceso) iniciado...")
            telegram_bot.infinity_polling(skip_pending=True, timeout=90)
        except Exception as e:
            err_msg = str(e)
            print(f"⚠️ Telegram Bot 1 error: {err_msg}")
            if "Conflict" in err_msg:
                print("🚨 Conflicto detectado (409). Esperando 20s para reintentar...")
                time.sleep(20)
            else:
                time.sleep(5)


def start_monkey_bot():
    """Hilo para el bot descargador MonkeyDescargar."""
    while True:
        try:
            print("🐵 MonkeyDescargar Bot iniciado...")
            monkey_bot.infinity_polling(skip_pending=True, timeout=90)
        except Exception as e:
            err_msg = str(e)
            print(f"⚠️ MonkeyDescargar error: {err_msg}")
            if "Conflict" in err_msg:
                print("🚨 Conflicto detectado (409). Esperando 20s para reintentar...")
                time.sleep(20)
            else:
                time.sleep(5)


if __name__ == "__main__":
    # Discord en hilo daemon
    threading.Thread(target=start_discord, daemon=True).start()

    # Telegram Bot 1 (acceso al canal) en hilo daemon
    threading.Thread(target=start_telegram_access, daemon=True).start()

    # MonkeyDescargar Bot en hilo daemon
    threading.Thread(target=start_monkey_bot, daemon=True).start()

    print("🚀 Todos los servicios iniciados")

    # FastAPI en el HILO PRINCIPAL (Render monitorea este puerto)
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
