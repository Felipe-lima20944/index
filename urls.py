# core/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

urlpatterns = [
    # ============================================================================
    # VIEWS HTML (RENDERIZAÇÃO)
    # ============================================================================
    path('', views.buscar_musicas_html, name='home'),
    path('buscar/', views.buscar_musicas_html, name='buscar_musicas'),
    path('register/', views.register, name='register'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/edit/', views.profile_edit, name='profile_edit'),
    path('profile/apps/', views.profile_apps, name='profile_apps'),
    path('assinatura/', views.assinatura, name='assinatura'),
    
    # ============================================================================
    # API DE INTEGRAÇÃO COM YOUTUBE MUSIC
    # ============================================================================
    path('api/search/', views.buscar_musicas_api, name='api_search'),
    path('api/recommendations/', views.recommendations_api, name='api_recommendations'),
    path('api/album/tracks/', views.album_tracks_api, name='api_album_tracks'),
    path('api/track/info/', views.track_info_api, name='api_track_info'),
    path('api/stream/url/', views.streaming_url_api, name='api_stream_url'),
    path('api/stream/download/', views.download_track_api, name='api_stream_download'),
    # Removed `/api/featured/` as it's no longer used
    
    # ============================================================================
    # API DE DADOS LOCAIS (BANCO DE DADOS)
    # ============================================================================
    path('api/artists/', views.artistas_list_api, name='api_artists_list'),
    path('api/artists/<int:artista_id>/', views.artista_detail_api, name='api_artist_detail'),
    path('api/albums/', views.albuns_list_api, name='api_albums_list'),
    path('api/albums/<int:album_id>/', views.album_detail_api, name='api_album_detail'),
    path('api/songs/', views.musicas_list_api, name='api_songs_list'),
    path('api/songs/<int:musica_id>/', views.musica_detail_api, name='api_song_detail'),
    path('api/songs/by-ids/', views.musicas_by_ids_api, name='api_songs_by_ids'),
    path('api/genres/', views.generos_list_api, name='api_genres_list'),
    path('api/genres/<slug:genero_slug>/', views.genero_detail_api, name='api_genre_detail'),
    path('api/banners/', views.banners_list_api, name='api_banners'),
    path('api/search-backgrounds/', views.search_backgrounds_list_api, name='api_search_backgrounds'),
    path('api/app-downloads/', views.app_downloads_api, name='api_app_downloads'),
    path('api/anonymous-promos/', views.anonymous_promos_list_api, name='api_anonymous_promos'),
    path('api/promos/', views.promos_list_api, name='api_promos'),
    path('api/plans/', views.plans_list_api, name='api_plans'),
    path('api/trending/', views.trending_list_api, name='api_trending'),
    
    # ============================================================================
    # API DE PLAYLISTS
    # ============================================================================
    path('api/playlists/', views.playlists_api, name='api_playlists'),
    path('api/playlists/<int:playlist_id>/', views.playlist_detail_api, name='api_playlist_detail'),
    path('api/playlists/<int:playlist_id>/add/', views.playlist_add_musica_api, name='api_playlist_add'),
    # novo endpoint para remoção por musica_id via corpo/param (recomendado para React)
    path('api/playlists/<int:playlist_id>/remove/', views.playlist_remove_musica_api, name='api_playlist_remove'),
    # alias de compatibilidade que também aceita `item_id` na URL
    path('api/playlists/<int:playlist_id>/remove/<int:item_id>/', views.playlist_remove_musica_api, name='api_playlist_remove_by_item'),
    path('api/playlists/<int:playlist_id>/reorder/', views.playlist_reorder_api, name='api_playlist_reorder'),
    # alias para excluir playlist usando rota legada com `/delete/` suffix
    path('api/playlists/<int:playlist_id>/delete/', views.playlist_detail_api, name='api_playlist_delete'),
    
    # ============================================================================
    # API DE FAVORITOS (caminho canônico, PT-BR alias abaixo)
    # ============================================================================
    path('api/favorites/', views.favoritos_api, name='api_favorites'),
    path('api/favorites/test/', views.favoritos_test, name='api_favorites_test'),
    # PT-BR alias (trailing slash only)
    path('api/favoritos/', views.favoritos_api, name='api_favoritos'),  # compatibilidade PT-BR

    
    # ============================================================================
    # API DE AVALIAÇÕES
    # ============================================================================
    path('api/ratings/', views.avaliacoes_api, name='api_ratings'),
    
    # ============================================================================
    # API DE PLAYBACK STATE
    # ============================================================================
    path('api/playback/state/', views.playback_state_api, name='api_playback_state'),
    path('api/playback/queue/', views.queue_api, name='api_playback_queue'),
    
    # ============================================================================
    # API DE ESTATÍSTICAS
    # ============================================================================
    path('api/stats/dashboard/', views.dashboard_stats_api, name='api_stats_dashboard'),
    
    # ============================================================================
    # ALIASES PARA COMPATIBILIDADE (nomes antigos)
    # ============================================================================
    path('api/buscar/', views.buscar_musicas, name='api_buscar'),
    path('api/recomendacoes/', views.recommendations, name='recomendacoes'),
    path('api/album/faixas/', views.album_tracks, name='album_faixas'),
    path('api/musica/info/', views.track_info, name='musica_info'),

    path('api/streaming-url/', views.streaming_url_api, name='api_streaming_url'),
    path('api/musicas/by-ids/', views.musicas_by_ids_api, name='api_musicas_by_ids'),

    # Recomendações com id na URL
    path('api/recommendations/<str:track_id>/', views.recommendations_api, name='api_recommendations_with_id'),

    # Álbum detalhe usado pelo template
    path('api/album/<str:album_id>/', views.album_detail_api, name='api_album_detail_by_id'),

    # ============================================================================
    # PLAYLISTS — mantenha apenas rotas canônicas (plural)
    # ============================================================================
    # Observação: templates e testes foram atualizados para usar `/api/playlists/`.
    # Remover rotas singulares legadas para reduzir superfície de rota.
    # A view `remove_track_from_playlist` está deprecada; o novo endpoint
    # `/api/playlists/<id>/remove/` deve ser usado por clientes modernos.
    # Mantemos a rota apenas temporariamente para compatibilidade.
    # path('api/playlists/<int:playlist_id>/remove/', views.remove_track_from_playlist, name='api_playlist_remove_track'),
    # Compartilhamento por link (APIs)
    path('api/playlists/<int:playlist_id>/share/toggle/', views.playlist_share_toggle_api, name='api_playlist_share_toggle'),
    path('api/playlists/<int:playlist_id>/share/regenerate/', views.playlist_share_regenerate_api, name='api_playlist_share_regenerate'),
    # ============================================================================
    # API DE AUTENTICAÇÃO (MOBILE / TOKEN)
    # ============================================================================
    path('api/auth/login/', views.api_auth_login, name='api_auth_login'),
    path('api/auth/register/', views.api_auth_register, name='api_auth_register'),
    path('api/auth/logout/', views.api_auth_logout, name='api_auth_logout'),
    path('api/auth/profile/', views.api_auth_profile, name='api_auth_profile'),
    # password reset via API (React/mobile clients)
    path('api/auth/password-reset/', views.api_auth_password_reset, name='api_auth_password_reset'),
    path('api/auth/password-reset-confirm/', views.api_auth_password_reset_confirm, name='api_auth_password_reset_confirm'),

    # ASAAS: cobrança PIX e webhook
    path('api/payments/asaas/create-pix/', views.asaas_create_pix, name='api_asaas_create_pix'),
    path('api/payments/asaas/status/', views.asaas_payment_status, name='api_asaas_payment_status'),
    # endpoint to retrieve a user's latest payment; include both slash/no-slash
    path('api/payments/asaas/my/', views.asaas_my_payment, name='api_asaas_my_payment'),
    path('api/payments/asaas/my', views.asaas_my_payment),
    path('api/payments/asaas/cancel/', views.asaas_cancel_payment, name='api_asaas_cancel_payment'),
    path('api/subscription/cancel/', views.asaas_cancel_subscription, name='api_subscription_cancel'),
    # accepts both with and without trailing slash since webhook providers sometimes omit it
    path('webhook/asaas/', views.asaas_webhook, name='asaas_webhook'),
    path('webhook/asaas', views.asaas_webhook),
]

# URLs públicas (sem API)
public_patterns = [
    path('', views.buscar_musicas_html, name='home'),
    path('buscar/', views.buscar_musicas_html, name='buscar'),
    path('share/<uuid:share_uuid>/', views.shared_playlist_view, name='playlist-share'),
    path('share/track/', views.shared_track_view, name='track-share'),
    path('short/track/', views.shorten_track, name='short-track'),
    path('t/<str:code>/', views.track_short_redirect, name='track-short'),
]

# Incluir rotas públicas no conjunto principal
urlpatterns += public_patterns
