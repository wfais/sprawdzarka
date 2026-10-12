"""Glowna mapa adresow."""
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path
from django.views.generic import RedirectView

from kurs import widoki_webhook

admin.site.site_header = "Sprawdzarka: administracja techniczna"
admin.site.site_title = "Sprawdzarka"
admin.site.index_title = "Dane systemu"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="panel:pulpit", permanent=False)),
    path("zdrowie/", lambda request: HttpResponse("ok", content_type="text/plain")),
    path("admin/", admin.site.urls),
    path("panel/", include("kurs.urls_panel")),
    path("github/webhook/", widoki_webhook.webhook, name="github_webhook"),
    path("", include("kurs.urls_student")),
]
