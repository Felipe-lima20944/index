from django.urls import path
from . import views

urlpatterns = [
    # Rotas de visualização da sua aplicação
    path('', views.ChatView.as_view(), name='chat_list_or_new'),
    path('conversa/<uuid:conversa_id>/', views.ConversaDetailView.as_view(), name='conversa_detail'),
    path('chat/<uuid:conversa_id>/', views.ChatView.as_view(), name='carregar_conversa'),
    path('home/', views.home_page, name='home_page'),
    path('termos/', views.termos, name='termos'),
    path('recursos/', views.recursos, name='recursos'),

    # Rotas de API da sua aplicação
    path('api/chat/stream/', views.StreamingChatView.as_view(), name='streaming_chat'),
    path('api/conversas/', views.listar_conversas, name='listar_conversas'),
    path('api/conversa/<uuid:conversa_id>/', views.carregar_conversa, name='carregar_conversa_api'),
    path('api/conversa/<uuid:conversa_id>/limpar/', views.limpar_conversa_api, name='limpar_conversa_api'),
    path('api/conversa/<uuid:conversa_id>/cancelar/', views.cancelar_conversa_api, name='cancelar_conversa_api'),
    path('api/conversa/excluir/', views.excluir_conversa_api, name='excluir_conversa_api'),
    path('api/conversa/<uuid:conversa_id>/restaurar/', views.restaurar_conversa_api, name='restaurar_conversa_api'),
    path('api/conversas/excluidas/', views.listar_conversas_excluidas_api, name='listar_conversas_excluidas_api'),
    path('api/limpar/', views.limpar_conversas, name='limpar_conversas'),
    path('api/cancelar/', views.cancelar_resposta, name='cancelar_resposta'),
    path('api/personalidades/', views.listar_personalidades, name='listar_personalidades'),
    path('api/status/', views.status_servico, name='status_servico'),
    path('api/feedback/<uuid:mensagem_id>/', views.enviar_feedback, name='enviar_feedback'),
    path('api/mensagem/<uuid:mensagem_id>/excluir/', views.excluir_mensagem_api, name='excluir_mensagem_api'),
    path('api/mensagem/<uuid:mensagem_id>/editar/', views.editar_mensagem_api, name='editar_mensagem_api'),
    path('api/chat/reprocessar/', views.reprocessar_conversa_api, name='reprocessar_conversa_api'),

    # Rota para ramificar conversa a partir de uma mensagem
    path('api/conversa/<uuid:conversa_id>/ramificar/<uuid:mensagem_id>/', views.ramificar_conversa_api, name='ramificar_conversa_api'),

    # --- ROTAS DE COMPARTILHAMENTO CORRIGIDAS ---
    # Rota da API para o frontend solicitar o link de compartilhamento.
    path('api/chat/compartilhar/<uuid:conversa_id>/', views.ativar_compartilhamento, name='ativar_compartilhamento'),

    # Rota pública para visualização de uma conversa.
    path('compartilhar/<uuid:uuid_compartilhamento>/', views.visualizar_conversa_compartilhada, name='visualizar_conversa_compartilhada'),

    # --- NOVAS ROTAS PARA FUNCIONALIDADES AVANÇADAS ---
    path('api/mensagem/<uuid:mensagem_id>/reacao/', views.adicionar_reacao, name='adicionar_reacao'),
    path('api/mensagem/<uuid:mensagem_id>/sinalizar/', views.sinalizar_mensagem, name='sinalizar_mensagem'),
    path('api/preferencias/', views.obter_preferencias_usuario, name='obter_preferencias'),
    path('api/preferencias/atualizar/', views.atualizar_preferencias_usuario, name='atualizar_preferencias'),
    path('api/conversa/<uuid:conversa_id>/metadata/', views.atualizar_conversa_metadata, name='atualizar_conversa_metadata'),
    path('api/conversa/<uuid:conversa_id>/titulo/', views.atualizar_titulo_conversa, name='atualizar_titulo_conversa'),
    path('api/conversa/<uuid:conversa_id>/analytics/', views.obter_analytics_conversa, name='obter_analytics_conversa'),
]

# Handlers para páginas de erro
handler404 = 'chat.views.handler404'
handler500 = 'chat.views.handler500'
