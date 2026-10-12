import pytest

from kurs.nazwiska import wyciagnij_nazwisko


@pytest.mark.parametrize("tekst, oczekiwane", [
    ("# Zestaw 3\n\nImię i nazwisko: Jan Kowalski\n", "Jan Kowalski"),
    ("**Imie i nazwisko:** Anna Maria Nowak", "Anna Maria Nowak"),
    ("- Autor: Piotr Wiśniewski, grupa 4", "Piotr Wiśniewski"),
    ("Nazwisko i imię - Zielińska Ewa", "Zielińska Ewa"),
    ("autor: jan kowalski", "Jan Kowalski"),
    ("Autor: KOWALSKA-NOWAK ANNA", "Kowalska-Nowak Anna"),
    ("Student: [Jan Kowalski](https://github.com/jk)", "Jan Kowalski"),
    ("Name: John Smith (jsmith)", "John Smith"),
])
def test_rozpoznaje_nazwisko(tekst, oczekiwane):
    assert wyciagnij_nazwisko(tekst) == oczekiwane


@pytest.mark.parametrize("tekst", [
    "Imię i nazwisko: (wpisz tutaj)",
    "Imię i nazwisko:",
    "Autor: Jan",
    "Autor: jkowalski123",
    "# Zestaw 3: funkcje\nZadanie 1: napisz funkcje",
    "",
    "Imię i nazwisko: Imię Nazwisko",
])
def test_odrzuca_zaslepki_i_smieci(tekst):
    assert wyciagnij_nazwisko(tekst) is None


def test_separator_z_polpauza():
    assert wyciagnij_nazwisko("Imię i nazwisko \N{EN DASH} Jan Kowalski") == "Jan Kowalski"


def test_nazwa_katalogu_w_narzedziu():
    import importlib.util
    from pathlib import Path

    sciezka = Path(__file__).resolve().parents[2] / "narzedzia" / "pobierz_repozytoria.py"
    spec = importlib.util.spec_from_file_location("pobierz_repozytoria", sciezka)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    assert modul.nazwa_katalogu("Paweł Łęcki", "pl-dev") == "Lecki_Pawel__pl-dev"
    assert modul.nazwa_katalogu("", "x_coder_x") == "x_coder_x"
