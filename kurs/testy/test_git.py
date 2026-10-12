"""Prawdziwe operacje git na lokalnych repozytoriach (zamiast GitHub)."""
import shutil
import subprocess

import pytest

from kurs.github import git
from kurs.github import klient

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="brak programu git")


def uruchom(*argumenty, cwd):
    return subprocess.run(["git", *argumenty], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def zdalne(tmp_path, monkeypatch):
    """Katalog z repozytoriami 'na serwerze': pelna nazwa org/repo to katalog bare."""
    serwer = tmp_path / "serwer"
    serwer.mkdir()

    def adres(pelna, token):
        return str(serwer / f"{pelna}.git")

    def utworz_puste(pelna):
        sciezka = serwer / f"{pelna}.git"
        sciezka.parent.mkdir(parents=True, exist_ok=True)
        uruchom("init", "-q", "--bare", "-b", "main", str(sciezka), cwd=tmp_path)

    monkeypatch.setattr(git, "adres_repo", adres)
    monkeypatch.setattr(klient, "token_instalacji", lambda: "TOKEN")
    return serwer, utworz_puste


@pytest.fixture
def zrodlo(tmp_path, zdalne):
    serwer, utworz_puste = zdalne
    robocze = tmp_path / "robocze"
    robocze.mkdir()
    uruchom("init", "-q", "-b", "main", cwd=robocze)
    uruchom("config", "user.email", "p@x", cwd=robocze)
    uruchom("config", "user.name", "Prowadzacy", cwd=robocze)
    (robocze / "rozwiazanie.py").write_text("print('tajne rozwiazanie')\n")
    uruchom("add", ".", cwd=robocze)
    uruchom("commit", "-q", "-m", "z rozwiazaniem", cwd=robocze)
    (robocze / "rozwiazanie.py").unlink()
    (robocze / "ZADANIE1").mkdir()
    (robocze / "ZADANIE1" / "main.py").write_text("def f():\n    pass\n")
    (robocze / "README.md").write_text("# Zestaw 1\n")
    uruchom("add", "-A", ".", cwd=robocze)
    uruchom("commit", "-q", "-m", "wersja dla studentow", cwd=robocze)
    utworz_puste("wfais/zrodlo")
    uruchom("push", "-q", str(serwer / "wfais/zrodlo.git"), "main", cwd=robocze)
    return serwer


def test_migawka_bez_historii(zrodlo, zdalne):
    serwer, utworz_puste = zdalne
    utworz_puste("wfais/start")
    sha = git.migawka_startowa("wfais/zrodlo", "wfais/start", "Stan startowy")
    start = serwer / "wfais/start.git"
    assert uruchom("rev-parse", "main", cwd=start) == sha
    assert uruchom("rev-list", "--count", "main", cwd=start) == "1"
    pliki = uruchom("ls-tree", "-r", "--name-only", "main", cwd=start).splitlines()
    assert sorted(pliki) == ["README.md", "ZADANIE1/main.py"]
    # Ponowne wywolanie nie psuje istniejacego stanu
    assert git.migawka_startowa("wfais/zrodlo", "wfais/start", "Stan startowy") == sha


def test_repo_studenta_z_galezia_feedback(zrodlo, zdalne):
    serwer, utworz_puste = zdalne
    utworz_puste("wfais/start")
    git.migawka_startowa("wfais/zrodlo", "wfais/start", "Stan startowy")
    utworz_puste("wfais/student")
    bazowy, poczatkowy = git.przygotuj_repo_studenta("wfais/start", "wfais/student", "jank")
    repo = serwer / "wfais/student.git"
    assert uruchom("rev-parse", "feedback", cwd=repo) == bazowy
    assert uruchom("rev-parse", "main", cwd=repo) == poczatkowy
    assert uruchom("rev-parse", "main^", cwd=repo) == bazowy
    assert uruchom("rev-parse", "main^{tree}", cwd=repo) == uruchom("rev-parse", "feedback^{tree}", cwd=repo)
    assert "@jank" in uruchom("log", "-1", "--format=%s", "main", cwd=repo)
    # Powtorzenie (np. po przerwanym tworzeniu) zwraca ten sam stan
    assert git.przygotuj_repo_studenta("wfais/start", "wfais/student", "jank") == (bazowy, poczatkowy)


def test_odtworzenie_brakujacej_galezi_feedback(zrodlo, zdalne):
    serwer, utworz_puste = zdalne
    utworz_puste("wfais/start")
    git.migawka_startowa("wfais/zrodlo", "wfais/start", "Stan startowy")
    utworz_puste("wfais/student")
    bazowy, poczatkowy = git.przygotuj_repo_studenta("wfais/start", "wfais/student", "jank")
    repo = serwer / "wfais/student.git"
    uruchom("branch", "-D", "feedback", cwd=repo)
    assert git.przygotuj_repo_studenta("wfais/start", "wfais/student", "jank") == (bazowy, poczatkowy)
    assert uruchom("rev-parse", "feedback", cwd=repo) == bazowy


def test_token_ukryty_w_komunikacie_bledu(tmp_path):
    with pytest.raises(git.BladGit) as blad:
        git._git(["fetch", "https://x-access-token:SEKRET123@127.0.0.1:9/a.git", "main"], str(tmp_path), "SEKRET123")
    assert "SEKRET123" not in str(blad.value)
