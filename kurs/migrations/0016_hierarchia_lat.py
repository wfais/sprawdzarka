"""Nowa hierarchia: uczelnia > rok akademicki > przedmiot > grupa. Krok 1: nowe powiazania (puste)."""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kurs", "0015_dostep_do_panelu"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="kurs", name="unikalny_kurs_uczelni"),
        migrations.RemoveConstraint(model_name="rok", name="unikalny_rok_kursu"),
        migrations.RemoveConstraint(model_name="grupa", name="unikalna_grupa_roku"),
        migrations.RemoveConstraint(model_name="zestaw", name="unikalny_zestaw_roku"),
        migrations.AddField(
            model_name="rok", name="uczelnia",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="lata",
                                    to="kurs.uczelnia", verbose_name="uczelnia"),
        ),
        migrations.AddField(
            model_name="kurs", name="rok",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="kursy",
                                    to="kurs.rok", verbose_name="rok akademicki"),
        ),
        migrations.AddField(
            model_name="kurs", name="ukryty",
            field=models.BooleanField(default=False, verbose_name="ukryty"),
        ),
        migrations.AddField(
            model_name="grupa", name="kurs",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="grupy",
                                    to="kurs.kurs", verbose_name="przedmiot"),
        ),
        migrations.AddField(
            model_name="zestaw", name="kurs",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="zestawy",
                                    to="kurs.kurs", verbose_name="przedmiot"),
        ),
    ]
