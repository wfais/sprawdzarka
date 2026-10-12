"""Strony dla studentow: logowanie przez GitHub i akceptacja zestawu."""
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from . import uslugi
from .forms import FormularzAkceptacji
from .github import klient as gh
from .models import Przydzial, Student, Zgloszenie

log = logging.getLogger(__name__)

SESJA_UZYTKOWNIKA = "gh_uzytkownik"


def _adres_powrotu():
    return settings.PUBLICZNY_ADRES + reverse("student:powrot")


def _blad(request, komunikat, status=400):
    return render(request, "kurs/student/blad.html", {"komunikat": komunikat}, status=status)


def logowanie(request):
    nastepny = request.GET.get("next") or "/"
    if not url_has_allowed_host_and_scheme(nastepny, allowed_hosts={request.get_host()}):
        nastepny = "/"
    if not settings.GITHUB_APP_CLIENT_ID:
        return _blad(request, "Logowanie przez GitHub nie jest jeszcze skonfigurowane.", status=503)
    stan = secrets.token_urlsafe(16)
    request.session["gh_stan"] = stan
    request.session["gh_nastepny"] = nastepny
    return redirect(gh.adres_autoryzacji(_adres_powrotu(), stan))


def powrot(request):
    stan = request.session.pop("gh_stan", None)
    if not stan or request.GET.get("state") != stan:
        return _blad(request, "Sesja logowania wygasła. Otwórz link do zestawu jeszcze raz.")
    if request.GET.get("error"):
        return _blad(request, "Logowanie przez GitHub zostało przerwane.")
    try:
        token = gh.wymien_kod(request.GET.get("code", ""), _adres_powrotu())
        dane = gh.uzytkownik(token)
    except gh.BladGitHub as blad:
        log.warning("Logowanie studenta nieudane: %s", blad)
        return _blad(request, "Nie udało się zalogować przez GitHub. Spróbuj ponownie za chwilę.", status=502)
    nastepny = request.session.pop("gh_nastepny", "/")
    request.session.cycle_key()
    request.session[SESJA_UZYTKOWNIKA] = {"id": dane["id"], "login": dane["login"], "nazwa": dane.get("name") or ""}
    return redirect(nastepny)


def wyloguj(request):
    request.session.pop(SESJA_UZYTKOWNIKA, None)
    nastepny = request.GET.get("next") or "/"
    if not url_has_allowed_host_and_scheme(nastepny, allowed_hosts={request.get_host()}):
        nastepny = "/"
    return redirect(nastepny)


def zestaw(request, token):
    przydzial = get_object_or_404(Przydzial.objects.select_related("zestaw", "grupa"), token=token)
    uzytkownik = request.session.get(SESJA_UZYTKOWNIKA)
    if not uzytkownik:
        return redirect(f"{reverse('student:logowanie')}?next={request.path}")

    student = Student.objects.filter(github_id=uzytkownik["id"]).first()
    zgloszenie = uslugi.aktywne_zgloszenie(przydzial, student)
    wymagaj_nazwiska = not (student and student.imie_nazwisko)

    if request.method == "POST" and request.POST.get("akcja") == "zaproszenie":
        if zgloszenie and zgloszenie.status == Zgloszenie.Status.GOTOWE:
            try:
                uslugi.ponow_zaproszenie(zgloszenie)
                messages.success(request, "Zaproszenie wysłane ponownie. Sprawdź e-mail albo stronę zaproszenia.")
            except uslugi.BLEDY as blad:
                log.warning("Ponowne zaproszenie nieudane: %s", blad)
                messages.error(request, "Nie udało się wysłać zaproszenia. Napisz do prowadzącego.")
        return redirect("student:zestaw", token=token)

    if request.method == "POST":
        formularz = FormularzAkceptacji(request.POST, wymagaj_nazwiska=wymagaj_nazwiska)
        if not przydzial.otwarty and zgloszenie is None:
            return _blad(request, "Ten zestaw nie przyjmuje już zgłoszeń.")
        if formularz.is_valid():
            uslugi.akceptuj(
                przydzial, uzytkownik["id"], uzytkownik["login"], formularz.cleaned_data.get("imie_nazwisko", ""),
            )
            return redirect("student:zestaw", token=token)
    else:
        formularz = FormularzAkceptacji(
            wymagaj_nazwiska=wymagaj_nazwiska, initial={"imie_nazwisko": uzytkownik.get("nazwa", "")},
        )

    return render(request, "kurs/student/zestaw.html", {
        "przydzial": przydzial,
        "uzytkownik": uzytkownik,
        "student": student,
        "zgloszenie": zgloszenie,
        "formularz": formularz,
        "wymagaj_nazwiska": wymagaj_nazwiska,
        "Status": Zgloszenie.Status,
        "galaz": settings.SPRAWDZARKA_GALAZ,
        "utknelo": bool(zgloszenie and zgloszenie.status == Zgloszenie.Status.TWORZENIE
                        and zgloszenie.utworzono < timezone.now() - timedelta(minutes=1)),
    })
