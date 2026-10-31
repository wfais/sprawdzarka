"""Klient REST API GitHub dzialajacy w imieniu aplikacji GitHub zainstalowanej w organizacji."""
from __future__ import annotations

import base64
import functools
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import urlencode

import jwt
import requests
from django.conf import settings

log = logging.getLogger(__name__)

LIMIT_CZASU = 30
_blokada = threading.Lock()
_pamiec: dict[str, Any] = {"token": None, "wygasa": 0.0, "instalacja": None}
_sesja = requests.Session()


class BladGitHub(Exception):
    def __init__(self, status: int, komunikat: str):
        super().__init__(f"GitHub {status}: {komunikat}" if status else komunikat)
        self.status = status
        self.komunikat = komunikat


class NazwaZajeta(BladGitHub):
    """Nazwa repozytorium jeszcze zajeta przez repozytorium w trakcie usuwania (GitHub usuwa je z opoznieniem)."""


def wyczysc_pamiec():
    """Uzywane w testach i po zmianie konfiguracji."""
    _pamiec.update({"token": None, "wygasa": 0.0, "instalacja": None})
    drzewo_blobow.cache_clear()


def org() -> str:
    return settings.GITHUB_ORG


def _url(sciezka: str) -> str:
    return sciezka if sciezka.startswith("http") else settings.GITHUB_API_URL + sciezka


def _naglowki(token: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {token}",
        "User-Agent": "sprawdzarka",
    }


def _komunikat(odp: requests.Response) -> str:
    try:
        dane = odp.json()
    except ValueError:
        return odp.text[:300] or odp.reason
    tekst = dane.get("message", "") if isinstance(dane, dict) else ""
    bledy = dane.get("errors") if isinstance(dane, dict) else None
    if bledy:
        szczegoly = [b.get("message") or b.get("code", "") if isinstance(b, dict) else str(b) for b in bledy]
        tekst += " (" + "; ".join(s for s in szczegoly if s) + ")"
    return tekst or odp.reason


def _przekroczony_limit(odp: requests.Response) -> bool:
    if odp.status_code == 429:
        return True
    return odp.status_code == 403 and (
        "Retry-After" in odp.headers or odp.headers.get("X-RateLimit-Remaining") == "0"
    )


def _czas_oczekiwania(odp: requests.Response, proba: int) -> float:
    retry_after = odp.headers.get("Retry-After", "")
    if retry_after.isdigit():
        return min(int(retry_after), 60)
    reset = odp.headers.get("X-RateLimit-Reset", "")
    if reset.isdigit():
        return max(1, min(int(reset) - int(time.time()), 60))
    return 2 ** proba


def _wyslij(metoda: str, url: str, token: str, *, proby: int = 4, **kwargs) -> requests.Response:
    for proba in range(proby):
        ostatnia = proba == proby - 1
        try:
            odp = _sesja.request(metoda, url, headers=_naglowki(token), timeout=LIMIT_CZASU, **kwargs)
        except requests.RequestException as blad:
            if ostatnia:
                raise BladGitHub(0, f"Brak połączenia z GitHub: {blad}") from blad
            time.sleep(2 ** proba)
            continue
        if not ostatnia and (odp.status_code in (502, 503, 504) or _przekroczony_limit(odp)):
            time.sleep(_czas_oczekiwania(odp, proba))
            continue
        return odp
    raise BladGitHub(0, "Nie udało się wykonać zapytania do GitHub.")


def _sprawdz(odp: requests.Response, oczekiwane) -> requests.Response:
    if odp.status_code not in oczekiwane:
        raise BladGitHub(odp.status_code, _komunikat(odp))
    return odp


# --- Uwierzytelnienie aplikacji ---

def _klucz_prywatny() -> str:
    if settings.GITHUB_APP_PRIVATE_KEY:
        return settings.GITHUB_APP_PRIVATE_KEY.replace("\\n", "\n")
    sciezka = Path(settings.GITHUB_APP_PRIVATE_KEY_PATH)
    if not sciezka.is_file():
        raise BladGitHub(0, f"Brak klucza prywatnego aplikacji GitHub: {sciezka}")
    return sciezka.read_text()


def jwt_aplikacji() -> str:
    if not settings.GITHUB_APP_ID:
        raise BladGitHub(0, "Brak GITHUB_APP_ID w konfiguracji.")
    teraz = int(time.time())
    dane = {"iat": teraz - 60, "exp": teraz + 540, "iss": str(settings.GITHUB_APP_ID)}
    return jwt.encode(dane, _klucz_prywatny(), algorithm="RS256")


def _jako_aplikacja(sciezka: str, params: Optional[dict] = None) -> Any:
    """Zapytanie uwierzytelnione samym kluczem aplikacji (ustawienia aplikacji, nie repozytoria)."""
    return _sprawdz(_wyslij("GET", _url(sciezka), jwt_aplikacji(), params=params), (200,)).json()


def adres_webhooka() -> str:
    """Adres, pod ktory GitHub wysyla powiadomienia aplikacji (do diagnozy)."""
    return (_jako_aplikacja("/app/hook/config") or {}).get("url", "")


def dostawy_webhookow(ile: int = 20) -> list[dict]:
    """Ostatnie powiadomienia wyslane przez GitHub do aplikacji: czas, zdarzenie, kod odpowiedzi serwera."""
    return _jako_aplikacja("/app/hook/deliveries", {"per_page": ile}) or []


def id_instalacji() -> int:
    if settings.GITHUB_INSTALLATION_ID:
        return int(settings.GITHUB_INSTALLATION_ID)
    if not _pamiec["instalacja"]:
        odp = _sprawdz(_wyslij("GET", _url(f"/orgs/{org()}/installation"), jwt_aplikacji()), (200,))
        _pamiec["instalacja"] = odp.json()["id"]
    return _pamiec["instalacja"]


def token_instalacji() -> str:
    """Token instalacji (wazny godzine), odnawiany 5 minut przed wygasnieciem."""
    with _blokada:
        if _pamiec["token"] and _pamiec["wygasa"] - time.time() > 300:
            return _pamiec["token"]
        sciezka = f"/app/installations/{id_instalacji()}/access_tokens"
        dane = _sprawdz(_wyslij("POST", _url(sciezka), jwt_aplikacji()), (201,)).json()
        _pamiec["token"] = dane["token"]
        _pamiec["wygasa"] = datetime.fromisoformat(dane["expires_at"].replace("Z", "+00:00")).timestamp()
        return dane["token"]


def zapytanie(metoda: str, sciezka: str, *, oczekiwane=(200,), **kwargs) -> Any:
    odp = _sprawdz(_wyslij(metoda, _url(sciezka), token_instalacji(), **kwargs), oczekiwane)
    if odp.status_code == 204 or not odp.content:
        return None
    return odp.json()


def stronicuj(sciezka: str, params: Optional[dict] = None) -> Iterator[dict]:
    url: Optional[str] = _url(sciezka)
    parametry = dict(params or {})
    parametry.setdefault("per_page", 100)
    while url:
        odp = _sprawdz(_wyslij("GET", url, token_instalacji(), params=parametry), (200,))
        yield from odp.json()
        url = odp.links.get("next", {}).get("url")
        parametry = None


# --- Repozytoria ---

def pobierz_repo(pelna: str) -> dict:
    return zapytanie("GET", f"/repos/{pelna}")


def utworz_repo(nazwa: str, opis: str = "") -> tuple[dict, bool]:
    """Tworzy prywatne repozytorium w organizacji. Zwraca (dane, czy_nowe)."""
    try:
        dane = zapytanie(
            "POST", f"/orgs/{org()}/repos", oczekiwane=(201,),
            json={
                "name": nazwa, "description": opis[:300], "private": True, "auto_init": False,
                "has_wiki": False, "has_projects": False, "has_issues": True,
            },
        )
        return dane, True
    except BladGitHub as blad:
        if blad.status == 422 and "already exists" in blad.komunikat.lower():
            try:
                return pobierz_repo(f"{org()}/{nazwa}"), False
            except BladGitHub as drugi:
                if drugi.status == 404:
                    raise NazwaZajeta(409, f"Nazwa {nazwa} jest jeszcze zajęta przez usuwane repozytorium.") from drugi
                raise
        raise


def ustaw_galaz_domyslna(pelna: str, galaz: str) -> None:
    zapytanie("PATCH", f"/repos/{pelna}", json={"default_branch": galaz})


def zapros(pelna: str, login: str, uprawnienie: str = "push") -> None:
    zapytanie("PUT", f"/repos/{pelna}/collaborators/{login}", oczekiwane=(201, 204), json={"permission": uprawnienie})


def dostep_studenta(pelna: str, login: str) -> tuple[str, bool]:
    """Uprawnienie konta do repozytorium (admin, write, read, none) i czy czeka na nie nieprzyjete zaproszenie."""
    try:
        uprawnienie = (zapytanie("GET", f"/repos/{pelna}/collaborators/{login}/permission") or {}).get("permission", "")
    except BladGitHub as blad:
        if blad.status != 404:
            raise
        uprawnienie = "none"
    oczekuje = any((z.get("invitee") or {}).get("login", "").lower() == login.lower()
                   for z in stronicuj(f"/repos/{pelna}/invitations"))
    return uprawnienie, oczekuje


def odbierz_dostep(pelna: str, login: str) -> None:
    for zaproszenie in stronicuj(f"/repos/{pelna}/invitations"):
        if (zaproszenie.get("invitee") or {}).get("login", "").lower() == login.lower():
            zapytanie("DELETE", f"/repos/{pelna}/invitations/{zaproszenie['id']}", oczekiwane=(204,))
    try:
        zapytanie("DELETE", f"/repos/{pelna}/collaborators/{login}", oczekiwane=(204,))
    except BladGitHub as blad:
        if blad.status != 404:
            raise


def zmien_nazwe(pelna: str, nowa_nazwa: str) -> dict:
    return zapytanie("PATCH", f"/repos/{pelna}", json={"name": nowa_nazwa})


def archiwizuj(pelna: str) -> None:
    zapytanie("PATCH", f"/repos/{pelna}", json={"archived": True})


def usun_repo(pelna: str) -> None:
    zapytanie("DELETE", f"/repos/{pelna}", oczekiwane=(204,))


@functools.lru_cache(maxsize=256)
def drzewo_blobow(pelna: str, sha: str) -> dict:
    """Mapa sciezka -> skrot zawartosci dla stanu repozytorium (niezmienna dla danego sha)."""
    dane = zapytanie("GET", f"/repos/{pelna}/git/trees/{sha}", params={"recursive": "1"})
    if dane.get("truncated"):
        raise BladGitHub(409, "Repozytorium ma zbyt wiele plików do porównania.")
    return {w["path"]: w["sha"] for w in dane.get("tree", []) if w.get("type") == "blob"}


def porownaj(pelna: str, sha_od: str, sha_do: str) -> list[str]:
    """Sciezki plikow, ktorych zawartosc rozni sie miedzy dwoma stanami (bezposrednie porownanie drzew).

    Porownanie drzew, a nie historii: przepisanie historii (amend, squash) bez zmiany kodu
    nie jest traktowane jako zmiana kodu.
    """
    od, do = drzewo_blobow(pelna, sha_od), drzewo_blobow(pelna, sha_do)
    return sorted(s for s in set(od) | set(do) if od.get(s) != do.get(s))


def aktywnosc(pelna: str, galaz: Optional[str]) -> Iterator[dict]:
    """Dziennik aktywnosci repozytorium (wypchniecia z czasem zarejestrowanym przez GitHub); galaz None: wszystkie."""
    yield from stronicuj(f"/repos/{pelna}/activity", {"ref": f"refs/heads/{galaz}"} if galaz else None)


def pobierz_archiwum(pelna: str, ref: str) -> bytes:
    """Archiwum tar.gz stanu repozytorium (GitHub przekierowuje do serwera plikow z tymczasowym tokenem)."""
    odp = _wyslij("GET", _url(f"/repos/{pelna}/tarball/{ref}"), token_instalacji(), allow_redirects=True)
    return _sprawdz(odp, (200,)).content


def drzewo_plikow(pelna: str, ref: str = "HEAD") -> list[str]:
    dane = zapytanie("GET", f"/repos/{pelna}/git/trees/{ref}", params={"recursive": "1"})
    return [w["path"] for w in dane.get("tree", []) if w.get("type") == "blob"]


def recenzja_pr(pelna: str, numer: int, uwagi: list[dict], commit_id: str = "") -> None:
    """Komentarze przypiete do linii kodu w pull requescie (tylko linie zmienione przez studenta)."""
    dane = {"event": "COMMENT", "body": "Uwagi do kodu."}
    if commit_id:
        dane["commit_id"] = commit_id
    zapytanie(
        "POST", f"/repos/{pelna}/pulls/{numer}/reviews", oczekiwane=(200,),
        json={**dane, "comments": [
            {"path": u["plik"], "line": int(u["linia"]), "side": "RIGHT", "body": u["tekst"]} for u in uwagi
        ]},
    )


def readme(pelna: str, ref: str) -> Optional[str]:
    try:
        dane = zapytanie("GET", f"/repos/{pelna}/readme", params={"ref": ref})
    except BladGitHub as blad:
        if blad.status == 404:
            return None
        raise
    return base64.b64decode(dane.get("content", "")).decode("utf-8", errors="replace")


# --- Pull request "Feedback" ---

def utworz_pr(pelna: str, head: str, base: str, tytul: str, tresc: str) -> int:
    try:
        dane = zapytanie(
            "POST", f"/repos/{pelna}/pulls", oczekiwane=(201,),
            json={"title": tytul, "head": head, "base": base, "body": tresc},
        )
        return dane["number"]
    except BladGitHub as blad:
        if blad.status == 422:
            istniejace = zapytanie(
                "GET", f"/repos/{pelna}/pulls",
                params={"head": f"{org()}:{head}", "base": base, "state": "all"},
            )
            if istniejace:
                return istniejace[0]["number"]
        raise


def _wyjatek_aplikacji() -> list[dict]:
    """Aplikacja moze przesuwac chroniona galaz (poprawki prowadzacego); nikt inny nie moze."""
    if not settings.GITHUB_APP_ID:
        return []
    return [{"actor_id": int(settings.GITHUB_APP_ID), "actor_type": "Integration", "bypass_mode": "always"}]


def chron_galaz(pelna: str, galaz: str) -> None:
    """Regula repozytorium: galezi nie mozna zmienic ani usunac (chroni stan startowy i pull request)."""
    zapytanie("POST", f"/repos/{pelna}/rulesets", oczekiwane=(201,), json={
        "name": f"ochrona-{galaz}", "target": "branch", "enforcement": "active",
        "bypass_actors": _wyjatek_aplikacji(),
        "conditions": {"ref_name": {"include": [f"refs/heads/{galaz}"], "exclude": []}},
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}, {"type": "update"}],
    })


def zezwol_aplikacji_na_galaz(pelna: str, galaz: str) -> None:
    """Dopisuje aplikacje do wyjatkow reguly chroniacej galaz (repozytoria utworzone przed ta zmiana)."""
    wyjatek = _wyjatek_aplikacji()
    if not wyjatek:
        return
    for regula in zapytanie("GET", f"/repos/{pelna}/rulesets") or []:
        if regula.get("name") != f"ochrona-{galaz}":
            continue
        dane = zapytanie("GET", f"/repos/{pelna}/rulesets/{regula['id']}")
        obecne = dane.get("bypass_actors") or []
        if any(a.get("actor_type") == "Integration" and a.get("actor_id") == wyjatek[0]["actor_id"] for a in obecne):
            return
        zapytanie("PUT", f"/repos/{pelna}/rulesets/{regula['id']}", json={
            "name": dane["name"], "target": dane.get("target", "branch"), "enforcement": dane["enforcement"],
            "bypass_actors": obecne + wyjatek, "conditions": dane.get("conditions", {}), "rules": dane.get("rules", []),
        })


def sha_galezi(pelna: str, ref: str = "HEAD") -> str:
    return zapytanie("GET", f"/repos/{pelna}/commits/{ref}")["sha"]


def czy_zawiera(pelna: str, sha: str, ref: str) -> bool:
    """Czy galaz ref zawiera commit sha (np. czy student scalil poprawke)."""
    dane = zapytanie("GET", f"/repos/{pelna}/compare/{sha}...{ref}")
    return dane.get("status") in ("ahead", "identical")


def komentarz_pr(pelna: str, numer: int, tresc: str) -> None:
    zapytanie("POST", f"/repos/{pelna}/issues/{numer}/comments", oczekiwane=(201,), json={"body": tresc})


def zamknij_pr(pelna: str, numer: int) -> None:
    zapytanie("PATCH", f"/repos/{pelna}/pulls/{numer}", json={"state": "closed"})


# --- Logowanie studentow (autoryzacja uzytkownika aplikacji GitHub) ---

def adres_autoryzacji(adres_powrotu: str, stan: str) -> str:
    parametry = {"client_id": settings.GITHUB_APP_CLIENT_ID, "redirect_uri": adres_powrotu, "state": stan}
    return f"{settings.GITHUB_URL}/login/oauth/authorize?{urlencode(parametry)}"


def wymien_kod(kod: str, adres_powrotu: str) -> str:
    try:
        odp = _sesja.post(
            f"{settings.GITHUB_URL}/login/oauth/access_token",
            data={
                "client_id": settings.GITHUB_APP_CLIENT_ID,
                "client_secret": settings.GITHUB_APP_CLIENT_SECRET,
                "code": kod,
                "redirect_uri": adres_powrotu,
            },
            headers={"Accept": "application/json"},
            timeout=LIMIT_CZASU,
        )
    except requests.RequestException as blad:
        raise BladGitHub(0, f"Brak połączenia z GitHub: {blad}") from blad
    try:
        dane = odp.json()
    except ValueError:
        dane = {}
    if "access_token" not in dane:
        raise BladGitHub(odp.status_code, dane.get("error_description") or "Nie udało się zalogować przez GitHub.")
    return dane["access_token"]


def uzytkownik(token_uzytkownika: str) -> dict:
    return _sprawdz(_wyslij("GET", _url("/user"), token_uzytkownika), (200,)).json()
