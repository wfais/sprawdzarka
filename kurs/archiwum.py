"""Archiwum roku akademickiego: plik JSON z kompletem danych (przedmioty, grupy, zestawy z PDF, zgloszenia, oceny)
i jego wczytanie."""
import base64
import json
import os

from django.core.files.base import ContentFile
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.utils import timezone

from .models import (
    Ewaluacja, Grupa, Kurs, OcenaZadania, OpiniaModelu, Poprawka, PoprawkaZgloszenia, Porownanie, Precedens, Przydzial,
    Rok, Student, Uczelnia, Wypchniecie, Zadanie, Zestaw, Zgloszenie,
)

FORMAT = "sprawdzarka-archiwum-roku"
# Wersja 2: rok akademicki z przedmiotami (wersja 1 to dawny rok jednego kursu)
WERSJA = 2

# Kolejnosc ma znaczenie: obiekt pojawia sie po wszystkich, na ktore wskazuje
SKLAD = [
    ("kursy", Kurs, "rok"),
    ("grupy", Grupa, "kurs__rok"),
    ("zestawy", Zestaw, "kurs__rok"),
    ("zadania", Zadanie, "zestaw__kurs__rok"),
    ("przydzialy", Przydzial, "zestaw__kurs__rok"),
    ("zgloszenia", Zgloszenie, "przydzial__zestaw__kurs__rok"),
    ("ewaluacje", Ewaluacja, "zgloszenie__przydzial__zestaw__kurs__rok"),
    ("oceny_zadan", OcenaZadania, "ewaluacja__zgloszenie__przydzial__zestaw__kurs__rok"),
    ("opinie", OpiniaModelu, "ewaluacja__zgloszenie__przydzial__zestaw__kurs__rok"),
    ("precedensy", Precedens, "zestaw__kurs__rok"),
    ("poprawki", Poprawka, "przydzial__zestaw__kurs__rok"),
    ("poprawki_zgloszen", PoprawkaZgloszenia, "poprawka__przydzial__zestaw__kurs__rok"),
]
ZGLOSZENIA_ROKU = "przydzial__zestaw__kurs__rok"


class BladArchiwum(Exception):
    pass


def _pola(obj) -> dict:
    dane = {}
    for pole in obj._meta.concrete_fields:
        if pole.primary_key:
            continue
        wartosc = getattr(obj, pole.attname)
        if isinstance(pole, models.FileField):
            wartosc = wartosc.name if wartosc else ""
        dane[pole.attname] = wartosc
    return dane


def _wpis(obj) -> dict:
    return {"pk": obj.pk, "pola": _pola(obj)}


def _pdf(zestaw) -> dict | None:
    if not zestaw.tresc_pdf:
        return None
    try:
        with zestaw.tresc_pdf.open("rb") as plik:
            return {"nazwa": os.path.basename(zestaw.tresc_pdf.name), "dane": base64.b64encode(plik.read()).decode()}
    except OSError:
        return None


def eksportuj(rok: Rok) -> dict:
    uczelnia = rok.uczelnia
    dane = {
        "format": FORMAT, "wersja": WERSJA, "utworzono": timezone.now(),
        "uczelnia": {"skrot": uczelnia.skrot, "nazwa": uczelnia.nazwa},
        "rok": _wpis(rok),
    }
    for klucz, model, sciezka in SKLAD:
        lista = []
        for obj in model.objects.filter(**{sciezka: rok}).order_by("pk"):
            wpis = _wpis(obj)
            if model is Zestaw:
                wpis["pdf"] = _pdf(obj)
            lista.append(wpis)
        dane[klucz] = lista
    dane["studenci"] = [
        _wpis(s) for s in Student.objects.filter(**{"zgloszenia__" + ZGLOSZENIA_ROKU: rok}).distinct().order_by("pk")
    ]
    repo = Zgloszenie.objects.filter(**{ZGLOSZENIA_ROKU: rok}).exclude(repo_id=None).values_list("repo_id", flat=True)
    dane["wypchniecia"] = [_wpis(w) for w in Wypchniecie.objects.filter(repo_id__in=list(repo)).order_by("pk")]
    return dane


def do_pliku(rok: Rok) -> bytes:
    return json.dumps(eksportuj(rok), cls=DjangoJSONEncoder, ensure_ascii=False, indent=1).encode("utf-8")


def nazwa_pliku(rok: Rok) -> str:
    return f"archiwum-{rok.uczelnia.skrot}-{rok.skrot}.json"


# --- Wczytanie ---

def _obiekt(model, pola: dict, mapy: dict):
    """Nowy obiekt modelu z pol z archiwum; klucze obce przez mapy starych numerow na nowe."""
    wartosci, daty = {}, {}
    for pole in model._meta.concrete_fields:
        if pole.primary_key or pole.attname not in pola:
            continue
        wartosc = pola[pole.attname]
        if pole.is_relation:
            if wartosc is not None:
                wartosc = mapy.get(pole.related_model, {}).get(wartosc)
                if wartosc is None and not pole.null:
                    raise BladArchiwum(f"Uszkodzone archiwum: {model.__name__}.{pole.name}")
        elif isinstance(pole, models.FileField):
            wartosc = ""
        elif wartosc is not None and not isinstance(pole, models.JSONField):
            wartosc = pole.to_python(wartosc)
        if getattr(pole, "auto_now", False) or getattr(pole, "auto_now_add", False):
            daty[pole.attname] = wartosc
        wartosci[pole.attname] = wartosc
    return model(**wartosci), daty


def _zapisz(model, obj, daty: dict):
    obj.save()
    daty = {k: v for k, v in daty.items() if v is not None}
    if daty:
        model.objects.filter(pk=obj.pk).update(**daty)
    return obj


def _sprawdz(dane: dict):
    if not isinstance(dane, dict) or dane.get("format") != FORMAT:
        raise BladArchiwum("To nie jest plik archiwum roku Sprawdzarki.")
    if dane.get("wersja", 0) > WERSJA:
        raise BladArchiwum("Archiwum z nowszej wersji Sprawdzarki. Zaktualizuj aplikację.")
    if dane.get("wersja", 0) < WERSJA:
        raise BladArchiwum("Archiwum ze starszej wersji Sprawdzarki (sprzed podziału lat na przedmioty) "
                           "nie jest obsługiwane.")
    for klucz in ["uczelnia", "rok"]:
        if not isinstance(dane.get(klucz), dict):
            raise BladArchiwum(f"Uszkodzone archiwum: brak części {klucz}.")
    if not isinstance(dane["rok"].get("pola"), dict):
        raise BladArchiwum("Uszkodzone archiwum: brak danych roku.")


def wczytaj(dane: dict) -> Rok:
    """Odtwarza rok z archiwum. Uczelnie i studentow laczy z istniejacymi (po skrocie, numerze konta)."""
    _sprawdz(dane)
    with transaction.atomic():
        uczelnia, _ = Uczelnia.objects.get_or_create(
            skrot=dane["uczelnia"]["skrot"], defaults={"nazwa": dane["uczelnia"]["nazwa"]})
        pola_roku = dane["rok"]["pola"]
        if Rok.objects.filter(uczelnia=uczelnia, skrot=pola_roku.get("skrot")).exists():
            raise BladArchiwum(f"Rok {pola_roku.get('nazwa')} na uczelni {uczelnia.skrot.upper()} już jest w bazie. "
                               "Archiwum można wczytać tylko wtedy, gdy tego roku nie ma.")
        zajete = [p["pola"]["przedrostek_repo"] for p in dane.get("przydzialy", [])
                  if Przydzial.objects.filter(przedrostek_repo=p["pola"]["przedrostek_repo"]).exists()]
        if zajete:
            raise BladArchiwum("W bazie są już zestawy z tymi przedrostkami repozytoriów: " + ", ".join(zajete[:5]))
        mapy = {Uczelnia: {pola_roku.get("uczelnia_id"): uczelnia.pk}}
        rok, daty = _obiekt(Rok, pola_roku, mapy)
        _zapisz(Rok, rok, daty)
        mapy[Rok] = {dane["rok"].get("pk"): rok.pk}

        mapy[Student] = {}
        for wpis in dane.get("studenci", []):
            istniejacy = Student.objects.filter(github_id=wpis["pola"]["github_id"]).first()
            if istniejacy is None:
                obj, daty = _obiekt(Student, wpis["pola"], mapy)
                istniejacy = _zapisz(Student, obj, daty)
            mapy[Student][wpis["pk"]] = istniejacy.pk

        for klucz, model, _ in SKLAD:
            mapy[model] = {}
            for wpis in dane.get(klucz, []):
                obj, daty = _obiekt(model, wpis["pola"], mapy)
                if model is Zestaw and wpis.get("pdf"):
                    pdf = wpis["pdf"]
                    obj.tresc_pdf.save(pdf["nazwa"], ContentFile(base64.b64decode(pdf["dane"])), save=False)
                mapy[model][wpis["pk"]] = _zapisz(model, obj, daty).pk

        for wpis in dane.get("wypchniecia", []):
            pola = wpis["pola"]
            klucz = {k: pola[k] for k in ["repo_id", "ref", "sha_przed", "sha_po"]}
            if not Wypchniecie.objects.filter(**klucz).exists():
                obj, daty = _obiekt(Wypchniecie, pola, mapy)
                _zapisz(Wypchniecie, obj, daty)
    return rok


def wczytaj_plik(tresc: bytes) -> Rok:
    try:
        dane = json.loads(tresc.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BladArchiwum("Plik nie jest poprawnym plikiem JSON.") from None
    return wczytaj(dane)


# --- Usuniecie z bazy ---

def usun(rok: Rok) -> None:
    """Usuwa zarchiwizowany rok (albo rok poligonu) z bazy. Studenci zostaja, bo moga byc w innych latach."""
    if not (rok.zarchiwizowany or rok.poligon) and Zgloszenie.objects.filter(**{ZGLOSZENIA_ROKU: rok}).exists():
        raise BladArchiwum("Rok ze zgłoszeniami studentów usuniesz z bazy dopiero po archiwizacji.")
    with transaction.atomic():
        for kurs in rok.kursy.all():
            usun_przedmiot(kurs)
        rok.delete()


def usun_przedmiot(kurs: Kurs) -> dict:
    """Usuwa przedmiot z grupami, zestawami (z PDF) i zgloszeniami. Zwraca liczniki."""
    with transaction.atomic():
        zgloszenia = Zgloszenie.objects.filter(przydzial__zestaw__kurs=kurs)
        repo = list(zgloszenia.exclude(repo_id=None).values_list("repo_id", flat=True))
        liczniki = {"grup": kurs.grupy.count(), "zestawow": kurs.zestawy.count(), "zgloszen": zgloszenia.count()}
        zgloszenia.delete()
        Przydzial.objects.filter(zestaw__kurs=kurs).delete()
        for zestaw in kurs.zestawy.all():
            if zestaw.tresc_pdf:
                zestaw.tresc_pdf.delete(save=False)
            zestaw.delete()
        kurs.grupy.all().delete()
        usun_historie_repozytoriow(repo)
        kurs.delete()
    return liczniki


def usun_historie_repozytoriow(repo: list) -> None:
    """Wypchniecia i porownania repozytoriow, ktore nie naleza juz do zadnego zgloszenia."""
    uzywane = set(Zgloszenie.objects.filter(repo_id__in=repo).values_list("repo_id", flat=True))
    wolne = [r for r in repo if r not in uzywane]
    Wypchniecie.objects.filter(repo_id__in=wolne).delete()
    Porownanie.objects.filter(repo_id__in=wolne).delete()
