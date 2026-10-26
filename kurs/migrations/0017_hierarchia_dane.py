"""Nowa hierarchia, krok 2: przeniesienie danych.

Dawne lata kursow tej samej uczelni o tym samym skrocie (np. 2627) staja sie jednym rokiem akademickim,
a kazdy kurs w danym roku osobnym przedmiotem tego roku. Grupy i zestawy trafiaja do swojego przedmiotu.
"""
import re
from collections import defaultdict
from datetime import date

from django.db import migrations


def _skrot_roku(nazwa):
    lata = re.findall(r"\d{4}", nazwa or "")
    if len(lata) >= 2:
        return lata[0][-2:] + lata[1][-2:]
    return re.sub(r"[^a-z0-9-]+", "-", (nazwa or "").lower()).strip("-")[:20]


def _biezacy_rok_akademicki():
    dzis = date.today()
    poczatek = dzis.year if dzis.month >= 10 else dzis.year - 1
    return f"{poczatek}/{poczatek + 1}"


def przenies(apps, schema_editor):
    Kurs = apps.get_model("kurs", "Kurs")
    Rok = apps.get_model("kurs", "Rok")
    Grupa = apps.get_model("kurs", "Grupa")
    Zestaw = apps.get_model("kurs", "Zestaw")

    stare_lata = list(Rok.objects.select_related("kurs").order_by("pk"))
    ukryte = {r.pk for r in stare_lata if r.ukryty}
    # 1. Jeden rok akademicki na uczelnie i skrot
    wedlug_klucza = defaultdict(list)
    for r in stare_lata:
        wedlug_klucza[(r.kurs.uczelnia_id, r.skrot)].append(r)
    glowny = {}
    for (uczelnia_id, _), lata in wedlug_klucza.items():
        g = lata[0]
        g.uczelnia_id = uczelnia_id
        g.zarchiwizowany = all(r.zarchiwizowany for r in lata)
        g.zarchiwizowano = max((r.zarchiwizowano for r in lata if r.zarchiwizowano), default=None) \
            if g.zarchiwizowany else None
        # Ukrycie dotyczylo kursu w danym roku, wiec przechodzi na przedmiot
        g.ukryty = False
        g.poligon = any(r.poligon for r in lata)
        g.save()
        for r in lata:
            glowny[r.pk] = g

    # 2. Przedmiot na kazdy dawny rok kursu
    uzyte = set()
    for r in stare_lata:
        k, g = r.kurs, glowny[r.pk]
        ukryty = r.pk in ukryte
        if k.pk in uzyte:
            przedmiot = Kurs.objects.create(uczelnia_id=k.uczelnia_id, nazwa=k.nazwa, skrot=k.skrot, kod=k.kod,
                                            opis=k.opis, poligon=k.poligon, rok=g, ukryty=ukryty)
        else:
            k.rok, k.ukryty = g, ukryty
            k.save()
            przedmiot = k
            uzyte.add(k.pk)
        Grupa.objects.filter(rok=r).update(kurs=przedmiot, rok=g)
        Zestaw.objects.filter(rok=r).update(kurs=przedmiot, rok=g)

    # 3. Dawne kursy bez zadnego roku trafiaja do najnowszego roku swojej uczelni
    for k in Kurs.objects.filter(rok__isnull=True):
        if k.poligon:
            k.delete()
            continue
        rok = Rok.objects.filter(uczelnia_id=k.uczelnia_id, poligon=False).order_by("-nazwa").first()
        if rok is None:
            nazwa = _biezacy_rok_akademicki()
            rok = Rok.objects.create(uczelnia_id=k.uczelnia_id, nazwa=nazwa, skrot=_skrot_roku(nazwa))
        k.rok = rok
        k.save()

    # 4. Scalone lata nie sa juz potrzebne
    for r in stare_lata:
        if glowny[r.pk].pk != r.pk:
            r.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("kurs", "0016_hierarchia_lat"),
    ]

    operations = [
        migrations.RunPython(przenies, migrations.RunPython.noop),
    ]
