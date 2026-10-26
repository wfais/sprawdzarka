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
