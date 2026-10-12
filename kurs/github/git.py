"""Operacje git wykonywane na serwerze: kopiowanie stanu startowego do nowych repozytoriow.

Uzywamy zwyklego programu git z tokenem instalacji aplikacji (wazny godzine).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
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


def migawka_startowa(zrodlo: str, cel: str, komunikat: str) -> str:
    """Kopiuje biezacy stan repozytorium zrodlowego jako jeden commit (bez historii) do galezi main celu.

    Brak historii chroni przed wyciekiem rozwiazan z wczesniejszych commitow zrodla.
    """
    galaz = settings.SPRAWDZARKA_GALAZ
    token = klient.token_instalacji()
    with _katalog_roboczy() as katalog:
        _git(["init", "-q"], katalog)
        istniejace = _galezie_zdalne(cel, token, katalog)
        if galaz in istniejace:
            return istniejace[galaz]
        _git(["fetch", "-q", "--depth", "1", adres_repo(zrodlo, token), "HEAD"], katalog, token)
        drzewo = _git(["rev-parse", "FETCH_HEAD^{tree}"], katalog)
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
