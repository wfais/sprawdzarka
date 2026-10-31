"""Stany wyswietlane diodami: praca studenta oraz zestaw w grupie."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from . import terminy

# Po tylu dniach od terminu ostatecznego nieocenione prace sa sygnalizowane jako zalegle
DNI_NA_OCENY = 7


@dataclass(frozen=True)
class Stan:
    klucz: str
    etykieta: str
    # Kolor diody (klasa CSS): niebieski, zielony, zolty, pomaranczowy, czerwony, fiolet, wylaczona
    dioda: str
    opis: str = ""


# --- Praca studenta ---

PRACE = [
    Stan("otwarte", "otwarte", "niebieski", "Repozytorium utworzone, jeszcze bez zmian w kodzie."),
    Stan("w_terminie", "w terminie", "zielony", "Kod oddany przed terminem."),
    Stan("przeterminowane", "przeterminowane", "pomaranczowy",
         "Zmiany w kodzie po terminie, ale przed terminem ostatecznym."),
    Stan("po_terminie", "po terminie", "czerwony", "Kod oddany wyłącznie po terminie ostatecznym."),
    Stan("nie_oddano", "nie oddano", "wylaczona", "Termin minął, brak zmian w kodzie."),
    Stan("tworzenie", "tworzenie repozytorium", "niebieski", "Repozytorium studenta jest tworzone."),
    Stan("blad", "błąd", "czerwony", "Nie udało się utworzyć repozytorium."),
]
PRACA = {s.klucz: s for s in PRACE}


def praca(status: str, kolor: str, mnoznik: Optional[Decimal], termin_miekki: datetime, teraz: datetime) -> Stan:
    """Stan pracy z wyniku przeliczenia terminow (uwzglednia indywidualne terminy studenta)."""
    if status == "tworzenie":
        return PRACA["tworzenie"]
    if status == "blad":
        return PRACA["blad"]
    if kolor in (terminy.NIEBIESKI, terminy.ZIELONY):
        return PRACA["w_terminie"]
    if kolor == terminy.ZOLTY:
        return PRACA["przeterminowane"]
    if kolor == terminy.POMARANCZOWY:
        # Oddane do terminu ostatecznego, a potem dalsze zmiany: diode wyznacza oceniany stan
        return PRACA["w_terminie"] if mnoznik is not None and mnoznik >= 1 else PRACA["przeterminowane"]
    if kolor == terminy.CZERWONY:
        return PRACA["po_terminie"]
    return PRACA["otwarte"] if teraz < termin_miekki else PRACA["nie_oddano"]


# --- Zestaw w grupie ---

ZESTAWY = [
    Stan("nieopublikowany", "publikacja", "czerwony", "Zestaw gotowy, link dla studentów jeszcze nie działa."),
    Stan("trwa", "aktywny", "niebieski", "Zestaw opublikowany, termin jeszcze nie minął."),
    Stan("po_terminie", "minął termin", "zolty", "Minął termin, trwa termin ostateczny."),
    Stan("do_oceny", "do oceny", "zielony", "Minął termin ostateczny, prace czekają na ocenę."),
    Stan("zalegle", "zaległe oceny", "fiolet",
         f"Od terminu ostatecznego minęło ponad {DNI_NA_OCENY} dni, a nie wszystkie prace są ocenione."),
    Stan("zakonczony", "ocenione", "wylaczona", "Wszystkie oddane prace są ocenione."),
]
ZESTAW = {s.klucz: s for s in ZESTAWY}


def zestaw(publikacja: Optional[datetime], termin_miekki: datetime, termin_twardy: datetime, teraz: datetime,
           do_oceny: int) -> Stan:
    """do_oceny: liczba oddanych prac, ktorych ocena nie zostala jeszcze wyslana studentowi."""
    if publikacja and teraz < publikacja:
        return ZESTAW["nieopublikowany"]
    if teraz < termin_miekki:
        return ZESTAW["trwa"]
    if teraz < termin_twardy:
        return ZESTAW["po_terminie"]
    if not do_oceny:
        return ZESTAW["zakonczony"]
    if teraz >= termin_twardy + timedelta(days=DNI_NA_OCENY):
        return ZESTAW["zalegle"]
    return ZESTAW["do_oceny"]
