"""Formularze panelu prowadzacego i strony akceptacji."""
import re
from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import Grupa, Kurs, Precedens, Przydzial, Rok, Student, Uczelnia, Zadanie, Zestaw, Zgloszenie, skrot_roku


class PoleDatyICzasu(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%dT%H:%M", **kwargs)


def oczysc_repo(wartosc: str) -> str:
    """Adres albo nazwa repozytorium w postaci organizacja/nazwa."""
    wartosc = (wartosc or "").strip()
    wartosc = re.sub(r"^(https?://github\.com/|git@[^:]+:)", "", wartosc)
    wartosc = wartosc.removesuffix(".git").strip("/")
    if wartosc and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", wartosc):
        raise forms.ValidationError("Podaj repozytorium w postaci organizacja/nazwa, np. wfais/python-zestaw3.")
    return wartosc


# Pole publikacji zwiniete pod odnosnikiem: zwykle puste (link dziala od razu), latwo je pomylic z terminem
SZABLON_POLA_PUBLIKACJI = "kurs/panel/_pole_publikacji.html"


def publikacja_od(wartosc, poprzednia=None):
    """Data publikacji: pusta albo z przeszlosci oznacza od razu (zostaje wczesniejsza publikacja, jesli byla)."""
    teraz = timezone.now()
    if wartosc and wartosc > teraz:
        return wartosc
    return poprzednia if poprzednia and poprzednia <= teraz else teraz


class FormularzUczelni(forms.ModelForm):
    class Meta:
        model = Uczelnia
        fields = ["nazwa", "skrot", "kolejnosc"]


class FormularzRoku(forms.ModelForm):
    class Meta:
        model = Rok
        fields = ["uczelnia", "nazwa", "skrot", "ukryty"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["nazwa"].widget.attrs["placeholder"] = "2026/2027"

    def clean(self):
        dane = super().clean()
        if dane.get("nazwa") and not dane.get("skrot"):
            dane["skrot"] = skrot_roku(dane["nazwa"])
        return dane


class FormularzKursu(forms.ModelForm):
    """Przedmiot w roku akademickim. Z parametrem rok (strona roku) bez wyboru roku."""

    class Meta:
        model = Kurs
        fields = ["rok", "nazwa", "skrot", "kod", "opis", "ukryty"]

    def __init__(self, *args, rok=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.rok = rok
        if rok is not None:
            del self.fields["rok"]
            del self.fields["ukryty"]
            self.instance.rok = rok
            return
        lata = Rok.objects.select_related("uczelnia")
        if not self.instance.pk:
            lata = lata.filter(zarchiwizowany=False)
        self.fields["rok"].queryset = lata
        self.fields["rok"].label_from_instance = lambda r: r.pelna_nazwa

    def clean_skrot(self):
        skrot = self.cleaned_data["skrot"]
        rok = self.rok or self.cleaned_data.get("rok")
        if rok and Kurs.objects.filter(rok=rok, skrot=skrot).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Ten skrót jest już użyty w tym roku akademickim.")
        return skrot


class _WKursie:
    """Skrot unikalny w obrebie przedmiotu (przedmiot ustawia widok)."""

    def __init__(self, *args, kurs=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.kurs = kurs or getattr(self.instance, "kurs", None)

    def clean_skrot(self):
        skrot = self.cleaned_data["skrot"]
        inne = self._meta.model.objects.filter(kurs=self.kurs, skrot=skrot).exclude(pk=self.instance.pk)
        if self.kurs and inne.exists():
            raise forms.ValidationError("Ten skrót jest już użyty w tym przedmiocie.")
        return skrot

    def _post_clean(self):
        if self.kurs:
            self.instance.kurs = self.kurs
        super()._post_clean()


class FormularzGrupy(_WKursie, forms.ModelForm):
    class Meta:
        model = Grupa
        fields = ["nazwa", "skrot", "opis", "aktywna"]


class FormularzNowejGrupy(FormularzGrupy):
    """Nowa grupa ze strony Grupy: z wyborem przedmiotu (tylko lata niezarchiwizowane)."""

    class Meta(FormularzGrupy.Meta):
        fields = ["kurs", "nazwa", "skrot", "opis", "aktywna"]
        labels = {"kurs": "Przedmiot"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kurs"].queryset = (Kurs.objects.filter(rok__zarchiwizowany=False).select_related("rok__uczelnia")
                                        .order_by("rok__poligon", "-rok__nazwa", "nazwa"))
        self.fields["kurs"].label_from_instance = lambda k: k.pelna_nazwa

    def clean_skrot(self):
        self.kurs = self.cleaned_data.get("kurs")
        return super().clean_skrot()


class FormularzZestawu(_WKursie, forms.ModelForm):
    class Meta:
        model = Zestaw
        fields = [
            "rodzaj", "nazwa", "skrot", "repo_zrodlowe", "wzorce_kodu", "punkty_max", "tresc_pdf", "tresc_tekst",
            "zasady_oceny", "modele", "kopiuj_actions",
        ]
        widgets = {
            "tresc_pdf": forms.ClearableFileInput(attrs={"accept": "application/pdf"}),
            "tresc_tekst": forms.Textarea(attrs={"rows": 8}),
            "zasady_oceny": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .oceniacze import skonfigurowane

        dostepne = ", ".join(m.identyfikator for m in skonfigurowane() if m.skonfigurowany)
        self.fields["modele"].help_text += f" Dostępne: {dostepne or 'brak (uzupełnij plik .env)'}."

    def clean_repo_zrodlowe(self):
        wartosc = oczysc_repo(self.cleaned_data["repo_zrodlowe"])
        if not wartosc:
            raise forms.ValidationError("Podaj repozytorium źródłowe.")
        return wartosc


def _przedmioty_do_kopii(wyklucz=None):
    przedmioty = Kurs.objects.filter(rok__zarchiwizowany=False).select_related("rok__uczelnia")
    return przedmioty.exclude(pk=wyklucz) if wyklucz else przedmioty


class FormularzKopiiZestawu(forms.Form):
    kurs = forms.ModelChoiceField(label="Do przedmiotu", queryset=Kurs.objects.none(),
                                  help_text="Np. ten sam przedmiot w kolejnym roku akademickim.")
    skrot = forms.SlugField(label="Skrót w przedmiocie docelowym", max_length=20, required=False,
                            help_text="Puste: ten sam skrót.")
    nazwa = forms.CharField(label="Nazwa w przedmiocie docelowym", max_length=100, required=False,
                            help_text="Puste: ta sama nazwa.")

    def __init__(self, *args, zestaw=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.zestaw = zestaw
        self.fields["kurs"].queryset = _przedmioty_do_kopii(zestaw.kurs_id)
        self.fields["kurs"].label_from_instance = lambda k: k.pelna_nazwa

    def clean(self):
        dane = super().clean()
        kurs = dane.get("kurs")
        skrot = dane.get("skrot") or self.zestaw.skrot
        dane["skrot"] = skrot
        if kurs and Zestaw.objects.filter(kurs=kurs, skrot=skrot).exists():
            self.add_error("skrot", "W tym przedmiocie jest już zestaw o takim skrócie. Podaj inny.")
        return dane


class FormularzKopiiKursu(forms.Form):
    """Przedmiot z zestawami (bez wydan grupom i bez studentow) do innego roku akademickiego."""

    rok = forms.ModelChoiceField(label="Do roku akademickiego", queryset=Rok.objects.none())
    nazwa = forms.CharField(label="Nazwa", max_length=150, required=False, help_text="Puste: ta sama nazwa.")
    skrot = forms.SlugField(label="Skrót", max_length=20, required=False, help_text="Puste: ten sam skrót.")
    grupy = forms.BooleanField(label="Kopiuj też grupy (bez studentów)", required=False,
                               help_text="Nazwy, skróty i opisy zajęć grup. Zwykle co roku są inne, więc domyślnie nie.")

    def __init__(self, *args, kurs=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.kurs = kurs
        self.fields["rok"].queryset = Rok.objects.filter(zarchiwizowany=False).select_related("uczelnia")
        self.fields["rok"].label_from_instance = lambda r: r.pelna_nazwa

    def clean(self):
        dane = super().clean()
        rok = dane.get("rok")
        dane["nazwa"] = dane.get("nazwa") or self.kurs.nazwa
        dane["skrot"] = skrot = dane.get("skrot") or self.kurs.skrot
        if rok and Kurs.objects.filter(rok=rok, skrot=skrot).exists():
            self.add_error("skrot", "W tym roku jest już przedmiot o takim skrócie. Podaj inny.")
        return dane


class FormularzPrzydzialu(forms.ModelForm):
    """Wydanie zestawu grupie: ze strony zestawu (wybor grupy) albo ze strony grupy (wybor zestawu)."""

    class Meta:
        model = Przydzial
        fields = ["zestaw", "grupa", "termin_miekki", "termin_twardy", "publikacja", "przedrostek_repo",
                  "repo_zrodlowe", "otwarty", "auto_ocena"]
        widgets = {"publikacja": PoleDatyICzasu(), "termin_miekki": PoleDatyICzasu(), "termin_twardy": PoleDatyICzasu()}

    def __init__(self, *args, zestaw=None, grupa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.zestaw, self.grupa = zestaw, grupa
        self.fields["publikacja"].template_name = SZABLON_POLA_PUBLIKACJI
        if zestaw is not None:
            del self.fields["zestaw"]
            self.fields["grupa"].queryset = Grupa.objects.filter(aktywna=True, kurs=zestaw.kurs_id).exclude(
                przydzialy__zestaw=zestaw)
        if grupa is not None:
            del self.fields["grupa"]
            self.fields["zestaw"].queryset = Zestaw.objects.filter(kurs=grupa.kurs_id).exclude(
                przydzialy__grupa=grupa).order_by("rodzaj", "skrot")
            self.fields["zestaw"].label = "Zestaw lub kolokwium"
        self.fields["przedrostek_repo"].required = False
        self.fields["przedrostek_repo"].help_text = (
            "Nazwy repozytoriów to dokładnie przedrostek + login (i przedrostek + start dla grupy), "
            "np. przedrostek uj-py2627-z03-g3- daje uj-py2627-z03-g3-jkowalski. Znak łączący na końcu wpisz sam. "
            "Puste: uczelnia-przedmiot i rok-zestaw-grupa-."
        )

    def clean_repo_zrodlowe(self):
        return oczysc_repo(self.cleaned_data.get("repo_zrodlowe"))

    def clean_publikacja(self):
        return publikacja_od(self.cleaned_data.get("publikacja"))

    def clean(self):
        dane = super().clean()
        zestaw = self.zestaw or dane.get("zestaw")
        grupa = self.grupa or dane.get("grupa")
        if grupa and zestaw and not dane.get("przedrostek_repo"):
            dane["przedrostek_repo"] = Przydzial.domyslny_przedrostek(zestaw, grupa)
        if grupa and zestaw and grupa.kurs_id != zestaw.kurs_id:
            raise forms.ValidationError("Zestaw i grupa muszą być z tego samego przedmiotu.")
        if grupa and zestaw and Przydzial.objects.filter(zestaw=zestaw, grupa=grupa).exists():
            raise forms.ValidationError("Ten zestaw jest już wydany tej grupie.")
        przedrostek = dane.get("przedrostek_repo")
        if przedrostek and Przydzial.objects.filter(przedrostek_repo=przedrostek).exists():
            self.add_error("przedrostek_repo", "Taki przedrostek jest już używany.")
        return dane

    def _post_clean(self):
        if self.zestaw is not None:
            self.instance.zestaw = self.zestaw
        if self.grupa is not None:
            self.instance.grupa = self.grupa
        if self.cleaned_data.get("przedrostek_repo"):
            self.instance.przedrostek_repo = self.cleaned_data["przedrostek_repo"]
        super()._post_clean()


class FormularzEdycjiPrzydzialu(forms.ModelForm):
    class Meta:
        model = Przydzial
        fields = ["termin_miekki", "termin_twardy", "publikacja", "repo_zrodlowe", "otwarty", "auto_ocena"]
        widgets = {"publikacja": PoleDatyICzasu(), "termin_miekki": PoleDatyICzasu(), "termin_twardy": PoleDatyICzasu()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["publikacja"].template_name = SZABLON_POLA_PUBLIKACJI
        if self.instance.publikacja and self.instance.publikacja <= timezone.now():
            # Juz opublikowany: pole puste i zwiniete; puste przy zapisie zostawia dotychczasowa publikacje
            self.initial["publikacja"] = None
        if self.instance.repo_startowe:
            # Repozytorium startowe juz powstalo: zrodlo zmienia sie tylko przez nowy zestaw
            del self.fields["repo_zrodlowe"]

    def clean_repo_zrodlowe(self):
        return oczysc_repo(self.cleaned_data.get("repo_zrodlowe"))

    def clean_publikacja(self):
        return publikacja_od(self.cleaned_data.get("publikacja"), self.instance.publikacja)


class FormularzKopiiPrzydzialu(forms.Form):
    """Jeden wiersz kopiowania zestawu do innej grupy tego samego przedmiotu."""

    grupa = forms.IntegerField(widget=forms.HiddenInput)
    wybrana = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": "wybor-grupy"}))
    publikacja = forms.DateTimeField(required=False, widget=PoleDatyICzasu())
    termin_miekki = forms.DateTimeField(required=False, widget=PoleDatyICzasu())
    termin_twardy = forms.DateTimeField(required=False, widget=PoleDatyICzasu())
    przedrostek_repo = forms.SlugField(max_length=60, required=False,
                                       widget=forms.TextInput(attrs={"title": "przedrostek repozytoriów"}))
    repo_zrodlowe = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={
        "placeholder": "inne źródło, np. wfais/zestaw3-b", "title": "inne repozytorium źródłowe (puste: repozytorium zestawu)"}))

    def clean_repo_zrodlowe(self):
        return oczysc_repo(self.cleaned_data.get("repo_zrodlowe"))

    def clean(self):
        dane = super().clean()
        if not dane.get("wybrana"):
            return dane
        miekki, twardy = dane.get("termin_miekki"), dane.get("termin_twardy")
        dane["publikacja"] = publikacja_od(dane.get("publikacja"))
        if miekki and dane["publikacja"] > max(miekki, timezone.now()):
            self.add_error("publikacja", "Publikacja nie może być późniejsza niż termin.")
        if not miekki:
            self.add_error("termin_miekki", "Podaj termin.")
        if not twardy:
            self.add_error("termin_twardy", "Podaj termin ostateczny.")
        if miekki and twardy and twardy < miekki:
            self.add_error("termin_twardy", "Termin ostateczny nie może być wcześniejszy niż termin.")
        przedrostek = dane.get("przedrostek_repo")
        if not przedrostek:
            self.add_error("przedrostek_repo", "Podaj przedrostek.")
        elif Przydzial.objects.filter(przedrostek_repo=przedrostek).exists():
            self.add_error("przedrostek_repo", "Taki przedrostek jest już używany.")
        return dane


class _ZestawKopii(forms.BaseFormSet):
    def clean(self):
        super().clean()
        widziane = set()
        for f in self.forms:
            dane = getattr(f, "cleaned_data", {})
            if dane.get("wybrana") and dane.get("przedrostek_repo"):
                if dane["przedrostek_repo"] in widziane:
                    f.add_error("przedrostek_repo", "Ten sam przedrostek w dwóch wierszach.")
                widziane.add(dane["przedrostek_repo"])


FormularzeKopiiPrzydzialu = forms.formset_factory(FormularzKopiiPrzydzialu, formset=_ZestawKopii, extra=0)


class FormularzImportu(forms.Form):
    plik = forms.FileField(label="Plik archiwum (.json)", widget=forms.ClearableFileInput(attrs={"accept": ".json"}))


class FormularzWyjatku(forms.ModelForm):
    class Meta:
        model = Zgloszenie
        fields = ["wyjatek_termin_miekki", "wyjatek_termin_twardy"]
        widgets = {"wyjatek_termin_miekki": PoleDatyICzasu(), "wyjatek_termin_twardy": PoleDatyICzasu()}
        labels = {"wyjatek_termin_miekki": "Termin", "wyjatek_termin_twardy": "Termin ostateczny"}
        help_texts = {"wyjatek_termin_miekki": "Puste albo taki jak grupy: termin oryginalny.",
                      "wyjatek_termin_twardy": "Puste albo taki jak grupy: termin oryginalny."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pola pokazuja terminy obowiazujace studenta (indywidualne albo grupy), do poprawienia na miejscu
        if not self.is_bound and self.instance.pk:
            self.initial["wyjatek_termin_miekki"] = self.instance.termin_miekki
            self.initial["wyjatek_termin_twardy"] = self.instance.termin_twardy

    def clean(self):
        dane = super().clean()
        miekki, twardy = dane.get("wyjatek_termin_miekki"), dane.get("wyjatek_termin_twardy")
        if miekki and twardy and twardy < miekki:
            raise forms.ValidationError("Termin ostateczny nie może być wcześniejszy niż termin.")
        # Termin taki sam jak grupy to brak wyjatku: zmiana terminu grupy obejmie tez tego studenta
        przydzial = self.instance.przydzial

        def ta_sama_minuta(a, b):
            return a.replace(second=0, microsecond=0) == b.replace(second=0, microsecond=0)

        if miekki and ta_sama_minuta(miekki, przydzial.termin_miekki):
            dane["wyjatek_termin_miekki"] = None
        if twardy and ta_sama_minuta(twardy, przydzial.termin_twardy):
            dane["wyjatek_termin_twardy"] = None
        return dane


class _NieWiecejNiz:
    def __init__(self, maks):
        self.maks = maks

    def __call__(self, wartosc):
        if wartosc is not None and wartosc > self.maks:
            raise forms.ValidationError(f"Maksymalnie {self.maks} pkt.")


class FormularzOceny(forms.Form):
    punkty = forms.DecimalField(
        label="Punkty za rozwiązanie", min_value=0, max_digits=6, decimal_places=2, localize=True,
    )
    mnoznik = forms.DecimalField(
        label="Mnożnik terminu", min_value=0, max_value=1, max_digits=4, decimal_places=2, localize=True,
        help_text="1 = w terminie, 0,8 = kara za spóźnienie.",
    )
    komentarz = forms.CharField(
        label="Komentarz końcowy", required=False, widget=forms.Textarea(attrs={"rows": 6}),
        help_text="Trafi do pull requestu razem z oceną. Uwagi do konkretnych linii dodawaj bezpośrednio na GitHubie.",
    )

    def __init__(self, *args, punkty_max: Decimal = None, **kwargs):
        super().__init__(*args, **kwargs)
        if punkty_max is not None:
            self.fields["punkty"].validators.append(_NieWiecejNiz(punkty_max))


class FormularzNazwiska(forms.ModelForm):
    class Meta:
        model = Student
        fields = ["imie_nazwisko"]


class FormularzAkceptacji(forms.Form):
    imie_nazwisko = forms.CharField(
        label="Imię i nazwisko", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "name", "placeholder": "Jan Kowalski"}),
    )

    def __init__(self, *args, wymagaj_nazwiska=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["imie_nazwisko"].required = wymagaj_nazwiska

    def clean_imie_nazwisko(self):
        wartosc = " ".join(self.cleaned_data.get("imie_nazwisko", "").split())
        if wartosc and len(wartosc.split()) < 2:
            raise forms.ValidationError("Podaj imię i nazwisko.")
        return wartosc


FormularzeZadan = forms.inlineformset_factory(
    Zestaw, Zadanie, fields=["kolejnosc", "kod", "katalog", "punkty", "tryb", "kryteria"], extra=1, can_delete=True,
    widgets={
        "kryteria": forms.Textarea(attrs={"rows": 3}),
        "kolejnosc": forms.NumberInput(attrs={"style": "width:60px"}),
        "punkty": forms.NumberInput(attrs={"style": "width:80px", "step": "0.25"}),
    },
)


class FormularzPrecedensu(forms.ModelForm):
    class Meta:
        model = Precedens
        fields = ["zestaw", "kod_zadania", "tresc", "aktywny"]
        widgets = {"tresc": forms.Textarea(attrs={"rows": 2})}
