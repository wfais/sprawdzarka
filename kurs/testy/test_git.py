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


def _dodaj_actions(serwer, tmp_path):
    robocze = tmp_path / "robocze"
    (robocze / ".github" / "workflows").mkdir(parents=True)
    (robocze / ".github" / "workflows" / "classroom.yml").write_text("name: Autograding\n")
    (robocze / ".github" / "dependabot.yml").write_text("version: 2\n")
    uruchom("add", "-A", ".", cwd=robocze)
    uruchom("commit", "-q", "-m", "z Actions", cwd=robocze)
    uruchom("push", "-q", str(serwer / "wfais/zrodlo.git"), "main", cwd=robocze)


def test_migawka_pomija_github_actions(zrodlo, zdalne, tmp_path):
    serwer, utworz_puste = zdalne
    _dodaj_actions(serwer, tmp_path)
    utworz_puste("wfais/start")
    git.migawka_startowa("wfais/zrodlo", "wfais/start", "Stan startowy")
    start = serwer / "wfais/start.git"
    pliki = uruchom("ls-tree", "-r", "--name-only", "main", cwd=start).splitlines()
    assert sorted(pliki) == [".github/dependabot.yml", "README.md", "ZADANIE1/main.py"]
    assert "Pominieto .github/workflows" in uruchom("log", "-1", "--format=%B", "main", cwd=start)


def test_migawka_z_github_actions_na_zyczenie(zrodlo, zdalne, tmp_path):
    serwer, utworz_puste = zdalne
    _dodaj_actions(serwer, tmp_path)
    utworz_puste("wfais/start")
    git.migawka_startowa("wfais/zrodlo", "wfais/start", "Stan startowy", z_actions=True)
    pliki = uruchom("ls-tree", "-r", "--name-only", "main", cwd=serwer / "wfais/start.git").splitlines()
    assert ".github/workflows/classroom.yml" in pliki


def test_brak_uprawnienia_workflows_ma_czytelny_komunikat(monkeypatch, tmp_path):
    class Wynik:
        returncode = 1
        stdout = ""
        stderr = ("! [remote rejected] main -> main (refusing to allow a GitHub App to create or update workflow "
                  "`.github/workflows/x.yml` without `workflows` permission)")

    monkeypatch.setattr(git.subprocess, "run", lambda *a, **k: Wynik())
    with pytest.raises(git.BladGit, match="uprawnienia Workflows"):
        git._git(["push", "x"], str(tmp_path), "TOKEN")


# --- Poprawki prowadzacego ---

def _student_z_praca(serwer, utworz_puste, tmp_path, tresc_main):
    """Repozytorium studenta z jego zmiana w ZADANIE1/main.py; zwraca (S0, katalog roboczy studenta)."""
    utworz_puste("wfais/start")
    s0 = git.migawka_startowa("wfais/zrodlo", "wfais/start", "Stan startowy")
    utworz_puste("wfais/student")
    git.przygotuj_repo_studenta("wfais/start", "wfais/student", "jank")
    praca = tmp_path / "praca"
    uruchom("clone", "-q", str(serwer / "wfais/student.git"), str(praca), cwd=tmp_path)
    uruchom("config", "user.email", "s@x", cwd=praca)
    uruchom("config", "user.name", "Student", cwd=praca)
    (praca / "ZADANIE1" / "main.py").write_text(tresc_main)
    uruchom("commit", "-q", "-am", "rozwiazanie", cwd=praca)
    uruchom("push", "-q", "origin", "main", cwd=praca)
    return s0, praca


def _poprawka_w_zrodle(serwer, tmp_path, pliki):
    robocze = tmp_path / "robocze"
    for sciezka, tresc in pliki.items():
        (robocze / sciezka).parent.mkdir(parents=True, exist_ok=True)
        (robocze / sciezka).write_text(tresc)
    uruchom("add", "-A", ".", cwd=robocze)
    uruchom("commit", "-q", "-m", "poprawka", cwd=robocze)
    uruchom("push", "-q", str(serwer / "wfais/zrodlo.git"), "main", cwd=robocze)


def test_poprawka_scalana_automatycznie_z_praca_studenta(zrodlo, zdalne, tmp_path):
    serwer, utworz_puste = zdalne
    s0, _ = _student_z_praca(serwer, utworz_puste, tmp_path, "def f():\n    return 42\n")
    _poprawka_w_zrodle(serwer, tmp_path, {"README.md": "# Zestaw 1\nPoprawiona tresc\n",
                                          "ZADANIE1/tests/test_main.py": "def test_x():\n    assert True\n"})
    s1 = git.poprawka_startowa("wfais/zrodlo", "wfais/start", "Poprawka prowadzacego nr 1")
    start = serwer / "wfais/start.git"
    assert uruchom("rev-parse", "main^", cwd=start) == s0
    assert git.poprawka_startowa("wfais/zrodlo", "wfais/start", "jeszcze raz") is None

    wynik = git.scal_poprawke("wfais/start", "wfais/student", s1, "poprawka-1", True, "Poprawka prowadzacego nr 1")
    repo = serwer / "wfais/student.git"
    assert wynik.scalona and wynik.feedback and not wynik.konflikty
    assert uruchom("rev-parse", "feedback", cwd=repo) == s1
    assert uruchom("show", "main:ZADANIE1/main.py", cwd=repo) == "def f():\n    return 42"
    assert "Poprawiona tresc" in uruchom("show", "main:README.md", cwd=repo)
    assert uruchom("show", "main:ZADANIE1/tests/test_main.py", cwd=repo).startswith("def test_x")
    # Powtorka niczego nie dubluje
    ponownie = git.scal_poprawke("wfais/start", "wfais/student", s1, "poprawka-1", True, "x")
    assert ponownie.scalona and ponownie.sha == wynik.sha


def test_konflikt_trafia_na_galaz_do_scalenia_przez_studenta(zrodlo, zdalne, tmp_path):
    serwer, utworz_puste = zdalne
    _student_z_praca(serwer, utworz_puste, tmp_path, "def f():\n    return 42\n")
    _poprawka_w_zrodle(serwer, tmp_path, {"ZADANIE1/main.py": "def f():\n    raise NotImplementedError\n"})
    s1 = git.poprawka_startowa("wfais/zrodlo", "wfais/start", "Poprawka prowadzacego nr 1")
    repo = serwer / "wfais/student.git"
    main_przed = uruchom("rev-parse", "main", cwd=repo)
    wynik = git.scal_poprawke("wfais/start", "wfais/student", s1, "poprawka-1", True, "Poprawka prowadzacego nr 1")
    assert not wynik.scalona and wynik.konflikty == ["ZADANIE1/main.py"]
    assert uruchom("rev-parse", "main", cwd=repo) == main_przed  # praca studenta nietknieta
    assert uruchom("rev-parse", "poprawka-1", cwd=repo) == s1
    assert uruchom("rev-parse", "feedback", cwd=repo) == s1


def test_bez_autoscalania_kazdy_scala_sam(zrodlo, zdalne, tmp_path):
    serwer, utworz_puste = zdalne
    _student_z_praca(serwer, utworz_puste, tmp_path, "def f():\n    return 42\n")
    _poprawka_w_zrodle(serwer, tmp_path, {"README.md": "# Zestaw 1\nPoprawiona tresc\n"})
    s1 = git.poprawka_startowa("wfais/zrodlo", "wfais/start", "Poprawka prowadzacego nr 1")
    wynik = git.scal_poprawke("wfais/start", "wfais/student", s1, "poprawka-1", False, "x")
    repo = serwer / "wfais/student.git"
    assert not wynik.scalona and not wynik.konflikty
    assert uruchom("rev-parse", "poprawka-1", cwd=repo) == s1
    assert "Poprawiona" not in uruchom("show", "main:README.md", cwd=repo)
