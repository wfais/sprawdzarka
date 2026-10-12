"""Panel prowadzacego: grupy, zestawy, zestawy w grupach, zgloszenia studentow."""
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Count, Prefetch, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import oceniacze, ocena, uslugi
from .forms import (
    FormularzeZadan, FormularzEdycjiPrzydzialu, FormularzGrupy, FormularzNazwiska, FormularzOceny,
    FormularzPrecedensu, FormularzPrzydzialu, FormularzWyjatku, FormularzZestawu,
)
from .models import Ewaluacja, Grupa, Precedens, Przydzial, Student, Wypchniecie, Zestaw, Zgloszenie

log = logging.getLogger(__name__)

prowadzacy = user_passes_test(lambda u: u.is_active and u.is_staff, login_url="panel:logowanie")

KOLEJNOSC_KOLOROW = ["zielony", "zolty", "pomaranczowy", "czerwony", "niebieski", "szary"]
ETYKIETY_KOLOROW = dict(Zgloszenie.Kolor.choices)


def _dolacz_liczniki(przydzialy):
    """Dopisuje do kazdego przydzialu liczniki kolorow i zgloszen wymagajacych uwagi."""
    przydzialy = list(przydzialy)
    mapa = {p.pk: p for p in przydzialy}
    for p in przydzialy:
        p.liczniki = {k: 0 for k in KOLEJNOSC_KOLOROW}
        p.lacznie = 0
        p.problemy = 0
        p.testy = 0
    wiersze = (
        Zgloszenie.objects.filter(przydzial_id__in=mapa)
        .exclude(status=Zgloszenie.Status.WYZEROWANE)
        .values("przydzial_id", "status", "stan_kolor", "testy_zmienione")
        .annotate(n=Count("id"))
    )
    for w in wiersze:
        p = mapa[w["przydzial_id"]]
        p.lacznie += w["n"]
        if w["testy_zmienione"]:
            p.testy += w["n"]
        if w["status"] == Zgloszenie.Status.GOTOWE:
            p.liczniki[w["stan_kolor"]] += w["n"]
        else:
            p.problemy += w["n"]
    for p in przydzialy:
        p.paski = [(k, ETYKIETY_KOLOROW[k], p.liczniki[k]) for k in KOLEJNOSC_KOLOROW if p.liczniki[k]]
    return przydzialy


@prowadzacy
def pulpit(request):
    grupy = list(Grupa.objects.filter(aktywna=True).prefetch_related(
        Prefetch("przydzialy", queryset=Przydzial.objects.select_related("zestaw").order_by("termin_miekki"))
    ))
    for grupa in grupy:
        grupa.lista = _dolacz_liczniki(grupa.przydzialy.all())
    return render(request, "kurs/panel/pulpit.html", {
        "grupy": grupy,
        "kandydaci": Student.objects.filter(imie_nazwisko="").exclude(kandydat_readme="").count(),
        "bez_nazwiska": Student.objects.filter(imie_nazwisko="").count(),
        "bledy": Zgloszenie.objects.filter(status=Zgloszenie.Status.BLAD).select_related("student", "przydzial"),
        "brak_zestawow": not Zestaw.objects.exists(),
        "brak_grup": not Grupa.objects.exists(),
    })


# --- Grupy ---

@prowadzacy
def grupy(request):
    formularz = FormularzGrupy(request.POST or None)
    if request.method == "POST" and formularz.is_valid():
        grupa = formularz.save()
        messages.success(request, f"Dodano grupę {grupa}.")
        return redirect("panel:grupy")
    lista = Grupa.objects.annotate(liczba_zestawow=Count("przydzialy"))
    return render(request, "kurs/panel/grupy.html", {"grupy": lista, "formularz": formularz})


@prowadzacy
def grupa_edycja(request, pk):
    grupa = get_object_or_404(Grupa, pk=pk)
    formularz = FormularzGrupy(request.POST or None, instance=grupa)
    if request.method == "POST" and formularz.is_valid():
        formularz.save()
        messages.success(request, "Zapisano zmiany grupy.")
        return redirect("panel:grupy")
    return render(request, "kurs/panel/formularz.html", {
        "tytul": f"Grupa: {grupa}", "formularz": formularz, "przycisk": "Zapisz", "wstecz": "panel:grupy",
    })


# --- Zestawy ---

def _odczytaj_pdf(request, zestaw, formularz):
    """Nowy PDF: odczyt tresci jako tekstu dla modeli (chyba ze prowadzacy sam wpisal tresc)."""
    if "tresc_pdf" not in formularz.changed_data or not zestaw.tresc_pdf:
        return
    if "tresc_tekst" in formularz.changed_data and zestaw.tresc_tekst.strip():
        return
    try:
        zestaw.tresc_tekst = ocena.tekst_z_pdf(zestaw.tresc_pdf.path)
        zestaw.save(update_fields=["tresc_tekst"])
    except Exception as blad:
        messages.warning(request, f"Nie udało się odczytać tekstu z PDF: {blad}")


@prowadzacy
def zestawy(request):
    lista = Zestaw.objects.annotate(liczba_grup=Count("przydzialy")).order_by("skrot")
    return render(request, "kurs/panel/zestawy.html", {"zestawy": lista})


@prowadzacy
def zestaw_nowy(request):
    formularz = FormularzZestawu(request.POST or None, request.FILES or None)
    if request.method == "POST" and formularz.is_valid():
        zestaw = formularz.save()
        _odczytaj_pdf(request, zestaw, formularz)
        messages.success(request, f"Dodano zestaw {zestaw}. Teraz wydaj go grupom.")
        return redirect("panel:zestaw", pk=zestaw.pk)
    return render(request, "kurs/panel/formularz.html", {
        "tytul": "Nowy zestaw", "formularz": formularz, "przycisk": "Dodaj zestaw", "pliki": True,
        "wstecz": "panel:zestawy",
    })


@prowadzacy
def zestaw_edycja(request, pk):
    zestaw = get_object_or_404(Zestaw, pk=pk)
    formularz = FormularzZestawu(request.POST or None, request.FILES or None, instance=zestaw)
    if request.method == "POST" and formularz.is_valid():
        formularz.save()
        _odczytaj_pdf(request, zestaw, formularz)
        messages.success(request, "Zapisano zmiany zestawu.")
        return redirect("panel:zestaw", pk=zestaw.pk)
    return render(request, "kurs/panel/formularz.html", {
        "tytul": f"Zestaw: {zestaw}", "formularz": formularz, "przycisk": "Zapisz", "pliki": True,
        "wstecz_obiekt": zestaw,
    })


@prowadzacy
def zestaw(request, pk):
    zestaw = get_object_or_404(Zestaw, pk=pk)
    formularz = FormularzPrzydzialu(request.POST or None, zestaw=zestaw)
    if request.method == "POST" and formularz.is_valid():
        przydzial = formularz.save()
        try:
            uslugi.utworz_repo_startowe(przydzial)
            messages.success(request, f"Zestaw wydany grupie {przydzial.grupa}. Repozytorium startowe gotowe.")
        except uslugi.BLEDY as blad:
            messages.warning(request, f"Zestaw wydany, ale repozytorium startowego nie udało się utworzyć: {blad}")
        return redirect("panel:przydzial", pk=przydzial.pk)
    przydzialy = _dolacz_liczniki(zestaw.przydzialy.select_related("grupa").order_by("grupa__nazwa"))
    zadania = list(zestaw.zadania.all())
    return render(request, "kurs/panel/zestaw.html", {
        "zestaw": zestaw, "przydzialy": przydzialy, "formularz": formularz, "zadania": zadania,
        "suma_zadan": sum((z.punkty for z in zadania), 0),
        "modele": oceniacze.dla_zestawu(zestaw),
    })


@prowadzacy
def zestaw_pdf(request, pk):
    zestaw = get_object_or_404(Zestaw, pk=pk)
    if not zestaw.tresc_pdf:
        raise Http404
    return FileResponse(zestaw.tresc_pdf.open("rb"), content_type="application/pdf")


# --- Zestaw w grupie ---

@prowadzacy
def przydzial(request, pk):
    przydzial = get_object_or_404(Przydzial.objects.select_related("zestaw", "grupa"), pk=pk)
    zgloszenia = list(
        przydzial.zgloszenia.exclude(status=Zgloszenie.Status.WYZEROWANE).select_related("student")
        .prefetch_related(Prefetch("ewaluacje", queryset=Ewaluacja.objects.order_by("-utworzono")))
    )
    zgloszenia.sort(key=lambda z: z.student.klucz_sortowania)
    for z in zgloszenia:
        lista = list(z.ewaluacje.all())
        z.ostatnia_ewaluacja = lista[0] if lista else None
    w_toku = any(z.ostatnia_ewaluacja and z.ostatnia_ewaluacja.w_toku for z in zgloszenia)
    filtr = request.GET.get("kolor", "")
    if filtr == "problemy":
        widoczne = [z for z in zgloszenia if z.status != Zgloszenie.Status.GOTOWE]
    elif filtr == "testy":
        widoczne = [z for z in zgloszenia if z.testy_zmienione]
    elif filtr:
        widoczne = [z for z in zgloszenia if z.status == Zgloszenie.Status.GOTOWE and z.stan_kolor == filtr]
    else:
        widoczne = zgloszenia
    _dolacz_liczniki([przydzial])
    return render(request, "kurs/panel/przydzial.html", {
        "przydzial": przydzial,
        "zgloszenia": widoczne,
        "wszystkich": len(zgloszenia),
        "filtr": filtr,
        "Status": Zgloszenie.Status,
        "w_toku": w_toku,
    })


@prowadzacy
def przydzial_edycja(request, pk):
    przydzial = get_object_or_404(Przydzial, pk=pk)
    formularz = FormularzEdycjiPrzydzialu(request.POST or None, instance=przydzial)
    if request.method == "POST" and formularz.is_valid():
        formularz.save()
        przydzial.zgloszenia.update(do_przeliczenia=True)
        messages.success(request, "Zapisano terminy. Stany zgłoszeń zostaną przeliczone.")
        return redirect("panel:przydzial", pk=przydzial.pk)
    return render(request, "kurs/panel/formularz.html", {
        "tytul": f"Terminy: {przydzial}", "formularz": formularz, "przycisk": "Zapisz", "wstecz_obiekt": przydzial,
    })


@prowadzacy
@require_POST
def przydzial_repo_startowe(request, pk):
    przydzial = get_object_or_404(Przydzial, pk=pk)
    try:
        uslugi.utworz_repo_startowe(przydzial)
        messages.success(request, f"Repozytorium startowe: {przydzial.repo_startowe}")
    except uslugi.BLEDY as blad:
        messages.error(request, f"Nie udało się utworzyć repozytorium startowego: {blad}")
    return redirect("panel:przydzial", pk=pk)


@prowadzacy
@require_POST
def przydzial_odswiez(request, pk):
    przydzial = get_object_or_404(Przydzial, pk=pk)
    licznik, bledy = uslugi.odswiez_przydzial(przydzial)
    if bledy:
        messages.warning(request, f"Odświeżono {licznik} zgłoszeń, błędy: " + "; ".join(bledy[:5]))
    else:
        messages.success(request, f"Odświeżono {licznik} zgłoszeń.")
    return redirect("panel:przydzial", pk=pk)


@prowadzacy
def przydzial_lista_repo(request, pk):
    """Lista repozytoriow do pobrania narzedziem narzedzia/pobierz_repozytoria.py."""
    przydzial = get_object_or_404(Przydzial, pk=pk)
    linie = ["# repozytorium\timie_nazwisko\tlogin\tstan\tsha_oceniany"]
    for z in przydzial.zgloszenia.filter(status=Zgloszenie.Status.GOTOWE).select_related("student"):
        linie.append("\t".join([
            z.repo_nazwa, z.student.imie_nazwisko or "", z.student.login, z.stan_kolor, z.sha_oceniany or "",
        ]))
    odpowiedz = HttpResponse("\n".join(linie) + "\n", content_type="text/plain; charset=utf-8")
    odpowiedz["Content-Disposition"] = f'attachment; filename="{przydzial.przedrostek_repo}.txt"'
    return odpowiedz


# --- Zgloszenie (karta pracy studenta) ---

def _zgloszenie(pk):
    return get_object_or_404(
        Zgloszenie.objects.select_related("student", "przydzial__zestaw", "przydzial__grupa"), pk=pk,
    )


def _podsumowanie_testow(ewaluacja):
    if not ewaluacja:
        return {}
    testy = dict(ewaluacja.testy or {})
    przypadki = [p for p in testy.get("przypadki", []) if p.get("wynik") != "pominiety"]
    testy["zdane"] = sum(1 for p in przypadki if p.get("wynik") == "zdany")
    testy["wszystkie"] = len(przypadki)
    return testy


@prowadzacy
def zgloszenie(request, pk):
    zgl = _zgloszenie(pk)
    mnoznik = zgl.mnoznik if zgl.mnoznik is not None else (zgl.sugerowany_mnoznik if zgl.sugerowany_mnoznik is not None else 1)
    formularz_oceny = FormularzOceny(
        punkty_max=zgl.przydzial.zestaw.punkty_max,
        initial={"punkty": zgl.punkty, "mnoznik": mnoznik, "komentarz": zgl.komentarz_koncowy},
    )
    wypchniecia = Wypchniecie.objects.filter(repo_id=zgl.repo_id).order_by("-czas") if zgl.repo_id else []
    ewaluacja = zgl.ewaluacje.first()
    modele_tabeli, wiersze = ocena.tabela_ocen(ewaluacja) if ewaluacja and ewaluacja.status == Ewaluacja.Status.GOTOWA else ([], [])
    return render(request, "kurs/panel/zgloszenie.html", {
        "ewaluacja": ewaluacja,
        "modele_tabeli": [(m, ocena.etykieta_modelu(m)) for m in modele_tabeli],
        "wiersze": wiersze,
        "liczba_ewaluacji": zgl.ewaluacje.count(),
        "mnoznik_domyslny": mnoznik,
        "opinie": ewaluacja.opinie.all() if ewaluacja else [],
        "testy": _podsumowanie_testow(ewaluacja),
        "z": zgl,
        "formularz_oceny": formularz_oceny,
        "formularz_wyjatku": FormularzWyjatku(instance=zgl),
        "formularz_nazwiska": FormularzNazwiska(instance=zgl.student),
        "wypchniecia": wypchniecia,
        "inne": zgl.student.zgloszenia.exclude(pk=zgl.pk).select_related("przydzial__zestaw", "przydzial__grupa"),
        "Status": Zgloszenie.Status,
        "github": settings.GITHUB_URL,
    })


@prowadzacy
@require_POST
def zgloszenie_termin(request, pk):
    zgl = _zgloszenie(pk)
    formularz = FormularzWyjatku(request.POST, instance=zgl)
    if formularz.is_valid():
        formularz.save()
        zgl.do_przeliczenia = True
        zgl.save(update_fields=["do_przeliczenia"])
        try:
            uslugi.przelicz(zgl)
        except uslugi.BLEDY as blad:
            messages.warning(request, f"Zapisano, ale przeliczenie się nie powiodło: {blad}")
        messages.success(request, "Zapisano indywidualne terminy.")
    else:
        messages.error(request, "Niepoprawne terminy.")
    return redirect("panel:zgloszenie", pk=pk)


@prowadzacy
@require_POST
def zgloszenie_ocena(request, pk):
    zgl = _zgloszenie(pk)
    formularz = FormularzOceny(request.POST, punkty_max=zgl.przydzial.zestaw.punkty_max)
    if not formularz.is_valid():
        bledy = "; ".join(f"{pole}: {' '.join(b)}" for pole, b in formularz.errors.items())
        messages.error(request, f"Popraw formularz oceny: {bledy}")
        return redirect("panel:zgloszenie", pk=pk)
    d = formularz.cleaned_data
    try:
        uslugi.zamknij_z_ocena(zgl, d["punkty"], d["mnoznik"], d["komentarz"])
        messages.success(request, f"Ocena wystawiona: {uslugi.liczba(zgl.wynik)} pkt. Pull request zamknięty.")
    except uslugi.BLEDY as blad:
        messages.error(request, f"Nie udało się zamknąć pull requestu: {blad}")
    nastepny = request.POST.get("nastepny")
    if nastepny == "przydzial":
        return redirect("panel:przydzial", pk=zgl.przydzial_id)
    return redirect("panel:zgloszenie", pk=pk)


@prowadzacy
@require_POST
def zgloszenie_zeruj(request, pk):
    zgl = _zgloszenie(pk)
    for komunikat in uslugi.wyzeruj(zgl, usun_repo=request.POST.get("usun_repo") == "1"):
        messages.info(request, komunikat)
    return redirect("panel:przydzial", pk=zgl.przydzial_id)


@prowadzacy
@require_POST
def zgloszenie_ponow(request, pk):
    zgl = _zgloszenie(pk)
    if uslugi.dokoncz_zgloszenie(zgl):
        messages.success(request, "Repozytorium utworzone.")
    else:
        zgl.refresh_from_db()
        messages.error(request, f"Nadal się nie udało: {zgl.blad_opis or 'trwa inne tworzenie'}")
    return redirect("panel:zgloszenie", pk=pk)


@prowadzacy
@require_POST
def zgloszenie_zaproszenie(request, pk):
    zgl = _zgloszenie(pk)
    try:
        uslugi.ponow_zaproszenie(zgl)
        messages.success(request, f"Zaproszenie do repozytorium wysłane ponownie do @{zgl.student.login}.")
    except uslugi.BLEDY as blad:
        messages.error(request, f"Nie udało się wysłać zaproszenia: {blad}")
    return redirect("panel:zgloszenie", pk=pk)


@prowadzacy
@require_POST
def zgloszenie_odswiez(request, pk):
    zgl = _zgloszenie(pk)
    try:
        nowe = uslugi.synchronizuj(zgl)
        uslugi.przelicz(zgl)
        messages.success(request, f"Odświeżono (nowe wypchnięcia: {nowe}).")
    except uslugi.BLEDY as blad:
        messages.error(request, f"Odświeżenie nieudane: {blad}")
    return redirect("panel:zgloszenie", pk=pk)


# --- Studenci ---

@prowadzacy
def studenci(request):
    lista = Student.objects.annotate(
        liczba=Count("zgloszenia", filter=~Q(zgloszenia__status=Zgloszenie.Status.WYZEROWANE))
    )
    filtr = request.GET.get("filtr", "")
    if filtr == "bez":
        lista = lista.filter(imie_nazwisko="")
    szukaj = request.GET.get("q", "").strip()
    if szukaj:
        lista = lista.filter(Q(login__icontains=szukaj) | Q(imie_nazwisko__icontains=szukaj))
    lista = sorted(lista, key=lambda s: s.klucz_sortowania)
    return render(request, "kurs/panel/studenci.html", {"studenci": lista, "filtr": filtr, "q": szukaj})


@prowadzacy
@require_POST
def student_nazwisko(request, pk):
    student = get_object_or_404(Student, pk=pk)
    if request.POST.get("akcja") == "zatwierdz" and student.kandydat_readme:
        student.imie_nazwisko = student.kandydat_readme
        student.zrodlo_nazwiska = Student.Zrodlo.README
        student.save(update_fields=["imie_nazwisko", "zrodlo_nazwiska"])
        messages.success(request, f"@{student.login} to {student.imie_nazwisko}.")
    elif request.POST.get("akcja") == "odrzuc":
        student.kandydat_readme = ""
        student.save(update_fields=["kandydat_readme"])
    else:
        formularz = FormularzNazwiska(request.POST, instance=student)
        if formularz.is_valid():
            student = formularz.save(commit=False)
            student.imie_nazwisko = " ".join(student.imie_nazwisko.split())
            student.zrodlo_nazwiska = Student.Zrodlo.RECZNIE if student.imie_nazwisko else ""
            student.save()
            messages.success(request, f"Zapisano: @{student.login}.")
    nastepny = request.POST.get("nastepny", "")
    if nastepny.startswith("/panel/"):
        return redirect(nastepny)
    return redirect("panel:studenci")


# --- Ocena automatyczna ---

def _uruchom_w_tle():
    """Rozpoczyna przetwarzanie kolejki ocen od razu (robot i tak je podejmie, blokada chroni przed dublowaniem)."""
    if not settings.SPRAWDZARKA_OCENA_W_WATKU:
        return
    import threading

    from django.db import close_old_connections

    def praca():
        try:
            while ocena.przetworz_kolejke(limit=3):
                pass
        finally:
            close_old_connections()

    threading.Thread(target=praca, daemon=True).start()


@prowadzacy
def zestaw_zadania(request, pk):
    zestaw = get_object_or_404(Zestaw, pk=pk)
    formularze = FormularzeZadan(request.POST or None, instance=zestaw)
    if request.method == "POST" and formularze.is_valid():
        formularze.save()
        messages.success(request, "Zapisano zadania i kryteria.")
        return redirect("panel:zestaw", pk=zestaw.pk)
    return render(request, "kurs/panel/zadania.html", {"zestaw": zestaw, "formularze": formularze})


@prowadzacy
@require_POST
def zestaw_zaproponuj(request, pk):
    zestaw = get_object_or_404(Zestaw, pk=pk)
    try:
        liczba_zadan = ocena.zaproponuj_zadania(zestaw)
        messages.success(request, f"Model zaproponował {liczba_zadan} zadań. Sprawdź i popraw kryteria.")
        return redirect("panel:zestaw_zadania", pk=pk)
    except (ocena.BladOceny, oceniacze.BladModelu) as blad:
        messages.error(request, f"Nie udało się zaproponować zadań: {blad}")
    return redirect("panel:zestaw", pk=pk)


@prowadzacy
@require_POST
def zestaw_tekst_z_pdf(request, pk):
    zestaw = get_object_or_404(Zestaw, pk=pk)
    if not zestaw.tresc_pdf:
        messages.error(request, "Zestaw nie ma pliku PDF.")
    else:
        try:
            zestaw.tresc_tekst = ocena.tekst_z_pdf(zestaw.tresc_pdf.path)
            zestaw.save(update_fields=["tresc_tekst"])
            messages.success(request, f"Odczytano treść z PDF ({len(zestaw.tresc_tekst)} znaków).")
        except Exception as blad:
            messages.error(request, f"Nie udało się odczytać PDF: {blad}")
    return redirect("panel:zestaw", pk=pk)


@prowadzacy
@require_POST
def przydzial_ocen(request, pk):
    przydzial = get_object_or_404(Przydzial, pk=pk)
    wymus = request.POST.get("wymus") == "1"
    licznik = 0
    for zgl in przydzial.zgloszenia.filter(status=Zgloszenie.Status.GOTOWE, pr_zamkniety=False).exclude(sha_oceniany=""):
        przed = zgl.ewaluacje.count()
        if ocena.zakolejkuj(zgl, wymus=wymus) and zgl.ewaluacje.count() > przed:
            licznik += 1
    if licznik:
        _uruchom_w_tle()
        messages.success(request, f"Dodano do kolejki oceny: {licznik}. Wyniki pojawią się w kolumnie „Ocena automatyczna”.")
    else:
        messages.info(request, "Nie ma nowych prac do oceny (oceniane są prace z oddanym kodem i otwartym pull requestem).")
    return redirect("panel:przydzial", pk=pk)


@prowadzacy
@require_POST
def zgloszenie_ocen(request, pk):
    zgl = _zgloszenie(pk)
    ewaluacja = ocena.zakolejkuj(zgl, wymus=request.POST.get("wymus") == "1")
    if ewaluacja is None:
        messages.error(request, "Brak oddanego kodu do oceny.")
    else:
        _uruchom_w_tle()
        messages.success(request, "Ocena automatyczna uruchomiona.")
    return redirect("panel:zgloszenie", pk=pk)


def _decimal(tekst, domyslna=None):
    from decimal import Decimal, InvalidOperation

    try:
        return Decimal(str(tekst).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return domyslna


@prowadzacy
@require_POST
def ewaluacja_zatwierdz(request, pk):
    from decimal import Decimal

    ewaluacja = get_object_or_404(Ewaluacja.objects.select_related("zgloszenie__przydzial__zestaw"), pk=pk)
    zgl = ewaluacja.zgloszenie
    oceny = list(ewaluacja.oceny_zadan.all())
    wartosci = {o.pk: _decimal(request.POST.get(f"pkt_{o.pk}", "")) for o in oceny}
    braki = [o.kod for o in oceny if wartosci[o.pk] is None]
    if braki:
        messages.error(request, "Uzupełnij punkty zadań: " + ", ".join(braki))
        return redirect("panel:zgloszenie", pk=zgl.pk)
    for o in oceny:
        wartosc = min(max(wartosci[o.pk], Decimal(0)), o.punkty_max)
        o.punkty_prowadzacego = wartosc if wartosc != o.punkty_konsensus else None
        o.save(update_fields=["punkty_prowadzacego"])
        regula = request.POST.get(f"prec_{o.pk}", "").strip()
        if regula and not Precedens.objects.filter(zrodlo=ewaluacja, kod_zadania=o.kod, tresc=regula).exists():
            Precedens.objects.create(zestaw=zgl.przydzial.zestaw, kod_zadania=o.kod, tresc=regula, zrodlo=ewaluacja)
    mnoznik = _decimal(request.POST.get("mnoznik", "1"), Decimal(1))
    mnoznik = min(max(mnoznik, Decimal(0)), Decimal(1))
    suma = sum((o.punkty_koncowe for o in ewaluacja.oceny_zadan.all()), Decimal(0))
    komentarz = request.POST.get("komentarz", "")
    uwagi = ocena.uwagi_do_linii(ewaluacja) if request.POST.get("uwagi") == "1" else None
    try:
        przypiete = uslugi.zamknij_z_ocena(zgl, suma, mnoznik, komentarz, rozbicie=ocena.rozbicie(ewaluacja),
                                           uwagi=uwagi, commit_id=ewaluacja.sha)
    except uslugi.BLEDY as blad:
        messages.error(request, f"Nie udało się zamknąć pull requestu: {blad}")
        return redirect("panel:zgloszenie", pk=zgl.pk)
    ewaluacja.zatwierdzona = True
    ewaluacja.komentarz = komentarz
    ewaluacja.save(update_fields=["zatwierdzona", "komentarz"])
    dopisek = " Uwagi przypięte do linii kodu." if przypiete else ""
    messages.success(request, f"Ocena wystawiona: {uslugi.liczba(zgl.wynik)} pkt.{dopisek}")
    if request.POST.get("nastepny") == "przydzial":
        return redirect("panel:przydzial", pk=zgl.przydzial_id)
    return redirect("panel:zgloszenie", pk=zgl.pk)


@prowadzacy
def modele(request):
    lista = oceniacze.skonfigurowane()
    bledne = [m for m in settings.SPRAWDZARKA_MODELE if m not in {x.identyfikator for x in lista}]
    return render(request, "kurs/panel/modele.html", {
        "modele": lista, "bledne": bledne, "tryb_testow": settings.SPRAWDZARKA_TESTY,
    })


@prowadzacy
@require_POST
def modele_sprawdz(request):
    identyfikator = request.POST.get("model", "")
    try:
        model = oceniacze.Model.z_napisu(identyfikator)
        if model.dostawca == "demo":
            messages.success(request, "Model demonstracyjny działa zawsze (nie łączy się z siecią).")
        else:
            odp = oceniacze.zapytaj(model, "Odpowiadasz wyłącznie JSON.", 'Zwróć dokładnie {"ok": true}', max_tokeny=200)
            oceniacze.wyciagnij_json(odp.tekst)
            messages.success(request, f"{identyfikator}: działa ({odp.czas_s:.1f} s).")
    except oceniacze.BladModelu as blad:
        messages.error(request, f"{identyfikator}: {blad}")
    return redirect("panel:modele")


@prowadzacy
def kalibracja(request):
    formularz = FormularzPrecedensu(request.POST or None)
    if request.method == "POST" and formularz.is_valid():
        formularz.save()
        messages.success(request, "Dodano regułę.")
        return redirect("panel:kalibracja")
    zestaw_id = request.GET.get("zestaw")
    zestaw = Zestaw.objects.filter(pk=zestaw_id).first() if zestaw_id else None
    return render(request, "kurs/panel/kalibracja.html", {
        "precedensy": Precedens.objects.select_related("zestaw"),
        "formularz": formularz,
        "statystyki": ocena.statystyki_kalibracji(zestaw),
        "zestawy": Zestaw.objects.all(),
        "wybrany": zestaw,
    })


@prowadzacy
@require_POST
def precedens_zmien(request, pk):
    precedens = get_object_or_404(Precedens, pk=pk)
    akcja = request.POST.get("akcja")
    if akcja == "usun":
        precedens.delete()
        messages.info(request, "Usunięto regułę.")
    elif akcja == "przelacz":
        precedens.aktywny = not precedens.aktywny
        precedens.save(update_fields=["aktywny"])
    elif akcja == "zapisz":
        tresc = request.POST.get("tresc", "").strip()
        if tresc:
            precedens.tresc = tresc
            precedens.save(update_fields=["tresc"])
            messages.success(request, "Zapisano regułę.")
    return redirect("panel:kalibracja")
