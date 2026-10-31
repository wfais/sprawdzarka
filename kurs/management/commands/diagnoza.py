"""Diagnoza rejestru wypchniec: powiadomienia GitHub i historia repozytorium studenta, bez sekretow.

    python manage.py diagnoza                 # tylko powiadomienia (webhook)
    python manage.py diagnoza <login|repo>    # do tego zgloszenia studenta albo repozytorium
"""
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from kurs import terminy, uslugi
from kurs.github import klient as gh
from kurs.models import Wypchniecie, Zgloszenie


def _czas(wartosc) -> str:
    return timezone.localtime(wartosc).strftime("%d.%m %H:%M:%S") if wartosc else "-"


def _czas_github(tekst: str) -> str:
    try:
        return _czas(datetime.fromisoformat((tekst or "").replace("Z", "+00:00")))
    except ValueError:
        return tekst or "-"


def _sha(wartosc: str) -> str:
    return (wartosc or "-")[:7]


class Command(BaseCommand):
    help = "Diagnoza powiadomien GitHub i wypchniec w repozytoriach studentow (bez sekretow)."

    def add_arguments(self, parser):
        parser.add_argument("szukaj", nargs="?", default="", help="Login studenta albo fragment nazwy repozytorium.")
        parser.add_argument("--dostaw", type=int, default=15, help="Ile ostatnich powiadomien pokazac.")

    def handle(self, *args, **opcje):
        self._powiadomienia(opcje["dostaw"])
        szukaj = opcje["szukaj"].strip().lstrip("@")
        if not szukaj:
            return
        zgloszenia = (Zgloszenie.objects.filter(Q(student__login__iexact=szukaj) | Q(repo_nazwa__icontains=szukaj))
                      .select_related("student", "przydzial__zestaw", "przydzial__grupa").order_by("utworzono"))
        if not zgloszenia:
            self.stdout.write(f"\nBrak zgłoszeń dla: {szukaj}")
        for z in zgloszenia:
            self._zgloszenie(z)

    def _powiadomienia(self, ile: int) -> None:
        self.stdout.write("== Powiadomienia GitHub (webhook) ==")
        try:
            adres = gh.adres_webhooka()
            oczekiwany = settings.PUBLICZNY_ADRES + reverse("github_webhook")
            self.stdout.write(f"Adres w aplikacji GitHub: {adres or '(pusty)'}"
                              + ("" if adres == oczekiwany else f"  UWAGA: oczekiwany {oczekiwany}"))
            dostawy = gh.dostawy_webhookow(ile)
        except gh.BladGitHub as blad:
            self.stdout.write(f"Nie udało się odczytać: {blad}")
            return
        if not dostawy:
            self.stdout.write("GitHub nie wysłał ostatnio żadnych powiadomień.")
        for d in dostawy:
            self.stdout.write(f"  {_czas_github(d.get('delivered_at'))}  {d.get('event', ''):<14} "
                              f"odpowiedź {d.get('status_code', '-')}"
                              f"  {d.get('status', '')}  repo {d.get('repository_id', '-')}")

    def _zgloszenie(self, z: Zgloszenie) -> None:
        self.stdout.write(f"\n== {z.przydzial.zestaw.nazwa}, {z.przydzial.grupa.nazwa}: @{z.student.login} "
                          f"({z.repo_nazwa or 'bez repozytorium'}, id {z.repo_id}) ==")
        self.stdout.write(f"Status: {z.status}; stan: {z.stan_kolor} ({z.stan_pracy.etykieta}); {z.stan_opis}")
        self.stdout.write(f"Konto studenta: id {z.student.github_id}; ostatnie wypchnięcie: {_czas(z.ostatnie_wypchniecie)}; "
                          f"przeliczono: {_czas(z.przeliczono)}; do przeliczenia: {z.do_przeliczenia}")
        self.stdout.write(f"Stan startowy {_sha(z.sha_bazowy)}, początek main {_sha(z.sha_poczatkowy)}, "
                          f"oceniany {_sha(z.sha_oceniany)}, bieżący {_sha(z.sha_teraz)}; "
                          f"termin {_czas(z.termin_miekki)}, ostateczny {_czas(z.termin_twardy)}")
        if not z.repo_id:
            return
        wzorce = z.przydzial.zestaw.lista_wzorcow()
        w_bazie = list(Wypchniecie.objects.filter(repo_id=z.repo_id).order_by("czas", "id"))
        self.stdout.write(f"Wypchnięcia w bazie ({len(w_bazie)}):")
        for w in w_bazie:
            kto = "student" if uslugi.od_studenta(w, z.student) else "INNE KONTO (pominięte)"
            pliki = ""
            if w.sha_przed and w.sha_przed != terminy.ZERO_SHA and w.sha_po != terminy.ZERO_SHA:
                try:
                    lista = uslugi._porownaj_z_pamiecia(z, w.sha_przed, w.sha_po)
                except gh.BladGitHub as blad:
                    lista, pliki = None, f" zmiany: błąd ({blad})"
                if lista is not None:
                    kod = [p for p in lista if terminy.pasuje_do_wzorcow(p, wzorce)]
                    pliki = f" zmiany: {', '.join(lista) or 'brak'}" + (" [KOD]" if kod else " [bez kodu]")
            self.stdout.write(f"  {_czas(w.czas)}  {w.ref.removeprefix('refs/heads/'):<10} @{w.autor or '?'} "
                              f"(id {w.autor_id or '?'}, {kto})  {_sha(w.sha_przed)}..{_sha(w.sha_po)}  "
                              f"[{w.zrodlo}]{pliki}")
        znane = {(w.ref, w.sha_przed, w.sha_po) for w in w_bazie}
        self.stdout.write("Dziennik aktywności na GitHubie (wszystkie gałęzie):")
        try:
            wpisy = list(gh.aktywnosc(z.repo_nazwa, None))
        except gh.BladGitHub as blad:
            self.stdout.write(f"  nie udało się odczytać: {blad}")
            return
        for a in sorted(wpisy, key=lambda a: a.get("timestamp", "")):
            ref = a.get("ref") or ""
            ref = ref if ref.startswith("refs/") else f"refs/heads/{ref}"
            jest = (ref, a.get("before") or terminy.ZERO_SHA, a.get("after") or terminy.ZERO_SHA) in znane
            self.stdout.write(f"  {_czas_github(a.get('timestamp'))}  "
                              f"{ref.removeprefix('refs/heads/'):<10} {a.get('activity_type', ''):<16} "
                              f"@{(a.get('actor') or {}).get('login', '?')}  {_sha(a.get('before'))}..{_sha(a.get('after'))}"
                              f"  w bazie: {'tak' if jest else 'NIE'}")
