from datetime import timedelta
from decimal import Decimal

import pytest
import responses
from django.utils import timezone

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
    from kurs.models import Kurs, Rok, Uczelnia
    assert client.get("/panel/").status_code == 200
    uj = Uczelnia.objects.get(skrot="uj")
    odp = client.post("/panel/uczelnie/", {"rodzaj": "rok", "r-uczelnia": uj.pk, "r-nazwa": "2026/2027", "r-skrot": ""})
    assert odp.status_code == 302
    rok = Rok.objects.get(uczelnia=uj)
    assert rok.skrot == "2627"
    odp = client.post(f"/panel/lata/{rok.pk}/", {"k-nazwa": "Język Python", "k-skrot": "py"})
    assert odp.status_code == 302
    kurs = Kurs.objects.get(skrot="py")
    assert kurs.rok == rok
    odp = client.post(f"/panel/kursy/{kurs.pk}/", {"g-nazwa": "Grupa 6", "g-skrot": "g6", "g-opis": "", "g-aktywna": "on"})
    assert odp.status_code == 302
    grupa = Grupa.objects.get(skrot="g6")
    assert grupa.kurs == kurs
    odp = client.post(f"/panel/kursy/{kurs.pk}/nowy-zestaw/", {
        "rodzaj": "zestaw", "nazwa": "Zestaw 1", "skrot": "z01", "repo_zrodlowe": "https://github.com/wfais/py-z1.git",
        "wzorce_kodu": "*.py", "punkty_max": "5",
    })
    assert odp.status_code == 302
    zestaw = Zestaw.objects.get(skrot="z01")
    assert zestaw.repo_zrodlowe == "wfais/py-z1" and zestaw.kurs == kurs
    odp = client.post(f"/panel/zestawy/{zestaw.pk}/", {
        "grupa": grupa.pk, "termin_miekki": "2026-10-26T23:59", "termin_twardy": "2026-11-02T23:59",
        "przedrostek_repo": "", "otwarty": "on",
    })
    assert odp.status_code == 302
    przydzial = Przydzial.objects.get()
    assert przydzial.przedrostek_repo == "uj-py2627-z01-g6-"
    assert przydzial.repo_startowe == "wfais/uj-py2627-z01-g6-start"
    assert github.migawki == [("wfais/py-z1", "wfais/uj-py2627-z01-g6-start")]
    assert "Grupa 6" in client.get("/panel/").content.decode()
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
def test_style_z_wersja(client, prowadzacy):
    """Adres arkusza zawiera wersje, wiec po aktualizacji przegladarka nie uzyje starego z pamieci."""
    import re
    strona = client.get("/panel/").content.decode()
    assert re.search(r'kurs/styl\.css\?v=[1-9][0-9]+"', strona)


@pytest.mark.django_db
def test_zdrowie(client):
    assert client.get("/zdrowie/").content == b"ok"


@pytest.mark.django_db
def test_wszystkie_strony_panelu_sie_otwieraja(client, github, prowadzacy, przydzial):
    from kurs import uslugi
    z = uslugi.akceptuj(przydzial, 99, "ewa", "")
    Student.objects.filter(pk=z.student_id).update(kandydat_readme="Ewa Lis")
    kurs = przydzial.zestaw.kurs
    rok = kurs.rok
    adresy = [
        "/panel/", "/panel/uczelnie/", "/panel/przedmioty/", "/panel/grupy/", "/panel/zestawy/", "/panel/oceny/",
        f"/panel/oceny/?zestaw={przydzial.zestaw.pk}", "/panel/ustawienia/", f"/panel/lata/{rok.pk}/", f"/panel/lata/{rok.pk}/edycja/",
        f"/panel/kursy/{kurs.pk}/", f"/panel/kursy/{kurs.pk}/edycja/",
        f"/panel/kursy/{kurs.pk}/kopiuj/", f"/panel/kursy/{kurs.pk}/usun/",
        f"/panel/grupy/{przydzial.grupa.pk}/usun/", f"/panel/zestawy/{przydzial.zestaw.pk}/usun/",
        f"/panel/przydzialy/{przydzial.pk}/usun/",
        f"/panel/kursy/{kurs.pk}/nowy-zestaw/", f"/panel/kursy/{kurs.pk}/nowy-zestaw/?rodzaj=kolokwium",
        f"/panel/lata/{rok.pk}/archiwum.json", f"/panel/uczelnie/{rok.uczelnia_id}/",
        f"/panel/grupy/{przydzial.grupa.pk}/", f"/panel/grupy/{przydzial.grupa.pk}/edycja/",
        f"/panel/zestawy/{przydzial.zestaw.pk}/", f"/panel/zestawy/{przydzial.zestaw.pk}/kopiuj/",
        f"/panel/zestawy/{przydzial.zestaw.pk}/edycja/", f"/panel/przydzialy/{przydzial.pk}/",
        f"/panel/przydzialy/{przydzial.pk}/kopiuj/",
        f"/panel/przydzialy/{przydzial.pk}/edycja/", f"/panel/przydzialy/{przydzial.pk}/?stan=w_terminie",
        f"/panel/przydzialy/{przydzial.pk}/?stan=blad", f"/panel/przydzialy/{przydzial.pk}/?stan=testy",
        f"/panel/przydzialy/{przydzial.pk}/?stan=do_oceny",
        f"/panel/zgloszenia/{z.pk}/",
        "/panel/studenci/", "/panel/studenci/?filtr=bez&q=ewa", "/admin/", "/admin/kurs/zgloszenie/",
    ]
    for adres in adresy:
        odp = client.get(adres)
        assert odp.status_code == 200, adres
    # Dawne adresy prowadza na nowe strony
    assert client.get("/panel/struktura/")["Location"] == "/panel/uczelnie/"
    assert client.get("/panel/administracja/")["Location"] == "/panel/ustawienia/"
    assert client.get("/panel/modele/")["Location"] == "/panel/ustawienia/#modele"
    assert client.get("/panel/kalibracja/")["Location"] == "/panel/oceny/#kalibracja"


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
    assert "<b>1</b> zmienione testy" in lista and "ZADANIE1/tests/test_main.py" in lista
    filtr = client.get(f"/panel/przydzialy/{przydzial.pk}/?stan=testy").content.decode()
    assert "Ewa Lis" in filtr and "Jan Nowak" not in filtr
    karta = client.get(f"/panel/zgloszenia/{z1.pk}/").content.decode()
    assert "Student zmienił pliki testów" in karta
    pulpit = client.get("/panel/").content.decode()
    assert "<b>1</b> zmienione testy" in pulpit


@pytest.mark.django_db
def test_karta_oznacza_pominiete_wypchniecia(client, github, prowadzacy, przydzial):
    from django.utils import timezone

    from kurs import uslugi
    from kurs.models import Wypchniecie
    z = uslugi.akceptuj(przydzial, 101, "ewa", "Ewa Lis")
    Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main", sha_przed="c" * 40,
                               sha_po="d" * 40, czas=timezone.now(), autor="witoldprzygoda", autor_id=5,
                               zrodlo="aktywnosc")
    karta = client.get(f"/panel/zgloszenia/{z.pk}/").content.decode()
    assert "pominięte" in karta


@pytest.mark.django_db
def test_przedrostek_z_formularza_bez_zmian(github, prowadzacy, zestaw):
    from kurs.forms import FormularzPrzydzialu
    from kurs.models import Grupa
    grupa = Grupa.objects.create(kurs=zestaw.kurs, nazwa="Grupa 9", skrot="g9")
    formularz = FormularzPrzydzialu(data={
        "grupa": grupa.pk, "termin_miekki": "2026-11-01T10:00", "termin_twardy": "2026-11-08T10:00",
        "przedrostek_repo": "set0_", "otwarty": "on",
    }, zestaw=zestaw)
    assert formularz.is_valid(), formularz.errors
    assert formularz.cleaned_data["przedrostek_repo"] == "set0_"


@pytest.mark.django_db
def test_strona_studenta_bez_readme_i_z_jednym_terminem(client, github, przydzial):
    zaloguj_studenta(client)
    strona = client.get(f"/z/{przydzial.token}/").content.decode()
    assert "Z karą do" in strona
    client.post(f"/z/{przydzial.token}/", {"imie_nazwisko": "Jan Kowalski"})
    strona = client.get(f"/z/{przydzial.token}/").content.decode()
    assert "Twoje repozytorium jest gotowe" in strona and "README" not in strona
    Przydzial.objects.filter(pk=przydzial.pk).update(termin_twardy=przydzial.termin_miekki)
    strona = client.get(f"/z/{przydzial.token}/").content.decode()
    assert "Z karą do" not in strona and "Termin" in strona


@pytest.mark.django_db
def test_publikacja_pod_terminami_i_zwinieta(client, github, prowadzacy, zestaw, grupa):
    """Pole publikacji stoi po terminach i jest schowane pod odnosnikiem (zwykle zostaje puste)."""
    tresc = client.get(f"/panel/zestawy/{zestaw.pk}/").content.decode()
    assert tresc.index('name="termin_twardy"') < tresc.index('name="publikacja"')
    assert '<details class="pozniejsza-publikacja">' in tresc


@pytest.mark.django_db
def test_edycja_opublikowanego_zostawia_date_publikacji(client, github, prowadzacy, przydzial):
    dawniej = timezone.now() - timedelta(days=3)
    Przydzial.objects.filter(pk=przydzial.pk).update(publikacja=dawniej)
    adres = f"/panel/przydzialy/{przydzial.pk}/edycja/"
    assert '<details class="pozniejsza-publikacja">' in client.get(adres).content.decode()
    client.post(adres, {
        "termin_miekki": timezone.localtime(przydzial.termin_miekki).strftime("%Y-%m-%dT%H:%M"),
        "termin_twardy": timezone.localtime(przydzial.termin_twardy).strftime("%Y-%m-%dT%H:%M"),
        "publikacja": "", "otwarty": "on",
    })
    przydzial.refresh_from_db()
    assert przydzial.publikacja == dawniej
    # Zaplanowana publikacja: pole od razu widoczne
    Przydzial.objects.filter(pk=przydzial.pk).update(publikacja=timezone.now() + timedelta(days=2))
    assert '<details class="pozniejsza-publikacja" open>' in client.get(adres).content.decode()


@pytest.mark.django_db
def test_menu_panelu(client, prowadzacy):
    strona = client.get("/panel/grupy/").content.decode()
    menu = strona[strona.index("<nav>"):strona.index("</nav>")]
    pozycje = ["Pulpit", "Uczelnie", "Przedmioty", "Grupy", "Studenci", "Zestawy", "Oceny", "Ustawienia"]
    assert [menu.index(f">{p}<") for p in pozycje] == sorted(menu.index(f">{p}<") for p in pozycje)
    assert 'class="aktywny">Grupy<' in menu and "Struktura" not in menu and "Administracja" not in menu


@pytest.mark.django_db
def test_nowa_grupa_i_nowy_zestaw_z_list(client, prowadzacy, zestaw, grupa):
    kurs = zestaw.kurs
    odp = client.post("/panel/grupy/", {"g-kurs": kurs.pk, "g-nazwa": "Grupa nr 9", "g-skrot": "g9", "g-aktywna": "on"})
    nowa = Grupa.objects.get(skrot="g9")
    assert nowa.kurs == kurs and odp["Location"] == f"/panel/grupy/{nowa.pk}/"
    odp = client.post("/panel/grupy/", {"g-kurs": kurs.pk, "g-nazwa": "Inna", "g-skrot": "g9"})
    assert odp.status_code == 200 and "już użyty w tym przedmiocie" in odp.content.decode()
    assert "Grupa nr 9" in client.get("/panel/grupy/").content.decode()
    odp = client.get(f"/panel/zestawy/?kurs={kurs.pk}&rodzaj=kolokwium")
    assert odp["Location"] == f"/panel/kursy/{kurs.pk}/nowy-zestaw/?rodzaj=kolokwium"
    assert client.get(f"/panel/zestawy/?kurs={kurs.pk}&rodzaj=zestaw")["Location"] == f"/panel/kursy/{kurs.pk}/nowy-zestaw/"
    assert zestaw.nazwa in client.get("/panel/zestawy/").content.decode()


@pytest.mark.django_db
def test_oceny_pokazuja_prace_do_decyzji(client, github, prowadzacy, przydzial):
    from kurs import uslugi
    assert "Nikt jeszcze nie oddał prac" in client.get("/panel/oceny/").content.decode()
    z = uslugi.akceptuj(przydzial, 77, "jank", "Jan Kowalski")
    Zgloszenie.objects.filter(pk=z.pk).update(sha_oceniany="c" * 40)
    strona = client.get("/panel/oceny/").content.decode()
    assert f"/panel/przydzialy/{przydzial.pk}/ocen/" in strona and ">Oceń</button>" in strona
    # Bez nowych prac do oceny przycisk wraca na liste Oceny
    Zgloszenie.objects.filter(pk=z.pk).update(sha_oceniany="")
    odp = client.post(f"/panel/przydzialy/{przydzial.pk}/ocen/", {"nastepny": "oceny"})
    assert odp["Location"] == "/panel/oceny/"


@pytest.mark.django_db
def test_przyjmowanie_zgloszen_wylacza_sie_po_terminie_ostatecznym(client, github, prowadzacy, przydzial):
    from kurs import uslugi
    teraz = timezone.now()
    Przydzial.objects.filter(pk=przydzial.pk).update(termin_miekki=teraz - timedelta(days=8),
                                                     termin_twardy=teraz - timedelta(minutes=1))
    assert uslugi.zamknij_po_terminach() == 1
    przydzial.refresh_from_db()
    assert not przydzial.otwarty and przydzial.zamkniety_po_terminie
    assert "po terminie ostatecznym" in client.get(f"/panel/przydzialy/{przydzial.pk}/").content.decode()
    # Prowadzacy wlacza przyjmowanie z powrotem: robot juz go nie wylacza
    Przydzial.objects.filter(pk=przydzial.pk).update(otwarty=True)
    assert uslugi.zamknij_po_terminach() == 0
    # Nowy termin ostateczny: po nim zestaw znow zamyka sie sam
    client.post(f"/panel/przydzialy/{przydzial.pk}/edycja/", {
        "termin_miekki": timezone.localtime(teraz + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
        "termin_twardy": timezone.localtime(teraz + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M"),
        "otwarty": "on",
    })
    przydzial.refresh_from_db()
    assert przydzial.otwarty and przydzial.zamkniety_po_terminie is None
    assert uslugi.zamknij_po_terminach(teraz + timedelta(days=4)) == 1


@pytest.mark.django_db
def test_po_terminie_ostatecznym_link_nie_przyjmuje_od_razu(client, github, przydzial):
    """Strona studenta zamyka zestaw sama, bez czekania na robota."""
    teraz = timezone.now()
    Przydzial.objects.filter(pk=przydzial.pk).update(termin_miekki=teraz - timedelta(days=2),
                                                     termin_twardy=teraz - timedelta(seconds=5))
    zaloguj_studenta(client)
    assert "nie przyjmuje już zgłoszeń" in client.get(f"/z/{przydzial.token}/").content.decode()
    przydzial.refresh_from_db()
    assert not przydzial.otwarty and przydzial.zamkniety_po_terminie
