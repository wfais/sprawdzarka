"""Dane pokazowe do obejrzenia panelu bez polaczenia z GitHub (tylko na wlasnym komputerze!)."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from kurs.models import (
    Ewaluacja, Grupa, OcenaZadania, OpiniaModelu, Przydzial, Student, Wypchniecie, Zadanie, Zestaw, Zgloszenie,
)

D = Decimal
STUDENCI = [
    ("akow99", "Anna Kowalska", "zielony", "Oddane w terminie."),
    ("pn-dev", "Piotr Nowak", "zolty", "Zmiany w kodzie po terminie (sugerowany mnożnik 0,8)."),
    ("x_coder_x", "", "zielony", "Oddane w terminie."),
    ("ewalis", "Ewa Lis", "pomaranczowy", "Oddane w terminie. Są też zmiany po terminie ostatecznym, oceniany jest stan z tego terminu."),
    ("mz2005", "Marek Zieliński", "szary", "Brak oddanego kodu."),
    ("kasia-w", "Katarzyna Wójcik", "czerwony", "Kod oddany wyłącznie po terminie ostatecznym."),
]


class Command(BaseCommand):
    help = "Tworzy przykladowe grupy, zestawy i zgloszenia (bez GitHub), aby obejrzec panel."

    def add_arguments(self, parser):
        parser.add_argument("--haslo", default="pokaz-sprawdzarki", help="Haslo konta prowadzacy (tworzonego, jesli brak).")

    @transaction.atomic
    def handle(self, *args, **opcje):
        if Zgloszenie.objects.exists():
            raise CommandError("Baza zawiera już zgłoszenia, dane pokazowe dodaje się tylko do pustej bazy.")
        Uzytkownik = get_user_model()
        if not Uzytkownik.objects.filter(username="prowadzacy").exists():
            Uzytkownik.objects.create_superuser("prowadzacy", "", opcje["haslo"])
        teraz = timezone.now()
        g3, _ = Grupa.objects.get_or_create(skrot="g3", defaults={"nazwa": "Grupa 3", "opis": "wtorek 8:00"})
        g4, _ = Grupa.objects.get_or_create(skrot="g4", defaults={"nazwa": "Grupa 4", "opis": "wtorek 10:00"})
        g6, _ = Grupa.objects.get_or_create(skrot="g6", defaults={"nazwa": "Grupa 6", "opis": "czwartek 12:00"})
        z3, _ = Zestaw.objects.get_or_create(skrot="z03", defaults={
            "nazwa": "Zestaw 3: funkcje i kolekcje", "repo_zrodlowe": "wfais/python-zestaw3", "punkty_max": D(5),
            "tresc_tekst": "Zadanie 1 (1 pkt): funkcja suma...\nZadanie 2 (1 pkt): gra w kości z interfejsem tkinter...",
        })
        z4, _ = Zestaw.objects.get_or_create(skrot="z04", defaults={
            "nazwa": "Zestaw 4: klasy", "repo_zrodlowe": "wfais/python-zestaw4", "punkty_max": D(5)})
        for numer, (kod, tryb) in enumerate([("ZADANIE1", "testy_modele"), ("ZADANIE2", "modele"),
                                             ("ZADANIE3", "testy_modele"), ("ZADANIE4", "testy_modele"),
                                             ("ZADANIE5", "recznie")], 1):
            Zadanie.objects.get_or_create(zestaw=z3, kod=kod, defaults={
                "kolejnosc": numer, "katalog": f"{kod}/", "punkty": D(1), "tryb": tryb,
                "kryteria": "Rozwiązanie zgodne z treścią, obsługa przypadków brzegowych."})
        przydzialy = []
        for grupa, przesuniecie in ((g3, -9), (g4, -2), (g6, 5)):
            p, _ = Przydzial.objects.get_or_create(zestaw=z3, grupa=grupa, defaults={
                "przedrostek_repo": f"py-z03-{grupa.skrot}", "termin_miekki": teraz + timedelta(days=przesuniecie),
                "termin_twardy": teraz + timedelta(days=przesuniecie + 7),
                "repo_startowe": f"wfais/py-z03-{grupa.skrot}-start", "sha_startowy": "a" * 40})
            przydzialy.append(p)
        Przydzial.objects.get_or_create(zestaw=z4, grupa=g4, defaults={
            "przedrostek_repo": "py-z04-g4", "termin_miekki": teraz + timedelta(days=12),
            "termin_twardy": teraz + timedelta(days=19), "repo_startowe": "wfais/py-z04-g4-start", "sha_startowy": "a" * 40})

        p = przydzialy[0]
        for numer, (login, nazwisko, kolor, opis) in enumerate(STUDENCI):
            student, _ = Student.objects.get_or_create(github_id=1000 + numer, defaults={
                "login": login, "imie_nazwisko": nazwisko, "zrodlo_nazwiska": "formularz" if nazwisko else "",
                "kandydat_readme": "" if nazwisko else "Jan Wiśniewski", "kandydat_repo": f"wfais/py-z03-g3-{login}"})
            mnoznik = {"zielony": D(1), "zolty": D("0.8"), "pomaranczowy": D(1), "czerwony": D(0)}.get(kolor)
            z = Zgloszenie.objects.create(
                przydzial=p, student=student, status=Zgloszenie.Status.GOTOWE, repo_id=5000 + numer,
                repo_nazwa=f"wfais/py-z03-g3-{login}", sha_bazowy="b" * 40, sha_poczatkowy="c" * 40,
                zaproszenie_wyslane=True, pr_numer=1, stan_kolor=kolor, stan_opis=opis, sugerowany_mnoznik=mnoznik,
                sha_oceniany="" if kolor == "szary" else f"{numer}" * 40, sha_teraz=f"{numer}" * 40,
                ostatnie_wypchniecie=None if kolor == "szary" else teraz - timedelta(days=10 - numer),
                przeliczono=teraz, do_przeliczenia=False,
            )
            if login == "pn-dev":
                # Przyklad znacznika: student zmienil testy prowadzacego
                z.testy_zmienione = True
                z.zmiany_testow = {"zmienione": ["ZADANIE1/tests/test_main.py"], "dodane": ["conftest.py"]}
                z.save(update_fields=["testy_zmienione", "zmiany_testow"])
            if kolor != "szary":
                Wypchniecie.objects.create(repo_id=z.repo_id, repo_nazwa=z.repo_nazwa, ref="refs/heads/main",
                                           sha_przed="c" * 40, sha_po=z.sha_oceniany, czas=z.ostatnie_wypchniecie,
                                           autor=login, zrodlo="webhook")
            if kolor in ("zielony", "zolty", "pomaranczowy"):
                self._ewaluacja(z, sporna=(login == "pn-dev"))
        self.stdout.write(self.style.SUCCESS(
            f"Gotowe. Panel: /panel/ (login: prowadzacy, hasło: {opcje['haslo']}, jeśli konto było tworzone)."))

    def _ewaluacja(self, zgloszenie, sporna):
        e = Ewaluacja.objects.create(
            zgloszenie=zgloszenie, sha=zgloszenie.sha_oceniany, status=Ewaluacja.Status.GOTOWA, sporna=sporna,
            suma=D("3.75") if sporna else D("4.50"), tokeny_we=18400, tokeny_wy=2100, zakonczono=timezone.now(),
            testy={"uruchomiono": True, "czas": 1.4, "przekroczono_czas": False, "kod_wyjscia": 1, "dziennik": "",
                   "przypadki": [
                       {"nazwa": "test_dodatnie", "plik": "ZADANIE1/tests/test_main.py", "wynik": "zdany", "komunikat": ""},
                       {"nazwa": "test_ujemne", "plik": "ZADANIE1/tests/test_main.py", "wynik": "niezdany",
                        "komunikat": "assert 0 == -5"}]},
            komentarz="ZADANIE1: brak obslugi liczb ujemnych\nZADANIE2: gra dziala, brak obslugi bledow",
        )
        punkty = {
            "anthropic:claude-sonnet-5-5": [0.5, 1.0, 1.0, 1.0],
            "openai:model-b": [0.5, 0.5 if sporna else 1.0, 1.0, 1.0],
            "xai:model-c": [0.5, 0.25 if sporna else 0.75, 1.0, 1.0],
        }
        for model, lista in punkty.items():
            OpiniaModelu.objects.create(ewaluacja=e, model=model, runda=1, ok=True, tokeny_we=6100, tokeny_wy=700, czas_s=21.5,
                                        dane={"zadania": [
                                            {"kod": f"ZADANIE{i}", "punkty": p, "uzasadnienie": f"Uzasadnienie modelu dla zadania {i}.",
                                             "komentarz": "komentarz", "uwagi": []} for i, p in enumerate(lista, 1)]})
        for i in range(1, 6):
            OcenaZadania.objects.create(
                ewaluacja=e, zadanie=Zadanie.objects.get(zestaw=zgloszenie.przydzial.zestaw, kod=f"ZADANIE{i}"),
                kod=f"ZADANIE{i}", punkty_max=D(1), tryb="recznie" if i == 5 else "testy_modele",
                testy_zdane=1 if i == 1 else None, testy_wszystkie=2 if i == 1 else None,
                punkty_konsensus=None if i == 5 else (D("0.50") if i == 2 and sporna else D(1) if i > 2 else D("0.50") if i == 1 else D("1")),
                sporne=(i == 2 and sporna) or i == 5, komentarz="komentarz",
            )
