"""Model danych: grupy, zestawy, przydzialy zestawow do grup, studenci i ich zgloszenia."""
import re
import secrets
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse


def nowy_token():
    return secrets.token_urlsafe(12)


class Grupa(models.Model):
    nazwa = models.CharField("nazwa", max_length=100)
    skrot = models.SlugField(
        "skrót", max_length=20, unique=True,
        help_text="Krótki identyfikator, np. g4. Trafia do nazw repozytoriów.",
    )
    opis = models.CharField("opis", max_length=200, blank=True)
    aktywna = models.BooleanField("aktywna", default=True)

    class Meta:
        ordering = ["nazwa"]
        verbose_name = "grupa"
        verbose_name_plural = "grupy"

    def __str__(self):
        return self.nazwa


class Zestaw(models.Model):
    nazwa = models.CharField("nazwa", max_length=100)
    skrot = models.SlugField(
        "skrót", max_length=20, unique=True,
        help_text="Krótki identyfikator, np. z03. Trafia do nazw repozytoriów.",
    )
    repo_zrodlowe = models.CharField(
        "repozytorium źródłowe", max_length=200,
        help_text="Twoje repozytorium z zadaniami w organizacji, np. wfais/python-zestaw3.",
    )
    wzorce_kodu = models.CharField(
        "pliki z kodem", max_length=200, default="*.py",
        help_text="Wzorce plików liczonych jako kod (oddzielone przecinkami). Zmiany w innych plikach, "
                  "np. README.md, nie wpływają na terminy.",
    )
    punkty_max = models.DecimalField("punkty maksymalne", max_digits=6, decimal_places=2, default=5)
    tresc_pdf = models.FileField("treść zadań (PDF)", upload_to="zestawy/", blank=True)
    tresc_tekst = models.TextField(
        "treść zadań (tekst dla modeli)", blank=True,
        help_text="Wypełniana automatycznie z PDF. Możesz ją poprawić, modele oceniające dostają ten tekst.",
    )
    zasady_oceny = models.TextField(
        "dodatkowe zasady oceniania", blank=True,
        help_text="Wskazówki dla modeli obowiązujące w tym zestawie, np. czego nie karać.",
    )
    modele = models.CharField(
        "modele oceniające", max_length=300, blank=True,
        help_text="Identyfikatory modeli oddzielone przecinkami. Puste: wszystkie skonfigurowane.",
    )
    auto_zamykanie = models.BooleanField(
        "zamykaj automatycznie zgodne oceny", default=False,
        help_text="Gdy modele są zgodne i nic nie jest podejrzane, ocena trafia do studenta bez Twojego udziału "
                  "(z sugerowanym mnożnikiem terminu). Sporne prace zawsze czekają na Ciebie.",
    )
    utworzono = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["skrot"]
        verbose_name = "zestaw"
        verbose_name_plural = "zestawy"

    def __str__(self):
        return self.nazwa

    def lista_wzorcow(self):
        wzorce = [w.strip() for w in re.split(r"[,\s]+", self.wzorce_kodu or "") if w.strip()]
        return wzorce or ["*.py"]


class Przydzial(models.Model):
    """Zestaw wydany konkretnej grupie: wlasne terminy, wlasny link i wlasne repozytorium startowe."""

    zestaw = models.ForeignKey(Zestaw, on_delete=models.PROTECT, related_name="przydzialy", verbose_name="zestaw")
    grupa = models.ForeignKey(Grupa, on_delete=models.PROTECT, related_name="przydzialy", verbose_name="grupa")
    termin_miekki = models.DateTimeField("termin", help_text="Do tego terminu pełne punkty.")
    termin_twardy = models.DateTimeField(
        "termin ostateczny", help_text="Zmiany w kodzie po terminie i przed tą chwilą: sugerowana kara.",
    )
    przedrostek_repo = models.SlugField(
        "przedrostek repozytoriów", max_length=60, unique=True,
        help_text="Repozytoria studentów: przedrostek-login, np. py-z03-g4-jkowalski.",
    )
    token = models.CharField(max_length=40, unique=True, default=nowy_token, editable=False)
    otwarty = models.BooleanField("przyjmuje zgłoszenia", default=True)
    auto_ocena = models.BooleanField(
        "oceniaj automatycznie po terminie ostatecznym", default=False,
        help_text="Po terminie ostatecznym robot sam uruchomi testy i ocenę modelami dla wszystkich zgłoszeń.",
    )
    repo_startowe = models.CharField("repozytorium startowe", max_length=200, blank=True)
    sha_startowy = models.CharField(max_length=40, blank=True)
    utworzono = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["termin_miekki"]
        verbose_name = "zestaw w grupie"
        verbose_name_plural = "zestawy w grupach"
        constraints = [models.UniqueConstraint(fields=["zestaw", "grupa"], name="zestaw_raz_w_grupie")]

    def __str__(self):
        return f"{self.zestaw} / {self.grupa}"

    def clean(self):
        if self.termin_miekki and self.termin_twardy and self.termin_twardy < self.termin_miekki:
            raise ValidationError({"termin_twardy": "Termin ostateczny nie może być wcześniejszy niż termin."})

    @property
    def link_akceptacji(self):
        return settings.PUBLICZNY_ADRES + reverse("student:zestaw", args=[self.token])

    @property
    def url_repo_startowego(self):
        return f"{settings.GITHUB_URL}/{self.repo_startowe}" if self.repo_startowe else ""


class Student(models.Model):
    class Zrodlo(models.TextChoices):
        FORMULARZ = "formularz", "formularz akceptacji"
        README = "readme", "README (zatwierdzone)"
        RECZNIE = "recznie", "wpisane ręcznie"

    github_id = models.BigIntegerField("numer konta GitHub", unique=True)
    login = models.CharField("login GitHub", max_length=100)
    imie_nazwisko = models.CharField("imię i nazwisko", max_length=200, blank=True)
    zrodlo_nazwiska = models.CharField("źródło", max_length=20, choices=Zrodlo.choices, blank=True)
    kandydat_readme = models.CharField("kandydat z README", max_length=200, blank=True)
    kandydat_repo = models.CharField("repozytorium kandydata", max_length=200, blank=True)
    utworzono = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["imie_nazwisko", "login"]
        verbose_name = "student"
        verbose_name_plural = "studenci"

    def __str__(self):
        return self.wyswietlana_nazwa

    @property
    def wyswietlana_nazwa(self):
        return self.imie_nazwisko or f"@{self.login}"

    @property
    def klucz_sortowania(self):
        if self.imie_nazwisko:
            slowa = self.imie_nazwisko.split()
            return (0, slowa[-1].lower(), self.imie_nazwisko.lower())
        return (1, self.login.lower(), "")


ETYKIETY_ZMIAN_TESTOW = (
    ("zmienione", "zmienione"),
    ("usuniete", "usunięte"),
    ("dodane", "dodane"),
    ("wczesniej", "zmieniane wcześniej, potem przywrócone"),
)


class Zgloszenie(models.Model):
    """Akceptacja zestawu przez studenta wraz z jego repozytorium i stanem terminow."""

    class Status(models.TextChoices):
        TWORZENIE = "tworzenie", "tworzenie repozytorium"
        GOTOWE = "gotowe", "gotowe"
        BLAD = "blad", "błąd"
        WYZEROWANE = "wyzerowane", "wyzerowane"

    class Kolor(models.TextChoices):
        SZARY = "szary", "brak"
        NIEBIESKI = "niebieski", "w toku"
        ZIELONY = "zielony", "w terminie"
        ZOLTY = "zolty", "spóźnione"
        POMARANCZOWY = "pomaranczowy", "zmiany po terminie"
        CZERWONY = "czerwony", "po terminie"

    przydzial = models.ForeignKey(Przydzial, on_delete=models.PROTECT, related_name="zgloszenia")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="zgloszenia")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TWORZENIE)
    blad_opis = models.TextField(blank=True)
    w_toku_od = models.DateTimeField(null=True, blank=True)

    repo_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    repo_nazwa = models.CharField(max_length=200, blank=True)
    sha_bazowy = models.CharField(max_length=40, blank=True)
    sha_poczatkowy = models.CharField(max_length=40, blank=True)
    zaproszenie_wyslane = models.BooleanField(default=False)
    pr_numer = models.PositiveIntegerField(null=True, blank=True)

    wyjatek_termin_miekki = models.DateTimeField("indywidualny termin", null=True, blank=True)
    wyjatek_termin_twardy = models.DateTimeField("indywidualny termin ostateczny", null=True, blank=True)

    # Wynik ostatniego przeliczenia terminow
    stan_kolor = models.CharField(max_length=20, choices=Kolor.choices, default=Kolor.SZARY)
    stan_opis = models.CharField(max_length=300, blank=True)
    sugerowany_mnoznik = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    sha_oceniany = models.CharField(max_length=40, blank=True)
    sha_teraz = models.CharField(max_length=40, blank=True)
    ostatnie_wypchniecie = models.DateTimeField(null=True, blank=True)
    przeliczono = models.DateTimeField(null=True, blank=True)
    do_przeliczenia = models.BooleanField(default=True)
    sha_readme = models.CharField(max_length=40, blank=True)
    # Zmiany studenta w plikach testow, ich danych i konfiguracji pytest wzgledem stanu startowego
    # (klucze: zmienione, usuniete, dodane, wczesniej, niepewne)
    testy_zmienione = models.BooleanField("zmienione testy", default=False)
    zmiany_testow = models.JSONField(default=dict, blank=True)

    # Decyzja prowadzacego
    punkty = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    mnoznik = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    komentarz_koncowy = models.TextField(blank=True)
    pr_zamkniety = models.BooleanField(default=False)
    oceniono = models.DateTimeField(null=True, blank=True)
    tresc_oceny_wyslana = models.TextField(blank=True)

    utworzono = models.DateTimeField(auto_now_add=True)
    zaktualizowano = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-utworzono"]
        verbose_name = "zgłoszenie"
        verbose_name_plural = "zgłoszenia"
        constraints = [
            models.UniqueConstraint(
                fields=["przydzial", "student"],
                condition=~Q(status="wyzerowane"),
                name="jedno_aktywne_zgloszenie",
            )
        ]

    def __str__(self):
        return f"{self.student} / {self.przydzial}"

    @property
    def termin_miekki(self):
        return self.wyjatek_termin_miekki or self.przydzial.termin_miekki

    @property
    def termin_twardy(self):
        # Termin ostateczny nigdy nie jest wczesniejszy niz (indywidualny) termin
        return max(self.wyjatek_termin_twardy or self.przydzial.termin_twardy, self.termin_miekki)

    @property
    def ma_wyjatek(self):
        return bool(self.wyjatek_termin_miekki or self.wyjatek_termin_twardy)

    @property
    def url_repo(self):
        return f"{settings.GITHUB_URL}/{self.repo_nazwa}" if self.repo_nazwa else ""

    @property
    def url_pr(self):
        return f"{self.url_repo}/pull/{self.pr_numer}" if self.repo_nazwa and self.pr_numer else ""

    @property
    def url_pr_pliki(self):
        return f"{self.url_pr}/files" if self.url_pr else ""

    @property
    def url_zaproszenia(self):
        return f"{self.url_repo}/invitations" if self.repo_nazwa else ""

    @property
    def zmiany_testow_powazne(self) -> bool:
        """Zmienione lub usuniete pliki prowadzacego (teraz albo w ktoryms wczesniejszym stanie)."""
        z = self.zmiany_testow or {}
        return bool(z.get("zmienione") or z.get("usuniete") or z.get("wczesniej"))

    @property
    def lista_zmian_testow(self) -> list:
        z = self.zmiany_testow or {}
        return [(etykieta, z[klucz]) for klucz, etykieta in ETYKIETY_ZMIAN_TESTOW if z.get(klucz)]

    @property
    def opis_zmian_testow(self) -> str:
        return "; ".join(f"{etykieta}: {', '.join(pliki)}" for etykieta, pliki in self.lista_zmian_testow)

    @property
    def url_ocenianego_stanu(self):
        return f"{self.url_repo}/tree/{self.sha_oceniany}" if self.repo_nazwa and self.sha_oceniany else ""

    @property
    def wynik(self):
        if self.punkty is None:
            return None
        mnoznik = self.mnoznik if self.mnoznik is not None else Decimal("1")
        return (self.punkty * mnoznik).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Wypchniecie(models.Model):
    """Pojedyncze wypchniecie (git push) zarejestrowane przez GitHub."""

    class Zrodlo(models.TextChoices):
        WEBHOOK = "webhook", "powiadomienie"
        AKTYWNOSC = "aktywnosc", "dziennik aktywności"

    repo_id = models.BigIntegerField(db_index=True)
    repo_nazwa = models.CharField(max_length=200)
    ref = models.CharField(max_length=200)
    sha_przed = models.CharField(max_length=40)
    sha_po = models.CharField(max_length=40)
    czas = models.DateTimeField("czas wypchnięcia")
    autor = models.CharField(max_length=100, blank=True)
    zrodlo = models.CharField(max_length=20, choices=Zrodlo.choices)
    zarejestrowano = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["czas", "id"]
        verbose_name = "wypchnięcie"
        verbose_name_plural = "wypchnięcia"
        constraints = [
            models.UniqueConstraint(fields=["repo_id", "ref", "sha_przed", "sha_po"], name="unikalne_wypchniecie")
        ]

    def __str__(self):
        return f"{self.repo_nazwa} {self.ref} {self.sha_po[:7]} {self.czas:%Y-%m-%d %H:%M}"


class Porownanie(models.Model):
    """Pamiec podreczna wyniku porownania dwoch stanow repozytorium (lista zmienionych plikow)."""

    repo_id = models.BigIntegerField()
    sha_od = models.CharField(max_length=40)
    sha_do = models.CharField(max_length=40)
    pliki = models.JSONField(default=list)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["repo_id", "sha_od", "sha_do"], name="unikalne_porownanie")]


class Zadanie(models.Model):
    """Pojedyncze zadanie w zestawie: punktacja i kryteria dla oceny automatycznej."""

    class Tryb(models.TextChoices):
        TESTY_I_MODELE = "testy_modele", "testy i modele"
        MODELE = "modele", "tylko modele (np. GUI, gry)"
        RECZNIE = "recznie", "tylko ręcznie"

    zestaw = models.ForeignKey(Zestaw, on_delete=models.CASCADE, related_name="zadania")
    kolejnosc = models.PositiveSmallIntegerField("kolejność", default=0)
    kod = models.CharField("oznaczenie", max_length=50, help_text="Np. ZADANIE1.")
    katalog = models.CharField(
        "katalog w repozytorium", max_length=200, blank=True,
        help_text="Np. ZADANIE1/. Puste: zadanie obejmuje całe repozytorium.",
    )
    punkty = models.DecimalField("punkty", max_digits=6, decimal_places=2)
    kryteria = models.TextField("kryteria oceny", blank=True)
    tryb = models.CharField("sposób oceny", max_length=20, choices=Tryb.choices, default=Tryb.TESTY_I_MODELE)

    class Meta:
        ordering = ["kolejnosc", "kod"]
        verbose_name = "zadanie"
        verbose_name_plural = "zadania"
        constraints = [models.UniqueConstraint(fields=["zestaw", "kod"], name="unikalny_kod_zadania")]

    def __str__(self):
        return f"{self.zestaw.skrot}/{self.kod}"

    def obejmuje(self, sciezka: str) -> bool:
        katalog = self.katalog.strip().strip("/")
        return not katalog or sciezka == katalog or sciezka.startswith(katalog + "/")


class Ewaluacja(models.Model):
    """Jedno uruchomienie oceny automatycznej (testy i modele) dla stanu repozytorium studenta."""

    class Status(models.TextChoices):
        OCZEKUJE = "oczekuje", "w kolejce"
        TESTY = "testy", "testy"
        MODELE = "modele", "ocena modelami"
        GOTOWA = "gotowa", "gotowa"
        BLAD = "blad", "błąd"

    zgloszenie = models.ForeignKey(Zgloszenie, on_delete=models.CASCADE, related_name="ewaluacje")
    sha = models.CharField(max_length=40)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OCZEKUJE)
    blad_opis = models.TextField(blank=True)
    testy = models.JSONField(default=dict, blank=True)
    pliki = models.JSONField(default=list, blank=True)
    podejrzane = models.TextField(blank=True)
    suma = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    komentarz = models.TextField(blank=True)
    sporna = models.BooleanField(default=False)
    zatwierdzona = models.BooleanField(default=False)
    automatycznie = models.BooleanField(default=False)
    tokeny_we = models.PositiveIntegerField(default=0)
    tokeny_wy = models.PositiveIntegerField(default=0)
    utworzono = models.DateTimeField(auto_now_add=True)
    zakonczono = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-utworzono"]
        verbose_name = "ewaluacja"
        verbose_name_plural = "ewaluacje"

    def __str__(self):
        return f"{self.zgloszenie} @ {self.sha[:7]}"

    @property
    def w_toku(self):
        return self.status in (self.Status.OCZEKUJE, self.Status.TESTY, self.Status.MODELE)


class OcenaZadania(models.Model):
    ewaluacja = models.ForeignKey(Ewaluacja, on_delete=models.CASCADE, related_name="oceny_zadan")
    zadanie = models.ForeignKey(Zadanie, on_delete=models.SET_NULL, null=True, blank=True)
    kod = models.CharField(max_length=50)
    punkty_max = models.DecimalField(max_digits=6, decimal_places=2)
    tryb = models.CharField(max_length=20, blank=True)
    testy_zdane = models.PositiveIntegerField(null=True, blank=True)
    testy_wszystkie = models.PositiveIntegerField(null=True, blank=True)
    punkty_konsensus = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    sporne = models.BooleanField(default=False)
    punkty_prowadzacego = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    komentarz = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["id"]

    @property
    def punkty_koncowe(self):
        return self.punkty_prowadzacego if self.punkty_prowadzacego is not None else self.punkty_konsensus


class OpiniaModelu(models.Model):
    ewaluacja = models.ForeignKey(Ewaluacja, on_delete=models.CASCADE, related_name="opinie")
    model = models.CharField(max_length=150)
    runda = models.PositiveSmallIntegerField(default=1)
    ok = models.BooleanField(default=False)
    blad = models.TextField(blank=True)
    dane = models.JSONField(default=dict, blank=True)
    surowa = models.TextField(blank=True)
    tokeny_we = models.PositiveIntegerField(default=0)
    tokeny_wy = models.PositiveIntegerField(default=0)
    czas_s = models.FloatField(default=0)

    class Meta:
        ordering = ["runda", "model"]


class Precedens(models.Model):
    """Rozstrzygniecie prowadzacego dolaczane do kolejnych ocen (kalibracja modeli)."""

    zestaw = models.ForeignKey(
        Zestaw, on_delete=models.CASCADE, null=True, blank=True, related_name="precedensy",
        help_text="Puste: obowiązuje we wszystkich zestawach.",
    )
    kod_zadania = models.CharField("zadanie", max_length=50, blank=True)
    tresc = models.TextField("reguła")
    aktywny = models.BooleanField("aktywny", default=True)
    zrodlo = models.ForeignKey(Ewaluacja, on_delete=models.SET_NULL, null=True, blank=True)
    utworzono = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-utworzono"]
        verbose_name = "precedens"
        verbose_name_plural = "precedensy"

    def __str__(self):
        return self.tresc[:60]
