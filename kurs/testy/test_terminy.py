from datetime import datetime, timedelta, timezone
from decimal import Decimal

from kurs import terminy
from kurs.terminy import Push

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
MIEKKI = T0 + timedelta(days=7)
TWARDY = T0 + timedelta(days=14)
BAZA, START = "b" * 40, "c" * 40


def zmiany_kodu(pary_z_kodem):
    """Funkcja porownujaca: kod zmienil sie tylko miedzy podanymi parami stanow (w dowolnej kolejnosci)."""
    stany_kodu = {}

    def kod(sha):
        return stany_kodu.get(sha, "pusty")

    for sha, wersja in pary_z_kodem.items():
        stany_kodu[sha] = wersja
    return lambda od, do: kod(od) != kod(do)


def oblicz(pushe, teraz, kod):
    return terminy.oblicz(pushe, BAZA, START, MIEKKI, TWARDY, teraz, kod)


def test_brak_wypchniec_po_terminie_daje_szary():
    wynik = oblicz([], TWARDY + timedelta(days=1), zmiany_kodu({}))
    assert wynik.kolor == terminy.SZARY
    assert wynik.sugerowany_mnoznik is None


def test_przed_terminem_ze_zmianami_niebieski():
    pushe = [Push(T0 + timedelta(days=1), "1" * 40)]
    wynik = oblicz(pushe, T0 + timedelta(days=2), zmiany_kodu({"1" * 40: "v1"}))
    assert wynik.kolor == terminy.NIEBIESKI
    assert wynik.sha_oceniany == "1" * 40


def test_w_terminie_zielony():
    pushe = [Push(T0 + timedelta(days=1), "1" * 40)]
    wynik = oblicz(pushe, TWARDY + timedelta(days=1), zmiany_kodu({"1" * 40: "v1"}))
    assert wynik.kolor == terminy.ZIELONY
    assert wynik.sugerowany_mnoznik == Decimal("1")
    assert wynik.sha_oceniany == "1" * 40


def test_zmiana_tylko_readme_po_terminie_nadal_zielony():
    pushe = [Push(T0 + timedelta(days=1), "1" * 40), Push(MIEKKI + timedelta(hours=2), "2" * 40)]
    # stan 2 ma ten sam kod co stan 1 (zmienil sie tylko README)
    wynik = oblicz(pushe, TWARDY + timedelta(days=1), zmiany_kodu({"1" * 40: "v1", "2" * 40: "v1"}))
    assert wynik.kolor == terminy.ZIELONY


def test_zmiana_kodu_po_terminie_zolty():
    pushe = [Push(T0 + timedelta(days=1), "1" * 40), Push(MIEKKI + timedelta(hours=2), "2" * 40)]
    wynik = oblicz(pushe, TWARDY + timedelta(days=1), zmiany_kodu({"1" * 40: "v1", "2" * 40: "v2"}))
    assert wynik.kolor == terminy.ZOLTY
    assert wynik.sugerowany_mnoznik == Decimal("0.8")
    assert wynik.sha_oceniany == "2" * 40


def test_calosc_po_terminie_przed_ostatecznym_zolty():
    pushe = [Push(MIEKKI + timedelta(days=1), "1" * 40)]
    wynik = oblicz(pushe, TWARDY + timedelta(days=1), zmiany_kodu({"1" * 40: "v1"}))
    assert wynik.kolor == terminy.ZOLTY


def test_zmiany_po_ostatecznym_przy_wczesniejszym_rozwiazaniu_pomaranczowy():
    pushe = [Push(T0 + timedelta(days=1), "1" * 40), Push(TWARDY + timedelta(hours=1), "2" * 40)]
    wynik = oblicz(pushe, TWARDY + timedelta(days=1), zmiany_kodu({"1" * 40: "v1", "2" * 40: "v2"}))
    assert wynik.kolor == terminy.POMARANCZOWY
    assert wynik.sugerowany_mnoznik == Decimal("1")
    assert wynik.sha_oceniany == "1" * 40


def test_kod_tylko_po_ostatecznym_czerwony():
    pushe = [Push(TWARDY + timedelta(hours=1), "1" * 40)]
    wynik = oblicz(pushe, TWARDY + timedelta(days=1), zmiany_kodu({"1" * 40: "v1"}))
    assert wynik.kolor == terminy.CZERWONY
    assert wynik.sugerowany_mnoznik == Decimal("0")


def test_miedzy_terminami_bez_zmian_przed_terminem_zolty():
    pushe = [Push(MIEKKI + timedelta(hours=3), "1" * 40)]
    wynik = oblicz(pushe, MIEKKI + timedelta(days=1), zmiany_kodu({"1" * 40: "v1"}))
    assert wynik.kolor == terminy.ZOLTY


def test_cofniecie_zmiany_po_terminie_nie_karze():
    # Po terminie student zmienil kod, a potem przywrocil stan sprzed terminu
    pushe = [
        Push(T0 + timedelta(days=1), "1" * 40),
        Push(MIEKKI + timedelta(hours=1), "2" * 40),
        Push(MIEKKI + timedelta(hours=2), "3" * 40),
    ]
    wynik = oblicz(pushe, TWARDY + timedelta(days=1), zmiany_kodu({"1" * 40: "v1", "2" * 40: "v2", "3" * 40: "v1"}))
    assert wynik.kolor == terminy.ZIELONY


def test_kolejnosc_wypchniec_nie_ma_znaczenia_na_wejsciu():
    pushe = [Push(MIEKKI + timedelta(hours=2), "2" * 40), Push(T0 + timedelta(days=1), "1" * 40)]
    wynik = oblicz(pushe, TWARDY + timedelta(days=1), zmiany_kodu({"1" * 40: "v1", "2" * 40: "v2"}))
    assert wynik.kolor == terminy.ZOLTY


def test_wzorce_plikow():
    assert terminy.pasuje_do_wzorcow("ZADANIE1/main.py", ["*.py"])
    assert terminy.pasuje_do_wzorcow("Main.PY", ["*.py"])
    assert not terminy.pasuje_do_wzorcow("README.md", ["*.py"])
    assert terminy.pasuje_do_wzorcow("src/a.ipynb", ["*.py", "*.ipynb"])
    assert terminy.pasuje_do_wzorcow("src/a.txt", ["src/*"])
    assert not terminy.pasuje_do_wzorcow("inne/a.txt", ["src/*"])
