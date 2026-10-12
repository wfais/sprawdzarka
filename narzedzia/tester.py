#!/usr/bin/env python
"""Odizolowany proces testujacy: wykonuje zlecenia z katalogu kolejki i zapisuje wyniki.

Uruchamiany bez dostepu do sieci, bazy danych i kluczy (w docker-compose: osobna usluga
z network_mode: none). Nie importuje Django. Gdy dziala jako root, kod studentow uruchamia
na nieuprzywilejowanym koncie (domyslnie nobody, uid 65534), ktore nie moze pisac w kolejce.

Uzycie: python narzedzia/tester.py /sciezka/do/kolejki
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kurs.uruchamianie import obsluz_kolejke  # noqa: E402


def main():
    kolejka = Path(sys.argv[1] if len(sys.argv) > 1 else "dane/kolejka")
    kolejka.mkdir(parents=True, exist_ok=True)
    uid = int(os.environ.get("SPRAWDZARKA_UID_TESTOW", "65534"))
    root = os.name == "posix" and os.geteuid() == 0
    print(f"Tester czeka na zlecenia w {kolejka}" + (f", kod studentow jako uid {uid}" if root else
          " (UWAGA: bez uprawnien root kod studentow dziala na tym samym koncie co tester)"), flush=True)
    while True:
        nazwa = obsluz_kolejke(kolejka, uid if root else None)
        if nazwa:
            print(f"Wykonano {nazwa}", flush=True)
        else:
            time.sleep(1)


if __name__ == "__main__":
    main()
