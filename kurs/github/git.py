"""Operacje git wykonywane na serwerze: kopiowanie stanu startowego do nowych repozytoriow.

Uzywamy zwyklego programu git z tokenem instalacji aplikacji (wazny godzine).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from django.conf import settings

from . import klient


class BladGit(Exception):
    pass


def adres_repo(pelna: str, token: str) -> str:
    host = urlparse(settings.GITHUB_URL).netloc
    return f"https://x-access-token:{token}@{host}/{pelna}.git"


def _srodowisko() -> dict:
    env = dict(os.environ)
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": settings.SPRAWDZARKA_GIT_AUTOR,
        "GIT_AUTHOR_EMAIL": settings.SPRAWDZARKA_GIT_EMAIL,
        "GIT_COMMITTER_NAME": settings.SPRAWDZARKA_GIT_AUTOR,
        "GIT_COMMITTER_EMAIL": settings.SPRAWDZARKA_GIT_EMAIL,
    })
    return env


def _git(argumenty: list[str], katalog: str, token: Optional[str] = None) -> str:
    polecenie = ["git", "-c", "credential.helper=", "-c", "core.autocrlf=false", *argumenty]
    try:
        wynik = subprocess.run(
            polecenie, cwd=katalog, env=_srodowisko(), capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        raise BladGit(f"git {argumenty[0]}: przekroczono limit czasu") from None
    except OSError as blad:
        raise BladGit(f"Nie udało się uruchomić git: {blad}") from None
    if wynik.returncode != 0:
        tekst = (wynik.stderr or wynik.stdout).strip()
        if token:
            tekst = tekst.replace(token, "***")
        if "workflow" in tekst and "permission" in tekst:
            raise BladGit(
                "Repozytorium zawiera konfigurację GitHub Actions (.github/workflows), a aplikacja GitHub nie ma "
                "uprawnienia Workflows. Dodaj je w ustawieniach aplikacji (Permissions & events, Repository "
                "permissions, Workflows: Read and write) albo wyłącz kopiowanie GitHub Actions w zestawie."
            )
        raise BladGit(f"git {argumenty[0]}: {tekst}")
    return wynik.stdout.strip()


def _galezie_zdalne(pelna: str, token: str, katalog: str) -> dict[str, str]:
    galezie = {}
    for linia in _git(["ls-remote", "--heads", adres_repo(pelna, token)], katalog, token).splitlines():
        sha, _, ref = linia.partition("\t")
        galezie[ref.removeprefix("refs/heads/")] = sha
    return galezie


def _katalog_roboczy():
    return tempfile.TemporaryDirectory(prefix="sprawdzarka-", ignore_cleanup_errors=True)


KATALOG_ACTIONS = ".github/workflows"


def _drzewo_zrodla(zrodlo: str, token: str, katalog: str, z_actions: bool) -> tuple[str, bool]:
    """Drzewo biezacego stanu zrodla (bez historii); bez z_actions z pominieciem .github/workflows."""
    _git(["fetch", "-q", "--depth", "1", adres_repo(zrodlo, token), "HEAD"], katalog, token)
    drzewo = _git(["rev-parse", "FETCH_HEAD^{tree}"], katalog)
    if z_actions:
        return drzewo, False
    _git(["read-tree", "FETCH_HEAD"], katalog)
    _git(["rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", KATALOG_ACTIONS], katalog)
    bez_actions = _git(["write-tree"], katalog)
    return bez_actions, bez_actions != drzewo


def migawka_startowa(zrodlo: str, cel: str, komunikat: str, z_actions: bool = False) -> str:
    """Kopiuje biezacy stan repozytorium zrodlowego jako jeden commit (bez historii) do galezi main celu.

    Brak historii chroni przed wyciekiem rozwiazan z wczesniejszych commitow zrodla.
    Bez z_actions pomijany jest katalog .github/workflows (konfiguracja GitHub Actions, np. pozostalosc
    po GitHub Classroom), zeby GitHub nie uruchamial jej przy kazdym wypchnieciu studenta.
    """
    galaz = settings.SPRAWDZARKA_GALAZ
    token = klient.token_instalacji()
    with _katalog_roboczy() as katalog:
        _git(["init", "-q"], katalog)
        istniejace = _galezie_zdalne(cel, token, katalog)
        if galaz in istniejace:
            return istniejace[galaz]
        drzewo, pominieto = _drzewo_zrodla(zrodlo, token, katalog, z_actions)
        if pominieto:
            komunikat += f"\n\nPominieto {KATALOG_ACTIONS} (konfiguracja GitHub Actions z repozytorium zrodlowego)."
        sha = _git(["commit-tree", drzewo, "-m", komunikat], katalog)
        _git(["push", "-q", adres_repo(cel, token), f"{sha}:refs/heads/{galaz}"], katalog, token)
    return sha


def przygotuj_repo_studenta(start: str, cel: str, login: str) -> tuple[str, str]:
    """Wypycha stan startowy do repozytorium studenta. Zwraca (sha_bazowy, sha_poczatkowy).

    Galaz feedback wskazuje stan startowy, a main dodatkowy pusty commit. Dzieki temu
    pull request "Feedback" (main do feedback) mozna otworzyc od razu i pokazuje on
    wszystkie zmiany studenta wzgledem stanu startowego.
    """
    galaz = settings.SPRAWDZARKA_GALAZ
    feedback = settings.SPRAWDZARKA_GALAZ_FEEDBACK
    token = klient.token_instalacji()
    with _katalog_roboczy() as katalog:
        _git(["init", "-q"], katalog)
        istniejace = _galezie_zdalne(cel, token, katalog)
        if galaz in istniejace:
            # Powtorka po przerwanym tworzeniu: odtworzenie stanu z repozytorium docelowego
            sha_poczatkowy = istniejace[galaz]
            if feedback in istniejace:
                return istniejace[feedback], sha_poczatkowy
            _git(["fetch", "-q", adres_repo(cel, token), f"refs/heads/{galaz}"], katalog, token)
            sha_bazowy = _git(["rev-parse", "FETCH_HEAD^"], katalog)
            _git(["push", "-q", adres_repo(cel, token), f"{sha_bazowy}:refs/heads/{feedback}"], katalog, token)
            return sha_bazowy, sha_poczatkowy
        _git(["fetch", "-q", adres_repo(start, token), f"refs/heads/{galaz}"], katalog, token)
        sha_bazowy = _git(["rev-parse", "FETCH_HEAD"], katalog)
        drzewo = _git(["rev-parse", "FETCH_HEAD^{tree}"], katalog)
        sha_poczatkowy = _git(
            ["commit-tree", drzewo, "-p", sha_bazowy, "-m", f"Repozytorium utworzone dla @{login}"], katalog,
        )
        _git(["push", "-q", adres_repo(cel, token), f"{sha_poczatkowy}:refs/heads/{galaz}"], katalog, token)
        _git(["push", "-q", adres_repo(cel, token), f"{sha_bazowy}:refs/heads/{feedback}"], katalog, token)
    return sha_bazowy, sha_poczatkowy


# --- Poprawki prowadzacego (zmiany w repozytorium zrodlowym po wydaniu zestawu) ---

def poprawka_startowa(zrodlo: str, start: str, komunikat: str, z_actions: bool = False) -> Optional[str]:
    """Nowy commit na galezi main repozytorium startowego z biezacym stanem zrodla.

    Rodzicem jest dotychczasowy stan startowy, wiec repozytoria studentow moga go scalic zwyklym
    scaleniem trojstronnym. Zwraca sha nowego stanu albo None, gdy zrodlo nie rozni sie od stanu startowego.
    """
    galaz = settings.SPRAWDZARKA_GALAZ
    token = klient.token_instalacji()
    with _katalog_roboczy() as katalog:
        _git(["init", "-q"], katalog)
        _git(["fetch", "-q", adres_repo(start, token), f"refs/heads/{galaz}"], katalog, token)
        poprzedni = _git(["rev-parse", "FETCH_HEAD"], katalog)
        drzewo_poprzednie = _git(["rev-parse", "FETCH_HEAD^{tree}"], katalog)
        drzewo, _ = _drzewo_zrodla(zrodlo, token, katalog, z_actions)
        if drzewo == drzewo_poprzednie:
            return None
        sha = _git(["commit-tree", drzewo, "-p", poprzedni, "-m", komunikat], katalog)
        _git(["push", "-q", adres_repo(start, token), f"{sha}:refs/heads/{galaz}"], katalog, token)
    return sha


@dataclass
class WynikScalenia:
    scalona: bool          # poprawka trafila na galaz main studenta
    sha: str = ""          # stan main po scaleniu
    konflikty: list = field(default_factory=list)  # pliki z konfliktem (gdy scalenie automatyczne sie nie udalo)
    feedback: bool = True  # czy galaz feedback przesunieto na nowy stan startowy


def scal_poprawke(start: str, cel: str, sha_poprawki: str, galaz_poprawki: str, autoscalanie: bool,
                  komunikat: str) -> WynikScalenia:
    """Przenosi poprawke do repozytorium studenta bez niszczenia jego pracy.

    Galaz feedback przesuwa sie na nowy stan startowy (pull request "Feedback" pokazuje dalej tylko prace
    studenta). Przy autoscalaniu poprawka jest scalana trojstronnie z galezia main; gdy scalenie sie nie
    udaje (te same linie zmienione przez prowadzacego i studenta) albo autoscalanie jest wylaczone,
    poprawka trafia na osobna galaz, ktora student scala sam (pull request).
    """
    galaz = settings.SPRAWDZARKA_GALAZ
    feedback = settings.SPRAWDZARKA_GALAZ_FEEDBACK
    token = klient.token_instalacji()
    adres_celu = adres_repo(cel, token)
    with _katalog_roboczy() as katalog:
        _git(["init", "-q"], katalog)
        _git(["fetch", "-q", adres_repo(start, token), f"+refs/heads/{galaz}:refs/remotes/start/{galaz}"], katalog, token)
        try:
            _git(["push", "-q", adres_celu, f"{sha_poprawki}:refs/heads/{feedback}"], katalog, token)
            feedback_ok = True
        except BladGit:
            feedback_ok = False  # np. regula chroniaca galaz bez wyjatku dla aplikacji
        konflikty: list = []
        if autoscalanie:
            for _proba in range(2):
                _git(["fetch", "-q", adres_celu, f"+refs/heads/{galaz}:refs/remotes/cel/{galaz}"], katalog, token)
                glowa = _git(["rev-parse", f"refs/remotes/cel/{galaz}"], katalog)
                if _zawiera(katalog, glowa, sha_poprawki):
                    return WynikScalenia(True, glowa, feedback=feedback_ok)
                _git(["checkout", "-q", "--detach", glowa], katalog)
                try:
                    _git(["merge", "--no-ff", "--no-edit", "-m", komunikat, sha_poprawki], katalog)
                except BladGit:
                    konflikty = [s for s in _git(["diff", "--name-only", "--diff-filter=U"], katalog).splitlines() if s]
                    try:
                        _git(["merge", "--abort"], katalog)
                    except BladGit:
                        pass
                    break
                sha = _git(["rev-parse", "HEAD"], katalog)
                try:
                    _git(["push", "-q", adres_celu, f"{sha}:refs/heads/{galaz}"], katalog, token)
                    return WynikScalenia(True, sha, feedback=feedback_ok)
                except BladGit:
                    continue  # student wlasnie cos wypchnal: ponowna proba na nowym stanie
        _git(["push", "-q", "-f", adres_celu, f"{sha_poprawki}:refs/heads/{galaz_poprawki}"], katalog, token)
        return WynikScalenia(False, "", konflikty, feedback=feedback_ok)


def _zawiera(katalog: str, glowa: str, sha: str) -> bool:
    try:
        _git(["merge-base", "--is-ancestor", sha, glowa], katalog)
        return True
    except BladGit:
        return False
