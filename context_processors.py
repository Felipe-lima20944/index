from django.conf import settings
from .models import Plan


def plan_values(request):
    """Disponibiliza valores do plano para todos os templates.

    Busca o plano ativo no banco; se não existir, usa uma cópia básica dos
    valores definidos em settings (para mitigar caso a tabela esteja vazia).
    """
    plan = None
    try:
        plan = Plan.objects.filter(is_active=True).order_by('-created_at').first()
    except Exception:
        plan = None

    if plan:
        return {
            'PLAN_ID': plan.slug,
            'PLAN_NAME': plan.name,
            'PLAN_PRICE': str(plan.price),
            'PLAN_LABEL': f"R$ {plan.price}/mês",
            'PLAN_DURATION_DAYS': plan.duration_days,
            'PLAN_OBJ': plan,
        }

    # fallback to settings values if no plan found
    return {
        'PLAN_ID': settings.PLAN_ID,
        'PLAN_NAME': settings.PLAN_NAME,
        'PLAN_PRICE': settings.PLAN_PRICE,
        'PLAN_LABEL': settings.PLAN_LABEL,
        'PLAN_DURATION_DAYS': settings.PLAN_DURATION_DAYS,
    }
