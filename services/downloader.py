"""
downloader.py - Lógica de descarga de medios.
Soporta: YouTube, Instagram, TikTok, Twitter/X, Facebook.
Usa yt-dlp como motor principal e instaloader como primario para Instagram.
"""
import os
import re
import glob
import shutil
import time

import requests
import yt_dlp
import instaloader
import urllib3

from config import IG_USERNAME, IG_PASSWORD, IG_COOKIES_RAW

# =============================================
# COOKIES HARDCODEADAS (Twitter/X y YouTube)
# =============================================
TWITTER_COOKIES_RAW = """# Netscape HTTP Cookie File
# https://curl.haxx.se/rfc/cookie_spec.html
# This is a generated file! Do not edit.

.x.com\tTRUE\t/\tTRUE\t1810490352\tguest_id_marketing\tv1%3A177593035160676828
.x.com\tTRUE\t/\tTRUE\t1810490352\tguest_id_ads\tv1%3A177593035160676828
.x.com\tTRUE\t/\tTRUE\t1810490352\tguest_id\tv1%3A177593035160676828
.x.com\tTRUE\t/\tTRUE\t1810490352\tpersonalization_id\t"v1_HWahJhSkq8QSBO7tRUbMaw=="
.x.com\tTRUE\t/\tTRUE\t1775939352\tgt\t2043026088029257913
.x.com\tTRUE\t/\tFALSE\t1810490352\t__cuid\t2faaaa98d11f4d509c0ecc751d9fa97c
.x.com\tTRUE\t/\tTRUE\t1776535153\texternal_referer\tpadhuUp37zjgzgv1mFWxJ12Ozwit7owX|0|8e8t2xd8A2w%3D
x.com\tFALSE\t/\tFALSE\t1791482363\tg_state\t{"i_l":0,"i_ll":1775930361857,"i_e":{"enable_itp_optimization":18},"i_et":1775930361857}
.x.com\tTRUE\t/\tTRUE\t1776016764\tatt\t1-2MA8eUGaasDYaUSeZehuZoy71OYNnByYUryIItWq
.x.com\tTRUE\t/\tTRUE\t0\t_twitter_sess\tBAh7BiIKZmxhc2hJQzonQWN0aW9uQ29udHJvbGxlcjo6Rmxhc2g6OkZsYXNo%250ASGFzaHsABjoKQHVzZWR7AA%253D%253D--1164b91ac812d853b877e93ddb612b7471bebc74
.x.com\tTRUE\t/\tTRUE\t1810490561\tkdt\tnFnnNsWvzF8gbFL4Wa5WZt7qpBe0KEWUzxHNssVR
.x.com\tTRUE\t/\tTRUE\t1810490561\ttwid\t"u=2043026939632300032"
.x.com\tTRUE\t/\tTRUE\t1810490561\tauth_token\t80108308f474e5f985bcd38ece6324207266216f
.x.com\tTRUE\t/\tTRUE\t1810490561\tct0\t2963a1517c6751d87012522103fa8d174c0a87f295ae9005a47edb576183a4e38d51ff745c3fb03646e3b0a99bef784cb014fcdb11046e11f9f4d9b5c1979ebd3f56bcfb0bfa798ad02382ab6bc01870
.x.com\tTRUE\t/\tTRUE\t1775933053\t__cf_bm\tIFzO8x.uEl0jgkZKgfR_.YyJ4zULeT1rvGiaMbxYCJ0-1775931253.0487516-1.0.1.1-23AFEOlfQth9FG0CMqLv4DK4ewyuKrXtBlj37uddHv1ypBRS_lRQ7vNoYgwd3raWKMbpnUe9jmZSAnvNvwwXrNjFM4yPE.sAwjXrvVv4i4ootkJRa1.cbfgSrn1AzkOn
"""

YOUTUBE_COOKIES_RAW = """# Netscape HTTP Cookie File
# https://curl.haxx.se/rfc/cookie_spec.html
# This is a generated file! Do not edit.

.youtube.com\tTRUE\t/\tTRUE\t1809482486\tLOGIN_INFO\tAFmmF2swRQIhALjjNlYJ9LOhBAIoyVnE3DFAozgoDro6wZRdsYL527WWAiBZWYSm2Xdg4OncoMrqanoRW1uaPB-EbDWoS-PKCqr7TA:QUQ3MjNmeVhKaGpNd3V5RnRQc3k0QlNVcEE3dW9od0owVXpXR1IzVjBoVXdGR0ZoLWpaVmtsc2hINjN6OEtRYXlfNFA3bW5pV2d3UGdMUzhDbXBMaEVtSXR3bFh3YW9rbHpBYWZHNGExZlhtM19VRHR3eXRKVC1pS0JTUzdISVhzckJPQzdUZ0pnRDJrdGl6cjB3aF9KMUZFR2doZTNHZmR3
.youtube.com\tTRUE\t/\tTRUE\t1810491797\tPREF\tf4=4000000&tz=America.Mexico_City
.youtube.com\tTRUE\t/\tTRUE\t1791482342\t__Secure-BUCKET\tCL4G
.youtube.com\tTRUE\t/\tFALSE\t1810491796\tHSID\tA6j_KIzwOuoRWzd6A
.youtube.com\tTRUE\t/\tTRUE\t1810491796\tSSID\tAUBTpfFf4O51S6Tdu
.youtube.com\tTRUE\t/\tFALSE\t1810491796\tAPISID\tKTY1E6SqrSFfxIKb/AIMF4uXAgQAhZdqvR
.youtube.com\tTRUE\t/\tTRUE\t1810491796\tSAPISID\tIty3cS4K7q1j5aRS/AVSxqJ-fgI5dsd037
.youtube.com\tTRUE\t/\tTRUE\t1810491796\t__Secure-1PAPISID\tIty3cS4K7q1j5aRS/AVSxqJ-fgI5dsd037
.youtube.com\tTRUE\t/\tTRUE\t1810491796\t__Secure-3PAPISID\tIty3cS4K7q1j5aRS/AVSxqJ-fgI5dsd037
.youtube.com\tTRUE\t/\tTRUE\t1807467553\t__Secure-1PSIDTS\tsidts-CjQBWhotCXxBwjbBMAmMKz6QvEM46yc8gdjCqVdMepZphxiL6sIgC3DC5oK2aLB7iu20LiTAEAA
.youtube.com\tTRUE\t/\tTRUE\t1807467553\t__Secure-3PSIDTS\tsidts-CjQBWhotCXxBwjbBMAmMKz6QvEM46yc8gdjCqVdMepZphxiL6sIgC3DC5oK2aLB7iu20LiTAEAA
.youtube.com\tTRUE\t/\tFALSE\t1810491796\tSID\tg.a0008wiNnftFjp7CxCTOruk65w3U7fKKyM0TUHkf0O2bU1Ir0vDKEFhFFSoKd4EuPd_ykJOaRAACgYKAbESARUSFQHGX2MiEzCV1hQhCHSxaoVjIVqZiRoVAUF8yKqUTZbTGJWikQaItL79ByEy0076
.youtube.com\tTRUE\t/\tTRUE\t1810491796\t__Secure-1PSID\tg.a0008wiNnftFjp7CxCTOruk65w3U7fKKyM0TUHkf0O2bU1Ir0vDKPriIE4uaG7Rd7F8uqpvEjgACgYKAeYSARUSFQHGX2MiBrwVghmP_vE75sJ2sr3OoBoVAUF8yKqcJbQPTTdqluH66BoSs5SM0076
.youtube.com\tTRUE\t/\tTRUE\t1810491796\t__Secure-3PSID\tg.a0008wiNnftFjp7CxCTOruk65w3U7fKKyM0TUHkf0O2bU1Ir0vDKAFjUeGddQWqtjVSBe1y-iwACgYKAdcSARUSFQHGX2MiiEEKB6_kRs4pcyvteLVdCxoVAUF8yKoYCZcdlImIKfrByQs-GAwo0076
.youtube.com\tTRUE\t/\tFALSE\t1807467799\tSIDCC\tAKEyXzWWG3DqQcUgx4IFcCJfhE2Bz_zvEiH9MYdCarBzUTE85IcJTDVMs4kFaVdBbQwCaQpj
.youtube.com\tTRUE\t/\tTRUE\t1807467799\t__Secure-1PSIDCC\tAKEyXzXfip5TX-II63-D5yeCPOxWluMqP5HnezitEr7rxbStb0X9CcOv0abPYx1j6rfW9Bfg
.youtube.com\tTRUE\t/\tTRUE\t1807467799\t__Secure-3PSIDCC\tAKEyXzXStuCfwgwvGH6CdzOmER4_Jab5BVwHpMCvw9Eu_x0LE6Uzv6wxLQy7wEvPxEsoAhAW
.youtube.com\tTRUE\t/\tTRUE\t1775932401\tCONSISTENCY\tAH5K9rbkocTODTtzvY3qPUkOBCjfLr7bSFmzJ11hZaE_b4wCXcQ0hWQf9wysJLSqZjpgL2PlM7OuNbfsV0dCa4Z2n_7hVZ7Pttnix3-00_TWJuokI7tSIHWPEm0
.youtube.com\tTRUE\t/\tTRUE\t1791483796\tVISITOR_INFO1_LIVE\tGf6X-O2rY0k
.youtube.com\tTRUE\t/\tTRUE\t1791483796\tVISITOR_PRIVACY_METADATA\tCgJNWBIEGgAgQw%3D%3D
.youtube.com\tTRUE\t/\tTRUE\t0\tYSC\tAqjp37qTh1k
.youtube.com\tTRUE\t/\tTRUE\t1791482342\t__Secure-ROLLOUT_TOKEN\tCNOD2MawkaSS8QEQoYLjwoXJkwMYi5WxibDmkwM%3D
"""


# =============================================
# FUNCIONES DE COOKIES
# =============================================
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


# Escribir archivos de cookies al importar este módulo
TWITTER_COOKIES_FILE = _escribir_cookies(TWITTER_COOKIES_RAW.strip(), 'twitter_cookies.txt')
YOUTUBE_COOKIES_FILE = _escribir_cookies(YOUTUBE_COOKIES_RAW.strip(), 'youtube_cookies.txt')
IG_COOKIES_FILE = _escribir_cookies(IG_COOKIES_RAW.strip(), 'instagram_cookies.txt') if IG_COOKIES_RAW.strip() else None


# =============================================
# CONFIGURACIÓN DE yt-dlp
# =============================================
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
    'fragment_retries': 5,
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

# Opciones específicas para Instagram (NSFW requiere autenticación)
YDL_OPTS_INSTAGRAM = {
    **YDL_OPTS,
    # Acepta video O imagen: un carrusel solo de fotos no puede usar best[ext=mp4]
    'format': 'best/bestvideo+bestaudio',
}
if IG_COOKIES_FILE:
    YDL_OPTS_INSTAGRAM['cookiefile'] = IG_COOKIES_FILE

# Opciones específicas para TikTok (acepta videos + imágenes de carrusel)
YDL_OPTS_TIKTOK = {
    **YDL_OPTS,
    'format': 'best/bestvideo+bestaudio',
    'extractor_args': {
        'tiktok': {
            'api_hostname': 'api22-normal-c-useast2a.tiktokv.com',
            'app_version': '34.5.4',
        }
    },
}


# =============================================
# INSTALOADER
# =============================================
IL = instaloader.Instaloader(
    download_videos=True,
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    post_metadata_txt_pattern='',
)

# Login de instaloader si hay credenciales de Instagram
if IG_USERNAME and IG_PASSWORD:
    try:
        IL.login(IG_USERNAME, IG_PASSWORD)
        print(f"✅ Instagram: instaloader logueado como {IG_USERNAME}")
    except Exception as e:
        print(f"⚠️ Instagram: no se pudo hacer login con instaloader: {e}")
        print("  → Se usarán cookies para yt-dlp como fallback")


def _cookies_instagram_dict():
    """Parsea IG_COOKIES (formato Netscape) a un dict {nombre: valor}."""
    cookies = {}
    for linea in IG_COOKIES_RAW.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith('#'):
            continue
        partes = linea.split('\t')
        if len(partes) >= 7 and 'instagram' in partes[0]:
            cookies[partes[5]] = partes[6]
    return cookies


def _cargar_sesion_instaloader_desde_cookies():
    """Carga la sesión de instaloader desde IG_COOKIES cuando no hay login por
    usuario/contraseña. Sin sesión, Instagram responde 401 a instaloader y los
    carruseles fallan aunque las cookies sí estén configuradas (solo las usaba
    yt-dlp, que no soporta carruseles de imágenes)."""
    if IL.context.is_logged_in or not IG_COOKIES_RAW.strip():
        return

    cookies = _cookies_instagram_dict()

    if 'sessionid' not in cookies:
        print("⚠️ Instagram: IG_COOKIES no contiene 'sessionid'; instaloader seguirá anónimo")
        return

    try:
        IL.context._session.cookies.update(cookies)
        username = IL.test_login()
        if username:
            IL.context.username = username
            print(f"✅ Instagram: sesión de instaloader cargada desde cookies como {username}")
        else:
            print("⚠️ Instagram: el sessionid de IG_COOKIES expiró o no es válido para instaloader")
    except Exception as e:
        print(f"⚠️ Instagram: no se pudo cargar la sesión desde cookies: {e}")


_cargar_sesion_instaloader_desde_cookies()


# =============================================
# FUNCIONES DE DESCARGA
# =============================================
def extraer_shortcode(url):
    """Extrae el shortcode de una URL de Instagram."""
    match = re.search(r'instagram\.com/(?:p|reel|reels)/([A-Za-z0-9_-]+)', url)
    return match.group(1) if match else None


# =============================================
# INSTAGRAM VÍA API WEB (primario si hay cookies)
# =============================================
IG_APP_ID = '936619743392459'
IG_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
         '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
_SHORTCODE_ALFABETO = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'


def _shortcode_a_pk(shortcode):
    """Convierte un shortcode de Instagram a su pk numérico (base 64).
    En shortcodes largos (posts privados) solo los primeros 11 chars codifican el pk."""
    pk = 0
    for c in shortcode[:11]:
        pk = pk * 64 + _SHORTCODE_ALFABETO.index(c)
    return pk


def descargar_instagram_api(url):
    """Descarga un post/carrusel de Instagram con la API web oficial usando
    las cookies de IG_COOKIES. Es el mismo endpoint que usa yt-dlp cuando hay
    sessionid, pero a diferencia de yt-dlp también descarga las FOTOS de los
    carruseles (yt-dlp solo extrae videos).

    Retorna (archivos, error_code) igual que descargar_instagram()."""
    shortcode = extraer_shortcode(url)
    if not shortcode:
        return [], "bad_url"

    cookies = _cookies_instagram_dict()
    if 'sessionid' not in cookies:
        return [], "no_cookies"

    headers = {
        'User-Agent': IG_UA,
        'Accept': '*/*',
        'X-IG-App-ID': IG_APP_ID,
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': 'https://www.instagram.com/',
    }

    pk = _shortcode_a_pk(shortcode)
    print(f"📸 API web de Instagram: shortcode={shortcode} pk={pk}")

    try:
        r = requests.get(
            f'https://i.instagram.com/api/v1/media/{pk}/info/',
            headers=headers, cookies=cookies, timeout=30,
        )
    except Exception as e:
        print(f"❌ API IG: error de conexión: {e}")
        return [], f"other: {str(e)[:100]}"

    if r.status_code in (401, 403):
        print(f"❌ API IG: HTTP {r.status_code} (sessionid expirado o sin acceso)")
        return [], "login_required"
    if r.status_code == 404:
        return [], "not_found"
    if r.status_code != 200:
        print(f"❌ API IG: HTTP {r.status_code}: {r.text[:150]}")
        return [], f"other: http {r.status_code}"

    try:
        items = r.json().get('items') or []
    except ValueError:
        print(f"❌ API IG: respuesta no es JSON: {r.text[:150]}")
        return [], "other: respuesta invalida"
    if not items:
        return [], "empty"

    item = items[0]
    # Carrusel → lista de medias; post simple → el item mismo
    medias = item.get('carousel_media') or [item]
    print(f"📸 API IG: {len(medias)} media(s) en el post")

    os.makedirs('downloads', exist_ok=True)
    archivos = []
    for i, media in enumerate(medias, 1):
        videos = media.get('video_versions') or []
        if videos:
            media_url, ext = videos[0].get('url'), 'mp4'
        else:
            candidatos = (media.get('image_versions2') or {}).get('candidates') or []
            if not candidatos:
                continue
            # candidates viene ordenado de mayor a menor resolución
            media_url, ext = candidatos[0].get('url'), 'jpg'
        if not media_url:
            continue

        destino = os.path.join('downloads', f'ig_{shortcode}_{i}.{ext}')
        try:
            with requests.get(media_url, headers={'User-Agent': IG_UA},
                              stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with open(destino, 'wb') as f:
                    for chunk in resp.iter_content(256 * 1024):
                        f.write(chunk)
            archivos.append(destino)
            print(f"  ✅ {destino}")
        except Exception as e:
            print(f"  ❌ No se pudo bajar media {i}: {e}")

    if archivos:
        return archivos, None
    return [], "empty"


def descargar_instagram(url):
    """Descarga un post de Instagram usando instaloader (videos e imágenes de carrusel).

    Retorna (archivos, error_code):
      - (lista, None) en éxito (lista vacía = no se descargó nada, error_code='empty')
      - ([], 'bad_url') si la URL no tiene shortcode
      - ([], 'login_required') si el post es privado y no hay login
      - ([], 'not_found') si el post no existe
      - ([], 'other: <msg>') en otros errores
    """
    shortcode = extraer_shortcode(url)
    if not shortcode:
        print(f"❌ No se pudo extraer shortcode de: {url}")
        return [], "bad_url"

    print(f"📸 Usando instaloader para shortcode: {shortcode}")
    print(f"📸 Login instaloader activo: {IL.context.is_logged_in}")

    carpeta_temp = "downloads/ig_temp"
    if os.path.exists(carpeta_temp):
        shutil.rmtree(carpeta_temp)
    os.makedirs(carpeta_temp, exist_ok=True)

    try:
        post = instaloader.Post.from_shortcode(IL.context, shortcode)
        print(f"📸 Post encontrado: is_video={post.is_video}, mediacount={post.mediacount}")
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

        try:
            shutil.rmtree(carpeta_temp)
        except:
            pass

        if archivos:
            print(f"📸 Instaloader descargó {len(archivos)} archivos")
            return archivos, None
        else:
            print(f"❌ Instaloader no descargó archivos del post")
            return [], "empty"
    except instaloader.exceptions.InstaloaderException as e:
        error_str = str(e).lower()
        if 'login' in error_str or 'private' in error_str:
            print(f"❌ Instagram requiere login o es privado: {e}")
            err_code = "login_required"
        elif 'not found' in error_str or '404' in error_str:
            print(f"❌ Post de Instagram no encontrado: {e}")
            err_code = "not_found"
        else:
            print(f"❌ Error de instaloader: {e}")
            err_code = f"other: {str(e)[:100]}"
        try:
            shutil.rmtree(carpeta_temp)
        except:
            pass
        return [], err_code
    except Exception as e:
        print(f"❌ Error inesperado con instaloader: {type(e).__name__}: {e}")
        try:
            shutil.rmtree(carpeta_temp)
        except:
            pass
        return [], f"other: {type(e).__name__}: {str(e)[:100]}"


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
    """Descarga media con yt-dlp. Para Instagram usa instaloader como primario."""
    os.makedirs('downloads', exist_ok=True)

    # Limpiar URL antes de pasarla a yt-dlp
    url = limpiar_url(url)
    plataforma = detectar_plataforma(url)

    print(f"🔗 Plataforma detectada: {plataforma}")

    # Instagram: 1) API web con cookies (baja fotos Y videos de carruseles),
    # 2) instaloader, 3) yt-dlp (solo videos)
    if plataforma == 'instagram':
        print("📸 Instagram: API web con cookies (primario)...")
        archivos_api, err_api = descargar_instagram_api(url)
        if archivos_api:
            return None, archivos_api, None
        # OJO: la API devuelve 404 también cuando el sessionid expiró, así que
        # su "not_found" no es confiable → siempre probar los fallbacks
        print(f"⚠️ API web no pudo ({err_api}), intentando instaloader...")

        archivos_inst, err_inst = descargar_instagram(url)
        if archivos_inst:
            return None, archivos_inst, None
        if err_inst == "not_found":
            return None, [], "Instagram: post no encontrado o eliminado"
        # Cualquier otro fallo → fallback a yt-dlp (que sí tiene cookies vía cookiefile)
        print(f"⚠️ instaloader no pudo ({err_inst}), fallback a yt-dlp con cookies...")

    # Usar opciones específicas por plataforma
    if plataforma == 'twitter':
        opciones = YDL_OPTS_TWITTER
    elif plataforma == 'instagram':
        opciones = YDL_OPTS_INSTAGRAM
    elif plataforma == 'tiktok':
        opciones = YDL_OPTS_TIKTOK
    else:
        opciones = YDL_OPTS

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

            # Instagram: si yt-dlp falló por restricción y NO hay login instaloader,
            # reintentar una vez con force_generic=True
            if (plataforma == 'instagram'
                and 'not available to everyone' in error_str
                and not IL.context.is_logged_in
                and intento < max_reintentos):
                print("⚠️ Reintentando yt-dlp con force_generic=True (sin login instaloader)...")
                opciones = {
                    **opciones,
                    'extractor_args': {'instagram': {'force_generic': True}},
                }
                continue

            # No reintentar para otros errores de descarga
            return None, [], str(e)

        except (TimeoutError, ConnectionError, OSError,
                urllib3.exceptions.TimeoutError,
                urllib3.exceptions.ProtocolError,
                urllib3.exceptions.ReadTimeoutError,
                urllib3.exceptions.ConnectTimeoutError) as e:
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


# URL de un reel público conocida para testear credenciales sin riesgo
IG_TEST_URL = "https://www.instagram.com/reel/C8aRs6CJvSD/"
