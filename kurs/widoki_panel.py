"""Panel prowadzacego: uczelnie, lata akademickie, przedmioty, grupy, zestawy, zestawy w grupach, zgloszenia."""
import logging
import secrets
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Count, Max, Prefetch, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import archiwum, logowanie, oceniacze, ocena, poligon, poprawki, stany, uslugi
from .github import klient as gh
from . import struktura as kopiowanie
from .forms import (
    FormularzeKopiiPrzydzialu, FormularzeZadan, FormularzEdycjiPrzydzialu, FormularzGrupy, FormularzImportu,
    FormularzKopiiKursu, FormularzKopiiZestawu, FormularzKursu, FormularzNazwiska, FormularzNowejGrupy, FormularzOceny,
    FormularzPrecedensu,
    FormularzPrzydzialu, FormularzRoku, FormularzUczelni, FormularzWyjatku, FormularzZestawu,
)
from .models import (
    Ewaluacja, Grupa, KontoProwadzacego, UstawieniaSerwisu, Kurs, NieudaneLogowanie, Poprawka, Precedens, Przydzial, Rok, Student, Uczelnia, Wypchniecie, Zestaw, Zgloszenie,
)

log = logging.getLogger(__name__)

prowadzacy = user_passes_test(lambda u: u.is_active and u.is_staff, login_url="panel:logowanie")

def _dolacz_liczniki(przydzialy):
    """Dopisuje do kazdego przydzialu liczniki stanow prac (diody), oceny i stan zestawu.

    Do oceny sa wszystkie zaakceptowane prace z repozytorium (takze bez kodu: te dostaja 0 pkt);
    ocenione to prace z wystawiona ocena. Ocena automatyczna dotyczy tylko prac z oddanym kodem."""
    przydzialy = list(przydzialy)
    mapa = {p.pk: p for p in przydzialy}
    teraz = timezone.now()
    for p in przydzialy:
        p.liczniki = {s.klucz: 0 for s in stany.PRACE}
        p.lacznie = p.testy = p.do_oceny = p.ocenione = p.do_oceny_automatycznej = 0
    wiersze = (
        Zgloszenie.objects.filter(przydzial_id__in=mapa).exclude(status=Zgloszenie.Status.WYZEROWANE)
        .values_list("przydzial_id", "status", "stan_kolor", "sugerowany_mnoznik", "wyjatek_termin_miekki",
                     "testy_zmienione", "pr_zamkniety", "sha_oceniany", "punkty")
    )
    for pk, status, kolor, mnoznik, wyjatek, testy, zamkniety, sha, punkty in wiersze:
        p = mapa[pk]
        p.liczniki[stany.praca(status, kolor, mnoznik, wyjatek or p.termin_miekki, teraz).klucz] += 1
        p.lacznie += 1
        p.testy += bool(testy)
        if status != Zgloszenie.Status.GOTOWE:
            continue
        if zamkniety or punkty is not None:
            p.ocenione += 1
        else:
            p.do_oceny += 1
            p.do_oceny_automatycznej += bool(sha)
    for p in przydzialy:
        p.paski = [(s, p.liczniki[s.klucz]) for s in stany.PRACE if p.liczniki[s.klucz]]
        p.stan = stany.zestaw(p.publikacja, p.termin_miekki, p.termin_twardy, teraz, p.do_oceny)
        p.do_ocenienia = p.ocenione + p.do_oceny
        p.dioda_ocen = _dioda_ocen(p, teraz)
    return przydzialy


def _dioda_ocen(p, teraz) -> str:
    """Szara przed terminem ostatecznym; po nim czerwona (nic nie ocenione), fioletowa (czesc), zielona (wszystko)."""
    if teraz <= p.termin_twardy or not p.do_ocenienia:
        return "wylaczona"
    if not p.ocenione:
        return "czerwony"
    return "zielony" if p.ocenione == p.do_ocenienia else "fiolet"


def _zadan(n: int) -> str:
    """1 zadanie, 2 zadania, 5 zadan (odmiana przez liczby)."""
    if n == 1:
        return "1 zadanie"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} zadania"
    return f"{n} zadań"


def _zamrozony(request, rok) -> bool:
    """Rok zarchiwizowany jest tylko do odczytu."""
    if rok.zarchiwizowany:
        messages.error(request, f"Rok {rok.nazwa} jest zarchiwizowany. Przywróć go, aby wprowadzać zmiany.")
        return True
    return False


def _grupy_z_przydzialami(grupy):
    """Dopisuje do grup liste przydzialow z licznikami (jednym zapytaniem dla wszystkich)."""
    grupy = list(grupy)
    przydzialy = _dolacz_liczniki(
        Przydzial.objects.filter(grupa__in=grupy).select_related("zestaw", "grupa").order_by("termin_miekki"))
    for g in grupy:
        g.lista = [p for p in przydzialy if p.grupa_id == g.pk]
        g.zestawy_lista = [p for p in g.lista if not p.zestaw.kolokwium]
        g.kolokwia_lista = [p for p in g.lista if p.zestaw.kolokwium]
    return grupy


@prowadzacy
def pulpit(request):
    kursy = list(Kurs.objects.filter(ukryty=False, rok__ukryty=False, rok__zarchiwizowany=False)
                 .select_related("rok__uczelnia"))
    kursy.sort(key=lambda k: k.rok.poligon)  # poligon na koncu, pod prawdziwymi przedmiotami
    grupy = _grupy_z_przydzialami(Grupa.objects.filter(kurs__in=kursy, aktywna=True).select_related("kurs"))
    for kurs in kursy:
        kurs.lista_grup = [g for g in grupy if g.kurs_id == kurs.pk]
    zalegle = [p for g in grupy for p in g.lista if p.stan.klucz == "zalegle"]
    return render(request, "kurs/panel/pulpit.html", {
        "kursy": kursy,
        "zalegle": zalegle,
        "ukrytych": Kurs.objects.filter(Q(ukryty=True) | Q(rok__ukryty=True) | Q(rok__zarchiwizowany=True)).count(),
        "kandydaci": Student.objects.filter(imie_nazwisko="").exclude(kandydat_readme="").count(),
        "bez_nazwiska": Student.objects.filter(imie_nazwisko="").count(),
        "bledy": Zgloszenie.objects.filter(status=Zgloszenie.Status.BLAD)
                 .exclude(przydzial__zestaw__kurs__rok__zarchiwizowany=True).select_related("student", "przydzial"),
        "brak_kursow": not Kurs.objects.exists(),
    })


# --- Struktura: uczelnie, lata akademickie, przedmioty ---

def _kursy_z_licznikami():
    return Kurs.objects.annotate(
        liczba_grup=Count("grupy", distinct=True),
        liczba_zestawow=Count("zestawy", filter=Q(zestawy__rodzaj=Zestaw.Rodzaj.ZESTAW), distinct=True),
        liczba_kolokwiow=Count("zestawy", filter=Q(zestawy__rodzaj=Zestaw.Rodzaj.KOLOKWIUM), distinct=True),
    ).order_by("nazwa")


def _uczelnie_z_latami(kursy: Prefetch):
    """Uczelnie z latami akademickimi (poligon na koncu, najnowsze lata na gorze) i przedmiotami lat."""
    lata = Rok.objects.order_by("poligon", "-nazwa").prefetch_related(kursy)
    return Uczelnia.objects.prefetch_related(Prefetch("lata", queryset=lata))


@prowadzacy
def uczelnie(request):
    rodzaj = request.POST.get("rodzaj") if request.method == "POST" else None
    f_uczelni = FormularzUczelni(request.POST if rodzaj == "uczelnia" else None, prefix="u")
    f_roku = FormularzRoku(request.POST if rodzaj == "rok" else None, prefix="r")
    for nazwa, formularz in [("uczelnia", f_uczelni), ("rok", f_roku)]:
        if rodzaj == nazwa and formularz.is_valid():
            obj = formularz.save()
            messages.success(request, f"Dodano: {obj}.")
            return redirect("panel:rok", pk=obj.pk) if nazwa == "rok" else redirect("panel:uczelnie")
    return render(request, "kurs/panel/uczelnie.html", {
        "uczelnie": _uczelnie_z_latami(Prefetch("kursy", queryset=Kurs.objects.order_by("nazwa"))),
        "f_uczelni": f_uczelni, "f_roku": f_roku,
    })


@prowadzacy
def przedmioty(request):
    formularz = FormularzKursu(request.POST or None, prefix="k")
    if request.method == "POST" and formularz.is_valid():
        kurs = formularz.save()
        messages.success(request, f"Dodano przedmiot {kurs.pelna_nazwa}.")
        return redirect("panel:kurs", pk=kurs.pk)
    return render(request, "kurs/panel/przedmioty.html", {
        "uczelnie": _uczelnie_z_latami(Prefetch("kursy", queryset=_kursy_z_licznikami())),
        "formularz": formularz,
    })


@prowadzacy
def uczelnia_edycja(request, pk):
    uczelnia = get_object_or_404(Uczelnia, pk=pk)
    formularz = FormularzUczelni(request.POST or None, instance=uczelnia)
    if request.method == "POST" and formularz.is_valid():
        formularz.save()
        messages.success(request, "Zapisano zmiany uczelni.")
        return redirect("panel:uczelnie")
    return render(request, "kurs/panel/formularz.html", {
        "tytul": f"Uczelnia: {uczelnia}", "formularz": formularz, "przycisk": "Zapisz", "wstecz": "panel:uczelnie",
        **_usuwanie(uczelnia, "panel:uczelnia_usun", "Usunięcie uczelni, która nie ma lat akademickich."),
    })


# --- Rok akademicki ---

def _rok(pk):
    return get_object_or_404(Rok.objects.select_related("uczelnia"), pk=pk)


@prowadzacy
def rok(request, pk):
    rok = _rok(pk)
    formularz = FormularzKursu(request.POST or None, rok=rok, prefix="k")
    if request.method == "POST" and not _zamrozony(request, rok) and formularz.is_valid():
        kurs = formularz.save()
        messages.success(request, f"Dodano przedmiot {kurs.nazwa}. Teraz dodaj grupy i zestawy.")
        return redirect("panel:kurs", pk=kurs.pk)
    kursy = list(_kursy_z_licznikami().filter(rok=rok))
    inne = (Kurs.objects.filter(rok__uczelnia=rok.uczelnia, rok__poligon=rok.poligon).exclude(rok=rok)
            .select_related("rok").order_by("-rok__nazwa", "nazwa"))
    return render(request, "kurs/panel/rok.html", {
        "rok": rok, "kursy": kursy, "formularz": formularz, "do_skopiowania": inne[:30],
        "zgloszen": Zgloszenie.objects.filter(przydzial__zestaw__kurs__rok=rok).count(),
    })


@prowadzacy
def rok_edycja(request, pk):
    rok = _rok(pk)
    formularz = FormularzRoku(request.POST or None, instance=rok)
    if request.method == "POST" and formularz.is_valid():
        formularz.save()
        messages.success(request, "Zapisano zmiany roku. Nazwy istniejących repozytoriów się nie zmieniają.")
        return redirect("panel:rok", pk=rok.pk)
    return render(request, "kurs/panel/formularz.html", {
        "tytul": f"Rok akademicki: {rok}", "formularz": formularz, "przycisk": "Zapisz", "wstecz_obiekt": rok,
        **_usuwanie(rok, "panel:rok_usun", "Usunięcie roku z bazy razem z przedmiotami, grupami, zestawami "
                                           "i zgłoszeniami."),
    })


@prowadzacy
@require_POST
def rok_ukryj(request, pk):
    rok = _rok(pk)
    rok.ukryty = not rok.ukryty
    rok.save(update_fields=["ukryty"])
    messages.success(request, f"Rok {rok.nazwa} {'ukryty na pulpicie' if rok.ukryty else 'widoczny na pulpicie'}.")
    return redirect("panel:rok", pk=rok.pk)


@prowadzacy
def rok_eksport(request, pk):
    rok = _rok(pk)
    odpowiedz = HttpResponse(archiwum.do_pliku(rok), content_type="application/json; charset=utf-8")
    odpowiedz["Content-Disposition"] = f'attachment; filename="{archiwum.nazwa_pliku(rok)}"'
    return odpowiedz


@prowadzacy
@require_POST
def rok_archiwizuj(request, pk):
    rok = _rok(pk)
    zamkniete = Przydzial.objects.filter(zestaw__kurs__rok=rok, otwarty=True).update(otwarty=False)
    rok.zarchiwizowany, rok.ukryty, rok.zarchiwizowano = True, True, timezone.now()
    rok.save(update_fields=["zarchiwizowany", "ukryty", "zarchiwizowano"])
    messages.success(request, f"Rok {rok.nazwa} zarchiwizowany (zamknięte zestawy: {zamkniete}). "
                              "Pobierz plik archiwum i zachowaj go w bezpiecznym miejscu.")
    return redirect("panel:rok", pk=rok.pk)


@prowadzacy
@require_POST
def rok_przywroc(request, pk):
    rok = _rok(pk)
    rok.zarchiwizowany = False
    rok.save(update_fields=["zarchiwizowany"])
    messages.success(request, f"Rok {rok.nazwa} przywrócony. Zestawy pozostają zamknięte, otworzysz je w terminach "
                              "zestawu w grupie. Rok jest nadal ukryty na pulpicie.")
    return redirect("panel:rok", pk=rok.pk)


@prowadzacy
def rok_usun(request, pk):
    rok = _rok(pk)
    blokada = kopiowanie.blokada_usuniecia(rok)
    if blokada:
        messages.error(request, blokada)
        return redirect("panel:rok", pk=rok.pk)
    if request.method == "POST":
        if request.POST.get("potwierdzenie", "").strip() != rok.nazwa:
            messages.error(request, f"Aby usunąć, wpisz dokładnie: {rok.nazwa}")
        else:
            opis = rok.pelna_nazwa
            archiwum.usun(rok)
            if rok.poligon:
                poligon.usun_resztki()
                messages.success(request, f"Usunięto z bazy: {opis}.")
            else:
                messages.success(request, f"Usunięto z bazy: {opis}. Odtworzysz go z pliku archiwum.")
            return redirect("panel:uczelnie")
    return render(request, "kurs/panel/rok_usun.html", {
        "rok": rok,
        "zgloszen": Zgloszenie.objects.filter(przydzial__zestaw__kurs__rok=rok).count(),
        "przedmiotow": rok.kursy.count(), "grup": Grupa.objects.filter(kurs__rok=rok).count(),
        "zestawow": Zestaw.objects.filter(kurs__rok=rok).count(),
    })


# --- Przedmiot (kurs w roku akademickim) ---

def _kurs(pk):
    return get_object_or_404(Kurs.objects.select_related("rok__uczelnia"), pk=pk)


@prowadzacy
def kurs(request, pk):
    kurs = _kurs(pk)
    formularz = FormularzGrupy(request.POST or None, kurs=kurs, prefix="g")
    if request.method == "POST" and not _zamrozony(request, kurs.rok) and formularz.is_valid():
        grupa = formularz.save()
        messages.success(request, f"Dodano grupę {grupa}.")
        return redirect("panel:kurs", pk=kurs.pk)
    grupy = _grupy_z_przydzialami(kurs.grupy.all())
    zestawy = kurs.zestawy.annotate(liczba_grup=Count("przydzialy")).order_by("rodzaj", "skrot")
    return render(request, "kurs/panel/kurs.html", {
        "kurs": kurs, "rok": kurs.rok, "grupy": grupy, "formularz": formularz,
        "zestawy": [z for z in zestawy if not z.kolokwium], "kolokwia": [z for z in zestawy if z.kolokwium],
    })


@prowadzacy
def kurs_edycja(request, pk):
    kurs = _kurs(pk)
    formularz = FormularzKursu(request.POST or None, instance=kurs)
    if request.method == "POST" and formularz.is_valid():
        formularz.save()
        messages.success(request, "Zapisano zmiany przedmiotu. Nazwy istniejących repozytoriów się nie zmieniają.")
        return redirect("panel:kurs", pk=kurs.pk)
    return render(request, "kurs/panel/formularz.html", {
        "tytul": f"Przedmiot: {kurs}", "formularz": formularz, "przycisk": "Zapisz", "wstecz_obiekt": kurs,
        **_usuwanie(kurs, "panel:kurs_usun", "Usunięcie przedmiotu razem z grupami, zestawami i zgłoszeniami "
                                             "studentów. Repozytoria na GitHubie zostają."),
    })


@prowadzacy
@require_POST
def kurs_ukryj(request, pk):
    kurs = _kurs(pk)
    kurs.ukryty = not kurs.ukryty
    kurs.save(update_fields=["ukryty"])
    messages.success(request, f"{kurs.nazwa} {'ukryty na pulpicie' if kurs.ukryty else 'widoczny na pulpicie'}.")
    return redirect("panel:kurs", pk=kurs.pk)


@prowadzacy
def kurs_kopiuj(request, pk):
    kurs = _kurs(pk)
    formularz = FormularzKopiiKursu(request.POST or None, kurs=kurs, initial={"rok": request.GET.get("rok")})
    if request.method == "POST" and formularz.is_valid():
        dane = formularz.cleaned_data
        nowy = kopiowanie.kopiuj_kurs(kurs, dane["rok"], dane["nazwa"], dane["skrot"], dane["grupy"])
        messages.success(request, f"Skopiowano {kurs.nazwa} do roku {nowy.rok.nazwa}: zestawy i kolokwia "
                                  f"{nowy.zestawy.count()}" + (f", grupy {nowy.grupy.count()}" if dane["grupy"] else "")
                         + ". Teraz wydaj zestawy grupom z nowymi terminami.")
        return redirect("panel:kurs", pk=nowy.pk)
    return render(request, "kurs/panel/formularz.html", {
        "tytul": f"Kopiuj do innego roku: {kurs}", "formularz": formularz, "przycisk": "Kopiuj",
        "wstecz_obiekt": kurs,
        "opis": "Powstanie przedmiot w wybranym roku ze wszystkimi zestawami i kolokwiami: te same ustawienia, zadania, "
                "kryteria, PDF i reguły kalibracji. Wydań grupom, terminów i zgłoszeń studentów się nie kopiuje.",
    })


# --- Poligon: dane fikcyjne do prob ---

@prowadzacy
@require_POST
def poligon_utworz(request):
    try:
        rok = poligon.utworz()
    except poligon.BladPoligonu as blad:
        messages.error(request, str(blad))
        return redirect("panel:ustawienia")
    messages.success(request, f"Poligon gotowy: {rok.pelna_nazwa} ("
                     + ", ".join(k.nazwa for k in rok.kursy.all())
                     + "). To dane fikcyjne, nie łączą się z GitHubem. Usuniesz je w całości w Ustawieniach.")
    return redirect("panel:pulpit")


@prowadzacy
def poligon_usun(request):
    lata = list(Rok.objects.filter(poligon=True))
    if not lata:
        messages.info(request, "Poligonu nie ma.")
        return redirect("panel:ustawienia")
    if request.method == "POST":
        if request.POST.get("potwierdzenie", "").strip() != poligon.SKROT_ROKU:
            messages.error(request, f"Aby usunąć, wpisz dokładnie: {poligon.SKROT_ROKU}")
        else:
            wynik = poligon.usun()
            messages.success(request, f"Poligon usunięty: przedmioty {wynik['przedmiotow']}, zgłoszenia "
                                      f"{wynik['zgloszen']}, fikcyjne konta studentów {wynik['studentow']}.")
            return redirect("panel:ustawienia")
    zgloszen = Zgloszenie.objects.filter(przydzial__zestaw__kurs__rok__in=lata).count()
    kursy = _kursy_z_licznikami().filter(rok__in=lata).select_related("rok")
    return render(request, "kurs/panel/usun.html", {
        "tytul": "Usuń poligon",
        "punkty": [f"rok {r.pelna_nazwa}" for r in lata]
                  + [f"{k.nazwa}: grupy {k.liczba_grup}, zestawy {k.liczba_zestawow}, kolokwia {k.liczba_kolokwiow}"
                     for k in kursy]
                  + [f"zgłoszenia fikcyjnych studentów: {zgloszen}, z wypchnięciami i ocenami",
                     "fikcyjne konta studentów"],
        "uwaga": "Prawdziwe lata, przedmioty, grupy i studenci zostają bez zmian.",
        "potwierdzenie": poligon.SKROT_ROKU, "przycisk": "Usuń poligon", "wstecz": reverse("panel:ustawienia"),
    })


# --- Usuwanie z potwierdzeniem (wpisanie nazwy, jak na GitHubie) ---

GITHUB_ZOSTAJE = "Repozytoria na GitHubie zostają nietknięte. Konta studentów zostają."


def _usuwanie(obiekt, nazwa_url: str, opis: str) -> dict:
    """Kontekst sekcji Usuwanie na stronie edycji."""
    return {"usun_url": reverse(nazwa_url, args=[obiekt.pk]), "usun_opis": opis,
            "usun_blokada": kopiowanie.blokada_usuniecia(obiekt)}


def _potwierdz_usuniecie(request, obiekt, *, tytul, punkty, potwierdzenie, wykonaj, po, wstecz, kurs=None, uwaga=""):
    blokada = kopiowanie.blokada_usuniecia(obiekt)
    if blokada:
        messages.error(request, blokada)
        return redirect(wstecz)
    if request.method == "POST":
        if request.POST.get("potwierdzenie", "").strip() != potwierdzenie:
            messages.error(request, f"Aby usunąć, wpisz dokładnie: {potwierdzenie}")
        else:
            messages.success(request, wykonaj())
            return redirect(po)
    return render(request, "kurs/panel/usun.html", {
        "tytul": tytul, "kurs": kurs, "punkty": punkty, "uwaga": uwaga, "potwierdzenie": potwierdzenie,
        "przycisk": "Usuń", "wstecz": wstecz,
    })


@prowadzacy
def uczelnia_usun(request, pk):
    uczelnia = get_object_or_404(Uczelnia, pk=pk)

    def wykonaj():
        nazwa = uczelnia.nazwa
        uczelnia.delete()
        return f"Usunięto uczelnię {nazwa}."
    return _potwierdz_usuniecie(
        request, uczelnia, tytul=f"Usuń uczelnię: {uczelnia.nazwa}", punkty=[f"uczelnia {uczelnia.nazwa}"],
        potwierdzenie=uczelnia.skrot, wykonaj=wykonaj, po=reverse("panel:uczelnie"),
        wstecz=reverse("panel:uczelnia_edycja", args=[pk]))


@prowadzacy
def kurs_usun(request, pk):
    kurs = _kurs(pk)

    def wykonaj():
        nazwa = str(kurs)
        wynik = kopiowanie.usun_kurs(kurs)
        return (f"Usunięto przedmiot {nazwa}: grupy {wynik['grup']}, zestawy i kolokwia {wynik['zestawow']}, "
                f"zgłoszenia {wynik['zgloszen']}.")
    return _potwierdz_usuniecie(
        request, kurs, tytul=f"Usuń przedmiot: {kurs}", kurs=kurs,
        punkty=[f"przedmiot {kurs.nazwa} w roku {kurs.rok.pelna_nazwa}",
                f"grupy: {kurs.grupy.count()}",
                f"zestawy i kolokwia z zadaniami, kryteriami i PDF: {kurs.zestawy.count()}",
                f"zgłoszenia studentów z wypchnięciami i ocenami: "
                f"{Zgloszenie.objects.filter(przydzial__zestaw__kurs=kurs).count()}"],
        uwaga="To przedmiot z poligonu (dane fikcyjne)." if kurs.poligon else GITHUB_ZOSTAJE,
        potwierdzenie=kurs.nazwa, wykonaj=wykonaj, po=reverse("panel:rok", args=[kurs.rok_id]),
        wstecz=reverse("panel:kurs_edycja", args=[pk]))


@prowadzacy
def grupa_usun(request, pk):
    grupa = get_object_or_404(Grupa.objects.select_related("kurs__rok__uczelnia"), pk=pk)

    def wykonaj():
        nazwa = grupa.nazwa
        wynik = kopiowanie.usun_grupe(grupa)
        return f"Usunięto grupę {nazwa}: zestawy w grupie {wynik['zestawow']}, zgłoszenia {wynik['zgloszen']}."
    return _potwierdz_usuniecie(
        request, grupa, tytul=f"Usuń grupę: {grupa.nazwa}", kurs=grupa.kurs,
        punkty=[f"grupa {grupa.nazwa} ({grupa.kurs})",
                f"zestawy i kolokwia wydane tej grupie: {grupa.przydzialy.count()}",
                f"zgłoszenia studentów z wypchnięciami i ocenami: "
                f"{Zgloszenie.objects.filter(przydzial__grupa=grupa).count()}"],
        uwaga=("To grupa z poligonu (dane fikcyjne)." if grupa.kurs.poligon else
               GITHUB_ZOSTAJE + " Definicje zestawów w przedmiocie zostają, możesz je wydać innej grupie."),
        potwierdzenie=grupa.nazwa, wykonaj=wykonaj, po=reverse("panel:kurs", args=[grupa.kurs_id]),
        wstecz=reverse("panel:grupa_edycja", args=[pk]))


@prowadzacy
def zestaw_usun(request, pk):
    zestaw = get_object_or_404(Zestaw.objects.select_related("kurs__rok__uczelnia"), pk=pk)

    def wykonaj():
        nazwa = zestaw.nazwa
        wynik = kopiowanie.usun_zestaw(zestaw)
        return f"Usunięto {nazwa}: wydania w grupach {wynik['grup']}, zgłoszenia {wynik['zgloszen']}."
    return _potwierdz_usuniecie(
        request, zestaw, tytul=f"Usuń: {zestaw.nazwa}", kurs=zestaw.kurs,
        punkty=[f"{zestaw.get_rodzaj_display()} {zestaw.nazwa} z zadaniami, kryteriami i PDF",
                f"wydania w grupach: {zestaw.przydzialy.count()}",
                f"zgłoszenia studentów z wypchnięciami i ocenami: "
                f"{Zgloszenie.objects.filter(przydzial__zestaw=zestaw).count()}"],
        uwaga="" if zestaw.kurs.poligon else GITHUB_ZOSTAJE + " Repozytorium źródłowe także.",
        potwierdzenie=zestaw.skrot, wykonaj=wykonaj, po=reverse("panel:kurs", args=[zestaw.kurs_id]),
        wstecz=reverse("panel:zestaw_edycja", args=[pk]))


@prowadzacy
def przydzial_usun(request, pk):
    przydzial = get_object_or_404(Przydzial.objects.select_related("zestaw__kurs__rok__uczelnia", "grupa"), pk=pk)
    potwierdzenie = f"{przydzial.grupa.skrot}/{przydzial.zestaw.skrot}"

    def wykonaj():
        nazwa = f"{przydzial.grupa.nazwa}: {przydzial.zestaw.nazwa}"
        ile = kopiowanie.usun_przydzial(przydzial)
        return f"Usunięto {nazwa} (zgłoszenia: {ile}). Zestaw w przedmiocie zostaje."
    return _potwierdz_usuniecie(
        request, przydzial, tytul=f"Usuń z grupy: {przydzial.grupa.nazwa}: {przydzial.zestaw.nazwa}",
        kurs=przydzial.zestaw.kurs,
        punkty=[f"wydanie zestawu {przydzial.zestaw.nazwa} grupie {przydzial.grupa.nazwa} (link i terminy)",
                f"zgłoszenia studentów z wypchnięciami i ocenami: {przydzial.zgloszenia.count()}"],
        uwaga="" if przydzial.w_poligonie else GITHUB_ZOSTAJE + " Definicja zestawu w przedmiocie zostaje.",
        potwierdzenie=potwierdzenie, wykonaj=wykonaj, po=reverse("panel:grupa", args=[przydzial.grupa_id]),
        wstecz=reverse("panel:przydzial_edycja", args=[pk]))


# --- Logowanie do panelu ---

class LogowanieProwadzacego(auth_views.LoginView):
    """Logowanie przez GitHub (wskazane konta) albo haslem, z blokada po kilku nieudanych probach."""

    template_name = "kurs/panel/logowanie.html"

    def get_context_data(self, **kwargs):
        kontekst = super().get_context_data(**kwargs)
        kontekst.update(github=logowanie.github_wlaczony(), haslo=settings.PANEL_LOGOWANIE_HASLEM,
                        limit_prob=settings.PANEL_LIMIT_PROB, blokada_minut=settings.PANEL_BLOKADA_MINUT)
        return kontekst

    def post(self, request, *args, **kwargs):
        if not settings.PANEL_LOGOWANIE_HASLEM:
            messages.error(request, "Logowanie hasłem jest wyłączone. Zaloguj się przez GitHub.")
            return redirect(request.get_full_path())
        if logowanie.zablokowane(logowanie.adres_ip(request), request.POST.get("username", "")):
            messages.error(request, f"Za dużo nieudanych prób logowania. Spróbuj ponownie za "
                                    f"{settings.PANEL_BLOKADA_MINUT} minut albo zaloguj się przez GitHub.")
            return redirect(request.get_full_path())
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        logowanie.zapisz_nieudane(logowanie.adres_ip(self.request), self.request.POST.get("username", ""))
        return super().form_invalid(form)

    def form_valid(self, form):
        logowanie.wyczysc(logowanie.adres_ip(self.request), form.get_user().get_username())
        return super().form_valid(form)


def logowanie_github(request):
    """Poczatek logowania do panelu przez GitHub (powrot obsluguje strona logowania studentow)."""
    if not logowanie.github_wlaczony():
        messages.error(request, "Logowanie przez GitHub nie jest skonfigurowane (PANEL_KONTA_GITHUB w pliku .env).")
        return redirect("panel:logowanie")
    if inny := logowanie.adres_pod_publicznym_hostem(request):
        return redirect(inny)
    nastepny = request.GET.get("next") or reverse("panel:pulpit")
    if not url_has_allowed_host_and_scheme(nastepny, allowed_hosts={request.get_host()}):
        nastepny = reverse("panel:pulpit")
    stan = secrets.token_urlsafe(16)
    request.session.update({"gh_stan": stan, "gh_nastepny": nastepny, "gh_cel": "panel"})
    return redirect(gh.adres_autoryzacji(settings.PUBLICZNY_ADRES + reverse("student:powrot"), stan))


# --- Ustawienia: modele, dostep do panelu, archiwum roku, poligon ---

@prowadzacy
def ustawienia(request):
    od = timezone.now() - timedelta(days=1)
    powiazane = {k.login.lower(): k for k in KontoProwadzacego.objects.all()}
    modele = oceniacze.skonfigurowane()
    return render(request, "kurs/panel/ustawienia.html", {
        "modele": modele, "tryb_testow": settings.SPRAWDZARKA_TESTY,
        "bledne": [m for m in settings.SPRAWDZARKA_MODELE if m not in {x.identyfikator for x in modele}],
        "lata_poligonu": Rok.objects.filter(poligon=True).select_related("uczelnia").prefetch_related("kursy"),
        "uzytkownik_admin": request.user.is_superuser,
        "konta_github": [(k, powiazane.get(k)) for k in settings.PANEL_KONTA_GITHUB],
        "github_wlaczony": logowanie.github_wlaczony(), "haslo": settings.PANEL_LOGOWANIE_HASLEM,
        "limit_prob": settings.PANEL_LIMIT_PROB, "blokada_minut": settings.PANEL_BLOKADA_MINUT,
        "nieudane": NieudaneLogowanie.objects.filter(czas__gte=od).count(),
        "f_importu": FormularzImportu(),
    })


def _do_ustawien(kotwica: str):
    return redirect(reverse("panel:ustawienia") + kotwica)


@prowadzacy
@require_POST
def tryb_serwisowy(request):
    ustawienia = UstawieniaSerwisu.biezace()
    ustawienia.tryb_serwisowy = request.POST.get("wlacz") == "1"
    ustawienia.save()
    if ustawienia.tryb_serwisowy:
        messages.warning(request, "Tryb serwisowy włączony: linki dla studentów pokazują stronę o pracach serwisowych.")
    else:
        messages.success(request, "Tryb serwisowy wyłączony: linki dla studentów działają normalnie.")
    return _do_ustawien("#serwis")


@prowadzacy
@require_POST
def wczytaj_archiwum(request):
    formularz = FormularzImportu(request.POST, request.FILES)
    if not formularz.is_valid():
        messages.error(request, "Wybierz plik archiwum.")
        return _do_ustawien("#archiwum")
    try:
        rok = archiwum.wczytaj_plik(formularz.cleaned_data["plik"].read())
    except archiwum.BladArchiwum as blad:
        messages.error(request, f"Nie wczytano archiwum: {blad}")
        return _do_ustawien("#archiwum")
    messages.success(request, f"Wczytano z archiwum: {rok}.")
    return redirect("panel:rok", pk=rok.pk)


def modele(request):
    """Dawny adres strony modeli: teraz sekcja Ustawien."""
    return _do_ustawien("#modele")


# --- Grupy ---

def _grupy_z_licznikami():
    return Grupa.objects.annotate(
        liczba_zestawow=Count("przydzialy", filter=Q(przydzialy__zestaw__rodzaj=Zestaw.Rodzaj.ZESTAW), distinct=True),
        liczba_kolokwiow=Count("przydzialy", filter=Q(przydzialy__zestaw__rodzaj=Zestaw.Rodzaj.KOLOKWIUM), distinct=True),
    ).order_by("nazwa")


@prowadzacy
def grupy(request):
    formularz = FormularzNowejGrupy(request.POST or None, prefix="g")
    if request.method == "POST" and formularz.is_valid():
        grupa = formularz.save()
        messages.success(request, f"Dodano grupę {grupa.nazwa} ({grupa.kurs.pelna_nazwa}).")
        return redirect("panel:grupa", pk=grupa.pk)
    kursy = Kurs.objects.order_by("nazwa").prefetch_related(Prefetch("grupy", queryset=_grupy_z_licznikami()))
    uczelnie = list(_uczelnie_z_latami(Prefetch("kursy", queryset=kursy)))
    studenci = dict(Zgloszenie.objects.exclude(status=Zgloszenie.Status.WYZEROWANE).values("przydzial__grupa_id")
                    .annotate(n=Count("student", distinct=True)).values_list("przydzial__grupa_id", "n"))
    for u in uczelnie:
        for r in u.lata.all():
            for k in r.kursy.all():
                for g in k.grupy.all():
                    g.liczba_studentow = studenci.get(g.pk, 0)
    return render(request, "kurs/panel/grupy.html", {"uczelnie": uczelnie, "formularz": formularz})


@prowadzacy
def grupa(request, pk):
    grupa = get_object_or_404(Grupa.objects.select_related("kurs__rok__uczelnia"), pk=pk)
    formularz = FormularzPrzydzialu(request.POST or None, grupa=grupa)
    if request.method == "POST" and not _zamrozony(request, grupa.kurs.rok) and formularz.is_valid():
        przydzial = formularz.save()
        return _po_wydaniu(request, przydzial)
    [grupa] = _grupy_z_przydzialami([grupa])
    return render(request, "kurs/panel/grupa.html", {"grupa": grupa, "kurs": grupa.kurs, "formularz": formularz})


@prowadzacy
def grupa_edycja(request, pk):
    grupa = get_object_or_404(Grupa.objects.select_related("kurs__rok"), pk=pk)
    formularz = FormularzGrupy(request.POST or None, instance=grupa)
    if request.method == "POST" and formularz.is_valid():
        formularz.save()
        messages.success(request, "Zapisano zmiany grupy.")
        return redirect("panel:grupa", pk=grupa.pk)
    return render(request, "kurs/panel/formularz.html", {
        "tytul": f"Edycja grupy: {grupa}", "formularz": formularz, "przycisk": "Zapisz", "wstecz_obiekt": grupa,
        **_usuwanie(grupa, "panel:grupa_usun", "Usunięcie grupy razem z wydanymi jej zestawami i zgłoszeniami "
                                              "studentów. Repozytoria na GitHubie zostają."),
    })


def _po_wydaniu(request, przydzial):
    blad = kopiowanie.utworz_repo(przydzial)
    if blad:
        messages.warning(request, f"Zestaw wydany, ale repozytorium startowego nie udało się utworzyć: {blad}")
    else:
        messages.success(request, f"Zestaw wydany grupie {przydzial.grupa}. Repozytorium startowe gotowe.")
    return redirect("panel:przydzial", pk=przydzial.pk)


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
    if "kurs" in request.GET:
        kurs = Kurs.objects.filter(pk=request.GET.get("kurs") or 0, rok__zarchiwizowany=False).first()
        if kurs:
            kolokwium = "?rodzaj=kolokwium" if request.GET.get("rodzaj") == "kolokwium" else ""
            return redirect(reverse("panel:zestaw_nowy", args=[kurs.pk]) + kolokwium)
        messages.error(request, "Wybierz przedmiot.")
    lista = Zestaw.objects.annotate(liczba_grup=Count("przydzialy")).order_by("-rodzaj", "skrot")
    kursy = Kurs.objects.order_by("nazwa").prefetch_related(Prefetch("zestawy", queryset=lista))
    return render(request, "kurs/panel/zestawy.html", {
        "uczelnie": _uczelnie_z_latami(Prefetch("kursy", queryset=kursy)),
        "kursy_do_wyboru": Kurs.objects.filter(rok__zarchiwizowany=False).select_related("rok__uczelnia")
                           .order_by("rok__poligon", "-rok__nazwa", "nazwa"),
    })


@prowadzacy
def zestaw_nowy(request, pk):
    kurs = _kurs(pk)
    if _zamrozony(request, kurs.rok):
        return redirect("panel:kurs", pk=kurs.pk)
    rodzaj = request.GET.get("rodzaj", Zestaw.Rodzaj.ZESTAW)
    formularz = FormularzZestawu(request.POST or None, request.FILES or None, kurs=kurs, initial={"rodzaj": rodzaj})
    if request.method == "POST" and formularz.is_valid():
        zestaw = formularz.save()
        _odczytaj_pdf(request, zestaw, formularz)
        messages.success(request, f"Dodano zestaw {zestaw}. Teraz wydaj go grupom.")
        try:
            liczba_zadan = ocena.utworz_zadania_z_katalogow(zestaw, zastap=False)
            if liczba_zadan:
                messages.info(request, f"Utworzono {_zadan(liczba_zadan)} na podstawie katalogów repozytorium. Sprawdź punkty.")
        except ocena.BladOceny as blad:
            log.info("Brak propozycji zadan z katalogow dla %s: %s", zestaw, blad)
        return redirect("panel:zestaw", pk=zestaw.pk)
    kolokwium = rodzaj == Zestaw.Rodzaj.KOLOKWIUM
    return render(request, "kurs/panel/formularz.html", {
        "tytul": f"{'Nowe kolokwium' if kolokwium else 'Nowy zestaw'}: {kurs}", "formularz": formularz,
        "przycisk": "Dodaj", "pliki": True, "wstecz_obiekt": kurs,
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
        **_usuwanie(zestaw, "panel:zestaw_usun", "Usunięcie zestawu z zadaniami oraz jego wydań we wszystkich "
                                                "grupach, ze zgłoszeniami. Repozytoria na GitHubie zostają."),
    })


@prowadzacy
def zestaw(request, pk):
    zestaw = get_object_or_404(Zestaw.objects.select_related("kurs__rok__uczelnia"), pk=pk)
    formularz = FormularzPrzydzialu(request.POST or None, zestaw=zestaw)
    if request.method == "POST" and not _zamrozony(request, zestaw.kurs.rok) and formularz.is_valid():
        przydzial = formularz.save()
        return _po_wydaniu(request, przydzial)
    przydzialy = _dolacz_liczniki(zestaw.przydzialy.select_related("grupa").order_by("grupa__nazwa"))
    zadania = list(zestaw.zadania.all())
    return render(request, "kurs/panel/zestaw.html", {
        "zestaw": zestaw, "przydzialy": przydzialy, "formularz": formularz, "zadania": zadania,
        "suma_zadan": sum((z.punkty for z in zadania), 0),
        "modele": oceniacze.dla_zestawu(zestaw),
    })


@prowadzacy
def zestaw_kopiuj(request, pk):
    zestaw = get_object_or_404(Zestaw.objects.select_related("kurs__rok__uczelnia"), pk=pk)
    formularz = FormularzKopiiZestawu(request.POST or None, zestaw=zestaw)
    if request.method == "POST" and formularz.is_valid():
        dane = formularz.cleaned_data
        nowy = kopiowanie.kopiuj_zestaw(zestaw, dane["kurs"], dane["skrot"], dane["nazwa"])
        messages.success(request, f"Skopiowano zestaw z zadaniami do: {nowy.kurs.pelna_nazwa}. Teraz wydaj go grupom.")
        return redirect("panel:zestaw", pk=nowy.pk)
    return render(request, "kurs/panel/formularz.html", {
        "tytul": f"Kopiuj do innego przedmiotu: {zestaw}", "formularz": formularz, "przycisk": "Kopiuj",
        "wstecz_obiekt": zestaw,
        "opis": "Powstanie nowy zestaw w wybranym przedmiocie: te same ustawienia, zadania, kryteria, PDF i reguły "
                "kalibracji. Zgłoszenia studentów nie są kopiowane. Cały przedmiot ze wszystkimi zestawami skopiujesz "
                "na stronie przedmiotu.",
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
    przydzial = get_object_or_404(Przydzial.objects.select_related("zestaw__kurs__rok__uczelnia", "grupa"), pk=pk)
    zgloszenia = list(
        przydzial.zgloszenia.exclude(status=Zgloszenie.Status.WYZEROWANE).select_related("student")
        .prefetch_related(Prefetch("ewaluacje", queryset=Ewaluacja.objects.order_by("-utworzono")))
    )
    zgloszenia.sort(key=lambda z: z.student.klucz_sortowania)
    for z in zgloszenia:
        lista = list(z.ewaluacje.all())
        z.ostatnia_ewaluacja = lista[0] if lista else None
    w_toku = any(z.ostatnia_ewaluacja and z.ostatnia_ewaluacja.w_toku for z in zgloszenia)
    odcisk = ocena.odcisk_konfiguracji(przydzial.zestaw)
    for z in zgloszenia:
        e = z.ostatnia_ewaluacja
        z.ocena_nieaktualna = bool(e and e.status == Ewaluacja.Status.GOTOWA and not e.zatwierdzona
                                   and (e.sha != z.sha_oceniany or e.konfiguracja != odcisk))
        z.do_wyslania = bool(e and e.status == Ewaluacja.Status.GOTOWA and not e.zatwierdzona and not z.pr_zamkniety
                             and not z.ocena_nieaktualna)
    filtr = request.GET.get("stan", "")
    if filtr == "testy":
        widoczne = [z for z in zgloszenia if z.testy_zmienione]
    elif filtr == "do_oceny":
        widoczne = [z for z in zgloszenia if z.status == Zgloszenie.Status.GOTOWE and not z.oceniona]
    elif filtr == "oceniono":
        widoczne = [z for z in zgloszenia if z.status == Zgloszenie.Status.GOTOWE and z.oceniona]
    elif filtr:
        widoczne = [z for z in zgloszenia if z.stan_pracy.klucz == filtr]
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
        "do_wyslania": sum(1 for z in widoczne if z.do_wyslania),
    })


@prowadzacy
def przydzial_edycja(request, pk):
    przydzial = get_object_or_404(Przydzial, pk=pk)
    formularz = FormularzEdycjiPrzydzialu(request.POST or None, instance=przydzial)
    if request.method == "POST" and formularz.is_valid():
        if "termin_twardy" in formularz.changed_data:
            przydzial.zamkniety_po_terminie = None  # nowy termin ostateczny: po nim zestaw znow zamknie sie sam
        formularz.save()
        przydzial.zgloszenia.update(do_przeliczenia=True)
        if przydzial.w_poligonie:
            # Poligon nie ma robota: przeliczenie od razu, z bazy
            uslugi.odswiez_przydzial(przydzial, synchronizacja=False)
            messages.success(request, "Zapisano terminy i przeliczono stany zgłoszeń.")
        else:
            messages.success(request, "Zapisano terminy. Stany zgłoszeń zostaną przeliczone.")
        return redirect("panel:przydzial", pk=przydzial.pk)
    return render(request, "kurs/panel/formularz.html", {
        "tytul": f"Terminy: {przydzial}", "formularz": formularz, "przycisk": "Zapisz", "wstecz_obiekt": przydzial,
        **_usuwanie(przydzial, "panel:przydzial_usun", "Usunięcie wydania zestawu tej grupie razem ze zgłoszeniami "
                                                      "studentów. Zestaw w przedmiocie i repozytoria na GitHubie "
                                                      "zostają."),
    })


@prowadzacy
def przydzial_kopiuj(request, pk):
    wzor = get_object_or_404(Przydzial.objects.select_related("zestaw__kurs__rok__uczelnia", "grupa"), pk=pk)
    kurs = wzor.zestaw.kurs
    kandydaci = list(Grupa.objects.filter(kurs=kurs, aktywna=True).exclude(przydzialy__zestaw=wzor.zestaw))
    przyszla = wzor.publikacja if wzor.publikacja and wzor.publikacja > timezone.now() else None
    poczatkowe = [{
        "grupa": g.pk, "publikacja": przyszla, "termin_miekki": wzor.termin_miekki, "termin_twardy": wzor.termin_twardy,
        "przedrostek_repo": Przydzial.domyslny_przedrostek(wzor.zestaw, g), "repo_zrodlowe": wzor.repo_zrodlowe,
    } for g in kandydaci]
    zestaw_formularzy = FormularzeKopiiPrzydzialu(request.POST or None, initial=poczatkowe)
    if request.method == "POST" and not _zamrozony(request, kurs.rok) and zestaw_formularzy.is_valid():
        mapa = {g.pk: g for g in kandydaci}
        wybrane = [f.cleaned_data for f in zestaw_formularzy.forms
                   if f.cleaned_data.get("wybrana") and f.cleaned_data.get("grupa") in mapa]
        if not wybrane:
            messages.warning(request, "Zaznacz co najmniej jedną grupę.")
        else:
            gotowe, bledy = [], []
            for dane in wybrane:
                nowy = kopiowanie.kopiuj_przydzial(
                    wzor, mapa[dane["grupa"]], dane["termin_miekki"], dane["termin_twardy"],
                    dane["przedrostek_repo"], dane["repo_zrodlowe"], dane["publikacja"])
                blad = kopiowanie.utworz_repo(nowy)
                (bledy if blad else gotowe).append(f"{nowy.grupa.nazwa}" + (f" ({blad})" if blad else ""))
            if gotowe:
                messages.success(request, "Zestaw wydany grupom: " + ", ".join(gotowe) + ".")
            if bledy:
                messages.warning(request, "Wydano, ale bez repozytorium startowego (utworzysz je na stronie "
                                          "zestawu w grupie): " + "; ".join(bledy))
            return redirect("panel:zestaw", pk=wzor.zestaw_id)
    return render(request, "kurs/panel/przydzial_kopiuj.html", {
        "przydzial": wzor, "kurs": kurs, "zestaw_formularzy": zestaw_formularzy,
        "wiersze": list(zip(kandydaci, zestaw_formularzy.forms)),
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
@require_POST
def przydzial_wyslij_oceny(request, pk):
    """Zbiorcze wyslanie ocen automatycznych zaznaczonym studentom (zawsze z decyzji prowadzacego)."""
    przydzial = get_object_or_404(Przydzial, pk=pk)
    wybrane = [int(x) for x in request.POST.getlist("zgl") if x.isdigit()]
    if not wybrane:
        messages.warning(request, "Nie zaznaczono żadnego studenta.")
        return redirect("panel:przydzial", pk=pk)
    wyslane, pominiete = 0, []
    for zgl in przydzial.zgloszenia.filter(pk__in=wybrane).select_related("student"):
        ewaluacja = zgl.ewaluacje.order_by("-utworzono").first()
        powod = ocena.wyslij_ocene(ewaluacja) if ewaluacja else "brak oceny automatycznej"
        if powod:
            pominiete.append(f"{zgl.student}: {powod}")
        else:
            wyslane += 1
    if wyslane:
        messages.success(request, f"Wysłano oceny: {wyslane}.")
    if pominiete:
        messages.warning(request, f"Pominięto {len(pominiete)}: " + "; ".join(pominiete[:10]))
    return redirect("panel:przydzial", pk=pk)


@prowadzacy
def przydzial_poprawka(request, pk):
    """Przeniesienie zmian z repozytorium zrodlowego do repozytoriow studentow grupy."""
    przydzial = get_object_or_404(Przydzial.objects.select_related("zestaw__kurs__rok", "grupa"), pk=pk)
    historia = list(przydzial.poprawki.all())
    trwa = any(p.w_toku for p in historia)
    if _zamrozony(request, przydzial.zestaw.kurs.rok):
        return redirect("panel:przydzial", pk=pk)
    if przydzial.w_poligonie:
        messages.error(request, "Poligon: poprawka ze źródła wymaga prawdziwego repozytorium na GitHubie.")
        return redirect("panel:przydzial", pk=pk)
    if not przydzial.repo_startowe or not przydzial.sha_startowy:
        messages.error(request, "Ta grupa nie ma jeszcze repozytorium startowego.")
        return redirect("panel:przydzial", pk=pk)
    try:
        pliki = poprawki.podglad(przydzial)
    except uslugi.BLEDY as blad:
        messages.error(request, f"Nie udało się porównać repozytorium źródłowego: {blad}")
        return redirect("panel:przydzial", pk=pk)
    if request.method == "POST":
        if trwa:
            messages.warning(request, "Poprzednia poprawka jest jeszcze wprowadzana. Poczekaj na jej zakończenie.")
        elif not pliki:
            messages.info(request, "Repozytorium źródłowe nie różni się od stanu startowego grupy.")
        else:
            poprawka = poprawki.zlec(przydzial, request.POST.get("opis", ""), request.POST.get("autoscalanie") == "1", pliki)
            poprawki.uruchom_w_tle()
            return redirect("panel:poprawka", pk=poprawka.pk)
        return redirect("panel:przydzial_poprawka", pk=pk)
    return render(request, "kurs/panel/poprawka_nowa.html", {
        "przydzial": przydzial, "pliki": pliki, "historia": historia, "trwa": trwa,
        "roznice": poprawki.roznice(przydzial, pliki) if pliki else [],
        "pominietych": max(len(pliki) - poprawki.MAKS_PLIKOW_PODGLADU, 0),
        "studentow": przydzial.zgloszenia.filter(status=Zgloszenie.Status.GOTOWE).count(),
    })


@prowadzacy
def zestaw_poprawka(request, pk):
    """Poprawka ze zrodla dla wszystkich grup zestawu: jeden podglad, jedno zatwierdzenie, wprowadzanie grupa po grupie."""
    zestaw = get_object_or_404(Zestaw.objects.select_related("kurs__rok__uczelnia"), pk=pk)
    if _zamrozony(request, zestaw.kurs.rok):
        return redirect("panel:zestaw", pk=pk)
    podglad = poprawki.podglad_zestawu(zestaw)
    if request.method == "POST":
        wybrane = {int(x) for x in request.POST.getlist("grupa") if x.isdigit()}
        opis, autoscalanie = request.POST.get("opis", ""), request.POST.get("autoscalanie") == "1"
        zlecone = []
        for pakiet in podglad["pakiety"]:
            for p in pakiet["przydzialy"]:
                if p.pk in wybrane and not p.trwa:
                    poprawki.zlec(p, opis, autoscalanie, pakiet["pliki"])
                    zlecone.append(p.grupa.nazwa)
        if zlecone:
            poprawki.uruchom_w_tle()
            messages.success(request, f"Korekta zlecona dla: {', '.join(zlecone)}. Robot wprowadza ją grupa po grupie.")
        else:
            messages.warning(request, "Nie zaznaczono żadnej grupy ze zmianami do wprowadzenia.")
        return redirect("panel:zestaw_poprawka", pk=pk)
    historia = list(Poprawka.objects.filter(przydzial__zestaw=zestaw).select_related("przydzial__grupa")[:40])
    return render(request, "kurs/panel/zestaw_poprawka.html", {
        "zestaw": zestaw, **podglad, "historia": historia, "w_toku": any(h.w_toku for h in historia),
    })


@prowadzacy
def poprawka(request, pk):
    poprawka = get_object_or_404(Poprawka.objects.select_related("przydzial__zestaw", "przydzial__grupa"), pk=pk)
    if request.method == "POST":
        zmienione = poprawki.sprawdz_scalenia(poprawka)
        messages.success(request, f"Sprawdzono scalenia. Nowo scalonych: {zmienione}.")
        return redirect("panel:poprawka", pk=pk)
    wpisy = list(poprawka.zgloszenia.select_related("zgloszenie__student"))
    wpisy.sort(key=lambda w: w.zgloszenie.student.klucz_sortowania)
    liczniki = {}
    for w in wpisy:
        liczniki[w.get_stan_display()] = liczniki.get(w.get_stan_display(), 0) + 1
    return render(request, "kurs/panel/poprawka.html", {
        "poprawka": poprawka, "przydzial": poprawka.przydzial, "wpisy": wpisy, "liczniki": liczniki,
    })


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
    nazwa_pliku = przydzial.przedrostek_repo.strip("-_") or "repozytoria"
    odpowiedz["Content-Disposition"] = f'attachment; filename="{nazwa_pliku}.txt"'
    return odpowiedz


# --- Zgloszenie (karta pracy studenta) ---

def _zgloszenie(pk):
    return get_object_or_404(
        Zgloszenie.objects.select_related("student", "przydzial__zestaw__kurs__rok__uczelnia", "przydzial__grupa"), pk=pk,
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
    mnoznik = zgl.mnoznik if zgl.mnoznik is not None else zgl.mnoznik_z_terminow
    formularz_oceny = FormularzOceny(
        punkty_max=zgl.przydzial.zestaw.punkty_max,
        initial={"punkty": zgl.punkty, "mnoznik": mnoznik, "komentarz": zgl.komentarz_koncowy},
    )
    wypchniecia = list(Wypchniecie.objects.filter(repo_id=zgl.repo_id).order_by("-czas")) if zgl.repo_id else []
    for w in wypchniecia:
        w.pominiete = w.sha_po != zgl.sha_poczatkowy and not uslugi.od_studenta(w, zgl.student)
    ewaluacja = zgl.ewaluacje.first()
    modele_tabeli, wiersze = ocena.tabela_ocen(ewaluacja) if ewaluacja and ewaluacja.status == Ewaluacja.Status.GOTOWA else ([], [])
    return render(request, "kurs/panel/zgloszenie.html", {
        "ewaluacja": ewaluacja,
        "modele_tabeli": [(m, ocena.etykieta_modelu(m)) for m in modele_tabeli],
        "wiersze": wiersze,
        "liczba_ewaluacji": zgl.ewaluacje.count(),
        "mnoznik_domyslny": mnoznik,
        "mnoznik_z_terminow": zgl.mnoznik_z_terminow,
        "mnoznik_spoznienia": Decimal(settings.SPRAWDZARKA_MNOZNIK_SPOZNIENIA),
        "opinie": ewaluacja.opinie.all() if ewaluacja else [],
        "testy": _podsumowanie_testow(ewaluacja),
        "ocena_nieaktualna": bool(
            ewaluacja and ewaluacja.status == Ewaluacja.Status.GOTOWA and not ewaluacja.zatwierdzona
            and (ewaluacja.sha != zgl.sha_oceniany or ewaluacja.konfiguracja != ocena.odcisk_konfiguracji(zgl.przydzial.zestaw))
        ),
        "z": zgl,
        "formularz_oceny": formularz_oceny,
        "formularz_wyjatku": FormularzWyjatku(instance=zgl),
        "termin_miekki_zmieniony": zgl.termin_miekki != zgl.przydzial.termin_miekki,
        "termin_twardy_zmieniony": zgl.termin_twardy != zgl.przydzial.termin_twardy,
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
    dane = request.POST
    if request.POST.get("akcja") == "przywroc":
        dane = {"wyjatek_termin_miekki": "", "wyjatek_termin_twardy": ""}
    formularz = FormularzWyjatku(dane, instance=zgl)
    if formularz.is_valid():
        formularz.save()
        zgl.do_przeliczenia = True
        zgl.save(update_fields=["do_przeliczenia"])
        try:
            uslugi.przelicz(zgl)
        except uslugi.BLEDY as blad:
            messages.warning(request, f"Zapisano, ale przeliczenie się nie powiodło: {blad}")
        messages.success(request, "Zapisano terminy studenta." if zgl.ma_wyjatek else "Student ma terminy grupy.")
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
    try:
        komunikaty = uslugi.wyzeruj(zgl, usun_repo=request.POST.get("usun_repo") == "1")
    except uslugi.BLEDY as blad:
        messages.error(request, f"GitHub nie wykonał zerowania ({blad}). Zgłoszenie zostało bez zmian, "
                                "spróbuj ponownie za chwilę.")
    else:
        for komunikat in komunikaty:
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

def _zakres_studentow(request):
    """Wybrany poziom struktury (najwezszy z podanych): grupa, przedmiot, rok albo uczelnia."""
    for nazwa, model, sciezka in [("grupa", Grupa, "przydzial__grupa"), ("kurs", Kurs, "przydzial__grupa__kurs"),
                                  ("rok", Rok, "przydzial__grupa__kurs__rok"),
                                  ("uczelnia", Uczelnia, "przydzial__grupa__kurs__rok__uczelnia")]:
        pk = request.GET.get(nazwa, "")
        if pk.isdigit():
            obiekt = model.objects.filter(pk=pk).first()
            if obiekt is not None:
                return nazwa, obiekt, sciezka
    return None, None, None


def _okruszki_zakresu(nazwa, obiekt):
    """Lista (etykieta, parametr, obiekt) od uczelni do wybranego poziomu."""
    if nazwa is None:
        return []
    grupa = obiekt if nazwa == "grupa" else None
    kurs = grupa.kurs if grupa else (obiekt if nazwa == "kurs" else None)
    rok = kurs.rok if kurs else (obiekt if nazwa == "rok" else None)
    uczelnia = rok.uczelnia if rok else obiekt
    lista = [(uczelnia.skrot.upper(), "uczelnia", uczelnia)]
    lista += [(rok.nazwa, "rok", rok)] if rok else []
    lista += [(kurs.nazwa, "kurs", kurs)] if kurs else []
    lista += [(grupa.nazwa, "grupa", grupa)] if grupa else []
    return lista


@prowadzacy
def studenci(request):
    """Studenci wedlug struktury: uczelnia, rok, przedmiot, grupa; w grupie tabela prac w kolejnych zestawach.

    Na kazdym poziomie "pokaz" zaweza strone do tego poziomu (parametry uczelnia, rok, kurs, grupa).
    """
    zakres, obiekt, sciezka = _zakres_studentow(request)
    filtr = request.GET.get("filtr", "")
    szukaj = request.GET.get("q", "").strip()

    prace = (Zgloszenie.objects.exclude(status=Zgloszenie.Status.WYZEROWANE)
             .select_related("student", "przydzial__zestaw", "przydzial__grupa__kurs__rok__uczelnia"))
    if zakres:
        prace = prace.filter(**{sciezka: obiekt})
    # Ponizej wybranego poziomu bez ukrytych i nieaktywnych; sam wybrany poziom pokazuje sie zawsze
    if zakres in (None, "uczelnia"):
        prace = prace.exclude(przydzial__grupa__kurs__rok__zarchiwizowany=True).exclude(
            przydzial__grupa__kurs__rok__ukryty=True)
    if zakres in (None, "uczelnia", "rok"):
        prace = prace.exclude(przydzial__grupa__kurs__ukryty=True)
    if zakres in (None, "uczelnia", "rok", "kurs"):
        prace = prace.exclude(przydzial__grupa__aktywna=False)
    osoby = Student.objects.all()
    if filtr == "bez":
        osoby = osoby.filter(imie_nazwisko="")
    if szukaj:
        osoby = osoby.filter(Q(login__icontains=szukaj) | Q(imie_nazwisko__icontains=szukaj))
    prace = prace.filter(student__in=osoby)

    grupy = {}
    for z in prace:
        g = z.przydzial.grupa
        wpis = grupy.setdefault(g.pk, {"grupa": g, "studenci": {}})
        wpis["studenci"].setdefault(z.student_id, {"student": z.student, "prace": {}})["prace"][z.przydzial_id] = z
    kolumny = {}
    for p in Przydzial.objects.filter(grupa_id__in=grupy).select_related("zestaw").order_by("termin_miekki"):
        kolumny.setdefault(p.grupa_id, []).append(p)

    uczelnie = {}
    for wpis in grupy.values():
        g = wpis["grupa"]
        wpis["kolumny"] = kolumny.get(g.pk, [])
        wpis["wiersze"] = sorted(
            ({"student": w["student"], "komorki": [w["prace"].get(p.pk) for p in wpis["kolumny"]]}
             for w in wpis["studenci"].values()),
            key=lambda w: w["student"].klucz_sortowania)
        rok = g.kurs.rok
        u = uczelnie.setdefault(rok.uczelnia_id, {"uczelnia": rok.uczelnia, "lata": {}})
        r = u["lata"].setdefault(rok.pk, {"rok": rok, "kursy": {}})
        r["kursy"].setdefault(g.kurs_id, {"kurs": g.kurs, "grupy": []})["grupy"].append(wpis)
    lista = sorted(uczelnie.values(), key=lambda u: (u["uczelnia"].kolejnosc, u["uczelnia"].nazwa))
    for u in lista:
        u["lista_lat"] = sorted(u["lata"].values(), key=lambda r: r["rok"].nazwa, reverse=True)
        u["lista_lat"].sort(key=lambda r: r["rok"].poligon)  # poligon na koncu
        for r in u["lista_lat"]:
            r["lista_kursow"] = sorted(r["kursy"].values(), key=lambda k: k["kurs"].nazwa)
            for k in r["lista_kursow"]:
                k["grupy"].sort(key=lambda w: w["grupa"].nazwa)
                k["liczba"] = len({w["student"].pk for g in k["grupy"] for w in g["wiersze"]})
            r["liczba"] = len({w["student"].pk for k in r["lista_kursow"] for g in k["grupy"] for w in g["wiersze"]})
        u["liczba"] = len({w["student"].pk for r in u["lista_lat"] for k in r["lista_kursow"]
                           for g in k["grupy"] for w in g["wiersze"]})

    # Konta bez zadnej pracy (np. tylko wyzerowane zgloszenia): tez do ustalenia nazwiska
    bez_grupy = []
    if zakres is None:
        aktywne = Zgloszenie.objects.exclude(status=Zgloszenie.Status.WYZEROWANE).values("student_id")
        bez_grupy = sorted(osoby.exclude(pk__in=aktywne), key=lambda s: s.klucz_sortowania)
    reszta = urlencode({k: v for k, v in (("q", szukaj), ("filtr", filtr)) if v})
    return render(request, "kurs/panel/studenci.html", {
        "uczelnie": lista, "bez_grupy": bez_grupy, "zakres": _okruszki_zakresu(zakres, obiekt),
        "zakres_pole": zakres, "zakres_pk": obiekt.pk if obiekt else "",
        "reszta": reszta, "dodatki": f"&{reszta}" if reszta else "",
        "lata_do_wyboru": Rok.objects.select_related("uczelnia"),
        "wybrany_rok": obiekt if zakres == "rok" else None, "filtr": filtr, "q": szukaj,
    })


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
    # Pusty wiersz na nowe zadanie z kolejnym numerem (bez zmian jest pomijany przy zapisie)
    nastepny = (zestaw.zadania.aggregate(m=Max("kolejnosc"))["m"] or 0) + 1
    formularze = FormularzeZadan(request.POST or None, instance=zestaw, initial=[{"kolejnosc": nastepny}])
    if request.method == "POST" and formularze.is_valid():
        formularze.save()
        messages.success(request, "Zapisano zadania i kryteria.")
        return redirect("panel:zestaw", pk=zestaw.pk)
    return render(request, "kurs/panel/zadania.html", {"zestaw": zestaw, "formularze": formularze})


@prowadzacy
@require_POST
def zestaw_z_katalogow(request, pk):
    zestaw = get_object_or_404(Zestaw, pk=pk)
    try:
        liczba_zadan = ocena.utworz_zadania_z_katalogow(zestaw)
        messages.success(request, f"Utworzono {_zadan(liczba_zadan)} na podstawie katalogów repozytorium. "
                                  "Sprawdź punkty i sposób oceny, popraw i zapisz.")
        return redirect("panel:zestaw_zadania", pk=pk)
    except ocena.BladOceny as blad:
        messages.error(request, str(blad))
    return redirect("panel:zestaw", pk=pk)


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
        messages.success(request, f"Dodano do kolejki oceny: {licznik}. Wyniki pojawią się w kolumnie „Ocena”.")
    else:
        messages.info(request, "Nie ma nowych prac do oceny (oceniane są prace z oddanym kodem i otwartym pull requestem).")
    if request.POST.get("nastepny") == "oceny":
        return redirect("panel:oceny")
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
    return _do_ustawien("#modele")


def _przydzialy_do_oceny():
    """Wszystkie zestawy wydane grupom (z lat i przedmiotow widocznych na pulpicie), takze bez
    zaakceptowanych prac. Dla kazdego: ocena automatyczna (ocenione / z oddanym kodem, w toku,
    do oceny) i wystawione oceny ostateczne."""
    przydzialy = list(_dolacz_liczniki(
        Przydzial.objects.filter(zestaw__kurs__ukryty=False, zestaw__kurs__rok__ukryty=False,
                                 zestaw__kurs__rok__zarchiwizowany=False)
        .select_related("zestaw__kurs__rok__uczelnia", "grupa")))
    mapa = {p.pk: p for p in przydzialy}
    odciski = {}
    for p in przydzialy:
        p.z_kodem = p.ocenione_automatycznie = p.w_toku = p.do_oceny_automatycznie = 0
        if p.zestaw_id not in odciski:
            odciski[p.zestaw_id] = ocena.odcisk_konfiguracji(p.zestaw)
    ewaluacje = Ewaluacja.objects.only("zgloszenie_id", "sha", "konfiguracja", "status", "zatwierdzona", "utworzono")
    for z in (Zgloszenie.objects.filter(przydzial_id__in=mapa, status=Zgloszenie.Status.GOTOWE).exclude(sha_oceniany="")
              .only("przydzial_id", "sha_oceniany", "pr_zamkniety").prefetch_related(Prefetch("ewaluacje", queryset=ewaluacje))):
        p = mapa[z.przydzial_id]
        odcisk = odciski[p.zestaw_id]
        biezace = [e for e in z.ewaluacje.all() if e.sha == z.sha_oceniany]
        p.z_kodem += 1
        # Ocena aktualna: gotowa dla tego stanu kodu i tych ustawien (zatwierdzona zostaje aktualna);
        # praca z wyslana ocena nie wymaga juz oceny automatycznej
        if z.pr_zamkniety or any(e.status == Ewaluacja.Status.GOTOWA and (e.konfiguracja == odcisk or e.zatwierdzona)
                                 for e in biezace):
            p.ocenione_automatycznie += 1
        elif any(e.w_toku and e.konfiguracja == odcisk for e in biezace):
            p.w_toku += 1
        else:
            p.do_oceny_automatycznie += 1
    przydzialy.sort(key=lambda p: (p.dioda_ocen != "czerwony" and p.dioda_ocen != "fiolet", p.zestaw.kurs.rok.poligon,
                                   p.termin_twardy))
    return przydzialy


@prowadzacy
def oceny(request):
    formularz = FormularzPrecedensu(request.POST or None)
    if request.method == "POST" and formularz.is_valid():
        formularz.save()
        messages.success(request, "Dodano regułę.")
        return redirect(reverse("panel:oceny") + "#kalibracja")
    zestaw_id = request.GET.get("zestaw")
    zestaw = Zestaw.objects.filter(pk=zestaw_id).first() if zestaw_id else None
    return render(request, "kurs/panel/oceny.html", {
        "przydzialy": _przydzialy_do_oceny(),
        "precedensy": Precedens.objects.select_related("zestaw"),
        "formularz": formularz,
        "statystyki": ocena.statystyki_kalibracji(zestaw),
        "zestawy": Zestaw.objects.all(),
        "wybrany": zestaw,
    })


def kalibracja(request):
    """Dawny adres kalibracji: teraz sekcja strony Oceny."""
    return redirect(reverse("panel:oceny") + "#kalibracja")


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
    return redirect(reverse("panel:oceny") + "#kalibracja")
