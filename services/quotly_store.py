"""
quotly_store.py - Registro de packs de stickers por usuario (bot Quotly/Monkey).

Reemplaza al data/packs.json del bot en Node. Usa Supabase para persistir en el
filesystem efímero de Render. Tabla: quotly_packs (ver quotly_packs_table.sql).

Cada fila: name (PK, nombre único del set), user_id, title, format, steal, count.
"""
from config import supabase

TABLE = "quotly_packs"


def get_packs(user_id, fmt=None):
    """Lista de packs del usuario (opcionalmente filtrada por formato)."""
    try:
        res = supabase.table(TABLE).select("*").eq("user_id", int(user_id)).execute()
        packs = res.data or []
    except Exception as e:
        print(f"⚠️ quotly_store.get_packs error: {e}")
        return []
    if fmt:
        packs = [p for p in packs if (p.get("format") or "static") == fmt]
    return packs


def add_pack(user_id, name, title, fmt="static", steal=False, count=1):
    """Registra un pack (idempotente por name)."""
    try:
        existing = supabase.table(TABLE).select("name").eq("name", name).execute()
        if existing.data:
            return
        supabase.table(TABLE).insert({
            "name": name,
            "user_id": int(user_id),
            "title": title,
            "format": fmt,
            "steal": bool(steal),
            "count": int(count),
        }).execute()
    except Exception as e:
        print(f"⚠️ quotly_store.add_pack error: {e}")


def increment_count(name, by=1):
    """Suma al contador de stickers de un pack."""
    try:
        res = supabase.table(TABLE).select("count").eq("name", name).execute()
        if not res.data:
            return
        current = res.data[0].get("count") or 0
        supabase.table(TABLE).update({"count": current + by}).eq("name", name).execute()
    except Exception as e:
        print(f"⚠️ quotly_store.increment_count error: {e}")


def update_title(name, title):
    """Cambia el título visible registrado de un pack."""
    try:
        supabase.table(TABLE).update({"title": title}).eq("name", name).execute()
    except Exception as e:
        print(f"⚠️ quotly_store.update_title error: {e}")
