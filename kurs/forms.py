"""Formularze panelu prowadzacego i strony akceptacji."""
import re
from decimal import Decimal

from django import forms

from .models import Grupa, Precedens, Przydzial, Student, Zadanie, Zestaw, Zgloszenie


class PoleDatyICzasu(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%dT%H:%M", **kwargs)


class FormularzGrupy(forms.ModelForm):
    class Meta:
        model = Grupa
        fields = ["nazwa", "skrot", "opis", "aktywna"]


class FormularzZestawu(forms.ModelForm):
    class Meta:
        model = Zestaw
        fields = [
            "nazwa", "skrot", "repo_zrodlowe", "wzorce_kodu", "punkty_max", "tresc_pdf", "tresc_tekst",
            "zasady_oceny", "modele", "auto_zamykanie",
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
        wartosc = self.cleaned_data["repo_zrodlowe"].strip()
        wartosc = re.sub(r"^(https?://github\.com/|git@[^:]+:)", "", wartosc)
        wartosc = wartosc.removesuffix(".git").strip("/")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", wartosc):
            raise forms.ValidationError("Podaj repozytorium w postaci organizacja/nazwa, np. wfais/python-zestaw3.")
        return wartosc


class FormularzPrzydzialu(forms.ModelForm):
    class Meta:
        model = Przydzial
        fields = ["grupa", "termin_miekki", "termin_twardy", "przedrostek_repo", "otwarty", "auto_ocena"]
        widgets = {"termin_miekki": PoleDatyICzasu(), "termin_twardy": PoleDatyICzasu()}

    def __init__(self, *args, zestaw=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.zestaw = zestaw
        self.fields["grupa"].queryset = Grupa.objects.filter(aktywna=True)
        self.fields["przedrostek_repo"].required = False
        self.fields["przedrostek_repo"].help_text = (
            "Puste: zostanie utworzony automatycznie (py-skrót zestawu-skrót grupy)."
        )

    def clean(self):
        dane = super().clean()
        grupa = dane.get("grupa")
        if grupa and self.zestaw and not dane.get("przedrostek_repo"):
            dane["przedrostek_repo"] = f"py-{self.zestaw.skrot}-{grupa.skrot}".lower()
        if grupa and self.zestaw and Przydzial.objects.filter(zestaw=self.zestaw, grupa=grupa).exists():
            raise forms.ValidationError("Ten zestaw jest już wydany tej grupie.")
        przedrostek = dane.get("przedrostek_repo")
        if przedrostek and Przydzial.objects.filter(przedrostek_repo=przedrostek).exists():
            self.add_error("przedrostek_repo", "Taki przedrostek jest już używany.")
        return dane

    def _post_clean(self):
        self.instance.zestaw = self.zestaw
        if self.cleaned_data.get("przedrostek_repo"):
            self.instance.przedrostek_repo = self.cleaned_data["przedrostek_repo"]
        super()._post_clean()


class FormularzEdycjiPrzydzialu(forms.ModelForm):
    class Meta:
        model = Przydzial
        fields = ["termin_miekki", "termin_twardy", "otwarty", "auto_ocena"]
        widgets = {"termin_miekki": PoleDatyICzasu(), "termin_twardy": PoleDatyICzasu()}


class FormularzWyjatku(forms.ModelForm):
    class Meta:
        model = Zgloszenie
        fields = ["wyjatek_termin_miekki", "wyjatek_termin_twardy"]
        widgets = {"wyjatek_termin_miekki": PoleDatyICzasu(), "wyjatek_termin_twardy": PoleDatyICzasu()}
        help_texts = {"wyjatek_termin_miekki": "Puste: termin grupy.", "wyjatek_termin_twardy": "Puste: termin grupy."}

    def clean(self):
        dane = super().clean()
        miekki, twardy = dane.get("wyjatek_termin_miekki"), dane.get("wyjatek_termin_twardy")
        if miekki and twardy and twardy < miekki:
            raise forms.ValidationError("Termin ostateczny nie może być wcześniejszy niż termin.")
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
    Zestaw, Zadanie, fields=["kolejnosc", "kod", "katalog", "punkty", "tryb", "kryteria"], extra=2, can_delete=True,
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
