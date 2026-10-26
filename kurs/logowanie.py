"""Dostep do panelu: logowanie przez GitHub dla wskazanych kont i blokada po nieudanych probach hasla."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import KontoProwadzacego, NieudaneLogowanie


class BladLogowania(Exception):
    pass


def github_wlaczony() -> bool:
    return bool(settings.PANEL_KONTA_GITHUB and settings.GITHUB_APP_CLIENT_ID)


def adres_pod_publicznym_hostem(request) -> Optional[str]:
    """Adres tej samej strony pod PUBLICZNY_ADRES, jesli otwarto ja pod innym hostem (np. 127.0.0.1 zamiast localhost).

    GitHub wraca zawsze pod PUBLICZNY_ADRES, a ciasteczko sesji z innego hosta tam nie trafia.
    """
    publiczny = urlsplit(settings.PUBLICZNY_ADRES)
    if publiczny.netloc and request.get_host() != publiczny.netloc:
        return settings.PUBLICZNY_ADRES + request.get_full_path()
    return None


def adres_ip(request) -> Optional[str]:
    """Adres klienta. Za serwerem posredniczacym (Caddy) ostatni wpis X-Forwarded-For dodaje on sam."""
    przekazane = request.META.get("HTTP_X_FORWARDED_FOR", "")
    adres = przekazane.split(",")[-1].strip() if przekazane else request.META.get("REMOTE_ADDR", "")
    return adres or None


# --- Blokada prob ---

def _ostatnie():
    return NieudaneLogowanie.objects.filter(czas__gte=timezone.now() - timedelta(minutes=settings.PANEL_BLOKADA_MINUT))


def zablokowane(ip: Optional[str], nazwa: str) -> bool:
    limit = settings.PANEL_LIMIT_PROB
    ostatnie = _ostatnie()
    if ip and ostatnie.filter(ip=ip).count() >= limit:
        return True
    return bool(nazwa) and ostatnie.filter(nazwa__iexact=nazwa).count() >= limit


def zapisz_nieudane(ip: Optional[str], nazwa: str) -> None:
    NieudaneLogowanie.objects.create(ip=ip, nazwa=(nazwa or "")[:150])
    # Stare wpisy nie sa juz potrzebne
    NieudaneLogowanie.objects.filter(czas__lt=timezone.now() - timedelta(days=7)).delete()


def wyczysc(ip: Optional[str], nazwa: str) -> None:
    NieudaneLogowanie.objects.filter(nazwa__iexact=nazwa).delete()
    if ip:
        NieudaneLogowanie.objects.filter(ip=ip).delete()


# --- Logowanie przez GitHub ---

@transaction.atomic
def prowadzacy_z_githuba(dane: dict):
    """Konto prowadzacego dla konta GitHub z listy dozwolonych (tworzone przy pierwszym logowaniu).

    Powiazanie zapamietuje staly numer konta GitHub: jesli ktos inny przejmie zwolniony login,
    nie dostanie sie do panelu.
    """
    github_id, login = dane.get("id"), (dane.get("login") or "").strip()
    if not github_id or not login:
        raise BladLogowania("GitHub nie przekazał danych konta.")
    if login.lower() not in settings.PANEL_KONTA_GITHUB:
        raise BladLogowania(f"Konto GitHub @{login} nie ma dostępu do panelu.")
    konto = KontoProwadzacego.objects.select_related("uzytkownik").filter(github_id=github_id).first()
    if konto is None:
        if KontoProwadzacego.objects.filter(login__iexact=login).exists():
            raise BladLogowania(f"Login @{login} jest powiązany z innym kontem GitHub.")
        Uzytkownik = get_user_model()
        uzytkownik = Uzytkownik.objects.filter(username__iexact=login).first()
        if uzytkownik is None:
            uzytkownik = Uzytkownik(username=login.lower(), first_name=(dane.get("name") or "")[:150])
            uzytkownik.set_unusable_password()
        uzytkownik.is_staff = uzytkownik.is_superuser = uzytkownik.is_active = True
        uzytkownik.save()
        konto = KontoProwadzacego.objects.create(uzytkownik=uzytkownik, github_id=github_id, login=login)
    if not konto.uzytkownik.is_active:
        raise BladLogowania("Konto prowadzącego jest wyłączone.")
    konto.login = login
    konto.ostatnie_logowanie = timezone.now()
    konto.save(update_fields=["login", "ostatnie_logowanie"])
    return konto.uzytkownik
