"""Diody: stan pracy studenta, stan zestawu w grupie, publikacja zestawu."""
from datetime import datetime, timedelta, timezone as tz
from decimal import Decimal

import pytest
from django.utils import timezone

from kurs import stany, uslugi
from kurs.forms import FormularzPrzydzialu
from kurs.models import Grupa, Przydzial, Student, Zgloszenie

from .test_widoki import zaloguj_studenta

D = Decimal
T = datetime(2026, 11, 10, 12, 0, tzinfo=tz.utc)


@pytest.mark.parametrize("status, kolor, mnoznik, teraz, klucz", [
    ("gotowe", "szary", None, T - timedelta(days=1), "otwarte"),
    ("gotowe", "szary", None, T + timedelta(days=1), "nie_oddano"),
    ("gotowe", "niebieski", None, T - timedelta(days=1), "w_terminie"),
    ("gotowe", "zielony", D(1), T + timedelta(days=9), "w_terminie"),
    ("gotowe", "zolty", D("0.8"), T + timedelta(days=9), "przeterminowane"),
    ("gotowe", "pomaranczowy", D(1), T + timedelta(days=9), "w_terminie"),
    ("gotowe", "pomaranczowy", D("0.8"), T + timedelta(days=9), "przeterminowane"),
    ("gotowe", "czerwony", D(0), T + timedelta(days=9), "po_terminie"),
    ("tworzenie", "szary", None, T, "tworzenie"),
    ("blad", "szary", None, T, "blad"),
])
def test_stan_pracy(status, kolor, mnoznik, teraz, klucz):
    assert stany.praca(status, kolor, mnoznik, T, teraz).klucz == klucz


def test_stan_zestawu_w_czasie():
    miekki, twardy = T, T + timedelta(days=7)

    def stan(teraz, do_oceny=3, publikacja=None):
        return stany.zestaw(publikacja, miekki, twardy, teraz, do_oceny).klucz

    assert stan(T - timedelta(days=3), publikacja=T - timedelta(days=2)) == "nieopublikowany"
    assert stan(T - timedelta(days=1), publikacja=T - timedelta(days=2)) == "trwa"
    assert stan(T - timedelta(days=1)) == "trwa"
    assert stan(T + timedelta(days=1)) == "po_terminie"
    assert stan(twardy + timedelta(days=1)) == "do_oceny"
    assert stan(twardy + timedelta(days=8)) == "zalegle"
    assert stan(twardy + timedelta(days=8), do_oceny=0) == "zakonczony"


@pytest.mark.django_db
def test_indywidualny_termin_zmienia_diode(github, przydzial):
    z = uslugi.akceptuj(przydzial, 99, "ewa", "Ewa Lis")
    teraz = timezone.now()
    Przydzial.objects.filter(pk=przydzial.pk).update(termin_miekki=teraz - timedelta(days=2),
                                                     termin_twardy=teraz + timedelta(days=5))
    Zgloszenie.objects.filter(pk=z.pk).update(stan_kolor="szary")
    z = Zgloszenie.objects.select_related("przydzial").get(pk=z.pk)
    assert z.stan_pracy.klucz == "nie_oddano"
    z.wyjatek_termin_miekki = teraz + timedelta(days=3)
    assert z.stan_pracy.klucz == "otwarte"


@pytest.mark.django_db
def test_zalegle_oceny_na_pulpicie(client, github, prowadzacy, przydzial):
    z = uslugi.akceptuj(przydzial, 99, "ewa", "Ewa Lis")
    teraz = timezone.now()
    Przydzial.objects.filter(pk=przydzial.pk).update(termin_miekki=teraz - timedelta(days=20),
                                                     termin_twardy=teraz - timedelta(days=10))
    Zgloszenie.objects.filter(pk=z.pk).update(stan_kolor="zielony", sugerowany_mnoznik=D(1), sha_oceniany="d" * 40)
    pulpit = client.get("/panel/").content.decode()
    assert "zaległe oceny" in pulpit and "Wymaga uwagi" in pulpit and "dioda fiolet pulsuje" in pulpit
    lista = client.get(f"/panel/przydzialy/{przydzial.pk}/?stan=do_oceny").content.decode()
    assert "Ewa Lis" in lista
    Zgloszenie.objects.filter(pk=z.pk).update(pr_zamkniety=True)
    pulpit = client.get("/panel/").content.decode()
    assert "zaległe oceny" not in pulpit and "ocenione" in pulpit


@pytest.mark.django_db
def test_publikacja_w_przyszlosci_blokuje_link(client, github, przydzial):
    teraz = timezone.now()
    Przydzial.objects.filter(pk=przydzial.pk).update(publikacja=teraz + timedelta(days=1))
    strona = client.get(f"/z/{przydzial.token}/").content.decode()
    assert "jeszcze nie jest udostępniony" in strona
    zaloguj_studenta(client)
    client.post(f"/z/{przydzial.token}/", {"imie_nazwisko": "Jan Kowalski"})
    assert not Zgloszenie.objects.exists()
    Przydzial.objects.filter(pk=przydzial.pk).update(publikacja=teraz - timedelta(minutes=1))
    client.post(f"/z/{przydzial.token}/", {"imie_nazwisko": "Jan Kowalski"})
    assert Zgloszenie.objects.count() == 1


@pytest.mark.django_db
def test_publikacja_z_formularza(zestaw, grupa):
    teraz = timezone.now()

    def formularz(publikacja, termin="2026-12-01T10:00"):
        return FormularzPrzydzialu(data={
            "grupa": grupa.pk, "publikacja": publikacja, "termin_miekki": termin,
            "termin_twardy": "2026-12-08T10:00", "otwarty": "on",
        }, zestaw=zestaw)

    f = formularz("")
    assert f.is_valid(), f.errors
    assert abs(f.cleaned_data["publikacja"] - teraz) < timedelta(minutes=1)
    f = formularz("2020-01-01T10:00")
    assert f.is_valid() and f.cleaned_data["publikacja"] > teraz - timedelta(minutes=1)
    jutro = timezone.localtime(teraz + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    f = formularz(jutro)
    assert f.is_valid() and f.save().publikacja > teraz
    f = FormularzPrzydzialu(data={
        "grupa": Grupa.objects.create(kurs=zestaw.kurs, nazwa="Grupa 5", skrot="g5").pk, "publikacja": "2026-12-05T10:00",
        "termin_miekki": "2026-12-01T10:00", "termin_twardy": "2026-12-08T10:00",
    }, zestaw=zestaw)
    assert not f.is_valid() and "publikacja" in f.errors


@pytest.mark.django_db
def test_kafelki_prac_na_liscie_studentow(client, github, prowadzacy, przydzial):
    uslugi.akceptuj(przydzial, 99, "ewa", "Ewa Lis")
    strona = client.get("/panel/studenci/").content.decode()
    assert 'class="kafelek"' in strona and "z03" in strona and "dioda niebieski" in strona
    assert Student.objects.count() == 1


@pytest.mark.django_db
def test_kurs_ma_identyfikator_a_grupa_zajecia(client, prowadzacy, przydzial):
    kurs = przydzial.zestaw.kurs
    kurs.kod = "WFAIS.IF-X204.0"
    kurs.save()
    Grupa.objects.filter(pk=przydzial.grupa_id).update(opis="poniedziałek 10:10 - 11:40, sala G-1-03")
    pulpit = client.get("/panel/").content.decode()
    assert "WFAIS.IF-X204.0" in pulpit and "sala G-1-03" in pulpit
    assert "WFAIS.IF-X204.0" in client.get(f"/panel/kursy/{kurs.pk}/").content.decode()
    assert "WFAIS.IF-X204.0" in client.get(f"/panel/lata/{kurs.rok_id}/").content.decode()
