"""Dostep do panelu: logowanie przez GitHub dla wskazanych kont, blokada prob hasla."""
import pytest
import responses

from kurs.models import KontoProwadzacego, NieudaneLogowanie


@pytest.fixture
def konta(settings):
    settings.PANEL_KONTA_GITHUB = ["witek"]
    settings.PANEL_LOGOWANIE_HASLEM = True
    settings.PANEL_LIMIT_PROB = 3
    settings.PANEL_BLOKADA_MINUT = 15


def powrot_z_github(client, gh_id, login, nastepny="/panel/"):
    client.get(f"/panel/logowanie/github/?next={nastepny}")
    stan = client.session["gh_stan"]
    responses.post("https://github.com/login/oauth/access_token", json={"access_token": "ghu_x"})
    responses.get("https://api.github.com/user", json={"id": gh_id, "login": login, "name": "Witold P"})
    return client.get(f"/auth/github/powrot/?code=kod&state={stan}")


@pytest.mark.django_db
def test_strona_logowania_pokazuje_github(client, konta):
    strona = client.get("/panel/logowanie/?next=/panel/studenci/").content.decode()
    assert "Zaloguj przez GitHub" in strona and "/panel/logowanie/github/?next=/panel/studenci/" in strona
    assert "Logowanie hasłem" in strona


@pytest.mark.django_db
@responses.activate
def test_dozwolone_konto_wchodzi_do_panelu(client, konta):
    odp = client.get("/panel/logowanie/github/?next=/panel/studenci/")
    assert odp.status_code == 302 and odp["Location"].startswith("https://github.com/login/oauth/authorize")
    assert client.session["gh_cel"] == "panel"
    odp = powrot_z_github(client, 501, "Witek", nastepny="/panel/studenci/")
    assert odp.status_code == 302 and odp["Location"] == "/panel/studenci/"
    assert client.get("/panel/").status_code == 200
    konto = KontoProwadzacego.objects.get()
    assert konto.github_id == 501 and konto.uzytkownik.is_staff and not konto.uzytkownik.has_usable_password()
    assert "gh_uzytkownik" not in client.session


@pytest.mark.django_db
@responses.activate
def test_niedozwolone_konto_odrzucone(client, konta):
    odp = powrot_z_github(client, 502, "obcy")
    assert odp["Location"] == "/panel/logowanie/"
    assert "nie ma dostępu do panelu" in client.get("/panel/logowanie/").content.decode()
    assert client.get("/panel/").status_code == 302
    assert not KontoProwadzacego.objects.exists()


@pytest.mark.django_db
@responses.activate
def test_przejety_login_odrzucony(client, konta):
    powrot_z_github(client, 501, "witek")
    client.post("/panel/wyloguj/")
    odp = powrot_z_github(client, 999, "witek")
    assert odp["Location"] == "/panel/logowanie/"
    assert client.get("/panel/").status_code == 302


@pytest.mark.django_db
def test_inny_host_przenosi_pod_publiczny_adres(client, konta, settings):
    settings.ALLOWED_HOSTS = ["testserver", "127.0.0.1"]
    odp = client.get("/panel/logowanie/github/?next=/panel/", HTTP_HOST="127.0.0.1")
    assert odp.status_code == 302 and odp["Location"] == "http://testserver/panel/logowanie/github/?next=/panel/"
    assert "gh_stan" not in client.session
    odp = client.get("/auth/github/?next=/z/abc/", HTTP_HOST="127.0.0.1")
    assert odp["Location"] == "http://testserver/auth/github/?next=/z/abc/"


@pytest.mark.django_db
def test_niebezpieczny_adres_powrotu(client, konta):
    client.get("/panel/logowanie/github/?next=https://zly.example/")
    assert client.session["gh_nastepny"] == "/panel/"


@pytest.mark.django_db
def test_student_nadal_loguje_sie_jako_student(client, konta, przydzial):
    client.get(f"/auth/github/?next=/z/{przydzial.token}/")
    assert client.session["gh_cel"] == "student"


@pytest.mark.django_db
def test_blokada_po_nieudanych_probach(client, konta, django_user_model):
    django_user_model.objects.create_user("admin", password="dobre-haslo-123", is_staff=True)
    for _ in range(3):
        assert client.post("/panel/logowanie/", {"username": "admin", "password": "zle"}).status_code == 200
    assert NieudaneLogowanie.objects.count() == 3
    odp = client.post("/panel/logowanie/", {"username": "admin", "password": "dobre-haslo-123"}, follow=True)
    assert "Za dużo nieudanych prób" in odp.content.decode()
    assert client.get("/panel/").status_code == 302
    NieudaneLogowanie.objects.all().delete()
    odp = client.post("/panel/logowanie/", {"username": "admin", "password": "dobre-haslo-123"})
    assert odp.status_code == 302 and client.get("/panel/").status_code == 200


@pytest.mark.django_db
def test_blokada_wedlug_adresu(client, konta, django_user_model):
    django_user_model.objects.create_user("admin", password="dobre-haslo-123", is_staff=True)
    for i in range(3):
        client.post("/panel/logowanie/", {"username": f"ktos{i}", "password": "zle"})
    odp = client.post("/panel/logowanie/", {"username": "admin", "password": "dobre-haslo-123"}, follow=True)
    assert "Za dużo nieudanych prób" in odp.content.decode()


@pytest.mark.django_db
def test_haslo_wylaczone(client, konta, settings, django_user_model):
    settings.PANEL_LOGOWANIE_HASLEM = False
    django_user_model.objects.create_user("admin", password="dobre-haslo-123", is_staff=True)
    assert "Logowanie hasłem" not in client.get("/panel/logowanie/").content.decode()
    odp = client.post("/panel/logowanie/", {"username": "admin", "password": "dobre-haslo-123"}, follow=True)
    assert "Logowanie hasłem jest wyłączone" in odp.content.decode()
    assert client.get("/panel/").status_code == 302


@pytest.mark.django_db
def test_logowanie_panelu_django_przez_panel(client):
    odp = client.get("/admin/login/?next=/admin/")
    assert odp.status_code == 302 and odp["Location"] == "/panel/logowanie/?next=/admin/"


@pytest.mark.django_db
def test_administracja_pokazuje_dostep(client, konta, prowadzacy):
    strona = client.get("/panel/ustawienia/").content.decode()
    assert "Dostęp do panelu" in strona and "@witek" in strona and "jeszcze się nie logowało" in strona
