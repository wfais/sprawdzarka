"""Poligon z danymi fikcyjnymi: utworzenie albo usuniecie w calosci."""
from django.core.management.base import BaseCommand, CommandError

from kurs import poligon


class Command(BaseCommand):
    help = "Tworzy poligon (fikcyjny rok z przedmiotami, grupami, studentami i pracami, bez GitHuba) albo usuwa go z opcja --usun."

    def add_arguments(self, parser):
        parser.add_argument("--usun", action="store_true", help="Usun caly poligon.")

    def handle(self, *args, **opcje):
        if opcje["usun"]:
            wynik = poligon.usun()
            self.stdout.write(self.style.SUCCESS(
                f"Poligon usunięty: przedmioty {wynik['przedmiotow']}, zgłoszenia {wynik['zgloszen']}, "
                f"konta {wynik['studentow']}."))
            return
        try:
            rok = poligon.utworz()
        except poligon.BladPoligonu as blad:
            raise CommandError(str(blad)) from None
        self.stdout.write(self.style.SUCCESS(
            f"Poligon gotowy: {rok.pelna_nazwa}, przedmioty: " + ", ".join(k.nazwa for k in rok.kursy.all())))
