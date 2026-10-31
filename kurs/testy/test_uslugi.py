from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from kurs import uslugi
from kurs.models import Student, Wypchniecie, Zgloszenie


@pytest.mark.django_db
def test_akceptacja_tworzy_repo_zaproszenie_i_pr(github, przydzial):
    z = uslugi.akceptuj(przydzial, 77, "JanK", "Jan Kowalski")
    assert z.status == Zgloszenie.Status.GOTOWE
    assert z.repo_nazwa == "wfais/py-z03-g4-jank"
    assert github.zaproszenia == [("wfais/py-z03-g4-jank", "JanK")]
    assert github.pull_requesty["wfais/py-z03-g4-jank"] == ("main", "feedback", "Feedback")
    assert z.sha_bazowy == "b" * 40 and z.sha_poczatkowy == "c" * 40
    student = Student.objects.get(github_id=77)
    assert student.imie_nazwisko == "Jan Kowalski"
    assert student.zrodlo_nazwiska == Student.Zrodlo.FORMULARZ


@pytest.mark.django_db
def test_powtorna_akceptacja_nie_tworzy_drugiego_repo(github, przydzial):
    z1 = uslugi.akceptuj(przydzial, 77, "jank", "Jan Kowalski")
    z2 = uslugi.akceptuj(przydzial, 77, "jank", "")
    assert z1.pk == z2.pk
    assert len(github.repozytoria) == 1
    assert len(github.zaproszenia) == 1


@pytest.mark.django_db
def test_nazwisko_z_formularza_nie_nadpisuje_ustalonego(github, przydzial):
    Student.objects.create(github_id=77, login="jank", imie_nazwisko="Jan Kowalski",
                           zrodlo_nazwiska=Student.Zrodlo.RECZNIE)
    uslugi.akceptuj(przydzial, 77, "jank", "Ktos Inny")
    assert Student.objects.get(github_id=77).imie_nazwisko == "Jan Kowalski"


@pytest.mark.django_db
def test_zmiana_loginu_aktualizowana(github, przydzial):
    Student.objects.create(github_id=77, login="stary")
    uslugi.akceptuj(przydzial, 77, "nowy", "Jan Kowalski")
    assert Student.objects.get(github_id=77).login == "nowy"


@pytest.mark.django_db
def test_blad_i_wznowienie_od_przerwanego_kroku(github, przydzial):
    github.blad_przy = "zapros"
    z = uslugi.akceptuj(przydzial, 77, "jank", "Jan Kowalski")
    assert z.status == Zgloszenie.Status.BLAD
    assert "zapros" in z.blad_opis
    assert z.repo_id  # repozytorium juz powstalo
    github.blad_przy = None
    z = uslugi.akceptuj(przydzial, 77, "jank", "")
    assert z.status == Zgloszenie.Status.GOTOWE
    assert len(github.repozytoria) == 1


@pytest.mark.django_db
def test_brak_repo_startowego_tworzy_je_przy_akceptacji(github, przydzial):
    przydzial.repo_startowe = ""
    przydzial.sha_startowy = ""
    przydzial.save()
    z = uslugi.akceptuj(przydzial, 77, "jank", "Jan Kowalski")
    assert z.status == Zgloszenie.Status.GOTOWE
    przydzial.refresh_from_db()
    assert przydzial.repo_startowe == "wfais/py-z03-g4-start"
    assert github.migawki == [("wfais/python-zestaw3", "wfais/py-z03-g4-start")]


@pytest.mark.django_db
def test_zerowanie_archiwizuje_i_pozwala_zaakceptowac_od_nowa(github, przydzial):
    z1 = uslugi.akceptuj(przydzial, 77, "jank", "Jan Kowalski")
    komunikaty = uslugi.wyzeruj(z1)
    z1.refresh_from_db()
    assert z1.status == Zgloszenie.Status.WYZEROWANE
    assert z1.repo_nazwa.startswith("wfais/py-z03-g4-jank-arch-")
    assert github.odebrane == [("wfais/py-z03-g4-jank", "jank")]
    assert any("archiwum" in k for k in komunikaty)
    z2 = uslugi.akceptuj(przydzial, 77, "jank", "")
    assert z2.pk != z1.pk
    assert z2.repo_nazwa == "wfais/py-z03-g4-jank"
    assert z2.repo_id != z1.repo_id


@pytest.mark.django_db
def test_zerowanie_z_usunieciem_od_razu_zwalnia_nazwe(github, przydzial):
    """GitHub usuwa z opoznieniem: przed usunieciem zmiana nazwy, zeby student mogl od razu zaakceptowac."""
    z1 = uslugi.akceptuj(przydzial, 77, "jank", "Jan Kowalski")
    komunikaty = uslugi.wyzeruj(z1, usun_repo=True)
    assert len(github.usuniete) == 1 and github.usuniete[0].startswith("wfais/py-z03-g4-jank-usuniete-")
    assert any("usunięte" in k for k in komunikaty)
    z2 = uslugi.akceptuj(przydzial, 77, "jank", "")
    assert z2.status == Zgloszenie.Status.GOTOWE and z2.repo_nazwa == "wfais/py-z03-g4-jank"


@pytest.mark.django_db
def test_nazwa_w_trakcie_usuwania_dostaje_przyrostek(github, przydzial, monkeypatch):
    """Repozytorium usuniete recznie na GitHubie: nazwa chwilowo zajeta, student nie czeka."""
    oryginal = github.utworz_repo

    def utworz(nazwa, opis=""):
        if nazwa == "py-z03-g4-jank":
            raise uslugi.gh.NazwaZajeta(409, "w trakcie usuwania")
        return oryginal(nazwa, opis)

    monkeypatch.setattr(uslugi.gh, "utworz_repo", utworz)
    z = uslugi.akceptuj(przydzial, 77, "jank", "Jan Kowalski")
    assert z.status == Zgloszenie.Status.GOTOWE and z.repo_nazwa == "wfais/py-z03-g4-jank-2"


@pytest.mark.django_db
def test_nazwa_zajeta_przez_stare_zgloszenie_dostaje_przyrostek(github, przydzial):
    """Zgloszenie wyzerowane przez starsza wersje mimo bledu GitHub: stare repozytorium ma nadal te nazwe."""
    z1 = uslugi.akceptuj(przydzial, 77, "jank", "Jan Kowalski")
    Zgloszenie.objects.filter(pk=z1.pk).update(status=Zgloszenie.Status.WYZEROWANE)
    z2 = uslugi.akceptuj(przydzial, 77, "jank", "")
    assert z2.repo_nazwa == "wfais/py-z03-g4-jank-2"


@pytest.mark.django_db
def test_blad_github_przy_zerowaniu_zostawia_zgloszenie(github, przydzial, monkeypatch):
    """Awaria GitHub w czasie zerowania: zgloszenie bez zmian, ponowna akceptacja nie daje drugiego repozytorium."""
    z1 = uslugi.akceptuj(przydzial, 77, "jank", "Jan Kowalski")

    def awaria(*args):
        raise uslugi.gh.BladGitHub(502, "awaria")

    monkeypatch.setattr(uslugi.gh, "odbierz_dostep", awaria)
    with pytest.raises(uslugi.gh.BladGitHub):
        uslugi.wyzeruj(z1, usun_repo=True)
    z1.refresh_from_db()
    assert z1.status == Zgloszenie.Status.GOTOWE and z1.repo_nazwa == "wfais/py-z03-g4-jank"
    assert github.usuniete == [] and github.archiwum == []
    assert uslugi.akceptuj(przydzial, 77, "jank", "").pk == z1.pk


@pytest.mark.django_db
def test_ponowione_zerowanie_po_bledzie_usuwania(github, przydzial, monkeypatch):
    z1 = uslugi.akceptuj(przydzial, 77, "jank", "Jan Kowalski")
    usun = uslugi.gh.usun_repo

    def awaria(pelna):
        raise uslugi.gh.BladGitHub(502, "awaria")

    monkeypatch.setattr(uslugi.gh, "usun_repo", awaria)
    with pytest.raises(uslugi.gh.BladGitHub):
        uslugi.wyzeruj(z1, usun_repo=True)
    z1.refresh_from_db()
    assert z1.status == Zgloszenie.Status.GOTOWE and z1.repo_nazwa.startswith("wfais/py-z03-g4-jank-usuniete-")
    monkeypatch.setattr(uslugi.gh, "usun_repo", usun)
    uslugi.wyzeruj(z1, usun_repo=True)
    z1.refresh_from_db()
    assert z1.status == Zgloszenie.Status.WYZEROWANE
    assert github.usuniete == [z1.repo_nazwa]  # nazwa zmieniona tylko raz


@pytest.mark.django_db
def test_zerowanie_gdy_repozytorium_juz_nie_istnieje(github, przydzial, monkeypatch):
    z1 = uslugi.akceptuj(przydzial, 77, "jank", "Jan Kowalski")

    def brak(*args):
        raise uslugi.gh.BladGitHub(404, "Not Found")

    monkeypatch.setattr(uslugi.gh, "odbierz_dostep", brak)
    komunikaty = uslugi.wyzeruj(z1)
    z1.refresh_from_db()
    assert z1.status == Zgloszenie.Status.WYZEROWANE and any("nie istnieje" in k for k in komunikaty)


def _zgloszenie_gotowe(przydzial, github):
    return uslugi.akceptuj(przydzial, 77, "jank", "Jan Kowalski")


@pytest.mark.django_db
def test_przeliczenie_terminow_na_podstawie_wypchniec(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    teraz = timezone.now()
    przydzial.termin_miekki = teraz - timedelta(days=2)
    przydzial.termin_twardy = teraz - timedelta(days=1)
    przydzial.save()
    Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main",
                               sha_przed="0" * 40, sha_po="c" * 40, czas=teraz - timedelta(days=5), zrodlo="webhook")
    Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main",
                               sha_przed="c" * 40, sha_po="d" * 40, czas=teraz - timedelta(days=3), zrodlo="webhook")
    Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main",
                               sha_przed="d" * 40, sha_po="e" * 40, czas=teraz - timedelta(hours=36), zrodlo="webhook")
    github.porownania = {
        ("b" * 40, "e" * 40): ["ZADANIE1/main.py", "README.md"],
        ("d" * 40, "e" * 40): ["ZADANIE1/main.py"],
    }
    uslugi.przelicz(z)
    z.refresh_from_db()
    assert z.stan_kolor == Zgloszenie.Kolor.ZOLTY
    assert z.sugerowany_mnoznik == Decimal("0.8")
    assert z.sha_oceniany == "e" * 40
    assert z.ostatnie_wypchniecie is not None


@pytest.mark.django_db
def test_zmiana_tylko_readme_po_terminie_nie_jest_kara(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    teraz = timezone.now()
    przydzial.termin_miekki = teraz - timedelta(days=2)
    przydzial.termin_twardy = teraz - timedelta(days=1)
    przydzial.save()
    for sha_przed, sha_po, dni in [("c", "d", 3), ("d", "e", 1.5)]:
        Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main",
                                   sha_przed=sha_przed * 40, sha_po=sha_po * 40,
                                   czas=teraz - timedelta(days=dni), zrodlo="webhook")
    github.porownania = {("b" * 40, "e" * 40): ["main.py", "README.md"], ("d" * 40, "e" * 40): ["README.md"]}
    uslugi.przelicz(z)
    z.refresh_from_db()
    assert z.stan_kolor == Zgloszenie.Kolor.ZIELONY


@pytest.mark.django_db
def test_porownania_sa_zapamietywane(github, przydzial, monkeypatch):
    z = _zgloszenie_gotowe(przydzial, github)
    wywolania = []
    oryginal = github.porownaj

    def licz(pelna, od, do):
        wywolania.append((od, do))
        return oryginal(pelna, od, do)

    monkeypatch.setattr(uslugi.gh, "porownaj", licz)
    Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main",
                               sha_przed="c" * 40, sha_po="d" * 40, czas=timezone.now(), zrodlo="webhook")
    uslugi.przelicz(z)
    uslugi.przelicz(z)
    assert len(wywolania) == 1


@pytest.mark.django_db
def test_synchronizacja_z_dziennika_aktywnosci(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    github.aktywnosci[z.repo_nazwa] = [
        {"activity_type": "branch_creation", "ref": "refs/heads/main", "before": "0" * 40, "after": "c" * 40,
         "timestamp": "2026-10-20T10:00:00Z", "actor": {"login": "sprawdzarka[bot]"}},
        {"activity_type": "push", "ref": "refs/heads/main", "before": "c" * 40, "after": "d" * 40,
         "timestamp": "2026-10-21T10:00:00Z", "actor": {"login": "jank"}},
        {"activity_type": "pr_merge", "ref": "refs/heads/main", "before": "d" * 40, "after": "f" * 40,
         "timestamp": "2026-10-22T10:00:00Z", "actor": {"login": "jank"}},
    ]
    # Scalenie pull requestu do main tez zmienia stan galezi i musi byc zarejestrowane
    assert uslugi.synchronizuj(z) == 3
    assert uslugi.synchronizuj(z) == 0
    assert Wypchniecie.objects.filter(repo_id=z.repo_id).count() == 3


@pytest.mark.django_db
def test_kandydat_nazwiska_z_readme(github, przydzial):
    z = uslugi.akceptuj(przydzial, 88, "x_coder_x", "")
    Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main",
                               sha_przed="c" * 40, sha_po="d" * 40, czas=timezone.now(), zrodlo="webhook")
    github.readme_tresc[(z.repo_nazwa, "d" * 40)] = "# Zestaw 3\nImię i nazwisko: Ewa Lis\n"
    github.readme_tresc[(z.repo_nazwa, "b" * 40)] = "# Zestaw 3\nImię i nazwisko: (wpisz)\n"
    uslugi.przelicz(z)
    student = Student.objects.get(github_id=88)
    assert student.kandydat_readme == "Ewa Lis"
    assert student.imie_nazwisko == ""


@pytest.mark.django_db
def test_przyklad_z_readme_startowego_nie_jest_kandydatem(github, przydzial):
    z = uslugi.akceptuj(przydzial, 88, "x_coder_x", "")
    Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main",
                               sha_przed="c" * 40, sha_po="d" * 40, czas=timezone.now(), zrodlo="webhook")
    github.readme_tresc[(z.repo_nazwa, "d" * 40)] = "Autor: Jan Kowalski\n"
    github.readme_tresc[(z.repo_nazwa, "b" * 40)] = "Autor: Jan Kowalski\n"
    uslugi.przelicz(z)
    assert Student.objects.get(github_id=88).kandydat_readme == ""


@pytest.mark.django_db
def test_zamkniecie_z_ocena(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    uslugi.zamknij_z_ocena(z, Decimal("4.5"), Decimal("0.8"), "Dobra robota w zadaniu 2.")
    z.refresh_from_db()
    assert z.pr_zamkniety
    assert z.wynik == Decimal("3.60")
    pelna, numer, tresc = github.komentarze[0]
    assert "## Ocena: 3,6 / 5 pkt" in tresc
    assert "RAZEM: 4,5 x 0,8 = 3,6 / 5" in tresc
    assert "Dobra robota" in tresc
    assert github.zamkniete == [(z.repo_nazwa, 1)]


@pytest.mark.django_db
def test_cykl_robota_przelicza_oczekujace(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    assert z.do_przeliczenia
    uslugi.cykl_robota(pelny=False)
    z.refresh_from_db()
    assert not z.do_przeliczenia
    assert z.przeliczono is not None


@pytest.mark.django_db
def test_ponowne_zamkniecie_nie_dubluje_komentarza(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    github.blad_zamkniecia = 500
    with pytest.raises(uslugi.gh.BladGitHub):
        uslugi.zamknij_z_ocena(z, Decimal("4"), Decimal("1"), "OK")
    z.refresh_from_db()
    assert z.punkty == Decimal("4") and not z.pr_zamkniety  # decyzja zapisana mimo bledu
    github.blad_zamkniecia = None
    uslugi.zamknij_z_ocena(z, Decimal("4"), Decimal("1"), "OK")
    assert len(github.komentarze) == 1
    z.refresh_from_db()
    assert z.pr_zamkniety


@pytest.mark.django_db
def test_scalony_pull_request_nie_blokuje_oceny(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    github.blad_zamkniecia = 422
    uslugi.zamknij_z_ocena(z, Decimal("3"), Decimal("1"), "")
    z.refresh_from_db()
    assert z.pr_zamkniety and z.punkty == Decimal("3")


@pytest.mark.django_db
def test_galaz_feedback_zabezpieczona(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    assert github.chronione == [(z.repo_nazwa, "feedback")]


@pytest.mark.django_db
def test_indywidualny_termin_pozniejszy_niz_ostateczny_grupy(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    z.wyjatek_termin_miekki = przydzial.termin_twardy + timedelta(days=3)
    assert z.termin_twardy == z.wyjatek_termin_miekki


@pytest.mark.django_db
def test_porzucone_tworzenie_podejmowane_przez_robota(github, przydzial):
    from kurs.models import Student

    student = Student.objects.create(github_id=5, login="porzucony")
    stare = timezone.now() - timedelta(minutes=10)
    z = Zgloszenie.objects.create(przydzial=przydzial, student=student, w_toku_od=stare)
    Zgloszenie.objects.filter(pk=z.pk).update(utworzono=stare)
    uslugi.cykl_robota(pelny=False)
    z.refresh_from_db()
    assert z.status == Zgloszenie.Status.GOTOWE


@pytest.mark.django_db
def test_ponowne_zaproszenie(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    uslugi.ponow_zaproszenie(z)
    assert github.zaproszenia[-1] == (z.repo_nazwa, "jank")


@pytest.mark.django_db
def test_recenzja_nie_jest_dublowana_po_bledzie_komentarza(github, przydzial, monkeypatch):
    z = _zgloszenie_gotowe(przydzial, github)
    uwagi = [{"plik": "a.py", "linia": 1, "tekst": "x"}]
    proby = {"n": 0}
    oryginal = github.komentarz_pr

    def zawodny(pelna, numer, tresc):
        proby["n"] += 1
        if proby["n"] == 1:
            raise uslugi.gh.BladGitHub(502, "Bad Gateway")
        oryginal(pelna, numer, tresc)

    monkeypatch.setattr(uslugi.gh, "komentarz_pr", zawodny)
    with pytest.raises(uslugi.gh.BladGitHub):
        uslugi.zamknij_z_ocena(z, Decimal("4"), Decimal("1"), "OK", uwagi=uwagi)
    assert uslugi.zamknij_z_ocena(z, Decimal("4"), Decimal("1"), "OK", uwagi=uwagi)
    assert len(github.recenzje) == 1 and len(github.komentarze) == 1


@pytest.mark.django_db
def test_brak_konfiguracji_github_nie_zmienia_kolorow(przydzial, monkeypatch):
    from kurs.models import Student

    student = Student.objects.create(github_id=5, login="s")
    z = Zgloszenie.objects.create(przydzial=przydzial, student=student, status=Zgloszenie.Status.GOTOWE, repo_id=1,
                                  repo_nazwa="wfais/x", sha_bazowy="b" * 40, sha_poczatkowy="c" * 40,
                                  stan_kolor=Zgloszenie.Kolor.ZIELONY)
    Wypchniecie.objects.create(repo_id=1, repo_nazwa="wfais/x", ref="refs/heads/main", sha_przed="c" * 40,
                               sha_po="d" * 40, czas=timezone.now(), zrodlo="webhook")

    def brak(*a, **k):
        raise uslugi.gh.BladGitHub(0, "Brak GITHUB_APP_ID w konfiguracji.")

    monkeypatch.setattr(uslugi.gh, "porownaj", brak)
    with pytest.raises(uslugi.gh.BladGitHub):
        uslugi.przelicz(z)
    z.refresh_from_db()
    assert z.stan_kolor == Zgloszenie.Kolor.ZIELONY and z.do_przeliczenia


def _wypchnij(z, sha_przed, sha_po, godzin_temu):
    Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main",
                               sha_przed=sha_przed * 40, sha_po=sha_po * 40,
                               czas=timezone.now() - timedelta(hours=godzin_temu), zrodlo="webhook")


@pytest.mark.django_db
def test_znakowanie_zmienionych_testow(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    _wypchnij(z, "c", "d", 2)
    baza = {"ZADANIE1/main.py": "1", "ZADANIE1/tests/test_main.py": "2", "ZADANIE1/tests/__init__.py": "3"}
    github.drzewa = {
        "b" * 40: baza,
        "d" * 40: {"ZADANIE1/main.py": "9", "ZADANIE1/tests/test_main.py": "8", "tests/test_moj.py": "7"},
    }
    github.porownania = {("b" * 40, "d" * 40): [
        "ZADANIE1/main.py", "ZADANIE1/tests/__init__.py", "ZADANIE1/tests/test_main.py", "tests/test_moj.py",
    ]}
    uslugi.przelicz(z)
    z.refresh_from_db()
    assert z.testy_zmienione and z.zmiany_testow_powazne
    assert z.zmiany_testow == {
        "zmienione": ["ZADANIE1/tests/test_main.py"],
        "usuniete": ["ZADANIE1/tests/__init__.py"],
        "dodane": ["tests/test_moj.py"],
    }
    assert "zmienione: ZADANIE1/tests/test_main.py" in z.opis_zmian_testow

    # Student przywraca testy: flaga zostaje, zmiany trafiaja do "wczesniej"
    _wypchnij(z, "d", "e", 1)
    github.drzewa["e" * 40] = {**baza, "ZADANIE1/main.py": "9"}
    github.porownania[("b" * 40, "e" * 40)] = ["ZADANIE1/main.py"]
    uslugi.przelicz(z)
    z.refresh_from_db()
    assert z.testy_zmienione
    assert z.zmiany_testow == {
        "wczesniej": ["ZADANIE1/tests/__init__.py", "ZADANIE1/tests/test_main.py", "tests/test_moj.py"],
    }


@pytest.mark.django_db
def test_bez_zmian_w_testach_brak_flagi(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    _wypchnij(z, "c", "d", 2)
    github.porownania = {("b" * 40, "d" * 40): ["ZADANIE1/main.py", "README.md"]}
    uslugi.przelicz(z)
    z.refresh_from_db()
    assert not z.testy_zmienione and z.zmiany_testow == {}


@pytest.mark.django_db
def test_tylko_dodane_testy_to_lagodny_znacznik(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    _wypchnij(z, "c", "d", 2)
    github.drzewa = {"b" * 40: {"ZADANIE1/main.py": "1"}, "d" * 40: {"ZADANIE1/main.py": "1", "tests/test_moj.py": "2"}}
    github.porownania = {("b" * 40, "d" * 40): ["tests/test_moj.py"]}
    uslugi.przelicz(z)
    z.refresh_from_db()
    assert z.testy_zmienione and not z.zmiany_testow_powazne


@pytest.mark.django_db
def test_brak_drzewa_traktowany_jako_zmiana(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    _wypchnij(z, "c", "d", 2)
    github.porownania = {("b" * 40, "d" * 40): ["ZADANIE1/tests/test_main.py"]}
    uslugi.przelicz(z)
    z.refresh_from_db()
    assert z.zmiany_testow == {"zmienione": ["ZADANIE1/tests/test_main.py"]}


@pytest.mark.django_db
def test_niepewne_porownanie_nie_zdejmuje_flagi(github, przydzial, monkeypatch):
    z = _zgloszenie_gotowe(przydzial, github)
    Zgloszenie.objects.filter(pk=z.pk).update(testy_zmienione=True, zmiany_testow={"zmienione": ["tests/test_a.py"]})
    z.refresh_from_db()
    _wypchnij(z, "c", "d", 2)

    def porownaj(pelna, od, do):
        raise uslugi.gh.BladGitHub(404, "brak")

    monkeypatch.setattr(uslugi.gh, "porownaj", porownaj)
    uslugi.przelicz(z)
    z.refresh_from_db()
    assert z.testy_zmienione
    assert z.zmiany_testow == {"zmienione": ["tests/test_a.py"], "niepewne": True}


@pytest.mark.django_db
def test_wypchniecia_prowadzacego_pomijane(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    teraz = timezone.now()
    przydzial.termin_miekki = teraz - timedelta(days=2)
    przydzial.termin_twardy = teraz - timedelta(days=1)
    przydzial.save()
    student = z.student
    # utworzenie galezi przez aplikacje, wypchniecie studenta w terminie, poprawka prowadzacego po terminie
    Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main", sha_przed="0" * 40,
                               sha_po="c" * 40, czas=teraz - timedelta(days=5), autor="sprawdzarka-wfais[bot]",
                               autor_id=999, zrodlo="aktywnosc")
    Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main", sha_przed="c" * 40,
                               sha_po="d" * 40, czas=teraz - timedelta(days=3), autor=student.login,
                               autor_id=student.github_id, zrodlo="aktywnosc")
    Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main", sha_przed="d" * 40,
                               sha_po="e" * 40, czas=teraz - timedelta(hours=2), autor="witoldprzygoda",
                               autor_id=38453366, zrodlo="aktywnosc")
    github.porownania = {
        ("b" * 40, "d" * 40): ["ZADANIE1/main.py"],
        ("c" * 40, "d" * 40): ["ZADANIE1/main.py"],
        ("b" * 40, "e" * 40): ["ZADANIE1/main.py"],
        ("d" * 40, "e" * 40): ["ZADANIE1/main.py"],
    }
    uslugi.przelicz(z)
    z.refresh_from_db()
    assert z.stan_kolor == Zgloszenie.Kolor.ZIELONY
    assert z.sha_oceniany == "d" * 40 and z.sha_teraz == "e" * 40
    assert "Pominięto wypchnięcia z innych kont" in z.stan_opis and ": 1." in z.stan_opis
    assert z.ostatnie_wypchniecie < teraz - timedelta(days=2)


@pytest.mark.django_db
def test_stare_wpisy_bez_numeru_konta_rozpoznawane_po_loginie(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    w_student = Wypchniecie(autor="JanK", autor_id=None)
    w_obcy = Wypchniecie(autor="witoldprzygoda", autor_id=None)
    w_nieznany = Wypchniecie(autor="", autor_id=None)
    assert uslugi.od_studenta(w_student, z.student)
    assert not uslugi.od_studenta(w_obcy, z.student)
    assert uslugi.od_studenta(w_nieznany, z.student)
    # numer konta ma pierwszenstwo przed loginem (student mogl zmienic login)
    assert uslugi.od_studenta(Wypchniecie(autor="stary-login", autor_id=z.student.github_id), z.student)


@pytest.mark.django_db
def test_synchronizacja_zapisuje_numer_konta(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    github.aktywnosci[z.repo_nazwa] = [
        {"activity_type": "push", "ref": "refs/heads/main", "before": "c" * 40, "after": "d" * 40,
         "timestamp": "2026-10-21T10:00:00Z", "actor": {"login": "jank", "id": 77}},
    ]
    uslugi.synchronizuj(z)
    assert Wypchniecie.objects.get(repo_id=z.repo_id).autor_id == 77


@pytest.mark.django_db
def test_poprawka_testu_przez_prowadzacego_nie_daje_znacznika(github, przydzial):
    z = _zgloszenie_gotowe(przydzial, github)
    teraz = timezone.now()
    # prowadzacy poprawia test w repozytorium studenta, student pobiera to i dopisuje kod
    _wypchnij(z, "c", "d", 3)
    Wypchniecie.objects.filter(sha_po="d" * 40).update(autor="witoldprzygoda", autor_id=38453366)
    Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main", sha_przed="d" * 40,
                               sha_po="e" * 40, czas=teraz - timedelta(hours=1), autor="jank", autor_id=77,
                               zrodlo="aktywnosc")
    baza = {"ZADANIE1/main.py": "1", "ZADANIE1/tests/test_main.py": "2"}
    github.drzewa = {
        "b" * 40: baza, "c" * 40: baza,
        "d" * 40: {**baza, "ZADANIE1/tests/test_main.py": "P"},
        "e" * 40: {**baza, "ZADANIE1/main.py": "9", "ZADANIE1/tests/test_main.py": "P"},
    }
    github.porownania = {
        ("c" * 40, "d" * 40): ["ZADANIE1/tests/test_main.py"],
        ("b" * 40, "e" * 40): ["ZADANIE1/main.py", "ZADANIE1/tests/test_main.py"],
    }
    uslugi.przelicz(z)
    z.refresh_from_db()
    assert not z.testy_zmienione, z.zmiany_testow

    # a zmiana testu przez samego studenta nadal jest znakowana
    Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main", sha_przed="e" * 40,
                               sha_po="f" * 40, czas=teraz - timedelta(minutes=5), autor="jank", autor_id=77,
                               zrodlo="aktywnosc")
    github.drzewa["f" * 40] = {**baza, "ZADANIE1/main.py": "9", "ZADANIE1/tests/test_main.py": "S"}
    github.porownania[("b" * 40, "f" * 40)] = ["ZADANIE1/main.py", "ZADANIE1/tests/test_main.py"]
    uslugi.przelicz(z)
    z.refresh_from_db()
    assert z.testy_zmienione and z.zmiany_testow.get("zmienione") == ["ZADANIE1/tests/test_main.py"]


@pytest.mark.django_db
def test_ustawienie_actions_przekazywane_do_migawki(github, przydzial):
    przydzial.repo_startowe, przydzial.sha_startowy = "", ""
    przydzial.save()
    uslugi.utworz_repo_startowe(przydzial)
    assert github.z_actions is False
    zestaw = przydzial.zestaw
    zestaw.kopiuj_actions = True
    zestaw.save()
    przydzial.repo_startowe, przydzial.sha_startowy = "", ""
    przydzial.save()
    uslugi.utworz_repo_startowe(przydzial)
    assert github.z_actions is True


@pytest.mark.django_db
def test_przedrostek_uzyty_dokladnie_jak_wpisany(github, przydzial):
    przydzial.przedrostek_repo = "set0-"
    przydzial.save()
    assert uslugi.nazwa_repo_studenta(przydzial, "PythonUniversity") == "set0-pythonuniversity"
    przydzial.przedrostek_repo = "set0_"
    przydzial.save()
    assert uslugi.nazwa_repo_studenta(przydzial, "PythonUniversity") == "set0_pythonuniversity"


# --- Prowadzacy zmienia plik w repozytorium studenta ---

STUDENT, PROWADZACY = 77, 38453366


def _porownania_z_drzew(github, monkeypatch):
    """Porownanie jak w prawdziwym kliencie: roznica drzew dwoch stanow."""
    def porownaj(pelna, od, do):
        a, b = github.drzewa[od], github.drzewa[do]
        return sorted(s for s in set(a) | set(b) if a.get(s) != b.get(s))

    monkeypatch.setattr(uslugi.gh, "porownaj", porownaj)


def _push(z, przed, po, godzin_temu, autor_id):
    Wypchniecie.objects.create(
        repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main", sha_przed=przed * 40, sha_po=po * 40,
        czas=timezone.now() - timedelta(hours=godzin_temu),
        autor="jank" if autor_id == STUDENT else "witoldprzygoda", autor_id=autor_id, zrodlo="webhook")


def _przed_terminem(przydzial):
    teraz = timezone.now()
    przydzial.termin_miekki, przydzial.termin_twardy = teraz + timedelta(days=2), teraz + timedelta(days=9)
    przydzial.save()


BAZA0 = {"ZADANIE1/zadanie1.py": "1", "ZADANIE1/tests/test_zadanie1.py": "2", "ZADANIE2/zadanie2.py": "3",
         "README.md": "4"}


@pytest.mark.django_db
def test_prowadzacy_poprawil_plik_studenta_praca_nadal_oddana(github, przydzial, monkeypatch):
    """Student zmienil zadanie1.py, potem prowadzacy (swoim kontem) poprawil ten sam plik."""
    z = _zgloszenie_gotowe(przydzial, github)
    _przed_terminem(przydzial)
    _porownania_z_drzew(github, monkeypatch)
    github.drzewa = {"b" * 40: BAZA0, "c" * 40: BAZA0,
                     "d" * 40: {**BAZA0, "ZADANIE1/zadanie1.py": "S"},
                     "e" * 40: {**BAZA0, "ZADANIE1/zadanie1.py": "P"}}
    _push(z, "c", "d", 48, STUDENT)
    _push(z, "d", "e", 2, PROWADZACY)
    uslugi.przelicz(z)
    z.refresh_from_db()
    assert z.stan_kolor == "niebieski", z.stan_opis
    assert z.stan_pracy.klucz == "w_terminie"
    assert "Pominięto wypchnięcia z innych kont" in z.stan_opis


@pytest.mark.django_db
def test_poprawka_prowadzacego_w_nietknietym_pliku_to_nie_praca_studenta(github, przydzial, monkeypatch):
    """Prowadzacy poprawil plik, ktorego student nie ruszal: to nadal brak pracy studenta."""
    z = _zgloszenie_gotowe(przydzial, github)
    _przed_terminem(przydzial)
    _porownania_z_drzew(github, monkeypatch)
    github.drzewa = {"b" * 40: BAZA0, "c" * 40: BAZA0, "d" * 40: {**BAZA0, "ZADANIE2/zadanie2.py": "P"}}
    _push(z, "c", "d", 2, PROWADZACY)
    uslugi.przelicz(z)
    z.refresh_from_db()
    assert z.stan_pracy.klucz == "otwarte", z.stan_opis


@pytest.mark.django_db
def test_student_zmienia_inny_plik_po_poprawce_prowadzacego(github, przydzial, monkeypatch):
    """Poprawka prowadzacego w zadanie2.py nie jest praca studenta, ale jego zmiana w zadanie1.py jest."""
    z = _zgloszenie_gotowe(przydzial, github)
    _przed_terminem(przydzial)
    _porownania_z_drzew(github, monkeypatch)
    poprawka = {**BAZA0, "ZADANIE2/zadanie2.py": "P"}
    github.drzewa = {"b" * 40: BAZA0, "c" * 40: BAZA0, "d" * 40: poprawka,
                     "e" * 40: {**poprawka, "ZADANIE1/zadanie1.py": "S"}}
    _push(z, "c", "d", 5, PROWADZACY)
    _push(z, "d", "e", 2, STUDENT)
    uslugi.przelicz(z)
    z.refresh_from_db()
    assert z.stan_pracy.klucz == "w_terminie", z.stan_opis


@pytest.mark.django_db
def test_polecenie_diagnoza(github, przydzial, monkeypatch):
    from io import StringIO

    from django.core.management import call_command
    z = _zgloszenie_gotowe(przydzial, github)
    _porownania_z_drzew(github, monkeypatch)
    github.drzewa = {"c" * 40: BAZA0, "d" * 40: {**BAZA0, "README.md": "R"}}
    _push(z, "c", "d", 1, STUDENT)
    github.aktywnosci[z.repo_nazwa] = [
        {"timestamp": "2026-10-31T00:05:00Z", "ref": "refs/heads/main", "activity_type": "push",
         "before": "c" * 40, "after": "d" * 40, "actor": {"login": "jank", "id": STUDENT}},
        {"timestamp": "2026-10-31T00:06:00Z", "ref": "refs/heads/proba", "activity_type": "branch_creation",
         "before": "0" * 40, "after": "e" * 40, "actor": {"login": "jank", "id": STUDENT}},
    ]
    wyjscie = StringIO()
    call_command("diagnoza", "jank", stdout=wyjscie)
    tekst = wyjscie.getvalue()
    assert "Adres w aplikacji GitHub: http://testserver/github/webhook/" in tekst and "UWAGA" not in tekst
    assert "Zdarzenia aplikacji: push" in tekst
    assert "push" in tekst and "odpowiedź 200" in tekst
    assert "zmiany: README.md [bez kodu]" in tekst and "(id 77, student)" in tekst
    assert "Dostęp na GitHubie: uprawnienie write, zaproszenie oczekuje: nie" in tekst and "UWAGA" not in tekst
    monkeypatch.setattr(uslugi.gh, "dostep_studenta", lambda pelna, login: ("none", True))
    wyjscie = StringIO()
    call_command("diagnoza", "jank", stdout=wyjscie)
    assert "student nie przyjął zaproszenia" in wyjscie.getvalue()
    monkeypatch.setattr(uslugi.gh, "zdarzenia_aplikacji", lambda: [])
    wyjscie = StringIO()
    call_command("diagnoza", stdout=wyjscie)
    assert "Zdarzenia aplikacji: (brak)  UWAGA: GitHub nie wysyła powiadomień" in wyjscie.getvalue()
    assert "w bazie: tak" in tekst and "proba" in tekst and "w bazie: NIE" in tekst
