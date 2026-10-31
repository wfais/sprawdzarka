"""Logika systemu: akceptacja zestawow, rejestr wypchniec, przeliczanie terminow, oceny, zerowanie."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from datetime import timezone as strefa
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Sequence

from django.conf import settings
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone

from . import nazwiska, terminy, uruchamianie
from .github import git
from .github import klient as gh
from .models import Porownanie, Przydzial, Student, Wypchniecie, Zgloszenie

log = logging.getLogger(__name__)


class BladPoligonu(Exception):
    """Operacja wymaga prawdziwego GitHuba, a dotyczy danych fikcyjnych z poligonu."""

    def __init__(self, czego="ta operacja"):
        super().__init__(f"to dane fikcyjne z poligonu, {czego} wymaga prawdziwego repozytorium na GitHubie")


BLEDY = (gh.BladGitHub, git.BladGit, BladPoligonu)
ZERO_SHA = terminy.ZERO_SHA

TRESC_PR = (
    "Ten pull request służy wyłącznie do sprawdzania Twojej pracy. **Nie scalaj go i nie zamykaj.**\n\n"
    "Pracuj na gałęzi `main`: każde wypchnięcie (`git push`) automatycznie pojawi się w zakładce "
    "*Files changed*. Prowadzący zostawi tu uwagi do kodu i ocenę."
)


def _galaz():
    return settings.SPRAWDZARKA_GALAZ


def _ref_galezi():
    return f"refs/heads/{settings.SPRAWDZARKA_GALAZ}"


# --- Poligon: dane fikcyjne bez GitHuba ---

def _w_poligonie(obiekt) -> bool:
    przydzial = obiekt if isinstance(obiekt, Przydzial) else obiekt.przydzial
    return przydzial.zestaw.kurs.rok.poligon


def sha_fikcyjny(tekst: str) -> str:
    return hashlib.sha1(tekst.encode()).hexdigest()


def nowe_repo_fikcyjne() -> int:
    """Ujemne numery repozytoriow: nie koliduja z numerami z GitHuba."""
    najmniejszy = Zgloszenie.objects.filter(repo_id__lt=0).order_by("repo_id").values_list("repo_id", flat=True).first()
    return min(najmniejszy or 0, -10_000_000) - 1


# --- Repozytorium startowe grupy ---

def utworz_repo_startowe(przydzial: Przydzial) -> None:
    """Kopia repozytorium zrodlowego (bez historii) jako repozytorium startowe danej grupy."""
    if przydzial.repo_startowe and przydzial.sha_startowy:
        return
    nazwa = f"{przydzial.przedrostek_repo}start"
    if _w_poligonie(przydzial):
        przydzial.repo_startowe = f"{settings.GITHUB_ORG}/{nazwa}"
        przydzial.sha_startowy = sha_fikcyjny(przydzial.repo_startowe)
        przydzial.save(update_fields=["repo_startowe", "sha_startowy"])
        return
    dane, _ = gh.utworz_repo(nazwa, f"Stan startowy: {przydzial.zestaw.nazwa}, {przydzial.grupa.nazwa}")
    sha = git.migawka_startowa(
        przydzial.zrodlo, dane["full_name"],
        f"Stan startowy: {przydzial.zestaw.nazwa} ({przydzial.grupa.nazwa})",
        z_actions=przydzial.zestaw.kopiuj_actions,
    )
    przydzial.repo_startowe = dane["full_name"]
    przydzial.sha_startowy = sha
    przydzial.save(update_fields=["repo_startowe", "sha_startowy"])


# --- Akceptacja zestawu przez studenta ---

def nazwa_repo_studenta(przydzial: Przydzial, login: str, numer: int = 1) -> str:
    nazwa = f"{przydzial.przedrostek_repo}{login}".lower()
    if numer > 1:
        nazwa = f"{nazwa}-{numer}"
    return nazwa[:100]


def zarejestruj_studenta(github_id: int, login: str, imie_nazwisko: str = "") -> Student:
    student, _ = Student.objects.get_or_create(github_id=github_id, defaults={"login": login})
    zmiany = []
    if student.login != login:
        student.login = login
        zmiany.append("login")
    imie_nazwisko = " ".join((imie_nazwisko or "").split())
    if imie_nazwisko and not student.imie_nazwisko:
        student.imie_nazwisko = imie_nazwisko
        student.zrodlo_nazwiska = Student.Zrodlo.FORMULARZ
        zmiany += ["imie_nazwisko", "zrodlo_nazwiska"]
    if zmiany:
        student.save(update_fields=zmiany)
    return student


def aktywne_zgloszenie(przydzial: Przydzial, student: Optional[Student]) -> Optional[Zgloszenie]:
    if student is None:
        return None
    return (
        Zgloszenie.objects.filter(przydzial=przydzial, student=student)
        .exclude(status=Zgloszenie.Status.WYZEROWANE)
        .first()
    )


def akceptuj(przydzial: Przydzial, github_id: int, login: str, imie_nazwisko: str = "") -> Zgloszenie:
    student = zarejestruj_studenta(github_id, login, imie_nazwisko)
    zgloszenie = aktywne_zgloszenie(przydzial, student)
    if zgloszenie is None:
        try:
            with transaction.atomic():
                zgloszenie = Zgloszenie.objects.create(przydzial=przydzial, student=student)
        except IntegrityError:
            zgloszenie = aktywne_zgloszenie(przydzial, student)
    if zgloszenie.status != Zgloszenie.Status.GOTOWE:
        dokoncz_zgloszenie(zgloszenie)
        zgloszenie.refresh_from_db()
    return zgloszenie


def _zajmij(zgloszenie: Zgloszenie) -> bool:
    """Blokada chroniaca przed rownoczesnym tworzeniem tego samego repozytorium (np. podwojne klikniecie)."""
    teraz = timezone.now()
    return Zgloszenie.objects.filter(pk=zgloszenie.pk).filter(
        Q(w_toku_od__isnull=True) | Q(w_toku_od__lt=teraz - timedelta(minutes=3))
    ).update(w_toku_od=teraz) == 1


def _utworz_repo_studenta(zgloszenie: Zgloszenie) -> dict:
    """Tworzy repozytorium; nazwa zajeta przez repozytorium innego zgloszenia dostaje przyrostek -2, -3..."""
    przydzial, student = zgloszenie.przydzial, zgloszenie.student
    for numer in range(1, 10):
        nazwa = nazwa_repo_studenta(przydzial, student.login, numer)
        dane, nowe = gh.utworz_repo(nazwa, f"{przydzial.zestaw.nazwa}, {przydzial.grupa.nazwa}: @{student.login}")
        if nowe or not Zgloszenie.objects.filter(repo_id=dane["id"]).exclude(pk=zgloszenie.pk).exists():
            return dane
    raise gh.BladGitHub(0, "Nie udało się znaleźć wolnej nazwy repozytorium.")


def dokoncz_zgloszenie(zgloszenie: Zgloszenie) -> bool:
    """Kolejne kroki tworzenia repozytorium studenta. Kazdy krok jest zapisywany, wiec mozna je ponawiac."""
    if not _zajmij(zgloszenie):
        return False
    if _w_poligonie(zgloszenie):
        return _dokoncz_fikcyjne(zgloszenie)
    try:
        zgloszenie.refresh_from_db()
        przydzial = zgloszenie.przydzial
        login = zgloszenie.student.login
        if not przydzial.sha_startowy:
            utworz_repo_startowe(przydzial)
        if not zgloszenie.repo_id:
            dane = _utworz_repo_studenta(zgloszenie)
            zgloszenie.repo_id = dane["id"]
            zgloszenie.repo_nazwa = dane["full_name"]
            zgloszenie.save(update_fields=["repo_id", "repo_nazwa"])
        if not zgloszenie.sha_poczatkowy:
            zgloszenie.sha_bazowy, zgloszenie.sha_poczatkowy = git.przygotuj_repo_studenta(
                przydzial.repo_startowe, zgloszenie.repo_nazwa, login,
            )
            zgloszenie.save(update_fields=["sha_bazowy", "sha_poczatkowy"])
            gh.ustaw_galaz_domyslna(zgloszenie.repo_nazwa, _galaz())
        if not zgloszenie.zaproszenie_wyslane:
            gh.zapros(zgloszenie.repo_nazwa, login)
            zgloszenie.zaproszenie_wyslane = True
            zgloszenie.save(update_fields=["zaproszenie_wyslane"])
        if not zgloszenie.pr_numer:
            zgloszenie.pr_numer = gh.utworz_pr(
                zgloszenie.repo_nazwa, _galaz(), settings.SPRAWDZARKA_GALAZ_FEEDBACK, "Feedback", TRESC_PR,
            )
            zgloszenie.save(update_fields=["pr_numer"])
            try:
                gh.chron_galaz(zgloszenie.repo_nazwa, settings.SPRAWDZARKA_GALAZ_FEEDBACK)
            except gh.BladGitHub as blad:
                log.info("Nie udalo sie zabezpieczyc galezi feedback w %s: %s", zgloszenie.repo_nazwa, blad)
        zgloszenie.status = Zgloszenie.Status.GOTOWE
        zgloszenie.blad_opis = ""
        zgloszenie.do_przeliczenia = True
        zgloszenie.save(update_fields=["status", "blad_opis", "do_przeliczenia"])
        return True
    except BLEDY as blad:
        log.warning("Tworzenie repozytorium dla zgloszenia %s nieudane: %s", zgloszenie.pk, blad)
        zgloszenie.status = Zgloszenie.Status.BLAD
        zgloszenie.blad_opis = str(blad)[:2000]
        zgloszenie.save(update_fields=["status", "blad_opis"])
        return False
    finally:
        Zgloszenie.objects.filter(pk=zgloszenie.pk).update(w_toku_od=None)


def _dokoncz_fikcyjne(zgloszenie: Zgloszenie) -> bool:
    """Poligon: repozytorium studenta tylko w bazie (bez GitHuba)."""
    zgloszenie.refresh_from_db()
    if not zgloszenie.repo_id:
        zgloszenie.repo_id = nowe_repo_fikcyjne()
        zgloszenie.repo_nazwa = f"{settings.GITHUB_ORG}/{nazwa_repo_studenta(zgloszenie.przydzial, zgloszenie.student.login)}"
    zgloszenie.sha_bazowy = zgloszenie.sha_poczatkowy = zgloszenie.sha_bazowy or sha_fikcyjny(zgloszenie.repo_nazwa)
    zgloszenie.zaproszenie_wyslane = True
    zgloszenie.pr_numer = zgloszenie.pr_numer or 1
    zgloszenie.status = Zgloszenie.Status.GOTOWE
    zgloszenie.blad_opis = ""
    zgloszenie.w_toku_od = None
    zgloszenie.save()
    przelicz(zgloszenie)
    return True


# --- Rejestr wypchniec ---

def zapisz_wypchniecie(repo_id, repo_nazwa, ref, sha_przed, sha_po, czas, autor, zrodlo, autor_id=None) -> bool:
    wpis, nowe = Wypchniecie.objects.get_or_create(
        repo_id=repo_id, ref=ref, sha_przed=sha_przed or ZERO_SHA, sha_po=sha_po or ZERO_SHA,
        defaults={"repo_nazwa": repo_nazwa, "czas": czas, "autor": autor or "", "autor_id": autor_id, "zrodlo": zrodlo},
    )
    if not nowe and autor_id and not wpis.autor_id:
        Wypchniecie.objects.filter(pk=wpis.pk).update(autor_id=autor_id)
        nowe = True  # zmiana autora wplywa na przeliczenie
    if nowe:
        Zgloszenie.objects.filter(repo_id=repo_id).update(do_przeliczenia=True)
    return nowe


def od_studenta(wypchniecie: Wypchniecie, student: Student) -> bool:
    """Czy wypchniecie zrobil sam student. Wypchniecia prowadzacego (wlasciciela organizacji) sa pomijane."""
    if wypchniecie.autor_id:
        return wypchniecie.autor_id == student.github_id
    if wypchniecie.autor:
        return wypchniecie.autor.lower() == (student.login or "").lower()
    return True  # autor nieznany: liczy sie jak wypchniecie studenta


def obsluz_webhook_push(dane: dict) -> bool:
    repo = dane.get("repository") or {}
    if not repo.get("id") or not dane.get("ref") or dane.get("deleted"):
        return False
    pushed_at = repo.get("pushed_at")
    czas = (
        datetime.fromtimestamp(pushed_at, tz=strefa.utc)
        if isinstance(pushed_at, (int, float)) else timezone.now()
    )
    return zapisz_wypchniecie(
        repo["id"], repo.get("full_name", ""), dane["ref"], dane.get("before"), dane.get("after"),
        czas, (dane.get("pusher") or {}).get("name", ""), Wypchniecie.Zrodlo.WEBHOOK,
        autor_id=(dane.get("sender") or {}).get("id"),
    )


def synchronizuj(zgloszenie: Zgloszenie) -> int:
    """Uzupelnia rejestr wypchniec z dziennika aktywnosci GitHub (gdyby powiadomienie nie dotarlo)."""
    if not zgloszenie.repo_nazwa or not zgloszenie.repo_id or _w_poligonie(zgloszenie):
        return 0
    nowe = 0
    for wpis in gh.aktywnosc(zgloszenie.repo_nazwa, _galaz()):
        if wpis.get("activity_type") == "branch_deletion" or not wpis.get("after"):
            continue
        ref = wpis.get("ref") or ""
        if not ref.startswith("refs/"):
            ref = f"refs/heads/{ref}"
        czas = datetime.fromisoformat(wpis["timestamp"].replace("Z", "+00:00"))
        nowe += zapisz_wypchniecie(
            zgloszenie.repo_id, zgloszenie.repo_nazwa, ref, wpis.get("before"), wpis.get("after"),
            czas, (wpis.get("actor") or {}).get("login", ""), Wypchniecie.Zrodlo.AKTYWNOSC,
            autor_id=(wpis.get("actor") or {}).get("id"),
        )
    return nowe


# --- Przeliczanie terminow ---

def _porownaj_z_pamiecia(zgloszenie: Zgloszenie, sha_od: str, sha_do: str) -> Optional[list]:
    wpis = Porownanie.objects.filter(repo_id=zgloszenie.repo_id, sha_od=sha_od, sha_do=sha_do).first()
    if wpis is not None:
        return wpis.pliki
    try:
        pliki = gh.porownaj(zgloszenie.repo_nazwa, sha_od, sha_do)
    except gh.BladGitHub as blad:
        if blad.status in (404, 409, 422):
            log.warning("Nie mozna porownac %s %s...%s: %s", zgloszenie.repo_nazwa, sha_od, sha_do, blad)
            return None
        raise
    Porownanie.objects.get_or_create(
        repo_id=zgloszenie.repo_id, sha_od=sha_od, sha_do=sha_do, defaults={"pliki": pliki},
    )
    return pliki


def przelicz(zgloszenie: Zgloszenie) -> None:
    if zgloszenie.status != Zgloszenie.Status.GOTOWE or not zgloszenie.repo_id:
        return
    # Flaga zdejmowana przed obliczeniem: powiadomienie w trakcie ustawi ja ponownie
    Zgloszenie.objects.filter(pk=zgloszenie.pk).update(do_przeliczenia=False)
    try:
        if _w_poligonie(zgloszenie):
            _przelicz_poligonu(zgloszenie)
        else:
            _przelicz(zgloszenie)
    except Exception:
        Zgloszenie.objects.filter(pk=zgloszenie.pk).update(do_przeliczenia=True)
        raise


def wypchniecia_zgloszenia(zgloszenie: Zgloszenie) -> tuple[list, list, list]:
    """(wszystkie, studenta, obce) wypchniecia galezi glownej; utworzenie galezi przez aplikacje pomijane."""
    student = zgloszenie.student
    wszystkie = []
    for p in Wypchniecie.objects.filter(repo_id=zgloszenie.repo_id, ref=_ref_galezi()).order_by("czas", "id"):
        p.wlasne = od_studenta(p, student)
        if not (p.sha_po == zgloszenie.sha_poczatkowy and not p.wlasne):
            wszystkie.append(p)
    pushe = [p for p in wszystkie if p.wlasne]
    obce = [p for p in wszystkie if not p.wlasne and p.sha_po != ZERO_SHA]
    return wszystkie, pushe, obce


def _przelicz(zgloszenie: Zgloszenie) -> None:
    wszystkie, pushe, obce = wypchniecia_zgloszenia(zgloszenie)
    wzorce = zgloszenie.przydzial.zestaw.lista_wzorcow()
    niepewne = []
    # Zmiany prowadzacego (poprawki stanu startowego, wypchniecia z innych kont) nie sa zmianami studenta
    potrzebne_wersje = bool(obce) or len(zgloszenie.przydzial.stany_startowe) > 1
    wersje_kodu: dict = {}
    wczytane = []

    def kod_zmieniony(sha_od, sha_do):
        pliki = _porownaj_z_pamiecia(zgloszenie, sha_od, sha_do)
        if pliki is None:
            niepewne.append((sha_od, sha_do))
            return True
        pliki = [p for p in pliki if terminy.pasuje_do_wzorcow(p, wzorce)]
        if pliki and potrzebne_wersje:
            if not wczytane:
                wersje_kodu.update(_wersje_prowadzacego(zgloszenie, obce, tylko_testy=False))
                wczytane.append(True)
            pliki = _bez_zmian_prowadzacego(zgloszenie, sha_do, pliki, wersje_kodu)
        return bool(pliki)

    wynik = terminy.oblicz(
        [terminy.Push(p.czas, p.sha_po, p.sha_przed, p.wlasne) for p in wszystkie],
        zgloszenie.sha_bazowy, zgloszenie.sha_poczatkowy,
        zgloszenie.termin_miekki, zgloszenie.termin_twardy, timezone.now(),
        kod_zmieniony, settings.SPRAWDZARKA_MNOZNIK_SPOZNIENIA,
    )
    _zapisz_wynik(zgloszenie, wynik, pushe, obce, niepewne)
    _sprawdz_testy(zgloszenie, pushe, obce)
    _sprawdz_readme(zgloszenie)


def _przelicz_poligonu(zgloszenie: Zgloszenie) -> None:
    """Poligon: terminy liczone wylacznie z bazy. Zmienione pliki wypchniec sa w pamieci porownan."""
    wszystkie, pushe, obce = wypchniecia_zgloszenia(zgloszenie)
    wzorce = zgloszenie.przydzial.zestaw.lista_wzorcow()
    lancuch = [zgloszenie.sha_poczatkowy] + [p.sha_po for p in wszystkie]
    porownania = {(w.sha_od, w.sha_do): w.pliki for w in Porownanie.objects.filter(repo_id=zgloszenie.repo_id)}
    pliki_krokow = [porownania.get((p.sha_przed, p.sha_po)) for p in wszystkie]

    def kod_zmieniony(sha_od, sha_do):
        if sha_od not in lancuch or sha_do not in lancuch:
            return True
        od, do = lancuch.index(sha_od), lancuch.index(sha_do)
        if od >= do:
            return True
        kroki = pliki_krokow[od:do]
        if any(k is None for k in kroki):
            return True
        return any(terminy.pasuje_do_wzorcow(p, wzorce) for k in kroki for p in k)

    wynik = terminy.oblicz(
        [terminy.Push(p.czas, p.sha_po, p.sha_przed, p.wlasne) for p in wszystkie],
        zgloszenie.sha_bazowy, zgloszenie.sha_poczatkowy,
        zgloszenie.termin_miekki, zgloszenie.termin_twardy, timezone.now(),
        kod_zmieniony, settings.SPRAWDZARKA_MNOZNIK_SPOZNIENIA,
    )
    _zapisz_wynik(zgloszenie, wynik, pushe, obce, [])


def _zapisz_wynik(zgloszenie: Zgloszenie, wynik, pushe, obce, niepewne) -> None:
    opis = wynik.opis
    if obce:
        opis += f" Pominięto wypchnięcia z innych kont niż konto studenta: {len(obce)}."
    if niepewne:
        opis += " Uwaga: części historii nie udało się porównać (sprawdź ręcznie)."
    zgloszenie.stan_kolor = wynik.kolor
    zgloszenie.stan_opis = opis[:300]
    zgloszenie.sugerowany_mnoznik = wynik.sugerowany_mnoznik
    zgloszenie.sha_oceniany = wynik.sha_oceniany
    zgloszenie.sha_teraz = wynik.sha_teraz
    zgloszenie.ostatnie_wypchniecie = max(
        (p.czas for p in pushe if p.sha_po not in (zgloszenie.sha_poczatkowy, ZERO_SHA)), default=None,
    )
    zgloszenie.przeliczono = timezone.now()
    zgloszenie.save(update_fields=[
        "stan_kolor", "stan_opis", "sugerowany_mnoznik", "sha_oceniany", "sha_teraz",
        "ostatnie_wypchniecie", "przeliczono",
    ])


KATEGORIE_ZMIAN_TESTOW = ("zmienione", "usuniete", "dodane")


def _pliki_prowadzacego_zmienione(zgloszenie: Zgloszenie, sha: str) -> Optional[list]:
    """Sciezki testow, ich danych i konfiguracji pytest rozniace sie od stanu startowego (None: nie wiadomo)."""
    pliki = _porownaj_z_pamiecia(zgloszenie, zgloszenie.sha_bazowy, sha)
    if pliki is None:
        return None
    return [p for p in pliki if uruchamianie.nalezy_do_prowadzacego(p)]


def _klasyfikuj_zmiany_testow(zgloszenie: Zgloszenie, sha: str, pliki: list) -> dict:
    wynik = {k: [] for k in KATEGORIE_ZMIAN_TESTOW}
    if not pliki:
        return wynik
    try:
        przed = gh.drzewo_blobow(zgloszenie.repo_nazwa, zgloszenie.sha_bazowy)
        po = gh.drzewo_blobow(zgloszenie.repo_nazwa, sha)
    except gh.BladGitHub as blad:
        log.warning("Nie mozna sklasyfikowac zmian testow %s: %s", zgloszenie.repo_nazwa, blad)
        przed = po = None
    for sciezka in pliki:
        if przed is None:
            kategoria = "zmienione"
        elif sciezka not in przed:
            kategoria = "dodane"
        elif sciezka not in po:
            kategoria = "usuniete"
        else:
            kategoria = "zmienione"
        wynik[kategoria].append(sciezka)
    return wynik


def _wersje_prowadzacego(zgloszenie: Zgloszenie, obce: Sequence, tylko_testy: bool = True) -> dict:
    """Wersje plikow pochodzace od prowadzacego: sciezka -> zbior skrotow zawartosci (None: plik usuniety).

    Zrodla: kolejne stany startowe grupy (gdy byly poprawki) i wypchniecia z innych kont niz konto studenta.
    Plik w takiej wersji nie jest zmiana studenta, nawet jesli rozni sie od biezacego stanu startowego.

    Plik z kodem zmieniony wypchnieciem z innego konta jest wersja prowadzacego tylko wtedy, gdy przed tym
    wypchnieciem mial wersje startowa albo wczesniejsza wersje prowadzacego. Jesli prowadzacy poprawil plik,
    ktory student juz zmienil, wynik nadal jest praca studenta (inaczej praca "znikalaby" z terminow i oceny).
    Pliki testow prowadzacego zmienione z innego konta zawsze sa jego wersja.
    """
    wersje: dict = {}

    def dodaj(sciezka, skrot):
        if not tylko_testy or uruchamianie.nalezy_do_prowadzacego(sciezka):
            wersje.setdefault(sciezka, set()).add(skrot)

    przydzial = zgloszenie.przydzial
    stany = przydzial.stany_startowe
    if len(stany) > 1 and przydzial.repo_startowe:
        poprzednie = None
        for sha in stany:
            try:
                drzewo = gh.drzewo_blobow(przydzial.repo_startowe, sha)
            except gh.BladGitHub as blad:
                log.warning("Nie mozna odczytac stanu startowego %s: %s", sha, blad)
                continue
            for sciezka, skrot in drzewo.items():
                dodaj(sciezka, skrot)
            for sciezka in set(poprzednie or {}) - set(drzewo):
                dodaj(sciezka, None)
            poprzednie = drzewo
    startowe = None
    for p in obce:
        if not p.sha_przed or p.sha_przed == ZERO_SHA:
            continue
        pliki = _porownaj_z_pamiecia(zgloszenie, p.sha_przed, p.sha_po)
        pliki = [s for s in (pliki or []) if not tylko_testy or uruchamianie.nalezy_do_prowadzacego(s)]
        if not pliki:
            continue
        try:
            drzewo = gh.drzewo_blobow(zgloszenie.repo_nazwa, p.sha_po)
        except gh.BladGitHub:
            continue
        kod = [s for s in pliki if not uruchamianie.nalezy_do_prowadzacego(s)]
        przed = None
        if kod:
            try:
                przed = gh.drzewo_blobow(zgloszenie.repo_nazwa, p.sha_przed)
                if startowe is None:
                    startowe = gh.drzewo_blobow(zgloszenie.repo_nazwa, zgloszenie.sha_bazowy)
            except gh.BladGitHub:
                przed = None  # bez pewnosci plik z kodem zostaje praca studenta
        for sciezka in pliki:
            if sciezka in kod:
                if przed is None:
                    continue
                poprzednia = przed.get(sciezka)
                if poprzednia != startowe.get(sciezka) and poprzednia not in wersje.get(sciezka, ()):
                    continue  # prowadzacy zmienil prace studenta: to nadal praca studenta
            dodaj(sciezka, drzewo.get(sciezka))
    return wersje


def wersje_dla_oceny(zgloszenie: Zgloszenie) -> dict:
    """Wersje plikow od prowadzacego dla oceny automatycznej (puste, gdy nie bylo poprawek ani obcych wypchniec)."""
    _, _, obce = wypchniecia_zgloszenia(zgloszenie)
    if not obce and len(zgloszenie.przydzial.stany_startowe) <= 1:
        return {}
    return _wersje_prowadzacego(zgloszenie, obce, tylko_testy=False)


def _bez_zmian_prowadzacego(zgloszenie: Zgloszenie, sha: str, pliki: list, wersje: dict) -> list:
    """Usuwa pliki, ktore w danym stanie maja wersje wprowadzona przez prowadzacego."""
    if not any(s in wersje for s in pliki):
        return pliki
    try:
        drzewo = gh.drzewo_blobow(zgloszenie.repo_nazwa, sha)
    except gh.BladGitHub:
        return pliki
    return [s for s in pliki if not (s in wersje and drzewo.get(s) in wersje[s])]


def _sprawdz_testy(zgloszenie: Zgloszenie, pushe: list, obce: Sequence = ()) -> None:
    """Znakowanie zgloszen, w ktorych student zmienil pliki testow prowadzacego (jak flaga w GitHub Classroom).

    Sprawdzany jest stan biezacy i oceniany oraz kazdy wczesniej wypchniety stan galezi glownej,
    wiec zmiana pozniej przywrocona tez zostawia slad. Porownania sa zapamietywane w bazie,
    wiec kazdy stan kosztuje jedno zapytanie do GitHub tylko raz.
    """
    if not zgloszenie.sha_bazowy:
        return
    pomijane = {"", zgloszenie.sha_bazowy, zgloszenie.sha_poczatkowy, ZERO_SHA}
    biezace = [s for s in dict.fromkeys([zgloszenie.sha_teraz, zgloszenie.sha_oceniany]) if s not in pomijane]
    historia = [s for s in dict.fromkeys(p.sha_po for p in pushe) if s not in pomijane and s not in biezace]
    zmiany = {k: set() for k in KATEGORIE_ZMIAN_TESTOW}
    niepewne = False
    wersje = (_wersje_prowadzacego(zgloszenie, obce)
              if obce or len(zgloszenie.przydzial.stany_startowe) > 1 else {})
    for sha in biezace:
        pliki = _pliki_prowadzacego_zmienione(zgloszenie, sha)
        if pliki is None:
            niepewne = True
            continue
        pliki = _bez_zmian_prowadzacego(zgloszenie, sha, pliki, wersje)
        for kategoria, sciezki in _klasyfikuj_zmiany_testow(zgloszenie, sha, pliki).items():
            zmiany[kategoria].update(sciezki)
    teraz = set().union(*zmiany.values())
    wczesniej = set()
    for sha in historia:
        pliki = _pliki_prowadzacego_zmienione(zgloszenie, sha)
        if pliki is None:
            niepewne = True
            continue
        wczesniej.update(_bez_zmian_prowadzacego(zgloszenie, sha, pliki, wersje))
    wczesniej -= teraz

    dane = {k: sorted(v) for k, v in zmiany.items() if v}
    if wczesniej:
        dane["wczesniej"] = sorted(wczesniej)
    flaga = bool(dane)
    if niepewne:
        if not flaga and zgloszenie.testy_zmienione:
            # Brak pewnosci nie zdejmuje wczesniej postawionej flagi
            dane, flaga = dict(zgloszenie.zmiany_testow or {}), True
        dane["niepewne"] = True
    if flaga != zgloszenie.testy_zmienione or dane != (zgloszenie.zmiany_testow or {}):
        zgloszenie.testy_zmienione = flaga
        zgloszenie.zmiany_testow = dane
        zgloszenie.save(update_fields=["testy_zmienione", "zmiany_testow"])


def _sprawdz_readme(zgloszenie: Zgloszenie) -> None:
    """Gdy student nie ma ustalonego nazwiska: proba odczytu kandydata z README (do zatwierdzenia)."""
    student = zgloszenie.student
    sha = zgloszenie.sha_teraz
    if student.imie_nazwisko or not sha or sha == zgloszenie.sha_readme:
        return
    try:
        kandydat = nazwiska.wyciagnij_nazwisko(gh.readme(zgloszenie.repo_nazwa, sha) or "")
        if kandydat and zgloszenie.sha_bazowy:
            # Odrzucenie przykladu pozostawionego w README startowym
            wzor = nazwiska.wyciagnij_nazwisko(gh.readme(zgloszenie.repo_nazwa, zgloszenie.sha_bazowy) or "")
            if wzor == kandydat:
                kandydat = None
    except gh.BladGitHub as blad:
        log.warning("Odczyt README %s nieudany: %s", zgloszenie.repo_nazwa, blad)
        return
    if kandydat and kandydat != student.kandydat_readme:
        student.kandydat_readme = kandydat
        student.kandydat_repo = zgloszenie.repo_nazwa
        student.save(update_fields=["kandydat_readme", "kandydat_repo"])
    zgloszenie.sha_readme = sha
    zgloszenie.save(update_fields=["sha_readme"])


def odswiez_przydzial(przydzial: Przydzial, synchronizacja: bool = True) -> tuple[int, list[str]]:
    """Synchronizacja i przeliczenie wszystkich zgloszen zestawu w grupie. Zwraca (liczba, bledy)."""
    bledy, licznik = [], 0
    for zgloszenie in przydzial.zgloszenia.filter(status=Zgloszenie.Status.GOTOWE).select_related("student"):
        try:
            if synchronizacja:
                synchronizuj(zgloszenie)
            przelicz(zgloszenie)
            licznik += 1
        except BLEDY as blad:
            bledy.append(f"@{zgloszenie.student.login}: {blad}")
    return licznik, bledy


def cykl_robota(pelny: bool) -> None:
    """Jedno przejscie robota: przeliczenie oczekujacych, a w pelnym cyklu tez synchronizacja."""
    close_old_connections()
    teraz = timezone.now()
    # Porzucone tworzenie repozytorium (np. przerwane zadanie przegladarki)
    for zgloszenie in Zgloszenie.objects.filter(
        Q(w_toku_od__isnull=True) | Q(w_toku_od__lt=teraz - timedelta(minutes=3)),
        status=Zgloszenie.Status.TWORZENIE, utworzono__lt=teraz - timedelta(minutes=2),
    ).exclude(przydzial__zestaw__kurs__rok__poligon=True)[:20]:
        dokoncz_zgloszenie(zgloszenie)
    # Lata zarchiwizowane sa zamrozone: robot ich nie synchronizuje ani nie przelicza
    zgloszenia = (Zgloszenie.objects.filter(status=Zgloszenie.Status.GOTOWE)
                  .exclude(przydzial__zestaw__kurs__rok__zarchiwizowany=True).exclude(przydzial__zestaw__kurs__rok__poligon=True)
                  .select_related("przydzial", "student"))
    if pelny:
        # Synchronizacja i przeliczenie zestawow, ktorych termin ostateczny minal nie dawniej niz 30 dni temu
        zgloszenia = zgloszenia.filter(przydzial__termin_twardy__gte=teraz - timedelta(days=30), pr_zamkniety=False)
    else:
        zgloszenia = zgloszenia.filter(do_przeliczenia=True)
    for zgloszenie in zgloszenia[:500]:
        try:
            if pelny:
                synchronizuj(zgloszenie)
            przelicz(zgloszenie)
        except BLEDY as blad:
            log.warning("Przeliczenie zgloszenia %s nieudane: %s", zgloszenie.pk, blad)


# --- Ocena i zamkniecie pull requestu ---

def liczba(wartosc: Decimal) -> str:
    return format(Decimal(wartosc).normalize(), "f").replace(".", ",")


def wynik_punktowy(punkty: Decimal, mnoznik: Decimal) -> Decimal:
    return (Decimal(punkty) * Decimal(mnoznik)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def tresc_oceny(zgloszenie: Zgloszenie, punkty: Decimal, mnoznik: Decimal, komentarz: str,
                rozbicie: Optional[list] = None) -> str:
    """Komentarz z ocena w ukladzie: punkty zadan, RAZEM (z mnoznikiem), komentarze do zadan."""
    maks = zgloszenie.przydzial.zestaw.punkty_max
    wynik = wynik_punktowy(punkty, mnoznik)
    blok = [f"{kod}: {liczba(pkt)} / {liczba(mx)}" for kod, pkt, mx in (rozbicie or [])]
    if blok:
        blok.append("")
    if mnoznik != 1:
        blok.append(f"RAZEM: {liczba(punkty)} x {liczba(mnoznik)} = {liczba(wynik)} / {liczba(maks)}")
    else:
        blok.append(f"RAZEM: {liczba(wynik)} / {liczba(maks)}")
    komentarz = (komentarz or "").strip().replace("```", "` ` `")
    if komentarz:
        blok += ["", komentarz]
    return "\n".join([f"## Ocena: {liczba(wynik)} / {liczba(maks)} pkt", "", "```", *blok, "```", ""])


def zamknij_z_ocena(zgloszenie: Zgloszenie, punkty: Decimal, mnoznik: Decimal, komentarz: str,
                    rozbicie: Optional[list] = None, uwagi: Optional[list] = None,
                    commit_id: str = "") -> bool:
    """Komentarz z ocena w pull requescie i jego zamkniecie (bez scalania).

    Decyzja zapisywana jest przed wysylka, a ten sam komentarz nie jest wysylany drugi raz
    (bezpieczne ponawianie po bledzie). Uwagi do linii kodu trafiaja jako recenzja; gdy GitHub
    ich nie przyjmie (linia spoza zmian), sa dopisywane do komentarza. Zwraca True, gdy uwagi
    zostaly przypiete do linii.
    """
    zgloszenie.punkty = punkty
    zgloszenie.mnoznik = mnoznik
    zgloszenie.komentarz_koncowy = komentarz
    zgloszenie.oceniono = timezone.now()
    zgloszenie.save(update_fields=["punkty", "mnoznik", "komentarz_koncowy", "oceniono"])
    przypiete = False
    klucz = tresc_oceny(zgloszenie, punkty, mnoznik, komentarz, rozbicie)
    if _w_poligonie(zgloszenie):
        # Poligon: ocena zapisana tylko w bazie, tak jakby trafila do pull requestu
        zgloszenie.tresc_oceny_wyslana = klucz
        zgloszenie.pr_zamkniety = True
        zgloszenie.save(update_fields=["tresc_oceny_wyslana", "pr_zamkniety"])
        return False
    znacznik_recenzji = klucz + "\n<!-- recenzja wyslana -->"
    if zgloszenie.pr_numer and klucz != zgloszenie.tresc_oceny_wyslana:
        # Ta ocena nie zostala jeszcze wyslana (przy ponowieniu po bledzie nic nie dublujemy)
        if uwagi and zgloszenie.tresc_oceny_wyslana == znacznik_recenzji:
            przypiete = True  # recenzja poszla przy poprzedniej probie
        elif uwagi:
            try:
                gh.recenzja_pr(zgloszenie.repo_nazwa, zgloszenie.pr_numer, uwagi, commit_id or zgloszenie.sha_oceniany)
                przypiete = True
                zgloszenie.tresc_oceny_wyslana = znacznik_recenzji
                zgloszenie.save(update_fields=["tresc_oceny_wyslana"])
            except gh.BladGitHub as blad:
                log.info("Uwagi do linii nieprzyjete (%s), dopisuje do komentarza", blad)
                komentarz = (komentarz or "").rstrip() + "\n\n" + "\n".join(
                    f"{u['plik']}:{u['linia']}: {u['tekst']}" for u in uwagi)
        gh.komentarz_pr(zgloszenie.repo_nazwa, zgloszenie.pr_numer,
                        tresc_oceny(zgloszenie, punkty, mnoznik, komentarz, rozbicie))
        zgloszenie.tresc_oceny_wyslana = klucz
        zgloszenie.save(update_fields=["tresc_oceny_wyslana"])
    if zgloszenie.pr_numer:
        try:
            gh.zamknij_pr(zgloszenie.repo_nazwa, zgloszenie.pr_numer)
        except gh.BladGitHub as blad:
            if blad.status != 422:  # 422: pull request juz scalony lub zamkniety
                raise
    zgloszenie.pr_zamkniety = True
    zgloszenie.save(update_fields=["pr_zamkniety"])
    return przypiete


def ponow_zaproszenie(zgloszenie: Zgloszenie) -> None:
    """Ponowne zaproszenie (zaproszenia wygasaja po 7 dniach); dla wspolpracownika nic nie zmienia."""
    if _w_poligonie(zgloszenie):
        raise BladPoligonu("zaproszenie")
    gh.zapros(zgloszenie.repo_nazwa, zgloszenie.student.login)


# --- Zerowanie zgloszenia ---

def wyzeruj(zgloszenie: Zgloszenie, usun_repo: bool = False) -> list[str]:
    """Odlacza zgloszenie, aby student mogl zaakceptowac zestaw od nowa. Zwraca komunikaty."""
    komunikaty = []
    if zgloszenie.repo_nazwa and not _w_poligonie(zgloszenie):
        try:
            gh.odbierz_dostep(zgloszenie.repo_nazwa, zgloszenie.student.login)
        except gh.BladGitHub as blad:
            komunikaty.append(f"Nie udało się odebrać dostępu do repozytorium: {blad}")
        try:
            if usun_repo:
                gh.usun_repo(zgloszenie.repo_nazwa)
                komunikaty.append(f"Repozytorium {zgloszenie.repo_nazwa} usunięte.")
            else:
                krotka = zgloszenie.repo_nazwa.split("/", 1)[-1]
                nowa = f"{krotka}-arch-{timezone.localtime():%Y%m%d%H%M}"[:100]
                dane = gh.zmien_nazwe(zgloszenie.repo_nazwa, nowa)
                gh.archiwizuj(dane["full_name"])
                zgloszenie.repo_nazwa = dane["full_name"]
                komunikaty.append(f"Repozytorium przeniesione do archiwum jako {dane['full_name']}.")
        except gh.BladGitHub as blad:
            komunikaty.append(f"Nie udało się zarchiwizować ani usunąć repozytorium: {blad}")
    zgloszenie.status = Zgloszenie.Status.WYZEROWANE
    zgloszenie.save(update_fields=["status", "repo_nazwa"])
    komunikaty.append("Zgłoszenie wyzerowane, student może ponownie zaakceptować zestaw.")
    return komunikaty
