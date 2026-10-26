"""Wyznaczanie stanu terminow na podstawie historii wypchniec.

Liczy sie czas wypchniecia zarejestrowany przez GitHub, a nie data commita
(te student moze dowolnie ustawic). Porownywane sa wylacznie pliki z kodem.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fnmatch import fnmatchcase
from typing import Callable, Optional, Sequence

ZERO_SHA = "0" * 40

SZARY = "szary"
NIEBIESKI = "niebieski"
ZIELONY = "zielony"
ZOLTY = "zolty"
POMARANCZOWY = "pomaranczowy"
CZERWONY = "czerwony"


def pasuje_do_wzorcow(sciezka: str, wzorce: Sequence[str]) -> bool:
    """Wzorzec bez ukosnika dopasowuje sama nazwe pliku (w dowolnym katalogu), z ukosnikiem cala sciezke."""
    nazwa = sciezka.rsplit("/", 1)[-1]
    for wzorzec in wzorce:
        cel = sciezka if "/" in wzorzec else nazwa
        if fnmatchcase(cel.lower(), wzorzec.lower()):
            return True
    return False


@dataclass(frozen=True)
class Push:
    czas: datetime
    sha_po: str
    sha_przed: str = ""
    # False: wypchniecie z innego konta niz konto studenta (np. prowadzacego); nie liczy sie do terminow
    wlasny: bool = True


@dataclass
class WynikTerminow:
    kolor: str
    opis: str
    sugerowany_mnoznik: Optional[Decimal]
    sha_oceniany: str
    sha_miekki: str
    sha_twardy: str
    sha_teraz: str


def stan_na(pushe: Sequence[Push], chwila: datetime, domyslny: str) -> str:
    """Stan galezi (sha) w danej chwili: ostatnie wypchniecie nie pozniejsze niz chwila."""
    sha = domyslny
    for push in pushe:
        if push.czas > chwila:
            break
        if push.sha_po and push.sha_po != ZERO_SHA:
            sha = push.sha_po
    return sha


def _liczba(wartosc: Decimal) -> str:
    return format(wartosc.normalize(), "f").replace(".", ",")


def oblicz(
    pushe: Sequence[Push],
    sha_bazowy: str,
    sha_poczatkowy: str,
    termin_miekki: datetime,
    termin_twardy: datetime,
    teraz: datetime,
    kod_zmieniony: Callable[[str, str], bool],
    mnoznik_spoznienia: Decimal = Decimal("0.8"),
) -> WynikTerminow:
    """Wyznacza kolor, opis, sugerowany mnoznik i oceniany stan repozytorium.

    sha_bazowy      - stan startowy (galaz feedback), od niego liczymy "czy cos oddano"
    sha_poczatkowy  - pierwszy stan galezi main (przed jakimkolwiek wypchnieciem studenta)
    kod_zmieniony   - funkcja (sha_od, sha_do) -> czy zmienil sie jakis plik z kodem

    Wypchniecia z innych kont (wlasny=False, np. poprawki prowadzacego) zmieniaja stan repozytorium,
    ale nie licza sie jako praca studenta: zmiana w danym okresie musi pochodzic z wypchniecia studenta
    (porownanie stanu tuz przed jego wypchnieciem ze stanem po nim).
    """
    wszystkie = sorted((p for p in pushe if p.sha_po and p.sha_po != ZERO_SHA), key=lambda p: p.czas)
    pushe = [p for p in wszystkie if p.wlasny]
    sa_obce = len(pushe) != len(wszystkie)
    sha_m = stan_na(wszystkie, termin_miekki, sha_poczatkowy)
    sha_t = stan_na(wszystkie, termin_twardy, sha_poczatkowy)
    sha_n = wszystkie[-1].sha_po if wszystkie else sha_poczatkowy

    def zmiana(od: str, do: str) -> bool:
        return od != do and kod_zmieniony(od, do)

    def od_studenta(od_czasu: Optional[datetime], do_czasu: Optional[datetime]) -> bool:
        """Czy w okresie (od_czasu, do_czasu] student sam zmienil kod. Bez obcych wypchniec zawsze prawda."""
        if not sa_obce:
            return True
        poprzedni = sha_poczatkowy
        for p in pushe:
            przed = p.sha_przed if p.sha_przed and p.sha_przed != ZERO_SHA else poprzedni
            w_okresie = (od_czasu is None or p.czas > od_czasu) and (do_czasu is None or p.czas <= do_czasu)
            if w_okresie and zmiana(przed, p.sha_po):
                return True
            poprzedni = p.sha_po
        return False

    def wynik(kolor, opis, mnoznik, oceniany):
        return WynikTerminow(kolor, opis, mnoznik, oceniany, sha_m, sha_t, sha_n)

    if teraz < termin_miekki:
        if zmiana(sha_bazowy, sha_n) and od_studenta(None, None):
            return wynik(NIEBIESKI, "Przed terminem, są już zmiany w kodzie.", None, sha_n)
        return wynik(SZARY, "Przed terminem, brak zmian w kodzie.", None, "")

    kod_do_terminu_ostatecznego = zmiana(sha_bazowy, sha_t) and od_studenta(None, termin_twardy)
    po_terminie_ostatecznym = zmiana(sha_t, sha_n) and od_studenta(termin_twardy, None)

    if not kod_do_terminu_ostatecznego:
        if po_terminie_ostatecznym:
            return wynik(CZERWONY, "Kod oddany wyłącznie po terminie ostatecznym.", Decimal("0"), sha_n)
        return wynik(SZARY, "Brak oddanego kodu.", None, "")

    if zmiana(sha_m, sha_t) and od_studenta(termin_miekki, termin_twardy):
        kolor = ZOLTY
        opis = f"Zmiany w kodzie po terminie (sugerowany mnożnik {_liczba(mnoznik_spoznienia)})."
        mnoznik = mnoznik_spoznienia
    else:
        kolor, opis, mnoznik = ZIELONY, "Oddane w terminie.", Decimal("1")

    if po_terminie_ostatecznym:
        kolor = POMARANCZOWY
        opis += " Są też zmiany po terminie ostatecznym, oceniany jest stan z tego terminu."

    return wynik(kolor, opis, mnoznik, sha_t)
