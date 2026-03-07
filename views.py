from rest_framework.response import Response
try:
    import yt_dlp
except Exception:
    yt_dlp = None  # optional dependency; some features may be disabled without it
import os
import tempfile, shutil
import requests
import json
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from functools import lru_cache
import time
from typing import Dict, List, Optional, Any, Tuple
import logging
import hashlib
from datetime import timedelta, datetime
import re

from django.conf import settings
from django.shortcuts import render, get_object_or_404, redirect
from django.utils.html import escape
from django.core.cache import cache
import secrets
from django.http import JsonResponse, HttpResponse, Http404, FileResponse
from django.urls import reverse
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Q, Count, Avg, F, Max
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.forms import UserCreationForm
from .forms import CustomUserCreationForm
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.contrib.auth.tokens import default_token_generator
from django.forms.models import model_to_dict
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticatedOrReadOnly, IsAuthenticated, AllowAny
from rest_framework import status

# YTMusic is an optional dependency used for some features (e.g. fetch from
# YouTube Music).  To keep migrations and simple deployments working even when
# it's absent we import it inside a try/except and fall back to None.  We also
# log a warning so it's easier to diagnose why calls to the API fail later.
try:
    from ytmusicapi import YTMusic
except ImportError as imp_err:
    YTMusic = None
    logger = logging.getLogger(__name__)
    logger.warning(
        "ytmusicapi não pôde ser importado: %s; funcionalidades do YouTube Music "
        "ficarão indisponíveis.",
        imp_err,
    )

from .models import (
    Artista, Album, Musica, Playlist, PlaylistItem,
    Favorito, PlaybackQueue,
    PlaybackState, Avaliacao, Genero, SharedTrack,
    TrendingMusic, SearchBackground, AnonymousPromo, UnsubscribedPromo,
    AppDownload,
)
from .models import Banner
from .models import AsaasCustomer, Payment, Subscription, Plan
from .serializers import (
    ArtistaSerializer, AlbumSerializer, MusicaSerializer, MusicaListSerializer,
    PlaylistSerializer, PlaylistDetailSerializer, PlaylistItemSerializer,
    FavoritoSerializer, AvaliacaoSerializer, GeneroSerializer,
    TrendingMusicSerializer, AppDownloadSerializer,
)
from .serializers import AnonymousPromoSerializer, UnsubscribedPromoSerializer
from .serializers import BannerSerializer
from django.views.decorators.http import require_POST
import uuid

# Configuração de logging
logger = logging.getLogger(__name__)

# Constantes
from django.conf import settings

CACHE_TIMEOUT = 3600  # 1 hora
SEARCH_CACHE_TIMEOUT = 300  # 5 minutos
ALBUM_CACHE_TIMEOUT = 3600 * 24  # 24 horas

# Duração do cache para URLs de streaming; agora configurable via settings.
#   em settings.py defina STREAM_CACHE_TIMEOUT = 18000  # 5 horas
#   ou None para cache indefinido (cuidado com espaço de memória).
STREAM_CACHE_TIMEOUT = getattr(settings, 'STREAM_CACHE_TIMEOUT', 1800)  # default 30 minutos
MAX_RETRIES = 3
REQUEST_TIMEOUT = 10
CONCURRENT_WORKERS = 4


# ============================================================================
# FUNÇÕES AUXILIARES
# ============================================================================

def _check_connectivity(timeout: int = 5) -> bool:
    """Verifica conectividade com o YouTube Music com timeout e cache."""
    cache_key = 'yt_connectivity'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        response = requests.get(
            'https://music.youtube.com',
            timeout=timeout,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        is_connected = response.status_code == 200
        cache.set(cache_key, is_connected, 60)
        return is_connected
    except Exception as e:
        logger.warning(f"Erro de conectividade: {e}")
        cache.set(cache_key, False, 60)
        return False


@lru_cache(maxsize=1)
def _get_headers_auth_path() -> Optional[str]:
    """Cache para o caminho do arquivo de autenticação."""
    candidates = []

    if getattr(settings, 'YT_HEADERS_AUTH', None):
        candidates.append(settings.YT_HEADERS_AUTH)

    candidates.extend([
        'headers_auth.json',
        os.path.join(os.getcwd(), 'headers_auth.json'),
        os.path.join(os.path.dirname(os.path.dirname(__file__)), 'headers_auth.json'),
    ])

    for candidate in candidates:
        if candidate:
            cand_str = str(candidate)
            if os.path.exists(cand_str):
                return cand_str
    return None


@lru_cache(maxsize=1)
def _init_ytmusic() -> YTMusic:
    """Inicializa cliente YTMusic.

    A biblioteca `ytmusicapi` é opcional. Se não estiver instalada, esta
    função levanta `ImportError` para que as rotas que dependem dela possam
    tratar o caso e devolver um erro amigável em vez de falhar com um
    ``NoneType``.
    """

    if YTMusic is None:
        # Não instalou a dependência; avisar de forma clara.
        raise ImportError(
            "biblioteca ytmusicapi não disponível; instale-a para usar recursos "
            "do YouTube Music ou ajuste as configurações para não chamar essas "
            "funções."
        )

    headers_path = _get_headers_auth_path()
    try:
        if headers_path:
            return YTMusic(str(headers_path))
        else:
            return YTMusic()
    except Exception as e:
        # registra qualquer problema durante a inicialização real do cliente
        logger.error(f"Erro ao inicializar YTMusic: {e}")
        # repassamos a exceção para que a camada de chamada lide com ela
        raise


def _get_cookiefile_path() -> Optional[str]:
    """Obtém caminho do arquivo de cookies."""
    cookie_setting = getattr(settings, 'YT_COOKIES_FILE', None)
    if cookie_setting:
        return str(cookie_setting)

    base_dir = getattr(settings, 'BASE_DIR', None)
    if base_dir:
        candidate = os.path.join(str(base_dir), 'cookies.txt')
        if os.path.exists(candidate):
            return candidate

    candidate = os.path.join(os.getcwd(), 'cookies.txt')
    if os.path.exists(candidate):
        return candidate

    return None


def _format_duration(seconds: int) -> str:
    """Formata duração em segundos para formato MM:SS ou HH:MM:SS."""
    if not seconds or seconds <= 0:
        return ''

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


def _parse_duration(duration_str: str) -> int:
    """Converte string de duração (ex: '3:45') para segundos."""
    if not duration_str:
        return 0

    try:
        parts = duration_str.split(':')
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 1:
            return int(parts[0])
    except (ValueError, TypeError):
        pass
    return 0


def _asaas_headers() -> Dict[str, str]:
    """Cabeçalhos padrão para chamadas à API do Asaas.

    Retorna um dicionário contendo `Content-Type` e o token de
    autenticação extraído de ``settings.ASAAS_API_KEY``.  Se a chave não
    estiver definida (string vazia ou None) um aviso é registrado; as
    chamadas subsequentes provavelmente irão falhar com 401/403 do
    Asaas.
    """
    token = getattr(settings, 'ASAAS_API_KEY', '') or ''
    if not token:
        logger.warning('ASAAS_API_KEY não configurada; requisições Asaas podem falhar')
    return {
        'Content-Type': 'application/json',
        'access_token': token,
    }


def _normalize_track(item: Dict, include_album_info: bool = True) -> Optional[Dict]:
    """Normaliza dados de uma faixa para formato consistente."""
    if not item:
        return None

    video_id = item.get('videoId') or item.get('id') or ''
    if not video_id:
        return None

    thumbs = item.get('thumbnails', [])
    thumbnail = ''
    if thumbs:
        if isinstance(thumbs, list) and thumbs:
            thumbnail = thumbs[-1].get('url', '') if isinstance(thumbs[-1], dict) else ''

    artists = []
    if isinstance(item.get('artists'), list):
        artists = [a.get('name', '') for a in item.get('artists', []) if a.get('name')]
    elif item.get('artist'):
        artists = [item.get('artist', '')]
    elif item.get('artists') and isinstance(item.get('artists'), str):
        artists = [item.get('artists')]

    duration = item.get('duration') or item.get('length') or ''
    duration_seconds = 0

    if isinstance(duration, (int, float)):
        duration_seconds = int(duration)
        duration = _format_duration(duration_seconds)
    else:
        duration_seconds = _parse_duration(str(duration))

    album_browse_id = ''
    album_name = ''
    if include_album_info and item.get('album'):
        if isinstance(item.get('album'), dict):
            album_browse_id = item.get('album', {}).get('browseId') or item.get('album', {}).get('id') or ''
            album_name = item.get('album', {}).get('name') or ''
        else:
            album_name = str(item.get('album', ''))

    result = {
        'id': video_id,
        'videoId': video_id,
        'title': item.get('title') or item.get('name') or 'Título desconhecido',
        'artists': artists,
        'artist': ', '.join(artists) if artists else 'Artista desconhecido',
        'thumb': thumbnail,
        'thumbnail': thumbnail,
        'duration': duration,
        'duration_seconds': duration_seconds,
        'album_browseId': album_browse_id,
        'album': album_name,
        'year': item.get('year', ''),
        'likeStatus': item.get('likeStatus', ''),
    }

    track_number = None
    for key in ('track_number', 'trackNumber', 'index', 'track', 'trackNo'):
        try:
            if item.get(key) is not None:
                track_number = int(item.get(key))
                break
        except Exception:
            continue

    result['track_number'] = track_number

    return result


def _normalize_album(item: Dict) -> Optional[Dict]:
    """Normaliza dados de um álbum para formato consistente."""
    if not item:
        return None

    browse_id = item.get('browseId') or item.get('id') or ''
    if not browse_id:
        return None

    thumbs = item.get('thumbnails', [])
    thumbnail = ''
    if thumbs:
        if isinstance(thumbs, list) and thumbs:
            thumbnail = thumbs[-1].get('url', '') if isinstance(thumbs[-1], dict) else ''

    artists = []
    if isinstance(item.get('artists'), list):
        artists = [a.get('name', '') for a in item.get('artists', []) if a.get('name')]
    elif item.get('artist'):
        artists = [item.get('artist')]

    return {
        'id': browse_id,
        'browseId': browse_id,
        'title': item.get('title') or item.get('name') or 'Álbum desconhecido',
        'artists': artists,
        'artist': ', '.join(artists) if artists else 'Artista desconhecido',
        'thumb': thumbnail,
        'thumbnail': thumbnail,
        'year': item.get('year', ''),
        'track_count': item.get('trackCount') or item.get('track_count') or 0,
        'total_duration': item.get('duration') or '',
    }


def _fetch_album_details_parallel(albums: List[Dict]) -> List[Dict]:
    """Busca detalhes de múltiplos álbuns em paralelo."""
    if not albums:
        return albums

    def fetch_single(browse_id: str) -> Optional[Dict]:
        if not browse_id:
            return None

        cache_key = f'album_details_{browse_id}'
        cached = cache.get(cache_key)
        if cached:
            return cached

        try:
            client = _init_ytmusic()
            details = client.get_album(browse_id)
            cache.set(cache_key, details, ALBUM_CACHE_TIMEOUT)
            return details
        except Exception as e:
            logger.warning(f"Erro ao buscar detalhes do álbum {browse_id}: {e}")
            return None

    to_fetch = [(idx, alb.get('id') or alb.get('browseId'))
                for idx, alb in enumerate(albums)
                if alb.get('id') or alb.get('browseId')]

    if not to_fetch:
        return albums

    with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as executor:
        future_to_idx = {
            executor.submit(fetch_single, browse_id): idx
            for idx, browse_id in to_fetch
        }

        for future in concurrent.futures.as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                details = future.result(timeout=3)
                if details:
                    tracks = details.get('tracks') or details.get('songs') or []
                    albums[idx]['track_count'] = len(tracks)

                    total_seconds = 0
                    for t in tracks:
                        if t.get('duration'):
                            if isinstance(t['duration'], (int, float)):
                                total_seconds += int(t['duration'])
                            else:
                                total_seconds += _parse_duration(str(t.get('duration', '')))

                    if total_seconds > 0:
                        albums[idx]['total_duration'] = _format_duration(total_seconds)
            except (TimeoutError, Exception) as e:
                logger.debug(f"Timeout ao processar álbum {idx}: {e}")

    return albums


def _resolve_musica_identifier(identifier) -> 'Musica':
    """Tenta obter uma Musica por id numérico (PK) ou por youtube_id."""
    if identifier is None:
        raise Musica.DoesNotExist()

    # numeric primary key?
    if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
        try:
            return Musica.objects.get(pk=int(identifier))
        except Musica.DoesNotExist:
            pass

    # try lookup by YouTube id
    yid = str(identifier)
    try:
        return Musica.objects.get(youtube_id=yid)
    except Musica.MultipleObjectsReturned:
        qs = Musica.objects.filter(youtube_id=yid).order_by('id')
        first = qs.first()
        logger.warning(f"_resolve_musica_identifier: múltiplos registros Musica com youtube_id={yid}; retornando id={getattr(first, 'id', None)}")
        if first is not None:
            return first
        raise Musica.DoesNotExist()
    except Musica.DoesNotExist:
        raise


# ============================================================================
# API: BANNERS
# ============================================================================


@api_view(['GET'])
@permission_classes([AllowAny])
def banners_list_api(request):
    """Retorna banners ativos ordenados para exibição no app."""
    banners = Banner.objects.filter(is_active=True).order_by('ordem')
    serializer = BannerSerializer(banners, many=True, context={'request': request})
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([AllowAny])
def search_backgrounds_list_api(request):
    """Retorna backgrounds de busca ativos ordenados por ordem."""
    bgs = SearchBackground.objects.filter(is_active=True).order_by('ordem')
    from .serializers import SearchBackgroundSerializer
    serializer = SearchBackgroundSerializer(bgs, many=True, context={'request': request})
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([AllowAny])
def anonymous_promos_list_api(request):
    """Retorna promoções anônimas ativas ordenadas por threshold_seconds."""
    promos = AnonymousPromo.objects.filter(is_active=True).order_by('threshold_seconds')
    from .serializers import AnonymousPromoSerializer
    serializer = AnonymousPromoSerializer(promos, many=True, context={'request': request})
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([AllowAny])
def promos_list_api(request):
    """Retorna promoções apropriadas ao estado do usuário.

    - Usuários não autenticados: retornam `AnonymousPromo`.
    - Usuários autenticados sem assinatura ativa: retornam `UnsubscribedPromo`.
    - Usuários autenticados com assinatura ativa: retornam lista vazia.
    """
    user = request.user if hasattr(request, 'user') else None
    try:
        if not user or not user.is_authenticated:
            qs = AnonymousPromo.objects.filter(is_active=True).order_by('threshold_seconds')
            serializer = AnonymousPromoSerializer(qs, many=True, context={'request': request})
            return Response(serializer.data)

        # usuário autenticado — checar assinatura ativa
        from .models import Subscription
        has_active = Subscription.objects.filter(usuario=user, status='active').exists()
        if has_active:
            return Response([])

        # usuário sem assinatura ativa → retornar promos para não-assinantes
        qs = UnsubscribedPromo.objects.filter(is_active=True).order_by('threshold_seconds')
        serializer = UnsubscribedPromoSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)
    except Exception as e:
        logger.exception('Erro em promos_list_api: %s', e)
        return Response([], status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([AllowAny])
def plans_list_api(request):
    """Retorna os planos disponíveis (público)."""
    try:
        from .models import Plan
        from .serializers import PlanSerializer
        qs = Plan.objects.all().order_by('-is_active', 'price')
        serializer = PlanSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)
    except Exception as e:
        logger.exception('Erro em plans_list_api: %s', e)
        return Response([], status=status.HTTP_200_OK)

    try:
        return Musica.objects.get(youtube_id=yid)
    except Musica.MultipleObjectsReturned:
        qs = Musica.objects.filter(youtube_id=yid).order_by('id')
        first = qs.first()
        logger.warning(f"_resolve_musica_identifier: múltiplos registros Musica com youtube_id={yid}; retornando id={getattr(first, 'id', None)}")
        if first is not None:
            return first
        raise Musica.DoesNotExist()
    except Musica.DoesNotExist:
        raise


def _is_valid_youtube_id(video_id: str) -> bool:
    """Validação simples do formato de YouTube video id (11 chars alnum/_-)."""
    try:
        import re
        return bool(re.match(r'^[A-Za-z0-9_-]{11}$', str(video_id)))
    except Exception:
        return False


def _build_ydl_opts_js_runtime(opts: dict) -> dict:
    """
    Aplica a configuração de JS runtime para yt-dlp de forma compatível
    com múltiplas versões da biblioteca.
    """
    jsr = getattr(settings, 'YT_DLP_JS_RUNTIMES', None)
    if not jsr:
        nodepath = getattr(settings, 'NODE_PATH', None)
        if nodepath:
            jsr = f'node:{nodepath}'

    if not jsr:
        return opts

    if isinstance(jsr, (list, tuple)):
        runtime_str = ';'.join(str(r) for r in jsr)
    else:
        runtime_str = str(jsr)

    logger.debug(f"_build_ydl_opts_js_runtime: aplicando runtime={runtime_str}")
    opts['extractor_args'] = {'youtube': {'js_runtimes': [runtime_str]}}

    # configure ffmpeg location automatically if we have one of the helper
    # packages installed; this prevents yt-dlp from complaining about missing
    # ffmpeg/ffprobe when the binary is bundled via pip (imageio-ffmpeg,
    # ffmpeg-static, etc.). if the user has installed a system ffmpeg the
    # default behaviour (no setting) is fine.
    try:
        import imageio_ffmpeg
        opts['ffmpeg_location'] = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        try:
            import ffmpeg_static
            opts['ffmpeg_location'] = ffmpeg_static.path
        except Exception:
            pass

    # apply proxy from settings if provided (YT_DLP_PROXY); default to localhost:8888
    # if the setting is not defined or falsy we still want to route through the
    # local residential proxy that listens on port 8888.  callers that need to
    # disable proxy can explicitly set YT_DLP_PROXY to an empty string.
    proxy = getattr(settings, 'YT_DLP_PROXY', None)
    if not proxy:
        # default proxy used by the deployment
        proxy = 'http://127.0.0.1:8888'
    # yt-dlp expects a string like 'http://host:port' or 'socks5://...'
    opts['proxy'] = proxy

    # always return opts so callers can chain safely
    return opts

def _ensure_asaas_customer(user, document=None):
    """Garantir que exista um AsaasCustomer local; cria no Asaas se necessário."""
    def only_digits(v):
        try:
            return ''.join([c for c in str(v) if c.isdigit()])
        except Exception:
            return ''

    try:
        cust = AsaasCustomer.objects.filter(usuario=user).first()
        if cust and getattr(cust, 'asaas_id', None):
            existing_data = cust.dados or {}
            try:
                if isinstance(existing_data, str):
                    existing_data = json.loads(existing_data)
            except Exception:
                existing_data = {}

            existing_cpf = existing_data.get('cpfCnpj') or existing_data.get('cpf_cnpj')
            if existing_cpf:
                return cust, None

            if document:
                try:
                    patch_payload = {'cpfCnpj': only_digits(document)}
                    profile = getattr(user, 'profile', None)
                    if profile is not None:
                        if getattr(profile, 'phone', None):
                            patch_payload['phone'] = profile.phone
                        if getattr(profile, 'mobile_phone', None):
                            patch_payload['mobilePhone'] = profile.mobile_phone
                        if getattr(profile, 'address', None):
                            patch_payload['address'] = profile.address
                        if getattr(profile, 'address_number', None):
                            patch_payload['addressNumber'] = profile.address_number
                        if getattr(profile, 'complement', None):
                            patch_payload['complement'] = profile.complement
                        if getattr(profile, 'state', None):
                            patch_payload['province'] = profile.state
                        if getattr(profile, 'city', None):
                            patch_payload['city'] = profile.city
                        if getattr(profile, 'postal_code', None):
                            patch_payload['postalCode'] = profile.postal_code

                    resp_upd = requests.patch(
                        f"{settings.ASAAS_BASE_URL.rstrip('/')}/api/v3/customers/{cust.asaas_id}",
                        json=patch_payload,
                        headers=_asaas_headers(),
                        timeout=settings.ASAAS_TIMEOUT
                    )
                    if resp_upd.status_code in (200, 201):
                        try:
                            updated = resp_upd.json()
                        except Exception:
                            updated = {}
                        cust.dados = updated
                        cust.save(update_fields=['dados', 'atualizado_em'])
                        return cust, None
                    else:
                        try:
                            err = resp_upd.json()
                        except Exception:
                            err = {'detail': resp_upd.text}
                        logger.error('Asaas update customer failed: %s %s', resp_upd.status_code, err)
                        if resp_upd.status_code == 404:
                            logger.warning('Customer %s not found on Asaas, attempting recreate', cust.asaas_id)
                            cust.delete()
                            return _ensure_asaas_customer(user, document=document)
                        return None, err
                except Exception as e:
                    logger.exception('Erro ao atualizar cliente Asaas: %s', e)
                    return None, {'detail': str(e)}

            return None, {'detail': 'missing_document'}

        payload = {
            'name': user.get_full_name() or user.username,
            'email': user.email or '',
        }

        profile = getattr(user, 'profile', None)
        if document:
            payload['cpfCnpj'] = only_digits(document)
        else:
            doc = None
            if hasattr(user, 'cpf') and getattr(user, 'cpf'):
                doc = getattr(user, 'cpf')
            elif hasattr(user, 'document') and getattr(user, 'document'):
                doc = getattr(user, 'document')
            elif profile is not None and getattr(profile, 'cpf_cnpj', None):
                doc = getattr(profile, 'cpf_cnpj')
            if doc:
                payload['cpfCnpj'] = only_digits(doc)

        if profile is not None:
            if getattr(profile, 'phone', None):
                payload['phone'] = profile.phone
            if getattr(profile, 'mobile_phone', None):
                payload['mobilePhone'] = profile.mobile_phone
            if getattr(profile, 'address', None):
                payload['address'] = profile.address
            if getattr(profile, 'address_number', None):
                payload['addressNumber'] = profile.address_number
            if getattr(profile, 'complement', None):
                payload['complement'] = profile.complement
            if getattr(profile, 'state', None):
                payload['province'] = profile.state
            if getattr(profile, 'city', None):
                payload['city'] = profile.city
            if getattr(profile, 'postal_code', None):
                payload['postalCode'] = profile.postal_code

        resp = requests.post(
            f"{settings.ASAAS_BASE_URL.rstrip('/')}/api/v3/customers",
            json=payload,
            headers=_asaas_headers(),
            timeout=settings.ASAAS_TIMEOUT
        )
        if resp.status_code in (200, 201):
            try:
                data = resp.json()
            except Exception:
                logger.error('Asaas returned non-JSON when creating customer: %s', resp.text[:1000])
                data = {}
            asaas_id = data.get('id') or data.get('object') or ''
            if not cust:
                cust = AsaasCustomer(usuario=user, asaas_id=asaas_id, dados=data)
            else:
                cust.asaas_id = asaas_id
                cust.dados = data
            cust.save()
            return cust, None
        else:
            try:
                err = resp.json()
            except Exception:
                err = {'detail': resp.text}
            logger.error('Asaas create customer failed: %s %s', resp.status_code, err)
            return None, err
    except Exception as e:
        logger.exception('Erro criando cliente Asaas: %s', e)
        return None, {'detail': str(e)}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def asaas_payment_status(request):
    """Get payment status from local DB or Asaas directly."""
    pid = request.GET.get('id')
    if not pid:
        return Response({'error': 'id parameter required'}, status=400)

    # first try looking for a record with the external asaas_payment_id
    payment = Payment.objects.filter(asaas_payment_id=pid).first()
    # if that fails, fall back to treating `pid` as our local Payment.pk
    if not payment and pid.isdigit():
        payment = Payment.objects.filter(id=int(pid)).first()
    if not payment:
        return Response({'error': 'payment_not_found'}, status=404)

    resp = {'status': payment.status}
    if payment.raw:
        resp['raw'] = payment.raw
    return Response(resp)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def asaas_my_payment(request):
    """Return the current user's most recent/pending Asaas payment (if any)."""
    user = request.user
    try:
        # prefer pending PIX payments
        pending = Payment.objects.filter(usuario=user, method='PIX').exclude(status__in=['paid', 'cancelled', 'failed']).order_by('-criado_em').first()
        if pending:
            p = pending
        else:
            # fallback to most recent payment of any method
            p = Payment.objects.filter(usuario=user).order_by('-criado_em').first()

        if not p:
            return Response({'detail': 'not_found'}, status=404)

        return Response({
            'payment_id': p.id,
            'asaas_payment_id': p.asaas_payment_id,
            'pix_payload': getattr(p, 'pix_qr_payload', '') or '',
            'pix_qr': getattr(p, 'pix_qr_image', '') or '',
            'copy_paste': getattr(p, 'pix_qr_payload', '') or '',
            'status': p.status,
            'raw': p.raw or {},
        })
    except Exception as e:
        logger.exception('Error in asaas_my_payment: %s', e)
        return Response({'error': 'internal_error'}, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def asaas_cancel_payment(request):
    """Cancel a payment locally and attempt to cancel it on Asaas if possible.

    Accepts JSON body with either `payment_id` (local DB id) or `asaas_id` (provider id).
    """
    user = request.user
    pid = request.data.get('payment_id') or request.data.get('id')
    asaas_id = request.data.get('asaas_id') or request.data.get('asaas_payment_id')
    try:
        payment = None
        if pid:
            payment = Payment.objects.filter(id=pid, usuario=user).first()
        if not payment and asaas_id:
            payment = Payment.objects.filter(asaas_payment_id=asaas_id, usuario=user).first()
        if not payment:
            return Response({'error': 'payment_not_found'}, status=404)

        # Do not cancel already paid payments
        if str(payment.status).lower() == 'paid':
            return Response({'error': 'already_paid'}, status=400)

        # attempt to cancel on Asaas if we have an id
        if payment.asaas_payment_id:
            try:
                cancel_url = f"{settings.ASAAS_BASE_URL.rstrip('/')}/api/v3/payments/{payment.asaas_payment_id}/cancel"
                resp = requests.post(cancel_url, headers=_asaas_headers(), timeout=settings.ASAAS_TIMEOUT)
                logger.info('Asaas cancel response %s: %s', resp.status_code, resp.text[:200])
            except Exception as e:
                logger.warning('Failed to call Asaas cancel: %s', e)

        payment.status = 'cancelled'
        payment.save(update_fields=['status', 'atualizado_em'])

        return Response({'status': 'cancelled'})
    except Exception as e:
        logger.exception('Error cancelling payment: %s', e)
        return Response({'error': 'internal_error'}, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def asaas_cancel_subscription(request):
    """Cancel a user's active subscription (local record) and attempt to cancel on Asaas.

    Accepts optional JSON body with `subscription_id` or `asaas_subscription_id`.
    If none provided, cancels the user's most recent active subscription.
    """
    user = request.user
    sub_id = request.data.get('subscription_id') or request.data.get('id')
    asaas_id = request.data.get('asaas_subscription_id') or request.data.get('asaas_id')
    try:
        subscription = None
        if sub_id:
            subscription = Subscription.objects.filter(id=sub_id, usuario=user).first()
        if not subscription and asaas_id:
            subscription = Subscription.objects.filter(asaas_subscription_id=asaas_id, usuario=user).first()
        if not subscription:
            # fallback: most recent active subscription
            subscription = Subscription.objects.filter(usuario=user, status='active').order_by('-criado_em').first()
        if not subscription:
            return Response({'error': 'subscription_not_found'}, status=404)

        # If there's an asaas_subscription_id attempt to cancel with Asaas
        if subscription.asaas_subscription_id:
            try:
                cancel_url = f"{settings.ASAAS_BASE_URL.rstrip('/')}/api/v3/subscriptions/{subscription.asaas_subscription_id}/cancel"
                resp = requests.post(cancel_url, headers=_asaas_headers(), timeout=settings.ASAAS_TIMEOUT)
                logger.info('Asaas subscription cancel response %s: %s', resp.status_code, resp.text[:200])
            except Exception as e:
                logger.warning('Failed to call Asaas subscription cancel: %s', e)

        subscription.status = 'cancelled'
        subscription.save(update_fields=['status', 'atualizado_em'])

        return Response({'status': 'cancelled'})
    except Exception as e:
        logger.exception('Error cancelling subscription: %s', e)
        return Response({'error': 'internal_error'}, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def asaas_create_pix(request):
    """
    Cria uma cobrança PIX no Asaas.
    Faz duas chamadas: (1) criar pagamento, (2) buscar QR code/payload PIX.
    Retorna pix_payload (copia e cola) e pix_qr (data URI base64 da imagem).
    """
    user = request.user
    amount = request.data.get('amount')
    description = request.data.get('description', 'Cobrança Beatflow')

    if not amount:
        return Response({'error': 'amount is required'}, status=400)

    # Se já houver um pagamento PIX pendente para este usuário, reutilizar em vez de criar outro
    try:
        # procurar qualquer cobrança PIX ainda não finalizada (não paga/estornada/recusada)
        existing = Payment.objects.filter(usuario=user, method='PIX')
        existing = existing.exclude(status__in=['paid','cancelled','failed'])
        existing = existing.order_by('-criado_em').first()
        if existing:
            return Response({
                'payment_id': existing.id,
                'asaas_payment_id': existing.asaas_payment_id,
                'pix_payload': existing.pix_qr_payload,
                'pix_qr': existing.pix_qr_image,
                'copy_paste': existing.pix_qr_payload,
                'status': existing.status,
                'message': 'existing_pending_payment'
            })
    except Exception as e:
        logger.debug(f"Erro ao verificar pagamentos pendentes: {e}")

    # Aceitar e persistir CPF/CNPJ
    document = request.data.get('document') or request.data.get('cpf') or request.data.get('cpf_cnpj')
    if document:
        try:
            normalized = ''.join([c for c in str(document) if c.isdigit()])
            profile = getattr(user, 'profile', None)
            if profile is not None and normalized:
                profile.cpf_cnpj = normalized
                profile.save(update_fields=['cpf_cnpj', 'updated_at'])
                logger.debug(f"Saved cpf_cnpj for user {user.username}")
        except Exception as e:
            logger.debug(f"Failed to save cpf_cnpj on profile: {e}")

    cust, asaas_err = _ensure_asaas_customer(user, document=document)
    if not cust:
        profile = getattr(user, 'profile', None)
        profile_cpf = getattr(profile, 'cpf_cnpj', None) if profile is not None else None
        logger.debug(f"Failed to ensure Asaas customer for user={user.username}; profile_cpf={profile_cpf}; asaas_err={asaas_err}")
        resp_payload = {
            'error': 'failed_to_create_customer',
            'profile_cpf': profile_cpf,
            'message': 'CPF/CNPJ ausente ou erro ao criar/atualizar cliente no Asaas. Atualize seu perfil com CPF/CNPJ ou envie "document" no body da requisição.'
        }
        if asaas_err:
            resp_payload['asaas_error'] = asaas_err
        return Response(resp_payload, status=400)

    payload = {
        'customer': cust.asaas_id,
        'billingType': 'PIX',
        'value': str(amount),
        'dueDate': (timezone.now() + timedelta(days=1)).strftime('%Y-%m-%d'),
        'description': description,
        'externalReference': f"beatflow:{user.id}:{uuid.uuid4()}"
    }

    try:
        # ── PASSO 1: Criar pagamento ──────────────────────────────────────────
        resp = requests.post(
            f"{settings.ASAAS_BASE_URL.rstrip('/')}/api/v3/payments",
            json=payload,
            headers=_asaas_headers(),
            timeout=settings.ASAAS_TIMEOUT
        )
        if resp.status_code not in (200, 201):
            logger.error('Asaas create payment failed: %s %s', resp.status_code, resp.text)
            detail = resp.text or f'status:{resp.status_code}'
            return Response({'error': 'asaas_error', 'detail': detail}, status=502)

        data = resp.json()
        # log completo para depuração de IDs inesperados
        logger.debug('Asaas payment create response: %s', data)

        asaas_payment_id = data.get('id') or data.get('object') or ''
        pix_payload = ''
        pix_qr = ''
        copy_paste = ''

        # ── PASSO 2: Buscar QR code PIX (endpoint separado) ──────────────────
        # O Asaas NÃO retorna o QR na resposta de criação; é necessário chamar
        # /api/v3/payments/{id}/pixQrCode para obter encodedImage e payload.
        if asaas_payment_id:
            try:
                qr_resp = requests.get(
                    f"{settings.ASAAS_BASE_URL.rstrip('/')}/api/v3/payments/{asaas_payment_id}/pixQrCode",
                    headers=_asaas_headers(),
                    timeout=settings.ASAAS_TIMEOUT
                )
                logger.debug('Asaas pixQrCode status: %s', qr_resp.status_code)
                if qr_resp.status_code in (200, 201):
                    qr_data = qr_resp.json()
                    logger.debug('Asaas pixQrCode response keys: %s', list(qr_data.keys()) if isinstance(qr_data, dict) else type(qr_data))

                    # Asaas retorna: encodedImage (PNG base64) e payload (código copia e cola)
                    raw_img     = qr_data.get('encodedImage') or qr_data.get('qrcode') or qr_data.get('qrCode') or ''
                    pix_payload = qr_data.get('payload') or qr_data.get('copyPaste') or qr_data.get('pixPayload') or ''
                    copy_paste  = pix_payload

                    # Garantir prefixo data URI
                    if raw_img:
                        if raw_img.startswith('data:'):
                            pix_qr = raw_img
                        else:
                            pix_qr = 'data:image/png;base64,' + raw_img
                else:
                    logger.warning(
                        'Asaas pixQrCode endpoint returned %s: %s',
                        qr_resp.status_code, qr_resp.text[:500]
                    )
            except Exception as qr_err:
                logger.warning('Erro ao buscar pixQrCode do Asaas: %s', qr_err)

        # ── FALLBACK: tentar extrair da resposta de criação ───────────────────
        if not pix_payload or not pix_qr:
            def find_any(dct, keys):
                for k in keys:
                    if k in dct and dct[k]:
                        return dct[k]
                return ''

            payload_keys = ['pixQrCode', 'pixPayload', 'copyPaste', 'payload', 'text']
            qr_keys      = ['encodedImage', 'qrcode', 'qrcode_base64', 'qrCode', 'qrCodeBase64', 'qr_code']

            if not pix_payload:
                pix_payload = find_any(data, payload_keys)

            if not pix_qr:
                raw_img = find_any(data, qr_keys)
                if raw_img:
                    pix_qr = raw_img if raw_img.startswith('data:') else 'data:image/png;base64,' + raw_img

            pix_block = data.get('pix') or {}
            if pix_block:
                if not pix_payload:
                    pix_payload = find_any(pix_block, payload_keys)
                if not pix_qr:
                    raw_img = find_any(pix_block, qr_keys)
                    if raw_img:
                        pix_qr = raw_img if raw_img.startswith('data:') else 'data:image/png;base64,' + raw_img
                if not copy_paste:
                    copy_paste = pix_block.get('copyPaste', '')

            if not pix_payload and copy_paste:
                pix_payload = copy_paste

        logger.info(
            'asaas_create_pix: payment=%s pix_payload_len=%s pix_qr_len=%s',
            asaas_payment_id, len(pix_payload), len(pix_qr)
        )

        # ── Salvar pagamento local ────────────────────────────────────────────
        # ao criar localmente marcamos sempre como "pending".
        # o Asaas pode retornar outros estados (por exemplo "AUTHORIZED"),
        # mas a transição para paid/cancelled/etc. será feita pelo webhook
        # que processa as notificações posteriores.
        payment = Payment.objects.create(
            usuario=user,
            asaas_payment_id=asaas_payment_id,
            amount=float(amount),
            currency='BRL',
            method='PIX',
            status='pending',
            pix_qr_payload=pix_payload or '',
            pix_qr_image=pix_qr or '',
            raw=data
        )
        logger.debug('Payment record created: id=%s asaas_payment_id=%s', payment.id, payment.asaas_payment_id)

        return Response({
            'payment_id': payment.id,
            'asaas_payment_id': asaas_payment_id,
            'pix_payload': payment.pix_qr_payload,
            'pix_qr': payment.pix_qr_image,
            'copy_paste': copy_paste,
            'status': payment.status,
        })

    except requests.exceptions.ReadTimeout as te:
        logger.error('Asaas request timed out: %s', te)
        return Response({'error': 'asaas_timeout', 'detail': 'Tempo de conexão com Asaas esgotado'}, status=504)
    except requests.exceptions.RequestException as re:
        logger.error('Asaas request exception: %s', re)
        return Response({'error': 'asaas_request_failed', 'detail': str(re)}, status=502)
    except Exception as e:
        logger.exception('Erro criando pagamento Asaas: %s', e)
        return Response({'error': 'internal_error'}, status=500)


@csrf_exempt
@require_http_methods(['POST'])
def asaas_webhook(request):
    """Endpoint público para receber webhooks do Asaas."""
    # verifica segredo se configurado
    secret = getattr(settings, 'ASAAS_WEBHOOK_SECRET', '')
    if not secret:
        # o administrador optou por não validar assinatura;
        # aceita qualquer requisição e evita logar warnings repetidos
        logger.info('Asaas webhook: segredo não configurado, pulando verificação de assinatura')
    else:
        # primary header fields used by Asaas
        sig = request.META.get('HTTP_X_ASAAS_SIGNATURE') or request.META.get('HTTP_X_HOOK_SIGNATURE')
        # some installations send a custom access token header
        if not sig:
            sig = request.META.get('HTTP_ASAAS_ACCESS_TOKEN')
        # some providers (or Postman tests) send token in Authorization header
        if not sig:
            auth = request.META.get('HTTP_AUTHORIZATION') or request.META.get('Authorization')
            if auth:
                # format may be "Token <value>" or bare value
                if auth.lower().startswith('token '):
                    sig = auth.split(' ', 1)[1].strip()
                    logger.debug('Asaas webhook: extracted signature from Authorization header')
                else:
                    sig = auth.strip()
                    logger.debug('Asaas webhook: using raw Authorization header as signature')
        if not sig or sig != secret:
            logger.warning('Asaas webhook: assinatura inválida (%s); headers=%s', sig, {k:v for k,v in request.META.items() if k.startswith('HTTP_')})
            return HttpResponse(status=403)
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except Exception:
        logger.warning('Asaas webhook: payload inválido')
        return HttpResponse(status=400)

    try:
        # Try to determine the real resource id.  Asaas often sends an
        # "evt_..." value in the top-level `id` field while the actual
        # payment identifier lives deeper in the JSON, e.g. payload["payment"]["id"].
        # Prefer those nested values first so we don't mistakenly treat the
        # event id as the payment id.
        resource_id = None
        # nested payment object is common in many event types
        payobj = payload.get('payment') if isinstance(payload, dict) else None
        if isinstance(payobj, dict):
            resource_id = (
                payobj.get('id') or payobj.get('paymentId') or payobj.get('payment_id')
            )
            if resource_id:
                logger.debug('Asaas webhook: resource_id extracted from nested payment object %s', resource_id)

        # fall back to top‑level fields
        if not resource_id:
            resource_id = (
                payload.get('id') or payload.get('object') or payload.get('paymentId')
                or payload.get('idCob') or payload.get('payment_id') or payload.get('idPagamento')
            )
        event = payload.get('event') or payload.get('eventType') or ''

        # always log at info level so we can see this even when debug is off
        logger.info('Asaas webhook: computed resource_id=%s event=%s', resource_id, event)

        if not resource_id:
            logger.warning('Asaas webhook sem resource_id; payload: %s', payload)

        if resource_id:
            payments = Payment.objects.filter(asaas_payment_id=resource_id)
            logger.info('Asaas webhook: payment query for %s returned %s rows (%s)', resource_id, payments.count(), list(payments.values_list('asaas_payment_id', flat=True)))
            if not payments.exists():
                logger.warning('Asaas webhook: nenhum pagamento encontrado para resource_id %s', resource_id)
                # tentativa de fallback usando externalReference presente no payload
                ext = payload.get('externalReference') or payload.get('externalreference')
                if ext:
                    logger.debug('Asaas webhook: procurando por externalReference %s', ext)
                    try:
                        payments = Payment.objects.filter(raw__icontains=str(ext))
                    except Exception:
                        payments = Payment.objects.none()
                    if payments.exists():
                        logger.info('Asaas webhook: correspondência encontrada através de externalReference %s', ext)
                # ainda não achou? já verificamos payobj antes, mas o payload
                # pode ter outros níveis mais profundos, então vamos fazer uma
                # varredura recursiva capturando também valores numéricos.
                if not payments.exists():
                    def _gather_strings(obj):
                        results = []
                        if isinstance(obj, dict):
                            for v in obj.values():
                                results.extend(_gather_strings(v))
                        elif isinstance(obj, list):
                            for v in obj:
                                results.extend(_gather_strings(v))
                        elif isinstance(obj, (str, int, float)):
                            results.append(str(obj))
                        return results
                    candidates = _gather_strings(payload)
                    if candidates:
                        payments = Payment.objects.filter(asaas_payment_id__in=candidates)
                        if payments.exists():
                            logger.info('Asaas webhook: correspondência encontrada via varredura recursiva (%s)', [p.asaas_payment_id for p in payments])
                # after all attempts, if still nothing log full payload for debugging
                if not payments.exists():
                    logger.warning('Asaas webhook: ainda nenhum pagamento correspondeu após todas as tentativas; payload completo: %s', payload)
            if payments.exists():
                for p in payments:
                    # Asaas sometimes uses different fields for status/event.
                    raw_status = None
                    # prefer event when provided (e.g. PAYMENT_RECEIVED)
                    if isinstance(event, str) and event:
                        raw_status = event
                    raw_status = raw_status or payload.get('status') or payload.get('paymentStatus') or payload.get('payment', {}).get('status') or p.status

                    # normalize
                    if isinstance(raw_status, str):
                        raw_status_norm = raw_status.strip().lower()
                    else:
                        raw_status_norm = str(raw_status).lower() if raw_status is not None else p.status

                    # map provider statuses/events to our internal choices
                    paid_indicators = ('paid', 'received', 'confirmed', 'payment_received', 'paymentreceived')
                    pending_indicators = ('pending', 'waiting', 'created')

                    if any(x in raw_status_norm for x in ('payment_received', 'paymentreceived', 'payment-received')) or raw_status_norm in ('received', 'confirmed'):
                        mapped_status = 'paid'
                    elif raw_status_norm in pending_indicators or raw_status_norm.startswith('pending'):
                        mapped_status = 'pending'
                    elif raw_status_norm in ('paid',) or any(x in raw_status_norm for x in ('paid',)):
                        mapped_status = 'paid'
                    else:
                        mapped_status = raw_status_norm

                    changed_to_paid = mapped_status == 'paid' and p.status != 'paid'

                    # set asaas_payment_id if missing
                    try:
                        payobj = payload.get('payment') if isinstance(payload, dict) else None
                        if payobj and isinstance(payobj, dict):
                            pay_id = payobj.get('id') or payobj.get('paymentId')
                            if pay_id and (not p.asaas_payment_id or str(p.asaas_payment_id).strip() == ''):
                                p.asaas_payment_id = str(pay_id)
                            # prefer billingType for method
                            billing = payobj.get('billingType') or payobj.get('billing_type')
                            if billing and (not getattr(p, 'method', None)):
                                p.method = billing
                    except Exception:
                        pass

                    # attempt to extract payment date from payload
                    pago_dt = None
                    try:
                        if isinstance(payobj, dict):
                            for fld in ('paymentDate', 'payment_date', 'confirmedDate', 'creditDate', 'dateCreated'):
                                v = payobj.get(fld)
                                if v:
                                    from django.utils.dateparse import parse_datetime, parse_date
                                    if isinstance(v, str):
                                        parsed_dt = parse_datetime(v)
                                        if parsed_dt:
                                            dt = parsed_dt
                                            # make timezone-aware if naive
                                            try:
                                                if timezone.is_naive(dt):
                                                    dt = timezone.make_aware(dt)
                                            except Exception:
                                                pass
                                        else:
                                            pd = parse_date(v)
                                            if pd:
                                                try:
                                                    dt = timezone.make_aware(datetime.combine(pd, datetime.min.time()))
                                                except Exception:
                                                    dt = datetime.combine(pd, datetime.min.time())
                                        if dt:
                                            pago_dt = dt
                                            break
                    except Exception:
                        pago_dt = None

                    p.status = mapped_status
                    if mapped_status == 'paid' and not p.pago_em:
                        p.pago_em = pago_dt or timezone.now()

                    # store full payload for debugging/audit
                    p.raw = payload

                    p.save()

                    # se pagamento PIX foi pago, criar assinatura automática
                    if changed_to_paid and (str(p.method).upper() == 'PIX' or (isinstance(payobj, dict) and str(payobj.get('billingType') or '').upper() == 'PIX')):
                        # só criar se usuário não tiver assinatura ativa
                        user = p.usuario
                        if user:
                            has_active = Subscription.objects.filter(usuario=user, status='active').exists()
                            if not has_active:
                                # criar assinatura válida de acordo com configuração
                                sub = Subscription.objects.create(
                                    usuario=user,
                                    plano_id=getattr(settings, 'PLAN_ID', None),
                                    status='active',
                                    iniciado_em=timezone.now(),
                                    periodo_termina_em=timezone.now() + timedelta(days=getattr(settings, 'PLAN_DURATION_DAYS', 30)),
                                    dados={'origin_payment': p.asaas_payment_id}
                                )
                                logger.info("Assinatura automática criada para user=%s via payment=%s", user.username, p.id)

        subscription_id = payload.get('subscriptionId') or payload.get('asaasSubscriptionId')
        if subscription_id:
            subs = Subscription.objects.filter(asaas_subscription_id=subscription_id)
            for s in subs:
                s.status = payload.get('status', s.status)
                s.dados = payload
                s.save()

        return HttpResponse(status=200)
    except Exception as e:
        logger.exception('Erro processando webhook Asaas: %s', e)
        return HttpResponse(status=500)


def _get_or_create_musica_by_youtube_id(video_id: str) -> 'Musica | None':
    """Tenta obter uma Musica por youtube_id; se não existir, cria uma entrada rica."""
    if not video_id or not _is_valid_youtube_id(video_id):
        logger.debug(f"youtube_id inválido: {video_id}")
        return None

    existing = Musica.objects.filter(youtube_id=video_id).first()
    if existing:
        return existing

    title = None
    artist_name = None
    duration_seconds = None
    metadata_source = 'none'

    try:
        if _check_connectivity():
            ytmusic = _init_ytmusic()
            info = None
            try:
                if hasattr(ytmusic, 'get_song'):
                    info = ytmusic.get_song(video_id)
                if not info:
                    sr = ytmusic.search(video_id, filter='songs', limit=1)
                    info = (sr[0] if sr else None)
            except Exception as e:
                logger.debug(f"YTMusic não forneceu metadata para {video_id}: {e}")
                info = None

            if isinstance(info, dict):
                metadata_source = 'ytmusic'
                title = info.get('title') or info.get('name')
                if isinstance(info.get('artists'), list) and info.get('artists'):
                    artist_name = info.get('artists')[0].get('name')
                else:
                    artist_name = info.get('artist') or None
                duration_seconds = info.get('duration_seconds') or None
    except Exception as e:
        logger.debug(f"Erro ao usar YTMusic para {video_id}: {e}")

    if not title and yt_dlp is not None:
            try:
                url = f'https://www.youtube.com/watch?v={video_id}'
                
                # Ajuste: Adicionado proxy residencial e suporte a cookies
                opts = {
                    'quiet': True, 
                    'skip_download': True,
                    'cookiefile': _get_cookiefile_path(), # Usa o cookies.txt automaticamente
                }
                
                opts = _build_ydl_opts_js_runtime(opts) or opts
                # guarantee proxy is applied (see above default port 8888 behaviour)
                opts.setdefault('proxy', 'http://127.0.0.1:8888')
                
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if isinstance(info, dict):
                        metadata_source = 'yt_dlp'
                        title = title or info.get('title')
                        duration_seconds = duration_seconds or int(info.get('duration') or 0)
                        artist_name = artist_name or info.get('uploader') or info.get('artist')
            except Exception as e:
                logger.debug(f"yt_dlp fallback falhou para {video_id}: {e}")

    try:
        if artist_name:
            artista_obj, _ = Artista.objects.get_or_create(nome=artist_name)
        else:
            artista_obj, _ = Artista.objects.get_or_create(nome='Artista Desconhecido')

        dur_td = timedelta(seconds=int(duration_seconds)) if duration_seconds else timedelta(seconds=0)

        musica = Musica.objects.create(
            titulo=title or f'Música {video_id}',
            artista=artista_obj,
            duracao=dur_td,
            youtube_id=video_id
        )
        logger.info(f"Criada Musica id={musica.id} youtube_id={video_id} source={metadata_source}")
        return musica
    except Exception as e:
        logger.exception(f"Falha ao criar Musica para youtube_id={video_id}: {e}")
        return None


# ============================================================================
# VIEWS HTML
# ============================================================================

@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
def register(request):
    """Registro de novo usuário."""
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL or '/')

    # use custom form to ensure email uniqueness
    form = CustomUserCreationForm(request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            user = form.save()
            # form already includes email and has validated uniqueness
            # but keep the explicit assignment to trigger any side-effects
            email = form.cleaned_data.get('email') or request.POST.get('email')
            if email:
                user.email = email
                user.save(update_fields=['email'])

            # create / update profile with submitted fields
            from .models import UserProfile
            profile, _ = UserProfile.objects.get_or_create(usuario=user)
            profile.cpf_cnpj = request.POST.get('cpf_cnpj', profile.cpf_cnpj or '')
            profile.phone = request.POST.get('phone', profile.phone or '')
            profile.mobile_phone = request.POST.get('mobile_phone', profile.mobile_phone or '')
            profile.address = request.POST.get('address', profile.address or '')
            profile.address_number = request.POST.get('address_number', profile.address_number or '')
            profile.complement = request.POST.get('complement', profile.complement or '')
            profile.city = request.POST.get('city', profile.city or '')
            profile.state = request.POST.get('state', profile.state or '')
            profile.postal_code = request.POST.get('postal_code', profile.postal_code or '')
            profile.save()

            # full name handling (optional)
            full_name = request.POST.get('full_name')
            if full_name is not None:
                try:
                    parts = (full_name or '').strip().split(' ', 1)
                    user.first_name = parts[0] if parts else ''
                    user.last_name = parts[1] if len(parts) > 1 else ''
                    user.save(update_fields=['first_name','last_name'])
                except Exception:
                    pass

            # Ensure backend is set when multiple authentication backends exist
            # use the first backend from settings or default to ModelBackend
            backend = None
            backends = getattr(settings, 'AUTHENTICATION_BACKENDS', [])
            if backends:
                backend = backends[0]
            else:
                backend = 'django.contrib.auth.backends.ModelBackend'
            user.backend = backend
            auth_login(request, user, backend=backend)
            # message shown on next page load; using large congratulatory text per request
            # Determine how many trial days were granted (lookup plan)
            trial_len = 1
            try:
                trial_plan = Plan.objects.filter(slug__startswith='trial').first()
                if trial_plan and hasattr(trial_plan, 'trial_days'):
                    trial_len = trial_plan.trial_days or trial_len
            except Exception:
                pass
            messages.success(request, f"Parabéns! Você está usando {trial_len} dia{'s' if trial_len!=1 else ''} de teste grátis. Aproveite e curta suas músicas!")
            next_url = request.POST.get('next') or request.GET.get('next') or settings.LOGIN_REDIRECT_URL or '/'
            return redirect(next_url)

    return render(request, 'core/register.html', {'form': form})


@login_required
@require_http_methods(['GET', 'POST'])
def profile_edit(request):
    """Editar perfil do usuário."""
    user = request.user
    profile = getattr(user, 'profile', None)

    if request.method == 'POST':
        full_name = request.POST.get('full_name')
        email = request.POST.get('email')
        if full_name is not None:
            try:
                parts = (full_name or '').strip().split(' ', 1)
                user.first_name = parts[0] if parts else ''
                user.last_name = parts[1] if len(parts) > 1 else ''
            except Exception:
                pass
        if email is not None:
            user.email = email
        user.save()

        if profile is None:
            from .models import UserProfile
            profile = UserProfile.objects.create(usuario=user)

        profile.cpf_cnpj = request.POST.get('cpf_cnpj', profile.cpf_cnpj or '')
        profile.phone = request.POST.get('phone', profile.phone or '')
        profile.mobile_phone = request.POST.get('mobile_phone', profile.mobile_phone or '')
        profile.address = request.POST.get('address', profile.address or '')
        profile.address_number = request.POST.get('address_number', profile.address_number or '')
        profile.complement = request.POST.get('complement', profile.complement or '')
        profile.city = request.POST.get('city', profile.city or '')
        profile.state = request.POST.get('state', profile.state or '')
        profile.postal_code = request.POST.get('postal_code', profile.postal_code or '')
        profile.save()

        messages.success(request, 'Perfil atualizado com sucesso')
        return redirect('home')

    # include subscription info for profile page
    try:
        assinaturas = Subscription.objects.filter(usuario=user, status='active').order_by('-criado_em')[:20]
    except Exception:
        assinaturas = []
    assinatura_ativa = assinaturas[0] if assinaturas else None

    return render(request, 'core/edit_profile.html', {
        'user': user,
        'profile': profile,
        'assinatura_ativa': assinatura_ativa,
    })


from django.contrib.auth import logout as django_logout


def logout_view(request):
    """Efetua logout do usuário."""
    try:
        django_logout(request)
    except Exception:
        try:
            auth_logout(request)
        except Exception:
            pass

    # show confirmation message and redirect
    messages.info(request, 'Você saiu da sua conta')
    next_url = request.GET.get('next') or getattr(settings, 'LOGOUT_REDIRECT_URL', '/') or '/'
    return redirect(next_url)


# helper for the page default dataset
import json
from pathlib import Path


def _load_explorar_default() -> dict:
    """Attempt to read the JSON file generated by the helper script.

    The script `scripts/fetch_featured.py` writes to `musicas.json` in the
    project root; if that file exists and contains valid JSON we return it,
    otherwise an empty dict is returned. The view will inject the result into
    the template so the JavaScript can render it without a network request.
    """
    path = Path(settings.BASE_DIR) / "musicas.json"
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

    messages.info(request, 'Você saiu da sua conta')
    next_url = request.GET.get('next') or getattr(settings, 'LOGOUT_REDIRECT_URL', '/') or '/'
    return redirect(next_url)


@ensure_csrf_cookie
def buscar_musicas_html(request):
    """View para renderizar página de busca."""
    results = []
    albums = []
    cookie_present = False
    query = request.GET.get('q', '')

    if query:
        if not _check_connectivity():
            results = []
            albums = []
        else:
            try:
                ytmusic = _init_ytmusic()

                normalized_query = (query or '').strip().lower()
                cache_key = 'search_' + hashlib.sha1(normalized_query.encode('utf-8')).hexdigest()
                cached_results = cache.get(cache_key)

                if cached_results:
                    results, albums = cached_results
                else:
                    yt_results = ytmusic.search(query, filter="songs", limit=10)
                    results = []
                    for item in yt_results:
                        normalized = _normalize_track(item)
                        if normalized:
                            results.append(normalized)

                    try:
                        yt_albums = ytmusic.search(query, filter="albums", limit=6)
                        albums = []
                        for item in yt_albums:
                            normalized = _normalize_album(item)
                            if normalized:
                                albums.append(normalized)

                        albums = _fetch_album_details_parallel(albums)
                    except Exception as e:
                        logger.error(f"Erro ao buscar álbuns: {e}")
                        albums = []

                    cache.set(cache_key, (results, albums), SEARCH_CACHE_TIMEOUT)

            except Exception as e:
                logger.error(f"Erro na busca: {e}")
                results = []
                albums = []

    cookie_present = _get_cookiefile_path() is not None

    featured = {}
    musicas_list = []
    explorar_default = {}

    if not query:
        featured = _get_featured_albums()
        musicas_list = _get_local_musicas()
        explorar_default = _load_explorar_default()

    playlists_usuario = []
    favoritos_usuario = []
    if request.user.is_authenticated:
        playlists_queryset = Playlist.objects.filter(usuario=request.user).annotate(
            total_musicas=Count('musicas')
        ).order_by('-criada_em')

        playlists_usuario = []
        for playlist in playlists_queryset:
            # collect up to four thumbnail URLs from the first tracks
            covers = []
            items = playlist.playlistitem_set.select_related('musica').order_by('ordem')[:4]
            for item in items:
                m = item.musica
                url = ''
                # prefer album cover if available
                if m.album and getattr(m.album, 'capa', None):
                    url = m.album.capa.url if hasattr(m.album.capa, 'url') else m.album.capa
                elif m.youtube_id:
                    url = f"https://i.ytimg.com/vi/{m.youtube_id}/hqdefault.jpg"
                else:
                    url = m.capa or ''
                covers.append(url)
            # pad to always have four entries (empty strings fill with placeholder)
            while len(covers) < 4:
                covers.append('')

            playlists_usuario.append({
                'id': playlist.id,
                'nome': playlist.nome,
                'descricao': playlist.descricao,
                'criada_em': playlist.criada_em.isoformat() if playlist.criada_em else None,
                'visibilidade': playlist.visibilidade,
                'total_musicas': getattr(playlist, 'total_musicas', 0),
                'capa': playlist.capa.url if playlist.capa else None,
                'is_shared': getattr(playlist, 'is_shared', False),
                'share_uuid': str(getattr(playlist, 'share_uuid', '')) if getattr(playlist, 'share_uuid', None) else None,
                'covers': covers,
            })

        from .serializers import FavoritoSerializer
        fav_qs = Favorito.objects.filter(usuario=request.user).select_related('musica').order_by('-adicionado_em')
        try:
            favoritos_usuario = FavoritoSerializer(fav_qs, many=True, context={'request': request}).data
        except Exception:
            favoritos_usuario = []

    try:
        json.dumps(playlists_usuario)
    except TypeError:
        try:
            playlists_usuario = PlaylistSerializer(playlists_queryset, many=True, context={'request': request}).data
        except Exception:
            playlists_usuario = []

    # also supply subscription state so profile tab can display it
    assinatura_ativa = None
    if request.user.is_authenticated:
        try:
            assinaturas = Subscription.objects.filter(usuario=request.user, status='active').order_by('-criado_em')[:20]
            assinatura_ativa = assinaturas[0] if assinaturas else None
        except Exception:
            assinatura_ativa = None

    # prepare genres JSON so frontend doesn't have to request it later
    try:
        generos_qs = Genero.objects.all().order_by('nome')
        # basic serialized data (id, nome, slug, descricao, icone, cor)
        from .serializers import GeneroSerializer
        base = GeneroSerializer(generos_qs, many=True).data
        # extra metadata that used to live in the template (emoji, gradient, image)
        from django.templatetags.static import static
        # build metadata map; only include json_url if file actually exists so client
        # won't try to fetch a non‑existent resource and fall back to API.  we use
        # staticfiles.finders in case static files are collected or stored elsewhere.
        from django.contrib.staticfiles import finders
        def maybe_static(path):
            url = static(path)
            if finders.find(path):
                return url
            return None

        GENRE_META = {
            'pop':        {'emoji':'✨','gradient':'linear-gradient(135deg,#ec4899,#f97316)','image':static('popcapa.jpg'),'json_url':maybe_static('pop.json')},
            'rock':       {'emoji':'🎸','gradient':'linear-gradient(135deg,#1f2937,#dc2626)','image':static('rock_capa.jpg'),'json_url':maybe_static('rock.json')},
            'sertanejo':  {'emoji':'🤠','gradient':'linear-gradient(135deg,#d97706,#b45309)','image':static('sertanejocapa.jpg'),'json_url':maybe_static('sertanejo.json')},
            'funk':       {'emoji':'🔥','gradient':'linear-gradient(135deg,#16a34a,#15803d)','image':static('funkcapa.jpg'),'json_url':maybe_static('funk.json')},
            'jazz':       {'emoji':'🎷','gradient':'linear-gradient(135deg,#1d4ed8,#1e3a8a)','image':static('jazzcapa.jpg'),'json_url':maybe_static('jazz.json')},
            'eletrônico': {'emoji':'⚡','gradient':'linear-gradient(135deg,#0ea5e9,#7c3aed)','image':static('eletrocapa.jpg'),'json_url':maybe_static('eletrônico.json')},
            'r&b':        {'emoji':'🎤','gradient':'linear-gradient(135deg,#7c3aed,#db2777)','image':static('rbcapa.jpg'),'json_url':maybe_static('r&b.json')},
            'pagode':     {'emoji':'🥁','gradient':'linear-gradient(135deg,#ea580c,#c2410c)','image':static('pagode.jpg'),'json_url':maybe_static('pagode.json')},
            'hip-hop':    {'emoji':'🎧','gradient':'linear-gradient(135deg,#374151,#111827)','image':static('hiphop.jpg'),'json_url':maybe_static('hip-hop.json')},
            'mpb':        {'emoji':'🌿','gradient':'linear-gradient(135deg,#059669,#047857)','image':static('mpb.jpg'),'json_url':maybe_static('mpb.json')},
            'clássico':   {'emoji':'🎻','gradient':'linear-gradient(135deg,#92400e,#78350f)','image':static('classicacapa.jpg'),'json_url':maybe_static('clássico.json')},
            'gospel':     {'emoji':'🙏','gradient':'linear-gradient(135deg,#6d28d9,#4c1d95)','image':static('gospel.jpg'),'json_url':maybe_static('gospel.json')},
        }
        # attach metadata to each serialized genre if available
        for g in base:
            meta = GENRE_META.get(g.get('slug'))
            if meta:
                g.update(meta)
        generos_json = json.dumps(base)
    except Exception:
        generos_json = '[]'

    # determine a footer link (e.g. banner or registration URL)
    from .models import Banner
    footer_link = None
    try:
        b = Banner.objects.filter(is_active=True).exclude(link__isnull=True).order_by('ordem').first()
        if b and b.link:
            footer_link = b.link
    except Exception:
        footer_link = None

    if not footer_link:
        from django.urls import reverse
        footer_link = reverse('register')

    # banner used in the "explorar" hero section; can be None
    try:
        explore_banner = Banner.objects.filter(is_active=True).order_by('ordem').first()
    except Exception:
        explore_banner = None

    # optional file that users can download via profile
    try:
        af = AppDownload.objects.filter(is_active=True).order_by('-created_at').first()
        if af:
            app_file = {
                'id': af.id,
                'titulo': af.titulo,
                'arquivo_url': af.arquivo.url if af.arquivo and hasattr(af.arquivo, 'url') else '',
                'created_at': af.created_at.isoformat(),
            }
        else:
            app_file = None
    except Exception:
        app_file = None

    return render(request, 'core/buscar_musicas.html', {
        'results': results,
        'albums': albums,
        'featured': featured,
        'musicas_list': musicas_list,
        'explorar_default': explorar_default,
        'musicas_count': len(musicas_list),
        'musicas_initial_limit': getattr(settings, 'MUSICAS_INITIAL_LIMIT', 5),
        'cookie_present': cookie_present,
        'query': query,
        'playlists_usuario': playlists_usuario,
        'favoritos_usuario': favoritos_usuario,
        'assinatura_ativa': assinatura_ativa,
        'generos_json': generos_json,
        'promo_timeout': getattr(settings, 'PROMO_TIMEOUT', 30),
        'footer_link': footer_link,
        'explore_banner': explore_banner,
        'app_file': app_file,
    })


@login_required
def assinatura(request):
    """Página separada de assinatura (mostra opção de pagamento PIX)."""
    profile = getattr(request.user, 'profile', None)
    # buscar últimos pagamentos e assinaturas do usuário
    try:
        pagamentos = Payment.objects.filter(usuario=request.user).order_by('-criado_em')[:50]
    except Exception:
        pagamentos = []

    # verificar se há pagamento PIX pendente
    try:
        pending_payment_qs = Payment.objects.filter(usuario=request.user, method='PIX')
        pending_payment_qs = pending_payment_qs.exclude(status__in=['paid','cancelled','failed'])
        pending_payment = pending_payment_qs.order_by('-criado_em').first()
        has_pending = bool(pending_payment)
    except Exception:
        pending_payment = None
        has_pending = False

    try:
        assinaturas = Subscription.objects.filter(usuario=request.user, status='active').order_by('-criado_em')[:20]
    except Exception:
        assinaturas = []

    # take most recent active subscription (if any) for simple checks
    assinatura_ativa = assinaturas[0] if assinaturas else None

    # fetch available plans from DB to display on the assinaturas page
    try:
        # exclude free plans (price 0) from the list shown to users
        plans = list(Plan.objects.filter(price__gt=0).order_by('-is_active', 'price'))
    except Exception:
        plans = []

    # if a plan slug is provided as GET param, select it for the template
    selected_plan = None
    sel_slug = request.GET.get('plan')
    if sel_slug:
        try:
            selected_plan = Plan.objects.filter(slug=sel_slug).first()
        except Exception:
            selected_plan = None

    selected_plan_price = str(selected_plan.price) if selected_plan else None
    selected_plan_label = f"R$ {selected_plan.price}/mês" if selected_plan else None
    selected_plan_name = selected_plan.name if selected_plan else None

    return render(request, 'core/assinatura.html', {
        'profile': profile,
        'pagamentos': pagamentos,
        'assinaturas': assinaturas,
        'assinatura_ativa': assinatura_ativa,
        'plans': plans,
        'selected_plan': selected_plan,
        'SELECTED_PLAN_PRICE': selected_plan_price,
        'SELECTED_PLAN_LABEL': selected_plan_label,
        'SELECTED_PLAN_NAME': selected_plan_name,
        'has_pending_payment': has_pending,
        'pending_payment': pending_payment,
    })


from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

@login_required
def profile_apps(request):
    """Lista de arquivos de aplicativo disponíveis (HTML)."""
    apps = AppDownload.objects.filter(is_active=True).order_by('-created_at')
    return render(request, 'core/profile_apps.html', {'apps': apps})

# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def app_downloads_api(request):
    """Retorna a lista de `AppDownload` ativos em formato JSON.

    Esse endpoint é usado pelo aplicativo móvel para exibir todas as
    versões/disponibilidades e permitir o download.
    """
    apps = AppDownload.objects.filter(is_active=True).order_by('-created_at')
    serializer = AppDownloadSerializer(apps, many=True, context={'request': request})
    return Response(serializer.data)


def _get_featured_albums() -> Dict:
    """Obtém álbuns em destaque (com cache)."""
    cache_key = 'featured_albums'
    cached = cache.get(cache_key)
    if cached:
        return cached

    featured = {}
    featured_file = getattr(settings, 'YT_FEATURED_FILE', None)

    max_per_genre = int(getattr(settings, 'YT_FEATURED_MAX_PER_GENRE', 6))
    allow_fallback = bool(getattr(settings, 'YT_FEATURED_ALLOW_FALLBACK', False))

    if not featured_file:
        base_dir = getattr(settings, 'BASE_DIR', None)
        featured_file = os.path.join(base_dir, 'featured.json') if base_dir else os.path.join(os.getcwd(), 'featured.json')

    try:
        if featured_file and os.path.exists(str(featured_file)):
            with open(str(featured_file), 'r', encoding='utf-8') as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    featured = data
                    cache.set(cache_key, featured, CACHE_TIMEOUT)
                    return featured
    except Exception as e:
        logger.warning(f"Erro ao carregar featured.json: {e}")

    if not allow_fallback:
        cache.set(cache_key, featured, CACHE_TIMEOUT)
        return featured

    if _check_connectivity():
        genres = [
            {'name': 'funk', 'limit': max_per_genre},
            {'name': 'mpb', 'limit': max_per_genre},
            {'name': 'sertanejo', 'limit': max_per_genre},
            {'name': 'rock', 'limit': max_per_genre}
        ]
        ytmusic = _init_ytmusic()

        for genre_info in genres:
            genre = genre_info['name']
            limit = genre_info['limit']
            try:
                raw = ytmusic.search(genre, filter='albums', limit=limit) or []
                albums_for_genre = []

                for a in raw:
                    album = _normalize_album(a)
                    if album:
                        albums_for_genre.append(album)

                albums_for_genre = _fetch_album_details_parallel(albums_for_genre)
                featured[genre] = albums_for_genre

            except Exception as e:
                logger.warning(f"Erro ao buscar álbuns para gênero {genre}: {e}")
                featured[genre] = []

    cache.set(cache_key, featured, CACHE_TIMEOUT)
    return featured


def _get_local_musicas() -> List[Dict]:
    """Carrega músicas do arquivo local musicas.json."""
    musicas_list = []

    try:
        musicas_file = os.path.join(
            getattr(settings, 'BASE_DIR', os.getcwd()),
            'musicas.json'
        )

        if musicas_file and os.path.exists(str(musicas_file)):
            cache_key = 'local_musicas'
            cached = cache.get(cache_key)

            if cached:
                return cached

            with open(str(musicas_file), 'r', encoding='utf-8') as mf:
                mdata = json.load(mf)

                if isinstance(mdata, dict):
                    for artista, itens in mdata.items():
                        for t in itens:
                            track = {
                                'id': t.get('videoId') or t.get('id') or '',
                                'title': t.get('titulo') or t.get('title') or '',
                                'artists': t.get('artista') or artista,
                                'thumb': t.get('capa') or t.get('thumbnail') or '',
                                'duration': t.get('duracao') or t.get('duration') or '',
                                'country': t.get('pais') or 'br'
                            }
                            if track['id']:
                                musicas_list.append(track)

            cache.set(cache_key, musicas_list, CACHE_TIMEOUT)
            return musicas_list

    except Exception as e:
        logger.warning(f"Erro ao carregar musicas.json: {e}")

    return []


# ============================================================================
# VIEWS DE DADOS LOCAIS (BANCO DE DADOS)
# ============================================================================

@api_view(['GET'])
def artistas_list_api(request):
    """Lista todos os artistas.

    Parâmetros de query string opcionais:
    - genero: slug de gênero para filtrar por gênero
    - pais: substring de país
    - nome ou q: busca por nome
    """
    artistas = Artista.objects.all().order_by('nome')

    genero = request.GET.get('genero')
    if genero:
        artistas = artistas.filter(generos__slug=genero)

    pais = request.GET.get('pais')
    if pais:
        artistas = artistas.filter(pais__icontains=pais)

    nome = request.GET.get('nome') or request.GET.get('q')
    if nome:
        artistas = artistas.filter(nome__icontains=nome)

    serializer = ArtistaSerializer(artistas, many=True, context={'request': request})
    return Response(serializer.data)


@api_view(['GET'])
def trending_list_api(request):
    """Retorna JSON de músicas em alta ordenadas pela tabela TrendingMusic."""
    entries = TrendingMusic.objects.all().order_by('-added_at')
    serializer = TrendingMusicSerializer(entries, many=True, context={'request': request})
    return Response(serializer.data)

    page = int(request.GET.get('page', 1))
    page_size = int(request.GET.get('page_size', 20))

    paginator = Paginator(artistas, page_size)
    artistas_page = paginator.get_page(page)

    serializer = ArtistaSerializer(artistas_page, many=True, context={'request': request})

    return Response({
        'artists': serializer.data,
        'total_pages': paginator.num_pages,
        'current_page': page,
        'total_count': paginator.count
    })


@api_view(['GET'])
def artista_detail_api(request, artista_id):
    """Detalhes de um artista."""
    artista = get_object_or_404(Artista, id=artista_id)
    serializer = ArtistaSerializer(artista, context={'request': request})

    albuns = Album.objects.filter(artista=artista).order_by('-ano')
    musicas_populares = Musica.objects.filter(artista=artista).order_by('-visualizacoes')[:10]

    data = serializer.data
    data['albuns'] = AlbumSerializer(albuns, many=True, context={'request': request}).data
    data['musicas_populares'] = MusicaSerializer(musicas_populares, many=True, context={'request': request}).data

    return Response(data)


@api_view(['GET'])
def albuns_list_api(request):
    """Lista todos os álbuns."""
    albuns = Album.objects.all().select_related('artista').order_by('-ano', 'titulo')

    artista_id = request.GET.get('artista')
    if artista_id:
        albuns = albuns.filter(artista_id=artista_id)

    genero = request.GET.get('genero')
    if genero:
        albuns = albuns.filter(generos__slug=genero)

    ano = request.GET.get('ano')
    if ano:
        albuns = albuns.filter(ano=ano)

    tipo = request.GET.get('tipo')
    if tipo:
        albuns = albuns.filter(tipo=tipo)

    page = int(request.GET.get('page', 1))
    page_size = int(request.GET.get('page_size', 20))

    paginator = Paginator(albuns, page_size)
    albuns_page = paginator.get_page(page)

    serializer = AlbumSerializer(albuns_page, many=True, context={'request': request})

    return Response({
        'albums': serializer.data,
        'total_pages': paginator.num_pages,
        'current_page': page,
        'total_count': paginator.count
    })


@api_view(['GET'])
def album_detail_api(request, album_id):
    """Detalhes de um álbum."""
    try:
        pk = int(str(album_id))
    except (ValueError, TypeError):
        pk = None

    if pk is not None:
        album = get_object_or_404(Album.objects.select_related('artista'), id=pk)
        serializer = AlbumSerializer(album, context={'request': request})
        musicas = Musica.objects.filter(album=album).order_by('disc_number', 'track_number')
        data = serializer.data
        data['musicas'] = MusicaSerializer(musicas, many=True, context={'request': request}).data
        return Response(data)

    cache_key = f'album_details_{album_id}'
    cached = cache.get(cache_key)
    if cached:
        return Response(cached)

    try:
        if not _check_connectivity():
            raise Http404("Conectividade com YouTube Music indisponível")

        logger.debug(f"Buscando detalhes do álbum {album_id} via YTMusic")
        client = _init_ytmusic()
        details = client.get_album(album_id)
        if not details:
            raise Http404("Álbum não encontrado")

        tracks = details.get('tracks') or details.get('songs') or []
        normalized_tracks = []
        for t in tracks:
            nt = _normalize_track(t)
            if nt:
                normalized_tracks.append(nt)

        try:
            prefetch = request.GET.get('prefetch') == '1'
            prefetch_limit = int(request.GET.get('prefetch_limit', 1))
        except Exception:
            prefetch = False
            prefetch_limit = 1

        if prefetch and normalized_tracks:
            from concurrent.futures import ThreadPoolExecutor
            ids_to_prefetch = [t.get('id') for t in normalized_tracks if t.get('id')][:prefetch_limit]
            if ids_to_prefetch:
                with ThreadPoolExecutor(max_workers=2) as ex:
                    futures = {ex.submit(_extract_stream_url, vid, True): vid for vid in ids_to_prefetch}
                    for fut in futures:
                        vid = futures[fut]
                        try:
                            url = fut.result(timeout=10)
                            if url:
                                for t in normalized_tracks:
                                    if t.get('id') == vid:
                                        t['audio_url'] = url
                                        t['streamable'] = True
                                        break
                        except Exception as e:
                            logger.debug(f"Prefetch stream falhou para {vid}: {e}")

        resp = {
            # default to the incoming identifier but we will overwrite if we
            # manage to persist the album in the database.
            'id': album_id,
            'title': details.get('title') or details.get('name') or '',
            'artist': '',
            'year': details.get('year', ''),
            'tracks': normalized_tracks,
            'musicas': normalized_tracks,
            'faixas': normalized_tracks,
            'track_count': len(normalized_tracks),
        }

        try:
            artist_name = ''
            if details.get('artists') and isinstance(details.get('artists'), list):
                artist_name = details.get('artists')[0].get('name') or ''
            if not artist_name:
                artist_name = details.get('artist') or ''
            resp['artist'] = artist_name

            year_val = details.get('year') or ''
            try:
                year_int = int(str(year_val)) if str(year_val).isdigit() else None
            except Exception:
                year_int = None

            album_title = details.get('title') or ''

            album_db = None
            if album_title and artist_name:
                album_db = Album.objects.filter(
                    titulo__iexact=album_title,
                    artista__nome__iexact=artist_name
                ).select_related('artista').first()

            if not album_db:
                if artist_name:
                    artista_obj, _ = Artista.objects.get_or_create(nome=artist_name)
                else:
                    artista_obj, _ = Artista.objects.get_or_create(nome='Artista Desconhecido')

                album_kwargs = {
                    'titulo': album_title or f'Album {album_id}',
                    'artista': artista_obj,
                    'descricao': details.get('description') or details.get('wiki') or '',
                    'ano': year_int if year_int else timezone.now().year,
                }
                album_db = Album.objects.create(**album_kwargs)
            # ensure the response id points to the real database object
            if album_db:
                resp['id'] = album_db.id
                # sharing disabled – we no longer expose share metadata

            for idx, t in enumerate(normalized_tracks):
                try:
                    vid = t.get('id')
                    existing = None
                    if vid:
                        existing = Musica.objects.filter(youtube_id=vid).first()

                    if existing:
                        if existing.album is None:
                            existing.album = album_db
                            existing.save(update_fields=['album'])
                        continue

                    dur_secs = t.get('duration_seconds') or 0
                    dur_td = timedelta(seconds=int(dur_secs)) if dur_secs else timedelta(seconds=0)
                    track_number = t.get('track_number') or (idx + 1)

                    Musica.objects.create(
                        titulo=t.get('title') or f'Track {idx+1}',
                        artista=album_db.artista,
                        album=album_db,
                        duracao=dur_td,
                        track_number=track_number,
                        youtube_id=vid or ''
                    )
                except Exception as e:
                    logger.debug(f"Erro ao criar faixa local para album {album_id}: {e}")

            try:
                album_db.update_stats()
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"Falha ao persistir álbum {album_id} no DB: {e}")

        cache.set(cache_key, resp, ALBUM_CACHE_TIMEOUT)
        return Response(resp)
    except Http404:
        raise
    except Exception as e:
        logger.exception(f"Erro ao obter detalhes do álbum {album_id}")
        raise


@api_view(['GET'])
def musicas_list_api(request):
    """Lista todas as músicas."""
    musicas = Musica.objects.all().select_related('artista', 'album').order_by('titulo')

    artista_id = request.GET.get('artista')
    if artista_id:
        musicas = musicas.filter(artista_id=artista_id)

    album_id = request.GET.get('album')
    if album_id:
        musicas = musicas.filter(album_id=album_id)

    genero = request.GET.get('genero')
    if genero:
        musicas = musicas.filter(generos__slug=genero)

    busca = request.GET.get('q')
    if busca:
        musicas = musicas.filter(
            Q(titulo__icontains=busca) |
            Q(artista__nome__icontains=busca) |
            Q(album__titulo__icontains=busca)
        )

    ordenar_por = request.GET.get('sort', 'titulo')
    if ordenar_por == 'visualizacoes':
        musicas = musicas.order_by('-visualizacoes')
    elif ordenar_por == 'likes':
        musicas = musicas.order_by('-likes')
    elif ordenar_por == 'recentes':
        musicas = musicas.order_by('-data_lancamento')
    else:
        musicas = musicas.order_by('titulo')

    page = int(request.GET.get('page', 1))
    page_size = int(request.GET.get('page_size', 20))

    paginator = Paginator(musicas, page_size)
    musicas_page = paginator.get_page(page)

    serializer = MusicaSerializer(musicas_page, many=True, context={'request': request})

    return Response({
        'songs': serializer.data,
        'total_pages': paginator.num_pages,
        'current_page': page,
        'total_count': paginator.count
    })


@api_view(['GET'])
def musica_detail_api(request, musica_id):
    """Detalhes de uma música."""
    musica = get_object_or_404(Musica.objects.select_related('artista', 'album'), id=musica_id)

    musica.increment_views()

    serializer = MusicaSerializer(musica, context={'request': request})

    data = serializer.data
    if request.user.is_authenticated:
        data['is_liked'] = Favorito.objects.filter(
            usuario=request.user, musica=musica
        ).exists()

    return Response(data)


@api_view(['GET'])
def musicas_by_ids_api(request):
    """Retorna músicas por lista de IDs."""
    ids_str = request.GET.get('ids', '')
    if not ids_str:
        return Response({'success': False, 'musicas': []})

    try:
        tokens = [t.strip() for t in ids_str.split(',') if t.strip()]
        numeric_ids = [int(t) for t in tokens if t.isdigit()]
        youtube_ids = [t for t in tokens if not t.isdigit()]

        qs = Musica.objects.none()
        if numeric_ids and youtube_ids:
            qs = Musica.objects.filter(Q(id__in=numeric_ids) | Q(youtube_id__in=youtube_ids))
        elif numeric_ids:
            qs = Musica.objects.filter(id__in=numeric_ids)
        elif youtube_ids:
            qs = Musica.objects.filter(youtube_id__in=youtube_ids)

        qs = qs.select_related('artista', 'album')

        by_pk = {str(m.id): m for m in qs}
        by_yt = {m.youtube_id: m for m in qs if m.youtube_id}

        ordered = []
        for tok in tokens:
            if tok.isdigit() and tok in by_pk:
                ordered.append(by_pk[tok])
            elif tok in by_yt:
                ordered.append(by_yt[tok])
            else:
                continue

        serializer = MusicaSerializer(ordered, many=True, context={'request': request})
        return Response({'success': True, 'musicas': serializer.data})
    except Exception as e:
        logger.exception(f"Erro ao buscar músicas por IDs: {e}")
        return Response({'success': False, 'error': str(e)}, status=500)


# ============================================================================
# VIEWS DE PLAYLISTS
# ============================================================================

@csrf_exempt
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def playlists_api(request):
    """Lista playlists do usuário ou cria nova."""
    if request.method == 'GET':
        playlists = Playlist.objects.filter(usuario=request.user).annotate(
            total_musicas=Count('musicas')
        ).order_by('-criada_em')

        serializer = PlaylistSerializer(playlists, many=True, context={'request': request})
        return Response(serializer.data)

    elif request.method == 'POST':
        nome = request.data.get('nome')
        descricao = request.data.get('descricao', '')
        visibilidade = request.data.get('visibilidade', 'private')

        if not nome:
            return Response({'success': False, 'error': 'Nome é obrigatório'}, status=400)

        playlist = Playlist.objects.create(
            nome=nome,
            descricao=descricao,
            usuario=request.user,
            visibilidade=visibilidade
        )

        serializer = PlaylistSerializer(playlist, context={'request': request})
        return Response({
            'success': True,
            'playlist_id': playlist.id,
            'playlist': serializer.data
        }, status=201)


@csrf_exempt
@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def playlist_detail_api(request, playlist_id):
    """Detalhes, atualização ou exclusão de playlist."""
    playlist = get_object_or_404(Playlist, id=playlist_id)

    if playlist.usuario != request.user and playlist.visibilidade != 'public':
        if request.user not in playlist.colaboradores.all():
            return Response({'error': 'Sem permissão'}, status=403)

    if request.method == 'GET':
        serializer = PlaylistDetailSerializer(playlist, context={'request': request})
        return Response(serializer.data)

    elif request.method == 'PUT':
        if playlist.usuario != request.user and request.user not in playlist.colaboradores.all():
            return Response({'error': 'Sem permissão'}, status=403)

        playlist.nome = request.data.get('nome', playlist.nome)
        playlist.descricao = request.data.get('descricao', playlist.descricao)
        playlist.visibilidade = request.data.get('visibilidade', playlist.visibilidade)
        playlist.save()

        serializer = PlaylistDetailSerializer(playlist, context={'request': request})
        return Response(serializer.data)

    elif request.method == 'DELETE':
        if playlist.usuario != request.user:
            return Response({'error': 'Sem permissão'}, status=403)

        playlist.delete()
        return Response({'success': True, 'message': 'Playlist deletada'})


@api_view(['GET'])
@permission_classes([AllowAny])
def shared_playlist_view(request, share_uuid):
    """View pública para playlist compartilhada por UUID.

    Supports both HTML and JSON output. When format=json or an AJAX
    request is detected, the playlist and normalized track data are
    returned as JSON; otherwise the normal template is rendered with the
    same information embedded for client-side scripts.
    """
    try:
        uuid_val = uuid.UUID(str(share_uuid))
    except Exception:
        raise Http404()

    playlist = get_object_or_404(Playlist, share_uuid=uuid_val, is_shared=True)
    if playlist.share_expires_at and playlist.share_expires_at < timezone.now():
        raise Http404()

    items = PlaylistItem.objects.filter(playlist=playlist).select_related('musica').order_by('ordem')
    tracks = [it.musica for it in items]

    # normalize for JS / API consumers
    normalized = []
    for t in tracks:
        thumb = ''
        if getattr(t, 'thumb', None):
            thumb = t.thumb
        elif getattr(t, 'thumbnail', None):
            thumb = t.thumbnail
        elif getattr(t, 'youtube_id', None):
            thumb = f'https://i.ytimg.com/vi/{t.youtube_id}/hqdefault.jpg'
        elif getattr(t, 'capa', None):
            thumb = t.capa.url if hasattr(t.capa, 'url') else t.capa
        # convert artist object to string if necessary
        artist_val = ''
        if getattr(t, 'artista', None):
            artist_val = str(t.artista)
        elif getattr(t, 'artist', None):
            artist_val = str(t.artist)
        normalized.append({
            'id': str(t.id),
            'titulo': t.titulo or t.title or '',
            'artista': artist_val,
            'youtube_id': t.youtube_id or '',
            'capa': thumb,
        })

    # respond with JSON if requested (DRF Request has no is_ajax)
    wants_json = request.GET.get('format') == 'json' or request.META.get('HTTP_X_REQUESTED_WITH') == 'XMLHttpRequest'
    if wants_json:
        return Response({'playlist': {
            'id': playlist.id,
            'nome': playlist.nome,
            'descricao': playlist.descricao,
            'usuario': str(playlist.usuario) if playlist.usuario else None,
            'total_musicas': len(tracks),
        }, 'tracks': normalized})

    # otherwise render template, passing both model list and JSON string
    context = {
        'playlist': playlist,
        'tracks': tracks,
        'tracks_json': json.dumps(normalized),
    }
    return render(request, 'core/shared_playlist.html', context)


# shared_album_view is no longer needed; album sharing removed from the app


def shared_track_view(request):
    # simple page that stores track info to localStorage and redirects to main app
    tid = request.GET.get('id', '')
    title = request.GET.get('title', '')
    artist = request.GET.get('artist', '')
    thumb = request.GET.get('thumb', '')
    # escape to avoid script injection
    context = {
        'track': {
            'id': escape(tid),
            'title': escape(title),
            'artist': escape(artist),
            'thumb': escape(thumb),
        }
    }
    return render(request, 'core/shared_track.html', context)


# shortened track share logic (uses SharedTrack model)
@api_view(['GET'])
@permission_classes([AllowAny])
@permission_classes([AllowAny])
def shorten_track(request):
    """Return a tiny URL that redirects to shared_track_view.

    Instead of using the in‑memory cache, persist the data in the database so
    URLs remain valid after the server restarts. Entries automatically expire
    (you may add a periodic cleanup task if desired).
    """
    tid = request.GET.get('id', '')
    title = request.GET.get('title', '')
    artist = request.GET.get('artist', '')
    thumb = request.GET.get('thumb', '')
    track = {
        'id': escape(tid),
        'title': escape(title),
        'artist': escape(artist),
        'thumb': escape(thumb),
    }

    # generate or reuse code via model method
    code = SharedTrack.generate_code()
    SharedTrack.objects.create(code=code, track=track)

    url = request.build_absolute_uri(reverse('track-short', args=[code]))
    return Response({'url': url})


def track_short_redirect(request, code):
    """Redirect page used by short urls; reuses shared_track template.

    Look up data from SharedTrack table instead of cache. If entry is missing we
    raise 404 as before. Optionally you could enforce expiration here by
    deleting records older than 24 h.
    """
    try:
        sh = SharedTrack.objects.get(code=code)
    except SharedTrack.DoesNotExist:
        raise Http404()
    return render(request, 'core/shared_track.html', {'track': sh.track})


@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def playlist_share_toggle_api(request, playlist_id):
    """Ativa/desativa o compartilhamento por link."""
    playlist = get_object_or_404(Playlist, id=playlist_id)
    if playlist.usuario != request.user:
        return Response({'error': 'Sem permissão'}, status=403)

    enable = request.data.get('enable')
    if enable is None:
        playlist.is_shared = not playlist.is_shared
    else:
        playlist.is_shared = bool(enable)

    try:
        playlist.save(update_fields=['is_shared'])
    except Exception:
        playlist.save()

    share_url = None
    if playlist.is_shared:
        share_url = request.build_absolute_uri(reverse('playlist-share', args=[playlist.share_uuid]))

    return Response({'is_shared': playlist.is_shared, 'share_url': share_url})


@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def playlist_share_regenerate_api(request, playlist_id):
    """Regenera o UUID de compartilhamento."""
    playlist = get_object_or_404(Playlist, id=playlist_id)
    if playlist.usuario != request.user:
        return Response({'error': 'Sem permissão'}, status=403)

    playlist.share_uuid = uuid.uuid4()
    if 'enable' in request.data:
        playlist.is_shared = bool(request.data.get('enable'))
    try:
        playlist.save(update_fields=['share_uuid', 'is_shared'])
    except Exception:
        playlist.save()

    share_url = None
    if playlist.is_shared:
        share_url = request.build_absolute_uri(reverse('playlist-share', args=[playlist.share_uuid]))

    return Response({'share_uuid': str(playlist.share_uuid), 'share_url': share_url})


# --- album sharing endpoints removed ----------------------------
# album sharing has been deprecated/disabled; endpoints were deleted
# (old logic can be restored from version control if needed)


@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def playlist_add_musica_api(request, playlist_id):
    """Adiciona música à playlist."""
    try:
        playlist = Playlist.objects.get(id=playlist_id)
    except Playlist.DoesNotExist:
        return Response({'success': False, 'error': 'Playlist não encontrada'}, status=404)

    if playlist.usuario != request.user and request.user not in playlist.colaboradores.all():
        return Response({'success': False, 'error': 'Sem permissão'}, status=403)

    musica_id = request.data.get('musica_id')
    if not musica_id:
        return Response({'success': False, 'error': 'musica_id é obrigatório'}, status=400)

    try:
        musica = _resolve_musica_identifier(musica_id)
    except Musica.DoesNotExist:
        if isinstance(musica_id, str) and not musica_id.isdigit():
            musica = _get_or_create_musica_by_youtube_id(musica_id)
            if musica is None:
                return Response({'success': False, 'error': 'Música não encontrada'}, status=404)
        else:
            return Response({'success': False, 'error': 'Música não encontrada'}, status=404)

    if PlaylistItem.objects.filter(playlist=playlist, musica=musica).exists():
        return Response({'success': False, 'error': 'Música já está na playlist'}, status=400)

    ultima_ordem = playlist.playlistitem_set.aggregate(max_ordem=Max('ordem'))['max_ordem'] or 0

    item = PlaylistItem.objects.create(
        playlist=playlist,
        musica=musica,
        ordem=ultima_ordem + 1,
        added_by=request.user
    )

    playlist.update_stats()

    try:
        from .serializers import PlaylistItemSerializer
        item_ser = PlaylistItemSerializer(item, context={'request': request}).data
    except Exception:
        item_ser = None

    return Response({'success': True, 'message': 'Música adicionada', 'item': item_ser})


@csrf_exempt
@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def playlist_remove_musica_api(request, playlist_id, item_id=None):
    """Remove uma música da playlist.

    Endpoint flexível usado por clientes REST (mobile/app/react) para excluir
    uma faixa. Suporta dois modos:

      * URL contém `item_id` (roteamento legado): remove o item da tabela
        `PlaylistItem` com a chave primária correspondente.
      * HTTP DELETE com JSON ou query param `musica_id`: localiza o item
        pela música (id numérico ou YouTube id) e apaga.

    O método também reprojeta as ordens posteriores e chama
    ``playlist.update_stats()`` antes de retornar.
    """
    playlist = get_object_or_404(Playlist, id=playlist_id)

    if playlist.usuario != request.user and request.user not in playlist.colaboradores.all():
        return Response({'success': False, 'error': 'Sem permissão'}, status=403)

    # caso trabalhemos com item_id diretamente (rotas antigas)
    if item_id is not None:
        try:
            item = PlaylistItem.objects.get(id=item_id, playlist=playlist)
        except PlaylistItem.DoesNotExist:
            return Response({'success': False, 'error': 'Item não encontrado'}, status=404)
    else:
        # procurar musica_id no corpo ou em query params
        musica_id = None
        if isinstance(request.data, dict):
            musica_id = request.data.get('musica_id') or request.data.get('id')
        if not musica_id:
            musica_id = request.GET.get('musica_id') or request.GET.get('id')

        if not musica_id:
            return Response({'success': False, 'error': 'musica_id é obrigatório'}, status=400)

        # localizar item pela música
        item = None
        if str(musica_id).isdigit():
            item = PlaylistItem.objects.filter(playlist=playlist, musica__id=musica_id).first()
        if not item:
            item = PlaylistItem.objects.filter(playlist=playlist, musica__youtube_id=musica_id).first()
        if not item:
            return Response({'success': False, 'error': 'Música não encontrada na playlist'}, status=404)

    # item encontrado; exclui e reajusta ordens
    item.delete()
    items = PlaylistItem.objects.filter(playlist=playlist).order_by('ordem')
    for idx, it in enumerate(items):
        it.ordem = idx + 1
        it.save()

    playlist.update_stats()
    return Response({'success': True, 'message': 'Música removida'})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@csrf_exempt
def playlist_reorder_api(request, playlist_id):
    """Reordena músicas da playlist."""
    playlist = get_object_or_404(Playlist, id=playlist_id)

    if playlist.usuario != request.user and request.user not in playlist.colaboradores.all():
        return Response({'success': False, 'error': 'Sem permissão'}, status=403)

    ordem = request.data.get('ordem', [])
    if not ordem or not isinstance(ordem, list):
        return Response({'success': False, 'error': 'Lista de ordem inválida'}, status=400)

    for idx, item_id in enumerate(ordem):
        try:
            item = PlaylistItem.objects.get(id=item_id, playlist=playlist)
            item.ordem = idx + 1
            item.save()
        except PlaylistItem.DoesNotExist:
            continue

    return Response({'success': True, 'message': 'Playlist reordenada'})





@api_view(['GET', 'POST', 'DELETE'])
@permission_classes([IsAuthenticated])
def favoritos_api(request):
    """Lista, adiciona ou remove favoritos."""
    if request.method == 'GET':
        favoritos = Favorito.objects.filter(
            usuario=request.user
        ).select_related('musica', 'musica__artista').order_by('-adicionado_em')
        serializer = FavoritoSerializer(favoritos, many=True)
        return Response(serializer.data)

    elif request.method == 'POST':
        musica_id = request.data.get('musica_id') or request.data.get('id')
        like_flag = request.data.get('like', True)

        if musica_id is None:
            return Response({'error': 'musica_id ou id é obrigatório'}, status=400)

        try:
            musica = _resolve_musica_identifier(musica_id)
        except Musica.DoesNotExist:
            if isinstance(musica_id, str) and not musica_id.isdigit():
                musica = _get_or_create_musica_by_youtube_id(musica_id)
                if musica is None:
                    return Response({'error': 'Música não encontrada'}, status=404)
            else:
                return Response({'error': 'Música não encontrada'}, status=404)

        if isinstance(like_flag, str):
            like_flag = like_flag.lower() not in ('0', 'false', 'no')

        if not like_flag:
            try:
                favorito = Favorito.objects.get(usuario=request.user, musica=musica)
                musica.likes = max(0, musica.likes - 1)
                musica.save(update_fields=['likes'])
                favorito.delete()
                return Response({'success': True, 'message': 'Removido dos favoritos'})
            except Favorito.DoesNotExist:
                return Response({'error': 'Favorito não encontrado'}, status=404)

        favorito, created = Favorito.objects.get_or_create(usuario=request.user, musica=musica)
        if created:
            musica.likes += 1
            musica.save(update_fields=['likes'])
            serializer = FavoritoSerializer(favorito)
            return Response(serializer.data, status=201)
        else:
            return Response({'message': 'Música já está nos favoritos'})

    elif request.method == 'DELETE':
        if str(request.GET.get('clear_all') or '').lower() in ('1', 'true', 'yes'):
            qs = Favorito.objects.filter(usuario=request.user)
            removed = qs.count()
            qs.delete()
            return Response({'success': True, 'message': 'Todos os favoritos removidos', 'removed_count': removed})

        musica_id = request.GET.get('musica_id') or request.GET.get('id')
        if not musica_id:
            return Response({'error': 'musica_id é obrigatório'}, status=400)

        try:
            musica = _resolve_musica_identifier(musica_id)
        except Musica.DoesNotExist:
            return Response({'error': 'Música não encontrada'}, status=404)

        try:
            favorito = Favorito.objects.get(usuario=request.user, musica=musica)
            musica.likes = max(0, musica.likes - 1)
            musica.save(update_fields=['likes'])
            favorito.delete()
            return Response({'success': True, 'message': 'Removido dos favoritos'})
        except Favorito.DoesNotExist:
            return Response({'error': 'Favorito não encontrado'}, status=404)


@api_view(['GET'])
@permission_classes([AllowAny])
def favoritos_test(request):
    """Rota de teste/health para favoritos."""
    if request.user and request.user.is_authenticated:
        cnt = Favorito.objects.filter(usuario=request.user).count()
    else:
        cnt = Favorito.objects.count()
    return Response({'ok': True, 'favorites_count': cnt})


# ============================================================================
# VIEWS DE AVALIAÇÕES
# ============================================================================

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def avaliacoes_api(request):
    """Avaliações do usuário."""
    if request.method == 'GET':
        musica_id = request.GET.get('musica_id')
        if musica_id:
            avaliacoes = Avaliacao.objects.filter(musica_id=musica_id).order_by('-created_at')
        else:
            avaliacoes = Avaliacao.objects.filter(usuario=request.user).order_by('-created_at')

        serializer = AvaliacaoSerializer(avaliacoes, many=True)
        return Response(serializer.data)

    elif request.method == 'POST':
        musica_id = request.data.get('musica_id')
        nota = request.data.get('nota')
        comentario = request.data.get('comentario', '')

        if not musica_id or not nota:
            return Response({'error': 'musica_id e nota são obrigatórios'}, status=400)

        try:
            nota = int(nota)
            if nota < 1 or nota > 5:
                return Response({'error': 'Nota deve ser entre 1 e 5'}, status=400)
        except ValueError:
            return Response({'error': 'Nota inválida'}, status=400)

        try:
            musica = _resolve_musica_identifier(musica_id)
        except Musica.DoesNotExist:
            return Response({'error': 'Música não encontrada'}, status=404)

        avaliacao, created = Avaliacao.objects.update_or_create(
            usuario=request.user,
            musica=musica,
            defaults={'nota': nota, 'comentario': comentario}
        )

        serializer = AvaliacaoSerializer(avaliacao)
        status_code = 201 if created else 200
        return Response(serializer.data, status=status_code)


# ============================================================================
# VIEWS DE PLAYBACK STATE
# ============================================================================

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def playback_state_api(request):
    """Obtém ou salva estado de reprodução."""
    state, created = PlaybackState.objects.get_or_create(usuario=request.user)

    if request.method == 'GET':
        data = {
            'musica_atual_id': state.musica_atual_id,
            'playlist_atual_id': state.playlist_atual_id,
            'posicao': state.posicao,
            'volume': state.volume,
            'updated_at': state.updated_at
        }

        if state.musica_atual:
            data['musica_atual'] = MusicaSerializer(
                state.musica_atual, context={'request': request}
            ).data

        return Response(data)

    elif request.method == 'POST':
        musica_id = request.data.get('musica_atual_id')
        playlist_id = request.data.get('playlist_atual_id')

        if musica_id:
            try:
                state.musica_atual = _resolve_musica_identifier(musica_id)
            except Musica.DoesNotExist:
                pass

        if playlist_id:
            try:
                state.playlist_atual = Playlist.objects.get(id=playlist_id)
            except Playlist.DoesNotExist:
                pass

        state.posicao = request.data.get('posicao', state.posicao)
        state.volume = request.data.get('volume', state.volume)
        state.save()

        return Response({'success': True, 'message': 'Estado salvo'})


@api_view(['GET', 'POST', 'DELETE'])
@permission_classes([AllowAny])
def queue_api(request):
    """GET: retorna fila; POST: salva fila; DELETE: limpa fila."""
    user = request.user if hasattr(request, 'user') and request.user.is_authenticated else None

    try:
        if user:
            pq, created = PlaybackQueue.objects.get_or_create(usuario=user)
        else:
            if not request.session.session_key:
                request.session.save()
            return Response({'queue': {}})
    except Exception as e:
        logger.error(f"Erro ao acessar PlaybackQueue: {e}")
        return Response({'error': 'Erro ao acessar fila do usuário'}, status=500)

    if request.method == 'GET':
        data = pq.data or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}
        return Response({'queue': data})

    if request.method == 'POST':
        try:
            pq.data = request.data
            pq.save()
            return Response({'status': 'saved', 'queue': pq.data})
        except Exception as e:
            logger.exception(f"Erro ao salvar fila: {e}")
            return Response({'error': 'Erro ao salvar fila'}, status=500)

    if request.method == 'DELETE':
        try:
            pq.data = {}
            pq.save()
            return Response({'status': 'cleared'})
        except Exception as e:
            logger.error(f"Erro ao limpar fila: {e}")
            return Response({'error': 'Erro ao limpar fila'}, status=500)


# ============================================================================
# VIEWS DE GÊNEROS
# ============================================================================

@api_view(['GET'])
def generos_list_api(request):
    """Lista todos os gêneros."""
    generos = Genero.objects.all().order_by('nome')
    serializer = GeneroSerializer(generos, many=True)
    return Response(serializer.data)


@api_view(['GET'])
def genero_detail_api(request, genero_slug):
    """Detalhes de um gênero com músicas e álbuns."""
    genero = get_object_or_404(Genero, slug=genero_slug)
    serializer = GeneroSerializer(genero)

    musicas = Musica.objects.filter(generos=genero).select_related('artista')[:20]
    albuns = Album.objects.filter(generos=genero).select_related('artista')[:20]
    artistas = Artista.objects.filter(generos=genero)[:20]

    data = serializer.data
    data['musicas'] = MusicaSerializer(musicas, many=True, context={'request': request}).data
    data['albuns'] = AlbumSerializer(albuns, many=True, context={'request': request}).data
    data['artistas'] = ArtistaSerializer(artistas, many=True, context={'request': request}).data

    return Response(data)


# ============================================================================
# VIEWS DE ESTATÍSTICAS
# ============================================================================

@api_view(['GET'])
def dashboard_stats_api(request):
    """Estatísticas para dashboard."""
    total_musicas = Musica.objects.count()
    total_albuns = Album.objects.count()
    total_artistas = Artista.objects.count()
    total_usuarios = get_user_model().objects.count()

    musicas_populares = Musica.objects.order_by('-visualizacoes')[:10]
    musicas_mais_curtidas = Musica.objects.order_by('-likes')[:10]

    return Response({
        'total_musicas': total_musicas,
        'total_albuns': total_albuns,
        'total_artistas': total_artistas,
        'total_usuarios': total_usuarios,
        'musicas_populares': MusicaSerializer(musicas_populares, many=True).data,
        'musicas_mais_curtidas': MusicaSerializer(musicas_mais_curtidas, many=True).data,
    })


# ============================================================================
# VIEWS DE INTEGRAÇÃO COM YOUTUBE MUSIC (API)
# ============================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticatedOrReadOnly])
def buscar_musicas_api(request):
    """API para buscar músicas e álbuns no YouTube Music."""
    query = request.GET.get('q')
    if not query:
        return Response({'error': 'Parâmetro de busca "q" é obrigatório.'}, status=400)

    normalized_query = (query or '').strip().lower()
    cache_key = 'api_search_' + hashlib.sha1(normalized_query.encode('utf-8')).hexdigest()
    cached_response = cache.get(cache_key)
    if cached_response:
        return Response(cached_response)

    if not _check_connectivity():
        return Response({'error': 'Sem conectividade com music.youtube.com'}, status=503)

    try:
        ytmusic = _init_ytmusic()
    except ImportError as ie:
        # Dependência ausente: informe cliente e evite rastreamento de stack
        logger.error(f"YTMusic não disponível: {ie}")
        return Response(
            {'error': str(ie)},
            status=503,
        )

    try:
        raw_songs = ytmusic.search(query, filter="songs", limit=20)
        songs = []
        for item in raw_songs:
            normalized = _normalize_track(item, include_album_info=True)
            if normalized:
                songs.append(normalized)

        albums = []
        try:
            raw_albums = ytmusic.search(query, filter="albums", limit=10)
            for item in raw_albums:
                normalized = _normalize_album(item)
                if normalized:
                    albums.append(normalized)

            albums = _fetch_album_details_parallel(albums)

        except Exception as e:
            logger.warning(f"Erro ao buscar álbuns na API: {e}")

        response_data = {'songs': songs, 'albums': albums}
        cache.set(cache_key, response_data, SEARCH_CACHE_TIMEOUT)

        return Response(response_data)

    except Exception as e:
        logger.error(f"Erro na API de busca: {e}", exc_info=True)
        return Response({'error': str(e)}, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticatedOrReadOnly])
def recommendations_api(request, track_id: Optional[str] = None):
    """Retorna recomendações baseadas em título/artista ou em um track_id."""
    title = request.GET.get('title', '').strip()
    artists = request.GET.get('artists', '').strip()

    if track_id and not title and not artists:
        try:
            if not _check_connectivity():
                return Response({'error': 'Sem conectividade com music.youtube.com'}, status=503)
            ytmusic = _init_ytmusic()
            if hasattr(ytmusic, 'get_song'):
                info = ytmusic.get_song(track_id)
                if isinstance(info, dict):
                    title = info.get('title') or info.get('name') or ''
                    if info.get('artists') and isinstance(info.get('artists'), list):
                        artists = ', '.join([a.get('name', '') for a in info.get('artists', []) if a.get('name')])
                    elif info.get('artist'):
                        artists = info.get('artist')
            if not title and not artists:
                try:
                    search_results = ytmusic.search(track_id, filter='songs', limit=1) or []
                    if search_results:
                        item = search_results[0]
                        title = item.get('title') or item.get('name') or ''
                        if item.get('artists') and isinstance(item.get('artists'), list):
                            artists = ', '.join([a.get('name', '') for a in item.get('artists', []) if a.get('name')])
                        elif item.get('artist'):
                            artists = item.get('artist')
                except Exception as e:
                    logger.debug(f"Busca fallback por id falhou para {track_id}: {e}")
        except Exception as e:
            logger.debug(f"Não foi possível obter metadados para recomendações do track {track_id}: {e}")

    query = f"{title} {artists}".strip()

    if not query:
        return Response({'recommendations': []})

    cache_key = 'recommendations_' + hashlib.sha1(query.encode('utf-8')).hexdigest()
    cached = cache.get(cache_key)
    if cached:
        return Response({'recommendations': cached})

    if not _check_connectivity():
        return Response({'error': 'Sem conectividade com music.youtube.com'}, status=503)

    try:
        ytmusic = _init_ytmusic()
        try:
            req_limit = int(request.GET.get('limit') or 0)
        except Exception:
            req_limit = 0
        limit = req_limit if (1 <= req_limit <= 100) else 24
        raw = ytmusic.search(query, filter='songs', limit=limit) or []

        recs = []
        for item in raw:
            normalized = _normalize_track(item, include_album_info=False)
            if normalized:
                mapped = {
                    'id': normalized['id'],
                    'titulo': normalized['title'],
                    'artista_nome': normalized.get('artist') or (', '.join(normalized.get('artists') or [])),
                    'capa': normalized['thumb'],
                    'audio_url': f"/api/streaming-url/?video_id={normalized['id']}"
                }
                recs.append(mapped)

        cache.set(cache_key, recs, CACHE_TIMEOUT)
        return Response({'success': True, 'recommendations': recs})

    except Exception as e:
        logger.error(f"Erro ao buscar recomendações: {e}")
        return Response({'error': str(e)}, status=500)


@api_view(['GET'])
def album_tracks_api(request):
    """Retorna faixas de um álbum do YouTube Music."""
    browse_id = request.GET.get('browseId') or request.GET.get('browse_id')
    if not browse_id:
        return Response({'error': 'Parâmetro browseId é obrigatório.'}, status=400)

    cache_key = f'album_tracks_{browse_id}'
    cached = cache.get(cache_key)
    if cached:
        return Response(cached)

    if not _check_connectivity():
        return Response({'error': 'Sem conectividade com music.youtube.com'}, status=503)

    try:
        ytmusic = _init_ytmusic()
        album = ytmusic.get_album(browse_id)

        tracks = album.get('tracks') or album.get('songs') or []
        thumb = ''

        if album.get('thumbnails'):
            try:
                thumbs = album.get('thumbnails')
                if thumbs and isinstance(thumbs, list):
                    thumb = thumbs[-1].get('url', '')
            except Exception:
                pass

        normalized_tracks = []
        for t in tracks:
            track = _normalize_track(t, include_album_info=False)
            if track:
                track['thumb'] = thumb
                normalized_tracks.append(track)

        def _sort_key(x):
            tn = x.get('track_number')
            return (tn if tn is not None else 999999, x.get('title') or '')

        try:
            normalized_tracks.sort(key=_sort_key)
        except Exception:
            pass

        response_data = {
            'tracks': normalized_tracks,
            'musicas': normalized_tracks,
            'faixas': normalized_tracks,
            'album': {
                'title': album.get('title') or album.get('name') or 'Álbum',
                'thumbnail': thumb,
                'year': album.get('year', ''),
                'artist': album.get('artist', ''),
                'track_count': len(normalized_tracks)
            }
        }

        cache.set(cache_key, response_data, ALBUM_CACHE_TIMEOUT)
        return Response(response_data)

    except Exception as e:
        logger.exception(f"Erro ao buscar faixas do álbum {browse_id}")
        return Response({'error': str(e)}, status=500)


@api_view(['GET'])
def track_info_api(request):
    """Retorna metadados de uma faixa do YouTube Music."""
    video_id = request.GET.get('video_id')
    if not video_id:
        return Response({'error': 'Parâmetro video_id é obrigatório.'}, status=400)

    cache_key = f'track_info_{video_id}'
    cached = cache.get(cache_key)
    if cached:
        return Response(cached)

    if not _check_connectivity():
        return Response({'error': 'Sem conectividade com music.youtube.com'}, status=503)

    try:
        ytmusic = _init_ytmusic()
        album_browse_id = ''
        album_name = ''

        try:
            if hasattr(ytmusic, 'get_song'):
                info = ytmusic.get_song(video_id)

                if isinstance(info, dict):
                    if info.get('album') and isinstance(info.get('album'), dict):
                        album_browse_id = info.get('album', {}).get('browseId') or info.get('album', {}).get('id') or ''
                        album_name = info.get('album', {}).get('name') or ''

                    if not album_browse_id:
                        mf = info.get('microformat') or info.get('song') or {}
                        if isinstance(mf, dict):
                            alb = mf.get('album') or mf.get('song') or {}
                            if isinstance(alb, dict):
                                album_browse_id = alb.get('browseId') or alb.get('id') or ''
                                album_name = alb.get('name') or ''
        except Exception as e:
            logger.debug(f"get_song falhou para {video_id}: {e}")

        if not album_browse_id:
            try:
                search_results = ytmusic.search(video_id, filter='songs', limit=1)
                if search_results and len(search_results) > 0:
                    item = search_results[0]
                    if item.get('album') and isinstance(item.get('album'), dict):
                        album_browse_id = item.get('album', {}).get('browseId') or item.get('album', {}).get('id') or ''
                        album_name = item.get('album', {}).get('name') or ''
            except Exception as e:
                logger.debug(f"Busca fallback falhou para {video_id}: {e}")

        response_data = {
            'album_browseId': album_browse_id,
            'album_name': album_name
        }
        cache.set(cache_key, response_data, CACHE_TIMEOUT)
        return Response(response_data)

    except Exception as e:
        logger.error(f"Erro ao buscar info da faixa: {e}")
        return Response({'error': str(e)}, status=500)


# simple in‑memory locks to prevent multiple concurrent yt-dlp runs for
# the same video_id. keys map to threading.Event that is set when the
# first extraction completes. this is intentionally very lightweight and
# will be lost on process restart; it's only to avoid hammering yt-dlp when
# several HTTP requests arrive at once.
_stream_locks: dict[str, "threading.Event"] = {}


def _extract_stream_url(video_id: str, is_prefetch: bool = False) -> Optional[str]:
    """Helper que obtém (e cacheia) a URL de streaming para um vídeo."""
    if not video_id:
        return None

    cache_key = f'stream_url_{video_id}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    # if another request is already fetching this video, wait for it
    if video_id in _stream_locks:
        ev = _stream_locks[video_id]
        ev.wait(timeout=10)
        return cache.get(cache_key)

    # otherwise create a lock/event for ourselves
    import threading
    ev = threading.Event()
    _stream_locks[video_id] = ev

    urls_to_try = [
        f'https://music.youtube.com/watch?v={video_id}',
        f'https://www.youtube.com/watch?v={video_id}',
        f'https://youtu.be/{video_id}'
    ]

    # opções padrão para yt-dlp (incluir proxy local por padrão)
    ydl_opts = {
        'cookiefile': _get_cookiefile_path(),
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'socket_timeout': REQUEST_TIMEOUT,
        # proxy padrao utilizado pelo servidor desenvolvimento/produção local
        'proxy': 'http://127.0.0.1:8888',
    }
    
    

    if is_prefetch:
        ydl_opts['extract_flat'] = True
        ydl_opts['format'] = 'bestaudio[abr<=128]/bestaudio'

    cookiefile_path = _get_cookiefile_path()
    if cookiefile_path and os.path.exists(cookiefile_path):
        ydl_opts['cookiefile'] = cookiefile_path

    # Apply JS runtime tweaks; guard against an unexpected None return value
    result = _build_ydl_opts_js_runtime(ydl_opts)
    if result:
        ydl_opts = result
    # guarantee proxy setting (default port 8888) is still present
    if ydl_opts is None:
        ydl_opts = {}
    ydl_opts.setdefault('proxy', 'http://127.0.0.1:8888')

    ydl_opts['headers'] = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
    }

    last_error = None
    try:
        for attempt_url in urls_to_try:
            for attempt in range(MAX_RETRIES):
                try:
                    # run extraction in a separate thread to enforce a hard timeout
                    def do_extract():
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            return ydl.extract_info(attempt_url, download=False)

                    from concurrent.futures import ThreadPoolExecutor, TimeoutError
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(do_extract)
                        try:
                            info = future.result(timeout=20)  # abort after 20 seconds
                        except TimeoutError:
                            last_error = 'extract timeout'
                            logger.warning(f"_extract_stream_url timeout for {attempt_url}")
                            future.cancel()
                            raise Exception(last_error)

                    if isinstance(info, dict):
                        stream_url = info.get('url')
                        if not stream_url and info.get('formats'):
                            formats = info.get('formats', [])
                            if formats:
                                audio_formats = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
                                if audio_formats:
                                    stream_url = audio_formats[-1].get('url')
                                else:
                                    stream_url = formats[-1].get('url')

                        if stream_url:
                            cache.set(cache_key, stream_url, STREAM_CACHE_TIMEOUT)
                            return stream_url
                    break
                except Exception as e:
                    last_error = str(e)
                    logger.debug(f"_extract_stream_url tentativa {attempt + 1} para {attempt_url} falhou: {e}")
                    time.sleep(1 * (attempt + 1))

        logger.debug(f"_extract_stream_url: não encontrou stream para {video_id}: {last_error}")
        return None
    finally:
        # signal any waiters and remove lock entry
        try:
            ev.set()
        except Exception:
            pass
        _stream_locks.pop(video_id, None)


@api_view(['GET'])
def streaming_url_api(request):
    """Obtém URL de streaming para um vídeo do YouTube."""
    video_id = request.GET.get('video_id')
    if not video_id:
        return Response({'error': 'Parâmetro video_id é obrigatório.'}, status=400)

    if not _is_valid_youtube_id(video_id):
        return Response({'error': 'video_id inválido'}, status=400)

    is_prefetch = request.GET.get('prefetch') == '1'

    try:
        url = _extract_stream_url(video_id, is_prefetch=is_prefetch)
        if url:
            return Response({'stream_url': url})
        else:
            return Response({'error': 'Não foi possível obter URL de streaming.'}, status=502)
    except Exception as e:
        logger.exception(f"Erro no streaming_url_api para {video_id}")
        return Response({'error': str(e)}, status=500)


@api_view(['GET'])
def download_track_api(request):
    """Retorna um arquivo MP3 convertido para um vídeo do YouTube.

    Baixa a melhor faixa de áudio disponível via yt_dlp e utiliza o
    "FFmpegExtractAudio" para converter para mp3. O arquivo é armazenado em
    um diretório temporário e enviado como anexo. O diretório é removido após
    a resposta.
    """
    video_id = request.GET.get('video_id')
    if not video_id:
        return Response({'error': 'Parâmetro video_id é obrigatório.'}, status=400)
    if not _is_valid_youtube_id(video_id):
        return Response({'error': 'video_id inválido'}, status=400)

    tmpdir = tempfile.mkdtemp()
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(tmpdir, '%(id)s.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
        }
        # apply proxy conditional on video_id (ignore for localhost/127.0.0.1)
        if not video_id.startswith('127.0.0.1') and not video_id.startswith('localhost'):
            proxy_setting = 'http://127.0.0.1:8888'
            if proxy_setting:
                ydl_opts['proxy'] = proxy_setting
                logger.debug('aplicando proxy para yt-dlp', extra={'proxy': proxy_setting})
        else:
            logger.debug('ignorando proxy para conexão local')

        # re‑use any existing cookie file or headers from _extract_stream_url
        # apply any JS runtime and proxy configuration, protecting against None
        result = _build_ydl_opts_js_runtime(ydl_opts)
        if result:
            ydl_opts = result
        # ensure proxy still set for download endpoint
        if ydl_opts is None:
            ydl_opts = {}
        ydl_opts.setdefault('proxy', 'http://127.0.0.1:8888')

        url = f'https://www.youtube.com/watch?v={video_id}'
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except (yt_dlp.utils.PostProcessingError, yt_dlp.utils.DownloadError) as pp_err:
            # DownloadError is raised by yt-dlp when PostProcessingError occurs
            # inside extract_info; catch both to be safe. If the message
            # mentions ffmpeg/ffprobe, retry without conversion.
            const_msg = str(pp_err).lower()
            if 'ffmpeg' in const_msg or 'ffprobe' in const_msg:
                logger.warning('ffmpeg missing during download, retrying without postprocessors: %s', pp_err)
                ydl_opts.pop('postprocessors', None)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
            else:
                # re-raise if it's another kind of download error
                raise
        if not isinstance(info, dict):
            raise ValueError('yt_dlp não retornou dict')
        filename = ydl.prepare_filename(info)
        # if we performed conversion above the file will be .mp3; otherwise it
        # will have the original extension supplied by yt-dlp
        target_file = filename
        if 'postprocessors' in ydl_opts:
            # conversion still present, ensure mp3
            target_file = os.path.splitext(filename)[0] + '.mp3'
        if not os.path.exists(target_file):
            return Response({'error': 'Falha ao baixar/convertar áudio. Verifique se o servidor tem ffmpeg instalado.'}, status=500)

        response = FileResponse(open(target_file, 'rb'), as_attachment=True,
                                filename=f"{info.get('title','track')}{os.path.splitext(target_file)[1]}")
        return response
    except Exception as e:
        logger.exception(f"Erro no download_track_api para {video_id}")
        return Response({'error': str(e)}, status=500)
    finally:
        # cleanup temporários
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass



# Download and conversion helpers have been completely removed.
# No local utilities related to file conversion remain in this file.

# ============================================================================
# VIEWS DE PLAYLIST (DELETE / REMOVE TRACK)
# ============================================================================

@login_required
def delete_playlist(request, playlist_id):
    """DELETE /api/playlist/<id>/delete/ — exclui a playlist inteira."""
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Método não permitido'}, status=405)

    playlist = get_object_or_404(Playlist, id=playlist_id)

    if playlist.usuario != request.user:
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    playlist.delete()
    return JsonResponse({'success': True})


@login_required
def remove_track_from_playlist(request, playlist_id):
    """[DEPRECATED]
    DELETE /api/playlist/<id>/remove/ — remove uma música da playlist.
    
    Essa view legacy não é utilizada pela API DRF moderna; o novo endpoint
    ``playlist_remove_musica_api`` substituiu-a e oferece suporte a remoção
    por ``musica_id``. A rota associada foi comentada em urls.py.
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Método não permitido'}, status=405)

    playlist = get_object_or_404(Playlist, id=playlist_id)

    if playlist.usuario != request.user:
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    try:
        data = json.loads(request.body)
        musica_id = data.get('musica_id')
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    if not musica_id:
        return JsonResponse({'error': 'musica_id é obrigatório'}, status=400)

    item = None
    if str(musica_id).isdigit():
        item = PlaylistItem.objects.filter(playlist=playlist, musica__id=musica_id).first()
    if not item:
        item = PlaylistItem.objects.filter(playlist=playlist, musica__youtube_id=musica_id).first()

    if not item:
        return JsonResponse({'error': 'Música não encontrada nesta playlist'}, status=404)

    item.delete()
    return JsonResponse({'success': True, 'removed_musica_id': musica_id})


# ============================================================================
# ============================================================================
# API DE AUTENTICAÇÃO (MOBILE / TOKEN)
# ============================================================================

from rest_framework.authtoken.models import Token
from django.contrib.auth import authenticate


@api_view(['POST'])
@permission_classes([AllowAny])
def api_auth_login(request):
    """Login via API — retorna Token para uso no mobile.

    Além de gerar/retornar o token também chamamos `auth_login` para
    estabelecer a sessão Django (necessário para disparar os sinais
    user_logged_in/user_logged_out) e possibilitar alertas de e‑mail.
    """
    username = request.data.get('username', '').strip()
    password = request.data.get('password', '')
    if not username or not password:
        return Response({'error': 'Informe usuário e senha'}, status=400)

    user = authenticate(request, username=username, password=password)
    if user is None:
        return Response({'error': 'Credenciais inválidas'}, status=401)

    # log the user into the session so signals fire normally
    try:
        auth_login(request, user)
    except Exception:
        # se algo falhar não é crítico — token ainda será emitido
        pass

    token, _ = Token.objects.get_or_create(user=user)
    return Response({
        'token': token.key,
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
        },
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_auth_logout(request):
    """Logout via API: remove token and call django logout so signals fire."""
    # delete token if exists
    try:
        Token.objects.filter(user=request.user).delete()
    except Exception:
        pass
    try:
        auth_logout(request)
    except Exception:
        pass
    return Response({'success': True})


@api_view(['POST'])
@permission_classes([AllowAny])
def api_auth_register(request):
    """Cadastro via API — cria usuário e retorna Token."""
    username = request.data.get('username', '').strip()
    email = request.data.get('email', '').strip()
    # note: we don't call auth_login here because registration route
    # typically logs the user in separately on the client side; signals
    # for user creation will fire automatically via post_save.
    password = request.data.get('password', '')
    password2 = request.data.get('password2', '')

    errors = {}
    if not username:
        errors['username'] = ['Campo obrigatório']
    if not email:
        errors['email'] = ['Campo obrigatório']
    if not password:
        errors['password'] = ['Campo obrigatório']
    if password != password2:
        errors['password2'] = ['As senhas não coincidem']
    if errors:
        return Response(errors, status=400)

    UserModel = get_user_model()
    if UserModel.objects.filter(username__iexact=username).exists():
        return Response({'username': ['Este nome de usuário já está em uso']}, status=400)
    if UserModel.objects.filter(email__iexact=email).exists():
        return Response({'email': ['Este e-mail já está em uso']}, status=400)

    user = UserModel.objects.create_user(username=username, email=email, password=password)
    token, _ = Token.objects.get_or_create(user=user)
    return Response({
        'token': token.key,
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
        },
    }, status=201)


# ---------------------------------------------------------------------
# password reset helpers for mobile/api clients
# ---------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([AllowAny])
def api_auth_password_reset(request):
    """Dispara email contendo link de redefinição.
    Link será consumido pelo frontend React/Native.
    """
    email = request.data.get('email', '').strip()
    if not email:
        return Response({'email': ['Campo obrigatório']}, status=400)
    UserModel = get_user_model()
    try:
        user = UserModel.objects.get(email__iexact=email)
    except UserModel.DoesNotExist:
        # não revelar existência do e-mail
        return Response({'success': True})
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    frontend = getattr(settings, 'FRONTEND_URL', '').rstrip('/')
    reset_link = f"{frontend}/reset-password?uid={uid}&token={token}"
    # utiliza o sinal já criado para envio
    from core.signals import trigger_password_reset_email
    trigger_password_reset_email(user, reset_link)
    return Response({'success': True})


@api_view(['POST'])
@permission_classes([AllowAny])
def api_auth_password_reset_confirm(request):
    """Recebe uid/token e nova senha, redefine se válido."""
    uidb64 = request.data.get('uid')
    token = request.data.get('token')
    new_pw = request.data.get('password', '')
    if not uidb64 or not token:
        return Response({'error': 'Dados incompletos.'}, status=400)
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        UserModel = get_user_model()
        user = UserModel.objects.get(pk=uid)
    except Exception:
        return Response({'error': 'Link inválido.'}, status=400)
    if not default_token_generator.check_token(user, token):
        return Response({'error': 'Token expirado ou inválido.'}, status=400)
    if not new_pw:
        return Response({'password': ['Informe a nova senha']}, status=400)
    user.set_password(new_pw)
    user.save()
    return Response({'success': True})


@api_view(['GET','PATCH'])
@permission_classes([IsAuthenticated])
def api_auth_profile(request):
    """Retorna ou atualiza dados do perfil do usuário autenticado.

    - **GET**: devolve informação básica de User + campos do UserProfile.
    - **PATCH**: aceita alterações parciais em `email`, `first_name`,
      `last_name` e nos campos do perfil estendido (cpf_cnpj, phone,
      mobile_phone, address, address_number, complement, city, state,
      postal_code).
    """
    user = request.user

    if request.method == 'PATCH':
        data = request.data
        changed = False
        # campos do User
        for fld in ('email', 'first_name', 'last_name'):
            if fld in data:
                setattr(user, fld, data.get(fld) or '')
                changed = True
        if changed:
            user.save()
        # perfil estendido
        profile = getattr(user, 'profile', None)
        if profile:
            prof_changed = False
            for fld in ('cpf_cnpj','phone','mobile_phone','address',
                        'address_number','complement','city','state','postal_code'):
                if fld in data:
                    setattr(profile, fld, data.get(fld) or '')
                    prof_changed = True
            if prof_changed:
                profile.save()
        # depois de salvar, vamos retornar o novo objeto abaixo

    # incluir informações de assinatura ativa (se houver) para exibir no app móvel
    try:
        assinatura = Subscription.objects.filter(usuario=user, status='active').order_by('-criado_em').first()
    except Exception:
        assinatura = None

    assinatura_data = None
    if assinatura:
        assinatura_data = {
            'id': assinatura.id,
            'plan': assinatura.plano_id,
            'status': assinatura.status,
            'periodo_termina_em': assinatura.periodo_termina_em.isoformat() if getattr(assinatura, 'periodo_termina_em', None) else None,
        }

    # include app download info if available
    app_info = None
    try:
        app_obj = AppDownload.objects.filter(is_active=True).order_by('-created_at').first()
        if app_obj and app_obj.arquivo and hasattr(app_obj.arquivo, 'url'):
            app_info = {
                'id': app_obj.id,
                'title': app_obj.titulo,
                'file_url': app_obj.arquivo.url,
            }
    except Exception:
        app_info = None

    resp = {
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'date_joined': user.date_joined,
        'assinatura_ativa': assinatura_data,
    }
    # include extended profile fields if available
    profile = getattr(user, 'profile', None)
    if profile:
        resp.update({
            'cpf_cnpj': profile.cpf_cnpj,
            'phone': profile.phone,
            'mobile_phone': profile.mobile_phone,
            'address': profile.address,
            'address_number': profile.address_number,
            'complement': profile.complement,
            'city': profile.city,
            'state': profile.state,
            'postal_code': profile.postal_code,
        })

    if app_info:
        resp['app_file'] = app_info
    return Response(resp)


# ALIASES PARA COMPATIBILIDADE COM URLS.PY
# ============================================================================

def buscar_musicas(request):
    return buscar_musicas_api(request)


def recommendations(request):
    return recommendations_api(request)


def album_tracks(request):
    return album_tracks_api(request)


def track_info(request):
    return track_info_api(request)


def streaming_url(request):
    return streaming_url_api(request)
