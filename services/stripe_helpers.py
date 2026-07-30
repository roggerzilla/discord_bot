"""
stripe_helpers.py - Funciones auxiliares para Stripe (asíncronas).

⚠️ DEPRECADO: estamos migrando el cobro a Telegram Stars
(ver services/telegram_stars_helpers.py). Este módulo y todas sus llamadas se
eliminarán una vez validado el nuevo flujo de suscripciones con Stars.
"""
import asyncio
import stripe

from config import TIER_MAPPING, TIER_3_ROLE_ID, DEFAULT_ROLE_ID


async def get_customer_subscription_data(customer_id: str):
    """Obtiene el estado y producto de la suscripción de un cliente de Stripe.
    Usa asyncio.to_thread para no bloquear el event loop."""
    def _blocking_stripe_call():
        try:
            active = stripe.Subscription.list(customer=customer_id, status='active', limit=1, expand=['data.plan.product'])
            if active.data:
                return "active", active.data[0].plan.product
            trial = stripe.Subscription.list(customer=customer_id, status='trialing', limit=1, expand=['data.plan.product'])
            if trial.data:
                return "trialing", trial.data[0].plan.product
            past = stripe.Subscription.list(customer=customer_id, status='past_due', limit=1, expand=['data.plan.product'])
            if past.data:
                return "past_due", past.data[0].plan.product
            return "canceled", None
        except Exception as e:
            print(f"🚨 Stripe Error {customer_id}: {e}")
            return None, None
    return await asyncio.to_thread(_blocking_stripe_call)


def calculate_roles_to_assign(product_obj):
    """Calcula qué roles de Discord asignar basado en el producto de Stripe."""
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
