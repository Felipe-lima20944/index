# core/models.py
from django.db import models
from django.contrib.auth.models import User
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from django.core.validators import MinValueValidator, MaxValueValidator
from django.urls import reverse
import uuid
import os

# signals import needed before any handlers are connected
from django.db.models.signals import post_migrate


# authentication backend used for login with either email or username
class EmailOrUsernameModelBackend(ModelBackend):
    """Allow users to authenticate using either their username or email address.

    This is the backend referenced in settings.AUTHENTICATION_BACKENDS. It
    replicates common logic: check incoming credential for an '@' symbol and
    attempt to fetch by email first, falling back to username otherwise.
    Case-insensitive lookup is used to behave nicely with user input.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None
        # determine whether value looks like an email address
        lookup = {'username__iexact': username}
        if '@' in username:
            lookup = {'email__iexact': username}
        try:
            user = UserModel.objects.get(**lookup)
        except UserModel.MultipleObjectsReturned:
            # varias contas compartilham o mesmo email; tentar autenticar manualmente
            users = UserModel.objects.filter(**lookup)
            for u in users:
                if u.check_password(password) and self.user_can_authenticate(u):
                    return u
            return None
        except UserModel.DoesNotExist:
            return None
        else:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
        return None

try:
    # Django 3.1+
    from django.db.models import JSONField
except ImportError:
    # Fallback para versões antigas
    from django.db.models import TextField
    JSONField = None


# ============================================================================
# FUNÇÕES AUXILIARES PARA PATHS DE UPLOAD
# ============================================================================

def artista_foto_path(instance, filename):
    """Gera caminho único para foto do artista"""
    ext = filename.split('.')[-1]
    nome_seguro = instance.nome.lower().replace(' ', '_').replace('/', '_')
    filename = f"{nome_seguro}_foto.{ext}"
    return os.path.join('artistas', filename)


def album_capa_path(instance, filename):
    """Gera caminho único para capa do álbum"""
    ext = filename.split('.')[-1]
    artista_nome = instance.artista.nome.lower().replace(' ', '_').replace('/', '_')
    album_titulo = instance.titulo.lower().replace(' ', '_').replace('/', '_')
    filename = f"{artista_nome}_{album_titulo}_capa.{ext}"
    return os.path.join('albuns', filename)


def musica_arquivo_path(instance, filename):
    """Gera caminho único para arquivo de música"""
    ext = filename.split('.')[-1]
    artista_nome = instance.artista.nome.lower().replace(' ', '_').replace('/', '_')
    musica_titulo = instance.titulo.lower().replace(' ', '_').replace('/', '_')
    filename = f"{artista_nome}_{musica_titulo}.{ext}"
    return os.path.join('musicas', filename)


def playlist_capa_path(instance, filename):
    """Gera caminho único para capa da playlist"""
    ext = filename.split('.')[-1]
    usuario = instance.usuario.username.lower()
    playlist_nome = instance.nome.lower().replace(' ', '_').replace('/', '_')
    filename = f"{usuario}_{playlist_nome}_capa.{ext}"
    return os.path.join('playlists', filename)


# ============================================================================
# MODELO: GENERO
# ============================================================================

class Genero(models.Model):
    """Gêneros musicais para categorização"""
    nome = models.CharField(max_length=50, unique=True, db_index=True)
    slug = models.SlugField(unique=True, max_length=60)
    descricao = models.TextField(blank=True)
    icone = models.CharField(
        max_length=50, 
        blank=True, 
        help_text="Classe do ícone (ex: fa-solid fa-rock)"
    )
    cor = models.CharField(
        max_length=7, 
        blank=True, 
        help_text="Cor em hexadecimal (ex: #ff0000)",
        default="#8b5cf6"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nome']
        verbose_name = 'Gênero'
        verbose_name_plural = 'Gêneros'
        indexes = [
            models.Index(fields=['slug']),
        ]

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.nome)[:60]
        super().save(*args, **kwargs)


# ============================================================================
# MODELO: ARTISTA
# ============================================================================

class Artista(models.Model):
    """Artistas/bandas musicais"""
    nome = models.CharField(max_length=100, db_index=True)
    nome_artistico = models.CharField(max_length=100, blank=True)
    biografia = models.TextField(blank=True)
    pais = models.CharField(max_length=50, blank=True)
    data_formacao = models.DateField(
        null=True, 
        blank=True, 
        help_text="Data de formação/início da carreira"
    )
    foto = models.ImageField(
        upload_to=artista_foto_path, 
        blank=True, 
        null=True
    )
    generos = models.ManyToManyField(
        Genero, 
        related_name='artistas', 
        blank=True
    )
    ouvintes_mensais = models.PositiveIntegerField(default=0)
    is_verified = models.BooleanField(
        default=False,
        help_text="Artista verificado"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nome']
        verbose_name = 'Artista'
        verbose_name_plural = 'Artistas'
        indexes = [
            models.Index(fields=['nome']),
            models.Index(fields=['-ouvintes_mensais']),
        ]

    def __str__(self):
        return self.nome_artistico or self.nome

    def get_absolute_url(self):
        return reverse('artista_detail', args=[self.id])

    @property
    def foto_url(self):
        if self.foto and hasattr(self.foto, 'url'):
            return self.foto.url
        return '/static/images/default-artist.jpg'

    def total_albuns(self):
        return self.albuns.count()

    def total_musicas(self):
        return self.musicas.count()


# ============================================================================
# MODELO: ALBUM
# ============================================================================

class Album(models.Model):
    """Álbuns musicais"""
    
    TIPO_CHOICES = [
        ('album', 'Álbum'),
        ('single', 'Single'),
        ('ep', 'EP'),
        ('compilation', 'Coletânea'),
        ('live', 'Ao Vivo'),
        ('remix', 'Remix'),
        ('soundtrack', 'Trilha Sonora'),
    ]
    
    titulo = models.CharField(max_length=200, db_index=True)
    artista = models.ForeignKey(
        Artista, 
        on_delete=models.CASCADE, 
        related_name='albuns'
    )
    tipo = models.CharField(
        max_length=20, 
        choices=TIPO_CHOICES, 
        default='album'
    )
    generos = models.ManyToManyField(
        Genero, 
        related_name='albuns', 
        blank=True
    )
    ano = models.PositiveIntegerField(
        validators=[MinValueValidator(1900), MaxValueValidator(2100)]
    )
    data_lancamento = models.DateField(default=timezone.now)
    capa = models.ImageField(
        upload_to=album_capa_path, 
        blank=True, 
        null=True
    )
    descricao = models.TextField(blank=True)
    gravadora = models.CharField(max_length=100, blank=True)
    total_faixas = models.PositiveIntegerField(default=0, editable=False)
    duracao_total = models.DurationField(null=True, blank=True, editable=False)
    avaliacao_media = models.FloatField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-ano', 'titulo']
        verbose_name = 'Álbum'
        verbose_name_plural = 'Álbuns'
        unique_together = ['artista', 'titulo', 'ano']
        indexes = [
            models.Index(fields=['artista', '-ano']),
            models.Index(fields=['titulo']),
            models.Index(fields=['-data_lancamento']),
        ]

    def __str__(self):
        return f"{self.titulo} - {self.artista} ({self.ano})"

    def get_absolute_url(self):
        return reverse('album_detail', args=[self.id])

    @property
    def capa_url(self):
        if self.capa and hasattr(self.capa, 'url'):
            return self.capa.url
        # Tentar usar capa da primeira música
        primeira_musica = self.musicas.first()
        if primeira_musica and primeira_musica.capa:
            return primeira_musica.capa
        return '/static/images/default-album.jpg'

    def update_stats(self):
        """Atualiza estatísticas do álbum"""
        musicas = self.musicas.all()
        self.total_faixas = musicas.count()
        
        if musicas.exists():
            # Calcular duração total
            total_seconds = sum(
                (m.duracao.total_seconds() for m in musicas if m.duracao), 
                0
            )
            from datetime import timedelta
            self.duracao_total = timedelta(seconds=total_seconds)
        
        self.save(update_fields=['total_faixas', 'duracao_total'])


# ============================================================================
# MODELO: MUSICA
# ============================================================================

class Musica(models.Model):
    """Músicas/faixas"""
    
    QUALIDADE_CHOICES = [
        ('low', 'Baixa (128kbps)'),
        ('medium', 'Média (192kbps)'),
        ('high', 'Alta (320kbps)'),
        ('lossless', 'Lossless (FLAC)'),
    ]
    
    titulo = models.CharField(max_length=200, db_index=True)
    artista = models.ForeignKey(
        Artista, 
        on_delete=models.CASCADE, 
        related_name='musicas'
    )
    album = models.ForeignKey(
        Album, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='musicas'
    )
    feat_artists = models.ManyToManyField(
        Artista, 
        related_name='participacoes', 
        blank=True
    )
    generos = models.ManyToManyField(
        Genero, 
        related_name='musicas', 
        blank=True
    )
    duracao = models.DurationField()
    track_number = models.PositiveIntegerField(default=1)
    disc_number = models.PositiveIntegerField(default=1)
    arquivo = models.FileField(
        upload_to=musica_arquivo_path,
        blank=True,
        null=True
    )
    arquivo_mp3 = models.FileField(
        upload_to='musicas/mp3/', 
        blank=True, 
        null=True
    )
    arquivo_flac = models.FileField(
        upload_to='musicas/flac/', 
        blank=True, 
        null=True
    )
    youtube_id = models.CharField(
        max_length=20, 
        blank=True,
        help_text="ID do vídeo no YouTube Music"
    )
    letra = models.TextField(blank=True)
    compositor = models.CharField(max_length=200, blank=True)
    qualidade = models.CharField(
        max_length=10, 
        choices=QUALIDADE_CHOICES, 
        default='high'
    )
    visualizacoes = models.PositiveIntegerField(default=0)
    likes = models.PositiveIntegerField(default=0)
    data_lancamento = models.DateField(default=timezone.now)
    is_explicit = models.BooleanField(
        default=False, 
        verbose_name="Conteúdo explícito"
    )
    is_instrumental = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['album', 'disc_number', 'track_number']
        verbose_name = 'Música'
        verbose_name_plural = 'Músicas'
        unique_together = ['album', 'track_number', 'disc_number']
        indexes = [
            models.Index(fields=['titulo']),
            models.Index(fields=['artista']),
            models.Index(fields=['-visualizacoes']),
            models.Index(fields=['-likes']),
            models.Index(fields=['data_lancamento']),
            models.Index(fields=['youtube_id']),
        ]

    def __str__(self):
        if self.feat_artists.exists():
            feat_names = ', '.join([a.nome for a in self.feat_artists.all()])
            return f"{self.titulo} (feat. {feat_names}) - {self.artista}"
        return f"{self.titulo} - {self.artista}"

    def get_absolute_url(self):
        return reverse('musica_detail', args=[self.id])

    @property
    def duracao_formatada(self):
        """Retorna duração formatada (MM:SS)"""
        if self.duracao:
            total_seconds = int(self.duracao.total_seconds())
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            return f"{minutes}:{seconds:02d}"
        return "0:00"

    @property
    def capa(self):
        """Retorna capa do álbum ou placeholder"""
        if self.album and self.album.capa and hasattr(self.album.capa, 'url'):
            return self.album.capa.url
        return '/static/images/default-track.jpg'

    @property
    def audio_url(self):
        """Retorna URL do arquivo de áudio disponível"""
        if self.arquivo and hasattr(self.arquivo, 'url'):
            return self.arquivo.url
        if self.arquivo_mp3 and hasattr(self.arquivo_mp3, 'url'):
            return self.arquivo_mp3.url
        if self.youtube_id:
            return f"/api/stream/url/?video_id={self.youtube_id}"
        return None

    def increment_views(self):
        """Incrementa contador de visualizações"""
        self.visualizacoes += 1
        self.save(update_fields=['visualizacoes'])


# ============================================================================
# MODELO: PLAYLIST
# ============================================================================

class Playlist(models.Model):
    """Playlists de usuários"""
    
    VISIBILITY_CHOICES = [
        ('public', 'Pública'),
        ('private', 'Privada'),
        ('collaborative', 'Colaborativa'),
    ]
    
    nome = models.CharField(max_length=100)
    descricao = models.TextField(blank=True)
    usuario = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='playlists'
    )
    musicas = models.ManyToManyField(
        Musica, 
        related_name='playlists', 
        through='PlaylistItem'
    )
    colaboradores = models.ManyToManyField(
        User, 
        related_name='playlists_colaborativas', 
        blank=True
    )
    capa = models.ImageField(
        upload_to=playlist_capa_path, 
        blank=True, 
        null=True
    )
    visibilidade = models.CharField(
        max_length=20, 
        choices=VISIBILITY_CHOICES, 
        default='private'
    )
    curtidas = models.PositiveIntegerField(default=0)
    duracao_total = models.DurationField(null=True, blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Compartilhamento por link
    is_shared = models.BooleanField(default=False)
    share_uuid = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    share_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-criada_em']
        verbose_name = 'Playlist'
        verbose_name_plural = 'Playlists'
        indexes = [
            models.Index(fields=['usuario', '-criada_em']),
        ]

    def __str__(self):
        return f"{self.nome} - {self.usuario.username}"

    @property
    def capa_url(self):
        if self.capa and hasattr(self.capa, 'url'):
            return self.capa.url
        # Usar primeira música como capa
        primeira = self.musicas.first()
        if primeira:
            return primeira.capa
        return '/static/images/default-playlist.jpg'

    def total_musicas(self):
        return self.musicas.count()

    def update_stats(self):
        """Atualiza estatísticas da playlist"""
        musicas = self.musicas.all()
        if musicas.exists():
            total_seconds = sum(
                (m.duracao.total_seconds() for m in musicas if m.duracao), 
                0
            )
            from datetime import timedelta
            self.duracao_total = timedelta(seconds=total_seconds)
            self.save(update_fields=['duracao_total'])


class PlaylistItem(models.Model):
    """Modelo intermediário para ordenar músicas na playlist"""
    playlist = models.ForeignKey(
        Playlist, 
        on_delete=models.CASCADE
    )
    musica = models.ForeignKey(
        Musica, 
        on_delete=models.CASCADE
    )
    ordem = models.PositiveIntegerField(default=0)
    added_at = models.DateTimeField(auto_now_add=True)
    added_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='added_tracks'
    )

    class Meta:
        ordering = ['ordem']
        unique_together = ['playlist', 'musica']
        indexes = [
            models.Index(fields=['playlist', 'ordem']),
        ]

    def __str__(self):
        return f"{self.playlist.nome} - {self.musica.titulo} (pos {self.ordem})"


# ============================================================================
# MODELO: HISTORICO REPRODUCAO
# ============================================================================

class HistoricoReproducao(models.Model):
    """Histórico de reprodução do usuário"""
    usuario = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='historico'
    )
    musica = models.ForeignKey(
        Musica, 
        on_delete=models.CASCADE, 
        related_name='historico'
    )
    tocada_em = models.DateTimeField(auto_now_add=True)
    duracao_reproduzida = models.DurationField(
        help_text="Quanto tempo foi reproduzido"
    )
    completou = models.BooleanField(default=False)

    class Meta:
        ordering = ['-tocada_em']
        verbose_name = 'Histórico de Reprodução'
        verbose_name_plural = 'Históricos de Reprodução'
        indexes = [
            models.Index(fields=['usuario', '-tocada_em']),
        ]

    def __str__(self):
        return f"{self.usuario.username} - {self.musica.titulo}"


# ============================================================================
# MODELO: FAVORITO
# ============================================================================

class Favorito(models.Model):
    """Músicas favoritas do usuário (likes)"""
    usuario = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='favoritos'
    )
    musica = models.ForeignKey(
        Musica, 
        on_delete=models.CASCADE, 
        related_name='favoritada_por'
    )
    adicionado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['usuario', 'musica']
        ordering = ['-adicionado_em']
        indexes = [
            models.Index(fields=['usuario', '-adicionado_em']),
        ]

    def __str__(self):
        return f"{self.usuario.username} - {self.musica.titulo}"


# ============================================================================
# MODELO: AVALIACAO
# ============================================================================

class Avaliacao(models.Model):
    """Avaliação de músicas (1-5 estrelas)"""
    usuario = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='avaliacoes'
    )
    musica = models.ForeignKey(
        Musica, 
        on_delete=models.CASCADE, 
        related_name='avaliacoes'
    )
    nota = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comentario = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['usuario', 'musica']
        verbose_name = 'Avaliação'
        verbose_name_plural = 'Avaliações'
        indexes = [
            models.Index(fields=['musica', '-nota']),
            models.Index(fields=['-created_at']),
        ]

    def __str__(self):
        return f"{self.usuario.username} - {self.musica.titulo}: {self.nota}★"


# ============================================================================
# MODELO: PLAYBACK QUEUE
# ============================================================================

class PlaybackQueue(models.Model):
    """Armazena a fila de reprodução do usuário"""
    usuario = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name='playback_queue'
    )
    data = JSONField(default=dict, blank=True) if JSONField else models.TextField(
        default='{}', 
        blank=True
    )
    current_track_index = models.IntegerField(default=0)
    current_position = models.FloatField(
        default=0, 
        help_text="Posição atual em segundos"
    )
    is_playing = models.BooleanField(default=False)
    repeat_mode = models.CharField(
        max_length=10, 
        default='off', 
        choices=[
            ('off', 'Off'),
            ('once', 'Once'),
            ('all', 'All'),
            ('one', 'One'),
        ]
    )
    shuffle = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Fila de Reprodução'
        verbose_name_plural = 'Filas de Reprodução'

    def __str__(self):
        return f"Queue - {self.usuario.username}"

    def set_queue(self, musicas, current_index=0):
        """Define a fila atual"""
        queue_data = {
            'queue': [m.id for m in musicas],
            'original_order': [m.id for m in musicas],
            'current_index': current_index
        }
        
        if JSONField:
            self.data = queue_data
        else:
            self.data = json.dumps(queue_data)
        
        self.save()

    def get_queue(self):
        """Retorna IDs das músicas na fila"""
        if not self.data:
            return []
        
        if isinstance(self.data, dict):
            return self.data.get('queue', [])
        
        try:
            import json
            data = json.loads(self.data)
            return data.get('queue', [])
        except:
            return []


# ============================================================================
# MODELO: PLAYBACK STATE
# ============================================================================

class PlaybackState(models.Model):
    """Estado atual da reprodução"""
    usuario = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name='playback_state'
    )
    musica_atual = models.ForeignKey(
        Musica, 
        on_delete=models.SET_NULL, 
        null=True
    )
    playlist_atual = models.ForeignKey(
        Playlist, 
        on_delete=models.SET_NULL, 
        null=True
    )
    posicao = models.FloatField(default=0)
    volume = models.IntegerField(
        default=70, 
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Estado de Reprodução'
        verbose_name_plural = 'Estados de Reprodução'

    def __str__(self):
        musica = self.musica_atual.titulo if self.musica_atual else 'Nenhuma'
        return f"{self.usuario.username} - {musica}"


# ============================================================================
# SIGNALS
# ============================================================================


# ============================================================================
# MODELOS: INTEGRAÇÃO ASAAS (Assinaturas / Pagamentos PIX)
# ============================================================================


class AsaasCustomer(models.Model):
    """Representa o cliente criado no Asaas vinculado a um `User` local."""
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='asaas_customer')
    asaas_id = models.CharField(max_length=128, unique=True, db_index=True)
    dados = JSONField(default=dict, blank=True) if JSONField else models.TextField(default='{}', blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Cliente Asaas'
        verbose_name_plural = 'Clientes Asaas'

    def __str__(self):
        return f"AsaasCustomer: {self.usuario.username} ({self.asaas_id})"


class Plan(models.Model):
    """Representa um plano de assinatura disponível na aplicação.

    Os administradores podem criar/editar planos via admin.
    O código usa o plano marcado como <code>is_active</code> ou, na falta dele, o primeiro plano criado.
    """
    slug = models.SlugField(max_length=50, unique=True, help_text='Identificador curto usado internamente')
    name = models.CharField(max_length=100, help_text='Nome exibido para o usuário')
    price = models.DecimalField(max_digits=10, decimal_places=2, help_text='Preço em reais (ex: 9.90)')
    duration_days = models.PositiveIntegerField(default=30, help_text='Número de dias do período da assinatura')
    is_active = models.BooleanField(default=False, help_text='Plano atualmente em oferta')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Plano'
        verbose_name_plural = 'Planos'
        ordering = ['-is_active', 'slug']

    def __str__(self):
        return f"{self.name} ({self.price})"


def _ensure_trial_plan_exists(app_config, **kwargs):
    """Post-migrate hook que garante que o plano trial esteja presente."""
    if app_config.name != 'core':
        return
    from decimal import Decimal
    Plan.objects.get_or_create(
        slug='trial_3dias',
        defaults={
            'name': 'Teste grátis 3 dias',
            'price': Decimal('0.00'),
            'duration_days': 3,
            'is_active': True,
        }
    )

# registrar o handler após definição da função
post_migrate.connect(_ensure_trial_plan_exists)


class Subscription(models.Model):
    """Assinatura recorrente (representação local da assinatura no Asaas)."""
    STATUS_CHOICES = [
        ('pending', 'Pendente'),
        ('active', 'Ativa'),
        ('cancelled', 'Cancelada'),
        ('past_due', 'Atrasada'),
        ('failed', 'Falhou'),
    ]

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name='assinaturas')
    plano_id = models.CharField(max_length=128, blank=True, help_text='ID do plano/sku local')
    asaas_subscription_id = models.CharField(max_length=128, blank=True, null=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    iniciado_em = models.DateTimeField(null=True, blank=True)
    periodo_termina_em = models.DateTimeField(null=True, blank=True)
    dados = JSONField(default=dict, blank=True) if JSONField else models.TextField(default='{}', blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Assinatura'
        verbose_name_plural = 'Assinaturas'

    def __str__(self):
        return f"Assinatura: {self.usuario.username} ({self.status})"

    @property
    def dias_restantes(self):
        """Número de dias inteiros que ainda faltam até o término.

        Usa ``timezone.now()`` para garantir que a contagem parte da data/hora atual
        e arredonda para cima se houver horas restantes. Se a assinatura já estiver
        expirada, retorna 0. Esta propriedade facilita mostrar "3 dias de teste"
        ou similar no front-end.
        """
        if not self.periodo_termina_em:
            return 0
        delta = self.periodo_termina_em - timezone.now()
        if delta.total_seconds() <= 0:
            return 0
        # dias inteiros restantes; se houver qualquer fração de dia conta como 1
        days = delta.days
        if delta.seconds > 0:
            days += 1
        return days


class Payment(models.Model):
    """Registro de pagamentos gerados via Asaas (PIX / cartão / boleto)."""
    METHOD_CHOICES = [
        ('PIX', 'PIX'),
        ('CREDIT_CARD', 'Cartão'),
        ('BOLETO', 'Boleto'),
        ('OTHER', 'Outro'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pendente'),
        ('paid', 'Pago'),
        ('overdue', 'Vencido'),
        ('cancelled', 'Cancelado'),
        ('failed', 'Falhou'),
    ]

    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='pagamentos')
    asaas_payment_id = models.CharField(max_length=128, unique=True, db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=6, default='BRL')
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default='PIX')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    pix_qr_payload = models.TextField(blank=True, help_text='Payload do PIX (copia e cola)')
    pix_qr_image = models.TextField(blank=True, help_text='Base64 ou URL do QR (quando disponível)')
    vencimento = models.DateTimeField(null=True, blank=True)
    pago_em = models.DateTimeField(null=True, blank=True)
    raw = JSONField(default=dict, blank=True) if JSONField else models.TextField(default='{}', blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Pagamento'
        verbose_name_plural = 'Pagamentos'

    def __str__(self):
        return f"Pagamento {self.asaas_payment_id} - {self.amount} {self.currency} ({self.status})"


from django.db.models.signals import post_save, post_delete, m2m_changed, post_migrate
from django.dispatch import receiver


# ============================================================================
# MODELO: PERFIL DO USUÁRIO (dados para integração com ASAAS)
# ============================================================================


class UserProfile(models.Model):
    """Perfil estendido do usuário para armazenar CPF/CNPJ e dados de contato."""
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    cpf_cnpj = models.CharField(max_length=32, blank=True, help_text='CPF ou CNPJ somente dígitos')
    phone = models.CharField(max_length=30, blank=True, help_text='Telefone fixo (opcional)')
    mobile_phone = models.CharField(max_length=30, blank=True, help_text='Telefone móvel (opcional)')
    address = models.CharField(max_length=200, blank=True)
    address_number = models.CharField(max_length=30, blank=True)
    complement = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Perfil do Usuário'
        verbose_name_plural = 'Perfis de Usuários'

    def __str__(self):
        return f"Perfil: {self.usuario.username}"


@receiver(post_save, sender=User)
def ensure_user_profile(sender, instance, created, **kwargs):
    """Garante que todo User tenha um UserProfile criado automaticamente.

    Além disso, concede automaticamente o plano de teste grátis de 3 dias na
    primeira criação da conta. Caso o plano de teste já exista no banco, não
    será duplicado; se o usuário já tiver sido beneficiado, nada acontece.
    """
    if created:
        UserProfile.objects.create(usuario=instance)
        # atribuir trial
        try:
            from decimal import Decimal
            # buscar ou criar o plano trial
            trial, _ = Plan.objects.get_or_create(
                slug='trial_3dias',
                defaults={
                    'name': 'Teste grátis 3 dias',
                    'price': Decimal('0.00'),
                    'duration_days': 3,
                    'is_active': True,
                }
            )
            # só cria assinatura se o usuário ainda não teve uma
            if not Subscription.objects.filter(usuario=instance, plano_id=trial.slug).exists():
                Subscription.objects.create(
                    usuario=instance,
                    plano_id=trial.slug,
                    status='active',
                    iniciado_em=timezone.now(),
                    periodo_termina_em=timezone.now() + timedelta(days=trial.duration_days),
                    dados={'note': 'assinatura automática de teste'}
                )
        except Exception:
            # falhas não devem impedir criação de perfil
            pass


@receiver(post_save, sender=Musica)
def update_album_stats_on_save(sender, instance, created, **kwargs):
    """Atualiza estatísticas do álbum quando uma música é salva"""
    if instance.album:
        # Usar transaction.on_commit para evitar problemas de consistência
        from django.db import transaction
        transaction.on_commit(lambda: instance.album.update_stats())


@receiver(post_delete, sender=Musica)
def update_album_stats_on_delete(sender, instance, **kwargs):
    """Atualiza estatísticas do álbum quando uma música é deletada"""
    if instance.album:
        album = instance.album
        # Pequeno atraso para garantir que a deleção foi concluída
        from django.db import transaction
        transaction.on_commit(lambda: album.update_stats())


@receiver(m2m_changed, sender=Playlist.musicas.through)
def update_playlist_stats_on_change(sender, instance, action, **kwargs):
    """Atualiza estatísticas da playlist quando músicas são adicionadas/removidas"""
    if action in ['post_add', 'post_remove', 'post_clear']:
        from django.db import transaction
        transaction.on_commit(lambda: instance.update_stats())


@receiver(post_save, sender=Avaliacao)
def update_musica_rating(sender, instance, **kwargs):
    """Atualiza média de avaliações da música"""
    musica = instance.musica


# ───────────────────────────────────────────────────────────────────────────
# Pagamento → assinatura automática (também há lógica no webhook)
# ───────────────────────────────────────────────────────────────────────────
from django.db.models.signals import pre_save

@receiver(pre_save, sender=Payment)
def _capture_old_payment_status(sender, instance, **kwargs):
    """Guarda o status anterior para uso no post_save."""
    if instance.pk:
        try:
            old = Payment.objects.get(pk=instance.pk)
            instance._old_status = old.status
        except Payment.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None

@receiver(post_save, sender=Payment)
def _maybe_create_subscription(sender, instance, created, **kwargs):
    """Cria uma assinatura local quando pagamento PIX vira "paid".

    - disparado tanto por webhook quanto por edição manual no admin.
    - não duplica se já existir assinatura ativa.
    """
    old = getattr(instance, '_old_status', None)
    if not created and old != 'paid' and instance.status == 'paid' and instance.method == 'PIX':
        user = instance.usuario
        if user and not Subscription.objects.filter(usuario=user, status='active').exists():
            Subscription.objects.create(
                usuario=user,
                plano_id='premium_mensal',
                status='active',
                iniciado_em=timezone.now(),
                periodo_termina_em=timezone.now() + timedelta(days=30),
                dados={'origin_payment': instance.asaas_payment_id}
            )


@receiver(post_save, sender=Favorito)
def increment_musica_likes(sender, instance, created, **kwargs):
    """Incrementa contador de likes quando adiciona aos favoritos

    - Também cria/atualiza uma playlist privada chamada "Curtidas"
      para o usuário e adiciona a música nela (não duplica).
    """
    if created:
        instance.musica.likes += 1
        instance.musica.save(update_fields=['likes'])

        # Adicionar à playlist "Curtidas" do usuário (criar se necessário)
        try:
            pl = Playlist.objects.filter(usuario=instance.usuario, nome__iexact='Curtidas').first()
            if not pl:
                pl = Playlist.objects.create(
                    usuario=instance.usuario,
                    nome='Curtidas',
                    descricao='Músicas que você curtiu',
                    visibilidade='private'
                )
            # Adiciona a música se ainda não estiver na playlist
            if not pl.musicas.filter(pk=instance.musica.pk).exists():
                pl.musicas.add(instance.musica)
                try:
                    pl.update_stats()
                except Exception:
                    pass
        except Exception:
            # não queremos que falhas no mecanismo de playlist quebrem o sinal principal
            pass


@receiver(post_delete, sender=Favorito)
def decrement_musica_likes(sender, instance, **kwargs):
    """Decrementa contador de likes quando remove dos favoritos

    - Também remove a música da playlist "Curtidas" do usuário, se existir.
    """
    instance.musica.likes = max(0, instance.musica.likes - 1)
    instance.musica.save(update_fields=['likes'])

    try:
        pl = Playlist.objects.filter(usuario=instance.usuario, nome__iexact='Curtidas').first()
        if pl and pl.musicas.filter(pk=instance.musica.pk).exists():
            pl.musicas.remove(instance.musica)
            try:
                pl.update_stats()
            except Exception:
                pass
    except Exception:
        pass


# ============================================================================
# MODELO PARA SESSÕES ANÔNIMAS (opcional)
# ============================================================================

class PlaybackQueueAnon(models.Model):
    """Fila de reprodução para usuários não autenticados"""
    session_key = models.CharField(max_length=40, unique=True, db_index=True)
    data = JSONField(default=dict, blank=True) if JSONField else models.TextField(
        default='{}', 
        blank=True
    )
    current_track_index = models.IntegerField(default=0)
    current_position = models.FloatField(default=0)
    is_playing = models.BooleanField(default=False)
    repeat_mode = models.CharField(max_length=10, default='off')
    shuffle = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Fila Anônima'
        verbose_name_plural = 'Filas Anônimas'

    def __str__(self):
        return f"Queue - {self.session_key}"
