"""Poligon: fikcyjny rok, przedmioty, grupy, studenci, wypchniecia i oceny do prob panelu, bez polaczenia z GitHubem.

Wszystko trafia do roku oznaczonego jako poligon (numery kont i repozytoriow sa ujemne, wiec nie koliduja
z GitHubem). usun() kasuje poligon w calosci, razem z fikcyjnymi kontami studentow.
"""
from __future__ import annotations

import random
import unicodedata
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import ocena, uslugi
from .archiwum import usun as usun_rok
from .models import (
    Ewaluacja, Grupa, Kurs, OcenaZadania, OpiniaModelu, Porownanie, Przydzial, Rok, Student, Uczelnia, Wypchniecie,
    Zadanie, Zestaw, Zgloszenie,
)

D = Decimal
NAZWA_ROKU = "2026/2027 poligon"
SKROT_ROKU = "poligon"
KONTO_PROWADZACEGO = -1
MODELE = ("symulacja:model-a", "symulacja:model-b", "symulacja:model-c")
T, TM, M, R = Zadanie.Tryb.TESTY, Zadanie.Tryb.TESTY_I_MODELE, Zadanie.Tryb.MODELE, Zadanie.Tryb.RECZNIE

KURSY = [
    {
        "skrot": "py", "nazwa": "Język Python", "kod": "WFAIS.IF-X204.0", "plik": "zadanie{n}.py",
        "grupy": [
            ("Grupa nr 3", "g3", "poniedziałek 10:10 - 11:40, sala G-1-03", 15),
            ("Grupa nr 4", "g4", "wtorek 8:00 - 9:30, sala G-1-03", 14),
            ("Grupa nr 6", "g6", "czwartek 12:00 - 13:30, sala G-0-11", 16),
        ],
        "zestawy": [
            {"skrot": "k1", "nazwa": "Kolokwium 1: podstawy składni", "kolokwium": True, "etap": "ocenione",
             "punkty": 10, "zadania": [("ZADANIE1", 4, TM), ("ZADANIE2", 3, TM), ("ZADANIE3", 3, M)]},
            {"skrot": "z01", "nazwa": "Zestaw 1: listy i słowniki", "etap": "po_terminie", "punkty": 5,
             "zadania": [("ZADANIE1", 1.25, TM), ("ZADANIE2", 1.25, TM), ("ZADANIE3", 1.25, TM), ("ZADANIE4", 1.25, M)]},
            {"skrot": "z02", "nazwa": "Zestaw 2: funkcje i wyjątki", "etap": "trwa", "punkty": 5,
             "zadania": [("ZADANIE1", 1, T), ("ZADANIE2", 1.5, TM), ("ZADANIE3", 1.5, TM), ("ZADANIE4", 1, R)]},
            {"skrot": "z03", "nazwa": "Zestaw 3: klasy i dziedziczenie", "etap": "nieopublikowany", "punkty": 5,
             "zadania": [("ZADANIE1", 2, TM), ("ZADANIE2", 2, TM), ("ZADANIE3", 1, M)]},
        ],
    },
    {
        "skrot": "cpp", "nazwa": "Język C++", "kod": "WFAIS.IF-A207.0", "plik": "main{n}.cpp",
        "wzorce": "*.cpp, *.h, *.hpp",
        "grupy": [
            ("Grupa nr 1", "g1", "środa 9:45 - 11:15, sala A-2-15", 13),
            ("Grupa nr 8", "g8", "piątek 11:30 - 13:00, sala A-2-15", 14),
        ],
        "zestawy": [
            {"skrot": "z01", "nazwa": "Zestaw 1: wskaźniki i pamięć", "etap": "zalegle", "punkty": 6,
             "zadania": [("ZADANIE1", 2, TM), ("ZADANIE2", 2, M), ("ZADANIE3", 2, R)]},
            {"skrot": "z02", "nazwa": "Zestaw 2: klasy i przeciążanie operatorów", "etap": "do_oceny", "punkty": 6,
             "zadania": [("ZADANIE1", 2, TM), ("ZADANIE2", 2, TM), ("ZADANIE3", 2, M)]},
        ],
    },
]

IMIONA_K = ["Anna", "Katarzyna", "Magdalena", "Julia", "Zofia", "Natalia", "Aleksandra", "Wiktoria", "Maja", "Oliwia",
            "Zuzanna", "Hanna", "Karolina", "Weronika", "Agnieszka", "Paulina", "Emilia", "Marta", "Joanna", "Gabriela"]
IMIONA_M = ["Jakub", "Mateusz", "Kacper", "Szymon", "Filip", "Michał", "Bartosz", "Wojciech", "Tomasz", "Piotr",
            "Adam", "Kamil", "Dawid", "Hubert", "Patryk", "Igor", "Oskar", "Marcin", "Paweł", "Krzysztof"]
NAZWISKA = [("Nowak", "Nowak"), ("Kowalski", "Kowalska"), ("Wiśniewski", "Wiśniewska"), ("Wójcik", "Wójcik"),
            ("Kowalczyk", "Kowalczyk"), ("Kamiński", "Kamińska"), ("Lewandowski", "Lewandowska"),
            ("Zieliński", "Zielińska"), ("Szymański", "Szymańska"), ("Woźniak", "Woźniak"), ("Dąbrowski", "Dąbrowska"),
            ("Kozłowski", "Kozłowska"), ("Jankowski", "Jankowska"), ("Mazur", "Mazur"), ("Kwiatkowski", "Kwiatkowska"),
            ("Krawczyk", "Krawczyk"), ("Piotrowski", "Piotrowska"), ("Grabowski", "Grabowska"),
            ("Nowakowski", "Nowakowska"), ("Pawłowski", "Pawłowska"), ("Michalski", "Michalska"), ("Król", "Król"),
            ("Wieczorek", "Wieczorek"), ("Jabłoński", "Jabłońska"), ("Wróbel", "Wróbel"), ("Majewski", "Majewska"),
            ("Olszewski", "Olszewska"), ("Stępień", "Stępień"), ("Malinowski", "Malinowska"), ("Jaworski", "Jaworska")]

KOMENTARZE = {
    "py": {
        "dobre": ["Rozwiazanie poprawne i czytelne.", "Dziala zgodnie z trescia, dobre nazwy zmiennych.",
                  "Poprawne, warto dodac obsluge pustych danych."],
        "srednie": ["Czesciowo poprawne: brak obslugi przypadkow brzegowych.",
                    "Logika w porzadku, ale funkcja zwraca zly typ dla pustej listy.",
                    "Dziala dla przykladow z tresci, nie dziala dla liczb ujemnych."],
        "slabe": ["Rozwiazanie niekompletne, brak wymaganej funkcji.",
                  "Wynik jest wypisywany zamiast zwracany, testy nie moga go sprawdzic.",
                  "Program konczy sie wyjatkiem dla poprawnych danych."],
    },
    "cpp": {
        "dobre": ["Poprawne uzycie wskaznikow, pamiec zwalniana.", "Klasa dobrze zaprojektowana, const tam gdzie trzeba.",
                  "Dziala poprawnie, czytelny podzial na funkcje."],
        "srednie": ["Dziala, ale brak kontroli zakresu tablicy.", "Operator przypisania bez obslugi samoprzypisania.",
                    "Poprawna logika, brak destruktora wirtualnego w klasie bazowej."],
        "slabe": ["Wyciek pamieci: brak delete[] po new[].", "Program nie kompiluje sie bez ostrzezen.",
                  "Niezdefiniowane zachowanie: odczyt poza tablica."],
    },
}

# Udzial prac ocenionych automatycznie i wyslanych studentom w zaleznosci od etapu zestawu
OCENIANIE = {"po_terminie": (0.25, 0.0), "do_oceny": (0.8, 0.35), "zalegle": (0.9, 0.5), "ocenione": (1.0, 1.0)}


class BladPoligonu(Exception):
    pass


def istnieje() -> bool:
    return Rok.objects.filter(poligon=True).exists()


# --- Pomocnicze ---

def _ascii(tekst: str) -> str:
    tekst = tekst.replace("ł", "l").replace("Ł", "L")
    return unicodedata.normalize("NFKD", tekst).encode("ascii", "ignore").decode().lower()


def _o_godzinie(dzien: datetime, godzina: int, minuta: int) -> datetime:
    lokalny = timezone.localtime(dzien)
    return timezone.make_aware(datetime.combine(lokalny.date(), time(godzina, minuta)))


def _terminy(etap: str, przesuniecie: int, teraz: datetime) -> tuple[datetime, datetime, datetime]:
    """(publikacja, termin, termin ostateczny) dla etapu zestawu; grupy roznia sie o dzien."""
    s = przesuniecie

    def dzien(dni, godzina=23, minuta=59):
        return _o_godzinie(teraz + timedelta(days=dni), godzina, minuta)

    if etap == "nieopublikowany":
        return dzien(4 + s, 8, 0), dzien(14 + s), dzien(21 + s)
    if etap == "trwa":
        return dzien(-6 - s, 8, 0), dzien(3 + s), dzien(10 + s)
    if etap == "po_terminie":
        return dzien(-13 - s, 8, 0), dzien(-2 - s), dzien(5 - s)
    if etap == "do_oceny":
        return dzien(-17 - s, 8, 0), dzien(-10 - s), dzien(-3 - s)
    if etap == "zalegle":
        return dzien(-28 - s, 8, 0), dzien(-21 - s), dzien(-14 - s)
    # Kolokwium: 90 minut pisania i 10 minut na wypchniecie
    start = dzien(-25 - 2 * s, 10, 10)
    return start, start + timedelta(minutes=90), start + timedelta(minutes=100)


def _miedzy(rng: random.Random, od: datetime, do: datetime) -> datetime:
    sekundy = max(int((do - od).total_seconds()), 2)
    return od + timedelta(seconds=rng.randint(1, sekundy - 1))


def _jakosc(student: Student) -> float:
    """Staly poziom studenta (0,35..1): wplywa na testy i punkty modeli."""
    los = random.Random(student.github_id).random()
    return round(1 - (los ** 2) * 0.65, 3)


# --- Studenci ---

def _studenci(rng: random.Random, ile: int) -> list[Student]:
    studenci, loginy, osoby = [], set(), set()
    numer = 0
    while len(studenci) < ile:
        kobieta = rng.random() < 0.45
        imie = rng.choice(IMIONA_K if kobieta else IMIONA_M)
        nazwisko = rng.choice(NAZWISKA)[1 if kobieta else 0]
        if (imie, nazwisko) in osoby:
            continue
        osoby.add((imie, nazwisko))
        wzor = rng.randint(0, 5)
        i, n = _ascii(imie), _ascii(nazwisko)
        login = [f"{i[0]}{n}", f"{i}{n[:3]}{rng.randint(10, 99)}", f"{n}-{i[0]}", f"{i}-dev",
                 f"{i}{n}", f"{i[:3]}{n[:3]}"][wzor]
        while login in loginy:
            login += str(rng.randint(0, 9))
        loginy.add(login)
        numer += 1
        nazwa = f"{imie} {nazwisko}"
        student = Student(github_id=-(1000 + numer), login=login)
        if numer % 13 == 5:
            # Imie i nazwisko do zatwierdzenia z README
            student.kandydat_readme = nazwa
        elif numer % 29 == 11:
            pass  # konto bez nazwiska
        else:
            student.imie_nazwisko = nazwa
            student.zrodlo_nazwiska = Student.Zrodlo.FORMULARZ
        studenci.append(student)
    istniejace = {s.github_id: s for s in Student.objects.filter(github_id__in=[s.github_id for s in studenci])}
    wynik = []
    for s in studenci:
        if s.github_id in istniejace:
            wynik.append(istniejace[s.github_id])
        else:
            s.save()
            wynik.append(s)
    return wynik


# --- Prace studentow ---

PROFILE_ETAPU = {
    "trwa": (["otwarte", "w_terminie"], {"otwarte": 4, "w_terminie": 6, "brak": 1}),
    "po_terminie": (["przeterminowane", "tylko_spoznione", "nic", "readme_po", "wyjatek", "testy"],
                    {"w_terminie": 7, "przeterminowane": 2, "nic": 1, "brak": 1}),
    "do_oceny": (["przeterminowane", "po_ostatecznym", "i_po_ostatecznym", "nic", "readme_po", "testy", "obcy"],
                 {"w_terminie": 6, "przeterminowane": 2, "nic": 1, "brak": 1}),
    "zalegle": (["przeterminowane", "po_ostatecznym", "i_po_ostatecznym", "nic", "testy", "obcy"],
                {"w_terminie": 6, "przeterminowane": 2, "nic": 1, "brak": 1}),
    "ocenione": (["przeterminowane", "nic"], {"w_terminie": 9, "przeterminowane": 2, "brak": 1}),
}


def _profile(rng: random.Random, etap: str, ile: int) -> list[str]:
    obowiazkowe, wagi = PROFILE_ETAPU[etap]
    lista = list(obowiazkowe[:ile])
    while len(lista) < ile:
        lista.append(rng.choices(list(wagi), weights=list(wagi.values()))[0])
    rng.shuffle(lista)
    return lista


def _pliki_kodu(spec_kursu: dict, zadania: list, rng: random.Random) -> list[str]:
    wybrane = rng.sample(zadania, k=rng.randint(1, min(2, len(zadania))))
    return [f"{kod}/{spec_kursu['plik'].format(n=kod[-1])}" for kod, _, _ in wybrane]


def _wypchniecia(profil: str, publikacja, miekki, twardy, teraz, rng) -> tuple[datetime, list[tuple[datetime, str]]]:
    """(chwila akceptacji, lista (czas, rodzaj)); rodzaj: kod, readme, testy, obcy."""
    akceptacja = _miedzy(rng, publikacja, min(miekki, teraz) - timedelta(minutes=30))
    przed = min(miekki, teraz)
    pushe = []
    w_terminie = profil in ("w_terminie", "przeterminowane", "i_po_ostatecznym", "readme_po", "testy", "obcy")
    if w_terminie:
        for _ in range(rng.randint(1, 4)):
            pushe.append((_miedzy(rng, akceptacja, przed), "kod"))
    if profil == "testy":
        pushe.append((_miedzy(rng, akceptacja, przed), "testy"))
    if profil in ("przeterminowane", "tylko_spoznione"):
        for _ in range(1 if w_terminie else rng.randint(1, 2)):
            pushe.append((_miedzy(rng, miekki, min(twardy, teraz)), "kod"))
    if profil in ("po_ostatecznym", "i_po_ostatecznym"):
        pushe.append((_miedzy(rng, twardy, teraz), "kod"))
    if profil == "readme_po":
        pushe.append((_miedzy(rng, miekki, teraz), "readme"))
    if profil == "obcy":
        pushe.append((_miedzy(rng, miekki, teraz), "obcy"))
    return akceptacja, sorted(pushe)


def _utworz_prace(przydzial: Przydzial, student: Student, profil: str, spec_kursu: dict, zadania: list,
                  teraz: datetime, rng: random.Random) -> Optional[Zgloszenie]:
    if profil == "brak":
        return None
    publikacja, miekki, twardy = przydzial.publikacja, przydzial.termin_miekki, przydzial.termin_twardy
    repo_nazwa = f"{settings.GITHUB_ORG}/{uslugi.nazwa_repo_studenta(przydzial, student.login)}"
    if profil == "blad":
        z = Zgloszenie.objects.create(
            przydzial=przydzial, student=student, status=Zgloszenie.Status.BLAD, do_przeliczenia=False,
            blad_opis="Symulacja: GitHub nie odpowiedział przy tworzeniu repozytorium (502). Kliknij Spróbuj ponownie.")
        return z
    repo_id = uslugi.nowe_repo_fikcyjne()
    start = uslugi.sha_fikcyjny(repo_nazwa)
    z = Zgloszenie.objects.create(
        przydzial=przydzial, student=student, status=Zgloszenie.Status.GOTOWE, repo_id=repo_id, repo_nazwa=repo_nazwa,
        sha_bazowy=start, sha_poczatkowy=start, zaproszenie_wyslane=True, pr_numer=1, do_przeliczenia=False,
    )
    if profil == "wyjatek":
        z.wyjatek_termin_miekki = _o_godzinie(teraz + timedelta(days=3), 23, 59)
        z.wyjatek_termin_twardy = _o_godzinie(teraz + timedelta(days=10), 23, 59)
        z.save(update_fields=["wyjatek_termin_miekki", "wyjatek_termin_twardy"])
    akceptacja, pushe = _wypchniecia(profil, publikacja, miekki, twardy, teraz, rng)
    Zgloszenie.objects.filter(pk=z.pk).update(utworzono=akceptacja)
    poprzedni = start
    for numer, (czas, rodzaj) in enumerate(pushe, 1):
        sha = uslugi.sha_fikcyjny(f"{repo_id}:{numer}")
        if rodzaj == "kod":
            pliki = _pliki_kodu(spec_kursu, zadania, rng) + (["README.md"] if numer == 1 else [])
        elif rodzaj == "testy":
            pliki = [f"{zadania[0][0]}/tests/test_{zadania[0][0].lower()}.py"]
        else:
            pliki = ["README.md"]
        obcy = rodzaj == "obcy"
        Wypchniecie.objects.create(
            repo_id=repo_id, repo_nazwa=repo_nazwa, ref="refs/heads/main", sha_przed=poprzedni, sha_po=sha, czas=czas,
            autor="prowadzacy" if obcy else student.login, autor_id=KONTO_PROWADZACEGO if obcy else student.github_id,
            zrodlo=Wypchniecie.Zrodlo.WEBHOOK,
        )
        Porownanie.objects.create(repo_id=repo_id, sha_od=poprzedni, sha_do=sha, pliki=pliki)
        poprzedni = sha
    if profil == "testy":
        z.testy_zmienione = True
        z.zmiany_testow = {"zmienione": [f"{zadania[0][0]}/tests/test_{zadania[0][0].lower()}.py"]}
        z.save(update_fields=["testy_zmienione", "zmiany_testow"])
    uslugi.przelicz(z)
    z.refresh_from_db()
    return z


# --- Ocena automatyczna (symulacja) ---

def symuluj_ewaluacje(zgloszenie: Zgloszenie, odcisk: Optional[str] = None, sha: str = "",
                      rng: Optional[random.Random] = None) -> Ewaluacja:
    """Gotowa ocena automatyczna z fikcyjnymi wynikami testow i opiniami trzech modeli."""
    rng = rng or random.Random(f"{zgloszenie.pk}:{timezone.now().timestamp()}")
    zestaw = zgloszenie.przydzial.zestaw
    kurs = "cpp" if zestaw.kurs.skrot == "cpp" else "py"
    jakosc = _jakosc(zgloszenie.student)
    zadania = list(zestaw.zadania.all())
    przypadki, testy_zadan = [], {}
    for z in zadania:
        if z.tryb not in Zadanie.TRYBY_Z_TESTAMI:
            continue
        ile = rng.randint(3, 5)
        zdane = min(ile, sum(rng.random() < jakosc + 0.1 for _ in range(ile)))
        testy_zadan[z.kod] = (zdane, ile)
        for i in range(ile):
            ok = i < zdane
            przypadki.append({
                "nazwa": f"test_{z.kod.lower()}_{i + 1}", "klasa": "", "plik": f"{z.kod}/tests/test_{z.kod.lower()}.py",
                "wynik": "zdany" if ok else "niezdany", "komunikat": "" if ok else f"AssertionError: assert {i + 2} == {i + 3}",
                "powod": "" if ok else f"assert {i + 2} == {i + 3}", "wczytanie": False,
            })
    wszystkie_zdane = all(zd == il for zd, il in testy_zadan.values())
    e = Ewaluacja.objects.create(
        zgloszenie=zgloszenie, sha=sha or zgloszenie.sha_oceniany,
        konfiguracja=odcisk or ocena.odcisk_konfiguracji(zestaw), status=Ewaluacja.Status.GOTOWA,
        testy={"uruchomiono": bool(przypadki), "czas": round(rng.uniform(0.8, 3.5), 1), "przekroczono_czas": False,
               "kod_wyjscia": 0 if wszystkie_zdane else 1, "dziennik": "Symulacja poligonu: testy nie były uruchamiane.",
               "przypadki": przypadki, "powod": "" if przypadki else "zestaw bez zadań z testami"},
        pliki=[{"sciezka": f"{z.kod}/{('main' if kurs == 'cpp' else 'zadanie')}{z.kod[-1]}.{'cpp' if kurs == 'cpp' else 'py'}",
                "nowy": False, "zmieniony": True, "readme": False, "znaki": rng.randint(300, 2500)} for z in zadania],
        zakonczono=timezone.now(),
    )
    do_modeli = [z for z in zadania if z.tryb in (Zadanie.Tryb.TESTY_I_MODELE, Zadanie.Tryb.MODELE)]
    opinie = {}
    for model in MODELE:
        wpisy = []
        for z in do_modeli:
            udzial = jakosc
            if z.kod in testy_zadan:
                zdane, ile = testy_zadan[z.kod]
                udzial = (jakosc + zdane / ile) / 2
            punkty = ocena.zaokraglij(D(str(max(0.0, min(1.0, udzial + rng.gauss(0, 0.06))))) * z.punkty)
            poziom = "dobre" if udzial > 0.8 else ("srednie" if udzial > 0.5 else "slabe")
            komentarz = rng.choice(KOMENTARZE[kurs][poziom])
            uwagi = []
            if poziom != "dobre" and rng.random() < 0.5:
                uwagi.append({"plik": e.pliki[zadania.index(z)]["sciezka"], "linia": rng.randint(3, 40), "tekst": komentarz})
            wpisy.append({"kod": z.kod, "punkty": float(punkty), "uzasadnienie": f"(symulacja) {komentarz}",
                          "komentarz": komentarz, "uwagi": uwagi})
        OpiniaModelu.objects.create(ewaluacja=e, model=model, runda=1, ok=True, dane={"zadania": wpisy},
                                    tokeny_we=rng.randint(4000, 9000), tokeny_wy=rng.randint(400, 1200),
                                    czas_s=round(rng.uniform(8, 40), 1))
        opinie[model] = {"zadania": wpisy}
    agregat = ocena.agreguj({m: ocena.odczytaj_punkty(d) for m, d in opinie.items()},
                            {z.kod: z.punkty for z in do_modeli}, 2)
    suma, sporna = D(0), False
    for z in zadania:
        a = agregat.get(z.kod)
        konsensus, sporne = (a.konsensus, a.sporne) if a else (None, True)
        komentarz = ocena._komentarz_najblizszy(opinie, z.kod, konsensus)
        zdane, ile = testy_zadan.get(z.kod, (None, None))
        if z.tryb == Zadanie.Tryb.TESTY and ile:
            konsensus, sporne = ocena.zaokraglij(z.punkty * zdane / ile), False
            komentarz = f"Testy automatyczne: zdane {zdane} z {ile}."
        OcenaZadania.objects.create(
            ewaluacja=e, zadanie=z, kod=z.kod, punkty_max=z.punkty, tryb=z.tryb, testy_zdane=zdane,
            testy_wszystkie=ile, punkty_konsensus=konsensus, sporne=sporne, komentarz=ocena.bez_ogonkow(komentarz)[:500],
        )
        suma += konsensus or 0
        sporna = sporna or sporne
    e.suma, e.sporna = suma, sporna
    e.komentarz = ocena.zloz_komentarz(e)
    e.tokeny_we = sum(o.tokeny_we for o in e.opinie.all())
    e.tokeny_wy = sum(o.tokeny_wy for o in e.opinie.all())
    e.save()
    return e


def _wyslij(e: Ewaluacja, rng: random.Random, chwila: datetime) -> None:
    """Prowadzacy uzupelnia zadania do decyzji i wysyla ocene (w poligonie tylko w bazie)."""
    jakosc = _jakosc(e.zgloszenie.student)
    for o in e.oceny_zadan.all():
        if o.punkty_konsensus is None or (o.sporne and rng.random() < 0.5):
            o.punkty_prowadzacego = ocena.zaokraglij(D(str(jakosc)) * o.punkty_max)
            o.save(update_fields=["punkty_prowadzacego"])
    if ocena.wyslij_ocene(e) is None:
        Zgloszenie.objects.filter(pk=e.zgloszenie_id).update(oceniono=chwila)


# --- Tworzenie i usuwanie ---

@transaction.atomic
def utworz(teraz: Optional[datetime] = None, ziarno: int = 2026) -> Rok:
    if istnieje():
        raise BladPoligonu("Poligon już istnieje. Usuń go, aby utworzyć nowy.")
    teraz = teraz or timezone.now()
    rng = random.Random(ziarno)
    uj, _ = Uczelnia.objects.get_or_create(skrot="uj", defaults={"nazwa": "Uniwersytet Jagielloński", "kolejnosc": 1})
    potrzeba = sum(g[3] for g in KURSY[0]["grupy"])
    studenci = _studenci(rng, potrzeba + 14)
    pula_py, nowi = studenci[:potrzeba], studenci[potrzeba:]
    rok = Rok.objects.create(uczelnia=uj, nazwa=NAZWA_ROKU, skrot=SKROT_ROKU, poligon=True)
    blad_dodany = False
    for spec in KURSY:
        kurs = Kurs.objects.create(rok=rok, skrot=spec["skrot"], nazwa=spec["nazwa"], kod=spec["kod"])
        if spec["skrot"] == "py":
            sklady, wolni = [], list(pula_py)
            for _, _, _, ile in spec["grupy"]:
                sklady.append(wolni[:ile])
                wolni = wolni[ile:]
        else:
            # Czesc studentow C++ chodzi tez na Pythona
            wspolni = rng.sample(pula_py, 13)
            wszyscy = nowi + wspolni
            rng.shuffle(wszyscy)
            sklady, poczatek = [], 0
            for _, _, _, ile in spec["grupy"]:
                sklady.append(wszyscy[poczatek:poczatek + ile])
                poczatek += ile
        grupy = [Grupa.objects.create(kurs=kurs, nazwa=n, skrot=s, opis=o) for n, s, o, _ in spec["grupy"]]
        for zs in spec["zestawy"]:
            zestaw = Zestaw.objects.create(
                kurs=kurs, skrot=zs["skrot"], nazwa=zs["nazwa"],
                rodzaj=Zestaw.Rodzaj.KOLOKWIUM if zs.get("kolokwium") else Zestaw.Rodzaj.ZESTAW,
                repo_zrodlowe=f"wfais/poligon-{spec['skrot']}-{zs['skrot']}", wzorce_kodu=spec.get("wzorce", "*.py"),
                punkty_max=D(str(zs["punkty"])),
                tresc_tekst="\n".join(f"{kod} ({ocena.liczba(D(str(p)))} pkt): treść fikcyjnego zadania do prób."
                                      for kod, p, _ in zs["zadania"]),
            )
            for kolejnosc, (kod, punkty, tryb) in enumerate(zs["zadania"], 1):
                Zadanie.objects.create(zestaw=zestaw, kolejnosc=kolejnosc, kod=kod, katalog=f"{kod}/",
                                       punkty=D(str(punkty)), tryb=tryb,
                                       kryteria="Rozwiązanie zgodne z treścią, obsługa przypadków brzegowych.")
            for przesuniecie, (grupa, sklad) in enumerate(zip(grupy, sklady)):
                publikacja, miekki, twardy = _terminy(zs["etap"], przesuniecie, teraz)
                przydzial = Przydzial.objects.create(
                    zestaw=zestaw, grupa=grupa, publikacja=publikacja, termin_miekki=miekki, termin_twardy=twardy,
                    przedrostek_repo=Przydzial.domyslny_przedrostek(zestaw, grupa),
                    otwarty=not zs.get("kolokwium"),
                )
                uslugi.utworz_repo_startowe(przydzial)
                if zs["etap"] == "nieopublikowany":
                    continue
                profile = _profile(rng, zs["etap"], len(sklad))
                if zs["etap"] == "trwa" and not blad_dodany:
                    profile[0], blad_dodany = "blad", True
                odcisk = ocena.odcisk_konfiguracji(zestaw)
                p_oceny, p_wyslania = OCENIANIE.get(zs["etap"], (0, 0))
                for student, profil in zip(sklad, profile):
                    z = _utworz_prace(przydzial, student, profil, spec, zs["zadania"], teraz, rng)
                    if z is None or not z.sha_oceniany or rng.random() >= p_oceny:
                        continue
                    if profil == "przeterminowane" and zs["etap"] == "po_terminie":
                        # Ocena zrobiona przed spoznionym wypchnieciem: nieaktualna
                        przed = (Wypchniecie.objects.filter(repo_id=z.repo_id, czas__lte=miekki)
                                 .order_by("-czas").values_list("sha_po", flat=True).first())
                        symuluj_ewaluacje(z, odcisk, sha=przed or z.sha_oceniany, rng=rng)
                        continue
                    e = symuluj_ewaluacje(z, odcisk, rng=rng)
                    if rng.random() < p_wyslania:
                        _wyslij(e, rng, min(teraz, twardy + timedelta(days=rng.randint(1, 4))))
    return rok


def usun_resztki() -> int:
    """Fikcyjne konta studentow bez zgloszen. Zwraca ich liczbe."""
    studentow, _ = Student.objects.filter(github_id__lt=0, zgloszenia__isnull=True).delete()
    return studentow


def usun() -> dict:
    """Usuwa caly poligon: rok, przedmioty, grupy, zestawy, prace, oceny i fikcyjne konta studentow."""
    with transaction.atomic():
        lata = list(Rok.objects.filter(poligon=True))
        przedmiotow = Kurs.objects.filter(rok__in=lata).count()
        zgloszen = Zgloszenie.objects.filter(przydzial__zestaw__kurs__rok__in=lata).count()
        for rok in lata:
            usun_rok(rok)
        studentow = usun_resztki()
    return {"lat": len(lata), "przedmiotow": przedmiotow, "zgloszen": zgloszen, "studentow": studentow}
