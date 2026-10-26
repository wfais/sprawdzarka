"""Struktura: wydawanie zestawow grupom, kopiowanie zestawow i przedmiotow miedzy grupami oraz latami, usuwanie."""
import logging
import os

from django.core.files.base import ContentFile
from django.db import transaction

from . import uslugi
from .models import Grupa, Kurs, Precedens, Przydzial, Rok, Student, Uczelnia, Zestaw, Zgloszenie

log = logging.getLogger(__name__)

POLA_ZESTAWU = [
    "rodzaj", "nazwa", "repo_zrodlowe", "wzorce_kodu", "punkty_max", "tresc_tekst", "zasady_oceny", "modele",
    "kopiuj_actions",
]
POLA_ZADANIA = ["kolejnosc", "kod", "katalog", "punkty", "kryteria", "tryb"]


def utworz_repo(przydzial: Przydzial) -> str:
    """Tworzy repozytorium startowe; zwraca pusty napis albo opis bledu."""
    try:
        uslugi.utworz_repo_startowe(przydzial)
        return ""
    except uslugi.BLEDY as blad:
        log.warning("Repozytorium startowe %s nieudane: %s", przydzial, blad)
        return str(blad)


def kopiuj_zestaw(zestaw: Zestaw, kurs: Kurs, skrot: str = "", nazwa: str = "") -> Zestaw:
    """Kopia zestawu (z zadaniami, PDF i regulami kalibracji) w innym przedmiocie, np. w kolejnym roku."""
    with transaction.atomic():
        nowy = Zestaw(kurs=kurs, skrot=skrot or zestaw.skrot, **{p: getattr(zestaw, p) for p in POLA_ZESTAWU})
        if nazwa:
            nowy.nazwa = nazwa
        if zestaw.tresc_pdf:
            try:
                with zestaw.tresc_pdf.open("rb") as plik:
                    nowy.tresc_pdf.save(os.path.basename(zestaw.tresc_pdf.name), ContentFile(plik.read()), save=False)
            except OSError as blad:
                log.warning("Nie skopiowano PDF zestawu %s: %s", zestaw.pk, blad)
        nowy.save()
        for zadanie in zestaw.zadania.all():
            nowy.zadania.create(**{p: getattr(zadanie, p) for p in POLA_ZADANIA})
        for regula in zestaw.precedensy.filter(aktywny=True):
            Precedens.objects.create(zestaw=nowy, kod_zadania=regula.kod_zadania, tresc=regula.tresc)
    return nowy


def kopiuj_kurs(kurs: Kurs, rok: Rok, nazwa: str = "", skrot: str = "", grupy: bool = False) -> Kurs:
    """Przedmiot z zestawami (bez wydan grupom i studentow) w innym roku akademickim."""
    with transaction.atomic():
        nowy = Kurs.objects.create(rok=rok, nazwa=nazwa or kurs.nazwa, skrot=skrot or kurs.skrot, kod=kurs.kod,
                                   opis=kurs.opis)
        for zestaw in kurs.zestawy.all():
            kopiuj_zestaw(zestaw, nowy)
        if grupy:
            for grupa in kurs.grupy.all():
                Grupa.objects.create(kurs=nowy, nazwa=grupa.nazwa, skrot=grupa.skrot, opis=grupa.opis,
                                     aktywna=grupa.aktywna)
    return nowy


# --- Usuwanie (repozytoria na GitHubie zawsze zostaja) ---

def blokada_usuniecia(obiekt) -> str:
    """Pusty napis, gdy obiekt mozna usunac; inaczej powod."""
    if isinstance(obiekt, Uczelnia):
        return "Uczelnia ma lata akademickie. Najpierw usuń jej lata." if obiekt.lata.exists() else ""
    if isinstance(obiekt, Rok):
        if obiekt.zarchiwizowany or obiekt.poligon:
            return ""
        if Zgloszenie.objects.filter(przydzial__zestaw__kurs__rok=obiekt).exists():
            return ("Rok ma zgłoszenia studentów. Najpierw go zarchiwizuj i pobierz plik archiwum, "
                    "potem usuniesz go z bazy.")
        return ""
    if isinstance(obiekt, Kurs):
        rok = obiekt.rok
    elif isinstance(obiekt, (Grupa, Zestaw)):
        rok = obiekt.kurs.rok
    else:
        rok = obiekt.zestaw.kurs.rok
    return "Rok jest zarchiwizowany (tylko do odczytu). Przywróć go albo usuń cały rok." if rok.zarchiwizowany else ""


def _usun_zgloszenia(zgloszenia) -> int:
    from .archiwum import usun_historie_repozytoriow

    repo = list(zgloszenia.exclude(repo_id=None).values_list("repo_id", flat=True))
    ile = zgloszenia.count()
    zgloszenia.delete()
    usun_historie_repozytoriow(repo)
    return ile


def _sprzataj_poligon(rok) -> None:
    if rok.poligon:
        Student.objects.filter(github_id__lt=0, zgloszenia__isnull=True).delete()


def usun_kurs(kurs) -> dict:
    """Usuwa przedmiot z grupami, zestawami i zgloszeniami. Zwraca liczniki."""
    from .archiwum import usun_przedmiot

    liczniki = usun_przedmiot(kurs)
    _sprzataj_poligon(kurs.rok)
    return liczniki


def usun_grupe(grupa) -> dict:
    """Usuwa grupe z jej zestawami w grupie i zgloszeniami. Zwraca liczniki."""
    with transaction.atomic():
        liczniki = {"zestawow": grupa.przydzialy.count(),
                    "zgloszen": _usun_zgloszenia(Zgloszenie.objects.filter(przydzial__grupa=grupa))}
        grupa.przydzialy.all().delete()
        grupa.delete()
    _sprzataj_poligon(grupa.kurs.rok)
    return liczniki


def usun_przydzial(przydzial) -> int:
    """Usuwa wydanie zestawu grupie razem ze zgloszeniami. Zwraca liczbe zgloszen."""
    with transaction.atomic():
        ile = _usun_zgloszenia(przydzial.zgloszenia.all())
        przydzial.delete()
    _sprzataj_poligon(przydzial.zestaw.kurs.rok)
    return ile


def usun_zestaw(zestaw) -> dict:
    """Usuwa zestaw (z zadaniami i PDF) oraz jego wydania we wszystkich grupach."""
    with transaction.atomic():
        liczniki = {"grup": zestaw.przydzialy.count(),
                    "zgloszen": _usun_zgloszenia(Zgloszenie.objects.filter(przydzial__zestaw=zestaw))}
        zestaw.przydzialy.all().delete()
        if zestaw.tresc_pdf:
            zestaw.tresc_pdf.delete(save=False)
        zestaw.delete()
    _sprzataj_poligon(zestaw.kurs.rok)
    return liczniki


def kopiuj_przydzial(wzor: Przydzial, grupa, termin_miekki, termin_twardy, przedrostek: str,
                     repo_zrodlowe: str = "", publikacja=None) -> Przydzial:
    """Ten sam zestaw dla innej grupy, z wlasnymi terminami (i ewentualnie innym repozytorium)."""
    return Przydzial.objects.create(
        zestaw=wzor.zestaw, grupa=grupa, publikacja=publikacja, termin_miekki=termin_miekki, termin_twardy=termin_twardy,
        przedrostek_repo=przedrostek, repo_zrodlowe=repo_zrodlowe, otwarty=wzor.otwarty, auto_ocena=wzor.auto_ocena,
    )
