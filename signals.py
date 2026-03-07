"""
📧 SIGNALS - Disparo Automático de Emails (Registro, Login, Logout, Planos, Reset Senha)

✅ Eventos cobertos:
1. Novo Usuário (post_save) → Email de boas-vindas / atribuições de trial
2. Login (manual) → Email de alerta com {horário}
3. Logout (manual) → Email de confirmação
4. Reset Senha (manual) → Email com link
5. Assinatura/Plano (post_save) → envio automático quando o plano é criado,
   renovado, alterado ou cancelado, além de avisos de falha de pagamento.

Use em apps.py:
    def ready(self):
        import core.signals  # ✅ Já feito!
"""

from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone
from datetime import datetime
import logging

User = get_user_model()
logger = logging.getLogger('beatflow')


# helper usado pelos templates de assinatura

def _plan_display(plano_slug: str) -> str:
    """Converte slug/id de plano em texto amigável.

    Primeiro tenta buscar o objeto Plan pelo slug/plano_id e retornar o
    seu atributo <code>name</code>. Se estiver faltando ou ocorrer erro, cai
    no comportamento antigo de simplesmente trocar underlines por espaços e
    aplicar title case.
    """
    if not plano_slug:
        return 'Plano'
    try:
        from core.models import Plan
        plan = Plan.objects.filter(slug=plano_slug).first()
        if plan and plan.name:
            return plan.name
    except Exception:
        # se algo falhar (por ex. import circular durante init) ignore
        pass
    return plano_slug.replace('_', ' ').title()


# ================================================================
# TEMPLATES HTML VIA DISPATCH
# ================================================================

def get_email_template(tipo, **kwargs):
    """Carrega HTML a partir de templates em core/templates/core/emails.

    `tipo` deve corresponder ao nome do arquivo sem extensão.
    Contexto adicional (nome, link, horario, plano, amount, etc.) é passado
    via kwargs e disponibilizado no template.
    """
    # converter slug de plano automaticamente
    if 'plano' in kwargs:
        kwargs['plano'] = _plan_display(kwargs['plano'])
    try:
        return render_to_string(f'core/emails/{tipo}.html', kwargs)
    except Exception as e:
        logger.error(f'erro ao carregar template "{tipo}": {e}')
        return ''


def send_email(assunto, html_content, email_to):
    """Envia email HTML com logging completo"""
    try:
        email = EmailMultiAlternatives(
            subject=assunto,
            body=strip_tags(html_content),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email_to] if isinstance(email_to, str) else email_to
        )
        email.attach_alternative(html_content, "text/html")
        result = email.send()
        
        msg = f"✅ Email: {assunto} → {email_to}"
        logger.info(msg)
        # também exibimos no stdout para depuração rápida
        # mas o registro principal já está em `beatflow`
        # print(msg) -- desativado para evitar duplicação
        return result > 0
    except Exception as e:
        msg = f"❌ ERRO ao enviar: {assunto} → {email_to} | {str(e)}"
        logger.error(msg)
        # print(msg)  # já logado
        return False


# ================================================================
# SIGNALS - DISPARO AUTOMÁTICO
# ================================================================

# conectar login/logout do Django para disparar emails automaticamente
from django.contrib.auth.signals import user_logged_in, user_logged_out

@receiver(user_logged_in)
def _handle_user_logged_in(sender, request, user, **kwargs):
    """Disparo automático ao detectar login bem‑sucedido."""
    if user and user.email:
        msg = f"🔐 sinal user_logged_in para {user.username} ({user.email})"
        logger.info(msg)
        print(msg)  # deixar visível no console de desenvolvimento
        trigger_login_alert_email(user)

@receiver(user_logged_out)
def _handle_user_logged_out(sender, request, user, **kwargs):
    """Disparo automático ao detectar logout."""
    if user and user.email:
        msg = f"👋 sinal user_logged_out para {user.username} ({user.email})"
        logger.info(msg)
        print(msg)
        trigger_logout_email(user)


@receiver(post_save, sender=User)
def user_created_signal(sender, instance, created, **kwargs):
    """Dispara quando novo usuário é criado
    Envia email de boas-vindas
    """
    if created and instance.email:
        try:
            nome = instance.first_name or instance.username
            msg = f"🆕 NOVO USUÁRIO: {instance.username} ({instance.email})"
            logger.info(msg)
            # print(msg)  # log já cuida da saída
            html = get_email_template('welcome', nome=nome)
            send_email(
                f'Bem-vindo ao BeatFlow {nome}!',
                html,
                instance.email
            )
        except Exception as e:
            msg = f"❌ ERRO ao enviar email de boas-vindas: {str(e)}"
            logger.error(msg)
            # print(msg)  # gravado no logger


def trigger_password_reset_email(user, reset_link):
    """Dispara email de reset de senha usando template"""
    try:
        nome = user.first_name or user.username
        logger.info(f"🔔 Disparando email de reset de senha para {user.email}...")
        html = get_email_template('password_reset', nome=nome, link=reset_link)
        send_email(
            f'Recupere sua Senha - BeatFlow',
            html,
            user.email
        )
    except Exception as e:
        logger.error(f"❌ Erro ao disparar email de reset de senha: {str(e)}")


def trigger_login_alert_email(user):
    """Alerta de novo login"""
    try:
        nome = user.first_name or user.username
        horario = datetime.now().strftime('%d/%m/%Y às %H:%M')
        logger.info(f"🔔 Disparando email de login para {user.email}...")
        html = get_email_template('login_alert', nome=nome, horario=horario)
        send_email(
            f'Novo Acesso - BeatFlow',
            html,
            user.email
        )
    except Exception as e:
        logger.error(f"❌ Erro ao disparar email de login: {str(e)}")


def trigger_logout_email(user):
    """Confirmação de logout"""
    try:
        nome = user.first_name or user.username
        logger.info(f"🔔 Disparando email de logout para {user.email}...")
        html = get_email_template('logout', nome=nome)
        send_email(
            f'Até Logo - BeatFlow',
            html,
            user.email
        )
    except Exception as e:
        logger.error(f"❌ Erro ao disparar email de logout: {str(e)}")


# ================================================================
# SIGNALS PARA ASSINATURAS / PLANOS ASAAS
# ================================================================

from core.models import Subscription, Payment

@receiver(pre_save, sender=Subscription)
def _track_subscription_changes(sender, instance, **kwargs):
    """Guarda valores antigos em atributos temporários para compararmos
    após o save. Isso nos permite detectar mudanças de status ou de plano.
    """
    if instance.pk:
        try:
            old = Subscription.objects.get(pk=instance.pk)
            instance._old_status = old.status
            # store the plano_id field (formerly plano_slug)
            instance._old_plan = getattr(old, 'plano_id', None)
        except Subscription.DoesNotExist:
            instance._old_status = None
            instance._old_plan = None


@receiver(pre_save, sender=Payment)
def _track_payment_changes(sender, instance, **kwargs):
    """Armazena status antigo do pagamento para detectar alterações."""
    if instance.pk:
        try:
            old = Payment.objects.get(pk=instance.pk)
            instance._old_status = old.status
        except Payment.DoesNotExist:
            instance._old_status = None


@receiver(post_save, sender=Subscription)
def subscription_saved_email(sender, instance, created, **kwargs):
    """Envia emails automáticos quando uma assinatura é criada ou seu
    status/ plano muda.
    """
    user = instance.usuario
    if not user.email:
        return

    nome = user.first_name or user.username
    # ------------------------------------------------------------------
    # NOVA ASSINATURA
    if created:
        html = get_email_template('subscription_started', nome=nome, plano=instance.plano_id)
        send_email('Assinatura ativada - BeatFlow', html, user.email)
        # também enviar cobrança PIX se houver uma pendente recente
        pending = Payment.objects.filter(usuario=user, method='PIX', status='pending').order_by('-criado_em').first()
        if pending:
            desc = ''
            if isinstance(pending.raw, dict):
                desc = pending.raw.get('description', '')
            # usar plano_id como fallback
            desc = desc or instance.plano_id
            venc = ''
            if pending.vencimento:
                try:
                    venc = pending.vencimento.strftime('%d/%m/%Y')
                except Exception:
                    venc = str(pending.vencimento)
            html2 = get_email_template(
                'payment_request',
                nome=nome,
                amount=str(pending.amount),
                description=desc,
                pix_payload=pending.pix_qr_payload,
                pix_qr=pending.pix_qr_image,
                vencimento=venc,
            )
            send_email(f'Cobrança gerada – {desc or "BeatFlow"}', html2, user.email)
        return

    # ------------------------------------------------------------------
    # ALTERAÇÃO DE STATUS/PLANO
    old_status = getattr(instance, '_old_status', None)
    old_plan = getattr(instance, '_old_plan', None)

    if old_status != instance.status:
        if instance.status == 'active':
            html = get_email_template('subscription_started', nome=nome, plano=instance.plano_id)
            send_email('Plano renovado - BeatFlow', html, user.email)
        elif instance.status == 'cancelled':
            html = get_email_template('subscription_cancelled', nome=nome, plano=instance.plano_id)
            send_email('Assinatura cancelada - BeatFlow', html, user.email)
        elif instance.status in ('past_due', 'failed'):
            html = get_email_template('payment_issue', nome=nome, plano=instance.plano_id, status=instance.status)
            send_email('Problema com pagamento - BeatFlow', html, user.email)

    # caso o plano tenha mudado de slug e a assinatura esteja ativa
    if old_plan and old_plan != instance.plano_id and instance.status == 'active':
        html = get_email_template('subscription_started', nome=nome, plano=instance.plano_id)
        send_email('Plano alterado - BeatFlow', html, user.email)


# ----------------------------------------------------------------
# pagamento PIX - notificar usuário com dados na criação
# ----------------------------------------------------------------

@receiver(post_save, sender=Payment)
def payment_created_email(sender, instance, created, **kwargs):
    """Envia email de cobrança PIX ao criar o pagamento no banco local.

    O template inclui plano/descrição, valor, vencimento, código PIX (copiar/
    colar) e imagem do QR code se disponíveis. Chamamos apenas para status
    pendente e método PIX, que é o caso normal no fluxo de criação.
    """
    # novo pagamento pendente -> enviar instruções PIX
    if created and instance.method.upper() == 'PIX' and instance.status == 'pending':
        user = instance.usuario
        if not user or not user.email:
            return
        nome = user.first_name or user.username
        # tentar extrair descrição/planos
        desc = ''
        if isinstance(instance.raw, dict):
            desc = instance.raw.get('description', '')

        venc = ''
        if instance.vencimento:
            try:
                venc = instance.vencimento.strftime('%d/%m/%Y')
            except Exception:
                venc = str(instance.vencimento)

        html = get_email_template(
            'payment_request',
            nome=nome,
            amount=str(instance.amount),
            description=desc,
            pix_payload=instance.pix_qr_payload,
            pix_qr=instance.pix_qr_image,
            vencimento=venc,
        )
        subject = f'💰 Cobrança gerada – {desc or "BeatFlow"}'
        send_email(subject, html, user.email)
        return

    # alteração de status (não criação)
    old_status = getattr(instance, '_old_status', None)
    if old_status != instance.status and instance.status == 'paid':
        user = instance.usuario
        if not user or not user.email:
            return
        nome = user.first_name or user.username
        desc = ''
        if isinstance(instance.raw, dict):
            desc = instance.raw.get('description', '')
        html = get_email_template(
            'payment_received',
            nome=nome,
            amount=str(instance.amount),
            description=desc,
        )
        send_email(f'Pagamento recebido – {desc or "BeatFlow"}', html, user.email)
        return

