"""Poprawki prowadzacego: zmiany w repozytorium zrodlowym przenoszone do repozytoriow studentow grupy.

Przebieg jednej poprawki:
1. repozytorium startowe grupy dostaje nowy commit z biezacym stanem zrodla (rodzic: poprzedni stan startowy),
2. w kazdym repozytorium studenta galaz feedback przesuwa sie na nowy stan startowy,
3. poprawka jest scalana trojstronnie z galezia main studenta (gdy wlaczone autoscalanie i nie ma konfliktu);
   w przeciwnym razie trafia na galaz poprawka-N z pull requestem, ktory student scala sam,
4. ocena automatyczna od razu uzywa nowych testow prowadzacego, a pliki w wersjach prowadzacego
   nie sa liczone jako zmiany studenta (terminy, znacznik zmienionych testow).
"""
from __future__ import annotations

import difflib
import logging
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models import Max
from django.utils import timezone

from . import uslugi
from .github import git
from .github import klient as gh
from .github.git import KATALOG_ACTIONS
from .models import Poprawka, PoprawkaZgloszenia, Przydzial, Zgloszenie

log = logging.getLogger(__name__)


def _zmiany(przydzial: Przydzial) -> dict:
    """Sciezka -> (skrot w stanie startowym grupy, skrot w zrodle) dla plikow, ktore sie roznia."""
    zestaw = przydzial.zestaw
    nowe = gh.drzewo_blobow(przydzial.zrodlo, gh.sha_galezi(przydzial.zrodlo))
    stare = gh.drzewo_blobow(przydzial.repo_startowe, przydzial.sha_startowy)
    if not zestaw.kopiuj_actions:
        nowe = {s: b for s, b in nowe.items() if not s.startswith(KATALOG_ACTIONS + "/")}
    return {s: (stare.get(s), nowe.get(s)) for s in sorted(set(nowe) | set(stare)) if nowe.get(s) != stare.get(s)}


def _lista_plikow(zmiany: dict) -> list[dict]:
    return [{"sciezka": s, "rodzaj": "dodany" if stary is None else ("usunięty" if nowy is None else "zmieniony")}
            for s, (stary, nowy) in zmiany.items()]


def podglad(przydzial: Przydzial) -> list[dict]:
    """Pliki, ktorymi biezacy stan repozytorium zrodlowego rozni sie od stanu startowego grupy."""
    return _lista_plikow(_zmiany(przydzial))


def podglad_zestawu(zestaw, teraz=None) -> dict:
    """Podglad poprawki dla wszystkich grup zestawu. Grupy z tymi samymi zmianami (to samo zrodlo i ten sam
    stan startowy) tworza jeden pakiet z jedna lista roznic. Domyslnie zaznaczone sa grupy przed terminem
    ostatecznym; po nim nowe testy od razu zmienilyby ocene automatyczna."""
    teraz = teraz or timezone.now()
    pakiety, bez_zmian, pominiete = {}, [], []
    for p in zestaw.przydzialy.select_related("grupa", "zestaw__kurs__rok").order_by("grupa__nazwa"):
        if p.w_poligonie:
            pominiete.append((p, "poligon, bez GitHuba"))
            continue
        if not p.repo_startowe or not p.sha_startowy:
            pominiete.append((p, "brak repozytorium startowego"))
            continue
        try:
            zmiany = _zmiany(p)
        except gh.BladGitHub as blad:
            pominiete.append((p, f"nie udało się porównać: {blad}"))
            continue
        if not zmiany:
            bez_zmian.append(p)
            continue
        p.trwa = p.poprawki.filter(stan__in=[Poprawka.Stan.OCZEKUJE, Poprawka.Stan.W_TOKU]).exists()
        p.po_terminie = teraz > p.termin_twardy
        p.zaznaczona = not p.trwa and not p.po_terminie
        p.studentow = p.zgloszenia.filter(status=Zgloszenie.Status.GOTOWE).count()
        klucz = (p.zrodlo, tuple(zmiany.items()))
        pakiet = pakiety.setdefault(klucz, {"zrodlo": p.zrodlo, "przydzialy": [], "pliki": _lista_plikow(zmiany)})
        pakiet["przydzialy"].append(p)
    lista = list(pakiety.values())
    for pakiet in lista:
        pakiet["roznice"] = roznice(pakiet["przydzialy"][0], pakiet["pliki"])
        pakiet["pominietych"] = max(len(pakiet["pliki"]) - MAKS_PLIKOW_PODGLADU, 0)
    return {"pakiety": lista, "bez_zmian": bez_zmian, "pominiete": pominiete}


MAKS_ROZMIAR_PODGLADU = 200_000  # bajtow na plik
MAKS_WIERSZY_PODGLADU = 400       # wierszy roznicy na plik
MAKS_PLIKOW_PODGLADU = 40


class _BezPodgladu(Exception):
    pass


def _tekst(repo: str, sha) -> str:
    if not sha:
        return ""
    dane = gh.tresc_bloba(repo, sha)
    if len(dane) > MAKS_ROZMIAR_PODGLADU:
        raise _BezPodgladu("Duży plik: podgląd pominięty.")
    try:
        return dane.decode("utf-8")
    except UnicodeDecodeError:
        raise _BezPodgladu("Plik binarny: podgląd pominięty.") from None


def roznice(przydzial: Przydzial, pliki: list) -> list[dict]:
    """Zmiany w tresci plikow wzgledem stanu startowego grupy, tylko do obejrzenia przed zatwierdzeniem poprawki.
    Kazdy wpis: sciezka, rodzaj, dodane, usuniete, wiersze [(rodzaj wiersza, tekst)], uwaga."""
    nowe = gh.drzewo_blobow(przydzial.zrodlo, gh.sha_galezi(przydzial.zrodlo))
    stare = gh.drzewo_blobow(przydzial.repo_startowe, przydzial.sha_startowy)
    wynik = []
    for plik in pliki[:MAKS_PLIKOW_PODGLADU]:
        sciezka = plik["sciezka"]
        wpis = {**plik, "dodane": 0, "usuniete": 0, "wiersze": [], "uwaga": ""}
        try:
            przed = _tekst(przydzial.repo_startowe, stare.get(sciezka))
            po = _tekst(przydzial.zrodlo, nowe.get(sciezka))
        except _BezPodgladu as powod:
            wpis["uwaga"] = str(powod)
        except gh.BladGitHub as blad:
            wpis["uwaga"] = f"Nie udało się pobrać treści: {blad}"
        else:
            roznica = list(difflib.unified_diff(przed.splitlines(), po.splitlines(), lineterm="", n=3))[2:]
            for wiersz in roznica:
                if wiersz.startswith("@@"):
                    rodzaj = "fragment"
                elif wiersz.startswith("+"):
                    rodzaj, wpis["dodane"] = "dodany", wpis["dodane"] + 1
                elif wiersz.startswith("-"):
                    rodzaj, wpis["usuniete"] = "usuniety", wpis["usuniete"] + 1
                else:
                    rodzaj = ""
                if len(wpis["wiersze"]) < MAKS_WIERSZY_PODGLADU:
                    wpis["wiersze"].append((rodzaj, wiersz))
            if len(roznica) > MAKS_WIERSZY_PODGLADU:
                wpis["uwaga"] = f"Pokazano {MAKS_WIERSZY_PODGLADU} z {len(roznica)} wierszy różnicy."
            elif not roznica:
                wpis["uwaga"] = "Zmienione tylko końce wierszy albo kodowanie."
        wynik.append(wpis)
    return wynik


def zlec(przydzial: Przydzial, opis: str, autoscalanie: bool, pliki: list) -> Poprawka:
    with transaction.atomic():
        numer = (przydzial.poprawki.aggregate(m=Max("numer"))["m"] or 0) + 1
        return Poprawka.objects.create(przydzial=przydzial, numer=numer, opis=opis.strip(),
                                       autoscalanie=autoscalanie, pliki=pliki)


def uruchom_w_tle() -> None:
    """Rozpoczyna wprowadzanie od razu (robot i tak je podejmie; blokada chroni przed dublowaniem)."""
    if not settings.SPRAWDZARKA_OCENA_W_WATKU:
        return
    import threading

    def praca():
        try:
            while przetworz(limit=1):
                pass
        finally:
            close_old_connections()

    threading.Thread(target=praca, daemon=True).start()


def przetworz(limit: int = 1) -> int:
    """Wprowadza oczekujace poprawki. Zwraca liczbe przetworzonych."""
    licznik = 0
    for poprawka in Poprawka.objects.filter(stan=Poprawka.Stan.OCZEKUJE).order_by("utworzono")[:limit]:
        zajeta = Poprawka.objects.filter(pk=poprawka.pk, stan=Poprawka.Stan.OCZEKUJE).update(
            stan=Poprawka.Stan.W_TOKU, rozpoczeto=timezone.now())
        if not zajeta:
            continue
        poprawka.refresh_from_db()
        try:
            wprowadz(poprawka)
        except Exception as blad:  # zapis bledu, zeby prowadzacy go zobaczyl
            log.exception("Poprawka %s nieudana", poprawka.pk)
            Poprawka.objects.filter(pk=poprawka.pk).update(
                stan=Poprawka.Stan.BLAD, blad_opis=str(blad)[:2000], zakonczono=timezone.now())
        licznik += 1
    return licznik


def wznow_przerwane(minuty: int = 30) -> int:
    """Poprawka przerwana w trakcie (np. restart serwera) wraca do kolejki; wprowadzanie jest powtarzalne."""
    return Poprawka.objects.filter(
        stan=Poprawka.Stan.W_TOKU, rozpoczeto__lt=timezone.now() - timedelta(minutes=minuty),
    ).update(stan=Poprawka.Stan.OCZEKUJE)


def _komunikat(poprawka: Poprawka) -> str:
    from .ocena import bez_ogonkow

    tekst = f"Poprawka prowadzacego nr {poprawka.numer}"
    if poprawka.opis:
        tekst += "\n\n" + bez_ogonkow(poprawka.opis)
    return tekst


def wprowadz(poprawka: Poprawka) -> None:
    przydzial = poprawka.przydzial
    zestaw = przydzial.zestaw
    komunikat = _komunikat(poprawka)
    if not poprawka.sha_po:
        poprzedni = przydzial.sha_startowy
        sha = git.poprawka_startowa(przydzial.zrodlo, przydzial.repo_startowe, komunikat,
                                    z_actions=zestaw.kopiuj_actions)
        if sha is None:
            poprawka.stan = Poprawka.Stan.BEZ_ZMIAN
            poprawka.zakonczono = timezone.now()
            poprawka.save(update_fields=["stan", "zakonczono"])
            return
        historia = przydzial.stany_startowe
        if sha not in historia:
            historia.append(sha)
        przydzial.historia_startowa = historia
        przydzial.sha_startowy = sha
        przydzial.save(update_fields=["historia_startowa", "sha_startowy"])
        poprawka.sha_przed, poprawka.sha_po = poprzedni, sha
        poprawka.save(update_fields=["sha_przed", "sha_po"])
    zgloszenia = (przydzial.zgloszenia.filter(status=Zgloszenie.Status.GOTOWE)
                  .exclude(poprawki__poprawka=poprawka).select_related("student"))
    for zgloszenie in zgloszenia:
        _do_zgloszenia(poprawka, zgloszenie, komunikat)
    poprawka.stan = Poprawka.Stan.GOTOWA
    poprawka.zakonczono = timezone.now()
    poprawka.save(update_fields=["stan", "zakonczono"])


def _opis_dla_studenta(poprawka: Poprawka) -> str:
    from .ocena import bez_ogonkow

    return f" ({bez_ogonkow(poprawka.opis)})" if poprawka.opis else ""


def _tresc_pr(poprawka: Poprawka, konflikty: list) -> str:
    linie = [f"Prowadzacy wprowadzil poprawke nr {poprawka.numer} do materialow zestawu{_opis_dla_studenta(poprawka)}.", ""]
    if konflikty:
        linie += [
            "Automatyczne scalenie z Twoja praca nie bylo mozliwe: Ty i prowadzacy zmieniliscie te same linie w plikach:",
            *[f"- {s}" for s in konflikty], "",
            "Scal ten pull request i rozwiaz konflikt.",
        ]
    else:
        linie.append("Scal ten pull request z Twoja praca.")
    linie += [
        "",
        "Na stronie GitHub: przycisk \"Resolve conflicts\" (gdy jest konflikt i przycisk jest dostepny), potem \"Merge pull request\".",
        "Albo lokalnie, w swoim repozytorium:",
        "",
        "    git pull",
        f"    git merge origin/{poprawka.galaz}",
        "    (jesli jest konflikt: popraw pliki, usun znaczniki <<<<<<< ======= >>>>>>>, potem git add . i git commit)",
        "    git push",
        "",
        "Nie zamykaj pull requestu \"Feedback\".",
    ]
    return "\n".join(linie)


def _do_zgloszenia(poprawka: Poprawka, zgloszenie: Zgloszenie, komunikat: str) -> None:
    def zapisz(stan, **pola):
        PoprawkaZgloszenia.objects.update_or_create(poprawka=poprawka, zgloszenie=zgloszenie, defaults={"stan": stan, **pola})

    if zgloszenie.pr_zamkniety:
        zapisz(PoprawkaZgloszenia.Stan.POMINIETA, komunikat="ocena już wysłana")
        return
    uwagi = []
    try:
        try:
            gh.zezwol_aplikacji_na_galaz(zgloszenie.repo_nazwa, settings.SPRAWDZARKA_GALAZ_FEEDBACK)
        except gh.BladGitHub as blad:
            log.warning("Nie mozna dopisac aplikacji do reguly %s: %s", zgloszenie.repo_nazwa, blad)
        wynik = git.scal_poprawke(przydzial_start(poprawka), zgloszenie.repo_nazwa, poprawka.sha_po,
                                  poprawka.galaz, poprawka.autoscalanie, komunikat)
        if not wynik.feedback:
            uwagi.append("nie przesunięto gałęzi feedback (pull request „Feedback” pokaże też poprawkę)")
        if wynik.scalona:
            zapisz(PoprawkaZgloszenia.Stan.SCALONA, komunikat="; ".join(uwagi))
            if zgloszenie.pr_numer:
                try:
                    gh.komentarz_pr(zgloszenie.repo_nazwa, zgloszenie.pr_numer, (
                        f"Prowadzacy wprowadzil poprawke nr {poprawka.numer} do materialow zestawu"
                        f"{_opis_dla_studenta(poprawka)}. Zostala juz scalona z Twoja galezia main. "
                        "Przed dalsza praca pobierz ja poleceniem: git pull"))
                except gh.BladGitHub as blad:
                    log.warning("Komentarz o poprawce %s nieudany: %s", zgloszenie.repo_nazwa, blad)
        else:
            numer = gh.utworz_pr(zgloszenie.repo_nazwa, poprawka.galaz, settings.SPRAWDZARKA_GALAZ,
                                 f"Poprawka prowadzacego nr {poprawka.numer}", _tresc_pr(poprawka, wynik.konflikty))
            zapisz(PoprawkaZgloszenia.Stan.DO_SCALENIA, pr_numer=numer, konflikty=wynik.konflikty,
                   komunikat="; ".join(uwagi))
        Zgloszenie.objects.filter(pk=zgloszenie.pk).update(sha_bazowy=poprawka.sha_po, do_przeliczenia=True)
    except uslugi.BLEDY as blad:
        log.warning("Poprawka %s dla %s nieudana: %s", poprawka.numer, zgloszenie.repo_nazwa, blad)
        zapisz(PoprawkaZgloszenia.Stan.BLAD, komunikat=str(blad)[:1000])


def przydzial_start(poprawka: Poprawka) -> str:
    return poprawka.przydzial.repo_startowe


def sprawdz_scalenia(poprawka: Poprawka | None = None) -> int:
    """Oznacza poprawki scalone przez studentow (galaz main zawiera nowy stan startowy). Zwraca liczbe zmian."""
    wpisy = (PoprawkaZgloszenia.objects.filter(stan=PoprawkaZgloszenia.Stan.DO_SCALENIA)
             .exclude(zgloszenie__przydzial__zestaw__kurs__rok__zarchiwizowany=True)
             .exclude(zgloszenie__przydzial__zestaw__kurs__rok__poligon=True).select_related("poprawka", "zgloszenie"))
    if poprawka is not None:
        wpisy = wpisy.filter(poprawka=poprawka)
    licznik = 0
    for wpis in wpisy[:300]:
        try:
            if gh.czy_zawiera(wpis.zgloszenie.repo_nazwa, wpis.poprawka.sha_po, settings.SPRAWDZARKA_GALAZ):
                wpis.stan = PoprawkaZgloszenia.Stan.SCALONA_PRZEZ_STUDENTA
                wpis.save(update_fields=["stan", "zaktualizowano"])
                licznik += 1
        except gh.BladGitHub as blad:
            log.warning("Sprawdzenie scalenia %s nieudane: %s", wpis.zgloszenie.repo_nazwa, blad)
    return licznik
