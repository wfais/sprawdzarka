#!/usr/bin/env python
"""Pobiera (lub aktualizuje) repozytoria studentow na dysk i opcjonalnie uruchamia ich programy.

Liste repozytoriow pobierzesz z panelu: strona zestawu w grupie, przycisk "Lista repozytoriow".
Skrypt korzysta z Twojego zwyklego dostepu do GitHub (klucz SSH albo menedzer hasel git).

Przyklady (Git Bash w Windows):
  python narzedzia/pobierz_repozytoria.py py-z03-g4.txt zestaw3_g4
  python narzedzia/pobierz_repozytoria.py py-z03-g4.txt zestaw3_g4 --ssh-host wp_git
  python narzedzia/pobierz_repozytoria.py py-z03-g4.txt zestaw3_g4 --stan oceniany
  python narzedzia/pobierz_repozytoria.py py-z03-g4.txt zestaw3_g4 --uruchom ZADANIE2/gra.py
"""
import argparse
import re
import subprocess
import sys
import unicodedata
from pathlib import Path


def nazwa_katalogu(imie_nazwisko: str, login: str) -> str:
    """Nazwisko_Imie__login bez polskich znakow (wygodne sortowanie i sciezki w Windows)."""
    czesci = imie_nazwisko.split()
    if czesci:
        czesci = [czesci[-1], *czesci[:-1]]
    tekst = "_".join(czesci + ["", login]) if czesci else login
    tekst = unicodedata.normalize("NFKD", tekst.replace("ł", "l").replace("Ł", "L"))
    tekst = "".join(z for z in tekst if not unicodedata.combining(z))
    return re.sub(r"[^A-Za-z0-9_.-]", "_", tekst)


def git(*argumenty, katalog=None):
    wynik = subprocess.run(["git", *argumenty], cwd=katalog, capture_output=True, text=True)
    if wynik.returncode != 0:
        raise RuntimeError((wynik.stderr or wynik.stdout).strip())
    return wynik.stdout.strip()


def wczytaj_liste(sciezka: Path):
    for linia in sciezka.read_text(encoding="utf-8").splitlines():
        if not linia.strip() or linia.startswith("#"):
            continue
        pola = (linia.split("\t") + [""] * 5)[:5]
        yield {"repo": pola[0], "imie_nazwisko": pola[1], "login": pola[2], "stan": pola[3], "sha": pola[4]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("lista", type=Path, help="Plik z listą repozytoriów pobrany z panelu.")
    parser.add_argument("katalog", type=Path, help="Katalog docelowy.")
    parser.add_argument("--ssh-host", help="Alias hosta SSH z ~/.ssh/config, np. wp_git (domyślnie HTTPS).")
    parser.add_argument("--stan", choices=["najnowszy", "oceniany"], default="najnowszy",
                        help="najnowszy: ostatni stan gałęzi main; oceniany: stan wskazany przez terminy.")
    parser.add_argument("--uruchom", help="Ścieżka programu w repozytorium, uruchamianego po kolei u każdego studenta.")
    argumenty = parser.parse_args()

    argumenty.katalog.mkdir(parents=True, exist_ok=True)
    pobrane = []
    for wpis in wczytaj_liste(argumenty.lista):
        adres = (f"git@{argumenty.ssh_host}:{wpis['repo']}.git" if argumenty.ssh_host
                 else f"https://github.com/{wpis['repo']}.git")
        cel = argumenty.katalog / nazwa_katalogu(wpis["imie_nazwisko"], wpis["login"])
        try:
            if (cel / ".git").is_dir():
                git("fetch", "--quiet", "origin", katalog=cel)
            else:
                git("clone", "--quiet", adres, str(cel))
            if argumenty.stan == "oceniany" and wpis["sha"]:
                git("checkout", "--quiet", wpis["sha"], katalog=cel)
            else:
                git("checkout", "--quiet", "main", katalog=cel)
                git("merge", "--quiet", "--ff-only", "origin/main", katalog=cel)
            print(f"[ok] {cel.name}  ({wpis['stan']})")
            pobrane.append((cel, wpis))
        except RuntimeError as blad:
            print(f"[BLAD] {wpis['repo']}: {blad}", file=sys.stderr)

    if argumenty.uruchom:
        for cel, wpis in pobrane:
            program = cel / argumenty.uruchom
            if not program.is_file():
                print(f"[brak] {cel.name}: nie ma {argumenty.uruchom}")
                continue
            input(f"\nEnter: uruchom program studenta {wpis['imie_nazwisko'] or wpis['login']} ({cel.name})")
            subprocess.run([sys.executable, str(program)], cwd=program.parent)


if __name__ == "__main__":
    main()
