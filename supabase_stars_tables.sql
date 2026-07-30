-- ============================================================
-- Tablas para el cobro con Telegram Stars.
-- Ejecutar en el SQL Editor de Supabase antes de usar el bot de Stars.
-- ============================================================

-- Suscripciones activas: vincula al pagador de Telegram con su cuenta de Discord.
create table if not exists telegram_star_subs (
    telegram_user_id             bigint primary key,
    discord_user_id              text,
    tier                         text,
    telegram_payment_charge_id   text,
    subscription_expiration_date timestamptz,
    status                       text default 'active',   -- active | canceled | expired
    is_recurring                 boolean default false,
    updated_at                   timestamptz default now()
);

create index if not exists idx_telegram_star_subs_discord on telegram_star_subs (discord_user_id);
create index if not exists idx_telegram_star_subs_status  on telegram_star_subs (status);

-- Códigos de vinculación temporales (Discord genera el código, Telegram lo canjea).
create table if not exists telegram_link_codes (
    code             text primary key,
    discord_user_id  text not null,
    created_at       timestamptz default now()
);
