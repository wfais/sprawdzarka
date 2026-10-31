"""Usuwanie z potwierdzeniem nazwy oraz strona Ustawienia."""
import pytest

from kurs import uslugi
from kurs.models import Grupa, Kurs, Przydzial, Rok, Uczelnia, Zestaw, Zgloszenie


@pytest.mark.django_db
def test_administracja_ma_poligon(client, prowadzacy):
    strona = client.get("/panel/ustawienia/").content.decode()
    assert "Utwórz poligon" in strona and "panel Django" in strona
    assert "Utwórz poligon" not in client.get("/panel/uczelnie/").content.decode()


@pytest.mark.django_db
def test_strony_edycji_maja_usuwanie(client, github, prowadzacy, przydzial):
    kurs = przydzial.zestaw.kurs
    for adres in [f"/panel/uczelnie/{kurs.rok.uczelnia_id}/", f"/panel/kursy/{kurs.pk}/edycja/",
                  f"/panel/lata/{kurs.rok_id}/edycja/",
                  f"/panel/grupy/{przydzial.grupa_id}/edycja/", f"/panel/zestawy/{przydzial.zestaw_id}/edycja/",
                  f"/panel/przydzialy/{przydzial.pk}/edycja/"]:
        assert "Usuwanie" in client.get(adres).content.decode(), adres


@pytest.mark.django_db
def test_uczelnia_tylko_pusta(client, prowadzacy, rok):
    odp = client.get(f"/panel/uczelnie/{rok.uczelnia_id}/").content.decode()
    assert "Najpierw usuń jej lata" in odp
    client.post(f"/panel/uczelnie/{rok.uczelnia_id}/usun/", {"potwierdzenie": "uj"})
    assert Uczelnia.objects.filter(pk=rok.uczelnia_id).exists()
    nowa = Uczelnia.objects.create(nazwa="Politechnika", skrot="pk")
    assert client.get(f"/panel/uczelnie/{nowa.pk}/usun/").status_code == 200
    client.post(f"/panel/uczelnie/{nowa.pk}/usun/", {"potwierdzenie": "pk"})
    assert not Uczelnia.objects.filter(pk=nowa.pk).exists()


@pytest.mark.django_db
def test_rok_bez_zgloszen_mozna_usunac_od_razu(client, github, prowadzacy, przydzial):
    rok = przydzial.zestaw.kurs.rok
    uslugi.akceptuj(przydzial, 99, "ewa", "Ewa Lis")
    assert "Najpierw go zarchiwizuj" in client.get(f"/panel/lata/{rok.pk}/edycja/").content.decode()
    client.post(f"/panel/lata/{rok.pk}/usun/", {"potwierdzenie": rok.nazwa})
    assert Rok.objects.filter(pk=rok.pk).exists()
    pusty = Rok.objects.create(uczelnia=rok.uczelnia, nazwa="2030/2031")
    kurs = Kurs.objects.create(rok=pusty, nazwa="Język Python", skrot="py")
    Grupa.objects.create(kurs=kurs, nazwa="Grupa 1", skrot="g1")
    client.post(f"/panel/lata/{pusty.pk}/usun/", {"potwierdzenie": "2030/2031"})
    assert not Rok.objects.filter(pk=pusty.pk).exists() and not Kurs.objects.filter(pk=kurs.pk).exists()
    assert Rok.objects.filter(pk=rok.pk).exists()


@pytest.mark.django_db
def test_usuniecie_przedmiotu_z_grupami_i_zgloszeniami(client, github, prowadzacy, przydzial):
    """Przedmiot usuwa sie bezposrednio (bez usuwania roku), razem z zawartoscia."""
    kurs = przydzial.zestaw.kurs
    uslugi.akceptuj(przydzial, 99, "ewa", "Ewa Lis")
    inny = Kurs.objects.create(rok=kurs.rok, nazwa="Język C++", skrot="cpp")
    strona = client.get(f"/panel/kursy/{kurs.pk}/usun/").content.decode()
    assert "zgłoszenia studentów z wypchnięciami i ocenami: 1" in strona
    client.post(f"/panel/kursy/{kurs.pk}/usun/", {"potwierdzenie": "zla nazwa"})
    assert Kurs.objects.filter(pk=kurs.pk).exists()
    odp = client.post(f"/panel/kursy/{kurs.pk}/usun/", {"potwierdzenie": kurs.nazwa})
    assert odp["Location"] == f"/panel/lata/{kurs.rok_id}/"
    assert not Kurs.objects.filter(pk=kurs.pk).exists() and Kurs.objects.filter(pk=inny.pk).exists()
    assert not Grupa.objects.exists() and not Zestaw.objects.exists() and not Zgloszenie.objects.exists()


@pytest.mark.django_db
def test_usuniecie_zestawu_i_wydania(client, github, prowadzacy, przydzial):
    z = uslugi.akceptuj(przydzial, 99, "ewa", "Ewa Lis")
    g5 = Grupa.objects.create(kurs=przydzial.zestaw.kurs, nazwa="Grupa 5", skrot="g5")
    drugi = Przydzial.objects.create(zestaw=przydzial.zestaw, grupa=g5, przedrostek_repo="x-g5-",
                                     termin_miekki=przydzial.termin_miekki, termin_twardy=przydzial.termin_twardy)
    # Wydanie jednej grupie
    client.post(f"/panel/przydzialy/{drugi.pk}/usun/", {"potwierdzenie": "g5"})
    assert Przydzial.objects.filter(pk=drugi.pk).exists()
    odp = client.post(f"/panel/przydzialy/{drugi.pk}/usun/", {"potwierdzenie": "g5/z03"})
    assert odp["Location"] == f"/panel/grupy/{g5.pk}/" and not Przydzial.objects.filter(pk=drugi.pk).exists()
    assert Zgloszenie.objects.filter(pk=z.pk).exists()
    # Caly zestaw z wydaniami i zgloszeniami
    zestaw = przydzial.zestaw
    client.post(f"/panel/zestawy/{zestaw.pk}/usun/", {"potwierdzenie": "z03"})
    assert not Zestaw.objects.filter(pk=zestaw.pk).exists() and not Zgloszenie.objects.exists()
    assert Grupa.objects.count() == 2


@pytest.mark.django_db
def test_w_zarchiwizowanym_roku_usuwanie_zablokowane(client, github, prowadzacy, przydzial):
    kurs = przydzial.zestaw.kurs
    Rok.objects.filter(pk=kurs.rok_id).update(zarchiwizowany=True)
    client.post(f"/panel/grupy/{przydzial.grupa_id}/usun/", {"potwierdzenie": przydzial.grupa.nazwa})
    client.post(f"/panel/zestawy/{przydzial.zestaw_id}/usun/", {"potwierdzenie": "z03"})
    client.post(f"/panel/kursy/{kurs.pk}/usun/", {"potwierdzenie": kurs.nazwa})
    assert Grupa.objects.exists() and Zestaw.objects.exists() and Kurs.objects.exists()
