from decimal import Decimal

import pytest
import responses

from kurs.models import Grupa, Przydzial, Student, Zestaw, Zgloszenie


def zaloguj_studenta(client, gh_id=77, login="jank", nazwa="Jan Kowalski"):
    sesja = client.session
    sesja["gh_uzytkownik"] = {"id": gh_id, "login": login, "nazwa": nazwa}
    sesja.save()


@pytest.mark.django_db
def test_strona_zestawu_wymaga_logowania_github(client, przydzial):
    odp = client.get(f"/z/{przydzial.token}/")
    assert odp.status_code == 302
    assert odp["Location"].startswith("/auth/github/?next=/z/")


@pytest.mark.django_db
def test_logowanie_przekierowuje_do_github(client, przydzial):
    odp = client.get(f"/auth/github/?next=/z/{przydzial.token}/")
    assert odp.status_code == 302
    assert odp["Location"].startswith("https://github.com/login/oauth/authorize?client_id=Iv1.test")
    assert client.session["gh_nastepny"] == f"/z/{przydzial.token}/"


@pytest.mark.django_db
@responses.activate
def test_powrot_z_github_zapisuje_uzytkownika(client, przydzial):
    sesja = client.session
    sesja["gh_stan"] = "abc"
    sesja["gh_nastepny"] = f"/z/{przydzial.token}/"
    sesja.save()
    responses.post("https://github.com/login/oauth/access_token", json={"access_token": "ghu_x"})
    responses.get("https://api.github.com/user", json={"id": 77, "login": "jank", "name": "Jan Kowalski"})
    odp = client.get("/auth/github/powrot/?code=kod&state=abc")
    assert odp.status_code == 302
    assert odp["Location"] == f"/z/{przydzial.token}/"
    assert client.session["gh_uzytkownik"]["login"] == "jank"


@pytest.mark.django_db
def test_powrot_ze_zlym_stanem_odrzucony(client):
    sesja = client.session
    sesja["gh_stan"] = "abc"
    sesja.save()
    assert client.get("/auth/github/powrot/?code=kod&state=inny").status_code == 400


@pytest.mark.django_db
def test_akceptacja_przez_strone(client, github, przydzial):
    zaloguj_studenta(client)
    odp = client.get(f"/z/{przydzial.token}/")
    assert odp.status_code == 200
    assert "Akceptuję zestaw" in odp.content.decode()
    odp = client.post(f"/z/{przydzial.token}/", {"imie_nazwisko": "Jan Kowalski"})
    assert odp.status_code == 302
    odp = client.get(f"/z/{przydzial.token}/")
    tresc = odp.content.decode()
    assert "Twoje repozytorium jest gotowe" in tresc
    assert "wfais/py-z03-g4-jank/invitations" in tresc


@pytest.mark.django_db
def test_akceptacja_wymaga_imienia_i_nazwiska(client, github, przydzial):
    zaloguj_studenta(client, nazwa="")
    odp = client.post(f"/z/{przydzial.token}/", {"imie_nazwisko": "Jan"})
    assert odp.status_code == 200
    assert not Zgloszenie.objects.exists()


@pytest.mark.django_db
def test_zamkniety_zestaw_nie_przyjmuje(client, github, przydzial):
    przydzial.otwarty = False
    przydzial.save()
    zaloguj_studenta(client)
    assert "nie przyjmuje" in client.get(f"/z/{przydzial.token}/").content.decode()
    assert client.post(f"/z/{przydzial.token}/", {"imie_nazwisko": "Jan Kowalski"}).status_code == 400


@pytest.mark.django_db
def test_panel_wymaga_logowania(client):
    odp = client.get("/panel/")
    assert odp.status_code == 302
    assert "/panel/logowanie/" in odp["Location"]


@pytest.mark.django_db
def test_student_nie_jest_prowadzacym(client, django_user_model):
    zwykly = django_user_model.objects.create_user("zwykly", password="haslo-testowe-123")
    client.force_login(zwykly)
    assert client.get("/panel/").status_code == 302


@pytest.mark.django_db
def test_pelny_przebieg_panelu(client, github, prowadzacy):
    assert client.get("/panel/").status_code == 200
    odp = client.post("/panel/grupy/", {"nazwa": "Grupa 6", "skrot": "g6", "opis": "", "aktywna": "on"})
    assert odp.status_code == 302
    grupa = Grupa.objects.get(skrot="g6")
    odp = client.post("/panel/zestawy/nowy/", {
        "nazwa": "Zestaw 1", "skrot": "z01", "repo_zrodlowe": "https://github.com/wfais/py-z1.git",
        "wzorce_kodu": "*.py", "punkty_max": "5",
    })
    assert odp.status_code == 302
    zestaw = Zestaw.objects.get(skrot="z01")
    assert zestaw.repo_zrodlowe == "wfais/py-z1"
    odp = client.post(f"/panel/zestawy/{zestaw.pk}/", {
        "grupa": grupa.pk, "termin_miekki": "2026-10-26T23:59", "termin_twardy": "2026-11-02T23:59",
        "przedrostek_repo": "", "otwarty": "on",
    })
    assert odp.status_code == 302
    przydzial = Przydzial.objects.get()
    assert przydzial.przedrostek_repo == "py-z01-g6"
    assert przydzial.repo_startowe == "wfais/py-z01-g6-start"
    assert github.migawki == [("wfais/py-z1", "wfais/py-z01-g6-start")]
    tresc = client.get(f"/panel/przydzialy/{przydzial.pk}/").content.decode()
    assert przydzial.link_akceptacji in tresc

    # Student akceptuje
    from kurs import uslugi
    z = uslugi.akceptuj(przydzial, 99, "ewa", "Ewa Lis")
    assert "Ewa Lis" in client.get(f"/panel/przydzialy/{przydzial.pk}/").content.decode()
    assert client.get(f"/panel/zgloszenia/{z.pk}/").status_code == 200
    assert client.get(f"/panel/przydzialy/{przydzial.pk}/repozytoria.txt").content.decode().count("wfais/") == 1

    # Indywidualny termin
    odp = client.post(f"/panel/zgloszenia/{z.pk}/termin/", {
        "wyjatek_termin_miekki": "2026-11-05T12:00", "wyjatek_termin_twardy": "",
    })
    z.refresh_from_db()
    assert z.wyjatek_termin_miekki is not None

    # Ocena i zamkniecie
    odp = client.post(f"/panel/zgloszenia/{z.pk}/ocena/", {"punkty": "4,5", "mnoznik": "1", "komentarz": "OK"})
    z.refresh_from_db()
    assert z.pr_zamkniety and z.punkty == Decimal("4.5")
    assert github.zamkniete

    # Ocena ponad maksimum odrzucona
    client.post(f"/panel/zgloszenia/{z.pk}/ocena/", {"punkty": "7", "mnoznik": "1", "komentarz": ""})
    z.refresh_from_db()
    assert z.punkty == Decimal("4.5")

    # Zerowanie
    client.post(f"/panel/zgloszenia/{z.pk}/zeruj/", {})
    z.refresh_from_db()
    assert z.status == Zgloszenie.Status.WYZEROWANE

    # Studenci
    assert "Ewa Lis" in client.get("/panel/studenci/").content.decode()


@pytest.mark.django_db
def test_zatwierdzenie_kandydata(client, prowadzacy):
    s = Student.objects.create(github_id=5, login="x", kandydat_readme="Ewa Lis")
    client.post(f"/panel/studenci/{s.pk}/", {"akcja": "zatwierdz"})
    s.refresh_from_db()
    assert s.imie_nazwisko == "Ewa Lis"
    assert s.zrodlo_nazwiska == Student.Zrodlo.README


@pytest.mark.django_db
def test_wydanie_tej_samej_grupie_dwa_razy_odrzucone(client, github, prowadzacy, przydzial):
    odp = client.post(f"/panel/zestawy/{przydzial.zestaw.pk}/", {
        "grupa": przydzial.grupa.pk, "termin_miekki": "2026-10-26T23:59", "termin_twardy": "2026-11-02T23:59",
        "przedrostek_repo": "", "otwarty": "on",
    })
    assert odp.status_code == 200
    assert Przydzial.objects.count() == 1


@pytest.mark.django_db
def test_terminy_w_zlej_kolejnosci_odrzucone(client, github, prowadzacy, zestaw, grupa):
    odp = client.post(f"/panel/zestawy/{zestaw.pk}/", {
        "grupa": grupa.pk, "termin_miekki": "2026-11-26T23:59", "termin_twardy": "2026-11-02T23:59",
        "przedrostek_repo": "", "otwarty": "on",
    })
    assert odp.status_code == 200
    assert not Przydzial.objects.exists()


@pytest.mark.django_db
def test_zdrowie(client):
    assert client.get("/zdrowie/").content == b"ok"


@pytest.mark.django_db
def test_wszystkie_strony_panelu_sie_otwieraja(client, github, prowadzacy, przydzial):
    from kurs import uslugi
    z = uslugi.akceptuj(przydzial, 99, "ewa", "")
    Student.objects.filter(pk=z.student_id).update(kandydat_readme="Ewa Lis")
    adresy = [
        "/panel/", "/panel/grupy/", f"/panel/grupy/{przydzial.grupa.pk}/", "/panel/zestawy/",
        "/panel/zestawy/nowy/", f"/panel/zestawy/{przydzial.zestaw.pk}/",
        f"/panel/zestawy/{przydzial.zestaw.pk}/edycja/", f"/panel/przydzialy/{przydzial.pk}/",
        f"/panel/przydzialy/{przydzial.pk}/edycja/", f"/panel/przydzialy/{przydzial.pk}/?kolor=zielony",
        f"/panel/przydzialy/{przydzial.pk}/?kolor=problemy", f"/panel/przydzialy/{przydzial.pk}/?kolor=testy",
        f"/panel/zgloszenia/{z.pk}/",
        "/panel/studenci/", "/panel/studenci/?filtr=bez&q=ewa", "/admin/", "/admin/kurs/zgloszenie/",
    ]
    for adres in adresy:
        odp = client.get(adres)
        assert odp.status_code == 200, adres


@pytest.mark.django_db
def test_student_moze_ponowic_zaproszenie(client, github, przydzial):
    zaloguj_studenta(client)
    client.post(f"/z/{przydzial.token}/", {"imie_nazwisko": "Jan Kowalski"})
    odp = client.post(f"/z/{przydzial.token}/", {"akcja": "zaproszenie"}, follow=True)
    assert "Zaproszenie wysłane ponownie" in odp.content.decode()
    assert len(github.zaproszenia) == 2


@pytest.mark.django_db
def test_indywidualne_terminy_w_zlej_kolejnosci_odrzucone(client, github, prowadzacy, przydzial):
    from kurs import uslugi

    z = uslugi.akceptuj(przydzial, 99, "ewa", "Ewa Lis")
    client.post(f"/panel/zgloszenia/{z.pk}/termin/", {
        "wyjatek_termin_miekki": "2026-11-20T12:00", "wyjatek_termin_twardy": "2026-11-10T12:00",
    })
    z.refresh_from_db()
    assert z.wyjatek_termin_miekki is None


def test_adres_publiczny_w_dozwolonych_hostach():
    import importlib
    import os

    from sprawdzarka import settings as ustawienia

    stary = os.environ.get("PUBLICZNY_ADRES")
    os.environ["PUBLICZNY_ADRES"] = "https://sprawdzarka.przyklad.pl"
    try:
        importlib.reload(ustawienia)
        assert "sprawdzarka.przyklad.pl" in ustawienia.ALLOWED_HOSTS
    finally:
        if stary is None:
            os.environ.pop("PUBLICZNY_ADRES")
        else:
            os.environ["PUBLICZNY_ADRES"] = stary
        importlib.reload(ustawienia)


@pytest.mark.django_db
def test_znacznik_zmienionych_testow_w_panelu(client, github, prowadzacy, przydzial):
    from kurs import uslugi
    from kurs.models import Zgloszenie
    z1 = uslugi.akceptuj(przydzial, 101, "ewa", "Ewa Lis")
    uslugi.akceptuj(przydzial, 102, "jan", "Jan Nowak")
    Zgloszenie.objects.filter(pk=z1.pk).update(
        testy_zmienione=True, zmiany_testow={"zmienione": ["ZADANIE1/tests/test_main.py"]})
    lista = client.get(f"/panel/przydzialy/{przydzial.pk}/").content.decode()
    assert "zmienione testy 1" in lista and "ZADANIE1/tests/test_main.py" in lista
    filtr = client.get(f"/panel/przydzialy/{przydzial.pk}/?kolor=testy").content.decode()
    assert "Ewa Lis" in filtr and "Jan Nowak" not in filtr
    karta = client.get(f"/panel/zgloszenia/{z1.pk}/").content.decode()
    assert "Student zmienił pliki testów" in karta
    pulpit = client.get("/panel/").content.decode()
    assert "zmienione testy 1" in pulpit
