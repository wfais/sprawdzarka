"""Poprawki prowadzacego przenoszone do repozytoriow studentow."""
from datetime import timedelta

import pytest
from django.utils import timezone

from kurs import poprawki, uslugi, uruchamianie
from kurs.github.git import WynikScalenia
from kurs.models import Poprawka, PoprawkaZgloszenia, Wypchniecie, Zgloszenie


def _studenci(przydzial):
    z1 = uslugi.akceptuj(przydzial, 201, "ewa", "Ewa Lis")
    z2 = uslugi.akceptuj(przydzial, 202, "jan", "Jan Nowak")
    z3 = uslugi.akceptuj(przydzial, 203, "ola", "Ola Kot")
    Zgloszenie.objects.filter(pk=z1.pk).update(pr_numer=1)
    Zgloszenie.objects.filter(pk=z3.pk).update(pr_zamkniety=True)
    return [Zgloszenie.objects.get(pk=z.pk) for z in (z1, z2, z3)]


@pytest.mark.django_db
def test_podglad_pomija_actions(github, przydzial):
    github.drzewa = {
        przydzial.sha_startowy: {"README.md": "1", "ZADANIE1/main.py": "2"},
        "zrodlo-wfais/python-zestaw3": {"README.md": "1", "ZADANIE1/main.py": "3", "ZADANIE1/tests/test_n.py": "4",
                                        ".github/workflows/classroom.yml": "5"},
    }
    assert poprawki.podglad(przydzial) == [
        {"sciezka": "ZADANIE1/main.py", "rodzaj": "zmieniony"},
        {"sciezka": "ZADANIE1/tests/test_n.py", "rodzaj": "dodany"},
    ]


@pytest.mark.django_db
def test_wprowadzenie_poprawki(github, przydzial):
    ewa, jan, ola = _studenci(przydzial)
    stary_start = przydzial.sha_startowy
    github.wyniki_scalenia[jan.repo_nazwa] = WynikScalenia(False, konflikty=["ZADANIE1/main.py"])
    poprawka = poprawki.zlec(przydzial, "Poprawiony test", True, [{"sciezka": "x", "rodzaj": "zmieniony"}])
    assert poprawka.numer == 1 and poprawki.przetworz() == 1
    poprawka.refresh_from_db()
    przydzial.refresh_from_db()
    assert poprawka.stan == Poprawka.Stan.GOTOWA and poprawka.sha_po == "e" * 40
    assert przydzial.sha_startowy == "e" * 40 and przydzial.historia_startowa == [stary_start, "e" * 40]
    stany = {w.zgloszenie_id: w for w in PoprawkaZgloszenia.objects.all()}
    assert stany[ewa.pk].stan == PoprawkaZgloszenia.Stan.SCALONA
    assert stany[jan.pk].stan == PoprawkaZgloszenia.Stan.DO_SCALENIA and stany[jan.pk].konflikty == ["ZADANIE1/main.py"]
    assert stany[ola.pk].stan == PoprawkaZgloszenia.Stan.POMINIETA
    # Student z konfliktem dostaje pull request z instrukcja, scalony automatycznie: komentarz w Feedback
    head, base, tytul = github.pull_requesty[jan.repo_nazwa]
    assert (head, base, tytul) == ("poprawka-1", "main", "Poprawka prowadzacego nr 1")
    assert any(r == ewa.repo_nazwa and "git pull" in t for r, _, t in github.komentarze)
    for z in (ewa, jan):
        z.refresh_from_db()
        assert z.sha_bazowy == "e" * 40 and z.do_przeliczenia
    ola.refresh_from_db()
    assert ola.sha_bazowy != "e" * 40
    # Ponowne przetworzenie nic nie dubluje
    assert poprawki.przetworz() == 0 and len(github.scalenia) == 2


@pytest.mark.django_db
def test_bez_zmian_w_zrodle(github, przydzial):
    _studenci(przydzial)
    github.nowy_start = None
    poprawka = poprawki.zlec(przydzial, "", True, [])
    poprawki.przetworz()
    poprawka.refresh_from_db()
    assert poprawka.stan == Poprawka.Stan.BEZ_ZMIAN and not github.scalenia


@pytest.mark.django_db
def test_bez_autoscalania_kazdy_dostaje_pull_request(github, przydzial):
    ewa, jan, _ = _studenci(przydzial)
    poprawki.zlec(przydzial, "", False, [])
    poprawki.przetworz()
    assert set(github.pull_requesty) >= {ewa.repo_nazwa, jan.repo_nazwa}
    assert PoprawkaZgloszenia.objects.filter(stan=PoprawkaZgloszenia.Stan.DO_SCALENIA).count() == 2


@pytest.mark.django_db
def test_sprawdzenie_scalen(github, przydzial):
    _, jan, _ = _studenci(przydzial)
    github.wyniki_scalenia[jan.repo_nazwa] = WynikScalenia(False, konflikty=["a.py"])
    poprawka = poprawki.zlec(przydzial, "", True, [])
    poprawki.przetworz()
    assert poprawki.sprawdz_scalenia() == 0
    github.zawiera[jan.repo_nazwa] = True
    assert poprawki.sprawdz_scalenia(poprawka) == 1
    assert PoprawkaZgloszenia.objects.get(zgloszenie=jan).stan == PoprawkaZgloszenia.Stan.SCALONA_PRZEZ_STUDENTA


@pytest.mark.django_db
def test_szkielet_sprzed_poprawki_nie_jest_zmiana_studenta(github, przydzial):
    """Student nie scalil poprawki: jego stary szkielet i stare testy nie sa jego zmianami."""
    z = uslugi.akceptuj(przydzial, 301, "adam", "Adam Mak")
    przydzial.historia_startowa = ["b" * 40, "e" * 40]
    przydzial.sha_startowy = "e" * 40
    przydzial.save()
    Zgloszenie.objects.filter(pk=z.pk).update(sha_bazowy="e" * 40)
    z.refresh_from_db()
    stary = {"ZADANIE1/main.py": "1", "ZADANIE1/tests/test_main.py": "2"}
    github.drzewa = {"b" * 40: stary, "c" * 40: stary, "e" * 40: {"ZADANIE1/main.py": "9", "ZADANIE1/tests/test_main.py": "8"}}
    github.porownania = {("e" * 40, "c" * 40): ["ZADANIE1/main.py", "ZADANIE1/tests/test_main.py"]}
    Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main", sha_przed="0" * 40,
                               sha_po="c" * 40, czas=timezone.now() - timedelta(days=1), autor="sprawdzarka-wfais[bot]",
                               zrodlo="aktywnosc")
    uslugi.przelicz(z)
    z.refresh_from_db()
    assert z.stan_kolor == Zgloszenie.Kolor.SZARY, z.stan_opis
    assert not z.testy_zmienione, z.zmiany_testow


def test_stare_wersje_testow_nie_sa_zmiana_studenta(tmp_path):
    baza = tmp_path / "baza"
    kod = tmp_path / "kod"
    for katalog, tresc in ((baza, "nowy test\n"), (kod, "stary test\n")):
        (katalog / "ZADANIE1" / "tests").mkdir(parents=True)
        (katalog / "ZADANIE1" / "tests" / "test_main.py").write_text(tresc)
    dozwolone = {"ZADANIE1/tests/test_main.py": {uruchamianie.skrot_git(b"stary test\n")}}
    assert uruchamianie.zmiany_plikow_prowadzacego(baza, kod)["zmienione"] == ["ZADANIE1/tests/test_main.py"]
    assert uruchamianie.zmiany_plikow_prowadzacego(baza, kod, dozwolone)["zmienione"] == []
    # Skrot zgodny z git: "git hash-object" dla pustego pliku
    assert uruchamianie.skrot_git(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


@pytest.mark.django_db
def test_strony_poprawki(client, github, prowadzacy, przydzial):
    ewa, jan, _ = _studenci(przydzial)
    github.drzewa = {przydzial.sha_startowy: {"README.md": "1"}, "zrodlo-wfais/python-zestaw3": {"README.md": "2"}}
    github.bloby = {"1": "# Zestaw\nstara linia\n".encode(), "2": "# Zestaw\nnowa linia\n".encode()}
    strona = client.get(f"/panel/przydzialy/{przydzial.pk}/poprawka/")
    tresc = strona.content.decode()
    assert strona.status_code == 200 and "README.md" in tresc and "nic nie zostało jeszcze zmienione" in tresc
    assert '<span class="usuniety">-stara linia</span>' in tresc and '<span class="dodany">+nowa linia</span>' in tresc
    assert not Poprawka.objects.exists()  # samo otwarcie niczego nie zmienia
    odp = client.post(f"/panel/przydzialy/{przydzial.pk}/poprawka/", {"opis": "Nowy opis", "autoscalanie": "1"})
    poprawka = Poprawka.objects.get()
    assert odp.status_code == 302 and odp["Location"].endswith(f"/panel/poprawki/{poprawka.pk}/")
    assert client.get(f"/panel/poprawki/{poprawka.pk}/").status_code == 200
    poprawki.przetworz()
    karta = client.get(f"/panel/poprawki/{poprawka.pk}/").content.decode()
    assert "scalona automatycznie" in karta and "Ewa Lis" in karta
    assert "Poprawka ze źródła" in client.get(f"/panel/przydzialy/{przydzial.pk}/").content.decode()


@pytest.mark.django_db
def test_strona_studenta_informuje_o_poprawce(client, github, przydzial):
    from kurs.testy.test_widoki import zaloguj_studenta
    z = uslugi.akceptuj(przydzial, 401, "kasia", "Kasia Kowal")
    github.wyniki_scalenia[z.repo_nazwa] = WynikScalenia(False, konflikty=["ZADANIE1/main.py"])
    poprawki.zlec(przydzial, "Poprawiony test", True, [])
    poprawki.przetworz()
    zaloguj_studenta(client, 401, "kasia")
    strona = client.get(f"/z/{przydzial.token}/").content.decode()
    assert "Poprawka prowadzącego nr 1" in strona and "/pull/" in strona


@pytest.mark.django_db
def test_podglad_bez_zmian_niczego_nie_zleca(client, github, prowadzacy, przydzial):
    github.drzewa = {przydzial.sha_startowy: {"README.md": "1"}, "zrodlo-wfais/python-zestaw3": {"README.md": "1"}}
    tresc = client.get(f"/panel/przydzialy/{przydzial.pk}/poprawka/").content.decode()
    assert "Brak zmian" in tresc and "nic nie zostało zmienione" in tresc and "Zatwierdzam" not in tresc
    assert not Poprawka.objects.exists()
