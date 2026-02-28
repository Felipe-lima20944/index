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
    # accept '/registro' without trailing slash as well
    path('register', views.register),
    path('register/', views.register, name='register'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/edit/', views.profile_edit, name='profile_edit'),
    path('assinatura/', views.assinatura, name='assinatura'),
    
    # ============================================================================
    # API DE INTEGRAÇÃO COM YOUTUBE MUSIC
    # ============================================================================
    path('api/search/', views.buscar_musicas_api, name='api_search'),
    path('api/recommendations/', views.recommendations_api, name='api_recommendations'),
    path('api/album/tracks/', views.album_tracks_api, name='api_album_tracks'),
    path('api/track/info/', views.track_info_api, name='api_track_info'),
    path('api/stream/url/', views.streaming_url_api, name='api_stream_url'),
    path('api/featured/', views.featured_albums_api, name='api_featured'),
    
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
    
    # ============================================================================
    # API DE PLAYLISTS
    # ============================================================================
    path('api/playlists/', views.playlists_api, name='api_playlists'),
    path('api/playlists/<int:playlist_id>/', views.playlist_detail_api, name='api_playlist_detail'),
    path('api/playlists/<int:playlist_id>/add/', views.playlist_add_musica_api, name='api_playlist_add'),
    path('api/playlists/<int:playlist_id>/remove/<int:item_id>/', views.playlist_remove_musica_api, name='api_playlist_remove'),
    path('api/playlists/<int:playlist_id>/reorder/', views.playlist_reorder_api, name='api_playlist_reorder'),
    
    # ============================================================================
    # API DE HISTÓRICO E FAVORITOS (APIs canônicas + PT-BR alias)
    # ============================================================================
    path('api/history/', views.historico_api, name='api_history'),
    # Canonical favorites endpoint (supports GET/POST/DELETE and {id,like} schema)
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
    # Manter rota para remoção por musica_id no formato plural.
    path('api/playlists/<int:playlist_id>/remove/', views.remove_track_from_playlist, name='api_playlist_remove_track'),
    # Compartilhamento por link (APIs)
    path('api/playlists/<int:playlist_id>/share/toggle/', views.playlist_share_toggle_api, name='api_playlist_share_toggle'),
    path('api/playlists/<int:playlist_id>/share/regenerate/', views.playlist_share_regenerate_api, name='api_playlist_share_regenerate'),
    # ASAAS: cobrança PIX e webhook
    path('api/payments/asaas/create-pix/', views.asaas_create_pix, name='api_asaas_create_pix'),
    path('api/payments/asaas/status/', views.asaas_payment_status, name='api_asaas_payment_status'),
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
