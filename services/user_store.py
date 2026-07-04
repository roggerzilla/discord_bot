"""
user_store.py - Persistencia de usuarios que aceptaron al Monkey.
Usa Supabase para que funcione en Render (filesystem efímero).
"""
from config import supabase

MONKEY_TABLE = "monkey_accepted_users"

# Cache local para no consultar Supabase en cada mensaje
_cache: set = set()
_cache_loaded: bool = False


def _load_cache():
    """Carga todos los user_ids aceptados de Supabase al cache local."""
    global _cache, _cache_loaded
    if _cache_loaded:
        return
    try:
        result = supabase.table(MONKEY_TABLE).select("user_id").execute()
        _cache = {row["user_id"] for row in result.data}
        _cache_loaded = True
        print(f"🐵 Cache de aceptaciones cargado: {len(_cache)} usuarios")
    except Exception as e:
        print(f"⚠️ Error cargando cache de monkey_accepted_users: {e}")
        print("   → Asegúrate de crear la tabla 'monkey_accepted_users' en Supabase")
        _cache_loaded = True  # No reintentar en cada mensaje


def has_accepted(user_id: int) -> bool:
    """Verifica si un usuario ya aceptó al Monkey."""
    _load_cache()
    return user_id in _cache


def mark_accepted(user_id: int) -> None:
    """Marca a un usuario como que aceptó al Monkey."""
    _load_cache()
    try:
        supabase.table(MONKEY_TABLE).upsert({
            "user_id": user_id
        }).execute()
        _cache.add(user_id)
        print(f"🐵 Usuario {user_id} aceptó al Monkey")
    except Exception as e:
        print(f"⚠️ Error guardando aceptación de {user_id}: {e}")
        # Aun así lo agregamos al cache para no bloquear al usuario
        _cache.add(user_id)


def remove_accepted(user_id: int) -> None:
    """Elimina la aceptación de un usuario (para /monkeyperdon)."""
    _load_cache()
    try:
        supabase.table(MONKEY_TABLE).delete().eq("user_id", user_id).execute()
        _cache.discard(user_id)
        print(f"🐵 Usuario {user_id} reseteó su aceptación")
    except Exception as e:
        print(f"⚠️ Error eliminando aceptación de {user_id}: {e}")
        _cache.discard(user_id)
