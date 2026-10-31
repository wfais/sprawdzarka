"""Strona Studenci: uklad uczelnia, rok, przedmiot, grupa; wyszukiwanie i wybor roku."""
import pytest

from kurs import uslugi
from kurs.models import Grupa, Kurs, Przydzial, Rok, Student, Zestaw, Zgloszenie


@pytest.mark.django_db
def test_studenci_w_grupach_z_kolumnami_zestawow(client, github, prowadzacy, przydzial):
    uslugi.akceptuj(przydzial, 99, "ewa", "Ewa Lis")
    kurs = przydzial.zestaw.kurs
    inny = Kurs.objects.create(rok=kurs.rok, nazwa="Język C++", skrot="cpp")
    g8 = Grupa.objects.create(kurs=inny, nazwa="Grupa 8", skrot="g8")
    z1 = Zestaw.objects.create(kurs=inny, nazwa="Zestaw 1", skrot="c01", repo_zrodlowe="wfais/cpp1")
    p8 = Przydzial.objects.create(zestaw=z1, grupa=g8, przedrostek_repo="uj-cpp-c01-g8-",
                                  termin_miekki=przydzial.termin_miekki, termin_twardy=przydzial.termin_twardy)
    uslugi.akceptuj(p8, 99, "ewa", "")
    uslugi.akceptuj(p8, 98, "jan", "Jan Kot")
    strona = client.get("/panel/studenci/").content.decode()
    # Ewa w obu grupach, Jan tylko w grupie C++
    assert strona.count("Ewa Lis") == 2 and strona.count('value="Jan Kot"') == 1
    # Przedmioty alfabetycznie, kazdy ze swoimi grupami
    assert strona.index("Język C++") < strona.index("Grupa 8") < strona.index("Język Python") < strona.index("Grupa 4")
    assert "z03" in strona
    assert "c01" in strona and "dioda niebieski" in strona
    # Wyszukiwanie zostawia tylko pasujace osoby i ich grupy
    strona = client.get("/panel/studenci/?q=kot").content.decode()
    assert "Jan Kot" in strona and "Ewa Lis" not in strona and "Grupa 4" not in strona
    # Wybrany rok
    inny_rok = Rok.objects.create(uczelnia=kurs.rok.uczelnia, nazwa="2027/2028")
    strona = client.get(f"/panel/studenci/?rok={inny_rok.pk}").content.decode()
    assert "Ewa Lis" not in strona and "Nikt nie pasuje" not in strona


@pytest.mark.django_db
def test_konto_bez_prac_tez_na_liscie(client, github, prowadzacy, przydzial):
    z = uslugi.akceptuj(przydzial, 99, "ewa", "Ewa Lis")
    Zgloszenie.objects.filter(pk=z.pk).update(status=Zgloszenie.Status.WYZEROWANE)
    Student.objects.create(github_id=5, login="anonim")
    strona = client.get("/panel/studenci/?filtr=bez").content.decode()
    assert "Konta bez prac" in strona and "anonim" in strona and "Ewa Lis" not in strona


@pytest.mark.django_db
def test_pokaz_na_kazdym_poziomie(client, github, prowadzacy, przydzial):
    uslugi.akceptuj(przydzial, 99, "ewa", "Ewa Lis")
    kurs = przydzial.zestaw.kurs
    g5 = Grupa.objects.create(kurs=kurs, nazwa="Grupa 5", skrot="g5")
    p5 = Przydzial.objects.create(zestaw=przydzial.zestaw, grupa=g5, przedrostek_repo="uj-py2627-z03-g5-",
                                  termin_miekki=przydzial.termin_miekki, termin_twardy=przydzial.termin_twardy)
    uslugi.akceptuj(p5, 98, "jan", "Jan Kot")
    strona = client.get("/panel/studenci/").content.decode()
    # Grupy domyslnie zwiniete; "pokaz" na poziomie uczelni, roku i przedmiotu rozwija wszystkie ich grupy
    assert strona.count("data-przelacz-grupy") == 3 and strona.count('class="przycisk maly przelacznik"') == 2
    assert 'grupa-studentow" open' not in strona and "Ewa Lis" in strona and "Jan Kot" in strona
    # Zakres z adresu dalej dziala (np. z zakladki), wtedy grupy sa rozwiniete
    strona = client.get(f"/panel/studenci/?grupa={g5.pk}").content.decode()
    assert "Jan Kot" in strona and "Ewa Lis" not in strona and "pokaż wszystkich" in strona
    assert f'?kurs={kurs.pk}' in strona and 'grupa-studentow" open' in strona  # okruszki prowadza poziom wyzej
    strona = client.get(f"/panel/studenci/?kurs={kurs.pk}&q=lis").content.decode()
    assert "Ewa Lis" in strona and "Jan Kot" not in strona and 'grupa-studentow" open' in strona
    assert 'name="kurs"' in strona  # wyszukiwanie zostaje na tym poziomie


@pytest.mark.django_db
def test_ukryte_i_nieaktywne_pominiete(client, github, prowadzacy, przydzial):
    uslugi.akceptuj(przydzial, 99, "ewa", "Ewa Lis")
    kurs, grupa = przydzial.zestaw.kurs, przydzial.grupa
    rok = kurs.rok
    adres = "/panel/studenci/"

    def widac(parametry=""):
        return "Ewa Lis" in client.get(adres + parametry).content.decode()

    Grupa.objects.filter(pk=grupa.pk).update(aktywna=False)
    assert not widac() and not widac(f"?kurs={kurs.pk}") and widac(f"?grupa={grupa.pk}")
    Grupa.objects.filter(pk=grupa.pk).update(aktywna=True)
    Kurs.objects.filter(pk=kurs.pk).update(ukryty=True)
    assert not widac() and not widac(f"?rok={rok.pk}") and widac(f"?kurs={kurs.pk}")
    Kurs.objects.filter(pk=kurs.pk).update(ukryty=False)
    Rok.objects.filter(pk=rok.pk).update(ukryty=True)
    assert not widac() and not widac(f"?uczelnia={rok.uczelnia_id}") and widac(f"?rok={rok.pk}")
