# core/serializers.py
from rest_framework import serializers
from django.contrib.auth.models import User
from .models import (
    Artista, Album, Musica, Playlist, PlaylistItem,
    HistoricoReproducao, Favorito, Avaliacao, Genero,
    PlaybackState, PlaybackQueue
)


# ============================================================================
# SERIALIZER: GENERO
# ============================================================================

class GeneroSerializer(serializers.ModelSerializer):
    class Meta:
        model = Genero
        fields = ['id', 'nome', 'slug', 'descricao', 'icone', 'cor']


# ============================================================================
# SERIALIZER: ARTISTA
# ============================================================================

class ArtistaSerializer(serializers.ModelSerializer):
    generos = GeneroSerializer(many=True, read_only=True)
    generos_ids = serializers.PrimaryKeyRelatedField(
        many=True, 
        queryset=Genero.objects.all(),
        write_only=True,
        source='generos'
    )
    foto_url = serializers.SerializerMethodField()
    total_albuns = serializers.IntegerField(read_only=True)
    total_musicas = serializers.IntegerField(read_only=True)

    class Meta:
        model = Artista
        fields = [
            'id', 'nome', 'nome_artistico', 'biografia', 'pais',
            'data_formacao', 'foto', 'foto_url', 'generos', 'generos_ids',
            'ouvintes_mensais', 'is_verified', 'total_albuns', 'total_musicas',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at', 'ouvintes_mensais']

    def get_foto_url(self, obj):
        return obj.foto_url


class ArtistaDetailSerializer(ArtistaSerializer):
    """Serializer com mais detalhes para página de artista"""
    # Os campos adicionais são populados na view
    pass


# ============================================================================
# SERIALIZER: ALBUM
# ============================================================================

class AlbumSerializer(serializers.ModelSerializer):
    artista_nome = serializers.CharField(source='artista.nome', read_only=True)
    artista = serializers.PrimaryKeyRelatedField(queryset=Artista.objects.all())
    generos = GeneroSerializer(many=True, read_only=True)
    generos_ids = serializers.PrimaryKeyRelatedField(
        many=True, 
        queryset=Genero.objects.all(),
        write_only=True,
        source='generos'
    )
    capa_url = serializers.SerializerMethodField()
    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)

    class Meta:
        model = Album
        fields = [
            'id', 'titulo', 'artista', 'artista_nome', 'tipo', 'tipo_display',
            'generos', 'generos_ids', 'ano', 'data_lancamento', 'capa', 'capa_url',
            'descricao', 'gravadora', 'total_faixas', 'duracao_total',
            'avaliacao_media', 'created_at', 'updated_at'
        ]
        read_only_fields = ['total_faixas', 'duracao_total', 'avaliacao_media']

    def get_capa_url(self, obj):
        return obj.capa_url


class AlbumDetailSerializer(AlbumSerializer):
    """Serializer para detalhes do álbum incluindo músicas"""
    musicas = serializers.SerializerMethodField()

    class Meta(AlbumSerializer.Meta):
        fields = AlbumSerializer.Meta.fields + ['musicas']

    def get_musicas(self, obj):
        musicas = obj.musicas.all().order_by('disc_number', 'track_number')
        return MusicaSerializer(musicas, many=True, context=self.context).data


# ============================================================================
# SERIALIZER: MUSICA
# ============================================================================

class MusicaSerializer(serializers.ModelSerializer):
    # expose both the raw FK and a string-typed artist name for clients
    artista_nome = serializers.CharField(source='artista.nome', read_only=True)
    artista = serializers.PrimaryKeyRelatedField(queryset=Artista.objects.all())
    artist = serializers.CharField(source='artista_nome', read_only=True)
    
    album_titulo = serializers.CharField(source='album.titulo', read_only=True, allow_null=True)
    album = serializers.PrimaryKeyRelatedField(
        queryset=Album.objects.all(),
        allow_null=True,
        required=False
    )
    
    feat_artists = serializers.StringRelatedField(many=True, read_only=True)
    feat_artists_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Artista.objects.all(),
        write_only=True,
        required=False,
        source='feat_artists'
    )
    
    generos = GeneroSerializer(many=True, read_only=True)
    generos_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Genero.objects.all(),
        write_only=True,
        required=False,
        source='generos'
    )
    
    duracao_formatada = serializers.SerializerMethodField()
    capa = serializers.SerializerMethodField()
    audio_url = serializers.SerializerMethodField()
    qualidade_display = serializers.CharField(source='get_qualidade_display', read_only=True)

    class Meta:
        model = Musica
        fields = [
            'id', 'titulo', 'artista', 'artista_nome', 'album', 'album_titulo',
            'feat_artists', 'feat_artists_ids', 'generos', 'generos_ids',
            'duracao', 'duracao_formatada', 'track_number', 'disc_number',
            'arquivo', 'arquivo_mp3', 'arquivo_flac', 'youtube_id',
            'letra', 'compositor', 'qualidade', 'qualidade_display',
            'visualizacoes', 'likes', 'data_lancamento', 'is_explicit',
            'is_instrumental', 'capa', 'audio_url', 'created_at', 'updated_at'
        ]
        read_only_fields = ['visualizacoes', 'likes', 'created_at', 'updated_at']

    def get_duracao_formatada(self, obj):
        return obj.duracao_formatada

    def get_capa(self, obj):
        # Prioridade: capa do álbum (se existir) → thumbnail do YouTube (quando houver youtube_id) → placeholder
        try:
            # Se houver capa associada ao álbum/música, usá-la
            if getattr(obj, 'album', None) and getattr(obj.album, 'capa', None):
                # Album.capa pode ser um ImageField — tentar retornar a URL
                if hasattr(obj.album.capa, 'url'):
                    return obj.album.capa.url
        except Exception:
            pass

        # Se for uma faixa vinculada ao YouTube, usar thumbnail pública
        if getattr(obj, 'youtube_id', None):
            return f"https://i.ytimg.com/vi/{obj.youtube_id}/hqdefault.jpg"

        # Fallback para o comportamento existente (propriedade `capa` do modelo)
        return obj.capa

    def get_audio_url(self, obj):
        return obj.audio_url


class MusicaListSerializer(MusicaSerializer):
    """Versão simplificada para listas (inclui `youtube_id` e `audio_url` para reprodução)."""
    class Meta(MusicaSerializer.Meta):
        fields = [
            'id', 'titulo', 'artista', 'artist', 'artista_nome', 'album', 'album_titulo',
            'duracao_formatada', 'capa', 'youtube_id', 'audio_url', 'visualizacoes',
            'likes', 'is_explicit'
        ]


# ============================================================================
# SERIALIZER: PLAYLIST
# ============================================================================

class PlaylistItemSerializer(serializers.ModelSerializer):
    musica = MusicaListSerializer(read_only=True)
    musica_id = serializers.PrimaryKeyRelatedField(
        queryset=Musica.objects.all(),
        write_only=True,
        source='musica'
    )
    added_by_username = serializers.CharField(source='added_by.username', read_only=True)

    class Meta:
        model = PlaylistItem
        fields = ['id', 'musica', 'musica_id', 'ordem', 'added_at', 'added_by_username']
        read_only_fields = ['added_at']


class PlaylistSerializer(serializers.ModelSerializer):
    usuario_username = serializers.CharField(source='usuario.username', read_only=True)
    capa_url = serializers.SerializerMethodField()
    visibilidade_display = serializers.CharField(source='get_visibilidade_display', read_only=True)
    total_musicas = serializers.SerializerMethodField()

    class Meta:
        model = Playlist
        fields = [
            'id', 'nome', 'descricao', 'usuario', 'usuario_username',
            'capa', 'capa_url', 'visibilidade', 'visibilidade_display',
            'curtidas', 'duracao_total', 'criada_em', 'updated_at',
            'total_musicas'
        ]
        read_only_fields = ['usuario', 'curtidas', 'criada_em', 'updated_at']

    def get_capa_url(self, obj):
        return obj.capa_url

    def get_total_musicas(self, obj):
        """
        Retorna o número de músicas na playlist de forma segura.
        Funciona tanto com atributo anotado (Count) quanto com método do modelo.
        """
        # Verificar se é um atributo anotado (inteiro)
        if hasattr(obj, 'total_musicas'):
            total = getattr(obj, 'total_musicas')
            if isinstance(total, int):
                return total
            elif callable(total):
                return total()
        
        # Fallback: contar diretamente
        return obj.musicas.count()


class PlaylistDetailSerializer(PlaylistSerializer):
    """Serializer com músicas da playlist"""
    # Renomeado de 'musicas' para 'itens' no retorno JSON para deixar claro
    # que cada elemento é um PlaylistItem (com .musica embutido).
    # ATENÇÃO: o frontend espera a chave 'musicas' — mantemos o nome do campo.
    musicas = serializers.SerializerMethodField()
    colaboradores = serializers.StringRelatedField(many=True, read_only=True)

    class Meta(PlaylistSerializer.Meta):
        fields = PlaylistSerializer.Meta.fields + ['colaboradores', 'musicas']

    def get_musicas(self, obj):
        # Ordenar pela coluna 'ordem' do PlaylistItem
        items = obj.playlistitem_set.all().select_related(
            'musica',
            'musica__artista',
            'musica__album',
            'added_by'
        ).order_by('ordem')
        return PlaylistItemSerializer(items, many=True, context=self.context).data


# ============================================================================
# SERIALIZER: HISTORICO
# ============================================================================

class HistoricoSerializer(serializers.ModelSerializer):
    musica_titulo = serializers.CharField(source='musica.titulo', read_only=True)
    musica_artista = serializers.CharField(source='musica.artista.nome', read_only=True)
    musica_capa = serializers.SerializerMethodField()

    class Meta:
        model = HistoricoReproducao
        fields = [
            'id', 'usuario', 'musica', 'musica_titulo', 'musica_artista',
            'musica_capa', 'tocada_em', 'duracao_reproduzida', 'completou'
        ]
        read_only_fields = ['usuario', 'tocada_em']

    def get_musica_capa(self, obj):
        return obj.musica.capa


# ============================================================================
# SERIALIZER: FAVORITO
# ============================================================================

class FavoritoSerializer(serializers.ModelSerializer):
    musica = MusicaListSerializer(read_only=True)
    musica_id = serializers.PrimaryKeyRelatedField(
        queryset=Musica.objects.all(),
        write_only=True,
        source='musica'
    )
    # Expor um boolean `like` para compatibilidade com o novo esquema de API
    like = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Favorito
        fields = ['id', 'usuario', 'musica', 'musica_id', 'adicionado_em', 'like']
        read_only_fields = ['usuario', 'adicionado_em', 'like']

    def get_like(self, obj):
        # Um registro de Favorito representa um "like" — sempre True
        return True


# ============================================================================
# SERIALIZER: AVALIACAO
# ============================================================================

class AvaliacaoSerializer(serializers.ModelSerializer):
    usuario_username = serializers.CharField(source='usuario.username', read_only=True)
    musica_titulo = serializers.CharField(source='musica.titulo', read_only=True)

    class Meta:
        model = Avaliacao
        fields = [
            'id', 'usuario', 'usuario_username', 'musica', 'musica_titulo',
            'nota', 'comentario', 'created_at', 'updated_at'
        ]
        read_only_fields = ['usuario', 'created_at', 'updated_at']

    def validate_nota(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError("A nota deve ser entre 1 e 5")
        return value


# ============================================================================
# SERIALIZER: PLAYBACK STATE
# ============================================================================

class PlaybackStateSerializer(serializers.ModelSerializer):
    musica_atual = MusicaListSerializer(read_only=True)
    playlist_atual = PlaylistSerializer(read_only=True)
    musica_atual_id = serializers.PrimaryKeyRelatedField(
        queryset=Musica.objects.all(),
        write_only=True,
        required=False,
        source='musica_atual'
    )
    playlist_atual_id = serializers.PrimaryKeyRelatedField(
        queryset=Playlist.objects.all(),
        write_only=True,
        required=False,
        source='playlist_atual'
    )

    class Meta:
        model = PlaybackState
        fields = [
            'id', 'usuario', 'musica_atual', 'musica_atual_id',
            'playlist_atual', 'playlist_atual_id', 'posicao', 'volume',
            'updated_at'
        ]
        read_only_fields = ['usuario', 'updated_at']


# ============================================================================
# SERIALIZER: USER
# ============================================================================

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'date_joined']
        read_only_fields = ['date_joined']


class UserProfileSerializer(serializers.ModelSerializer):
    playlists_count = serializers.IntegerField(source='playlists.count', read_only=True)
    favoritos_count = serializers.IntegerField(source='favoritos.count', read_only=True)
    historico_count = serializers.IntegerField(source='historico.count', read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'playlists_count', 'favoritos_count', 'historico_count',
            'date_joined', 'last_login'
        ]
        read_only_fields = ['date_joined', 'last_login']


# ============================================================================
# SERIALIZER: YOUTUBE MUSIC (para respostas da API)
# ============================================================================

class YTMusicTrackSerializer(serializers.Serializer):
    """Serializer para músicas do YouTube Music"""
    id = serializers.CharField()
    videoId = serializers.CharField()
    title = serializers.CharField()
    artists = serializers.ListField(child=serializers.CharField())
    artist = serializers.CharField()
    thumb = serializers.URLField(allow_blank=True)
    thumbnail = serializers.URLField(allow_blank=True)
    duration = serializers.CharField()
    duration_seconds = serializers.IntegerField()
    album_browseId = serializers.CharField(allow_blank=True)
    album = serializers.CharField(allow_blank=True)
    year = serializers.CharField(allow_blank=True)
    track_number = serializers.IntegerField(allow_null=True)


class YTMusicAlbumSerializer(serializers.Serializer):
    """Serializer para álbuns do YouTube Music"""
    id = serializers.CharField()
    browseId = serializers.CharField()
    title = serializers.CharField()
    artists = serializers.ListField(child=serializers.CharField())
    artist = serializers.CharField()
    thumb = serializers.URLField(allow_blank=True)
    thumbnail = serializers.URLField(allow_blank=True)
    year = serializers.CharField(allow_blank=True)
    track_count = serializers.IntegerField()
    total_duration = serializers.CharField(allow_blank=True)


# ============================================================================
# SERIALIZER: DASHBOARD STATS
# ============================================================================

class DashboardStatsSerializer(serializers.Serializer):
    """Serializer para estatísticas do dashboard"""
    total_musicas = serializers.IntegerField()
    total_albuns = serializers.IntegerField()
    total_artistas = serializers.IntegerField()
    total_usuarios = serializers.IntegerField()
    musicas_populares = MusicaListSerializer(many=True)
    musicas_mais_curtidas = MusicaListSerializer(many=True)
