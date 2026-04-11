import os
import re
import glob
import shutil
import discord
from discord.ext import tasks
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from supabase import create_client, Client
import uvicorn
import stripe
from dotenv import load_dotenv
import asyncio
import threading
import time
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo
import yt_dlp
import instaloader

load_dotenv()

## ===============================
## CONFIGURACIÓN
## ===============================

# --- SAFE MODE ---
SAFE_MODE_NO_BAN = False 

# Tiers
TIER_MAPPING = {
    "prod_SZ9dmrnfH9AwhO": 1459004030381592606, # Tier 1
    "prod_SZ9eQne47KPluz": 1459004119711879372, # Tier 2
    "prod_SZ9ezfEZ3OhuFC": 1459004146970787861  # Tier 3
}
TIER_3_PRODUCT_ID = "prod_SZ9ezfEZ3OhuFC"
TIER_3_ROLE_ID = TIER_MAPPING[TIER_3_PRODUCT_ID]

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0"))
DEFAULT_ROLE_ID = int(os.environ.get("DISCORD_ROLE_ID", "0"))
ADMIN_LOG_CHANNEL_ID = int(os.environ.get("ADMIN_LOG_CHANNEL_ID", "0"))

MANAGED_ROLES = list(TIER_MAPPING.values())
if DEFAULT_ROLE_ID: MANAGED_ROLES.append(DEFAULT_ROLE_ID)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

stripe.api_key = STRIPE_SECRET_KEY
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
TABLE_NAME = "subscriptions_discord"

ACTIVE_STATUSES = ["active", "trialing", "past_due"]

# Telegram Config (Bot de acceso al canal)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID', '-100...') 
CHANNEL_LINK = os.environ.get('CHANNEL_LINK', 'https://t.me/...')
TELEGRAM_ADMIN_ID = int(os.environ.get('TELEGRAM_ADMIN_ID', '0'))
telegram_links = {
    "1": os.environ.get('LINK_BOT1'),
    "2": os.environ.get('LINK_BOT2'),
    "3": os.environ.get('LINK_BOT3'),
    "4": os.environ.get('LINK_BOT4')
}

# Telegram Config (Bot descargador - MonkeyDescargar)
MONKEY_TELEGRAM_TOKEN = os.environ.get('MONKEY_TELEGRAM_TOKEN', '8716244791:AAEdLg6RTfdNljLb3UreC9k9wauUk-1te0o')

## ====================
## HELPER STRIPE (ASÍNCRONO - SOLUCIÓN AL CRASH)
## ====================
async def get_customer_subscription_data(customer_id: str):
    def _blocking_stripe_call():
        try:
            active = stripe.Subscription.list(customer=customer_id, status='active', limit=1, expand=['data.plan.product'])
            if active.data: return "active", active.data[0].plan.product
            trial = stripe.Subscription.list(customer=customer_id, status='trialing', limit=1, expand=['data.plan.product'])
            if trial.data: return "trialing", trial.data[0].plan.product
            past = stripe.Subscription.list(customer=customer_id, status='past_due', limit=1, expand=['data.plan.product'])
            if past.data: return "past_due", past.data[0].plan.product
            return "canceled", None
        except Exception as e:
            print(f"🚨 Stripe Error {customer_id}: {e}")
            return None, None
    return await asyncio.to_thread(_blocking_stripe_call)

def calculate_roles_to_assign(product_obj):
    product_id = product_obj.get('id') if isinstance(product_obj, dict) else product_obj
    roles_to_give = []
    tier_role = TIER_MAPPING.get(product_id)
    if tier_role:
        roles_to_give.append(tier_role)
        if tier_role == TIER_3_ROLE_ID:
            roles_to_give.append(DEFAULT_ROLE_ID)
    else:
        roles_to_give.append(TIER_3_ROLE_ID)
        roles_to_give.append(DEFAULT_ROLE_ID)
    return list(set([r for r in roles_to_give if r != 0]))

## ====================
## FASTAPI
## ====================
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

## ====================
## TELEGRAM BOT 1 - Acceso al canal
## ====================
telegram_bot = telebot.TeleBot(TELEGRAM_TOKEN)

def check_membership(user_id):
    clean_id = str(CHANNEL_ID).strip().replace("'", "").replace('"', "")
    if not clean_id.startswith("-100"): clean_id = "-100" + clean_id
    try:
        member = telegram_bot.get_chat_member(int(clean_id), user_id)
        return member.status in ['creator', 'administrator', 'member', 'restricted']
    except:
        return False

def get_main_menu():
    markup = InlineKeyboardMarkup(row_width=1)
    btn1 = InlineKeyboardButton("🔥 Img to Video Bot 1 monkeyvideos", url=telegram_links["1"])
    btn2 = InlineKeyboardButton("🤖 Img to Video Bot 2 videos69", url=telegram_links["2"])
    btn3 = InlineKeyboardButton("🤖 Nudify videos", url=telegram_links["3"])
    btn4 = InlineKeyboardButton("🔥 Img to img Bot ", url=telegram_links["4"])
    markup.add(btn1, btn2, btn3, btn4)
    return markup

@telegram_bot.message_handler(commands=['start'])
def send_welcome(message):
    if check_membership(message.from_user.id):
        telegram_bot.reply_to(message, "✅ **Access Granted**", reply_markup=get_main_menu(), parse_mode="Markdown")
    else:
        markup = InlineKeyboardMarkup()
        btn_join = InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)
        markup.add(btn_join)
        telegram_bot.reply_to(
            message, 
            "⛔ **Access Denied**\n\nYou must join the channel first to use this bot.", 
            reply_markup=markup,
            parse_mode="Markdown"
        )

## ====================
## TELEGRAM BOT 2 - MonkeyDescargar (Descargador de medios)
## ====================
monkey_bot = telebot.TeleBot(MONKEY_TELEGRAM_TOKEN)

# =============================================
# COOKIES HARDCODEADAS (Twitter/X y YouTube)
# =============================================
TWITTER_COOKIES_RAW = """# Netscape HTTP Cookie File
# https://curl.haxx.se/rfc/cookie_spec.html
# This is a generated file! Do not edit.

.x.com	TRUE	/	TRUE	1810490352	guest_id_marketing	v1%3A177593035160676828
.x.com	TRUE	/	TRUE	1810490352	guest_id_ads	v1%3A177593035160676828
.x.com	TRUE	/	TRUE	1810490352	guest_id	v1%3A177593035160676828
.x.com	TRUE	/	TRUE	1810490352	personalization_id	"v1_HWahJhSkq8QSBO7tRUbMaw=="
.x.com	TRUE	/	TRUE	1775939352	gt	2043026088029257913
.x.com	TRUE	/	FALSE	1810490352	__cuid	2faaaa98d11f4d509c0ecc751d9fa97c
.x.com	TRUE	/	TRUE	1776535153	external_referer	padhuUp37zjgzgv1mFWxJ12Ozwit7owX|0|8e8t2xd8A2w%3D
x.com	FALSE	/	FALSE	1791482363	g_state	{"i_l":0,"i_ll":1775930361857,"i_e":{"enable_itp_optimization":18},"i_et":1775930361857}
.x.com	TRUE	/	TRUE	1776016764	att	1-2MA8eUGaasDYaUSeZehuZoy71OYNnByYUryIItWq
.x.com	TRUE	/	TRUE	0	_twitter_sess	BAh7BiIKZmxhc2hJQzonQWN0aW9uQ29udHJvbGxlcjo6Rmxhc2g6OkZsYXNo%250ASGFzaHsABjoKQHVzZWR7AA%253D%253D--1164b91ac812d853b877e93ddb612b7471bebc74
.x.com	TRUE	/	TRUE	1810490561	kdt	nFnnNsWvzF8gbFL4Wa5WZt7qpBe0KEWUzxHNssVR
.x.com	TRUE	/	TRUE	1810490561	twid	"u=2043026939632300032"
.x.com	TRUE	/	TRUE	1810490561	auth_token	80108308f474e5f985bcd38ece6324207266216f
.x.com	TRUE	/	TRUE	1810490561	ct0	2963a1517c6751d87012522103fa8d174c0a87f295ae9005a47edb576183a4e38d51ff745c3fb03646e3b0a99bef784cb014fcdb11046e11f9f4d9b5c1979ebd3f56bcfb0bfa798ad02382ab6bc01870
.x.com	TRUE	/	TRUE	1775933053	__cf_bm	IFzO8x.uEl0jgkZKgfR_.YyJ4zULeT1rvGiaMbxYCJ0-1775931253.0487516-1.0.1.1-23AFEOlfQth9FG0CMqLv4DK4ewyuKrXtBlj37uddHv1ypBRS_lRQ7vNoYgwd3raWKMbpnUe9jmZSAnvNvwwXrNjFM4yPE.sAwjXrvVv4i4ootkJRa1.cbfgSrn1AzkOn
"""

YOUTUBE_COOKIES_RAW = """# Netscape HTTP Cookie File
# https://curl.haxx.se/rfc/cookie_spec.html
# This is a generated file! Do not edit.

.youtube.com	TRUE	/	TRUE	1809482486	LOGIN_INFO	AFmmF2swRQIhALjjNlYJ9LOhBAIoyVnE3DFAozgoDro6wZRdsYL527WWAiBZWYSm2Xdg4OncoMrqanoRW1uaPB-EbDWoS-PKCqr7TA:QUQ3MjNmeVhKaGpNd3V5RnRQc3k0QlNVcEE3dW9od0owVXpXR1IzVjBoVXdGR0ZoLWpaVmtsc2hINjN6OEtRYXlfNFA3bW5pV2d3UGdMUzhDbXBMaEVtSXR3bFh3YW9rbHpBYWZHNGExZlhtM19VRHR3eXRKVC1pS0JTUzdISVhzckJPQzdUZ0pnRDJrdGl6cjB3aF9KMUZFR2doZTNHZmR3
.youtube.com	TRUE	/	TRUE	1810491797	PREF	f4=4000000&tz=America.Mexico_City
.youtube.com	TRUE	/	TRUE	1791482342	__Secure-BUCKET	CL4G
.youtube.com	TRUE	/	FALSE	1810491796	HSID	A6j_KIzwOuoRWzd6A
.youtube.com	TRUE	/	TRUE	1810491796	SSID	AUBTpfFf4O51S6Tdu
.youtube.com	TRUE	/	FALSE	1810491796	APISID	KTY1E6SqrSFfxIKb/AIMF4uXAgQAhZdqvR
.youtube.com	TRUE	/	TRUE	1810491796	SAPISID	Ity3cS4K7q1j5aRS/AVSxqJ-fgI5dsd037
.youtube.com	TRUE	/	TRUE	1810491796	__Secure-1PAPISID	Ity3cS4K7q1j5aRS/AVSxqJ-fgI5dsd037
.youtube.com	TRUE	/	TRUE	1810491796	__Secure-3PAPISID	Ity3cS4K7q1j5aRS/AVSxqJ-fgI5dsd037
.youtube.com	TRUE	/	TRUE	1807467553	__Secure-1PSIDTS	sidts-CjQBWhotCXxBwjbBMAmMKz6QvEM46yc8gdjCqVdMepZphxiL6sIgC3DC5oK2aLB7iu20LiTAEAA
.youtube.com	TRUE	/	TRUE	1807467553	__Secure-3PSIDTS	sidts-CjQBWhotCXxBwjbBMAmMKz6QvEM46yc8gdjCqVdMepZphxiL6sIgC3DC5oK2aLB7iu20LiTAEAA
.youtube.com	TRUE	/	FALSE	1810491796	SID	g.a0008wiNnftFjp7CxCTOruk65w3U7fKKyM0TUHkf0O2bU1Ir0vDKEFhFFSoKd4EuPd_ykJOaRAACgYKAbESARUSFQHGX2MiEzCV1hQhCHSxaoVjIVqZiRoVAUF8yKqUTZbTGJWikQaItL79ByEy0076
.youtube.com	TRUE	/	TRUE	1810491796	__Secure-1PSID	g.a0008wiNnftFjp7CxCTOruk65w3U7fKKyM0TUHkf0O2bU1Ir0vDKPriIE4uaG7Rd7F8uqpvEjgACgYKAeYSARUSFQHGX2MiBrwVghmP_vE75sJ2sr3OoBoVAUF8yKqcJbQPTTdqluH66BoSs5SM0076
.youtube.com	TRUE	/	TRUE	1810491796	__Secure-3PSID	g.a0008wiNnftFjp7CxCTOruk65w3U7fKKyM0TUHkf0O2bU1Ir0vDKAFjUeGddQWqtjVSBe1y-iwACgYKAdcSARUSFQHGX2MiiEEKB6_kRs4pcyvteLVdCxoVAUF8yKoYCZcdlImIKfrByQs-GAwo0076
.youtube.com	TRUE	/	FALSE	1807467799	SIDCC	AKEyXzWWG3DqQcUgx4IFcCJfhE2Bz_zvEiH9MYdCarBzUTE85IcJTDVMs4kFaVdBbQwCaQpj
.youtube.com	TRUE	/	TRUE	1807467799	__Secure-1PSIDCC	AKEyXzXfip5TX-II63-D5yeCPOxWluMqP5HnezitEr7rxbStb0X9CcOv0abPYx1j6rfW9Bfg
.youtube.com	TRUE	/	TRUE	1807467799	__Secure-3PSIDCC	AKEyXzXStuCfwgwvGH6CdzOmER4_Jab5BVwHpMCvw9Eu_x0LE6Uzv6wxLQy7wEvPxEsoAhAW
.youtube.com	TRUE	/	TRUE	1775932401	CONSISTENCY	AH5K9rbkocTODTtzvY3qPUkOBCjfLr7bSFmzJ11hZaE_b4wCXcQ0hWQf9wysJLSqZjpgL2PlM7OuNbfsV0dCa4Z2n_7hVZ7Pttnix3-00_TWJuokI7tSIHWPEm0
.youtube.com	TRUE	/	TRUE	1791483796	VISITOR_INFO1_LIVE	Gf6X-O2rY0k
.youtube.com	TRUE	/	TRUE	1791483796	VISITOR_PRIVACY_METADATA	CgJNWBIEGgAgQw%3D%3D
.youtube.com	TRUE	/	TRUE	0	YSC	Aqjp37qTh1k
.youtube.com	TRUE	/	TRUE	1791482342	__Secure-ROLLOUT_TOKEN	CNOD2MawkaSS8QEQoYLjwoXJkwMYi5WxibDmkwM%3D
"""

def _escribir_cookies(contenido, archivo):
    """Escribe cookies a un archivo y verifica que se creó correctamente."""
    try:
        with open(archivo, 'w', encoding='utf-8') as f:
            f.write(contenido)
        ruta_abs = os.path.abspath(archivo)
        size = os.path.getsize(ruta_abs)
        print(f"🍪 Cookies escritas en {ruta_abs} ({size} bytes)")
        return ruta_abs
    except Exception as e:
        print(f"⚠️ Error escribiendo cookies en {archivo}: {e}")
        return None

# Escribir archivos de cookies al iniciar
TWITTER_COOKIES_FILE = _escribir_cookies(TWITTER_COOKIES_RAW.strip(), 'twitter_cookies.txt')
YOUTUBE_COOKIES_FILE = _escribir_cookies(YOUTUBE_COOKIES_RAW.strip(), 'youtube_cookies.txt')

# Configuración de yt-dlp (optimizada para servidores/datacenter)
YDL_OPTS = {
    'format': (
        'bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/'
        'bestvideo[ext=mp4]+bestaudio[ext=m4a]/'
        'bestvideo[height<=1080]+bestaudio/'
        'bestvideo+bestaudio/'
        'best[ext=mp4]/best'
    ),
    'outtmpl': 'downloads/%(id)s_%(autonumber)s.%(ext)s',
    'quiet': True,
    'noplaylist': False,
    'writethumbnail': False,
    'noprogress': True,
    'merge_output_format': 'mp4',
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    },
    'extractor_args': {
        'youtube': {
            'player_client': ['web', 'ios', 'tv'],
            'player_skip': ['webpage', 'configs'],
        }
    },
    'socket_timeout': 30,
    'retries': 3,
}

# Aplicar cookies de YouTube
if YOUTUBE_COOKIES_FILE:
    YDL_OPTS['cookiefile'] = YOUTUBE_COOKIES_FILE

# Opciones específicas para X/Twitter
YDL_OPTS_TWITTER = {
    **YDL_OPTS,
    'format': 'best[ext=mp4]/best',
}
# Aplicar cookies de Twitter (OBLIGATORIO - guest tokens ya no funcionan)
if TWITTER_COOKIES_FILE:
    YDL_OPTS_TWITTER['cookiefile'] = TWITTER_COOKIES_FILE

# Instancia de instaloader (para posts públicos de IG)
IL = instaloader.Instaloader(
    download_videos=True,
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    post_metadata_txt_pattern='',
)

def extraer_shortcode(url):
    """Extrae el shortcode de una URL de Instagram."""
    match = re.search(r'instagram\.com/(?:p|reel|reels)/([A-Za-z0-9_-]+)', url)
    return match.group(1) if match else None

def descargar_instagram(url):
    """Descarga un post de Instagram usando instaloader."""
    shortcode = extraer_shortcode(url)
    if not shortcode:
        print(f"❌ No se pudo extraer shortcode de: {url}")
        return []
    
    print(f"📸 Usando instaloader para shortcode: {shortcode}")
    carpeta_temp = "downloads/ig_temp"
    if os.path.exists(carpeta_temp):
        shutil.rmtree(carpeta_temp)
    os.makedirs(carpeta_temp, exist_ok=True)
    
    try:
        post = instaloader.Post.from_shortcode(IL.context, shortcode)
        IL.dirname_pattern = carpeta_temp
        IL.download_post(post, target="")
        
        archivos = []
        for f in sorted(glob.glob(os.path.join(carpeta_temp, '*'))):
            ext = f.lower()
            if ext.endswith(('.jpg', '.jpeg', '.png', '.webp', '.mp4')):
                destino = os.path.join('downloads', os.path.basename(f))
                shutil.move(f, destino)
                archivos.append(destino)
                print(f"  ✅ {destino}")
        
        try: shutil.rmtree(carpeta_temp)
        except: pass
        return archivos
    except Exception as e:
        print(f"❌ Error con instaloader: {e}")
        try: shutil.rmtree(carpeta_temp)
        except: pass
        return []

def limpiar_url(url):
    """Limpia y normaliza URLs para evitar bugs de yt-dlp."""
    url = url.strip()
    
    # YouTube Shorts → formato watch?v= (yt-dlp a veces falla con /shorts/)
    match = re.search(r'youtube\.com/shorts/([A-Za-z0-9_-]+)', url)
    if match:
        video_id = match.group(1)
        url = f"https://www.youtube.com/watch?v={video_id}"
        print(f"🔄 Short convertido a: {url}")
        return url
    
    # youtu.be/ID → también normalizar
    match = re.search(r'youtu\.be/([A-Za-z0-9_-]+)', url)
    if match:
        video_id = match.group(1)
        url = f"https://www.youtube.com/watch?v={video_id}"
        return url
    
    # Limpiar parámetros de tracking de Instagram (?igsh=, ?utm_source=, etc.)
    if 'instagram.com' in url:
        match = re.search(r'(https?://(?:www\.)?instagram\.com/(?:p|reel|reels)/[A-Za-z0-9_-]+/?)', url)
        if match:
            url = match.group(1)
    
    return url

def detectar_plataforma(url):
    """Detecta la plataforma de una URL."""
    url_lower = url.lower()
    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'youtube'
    elif 'instagram.com' in url_lower:
        return 'instagram'
    elif 'tiktok.com' in url_lower:
        return 'tiktok'
    elif 'x.com' in url_lower or 'twitter.com' in url_lower:
        return 'twitter'
    elif 'facebook.com' in url_lower or 'fb.watch' in url_lower or 'fb.gg' in url_lower:
        return 'facebook'
    return 'desconocida'

def descargar_media(url, max_reintentos=2):
    """Descarga media con yt-dlp. Para Instagram usa instaloader como fallback."""
    os.makedirs('downloads', exist_ok=True)
    
    # Limpiar URL antes de pasarla a yt-dlp
    url = limpiar_url(url)
    plataforma = detectar_plataforma(url)
    
    # Usar opciones específicas para Twitter/X
    if plataforma == 'twitter':
        opciones = YDL_OPTS_TWITTER
    else:
        opciones = YDL_OPTS
    
    print(f"🔗 Plataforma detectada: {plataforma}")
    
    error_msg = None
    ultimo_error = None
    
    for intento in range(max_reintentos + 1):
        archivos_antes = set(glob.glob('downloads/*'))
        
        try:
            with yt_dlp.YoutubeDL(opciones) as ydl:
                info = ydl.extract_info(url, download=True)
            
            archivos_despues = set(glob.glob('downloads/*'))
            archivos_nuevos = sorted(archivos_despues - archivos_antes)
            
            # Si yt-dlp descargó algo pero no lo detectó el glob, buscar en info
            if not archivos_nuevos and info:
                archivo = info.get('filepath') or info.get('_filename') or info.get('filename')
                if not archivo and 'requested_downloads' in info:
                    descargas = info.get('requested_downloads', [])
                    if descargas:
                        archivo = descargas[0].get('filepath') or descargas[0].get('_filename')
                if archivo and os.path.exists(archivo):
                    archivos_nuevos.append(archivo)
                    print(f"✅ Encontrado vía info dict: {archivo}")
            
            # Fallback: si yt-dlp no descargó nada y es Instagram → instaloader
            if not archivos_nuevos and plataforma == 'instagram':
                print("⚠️ yt-dlp no descargó archivos de Instagram, usando instaloader...")
                archivos_nuevos = descargar_instagram(url)
            
            return info, archivos_nuevos, None
        
        except yt_dlp.utils.DownloadError as e:
            ultimo_error = e
            error_str = str(e).lower()
            
            # Si es error de formato (YouTube Shorts), intentar con formatos cada vez más simples
            if 'requested format is not available' in error_str:
                if intento == 0:
                    print(f"⚠️ Formato no disponible, reintentando con best[ext=mp4]/best...")
                    opciones = {**opciones, 'format': 'best[ext=mp4]/best'}
                    continue
                else:
                    # Último recurso: formato 'best' sin merge_output_format
                    print(f"⚠️ Sigue fallando, reintentando con formato 'best' puro...")
                    opciones_simple = {**opciones, 'format': 'best'}
                    opciones_simple.pop('merge_output_format', None)
                    opciones = opciones_simple
                    continue
            
            # Si es error de Instagram por login/privacidad, intentar con instaloader
            if plataforma == 'instagram' and (
                'empty media response' in error_str or
                'not available to everyone' in error_str or
                'login required' in error_str
            ):
                print("⚠️ Instagram requiere login en yt-dlp, probando con instaloader...")
                archivos = descargar_instagram(url)
                if archivos:
                    return None, archivos, None
            
            # No reintentar para otros errores de descarga
            return None, [], str(e)
        
        except (TimeoutError, ConnectionError, OSError) as e:
            ultimo_error = e
            if intento < max_reintentos:
                espera = (intento + 1) * 3
                print(f"⏳ Timeout/conexión fallida (intento {intento + 1}/{max_reintentos + 1}), "
                      f"reintentando en {espera}s...")
                time.sleep(espera)
            else:
                return None, [], str(e)
        
        except Exception as e:
            return None, [], str(e)
    
    return None, [], str(ultimo_error) if ultimo_error else "Error desconocido"

@monkey_bot.message_handler(func=lambda msg: True, content_types=['text'])
def monkey_procesar_mensaje(message):
    """Handler principal del bot descargador."""
    texto = message.text.strip()
    chat_id = message.chat.id
    
    # Lista de dominios soportados (ahora incluye X/Twitter)
    redes_soportadas = [
        "youtube.com", "youtu.be",
        "tiktok.com",
        "instagram.com",
        "facebook.com", "fb.watch", "fb.gg",
        "x.com", "twitter.com",
    ]
    
    if not any(red in texto.lower() for red in redes_soportadas):
        return  # No es un link soportado, ignorar
    
    # YouTube Community Posts no son videos, yt-dlp no los soporta
    if re.search(r'youtube\.com/post/', texto.lower()):
        monkey_bot.reply_to(message, "⚠️ Ese es un **Community Post** de YouTube (texto/imágenes), no un video. Solo puedo descargar videos, shorts y reels.", parse_mode='Markdown')
        return
    
    # Detectar plataforma para mensaje personalizado
    plataforma = detectar_plataforma(texto)
    emoji_plataforma = {
        'youtube': '🎬', 'instagram': '📸', 'tiktok': '🎵',
        'twitter': '🐦', 'facebook': '📘', 'desconocida': '🔗'
    }
    emoji = emoji_plataforma.get(plataforma, '🔗')
    
    msg_espera = monkey_bot.reply_to(message, f"{emoji} Monkey Descargando de {plataforma.capitalize()} en monkey HD... dame un monkey momento.")
    
    try:
        info, archivos_nuevos, dl_error = descargar_media(texto)
        
        print(f"\n🔍 MONKEY ARCHIVOS DESCARGADOS: {archivos_nuevos}")
        if dl_error:
            print(f"📛 MONKEY ERROR DE DESCARGA: {dl_error}")
        
        if archivos_nuevos:
            if len(archivos_nuevos) == 1:
                # Un solo archivo
                archivo = archivos_nuevos[0]
                if archivo.lower().endswith('.mp4'):
                    with open(archivo, 'rb') as f:
                        monkey_bot.send_video(chat_id, f, supports_streaming=True)
                else:
                    with open(archivo, 'rb') as f:
                        monkey_bot.send_photo(chat_id, f)
            else:
                # Múltiples archivos → media group (max 10 por lote)
                for i in range(0, len(archivos_nuevos), 10):
                    lote = archivos_nuevos[i:i+10]
                    media_group = []
                    for archivo in lote:
                        if archivo.lower().endswith('.mp4'):
                            media_group.append(InputMediaVideo(open(archivo, 'rb')))
                        else:
                            media_group.append(InputMediaPhoto(open(archivo, 'rb')))
                    
                    if len(media_group) == 1:
                        archivo = lote[0]
                        if archivo.lower().endswith('.mp4'):
                            with open(archivo, 'rb') as f:
                                monkey_bot.send_video(chat_id, f, supports_streaming=True)
                        else:
                            with open(archivo, 'rb') as f:
                                monkey_bot.send_photo(chat_id, f)
                    else:
                        try:
                            monkey_bot.send_media_group(chat_id, media_group)
                        except Exception as mg_err:
                            print(f"⚠️ Error media_group, enviando uno por uno: {mg_err}")
                            for archivo in lote:
                                try:
                                    if archivo.lower().endswith('.mp4'):
                                        with open(archivo, 'rb') as f:
                                            monkey_bot.send_video(chat_id, f)
                                    else:
                                        with open(archivo, 'rb') as f:
                                            monkey_bot.send_photo(chat_id, f)
                                except Exception as ind_err:
                                    print(f"❌ Error enviando {archivo}: {ind_err}")
            
            # Limpiar archivos descargados
            for arch in archivos_nuevos:
                try: os.remove(arch)
                except: pass
            
            # Borrar mensaje de "Descargando..."
            try: monkey_bot.delete_message(chat_id, msg_espera.message_id)
            except: pass
        
        else:
            # No se descargó nada - mostrar error amigable
            if dl_error:
                dl_lower = dl_error.lower()
                if 'empty media response' in dl_lower or 'not available to everyone' in dl_lower:
                    user_msg = (
                        "❌ No se pudo descargar de Instagram.\n"
                        "Este post parece ser privado o estar restringido.\n"
                        "Solo puedo descargar contenido público."
                    )
                elif 'bad guest token' in dl_lower or ('twitter' in dl_lower and 'api' in dl_lower):
                    user_msg = (
                        "❌ Twitter/X requiere autenticación para descargar.\n"
                        "El administrador necesita configurar las cookies de Twitter."
                    )
                elif 'timeout' in dl_lower or 'connection' in dl_lower or 'timed out' in dl_lower:
                    user_msg = (
                        "❌ Error de conexión.\n"
                        "El servidor tardó demasiado en responder.\n"
                        "Intenta enviar el link de nuevo en unos minutos."
                    )
                elif 'requested format is not available' in dl_lower:
                    user_msg = (
                        "❌ No se pudo descargar este video en ningún formato disponible.\n"
                        "Puede ser una restricción regional o del contenido."
                    )
                else:
                    short_err = dl_error[:300]
                    user_msg = f"❌ Error al descargar:\n`{short_err}`"
                
                monkey_bot.edit_message_text(
                    user_msg, chat_id, msg_espera.message_id, parse_mode='Markdown'
                )
            else:
                monkey_bot.edit_message_text(
                    "❌ No se pudo descargar. El post puede ser privado o la plataforma bloqueó la descarga.",
                    chat_id, msg_espera.message_id
                )
    
    except Exception as e:
        error_msg = str(e)[:800]
        try:
            monkey_bot.edit_message_text(
                f"❌ Error al descargar:\n`{error_msg}`",
                chat_id, msg_espera.message_id,
                parse_mode='Markdown'
            )
        except:
            pass

## ====================
## DISCORD BOT
## ====================
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
    if guild: admin_log_channel = discord_client.get_channel(ADMIN_LOG_CHANNEL_ID)
    if not check_subscriptions.is_running():
        check_subscriptions.start()

@discord_client.event
async def on_message(message):
    if message.author.bot: return
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
                supabase.table(TABLE_NAME).update({"discord_user_id": str(message.author.id), "subscription_status": status, "updated_at": now}).eq("stripe_customer_id", c_id).execute()
            else:
                supabase.table(TABLE_NAME).insert({"stripe_customer_id": c_id, "discord_user_id": str(message.author.id), "subscription_status": status, "updated_at": now}).execute()

            roles = calculate_roles_to_assign(prod)
            if guild:
                mem = guild.get_member(message.author.id)
                if mem:
                    for rid in roles:
                        r = guild.get_role(rid)
                        if r: await mem.add_roles(r)
            
            await message.channel.send("✅ Linked successfully!")
            if admin_log_channel: await admin_log_channel.send(f"🟢 Link: {message.author.mention} ({email})")
        except Exception as e:
            print(f"Link Err: {e}")
            await message.channel.send("❌ Error.")

@tasks.loop(minutes=10)
async def check_subscriptions():
    print("🔄 Checking subscriptions...")
    if not guild: return
    try:
        response = supabase.table(TABLE_NAME).select("*").neq("discord_user_id", "None").execute()
        user_active_map = {}
        for row in response.data:
            c_id = row.get("stripe_customer_id")
            d_id = row.get("discord_user_id")
            current_db_status = row.get("subscription_status")
            real_status, prod_obj = await get_customer_subscription_data(c_id)
            if real_status is None: continue
            if real_status != current_db_status:
                supabase.table(TABLE_NAME).update({
                    "subscription_status": real_status,
                    "updated_at": discord.utils.utcnow().isoformat()
                }).eq("stripe_customer_id", c_id).execute()
            if d_id not in user_active_map: user_active_map[d_id] = False
            if real_status in ACTIVE_STATUSES: user_active_map[d_id] = True
            await asyncio.sleep(0.5)

        processed_users = set()
        for row in response.data:
            d_id = row.get("discord_user_id")
            if d_id in processed_users: continue
            processed_users.add(d_id)
            member = guild.get_member(int(d_id))
            if not member: continue
            is_user_safe = user_active_map.get(d_id, False)
            if is_user_safe:
                active_row = next((r for r in response.data if r["discord_user_id"] == d_id and r["subscription_status"] in ACTIVE_STATUSES), None)
                if active_row:
                    _, prod_obj = await get_customer_subscription_data(active_row["stripe_customer_id"])
                    roles_to_add = calculate_roles_to_assign(prod_obj)
                    for rid in roles_to_add:
                        r = guild.get_role(rid)
                        if r and r not in member.roles:
                            await mem.add_roles(r, reason="Sub Activa")
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

## ====================
## RUNNERS
## ====================
def start_discord():
    """Hilo para Discord con auto-reconnect."""
    while True:
        try:
            print("🎮 Iniciando Discord Bot...")
            discord_client.run(DISCORD_BOT_TOKEN)
        except Exception as e:
            print(f"⚠️ Discord Bot error: {e}")
            print("🔄 Reconectando Discord en 10 segundos...")
            time.sleep(10)

def start_telegram_access():
    """Hilo para el bot de acceso al canal."""
    print("🤖 Telegram Bot 1 (Acceso) iniciado...")
    while True:
        try: telegram_bot.infinity_polling(skip_pending=True, timeout=90)
        except Exception as e:
            print(f"⚠️ Telegram Bot 1 error: {e}")
            time.sleep(5)

def start_monkey_bot():
    """Hilo para el bot descargador MonkeyDescargar."""
    print("🐵 MonkeyDescargar Bot iniciado...")
    print("📌 Plataformas soportadas: YouTube, TikTok, Instagram, Facebook, X/Twitter")
    while True:
        try: monkey_bot.infinity_polling(skip_pending=True, timeout=90)
        except Exception as e:
            print(f"⚠️ MonkeyDescargar error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    # Discord en hilo daemon (ya NO es el principal)
    threading.Thread(target=start_discord, daemon=True).start()
    
    # Telegram Bot 1 (acceso al canal) en hilo daemon
    threading.Thread(target=start_telegram_access, daemon=True).start()
    
    # MonkeyDescargar Bot en hilo daemon
    threading.Thread(target=start_monkey_bot, daemon=True).start()
    
    print("🚀 Todos los servicios iniciados")
    
    # FastAPI en el HILO PRINCIPAL (Render monitorea este puerto)
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
