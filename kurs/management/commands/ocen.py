"""Ocena automatyczna z wiersza polecen, np. python manage.py ocen --przydzial 3."""
from django.core.management.base import BaseCommand, CommandError

from kurs import ocena
from kurs.models import Ewaluacja, Przydzial, Zgloszenie


class Command(BaseCommand):
    help = "Dodaje prace do kolejki oceny automatycznej i od razu je ocenia."

    def add_arguments(self, parser):
        grupa = parser.add_mutually_exclusive_group(required=True)
        grupa.add_argument("--przydzial", type=int, help="Numer zestawu w grupie (z adresu /panel/przydzialy/N/).")
        grupa.add_argument("--zgloszenie", type=int, help="Numer zgloszenia (z adresu /panel/zgloszenia/N/).")
        parser.add_argument("--wymus", action="store_true", help="Ocen ponownie rowniez juz ocenione.")

    def handle(self, *args, **opcje):
        if opcje["przydzial"]:
            try:
                przydzial = Przydzial.objects.get(pk=opcje["przydzial"])
            except Przydzial.DoesNotExist as blad:
                raise CommandError("Nie ma takiego zestawu w grupie.") from blad
            zgloszenia = przydzial.zgloszenia.filter(status=Zgloszenie.Status.GOTOWE)
        else:
            zgloszenia = Zgloszenie.objects.filter(pk=opcje["zgloszenie"])
        for zgloszenie in zgloszenia:
            ocena.zakolejkuj(zgloszenie, wymus=opcje["wymus"])
        while ocena.przetworz_kolejke(limit=3):
            pass
        for ewaluacja in Ewaluacja.objects.filter(zgloszenie__in=zgloszenia).order_by("zgloszenie_id", "-utworzono"):
            stan = "SPORNA" if ewaluacja.sporna else "zgodna"
            if ewaluacja.status == Ewaluacja.Status.BLAD:
                stan = f"BLAD: {ewaluacja.blad_opis[:120]}"
            self.stdout.write(f"{ewaluacja.zgloszenie.student}: {ewaluacja.suma} pkt, {stan}")
