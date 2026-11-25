"""Uruchamianie przeplywow GitHub Actions w repozytoriach studentow przez robota."""
from datetime import timedelta

import pytest
from django.utils import timezone

from kurs import przeplywy
from kurs.github import klient as klient_modul
from kurs.models import StanPrzeplywu, Student, UstawieniaSerwisu, Zestaw, Zgloszenie

PLIK = "traficar.yml"
WYLACZONY = """on:
  workflow_dispatch:
  # schedule:
  #   - cron: "MM,MM * * * *"
"""
WLACZONY = """on:
  workflow_dispatch:
  schedule:
    - cron: "7,37 * * * *"   # co 30 minut
"""


class FalszyweActions:
    """Imitacja API GitHub Actions dla jednego repozytorium."""

    def __init__(self):
        self.stan = "active"
        self.jest = True
        self.tresc = WLACZONY
        self.ostatnie = None      # najnowsze uruchomienie (skrot, gdy historia jest pusta)
        self.historia = []        # ostatnie uruchomienia, od najnowszego
        self.zapytania_o_uruchomienia = 0
        self.uruchomienia = []
        self.blad = None

    def przeplyw(self, pelna, plik):
        if self.blad:
            raise klient_modul.BladGitHub(403, self.blad)
        return {"path": f".github/workflows/{plik}", "state": self.stan} if self.jest else None

    def plik_tekstowy(self, pelna, sciezka, ref=None):
        return self.tresc

    def ostatnie_uruchomienia(self, pelna, plik, ile=3):
        self.zapytania_o_uruchomienia += 1
        historia = self.historia or ([self.ostatnie] if self.ostatnie else [])
        return historia[:ile]

    def uruchom_przeplyw(self, pelna, plik, galaz):
        self.uruchomienia.append((pelna, plik, galaz))


@pytest.fixture
def actions(monkeypatch):
    falszywe = FalszyweActions()
    for nazwa in ("przeplyw", "plik_tekstowy", "ostatnie_uruchomienia", "uruchom_przeplyw"):
        monkeypatch.setattr(klient_modul, nazwa, getattr(falszywe, nazwa))
    return falszywe


@pytest.fixture
def wlaczone(db):
    ustawienia = UstawieniaSerwisu.biezace()
    ustawienia.przeplywy_wlaczone = True
    ustawienia.save()
    return ustawienia


@pytest.fixture
def zgloszenie(przydzial):
    zestaw = przydzial.zestaw
    zestaw.kopiuj_actions, zestaw.przeplyw_plik, zestaw.przeplyw_co_minut = True, PLIK, 60
    zestaw.save()
    return Zgloszenie.objects.create(
        przydzial=przydzial, student=Student.objects.create(github_id=77, login="jank"),
        status=Zgloszenie.Status.GOTOWE, repo_nazwa="wfais/py-z03-g4-jank",
    )


def _uruchomienie(minut_temu, status="completed", wynik="success"):
    czas = (timezone.now() - timedelta(minutes=minut_temu)).isoformat().replace("+00:00", "Z")
    return {"created_at": czas, "status": status, "conclusion": wynik}


@pytest.mark.parametrize("tresc, oczekiwane", [
    (WLACZONY, True),
    (WYLACZONY, False),
    ("on:\n  schedule:\n  # - cron: '7 * * * *'\n", False),   # sam naglowek bez linii cron
    ("on:\n  push:\n", False),
    ("", False),
    (None, False),
])
def test_rozpoznanie_wlaczonego_harmonogramu(tresc, oczekiwane):
    assert przeplywy.harmonogram_wlaczony(tresc) is oczekiwane


@pytest.mark.django_db
def test_bez_glownego_wylacznika_nic_sie_nie_dzieje(actions, zgloszenie):
    assert przeplywy.cykl() == {}
    assert actions.uruchomienia == []
    zgloszenie.refresh_from_db()
    assert zgloszenie.przeplyw_stan == ""


@pytest.mark.django_db
def test_pierwsze_uruchomienie(actions, wlaczone, zgloszenie):
    assert przeplywy.cykl() == {StanPrzeplywu.DZIALA: 1}
    assert actions.uruchomienia == [("wfais/py-z03-g4-jank", PLIK, "main")]
    zgloszenie.refresh_from_db()
    assert zgloszenie.przeplyw_stan == StanPrzeplywu.DZIALA
    assert zgloszenie.przeplyw_wyzwolenia == 1
    assert zgloszenie.przeplyw_ostatnie is not None
    assert UstawieniaSerwisu.biezace().przeplywy_licznik == 1


@pytest.mark.django_db
def test_czeka_az_student_wlaczy_harmonogram(actions, wlaczone, zgloszenie):
    actions.tresc = WYLACZONY
    przeplywy.cykl()
    assert actions.uruchomienia == []
    zgloszenie.refresh_from_db()
    assert zgloszenie.przeplyw_stan == StanPrzeplywu.CZEKA


@pytest.mark.django_db
def test_tryb_zawsze_nie_patrzy_na_harmonogram(actions, wlaczone, zgloszenie):
    Zestaw.objects.update(przeplyw_start=Zestaw.StartPrzeplywu.ZAWSZE)
    actions.tresc = WYLACZONY
    przeplywy.cykl()
    assert len(actions.uruchomienia) == 1


@pytest.mark.django_db
@pytest.mark.parametrize("minut_temu, uruchomiono", [(20, False), (49, False), (51, True), (300, True)])
def test_odstep_miedzy_uruchomieniami(actions, wlaczone, zgloszenie, minut_temu, uruchomiono):
    # co 60 minut z zapasem 10 minut na cykl robota
    actions.ostatnie = _uruchomienie(minut_temu)
    przeplywy.cykl()
    assert bool(actions.uruchomienia) is uruchomiono


@pytest.mark.django_db
def test_uruchomienie_w_toku_nie_jest_dublowane(actions, wlaczone, zgloszenie):
    actions.ostatnie = _uruchomienie(120, status="in_progress", wynik=None)
    przeplywy.cykl()
    assert actions.uruchomienia == []


def _seria(*wyniki):
    """Historia uruchomien od najnowszego, co godzine (najnowsze 70 minut temu, czyli po odstepie)."""
    return [_uruchomienie(70 + 60 * i, wynik=w) for i, w in enumerate(wyniki)]


@pytest.mark.django_db
def test_trzy_porazki_z_rzedu_wstrzymuja_uruchamianie(actions, wlaczone, zgloszenie):
    actions.historia = _seria("failure", "timed_out", "startup_failure")
    assert przeplywy.cykl() == {StanPrzeplywu.BLAD: 1}
    assert actions.uruchomienia == []
    assert actions.zapytania_o_uruchomienia == 1   # jedno zapytanie o historie uruchomien
    zgloszenie.refresh_from_db()
    assert zgloszenie.przeplyw_stan == StanPrzeplywu.BLAD
    assert zgloszenie.przeplyw_opis == przeplywy.OPIS_SERII_BLEDOW
    assert zgloszenie.przeplyw_opis.startswith("3 nieudane uruchomienia z rzędu: przepływ nie jest uruchamiany")
    assert zgloszenie.przeplyw_ostatnie is not None
    assert UstawieniaSerwisu.biezace().przeplywy_licznik == 0


@pytest.mark.django_db
@pytest.mark.parametrize("wyniki, uruchomiono", [
    (("failure", "failure"), True),                                    # dwie porazki: za malo
    (("success", "failure", "failure"), True),                         # udane (np. reczne po poprawce) wznawia
    (("failure", "failure", "success", "failure"), True),              # sukces przerywa serie
    (("failure", "skipped", "failure", "failure"), True),              # inny wynik tez przerywa serie
    # anulowane sa pomijane: nie licza sie jako blad i nie przerywaja serii
    (("failure", "cancelled", "failure", "failure"), False),
    (("cancelled", "failure", "failure", "failure"), False),
    (("failure", "cancelled", "cancelled", "failure"), True),          # tylko dwie zakonczone porazki
    (("cancelled", "cancelled", "cancelled"), True),
])
def test_regula_serii_bledow(actions, wlaczone, zgloszenie, wyniki, uruchomiono):
    actions.historia = _seria(*wyniki)
    przeplywy.cykl()
    assert bool(actions.uruchomienia) is uruchomiono
    zgloszenie.refresh_from_db()
    assert zgloszenie.przeplyw_stan == (StanPrzeplywu.DZIALA if uruchomiono else StanPrzeplywu.BLAD)


@pytest.mark.django_db
def test_uruchomienie_w_toku_ma_pierwszenstwo_przed_seria_bledow(actions, wlaczone, zgloszenie):
    actions.historia = [_uruchomienie(5, status="in_progress", wynik=None)] + _seria("failure", "failure", "failure")
    przeplywy.cykl()
    assert actions.uruchomienia == []
    zgloszenie.refresh_from_db()
    assert zgloszenie.przeplyw_stan == StanPrzeplywu.DZIALA
    assert zgloszenie.przeplyw_opis == "Uruchomienie w toku."


@pytest.mark.django_db
def test_seria_bledow_widoczna_na_karcie_i_w_ustawieniach(client, prowadzacy, actions, wlaczone, zgloszenie):
    actions.historia = _seria("failure", "failure", "failure")
    przeplywy.cykl()
    karta = client.get(f"/panel/zgloszenia/{zgloszenie.pk}/").content.decode()
    assert '<span class="znacznik czerwony">błąd</span>' in karta
    assert "3 nieudane uruchomienia z rzędu" in karta and "przycisk Run workflow" in karta
    ustawienia = client.get("/panel/ustawienia/").content.decode()
    assert "błąd 1" in ustawienia


@pytest.mark.django_db
def test_wylaczony_przeplyw_konczy_uruchamianie(actions, wlaczone, zgloszenie):
    actions.stan = "disabled_manually"
    przeplywy.cykl()
    assert actions.uruchomienia == []
    zgloszenie.refresh_from_db()
    assert zgloszenie.przeplyw_stan == StanPrzeplywu.ZAKONCZONY


@pytest.mark.django_db
def test_po_terminie_ostatecznym_bez_uruchomien(actions, wlaczone, zgloszenie):
    zgloszenie.wyjatek_termin_miekki = timezone.now() - timedelta(days=2)
    zgloszenie.wyjatek_termin_twardy = timezone.now() - timedelta(days=1)
    zgloszenie.save()
    przeplywy.cykl()
    assert actions.uruchomienia == []
    zgloszenie.refresh_from_db()
    assert zgloszenie.przeplyw_stan == StanPrzeplywu.ZAKONCZONY


@pytest.mark.django_db
def test_brak_pliku_i_blad_api(actions, wlaczone, zgloszenie):
    actions.jest = False
    przeplywy.cykl()
    zgloszenie.refresh_from_db()
    assert zgloszenie.przeplyw_stan == StanPrzeplywu.BRAK
    actions.blad = "Resource not accessible by integration"
    przeplywy.cykl()
    zgloszenie.refresh_from_db()
    assert zgloszenie.przeplyw_stan == StanPrzeplywu.BLAD
    assert "not accessible" in zgloszenie.przeplyw_opis
    assert actions.uruchomienia == []


@pytest.mark.django_db
def test_dzienny_limit(actions, wlaczone, zgloszenie):
    wlaczone.przeplywy_limit = 0
    wlaczone.save()
    przeplywy.cykl()
    assert actions.uruchomienia == []
    zgloszenie.refresh_from_db()
    assert "limit" in zgloszenie.przeplyw_opis


@pytest.mark.django_db
def test_licznik_dzienny_zeruje_sie_kolejnego_dnia(actions, wlaczone, zgloszenie):
    wlaczone.przeplywy_limit = 1
    wlaczone.przeplywy_dzien = timezone.localdate() - timedelta(days=1)
    wlaczone.przeplywy_licznik = 1
    wlaczone.save()
    przeplywy.cykl()
    assert len(actions.uruchomienia) == 1


@pytest.mark.django_db
def test_pomija_zestawy_bez_przeplywu_i_poligon(actions, wlaczone, zgloszenie):
    Zestaw.objects.update(przeplyw_plik="")
    assert przeplywy.cykl() == {}
    Zestaw.objects.update(przeplyw_plik=PLIK)
    rok = zgloszenie.przydzial.zestaw.kurs.rok
    rok.poligon = True
    rok.save()
    assert przeplywy.cykl() == {}
    assert actions.uruchomienia == []


@pytest.mark.django_db
def test_zestaw_wymaga_kopiowania_actions_i_nazwy_pliku(kurs):
    from django.core.exceptions import ValidationError

    zestaw = Zestaw(kurs=kurs, nazwa="Zestaw 5", skrot="z05", repo_zrodlowe="wfais/z5", przeplyw_plik=PLIK)
    with pytest.raises(ValidationError, match="kopiowanie konfiguracji"):
        zestaw.full_clean()
    zestaw.kopiuj_actions = True
    zestaw.full_clean()
    zestaw.przeplyw_plik = ".github/workflows/traficar.yml"
    with pytest.raises(ValidationError, match="samą nazwę"):
        zestaw.full_clean()


@pytest.mark.django_db
def test_ustawienia_w_panelu(client, prowadzacy):
    odp = client.get("/panel/ustawienia/")
    assert "Przepływy GitHub Actions" in odp.content.decode()
    odp = client.post("/panel/ustawienia/przeplywy/", {"przeplywy_wlaczone": "on", "przeplywy_limit": "500"})
    assert odp.status_code == 302
    ustawienia = UstawieniaSerwisu.biezace()
    assert ustawienia.przeplywy_wlaczone and ustawienia.przeplywy_limit == 500


@pytest.mark.django_db
def test_karta_studenta_pokazuje_stan(client, prowadzacy, actions, wlaczone, zgloszenie):
    przeplywy.cykl()
    tresc = client.get(f"/panel/zgloszenia/{zgloszenie.pk}/").content.decode()
    assert "Przepływ GitHub Actions" in tresc
    assert "uruchamiany" in tresc


@pytest.mark.django_db
def test_formularz_zestawu_z_przeplywem(client, prowadzacy, github, kurs):
    dane = {"rodzaj": "zestaw", "nazwa": "Zestaw 5", "skrot": "z05", "repo_zrodlowe": "wfais/python-zestaw5",
            "wzorce_kodu": "*.py", "punkty_max": "5", "modele": "", "przeplyw_plik": PLIK}
    odp = client.post(f"/panel/kursy/{kurs.pk}/nowy-zestaw/", dane)
    assert odp.status_code == 200 and "kopiowanie konfiguracji" in odp.content.decode()
    odp = client.post(f"/panel/kursy/{kurs.pk}/nowy-zestaw/", {**dane, "kopiuj_actions": "on",
                                                              "przeplyw_co_minut": "30", "przeplyw_start": "zawsze"})
    assert odp.status_code == 302
    zestaw = Zestaw.objects.get(skrot="z05")
    assert (zestaw.przeplyw_plik, zestaw.przeplyw_co_minut, zestaw.przeplyw_start) == (PLIK, 30, "zawsze")
