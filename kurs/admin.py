"""Panel administracyjny Django: awaryjny dostep do wszystkich danych."""
from django.contrib import admin

from .models import (
    Grupa, KontoProwadzacego, Kurs, NieudaneLogowanie, Porownanie, Przydzial, Rok, Student, Uczelnia, Wypchniecie, Zestaw,
    Zgloszenie,
)


@admin.register(Uczelnia)
class UczelniaAdmin(admin.ModelAdmin):
    list_display = ["nazwa", "skrot", "kolejnosc"]


@admin.register(Rok)
class RokAdmin(admin.ModelAdmin):
    list_display = ["nazwa", "skrot", "uczelnia", "ukryty", "zarchiwizowany"]
    list_filter = ["uczelnia", "ukryty", "zarchiwizowany"]


@admin.register(Kurs)
class KursAdmin(admin.ModelAdmin):
    list_display = ["nazwa", "skrot", "rok", "kod", "ukryty"]
    list_filter = ["rok", "ukryty"]


@admin.register(Grupa)
class GrupaAdmin(admin.ModelAdmin):
    list_display = ["nazwa", "skrot", "kurs", "aktywna"]
    list_filter = ["kurs"]


@admin.register(Zestaw)
class ZestawAdmin(admin.ModelAdmin):
    list_display = ["nazwa", "skrot", "rodzaj", "kurs", "repo_zrodlowe", "punkty_max"]
    list_filter = ["kurs", "rodzaj"]


@admin.register(Przydzial)
class PrzydzialAdmin(admin.ModelAdmin):
    list_display = ["zestaw", "grupa", "termin_miekki", "termin_twardy", "otwarty", "repo_startowe"]
    list_filter = ["grupa", "otwarty"]
    readonly_fields = ["token"]


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ["login", "imie_nazwisko", "zrodlo_nazwiska", "kandydat_readme", "github_id"]
    search_fields = ["login", "imie_nazwisko"]


@admin.register(Zgloszenie)
class ZgloszenieAdmin(admin.ModelAdmin):
    list_display = ["student", "przydzial", "status", "stan_kolor", "repo_nazwa", "punkty", "mnoznik"]
    list_filter = ["status", "stan_kolor", "testy_zmienione", "przydzial__grupa"]
    search_fields = ["student__login", "student__imie_nazwisko", "repo_nazwa"]


@admin.register(Wypchniecie)
class WypchniecieAdmin(admin.ModelAdmin):
    list_display = ["repo_nazwa", "ref", "sha_po", "czas", "autor", "zrodlo"]
    search_fields = ["repo_nazwa", "sha_po"]


@admin.register(Porownanie)
class PorownanieAdmin(admin.ModelAdmin):
    list_display = ["repo_id", "sha_od", "sha_do"]


@admin.register(KontoProwadzacego)
class KontoProwadzacegoAdmin(admin.ModelAdmin):
    list_display = ["login", "github_id", "uzytkownik", "ostatnie_logowanie"]


@admin.register(NieudaneLogowanie)
class NieudaneLogowanieAdmin(admin.ModelAdmin):
    list_display = ["czas", "ip", "nazwa"]
