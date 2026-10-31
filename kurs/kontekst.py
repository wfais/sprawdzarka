"""Dodatki do kontekstu szablonow."""
from pathlib import Path

PLIKI_STATYCZNE = [Path(__file__).parent / "static" / "kurs" / nazwa for nazwa in ("styl.css", "panel.js")]


def statyka(request):
    """Wersja arkusza stylow i skryptu (czas zmiany pliku): po aktualizacji przegladarka pobiera nowe pliki."""
    try:
        wersja = int(max(p.stat().st_mtime for p in PLIKI_STATYCZNE))
    except OSError:
        wersja = 0
    return {"wersja_statyki": wersja}


# Pozycja menu panelu dla adresu strony (przedrostek adresu, pozycja)
SEKCJE_PANELU = [
    ("/panel/uczelnie", "uczelnie"), ("/panel/lata", "uczelnie"), ("/panel/struktura", "uczelnie"),
    ("/panel/przedmioty", "przedmioty"), ("/panel/kursy", "przedmioty"),
    ("/panel/grupy", "grupy"),
    ("/panel/studenci", "studenci"),
    ("/panel/zestawy", "zestawy"), ("/panel/przydzialy", "zestawy"), ("/panel/zgloszenia", "zestawy"),
    ("/panel/poprawki", "zestawy"),
    ("/panel/oceny", "oceny"), ("/panel/kalibracja", "oceny"), ("/panel/ewaluacje", "oceny"),
    ("/panel/ustawienia", "ustawienia"), ("/panel/administracja", "ustawienia"), ("/panel/modele", "ustawienia"),
    ("/panel/poligon", "ustawienia"),
]


def sekcja_panelu(request):
    """Ktora pozycja menu panelu jest podswietlona."""
    sciezka = request.path
    if sciezka == "/panel/":
        return {"sekcja_panelu": "pulpit"}
    for przedrostek, sekcja in SEKCJE_PANELU:
        if sciezka.startswith(przedrostek):
            return {"sekcja_panelu": sekcja}
    return {"sekcja_panelu": ""}
