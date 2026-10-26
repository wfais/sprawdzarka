import json

import pytest

from kurs.models import Wypchniecie, Zgloszenie
from kurs.testy.conftest import podpisz, wyslij_webhook

PUSH = {
    "ref": "refs/heads/main",
    "before": "1" * 40,
    "after": "2" * 40,
    "deleted": False,
    "repository": {"id": 555, "full_name": "wfais/py-z03-g4-jan", "pushed_at": 1792000000},
    "pusher": {"name": "jan"},
    "sender": {"login": "jan", "id": 4242},
}


@pytest.mark.django_db
def test_zly_podpis_odrzucony(client):
    odp = client.post("/github/webhook/", data=json.dumps(PUSH), content_type="application/json",
                      HTTP_X_GITHUB_EVENT="push", HTTP_X_HUB_SIGNATURE_256="sha256=zly")
    assert odp.status_code == 403
    assert not Wypchniecie.objects.exists()


@pytest.mark.django_db
def test_ping(client):
    assert wyslij_webhook(client, {"zen": "x"}, zdarzenie="ping").status_code == 200


@pytest.mark.django_db
def test_push_zapisany_raz(client):
    assert wyslij_webhook(client, PUSH).status_code == 200
    assert wyslij_webhook(client, PUSH).status_code == 200
    assert Wypchniecie.objects.count() == 1
    w = Wypchniecie.objects.get()
    assert w.sha_po == "2" * 40
    assert w.czas.timestamp() == 1792000000


@pytest.mark.django_db
def test_push_oznacza_zgloszenie_do_przeliczenia(client, przydzial):
    from kurs.models import Student
    student = Student.objects.create(github_id=1, login="jan")
    z = Zgloszenie.objects.create(przydzial=przydzial, student=student, repo_id=555, do_przeliczenia=False,
                                  status=Zgloszenie.Status.GOTOWE)
    wyslij_webhook(client, PUSH)
    z.refresh_from_db()
    assert z.do_przeliczenia


@pytest.mark.django_db
def test_inne_zdarzenia_pomijane(client):
    assert wyslij_webhook(client, {"action": "opened"}, zdarzenie="issues").status_code == 202


def test_podpis_funkcja():
    from kurs.widoki_webhook import podpis_poprawny
    assert podpis_poprawny(b"abc", podpisz(b"abc"), "sekret-testowy")
    assert not podpis_poprawny(b"abc", podpisz(b"abcd"), "sekret-testowy")


@pytest.mark.django_db
def test_numer_konta_wypychajacego_zapisany(client):
    wyslij_webhook(client, PUSH)
    assert Wypchniecie.objects.get().autor_id == 4242
