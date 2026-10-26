"""Uczelnie, lata akademickie, przedmioty: wydawanie, kopiowanie zestawow i przedmiotow, ukrywanie, archiwum."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from kurs import archiwum, uslugi
from kurs.forms import FormularzGrupy, FormularzPrzydzialu
from kurs.models import (
    Ewaluacja, Grupa, Kurs, OcenaZadania, Precedens, Przydzial, Rok, Student, Wypchniecie, Zadanie, Zestaw,
    Zgloszenie, skrot_roku,
)

D = Decimal


def _inny_rok(rok, nazwa="2027/2028"):
    return Rok.objects.create(uczelnia=rok.uczelnia, nazwa=nazwa)


def _inny_kurs(kurs, nazwa_roku="2027/2028"):
    """Ten sam przedmiot w innym roku akademickim."""
    return Kurs.objects.create(rok=_inny_rok(kurs.rok, nazwa_roku), nazwa=kurs.nazwa, skrot=kurs.skrot)


def test_skrot_roku():
    assert skrot_roku("2026/2027") == "2627"
    assert skrot_roku("Lato 2027") == "lato-2027"


@pytest.mark.django_db
def test_domyslny_przedrostek(zestaw, grupa):
    assert Przydzial.domyslny_przedrostek(zestaw, grupa) == "uj-py2627-z03-g4-"


@pytest.mark.django_db
def test_uczelnie_z_migracji():
    from kurs.models import Uczelnia
    assert set(Uczelnia.objects.values_list("skrot", flat=True)) >= {"uj", "ans"}


@pytest.mark.django_db
def test_skrot_grupy_unikalny_w_przedmiocie(kurs, grupa):
    assert not FormularzGrupy(data={"nazwa": "Inna", "skrot": "g4", "aktywna": "on"}, kurs=kurs).is_valid()
    drugi = _inny_kurs(kurs)
    formularz = FormularzGrupy(data={"nazwa": "Grupa 4", "skrot": "g4", "aktywna": "on"}, kurs=drugi)
    assert formularz.is_valid(), formularz.errors
    assert formularz.save().kurs == drugi


@pytest.mark.django_db
def test_wydanie_ze_strony_grupy(client, github, prowadzacy, grupa, zestaw):
    kolokwium = Zestaw.objects.create(kurs=zestaw.kurs, rodzaj=Zestaw.Rodzaj.KOLOKWIUM, nazwa="Kolokwium 1",
                                      skrot="k1", repo_zrodlowe="wfais/py-k1")
    strona = client.get(f"/panel/grupy/{grupa.pk}/").content.decode()
    assert "Kolokwium 1" in strona and "Zestaw 3" in strona
    odp = client.post(f"/panel/grupy/{grupa.pk}/", {
        "zestaw": kolokwium.pk, "termin_miekki": "2026-11-10T10:00", "termin_twardy": "2026-11-10T11:30",
        "przedrostek_repo": "", "otwarty": "on",
    })
    assert odp.status_code == 302
    p = Przydzial.objects.get(zestaw=kolokwium)
    assert p.grupa == grupa and p.przedrostek_repo == "uj-py2627-k1-g4-"
    assert github.migawki == [("wfais/py-k1", "wfais/uj-py2627-k1-g4-start")]
    strona = client.get(f"/panel/grupy/{grupa.pk}/").content.decode()
    assert "kolokwium" in strona


@pytest.mark.django_db
def test_zestaw_z_innego_przedmiotu_niedostepny(kurs, grupa):
    obcy = Zestaw.objects.create(kurs=_inny_kurs(kurs), nazwa="Obcy", skrot="z09", repo_zrodlowe="wfais/x")
    formularz = FormularzPrzydzialu(data={
        "zestaw": obcy.pk, "termin_miekki": "2026-11-01T10:00", "termin_twardy": "2026-11-08T10:00",
    }, grupa=grupa)
    assert not formularz.is_valid()
    assert "zestaw" in formularz.errors


@pytest.mark.django_db
def test_kopiuj_do_innych_grup(client, github, prowadzacy, przydzial):
    kurs = przydzial.zestaw.kurs
    g5 = Grupa.objects.create(kurs=kurs, nazwa="Grupa 5", skrot="g5")
    g6 = Grupa.objects.create(kurs=kurs, nazwa="Grupa 6", skrot="g6")
    Grupa.objects.create(kurs=_inny_kurs(kurs), nazwa="Grupa 7", skrot="g7")
    strona = client.get(f"/panel/przydzialy/{przydzial.pk}/kopiuj/").content.decode()
    assert "Grupa 5" in strona and "Grupa 6" in strona and "Grupa 7" not in strona
    assert "uj-py2627-z03-g5-" in strona
    odp = client.post(f"/panel/przydzialy/{przydzial.pk}/kopiuj/", {
        "form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "2",
        "form-0-grupa": g5.pk, "form-0-wybrana": "on", "form-0-termin_miekki": "2026-12-01T23:59",
        "form-0-termin_twardy": "2026-12-08T23:59", "form-0-przedrostek_repo": "uj-py2627-z03-g5-",
        "form-0-repo_zrodlowe": "https://github.com/wfais/python-zestaw3-b",
        "form-1-grupa": g6.pk, "form-1-termin_miekki": "2026-12-01T23:59",
        "form-1-termin_twardy": "2026-12-08T23:59", "form-1-przedrostek_repo": "uj-py2627-z03-g6-",
        "form-1-repo_zrodlowe": "",
    }, follow=True)
    assert odp.redirect_chain[0][0] == f"/panel/zestawy/{przydzial.zestaw_id}/"
    assert "Zestaw wydany grupom: Grupa 5." in odp.content.decode()
    nowy = Przydzial.objects.get(grupa=g5)
    assert nowy.zestaw == przydzial.zestaw and nowy.repo_zrodlowe == "wfais/python-zestaw3-b"
    assert timezone.localtime(nowy.termin_miekki).strftime("%d.%m %H:%M") == "01.12 23:59"
    assert nowy.repo_startowe == "wfais/uj-py2627-z03-g5-start"
    assert github.migawki == [("wfais/python-zestaw3-b", "wfais/uj-py2627-z03-g5-start")]
    assert not Przydzial.objects.filter(grupa=g6).exists()
    # Grupa z zestawem znika z listy do kopiowania
    assert "Grupa 5" not in client.get(f"/panel/przydzialy/{przydzial.pk}/kopiuj/").content.decode()


@pytest.mark.django_db
def test_kopiowanie_odrzuca_zajety_przedrostek(client, github, prowadzacy, przydzial):
    g5 = Grupa.objects.create(kurs=przydzial.zestaw.kurs, nazwa="Grupa 5", skrot="g5")
    odp = client.post(f"/panel/przydzialy/{przydzial.pk}/kopiuj/", {
        "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
        "form-0-grupa": g5.pk, "form-0-wybrana": "on", "form-0-termin_miekki": "2026-12-01T23:59",
        "form-0-termin_twardy": "2026-11-08T23:59", "form-0-przedrostek_repo": przydzial.przedrostek_repo,
    })
    assert odp.status_code == 200
    tresc = odp.content.decode()
    assert "Taki przedrostek jest już używany" in tresc and "nie może być wcześniejszy" in tresc
    assert Przydzial.objects.count() == 1


@pytest.mark.django_db
def test_kopiuj_zestaw_do_innego_roku(client, prowadzacy, zestaw):
    Zadanie.objects.create(zestaw=zestaw, kod="ZADANIE1", katalog="ZADANIE1/", punkty=D(2), kryteria="k1")
    Precedens.objects.create(zestaw=zestaw, tresc="Nie karz za brak docstringow")
    drugi = _inny_kurs(zestaw.kurs)
    odp = client.post(f"/panel/zestawy/{zestaw.pk}/kopiuj/", {"kurs": drugi.pk, "skrot": "", "nazwa": ""})
    assert odp.status_code == 302
    kopia = Zestaw.objects.get(kurs=drugi)
    assert (kopia.skrot, kopia.nazwa, kopia.repo_zrodlowe) == (zestaw.skrot, zestaw.nazwa, zestaw.repo_zrodlowe)
    assert list(kopia.zadania.values_list("kod", "punkty", "kryteria")) == [("ZADANIE1", D(2), "k1")]
    assert kopia.precedensy.count() == 1
    # Drugi raz ten sam skrot w tym roku: odrzucone
    odp = client.post(f"/panel/zestawy/{zestaw.pk}/kopiuj/", {"kurs": drugi.pk, "skrot": "", "nazwa": ""})
    assert odp.status_code == 200 and Zestaw.objects.filter(kurs=drugi).count() == 1


@pytest.mark.django_db
def test_ukryty_rok_znika_z_pulpitu(client, prowadzacy, przydzial):
    rok = przydzial.zestaw.kurs.rok
    assert "Grupa 4" in client.get("/panel/").content.decode()
    client.post(f"/panel/lata/{rok.pk}/ukryj/")
    rok.refresh_from_db()
    assert rok.ukryty
    assert "Grupa 4" not in client.get("/panel/").content.decode()
    assert "ukryty" in client.get("/panel/struktura/").content.decode()
    client.post(f"/panel/lata/{rok.pk}/ukryj/")
    assert "Grupa 4" in client.get("/panel/").content.decode()


@pytest.mark.django_db
def test_ukryty_przedmiot_znika_z_pulpitu(client, prowadzacy, przydzial):
    kurs = przydzial.zestaw.kurs
    inny = Kurs.objects.create(rok=kurs.rok, nazwa="Język C++", skrot="cpp")
    Grupa.objects.create(kurs=inny, nazwa="Grupa 8", skrot="g8")
    client.post(f"/panel/kursy/{kurs.pk}/ukryj/")
    kurs.refresh_from_db()
    assert kurs.ukryty
    pulpit = client.get("/panel/").content.decode()
    assert "Grupa 4" not in pulpit and "Grupa 8" in pulpit
    assert "Przedmioty ukryte" in pulpit
    client.post(f"/panel/kursy/{kurs.pk}/ukryj/")
    assert "Grupa 4" in client.get("/panel/").content.decode()


@pytest.mark.django_db
def test_archiwizacja_zamraza_rok(client, github, prowadzacy, przydzial, monkeypatch):
    z = uslugi.akceptuj(przydzial, 99, "ewa", "Ewa Lis")
    rok = przydzial.zestaw.kurs.rok
    client.post(f"/panel/lata/{rok.pk}/archiwizuj/")
    rok.refresh_from_db()
    przydzial.refresh_from_db()
    assert rok.zarchiwizowany and rok.ukryty and not przydzial.otwarty
    # Robot pomija zarchiwizowany rok
    przeliczone = []
    monkeypatch.setattr(uslugi, "przelicz", lambda zgl: przeliczone.append(zgl.pk))
    Zgloszenie.objects.filter(pk=z.pk).update(do_przeliczenia=True)
    uslugi.cykl_robota(pelny=False)
    assert przeliczone == []
    # Nie da sie wydac zestawu w zarchiwizowanym roku
    g5 = Grupa.objects.create(kurs=przydzial.zestaw.kurs, nazwa="Grupa 5", skrot="g5")
    client.post(f"/panel/zestawy/{przydzial.zestaw.pk}/", {
        "grupa": g5.pk, "termin_miekki": "2026-12-01T23:59", "termin_twardy": "2026-12-08T23:59",
    })
    assert not Przydzial.objects.filter(grupa=g5).exists()
    client.post(f"/panel/lata/{rok.pk}/przywroc/")
    rok.refresh_from_db()
    assert not rok.zarchiwizowany
    uslugi.cykl_robota(pelny=False)
    assert przeliczone == [z.pk]


def _rok_z_danymi(przydzial):
    z = uslugi.akceptuj(przydzial, 99, "ewa", "Ewa Lis")
    Zgloszenie.objects.filter(pk=z.pk).update(punkty=D("4.5"), mnoznik=D("0.8"), komentarz_koncowy="Dobrze")
    zadanie = Zadanie.objects.create(zestaw=przydzial.zestaw, kod="ZADANIE1", punkty=D(5))
    e = Ewaluacja.objects.create(zgloszenie=z, sha="d" * 40, status=Ewaluacja.Status.GOTOWA,
                                 testy={"ZADANIE1": {"zdane": 3, "wszystkie": 4}}, suma=D("4.5"))
    Ewaluacja.objects.filter(pk=e.pk).update(utworzono=timezone.now() - timedelta(days=30))
    OcenaZadania.objects.create(ewaluacja=e, zadanie=zadanie, kod="ZADANIE1", punkty_max=D(5), punkty_konsensus=D("4.5"))
    z.refresh_from_db()
    Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main", sha_przed="c" * 40,
                               sha_po="d" * 40, czas=timezone.now(), autor="ewa", autor_id=99, zrodlo="webhook")
    return z


@pytest.mark.django_db
def test_archiwum_usun_i_wczytaj(github, przydzial):
    z = _rok_z_danymi(przydzial)
    rok = przydzial.zestaw.kurs.rok
    Kurs.objects.create(rok=rok, nazwa="Język C++", skrot="cpp", kod="WFAIS.IF-A207.0")
    with pytest.raises(archiwum.BladArchiwum):
        archiwum.usun(rok)  # najpierw archiwizacja
    Rok.objects.filter(pk=rok.pk).update(zarchiwizowany=True)
    rok.refresh_from_db()
    plik = archiwum.do_pliku(rok)
    with pytest.raises(archiwum.BladArchiwum, match="już jest w bazie"):
        archiwum.wczytaj_plik(plik)
    archiwum.usun(rok)
    assert not Rok.objects.exists() and not Kurs.objects.exists()
    assert not Zgloszenie.objects.exists() and not Wypchniecie.objects.exists()
    assert Student.objects.count() == 1  # konto studenta zostaje

    nowy = archiwum.wczytaj_plik(plik)
    assert nowy.nazwa == "2026/2027" and nowy.zarchiwizowany and nowy.uczelnia.skrot == "uj"
    assert list(nowy.kursy.values_list("nazwa", "kod")) == [("Język C++", "WFAIS.IF-A207.0"), ("Język Python", "")]
    zgl = Zgloszenie.objects.get()
    assert zgl.przydzial.zestaw.kurs.rok == nowy and zgl.przydzial.grupa.kurs == zgl.przydzial.zestaw.kurs
    assert (zgl.punkty, zgl.mnoznik, zgl.komentarz_koncowy) == (D("4.5"), D("0.8"), "Dobrze")
    assert zgl.repo_id == z.repo_id and zgl.przydzial.token == przydzial.token
    assert Student.objects.count() == 1 and zgl.student.login == "ewa"
    e = zgl.ewaluacje.get()
    assert e.testy == {"ZADANIE1": {"zdane": 3, "wszystkie": 4}}
    assert e.utworzono < timezone.now() - timedelta(days=29)
    ocena = e.oceny_zadan.get()
    assert ocena.zadanie.zestaw == zgl.przydzial.zestaw
    assert Wypchniecie.objects.get().sha_po == "d" * 40


@pytest.mark.django_db
def test_wczytanie_archiwum_z_panelu(client, github, prowadzacy, przydzial):
    _rok_z_danymi(przydzial)
    rok = przydzial.zestaw.kurs.rok
    plik = client.get(f"/panel/lata/{rok.pk}/archiwum.json")
    assert plik["Content-Disposition"].endswith('archiwum-uj-2627.json"')
    tresc = b"".join(plik.streaming_content) if plik.streaming else plik.content
    client.post(f"/panel/lata/{rok.pk}/archiwizuj/")
    odp = client.post(f"/panel/lata/{rok.pk}/usun/", {"potwierdzenie": "zle"})
    assert Rok.objects.filter(pk=rok.pk).exists()
    odp = client.post(f"/panel/lata/{rok.pk}/usun/", {"potwierdzenie": "2026/2027"})
    assert odp["Location"] == "/panel/struktura/" and not Rok.objects.exists()
    odp = client.post("/panel/struktura/wczytaj/", {"plik": SimpleUploadedFile("a.json", tresc)})
    nowy = Rok.objects.get()
    assert odp["Location"] == f"/panel/lata/{nowy.pk}/"
    assert Zgloszenie.objects.count() == 1
    odp = client.post("/panel/struktura/wczytaj/", {"plik": SimpleUploadedFile("a.json", b"{zle")}, follow=True)
    assert "nie jest poprawnym plikiem JSON" in odp.content.decode()


@pytest.mark.django_db
def test_struktura_dodaje_uczelnie_rok_i_przedmiot(client, prowadzacy):
    client.post("/panel/struktura/", {"rodzaj": "uczelnia", "u-nazwa": "Politechnika", "u-skrot": "pk",
                                      "u-kolejnosc": "3"})
    from kurs.models import Uczelnia
    pk = Uczelnia.objects.get(skrot="pk")
    odp = client.post("/panel/struktura/", {"rodzaj": "rok", "r-uczelnia": pk.pk, "r-nazwa": "2026/2027"})
    rok = Rok.objects.get(uczelnia=pk)
    assert odp["Location"] == f"/panel/lata/{rok.pk}/" and rok.skrot == "2627"
    # Ten sam rok drugi raz: blad, nic nie powstaje
    odp = client.post("/panel/struktura/", {"rodzaj": "rok", "r-uczelnia": pk.pk, "r-nazwa": "2026/2027"})
    assert odp.status_code == 200 and Rok.objects.filter(uczelnia=pk).count() == 1
    odp = client.post("/panel/struktura/", {"rodzaj": "kurs", "k-rok": rok.pk, "k-nazwa": "Język C++", "k-skrot": "cpp",
                                            "k-kod": "WFAIS.IF-A207.0"})
    kurs = Kurs.objects.get(skrot="cpp")
    assert odp["Location"] == f"/panel/kursy/{kurs.pk}/" and kurs.rok == rok
    # Przedmiot na stronie roku (bez wyboru roku), skrot unikalny w roku
    odp = client.post(f"/panel/lata/{rok.pk}/", {"k-nazwa": "Język C", "k-skrot": "cpp"})
    assert odp.status_code == 200 and "już użyty w tym roku" in odp.content.decode()
    odp = client.post(f"/panel/lata/{rok.pk}/", {"k-nazwa": "Język C", "k-skrot": "c"})
    assert Kurs.objects.filter(rok=rok).count() == 2
    strona = client.get("/panel/struktura/").content.decode()
    assert "Język C++" in strona and "Język C<" in strona
    assert "Język C++" in client.get(f"/panel/lata/{rok.pk}/").content.decode()


@pytest.mark.django_db
def test_kopiuj_przedmiot_do_nowego_roku(client, prowadzacy, przydzial):
    zestaw = przydzial.zestaw
    kurs = zestaw.kurs
    Kurs.objects.filter(pk=kurs.pk).update(kod="WFAIS.IF-X204.0")
    Zadanie.objects.create(zestaw=zestaw, kod="ZADANIE1", katalog="ZADANIE1/", punkty=D(2), kryteria="k1")
    Zestaw.objects.create(kurs=kurs, rodzaj=Zestaw.Rodzaj.KOLOKWIUM, nazwa="Kolokwium 1", skrot="k1",
                          repo_zrodlowe="wfais/py-k1")
    nowy_rok = _inny_rok(kurs.rok)
    strona = client.get(f"/panel/lata/{nowy_rok.pk}/").content.decode()
    assert f"/panel/kursy/{kurs.pk}/kopiuj/?rok={nowy_rok.pk}" in strona
    assert client.get(f"/panel/kursy/{kurs.pk}/kopiuj/?rok={nowy_rok.pk}").status_code == 200
    odp = client.post(f"/panel/kursy/{kurs.pk}/kopiuj/", {"rok": nowy_rok.pk, "nazwa": "", "skrot": ""})
    kopia = Kurs.objects.get(rok=nowy_rok)
    assert odp["Location"] == f"/panel/kursy/{kopia.pk}/"
    assert (kopia.nazwa, kopia.skrot, kopia.kod) == ("Język Python", "py", "WFAIS.IF-X204.0")
    assert sorted(kopia.zestawy.values_list("skrot", flat=True)) == ["k1", "z03"]
    assert list(kopia.zestawy.get(skrot="z03").zadania.values_list("kod", flat=True)) == ["ZADANIE1"]
    assert not kopia.grupy.exists() and not Przydzial.objects.filter(zestaw__kurs=kopia).exists()
    assert Przydzial.domyslny_przedrostek(kopia.zestawy.get(skrot="z03"), przydzial.grupa) == "uj-py2728-z03-g4-"
    # Drugi raz do tego samego roku: skrot zajety; z grupami pod innym skrotem
    odp = client.post(f"/panel/kursy/{kurs.pk}/kopiuj/", {"rok": nowy_rok.pk, "nazwa": "", "skrot": ""})
    assert odp.status_code == 200 and Kurs.objects.filter(rok=nowy_rok).count() == 1
    client.post(f"/panel/kursy/{kurs.pk}/kopiuj/", {"rok": nowy_rok.pk, "nazwa": "Python B", "skrot": "pyb",
                                                    "grupy": "on"})
    assert list(Kurs.objects.get(skrot="pyb").grupy.values_list("skrot", flat=True)) == ["g4"]


@pytest.mark.django_db
def test_pdf_w_archiwum_i_kopii(settings, tmp_path, github, przydzial):
    from django.core.files.base import ContentFile
    settings.MEDIA_ROOT = str(tmp_path)
    zestaw = przydzial.zestaw
    zestaw.tresc_pdf.save("zestaw3.pdf", ContentFile(b"%PDF-1.4 tresc"), save=True)
    from kurs import struktura
    kopia = struktura.kopiuj_zestaw(zestaw, _inny_kurs(zestaw.kurs))
    assert kopia.tresc_pdf.read() == b"%PDF-1.4 tresc" and kopia.tresc_pdf.name != zestaw.tresc_pdf.name
    rok = zestaw.kurs.rok
    Rok.objects.filter(pk=rok.pk).update(zarchiwizowany=True)
    rok.refresh_from_db()
    plik = archiwum.do_pliku(rok)
    archiwum.usun(rok)
    nowy = archiwum.wczytaj_plik(plik)
    odtworzony = Zestaw.objects.get(kurs__rok=nowy)
    with odtworzony.tresc_pdf.open("rb") as f:
        assert f.read() == b"%PDF-1.4 tresc"
