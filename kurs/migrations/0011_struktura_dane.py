"""Uczelnie UJ i ANS; istniejace grupy i zestawy trafiaja do kursu Jezyk Python 2026/2027 na UJ."""
from django.db import migrations


def przenies(apps, schema_editor):
    Uczelnia = apps.get_model("kurs", "Uczelnia")
    Kurs = apps.get_model("kurs", "Kurs")
    Rok = apps.get_model("kurs", "Rok")
    Grupa = apps.get_model("kurs", "Grupa")
    Zestaw = apps.get_model("kurs", "Zestaw")

    uj, _ = Uczelnia.objects.get_or_create(skrot="uj", defaults={"nazwa": "Uniwersytet Jagielloński", "kolejnosc": 1})
    Uczelnia.objects.get_or_create(skrot="ans", defaults={"nazwa": "Akademia Nauk Stosowanych w Nowym Sączu",
                                                          "kolejnosc": 2})
    if not (Grupa.objects.filter(rok__isnull=True).exists() or Zestaw.objects.filter(rok__isnull=True).exists()):
        return
    kurs, _ = Kurs.objects.get_or_create(uczelnia=uj, skrot="py", defaults={"nazwa": "Język Python"})
    rok, _ = Rok.objects.get_or_create(kurs=kurs, skrot="2627", defaults={"nazwa": "2026/2027"})
    Grupa.objects.filter(rok__isnull=True).update(rok=rok)
    Zestaw.objects.filter(rok__isnull=True).update(rok=rok)


class Migration(migrations.Migration):

    dependencies = [
        ("kurs", "0010_struktura_uczelni"),
    ]

    operations = [
        migrations.RunPython(przenies, migrations.RunPython.noop),
    ]
