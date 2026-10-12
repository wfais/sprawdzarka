"""Odbior powiadomien (webhookow) od aplikacji GitHub."""
import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import uslugi

log = logging.getLogger(__name__)


def podpis_poprawny(tresc: bytes, podpis: str, sekret: str) -> bool:
    oczekiwany = "sha256=" + hmac.new(sekret.encode(), tresc, hashlib.sha256).hexdigest()
    return hmac.compare_digest(podpis or "", oczekiwany)


@csrf_exempt
@require_POST
def webhook(request):
    sekret = settings.GITHUB_WEBHOOK_SECRET
    if not sekret:
        return HttpResponse("Brak skonfigurowanego GITHUB_WEBHOOK_SECRET.", status=503)
    if not podpis_poprawny(request.body, request.headers.get("X-Hub-Signature-256", ""), sekret):
        return HttpResponseForbidden("Niepoprawny podpis.")
    zdarzenie = request.headers.get("X-GitHub-Event", "")
    if zdarzenie == "ping":
        return JsonResponse({"ok": True, "odpowiedz": "pong"})
    if zdarzenie != "push":
        return JsonResponse({"ok": True, "pominieto": zdarzenie}, status=202)
    try:
        dane = json.loads(request.body)
    except ValueError:
        return HttpResponseBadRequest("Niepoprawny JSON.")
    nowe = uslugi.obsluz_webhook_push(dane)
    return JsonResponse({"ok": True, "nowe": nowe})
