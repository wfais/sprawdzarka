"""Poligon: dane fikcyjne bez GitHuba, ich obsluga w panelu i usuwanie w calosci."""
from datetime import timedelta

import pytest
from django.utils import timezone

from kurs import ocena, poligon, uslugi
from kurs.github import git as git_modul
from kurs.github import klient as klient_modul
from kurs.models import Ewaluacja, Grupa, Kurs, Przydzial, Rok, Student, Wypchniecie, Zgloszenie
from kurs.testy.conftest import FUNKCJE_GIT, FUNKCJE_KLIENTA
from kurs.widoki_panel import _dolacz_liczniki


@pytest.fixture
def bez_githuba(monkeypatch):
    """Kazde siegniecie do GitHuba konczy test bledem."""
    def zakaz(*args, **kwargs):
        raise AssertionError("poligon nie moze laczyc sie z GitHubem")
    for nazwa in FUNKCJE_KLIENTA:
        monkeypatch.setattr(klient_modul, nazwa, zakaz)
    for nazwa in FUNKCJE_GIT:
        monkeypatch.setattr(git_modul, nazwa, zakaz)


@pytest.fixture
def rok_poligonu(db, bez_githuba):
    return poligon.utworz()


def _stany():
    przydzialy = _dolacz_liczniki(Przydzial.objects.filter(zestaw__kurs__rok__poligon=True).select_related("zestaw__kurs"))
    zestawy = {(p.zestaw.kurs.skrot, p.zestaw.skrot): p.stan.klucz for p in przydzialy}
    prace = {s.klucz for p in przydzialy for s, _ in p.paski}
    return zestawy, prace


def test_struktura_i_wszystkie_stany(rok_poligonu):
    assert rok_poligonu.poligon and rok_poligonu.uczelnia.skrot == "uj" and rok_poligonu.nazwa == "2026/2027 poligon"
    assert set(rok_poligonu.kursy.values_list("skrot", flat=True)) == {"py", "cpp"}
    assert set(Grupa.objects.filter(kurs__skrot="py", kurs__rok__poligon=True).values_list("nazwa", flat=True)) == {
        "Grupa nr 3", "Grupa nr 4", "Grupa nr 6"}
    assert set(Grupa.objects.filter(kurs__skrot="cpp").values_list("nazwa", flat=True)) == {"Grupa nr 1", "Grupa nr 8"}
    zestawy, prace = _stany()
    assert zestawy == {("py", "k1"): "zakonczony", ("py", "z01"): "po_terminie", ("py", "z02"): "trwa",
                       ("py", "z03"): "nieopublikowany", ("cpp", "z01"): "zalegle", ("cpp", "z02"): "do_oceny"}
    assert prace >= {"otwarte", "w_terminie", "przeterminowane", "po_terminie", "nie_oddano", "blad"}
    assert Zgloszenie.objects.filter(testy_zmienione=True).exists()
    assert Zgloszenie.objects.exclude(wyjatek_termin_miekki=None).exists()
    assert Ewaluacja.objects.filter(sporna=True).exists() and Zgloszenie.objects.filter(pr_zamkniety=True).exists()
    assert Student.objects.filter(imie_nazwisko="").exclude(kandydat_readme="").exists()
    assert not Student.objects.filter(github_id__gte=0).exists()
    with pytest.raises(poligon.BladPoligonu):
        poligon.utworz()


def test_readme_po_terminie_nie_jest_spoznieniem(rok_poligonu):
    for z in Zgloszenie.objects.filter(przydzial__zestaw__kurs__rok__poligon=True, status="gotowe"):
        ostatni = Wypchniecie.objects.filter(repo_id=z.repo_id).order_by("-czas").first()
        if ostatni and ostatni.czas > z.termin_miekki and z.stan_kolor == "zielony":
            return  # jest przypadek: tylko README po terminie, praca w terminie
    pytest.fail("brak przypadku README po terminie")


def test_panel_dziala_bez_githuba(client, prowadzacy, rok_poligonu):
    adresy = ["/panel/", "/panel/struktura/", "/panel/studenci/"]
    adresy.append(f"/panel/lata/{rok_poligonu.pk}/")
    adresy += [f"/panel/kursy/{k.pk}/" for k in rok_poligonu.kursy.all()]
    for g in Grupa.objects.filter(kurs__rok__poligon=True):
        adresy.append(f"/panel/grupy/{g.pk}/")
    for p in Przydzial.objects.filter(zestaw__kurs__rok__poligon=True):
        adresy += [f"/panel/przydzialy/{p.pk}/", f"/panel/przydzialy/{p.pk}/kopiuj/"]
    adresy += [f"/panel/zgloszenia/{z.pk}/" for z in Zgloszenie.objects.all()[:40]]
    for adres in adresy:
        assert client.get(adres).status_code == 200, adres
    assert "poligon: dane fikcyjne" in client.get("/panel/").content.decode()


def test_ocenianie_i_odswiezanie_w_poligonie(client, prowadzacy, rok_poligonu):
    p = Przydzial.objects.get(zestaw__kurs__skrot="cpp", zestaw__skrot="z02", grupa__skrot="g1")
    z = p.zgloszenia.filter(status="gotowe", pr_zamkniety=False).exclude(sha_oceniany="").first()
    odp = client.post(f"/panel/zgloszenia/{z.pk}/ocena/", {"punkty": "4,5", "mnoznik": "1", "komentarz": "OK"})
    assert odp.status_code == 302
    z.refresh_from_db()
    assert z.pr_zamkniety and "Ocena: 4,5" in z.tresc_oceny_wyslana
    # Ocena automatyczna od razu gotowa (symulacja)
    drugi = p.zgloszenia.filter(status="gotowe", pr_zamkniety=False).exclude(sha_oceniany="").first()
    drugi.ewaluacje.all().delete()
    client.post(f"/panel/zgloszenia/{drugi.pk}/ocen/")
    assert drugi.ewaluacje.get().status == Ewaluacja.Status.GOTOWA
    # Odswiezenie i zmiana terminow liczone z bazy
    assert client.post(f"/panel/przydzialy/{p.pk}/odswiez/").status_code == 302
    teraz = timezone.now()
    odp = client.post(f"/panel/przydzialy/{p.pk}/edycja/", {
        "termin_miekki": timezone.localtime(teraz + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M"),
        "termin_twardy": timezone.localtime(teraz + timedelta(days=37)).strftime("%Y-%m-%dT%H:%M"),
        "otwarty": "on"})
    assert odp.status_code == 302
    kolory = set(p.zgloszenia.filter(status="gotowe").values_list("stan_kolor", flat=True))
    assert kolory <= {"szary", "niebieski"}
    # Zaproszenie wymaga GitHuba: komunikat zamiast bledu
    odp = client.post(f"/panel/zgloszenia/{drugi.pk}/zaproszenie/", follow=True)
    assert "dane fikcyjne z poligonu" in odp.content.decode()


def test_blad_tworzenia_mozna_ponowic(client, prowadzacy, rok_poligonu):
    z = Zgloszenie.objects.get(status="blad")
    client.post(f"/panel/zgloszenia/{z.pk}/ponow/")
    z.refresh_from_db()
    assert z.status == "gotowe" and z.repo_id < 0


def test_robot_pomija_poligon(rok_poligonu, monkeypatch):
    Zgloszenie.objects.update(do_przeliczenia=True)
    uslugi.cykl_robota(pelny=True)
    uslugi.cykl_robota(pelny=False)
    assert ocena.zakolejkuj_po_terminach() == 0


def test_link_studenta_w_poligonie(client, rok_poligonu):
    p = Przydzial.objects.filter(zestaw__kurs__rok__poligon=True, publikacja__lt=timezone.now()).first()
    strona = client.get(f"/z/{p.token}/").content.decode()
    assert "poligonu" in strona and "Akceptuj" not in strona


def test_usuniecie_grupy_wymaga_nazwy(client, prowadzacy, rok_poligonu):
    g = Grupa.objects.get(kurs__skrot="py", kurs__rok__poligon=True, skrot="g4")
    client.post(f"/panel/grupy/{g.pk}/usun/", {"potwierdzenie": "g4"})
    assert Grupa.objects.filter(pk=g.pk).exists()
    odp = client.post(f"/panel/grupy/{g.pk}/usun/", {"potwierdzenie": "Grupa nr 4"})
    assert odp.status_code == 302 and not Grupa.objects.filter(pk=g.pk).exists()
    assert not Zgloszenie.objects.filter(przydzial__grupa_id=g.pk).exists()
    assert Grupa.objects.filter(kurs__rok__poligon=True).count() == 4


def test_usuniecie_poligonu_zostawia_prawdziwe_dane(client, prowadzacy, przydzial, bez_githuba):
    prawdziwy = Zgloszenie.objects.create(przydzial=przydzial, student=Student.objects.create(github_id=77, login="jan"),
                                          status="gotowe", repo_id=555, repo_nazwa="wfais/py-z03-g4-jan")
    Wypchniecie.objects.create(repo_id=555, repo_nazwa=prawdziwy.repo_nazwa, ref="refs/heads/main", sha_przed="a" * 40,
                               sha_po="b" * 40, czas=timezone.now(), zrodlo="webhook")
    client.post("/panel/poligon/utworz/")
    assert Rok.objects.filter(poligon=True).count() == 1 and Kurs.objects.filter(rok__poligon=True).count() == 2
    strona = client.get("/panel/poligon/usun/").content.decode()
    assert "Język C++: grupy 2" in strona
    client.post("/panel/poligon/usun/", {"potwierdzenie": "zle"})
    assert Rok.objects.filter(poligon=True).count() == 1
    client.post("/panel/poligon/usun/", {"potwierdzenie": "poligon"})
    assert not Rok.objects.filter(poligon=True).exists() and not Kurs.objects.filter(skrot="cpp").exists()
    assert list(Kurs.objects.values_list("skrot", flat=True)) == ["py"]  # prawdziwy przedmiot zostaje
    assert list(Student.objects.values_list("github_id", flat=True)) == [77]
    assert Zgloszenie.objects.get() == prawdziwy and Wypchniecie.objects.filter(repo_id=555).exists()
    assert not Wypchniecie.objects.filter(repo_id__lt=0).exists()


def test_polecenie_zarzadzania(db, bez_githuba):
    from django.core.management import call_command
    call_command("poligon")
    assert Rok.objects.filter(poligon=True).count() == 1 and Kurs.objects.count() == 2
    call_command("poligon", "--usun")
    assert not Rok.objects.filter(poligon=True).exists() and not Student.objects.exists()
