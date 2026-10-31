"""Wspolne przygotowanie testow: ustawienia i imitacja GitHub."""
import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from kurs.github import git as git_modul
from kurs.github import klient as klient_modul
from kurs.models import Grupa, Kurs, Przydzial, Rok, Uczelnia, Zestaw

SEKRET = "sekret-testowy"


def archiwum_tar(pliki: dict, katalog_glowny: str = "wfais-repo-abc123") -> bytes:
    """Archiwum tar.gz w ukladzie takim jak z GitHub (jeden katalog glowny)."""
    import io
    import tarfile

    bufor = io.BytesIO()
    with tarfile.open(fileobj=bufor, mode="w:gz") as tar:
        for sciezka, tresc in pliki.items():
            dane = tresc.encode()
            info = tarfile.TarInfo(f"{katalog_glowny}/{sciezka}")
            info.size = len(dane)
            tar.addfile(info, io.BytesIO(dane))
    return bufor.getvalue()


@pytest.fixture(autouse=True)
def _ustawienia(settings, tmp_path):
    settings.GITHUB_ORG = "wfais"
    settings.GITHUB_WEBHOOK_SECRET = SEKRET
    settings.GITHUB_APP_CLIENT_ID = "Iv1.test"
    settings.GITHUB_APP_CLIENT_SECRET = "tajne"
    settings.PUBLICZNY_ADRES = "http://testserver"
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.MEDIA_ROOT = tmp_path / "pliki"
    settings.SPRAWDZARKA_OCENA_W_WATKU = False
    settings.SPRAWDZARKA_ROWNOLEGLE_OCENY = 1
    settings.SPRAWDZARKA_MODELE = []
    settings.SPRAWDZARKA_TESTY = "lokalnie"  # w testach tylko przykladowy, bezpieczny kod
    settings.SPRAWDZARKA_KOLEJKA = tmp_path / "kolejka"
    klient_modul.wyczysc_pamiec()


class FalszywyGitHub:
    """Imitacja klienta GitHub i operacji git; zapisuje wywolania do sprawdzenia w testach."""

    def __init__(self):
        self.repozytoria = {}
        self.nastepne_id = 1000
        self.zaproszenia = []
        self.pull_requesty = {}
        self.komentarze = []
        self.zamkniete = []
        self.archiwum = []
        self.usuniete = []
        self.odebrane = []
        self.porownania = {}
        self.aktywnosci = {}
        self.readme_tresc = {}
        self.migawki = []
        self.blad_przy = None
        self.archiwa = {}
        self.recenzje = []
        self.odrzucaj_recenzje = False
        self.chronione = []
        self.blad_zamkniecia = None
        self.drzewa = {}
        self.nowy_start = "e" * 40      # wynik poprawki stanu startowego (None: brak zmian)
        self.wyniki_scalenia = {}       # repozytorium -> WynikScalenia
        self.scalenia = []
        self.zawiera = {}
        self.wyjatki_regul = []

    def _moze_blad(self, nazwa):
        if self.blad_przy == nazwa:
            raise klient_modul.BladGitHub(500, f"symulowany blad: {nazwa}")

    # klient
    def utworz_repo(self, nazwa, opis=""):
        self._moze_blad("utworz_repo")
        pelna = f"wfais/{nazwa}"
        if pelna in self.repozytoria:
            return self.repozytoria[pelna], False
        self.nastepne_id += 1
        dane = {"id": self.nastepne_id, "full_name": pelna}
        self.repozytoria[pelna] = dane
        return dane, True

    def ustaw_galaz_domyslna(self, pelna, galaz):
        pass

    def zapros(self, pelna, login, uprawnienie="push"):
        self._moze_blad("zapros")
        self.zaproszenia.append((pelna, login))

    def utworz_pr(self, pelna, head, base, tytul, tresc):
        self.pull_requesty[pelna] = (head, base, tytul)
        return 1

    def porownaj(self, pelna, sha_od, sha_do):
        return self.porownania.get((sha_od, sha_do), [])

    def sha_galezi(self, pelna, ref="HEAD"):
        return f"zrodlo-{pelna}"

    def zezwol_aplikacji_na_galaz(self, pelna, galaz):
        self.wyjatki_regul.append((pelna, galaz))

    def czy_zawiera(self, pelna, sha, ref):
        return self.zawiera.get(pelna, False)

    def poprawka_startowa(self, zrodlo, start, komunikat, z_actions=False):
        return self.nowy_start

    def scal_poprawke(self, start, cel, sha_poprawki, galaz_poprawki, autoscalanie, komunikat):
        from kurs.github.git import WynikScalenia
        self.scalenia.append((cel, sha_poprawki, galaz_poprawki, autoscalanie))
        domyslny = WynikScalenia(True, "m" * 40) if autoscalanie else WynikScalenia(False)
        return self.wyniki_scalenia.get(cel, domyslny)

    def drzewo_blobow(self, pelna, sha):
        if sha not in self.drzewa:
            raise klient_modul.BladGitHub(404, "brak drzewa")
        return self.drzewa[sha]

    def aktywnosc(self, pelna, galaz):
        return iter(self.aktywnosci.get(pelna, []))

    def dostep_studenta(self, pelna, login):
        return ("write", False)

    def adres_webhooka(self):
        return "http://testserver/github/webhook/"

    def zdarzenia_aplikacji(self):
        return ["push"]

    def dostawy_webhookow(self, ile=20):
        return [{"delivered_at": "2026-10-31T00:10:00Z", "event": "push", "status_code": 200, "status": "OK",
                 "repository_id": 1001}]

    def readme(self, pelna, ref):
        return self.readme_tresc.get((pelna, ref))

    def komentarz_pr(self, pelna, numer, tresc):
        self.komentarze.append((pelna, numer, tresc))

    def zamknij_pr(self, pelna, numer):
        if self.blad_zamkniecia:
            raise klient_modul.BladGitHub(self.blad_zamkniecia, "symulowany blad zamkniecia")
        self.zamkniete.append((pelna, numer))

    def odbierz_dostep(self, pelna, login):
        self.odebrane.append((pelna, login))

    def zmien_nazwe(self, pelna, nowa):
        dane = self.repozytoria.pop(pelna)
        dane = {**dane, "full_name": f"wfais/{nowa}"}
        self.repozytoria[dane["full_name"]] = dane
        return dane

    def archiwizuj(self, pelna):
        self.archiwum.append(pelna)

    def usun_repo(self, pelna):
        self.repozytoria.pop(pelna, None)
        self.usuniete.append(pelna)

    def pobierz_archiwum(self, pelna, ref):
        return archiwum_tar(self.archiwa[ref])

    def drzewo_plikow(self, pelna, ref="HEAD"):
        return ["ZADANIE1/main.py", "ZADANIE1/tests/test_main.py", "ZADANIE2/gra.py"]

    def recenzja_pr(self, pelna, numer, uwagi, commit_id=""):
        if self.odrzucaj_recenzje:
            raise klient_modul.BladGitHub(422, "Line could not be resolved")
        self.recenzje.append((pelna, numer, uwagi, commit_id))

    def chron_galaz(self, pelna, galaz):
        self.chronione.append((pelna, galaz))

    # git
    def migawka_startowa(self, zrodlo, cel, komunikat, z_actions=False):
        self.migawki.append((zrodlo, cel))
        self.z_actions = z_actions
        return "a" * 40

    def przygotuj_repo_studenta(self, start, cel, login):
        self._moze_blad("przygotuj_repo_studenta")
        return "b" * 40, "c" * 40


FUNKCJE_KLIENTA = [
    "utworz_repo", "ustaw_galaz_domyslna", "zapros", "utworz_pr", "porownaj", "aktywnosc", "readme",
    "adres_webhooka", "zdarzenia_aplikacji", "dostawy_webhookow", "dostep_studenta",
    "komentarz_pr", "zamknij_pr", "odbierz_dostep", "zmien_nazwe", "archiwizuj", "usun_repo",
    "pobierz_archiwum", "drzewo_plikow", "recenzja_pr", "chron_galaz", "drzewo_blobow",
    "sha_galezi", "zezwol_aplikacji_na_galaz", "czy_zawiera",
]
FUNKCJE_GIT = ["migawka_startowa", "przygotuj_repo_studenta", "poprawka_startowa", "scal_poprawke"]


@pytest.fixture
def github(monkeypatch):
    falszywy = FalszywyGitHub()
    for nazwa in FUNKCJE_KLIENTA:
        monkeypatch.setattr(klient_modul, nazwa, getattr(falszywy, nazwa))
    for nazwa in FUNKCJE_GIT:
        monkeypatch.setattr(git_modul, nazwa, getattr(falszywy, nazwa))
    return falszywy


@pytest.fixture
def rok(db):
    uczelnia, _ = Uczelnia.objects.get_or_create(skrot="uj", defaults={"nazwa": "Uniwersytet Jagielloński"})
    return Rok.objects.create(uczelnia=uczelnia, nazwa="2026/2027")


@pytest.fixture
def kurs(rok):
    return Kurs.objects.create(rok=rok, nazwa="Język Python", skrot="py")


@pytest.fixture
def grupa(kurs):
    return Grupa.objects.create(kurs=kurs, nazwa="Grupa 4", skrot="g4")


@pytest.fixture
def zestaw(kurs):
    return Zestaw.objects.create(kurs=kurs, nazwa="Zestaw 3", skrot="z03", repo_zrodlowe="wfais/python-zestaw3",
                                 punkty_max=Decimal("5"))


@pytest.fixture
def przydzial(db, zestaw, grupa):
    teraz = timezone.now()
    return Przydzial.objects.create(
        zestaw=zestaw, grupa=grupa, przedrostek_repo="py-z03-g4-",
        termin_miekki=teraz + timedelta(days=7), termin_twardy=teraz + timedelta(days=14),
        repo_startowe="wfais/py-z03-g4-start", sha_startowy="a" * 40,
    )


@pytest.fixture
def prowadzacy(db, django_user_model, client):
    uzytkownik = django_user_model.objects.create_user("prowadzacy", password="haslo-testowe-123", is_staff=True, is_superuser=True)
    client.force_login(uzytkownik)
    return uzytkownik


def podpisz(tresc: bytes) -> str:
    return "sha256=" + hmac.new(SEKRET.encode(), tresc, hashlib.sha256).hexdigest()


def wyslij_webhook(client, dane, zdarzenie="push"):
    tresc = json.dumps(dane).encode()
    return client.post(
        "/github/webhook/", data=tresc, content_type="application/json",
        HTTP_X_GITHUB_EVENT=zdarzenie, HTTP_X_HUB_SIGNATURE_256=podpisz(tresc),
    )
