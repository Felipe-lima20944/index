from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        # importar módulo de signals (conecta handlers automaticamente)
        try:
            import core.signals  # noqa: F401
        except Exception:
            pass

        # conectar signal para mostrar message após login bem-sucedido
        try:
            from django.contrib.auth.signals import user_logged_in
            from django.contrib import messages

            def _on_user_logged_in(sender, request, user, **kwargs):
                try:
                    messages.success(request, 'Login efetuado com sucesso')
                except Exception:
                    pass

            user_logged_in.connect(_on_user_logged_in, dispatch_uid='core.user_logged_in')
        except Exception:
            # ambiente de testes/gestores que não carregam signals
            pass
