"""Robot w tle: przelicza stany terminow, synchronizuje wypchniecia z GitHub i wykonuje ocene automatyczna."""
import logging
import time

from django.core.management.base import BaseCommand

from kurs import ocena, uslugi

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Przelicza stany terminow (co kilka sekund), synchronizuje wypchniecia z GitHub (co kilka minut) "
        "i przetwarza kolejke ocen automatycznych."
    )

    def add_arguments(self, parser):
        parser.add_argument("--raz", action="store_true", help="Jeden pelny cykl i zakonczenie.")
        parser.add_argument("--przerwa", type=int, default=10, help="Sekundy miedzy krotkimi cyklami.")
        parser.add_argument("--synchronizacja-co", type=int, default=300, help="Sekundy miedzy pelnymi cyklami.")

    def handle(self, *args, **opcje):
        ostatni_pelny = float("-inf")
        self.stdout.write("Robot uruchomiony.")
        while True:
            teraz = time.monotonic()
            pelny = opcje["raz"] or teraz - ostatni_pelny >= opcje["synchronizacja_co"]
            for nazwa, zadanie in (
                ("terminy", lambda: uslugi.cykl_robota(pelny=pelny)),
                ("ocena po terminach", lambda: pelny and ocena.zakolejkuj_po_terminach()),
                ("przerwane oceny", lambda: pelny and ocena.oznacz_przerwane()),
                ("kolejka ocen", lambda: ocena.przetworz_kolejke(limit=3)),
            ):
                try:
                    zadanie()
                except Exception:
                    log.exception("Blad robota (%s)", nazwa)
            if pelny:
                ostatni_pelny = teraz
            if opcje["raz"]:
                break
            time.sleep(opcje["przerwa"])
