from django.contrib import admin
from .models import (
	Plan, UserProfile, AsaasCustomer, Subscription, Payment,
    Genero, Banner, Musica, TrendingMusic, SearchBackground, AnonymousPromo,
    AppDownload,
)
from .models import UnsubscribedPromo
import json
from django.utils.safestring import mark_safe


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
	list_display = ('slug', 'name', 'price', 'duration_days', 'trial_days', 'is_active')
	list_editable = ('is_active',)
	prepopulated_fields = {'slug': ('name',)}
	fields = ('slug','name','price','duration_days','trial_days','is_active')


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
	list_display = ('usuario', 'cpf_cnpj', 'phone', 'mobile_phone')
	search_fields = ('usuario__username', 'cpf_cnpj', 'phone')
	fields = ('usuario','cpf_cnpj','phone','mobile_phone','address','city','state','postal_code')


@admin.register(AsaasCustomer)
class AsaasCustomerAdmin(admin.ModelAdmin):
	list_display = ('usuario', 'asaas_id', 'criado_em')
	search_fields = ('asaas_id', 'usuario__username')


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
	list_display = ('usuario', 'plano_id', 'status', 'iniciado_em')
	list_filter = ('status',)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
	list_display = ('asaas_payment_id', 'usuario', 'amount', 'status', 'criado_em')
	search_fields = ('asaas_payment_id', 'usuario__username')


@admin.register(Genero)
class GeneroAdmin(admin.ModelAdmin):
	list_display = ('nome', 'slug', 'cor')
	prepopulated_fields = {'slug': ('nome',)}
	search_fields = ('nome',)
	fields = ('nome', 'slug', 'descricao', 'icone', 'cor', 'capa_url', 'json_data')
	readonly_fields = ()


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
	list_display = ('titulo', 'subtitulo', 'ordem', 'is_active')
	list_editable = ('ordem', 'is_active')
	fields = ('titulo', 'subtitulo', 'imagem_url', 'link', 'ordem', 'is_active')
	search_fields = ('titulo', 'subtitulo')


# músicas e em alta
@admin.register(Musica)
class MusicaAdmin(admin.ModelAdmin):
    list_display = (
        'titulo', 'artista', 'album', 'track_number', 'is_trending',
        'visualizacoes', 'likes',
    )
    list_filter = ('is_trending', 'artista', 'album', 'generos')
    search_fields = ('titulo', 'artista__nome')
    raw_id_fields = ('artista', 'album', 'feat_artists', 'generos')


@admin.register(TrendingMusic)
class TrendingMusicAdmin(admin.ModelAdmin):
    list_display = ('display_title', 'added_at')
    ordering = ('-added_at',)
    fields = ('json_data', 'json_preview', 'added_at')
    readonly_fields = ('added_at', 'json_preview')

    def display_title(self, obj):
        if not obj:
            return ''
        try:
            j = obj.get_json()
            if isinstance(j, dict):
                return j.get('titulo') or j.get('title') or j.get('name') or f"Trending #{obj.pk}"
        except Exception:
            pass
        return f"Trending #{obj.pk}"

    display_title.short_description = 'Título'

    def json_preview(self, obj):
        if not obj:
            return ''
        try:
            data = obj.json_data
            if isinstance(data, str):
                import json
                data = json.loads(data or '{}')
            pretty = json.dumps(data or {}, indent=2, ensure_ascii=False)
        except Exception:
            pretty = str(getattr(obj, 'json_data', ''))
        from django.utils.safestring import mark_safe
        return mark_safe(f'<pre style="max-width:800px;white-space:pre-wrap;">{pretty}</pre>')

    json_preview.short_description = 'JSON (visualizar)'


# promoção anônima (imagem exibida após threshold para usuários não logados)
@admin.register(AnonymousPromo)
class AnonymousPromoAdmin(admin.ModelAdmin):
    list_display = ('imagem_url', 'link_url', 'threshold_seconds', 'is_active')
    list_editable = ('threshold_seconds', 'is_active')
    fields = ('imagem_url', 'link_url', 'threshold_seconds', 'is_active')
    search_fields = ('imagem_url', 'link_url')


@admin.register(UnsubscribedPromo)
class UnsubscribedPromoAdmin(admin.ModelAdmin):
    list_display = ('imagem_url', 'link_url', 'threshold_seconds', 'is_active')
    list_editable = ('threshold_seconds', 'is_active')


@admin.register(AppDownload)
class AppDownloadAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'arquivo', 'is_active', 'created_at')
    list_editable = ('is_active',)
    ordering = ('-created_at',)
    search_fields = ('titulo',)

@admin.register(SearchBackground)
class SearchBackgroundAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'subtitulo', 'ordem', 'is_active')
    list_editable = ('ordem', 'is_active')
    fields = ('titulo', 'subtitulo', 'imagem_url', 'ordem', 'is_active')
    search_fields = ('titulo', 'subtitulo')

    def display_title(self, obj):
        if not obj:
            return ''
        try:
            j = obj.get_json()
            if isinstance(j, dict):
                return j.get('titulo') or j.get('title') or j.get('name') or f"Trending #{obj.pk}"
        except Exception:
            pass
        return f"Trending #{obj.pk}"

    display_title.short_description = 'Título'

    def json_preview(self, obj):
        if not obj:
            return ''
        try:
            data = obj.json_data
            if isinstance(data, str):
                data = json.loads(data or '{}')
            pretty = json.dumps(data or {}, indent=2, ensure_ascii=False)
        except Exception:
            pretty = str(getattr(obj, 'json_data', ''))
        return mark_safe(f'<pre style="max-width:800px;white-space:pre-wrap;">{pretty}</pre>')

    json_preview.short_description = 'JSON (visualizar)'

