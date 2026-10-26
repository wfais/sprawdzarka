"""Przygotowanie kodu studenta i uruchamianie testow pytest.

Modul nie korzysta z Django: uzywa go takze samodzielny proces testujacy
(narzedzia/tester.py), ktory dziala bez dostepu do sieci, bazy i kluczy.

Zasady bezpieczenstwa:
- drzewo do testow to stan startowy prowadzacego plus WYLACZNIE pliki z kodem studenta
  (testy, konfiguracja pytest i pliki o nazwach kolidujacych z pytest pochodza od prowadzacego),
- pytest importowany jest zanim katalog z kodem studenta trafi na sciezke importu,
- w procesie testujacym uruchomionym jako root kod studenta dziala na osobnym, nieuprzywilejowanym
  koncie, a po kazdym zleceniu wszystkie jego procesy sa zabijane.
Kod studenta wykonuje sie w tym samym procesie co pytest, wiec zdeterminowany student moze
sfalszowac wyniki. Wynik testow jest dlatego sygnalem pomocniczym: modele oceniaja kod, typowe
konstrukcje sluzace manipulacji sa oznaczane jako podejrzane, a decyzja nalezy do prowadzacego.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Optional

from .terminy import pasuje_do_wzorcow

WZORCE_TESTOW = ("test_*.py", "*_test.py")
PLIKI_KONFIGURACJI = ("conftest.py", "pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml")
KATALOGI_TESTOW = ("tests", "test", "testy")
POMIJANE_KATALOGI = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", ".idea", ".vscode"}
# Moduly, ktorych student nie moze podmienic (nazwa pliku lub katalogu w korzeniu repozytorium)
ZASTRZEZONE_MODULY = {
    "pytest", "_pytest", "pluggy", "py", "iniconfig", "packaging", "sitecustomize", "usercustomize", "conftest",
}


def jest_plikiem_testow(sciezka: str) -> bool:
    # Porownania bez wielkosci liter: na dyskach Windows/macOS Conftest.py dziala jak conftest.py
    czesci = sciezka.lower().split("/")
    nazwa = czesci[-1]
    if any(fnmatchcase(nazwa, w) for w in WZORCE_TESTOW):
        return True
    return any(c in KATALOGI_TESTOW for c in czesci[:-1]) and nazwa.endswith(".py")


def nalezy_do_prowadzacego(sciezka: str) -> bool:
    """Pliki, ktore w drzewie testow zawsze pochodza ze stanu startowego: testy, ich dane, konfiguracja."""
    czesci = sciezka.lower().split("/")
    return jest_plikiem_testow(sciezka) or jest_konfiguracja(sciezka) or any(c in KATALOGI_TESTOW for c in czesci[:-1])


def jest_konfiguracja(sciezka: str) -> bool:
    return sciezka.lower().split("/")[-1] in PLIKI_KONFIGURACJI


def rozpakuj_archiwum(dane: bytes, cel: Path) -> Path:
    """Rozpakowuje archiwum tar.gz z GitHub (jeden katalog glowny) i zwraca ten katalog."""
    cel.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(dane), mode="r:*") as archiwum:
        if hasattr(tarfile, "data_filter"):
            archiwum.extractall(cel, filter="data")
        else:  # starsze Pythony: reczna kontrola sciezek
            korzen = str(cel.resolve())
            for element in archiwum.getmembers():
                sciezka = str((cel / element.name).resolve())
                if not (sciezka == korzen or sciezka.startswith(korzen + os.sep)) or element.issym() or element.islnk():
                    raise ValueError(f"Niebezpieczna sciezka w archiwum: {element.name}")
            archiwum.extractall(cel)
    podkatalogi = [p for p in cel.iterdir() if p.is_dir()]
    return podkatalogi[0] if len(podkatalogi) == 1 else cel


def lista_plikow(katalog: Path) -> list[str]:
    pliki = []
    for sciezka in Path(katalog).rglob("*"):
        if sciezka.is_file() and not sciezka.is_symlink() and not (set(sciezka.relative_to(katalog).parts) & POMIJANE_KATALOGI):
            pliki.append(sciezka.relative_to(katalog).as_posix())
    return sorted(pliki)


def przygotuj_drzewo_testow(baza: Path, kod: Path, cel: Path, wzorce: Optional[list[str]] = None) -> list[str]:
    """Kopia stanu startowego z nalozonymi plikami studenta. Zwraca dziennik zmian.

    Nakladane sa: zmienione pliki z kodem (wzorce, domyslnie *.py) oraz nowe pliki studenta dowolnego typu.
    Pozostaja od prowadzacego: testy, ich dane, konfiguracja pytest i pozostale pliki stanu startowego
    (np. pliki z danymi oczekiwanymi przez testy).
    """
    wzorce = wzorce or ["*.py"]
    baza, kod, cel = Path(baza), Path(kod), Path(cel)
    dziennik = []
    for wzgledna in lista_plikow(baza):
        (cel / wzgledna).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(baza / wzgledna, cel / wzgledna)
    pliki_bazy = set(lista_plikow(baza))
    for wzgledna in lista_plikow(kod):
        if nalezy_do_prowadzacego(wzgledna):
            if wzgledna not in pliki_bazy:
                dziennik.append(f"pominieto plik testow lub konfiguracji dodany przez studenta: {wzgledna}")
            elif (kod / wzgledna).read_bytes() != (baza / wzgledna).read_bytes():
                dziennik.append(f"pominieto zmiany studenta w pliku testow lub konfiguracji: {wzgledna}")
            continue
        if wzgledna in pliki_bazy and not pasuje_do_wzorcow(wzgledna, wzorce):
            if (kod / wzgledna).read_bytes() != (baza / wzgledna).read_bytes():
                dziennik.append(f"pominieto zmiany studenta w pliku prowadzacego (nie kod): {wzgledna}")
            continue
        pierwszy = wzgledna.split("/")[0].lower()
        modul = pierwszy[:-3] if pierwszy.endswith(".py") else pierwszy
        if modul in ZASTRZEZONE_MODULY:
            dziennik.append(f"pominieto {wzgledna}: nazwa koliduje z modulem pytest")
            continue
        (cel / wzgledna).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(kod / wzgledna, cel / wzgledna)
    cel.mkdir(parents=True, exist_ok=True)
    return dziennik


def skrot_git(dane: bytes) -> str:
    """Skrot zawartosci pliku w git (blob), jak w drzewach GitHub."""
    import hashlib

    return hashlib.sha1(b"blob %d\0" % len(dane) + dane).hexdigest()


def w_wersji_prowadzacego(sciezka: str, dane: Optional[bytes], dozwolone: Optional[dict]) -> bool:
    """Czy plik (None: brak pliku) ma jedna z wersji prowadzacego (wczesniejsze stany startowe, jego poprawki)."""
    if not dozwolone or sciezka not in dozwolone:
        return False
    return (skrot_git(dane) if dane is not None else None) in dozwolone[sciezka]


def zmiany_plikow_prowadzacego(baza: Path, kod: Path, dozwolone: Optional[dict] = None) -> dict:
    """Pliki testow, ich danych i konfiguracji pytest, ktore student zmienil, usunal lub dodal.

    dozwolone: wersje plikow od prowadzacego (sciezka -> skroty), ktore nie sa zmianami studenta.
    """
    baza, kod = Path(baza), Path(kod)
    w_bazie = {p for p in lista_plikow(baza) if nalezy_do_prowadzacego(p)}
    w_kodzie = {p for p in lista_plikow(kod) if nalezy_do_prowadzacego(p)}
    return {
        "zmienione": sorted(p for p in w_bazie & w_kodzie if (baza / p).read_bytes() != (kod / p).read_bytes()
                            and not w_wersji_prowadzacego(p, (kod / p).read_bytes(), dozwolone)),
        "usuniete": sorted(p for p in w_bazie - w_kodzie if not w_wersji_prowadzacego(p, None, dozwolone)),
        "dodane": sorted(p for p in w_kodzie - w_bazie if not w_wersji_prowadzacego(p, (kod / p).read_bytes(), dozwolone)),
    }


def pliki_testow(katalog: Path) -> list[str]:
    return [p for p in lista_plikow(katalog) if jest_plikiem_testow(p)]


def _srodowisko_testow(katalog_domowy: Path, katalog_tymczasowy: Path) -> dict:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(katalog_domowy),
        "USERPROFILE": str(katalog_domowy),
        "TMPDIR": str(katalog_tymczasowy),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "MPLBACKEND": "Agg",
        "LANG": "C.UTF-8",
    }
    for nazwa in ("SYSTEMROOT", "COMSPEC"):
        if nazwa in os.environ:
            env[nazwa] = os.environ[nazwa]
    env["TEMP"] = env["TMP"] = str(katalog_tymczasowy)
    return env


# Uruchomienie pytest: najpierw import pytest (bez katalogu studenta na sciezce), potem katalog studenta
_ROZRUCH = """
import sys
DRZEWO = {drzewo!r}
sys.path[:] = [p for p in sys.path if p not in ("", ".", DRZEWO)]
import pytest
sys.path.insert(0, DRZEWO)
raise SystemExit(pytest.main({argumenty!r}))
"""


def _usun_pliki_konta(uid: int) -> None:
    """Usuwa z katalogow tymczasowych wszystko, co zostawilo konto testowe."""
    for katalog in (Path(tempfile.gettempdir()), Path("/dev/shm")):
        try:
            wpisy = list(katalog.iterdir())
        except OSError:
            continue
        for wpis in wpisy:
            try:
                if wpis.lstat().st_uid != uid:
                    continue
                if wpis.is_dir() and not wpis.is_symlink():
                    shutil.rmtree(wpis, ignore_errors=True)
                else:
                    wpis.unlink()
            except OSError:
                pass


def _moze_zmienic_uzytkownika(uid: Optional[int]) -> bool:
    return uid is not None and os.name == "posix" and os.geteuid() == 0


def _oddaj(sciezka: Path, uid: int) -> None:
    for element in [sciezka, *sciezka.rglob("*")]:
        os.chown(element, uid, uid, follow_symlinks=False)


def _przygotowanie_procesu(uid: Optional[int]):
    """Funkcja wykonywana w procesie potomnym przed uruchomieniem pytest (tylko systemy uniksowe)."""
    def przygotuj():
        import resource

        limity = [
            (resource.RLIMIT_CPU, 300),
            (resource.RLIMIT_AS, 2 * 1024 ** 3),
            (resource.RLIMIT_FSIZE, 100 * 1024 ** 2),
        ]
        if uid is not None:
            limity.append((resource.RLIMIT_NPROC, 256))
        for zasob, wartosc in limity:
            try:
                resource.setrlimit(zasob, (wartosc, wartosc))
            except (ValueError, OSError):
                pass
        if uid is not None:
            os.setgroups([])
            os.setgid(uid)
            os.setuid(uid)
    return przygotuj


def _zabij_grupe(proces: subprocess.Popen, posix: bool) -> None:
    try:
        if posix:
            import signal

            os.killpg(proces.pid, signal.SIGKILL)
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proces.pid)], capture_output=True)
    except (OSError, ProcessLookupError):
        pass
    try:
        proces.kill()
    except OSError:
        pass


def _zabij_wszystkie_procesy(uid: int) -> None:
    """Zabija wszystkie procesy konta testowego (takze te, ktore uciekly z grupy przez setsid)."""
    import signal

    pid = os.fork()
    if pid == 0:
        try:
            os.setgroups([])
            os.setgid(uid)
            os.setuid(uid)
            os.kill(-1, signal.SIGKILL)
        finally:
            os._exit(0)
    os.waitpid(pid, 0)


_WYJATEK = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt)\b")
_MIEJSCE = re.compile(r'File "([^"]+)", line (\d+)')


def skrot_bledu(komunikat: str, drzewo: str = "") -> str:
    """Jedno zdanie o przyczynie: wyjatek albo niespelniona asercja, przy bledzie skladni z plikiem i linia."""
    linie_e = [linia[1:].strip() for linia in komunikat.splitlines() if linia.startswith("E ")]
    linie_e = [linia for linia in linie_e if linia]
    powod = next((linia for linia in reversed(linie_e) if _WYJATEK.match(linia)), "")
    if not powod:
        powod = next((linia for linia in linie_e if linia.startswith("assert")), "")
    if not powod:
        powod = linie_e[-1] if linie_e else (komunikat.strip().splitlines() or [""])[0]
    if powod.startswith(("SyntaxError", "IndentationError", "TabError")):
        miejsce = _MIEJSCE.search(komunikat)
        if miejsce:
            plik = miejsce.group(1).replace("\\", "/")
            if drzewo:
                plik = plik.replace(drzewo.replace("\\", "/").rstrip("/") + "/", "")
            powod += f" ({plik}, linia {miejsce.group(2)})"
    return powod[:300]


def _odczytaj_junit(sciezka: Path, drzewo: str = "") -> list[dict]:
    if not sciezka.is_file():
        return []
    try:
        korzen = ET.parse(sciezka).getroot()
    except ET.ParseError:
        return []
    przypadki = []
    for przypadek in korzen.iter("testcase"):
        wynik, komunikat = "zdany", ""
        for znacznik, nazwa_wyniku in (("failure", "niezdany"), ("error", "blad"), ("skipped", "pominiety")):
            element = przypadek.find(znacznik)
            if element is not None:
                wynik = nazwa_wyniku
                komunikat = ((element.get("message") or "") + "\n" + (element.text or "")).strip()
                break
        if drzewo:
            komunikat = komunikat.replace(drzewo + os.sep, "").replace(drzewo.replace("\\", "/") + "/", "")
        przypadki.append({
            "nazwa": przypadek.get("name", ""),
            "klasa": przypadek.get("classname", ""),
            "plik": (przypadek.get("file") or "").replace("\\", "/"),
            "wynik": wynik,
            "komunikat": komunikat[-1500:],
            "powod": skrot_bledu(komunikat, drzewo) if wynik in ("niezdany", "blad") else "",
            # Blad wczytania pliku testow (np. blad skladni w kodzie studenta): zaden test z pliku sie nie wykonal
            "wczytanie": wynik == "blad" and komunikat.startswith("collection failure"),
        })
    return przypadki


def uruchom_pytest(drzewo: Path, limit_czasu: int = 120, uid: Optional[int] = None) -> dict:
    """Uruchamia pytest dla przygotowanego drzewa i zwraca wyniki poszczegolnych testow.

    uid: konto, na ktorym ma dzialac kod studenta (uzywane tylko, gdy proces dziala jako root).
    """
    drzewo = Path(drzewo).resolve()
    posix = os.name == "posix"
    zmiana_uzytkownika = _moze_zmienic_uzytkownika(uid)
    with tempfile.TemporaryDirectory(prefix="sprawdzarka-testy-", ignore_cleanup_errors=True) as roboczy:
        roboczy = Path(roboczy)
        katalog_raportu = roboczy / "raport"
        katalog_raportu.mkdir()
        katalog_tymczasowy = roboczy / "tmp"
        katalog_tymczasowy.mkdir()
        raport = katalog_raportu / f"{uuid.uuid4().hex}.xml"
        if zmiana_uzytkownika:
            os.chmod(roboczy, 0o755)
            for katalog in (drzewo, katalog_raportu, katalog_tymczasowy):
                _oddaj(katalog, uid)
        argumenty = [
            "-q", "-p", "no:cacheprovider", "-o", "junit_family=xunit1",
            # Blad w pliku jednego zadania (np. skladni) nie moze zatrzymac testow pozostalych zadan
            "--continue-on-collection-errors",
            f"--junitxml={raport}", "--rootdir", str(drzewo), str(drzewo),
        ]
        polecenie = [sys.executable, "-s", "-B", "-X", "utf8", "-c",
                     _ROZRUCH.format(drzewo=str(drzewo), argumenty=argumenty)]
        start = time.monotonic()
        przekroczono = False
        with open(roboczy / "wyjscie.txt", "w+b") as plik_wyjscia:
            proces = subprocess.Popen(
                polecenie, cwd=drzewo, env=_srodowisko_testow(katalog_tymczasowy, katalog_tymczasowy),
                stdin=subprocess.DEVNULL,
                stdout=plik_wyjscia, stderr=subprocess.STDOUT,
                preexec_fn=_przygotowanie_procesu(uid if zmiana_uzytkownika else None) if posix else None,
                start_new_session=posix,
            )
            try:
                kod_wyjscia = proces.wait(timeout=limit_czasu)
            except subprocess.TimeoutExpired:
                przekroczono = True
                _zabij_grupe(proces, posix)
                try:
                    proces.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                kod_wyjscia = -1
            if zmiana_uzytkownika:
                _zabij_wszystkie_procesy(uid)
                _usun_pliki_konta(uid)
            plik_wyjscia.seek(0)
            wyjscie = plik_wyjscia.read()[-20000:].decode("utf-8", errors="replace")
        if przekroczono:
            wyjscie += f"\nPrzekroczono limit czasu {limit_czasu} s."
        przypadki = _odczytaj_junit(raport, str(drzewo))
    for przypadek in przypadki:
        if przypadek["plik"].startswith(str(drzewo)):
            przypadek["plik"] = Path(przypadek["plik"]).relative_to(drzewo).as_posix()
    return {
        "uruchomiono": True,
        "kod_wyjscia": kod_wyjscia,
        "przekroczono_czas": przekroczono,
        "czas": round(time.monotonic() - start, 2),
        "przypadki": przypadki,
        "dziennik": wyjscie[-6000:],
    }


# --- Kolejka zadan dla odizolowanego procesu testujacego ---

def zlec_w_kolejce(kolejka: Path, drzewo: Path, limit_czasu: int, czekaj_dodatkowo: int = 120) -> dict:
    """Kopiuje drzewo testow do katalogu kolejki i czeka na wynik od procesu testujacego."""
    zadanie = Path(kolejka) / uuid.uuid4().hex
    shutil.copytree(drzewo, zadanie / "kod", ignore=shutil.ignore_patterns(*POMIJANE_KATALOGI))
    for sciezka in [zadanie, *zadanie.rglob("*")]:
        try:
            os.chmod(sciezka, 0o755 if sciezka.is_dir() else 0o644)
        except OSError:
            pass
    (zadanie / "zadanie.json").write_text(json.dumps({"limit_czasu": limit_czasu}))
    (zadanie / "gotowe").write_text("1")
    koniec = time.monotonic() + limit_czasu + czekaj_dodatkowo
    wynik_plik = zadanie / "wynik.json"
    try:
        while time.monotonic() < koniec:
            if wynik_plik.is_file():
                return json.loads(wynik_plik.read_text())
            time.sleep(1)
        return {"uruchomiono": False, "blad": "Proces testujący nie odpowiedział (czy działa narzedzia/tester.py?)."}
    finally:
        shutil.rmtree(zadanie, ignore_errors=True)


def obsluz_kolejke(kolejka: Path, uid: Optional[int] = None) -> Optional[str]:
    """Wykonuje jedno oczekujace zlecenie z kolejki. Zwraca jego nazwe albo None."""
    kolejka = Path(kolejka)
    if not kolejka.is_dir():
        return None
    for zadanie in sorted(kolejka.iterdir(), key=lambda p: p.stat().st_mtime):
        if not (zadanie / "gotowe").is_file() or (zadanie / "wynik.json").exists():
            continue
        try:
            (zadanie / "w_toku").open("x").close()
        except (FileExistsError, OSError):
            continue
        try:
            parametry = json.loads((zadanie / "zadanie.json").read_text())
            with tempfile.TemporaryDirectory(prefix="sprawdzarka-zlecenie-", ignore_cleanup_errors=True) as kopia:
                os.chmod(kopia, 0o755)  # konto testowe musi moc wejsc do katalogu z kodem
                drzewo = Path(kopia) / "kod"
                shutil.copytree(zadanie / "kod", drzewo, symlinks=False)
                wynik = uruchom_pytest(drzewo, int(parametry.get("limit_czasu", 120)), uid)
        except Exception as blad:  # wynik zawsze musi trafic do zlecajacego
            wynik = {"uruchomiono": False, "blad": f"Błąd procesu testującego: {blad}"}
        tymczasowy = zadanie / f"wynik.{uuid.uuid4().hex}.tmp"
        tymczasowy.write_text(json.dumps(wynik))
        os.replace(tymczasowy, zadanie / "wynik.json")
        return zadanie.name
    return None
