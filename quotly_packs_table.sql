-- Tabla del registro de packs del bot Quotly/Monkey (services/quotly_store.py).
-- Ejecutar en el SQL Editor de Supabase.

create table if not exists quotly_packs (
    name       text primary key,          -- nombre único del set de stickers
    user_id    bigint not null,           -- dueño (id de Telegram)
    title      text,                      -- título visible
    format     text default 'static',     -- static | video | animated
    steal      boolean default false,     -- pack automático de /monkey_steal
    count      integer default 1,         -- cantidad de stickers (para saber si se llenó)
    created_at timestamptz default now()
);

create index if not exists quotly_packs_user_idx on quotly_packs (user_id);
