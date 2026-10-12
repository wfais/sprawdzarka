"""Heurystyczne odczytanie imienia i nazwiska z README studenta.

Wynik jest tylko kandydatem: prowadzacy zatwierdza go jednym kliknieciem w panelu.
"""
from __future__ import annotations

import re
from typing import Optional

_ETYKIETA = re.compile(
    r"^[ \t]*(?:[-*+>#]+[ \t]*)*(?:\*\*|__)?[ \t]*"
    r"(?:imi[eę][ \t]*(?:i|oraz|&)?[ \t]*nazwisko|nazwisko(?:[ \t]*i[ \t]*imi[eę])?"
    r"|autor(?:ka)?|student(?:ka)?|wykona[łl](?:a)?|author|name)"
    r"[ \t]*(?:\*\*|__)?[ \t]*[:=\-\N{EN DASH}\N{EM DASH}][ \t]*(?P<wartosc>[^\n]+)$",
    re.IGNORECASE | re.MULTILINE,
)

_SLOWO = re.compile(r"^[^\W\d_]+(?:-[^\W\d_]+)*$")

_ZASLEPKI = {
    "imie", "imię", "nazwisko", "wpisz", "tutaj", "todo", "xxx", "student", "studentka",
    "autor", "autorka", "name", "surname", "your", "twoje", "twoj", "twój", "brak",
    "uzupelnij", "uzupełnij", "podaj", "tu",
}


def _wielka_litera(slowo: str) -> str:
    return "-".join(czesc[:1].upper() + czesc[1:].lower() for czesc in slowo.split("-"))


def _oczysc(wartosc: str) -> Optional[str]:
    tekst = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", wartosc)
    tekst = re.split(
        r"[,;|(\[]|\s[-\N{EN DASH}\N{EM DASH}]\s|\s+grupa\b|\s+gr\.|\s+nr\b|\s+album\b|\s+@",
        tekst, maxsplit=1, flags=re.IGNORECASE,
    )[0]
    tekst = re.sub(r"[*_`~<>\"']", " ", tekst)
    slowa = [s.strip(".:") for s in tekst.split()]
    slowa = [s for s in slowa if s]
    if not 2 <= len(slowa) <= 4:
        return None
    if not all(_SLOWO.match(s) for s in slowa):
        return None
    if any(s.lower() in _ZASLEPKI for s in slowa):
        return None
    if all(s.islower() or s.isupper() for s in slowa):
        slowa = [_wielka_litera(s) for s in slowa]
    return " ".join(slowa)


def wyciagnij_nazwisko(tekst: str) -> Optional[str]:
    """Zwraca imie i nazwisko z pierwszej pasujacej linii z etykieta albo None."""
    for dopasowanie in _ETYKIETA.finditer(tekst or ""):
        kandydat = _oczysc(dopasowanie.group("wartosc"))
        if kandydat:
            return kandydat
    return None
