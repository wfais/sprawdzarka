"""Uruchamianie przeplywow GitHub Actions w repozytoriach studentow (zastepczy harmonogram).

Harmonogram GitHub (zdarzenie schedule) w nowych repozytoriach bywa zawodny: potrafi nie ruszyc wcale,
a uruchomienie na zadanie (workflow_dispatch) dziala zawsze. Dla zestawu ze wskazanym plikiem przeplywu
robot w pelnym cyklu sprawdza repozytoria studentow i uruchamia przeplyw, gdy od ostatniego uruchomienia
minelo dosc czasu. Uruchamianie konczy sie samo, gdy przeplyw zostanie wylaczony (np. przez siebie samego
po zebraniu danych) albo minie termin ostateczny studenta. Calosc wlacza glowny wylacznik w Ustawieniach,
a dzienny limit chroni minuty organizacji.
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from .github import klient as gh
from .models import StanPrzeplywu, UstawieniaSerwisu, Zestaw, Zgloszenie

log = logging.getLogger(__name__)

# Uruchomienia w toku: nowego nie zlecamy
W_TOKU = {"queued", "in_progress", "waiting", "requested", "pending"}
_SCHEDULE = re.compile(r"\s*schedule\s*:\s*$")
_CRON = re.compile(r"\s*-\s*cron\s*:\s*\S")


def harmonogram_wlaczony(tresc: Optional[str]) -> bool:
    """Czy plik przeplywu ma niezakomentowany harmonogram (schedule z co najmniej jedna linia cron)."""
    if not tresc:
        return False
    linie = [linia.split("#", 1)[0] for linia in tresc.splitlines()]
    return any(_SCHEDULE.fullmatch(linia) for linia in linie) and any(_CRON.match(linia) for linia in linie)


def _czas(tekst: Optional[str]) -> Optional[datetime]:
    if not tekst:
        return None
    return datetime.fromisoformat(tekst.replace("Z", "+00:00"))


def margines(co_minut: int) -> timedelta:
    """Zapas na niedokladnosc cyklu robota: uruchomienie co 60 minut przy cyklu co 30 minut."""
    return timedelta(minutes=min(10, co_minut // 5))


def do_obslugi(teraz: Optional[datetime] = None):
    """Zgloszenia z repozytoriami w zestawach z uruchamianym przeplywem (bez poligonu i lat archiwalnych)."""
    teraz = teraz or timezone.now()
    return (Zgloszenie.objects.filter(status=Zgloszenie.Status.GOTOWE, pr_zamkniety=False)
            .exclude(repo_nazwa="").exclude(przydzial__zestaw__przeplyw_plik="")
            .exclude(przydzial__zestaw__kurs__rok__zarchiwizowany=True)
            .exclude(przydzial__zestaw__kurs__rok__poligon=True)
            .filter(przydzial__termin_twardy__gte=teraz - timedelta(days=60))
            .select_related("przydzial__zestaw", "student"))


def _zapisz(zgloszenie: Zgloszenie, stan: str, opis: str, teraz: datetime, **pola) -> str:
    zgloszenie.przeplyw_stan = stan
    zgloszenie.przeplyw_opis = opis[:300]
    zgloszenie.przeplyw_sprawdzono = teraz
    for nazwa, wartosc in pola.items():
        setattr(zgloszenie, nazwa, wartosc)
    zgloszenie.save(update_fields=["przeplyw_stan", "przeplyw_opis", "przeplyw_sprawdzono", *pola])
    return stan


def _limit_wyczerpany(ustawienia: UstawieniaSerwisu, teraz: datetime) -> bool:
    dzien = timezone.localdate(teraz)
    if ustawienia.przeplywy_dzien != dzien:
        ustawienia.przeplywy_dzien, ustawienia.przeplywy_licznik = dzien, 0
        ustawienia.save(update_fields=["przeplywy_dzien", "przeplywy_licznik"])
    return ustawienia.przeplywy_licznik >= ustawienia.przeplywy_limit


def obsluz(zgloszenie: Zgloszenie, ustawienia: UstawieniaSerwisu, teraz: Optional[datetime] = None) -> str:
    """Jedno repozytorium: sprawdzenie stanu przeplywu i ewentualne uruchomienie. Zwraca nowy stan."""
    teraz = teraz or timezone.now()
    zestaw = zgloszenie.przydzial.zestaw
    plik, pelna = zestaw.przeplyw_plik, zgloszenie.repo_nazwa
    if teraz > zgloszenie.termin_twardy:
        return _zapisz(zgloszenie, StanPrzeplywu.ZAKONCZONY, "Minął termin ostateczny: przepływ nie jest już uruchamiany.",
                       teraz)
    try:
        przeplyw = gh.przeplyw(pelna, plik)
        if przeplyw is None:
            return _zapisz(zgloszenie, StanPrzeplywu.BRAK, f"W repozytorium nie ma przepływu .github/workflows/{plik}.",
                           teraz)
        if przeplyw.get("state") != "active":
            return _zapisz(zgloszenie, StanPrzeplywu.ZAKONCZONY,
                           f"Przepływ jest wyłączony ({przeplyw.get('state')}), np. sam zakończył zbieranie.", teraz)
        if zestaw.przeplyw_start == Zestaw.StartPrzeplywu.HARMONOGRAM:
            if not harmonogram_wlaczony(gh.plik_tekstowy(pelna, przeplyw.get("path") or f".github/workflows/{plik}")):
                return _zapisz(zgloszenie, StanPrzeplywu.CZEKA,
                               "Student jeszcze nie włączył harmonogramu (schedule) w pliku przepływu.", teraz)
        ostatnie = gh.ostatnie_uruchomienie(pelna, plik)
        czas_ostatniego = _czas(ostatnie.get("created_at")) if ostatnie else None
        pola = {"przeplyw_ostatnie": czas_ostatniego} if czas_ostatniego else {}
        if ostatnie and ostatnie.get("status") in W_TOKU:
            return _zapisz(zgloszenie, StanPrzeplywu.DZIALA, "Uruchomienie w toku.", teraz, **pola)
        odstep = timedelta(minutes=zestaw.przeplyw_co_minut) - margines(zestaw.przeplyw_co_minut)
        if czas_ostatniego and teraz - czas_ostatniego < odstep:
            wynik = ostatnie.get("conclusion") or ostatnie.get("status") or ""
            return _zapisz(zgloszenie, StanPrzeplywu.DZIALA, f"Ostatnie uruchomienie: {wynik}.", teraz, **pola)
        if _limit_wyczerpany(ustawienia, teraz):
            log.warning("Dzienny limit uruchomien przeplywow (%s) wyczerpany", ustawienia.przeplywy_limit)
            return _zapisz(zgloszenie, StanPrzeplywu.DZIALA, "Dzienny limit uruchomień wyczerpany, czeka na kolejną dobę.",
                           teraz, **pola)
        gh.uruchom_przeplyw(pelna, plik, settings.SPRAWDZARKA_GALAZ)
    except gh.BladGitHub as blad:
        log.warning("Przeplyw %s w %s: %s", plik, pelna, blad)
        return _zapisz(zgloszenie, StanPrzeplywu.BLAD, str(blad), teraz)
    ustawienia.przeplywy_licznik += 1
    ustawienia.save(update_fields=["przeplywy_licznik"])
    return _zapisz(zgloszenie, StanPrzeplywu.DZIALA, f"Uruchomiono {timezone.localtime(teraz):%d.%m %H:%M}.", teraz,
                   przeplyw_ostatnie=teraz, przeplyw_wyzwolenia=zgloszenie.przeplyw_wyzwolenia + 1)


def cykl(teraz: Optional[datetime] = None) -> dict:
    """Pelny cykl robota: wszystkie repozytoria z uruchamianym przeplywem. Zwraca liczniki stanow."""
    close_old_connections()
    ustawienia = UstawieniaSerwisu.biezace()
    if not ustawienia.przeplywy_wlaczone:
        return {}
    teraz = teraz or timezone.now()
    liczniki: dict = {}
    for zgloszenie in do_obslugi(teraz)[:2000]:
        stan = obsluz(zgloszenie, ustawienia, teraz)
        liczniki[stan] = liczniki.get(stan, 0) + 1
    return liczniki
