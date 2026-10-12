import pytest
import responses
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from kurs.github import klient


@pytest.fixture
def aplikacja(settings, tmp_path):
    klucz = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = klucz.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
    sciezka = tmp_path / "app.pem"
    sciezka.write_bytes(pem)
    settings.GITHUB_APP_ID = "12345"
    settings.GITHUB_APP_PRIVATE_KEY = ""
    settings.GITHUB_APP_PRIVATE_KEY_PATH = str(sciezka)
    settings.GITHUB_INSTALLATION_ID = ""
    klient.wyczysc_pamiec()
    responses.get("https://api.github.com/orgs/wfais/installation", json={"id": 42})
    responses.post("https://api.github.com/app/installations/42/access_tokens",
                   json={"token": "ghs_test", "expires_at": "2099-01-01T00:00:00Z"}, status=201)
    return klucz


@responses.activate
def test_token_instalacji_pobierany_raz(aplikacja):
    assert klient.token_instalacji() == "ghs_test"
    assert klient.token_instalacji() == "ghs_test"
    tokeny = [c for c in responses.calls if c.request.url.endswith("/access_tokens")]
    assert len(tokeny) == 1
    autoryzacja = tokeny[0].request.headers["Authorization"]
    assert autoryzacja.startswith("Bearer ey")


@responses.activate
def test_utworz_repo_nowe(aplikacja):
    responses.post("https://api.github.com/orgs/wfais/repos", json={"id": 1, "full_name": "wfais/r"}, status=201)
    dane, nowe = klient.utworz_repo("r", "opis")
    assert nowe and dane["full_name"] == "wfais/r"
    wyslane = responses.calls[-1].request
    assert b'"private": true' in wyslane.body
    assert wyslane.headers["Authorization"] == "Bearer ghs_test"


@responses.activate
def test_utworz_repo_istniejace(aplikacja):
    responses.post("https://api.github.com/orgs/wfais/repos", status=422, json={
        "message": "Repository creation failed.",
        "errors": [{"resource": "Repository", "code": "custom", "field": "name",
                    "message": "name already exists on this account"}],
    })
    responses.get("https://api.github.com/repos/wfais/r", json={"id": 7, "full_name": "wfais/r"})
    dane, nowe = klient.utworz_repo("r")
    assert not nowe and dane["id"] == 7


@responses.activate
def test_blad_z_komunikatem(aplikacja):
    responses.put("https://api.github.com/repos/wfais/r/collaborators/jan", status=404, json={"message": "Not Found"})
    with pytest.raises(klient.BladGitHub) as blad:
        klient.zapros("wfais/r", "jan")
    assert blad.value.status == 404
    assert "Not Found" in str(blad.value)


@responses.activate
def test_ponawianie_po_bledzie_serwera(aplikacja, monkeypatch):
    monkeypatch.setattr(klient.time, "sleep", lambda s: None)
    responses.get("https://api.github.com/repos/wfais/r", status=502)
    responses.get("https://api.github.com/repos/wfais/r", json={"id": 7})
    assert klient.pobierz_repo("wfais/r")["id"] == 7


@responses.activate
def test_porownanie_drzew(aplikacja):
    responses.get("https://api.github.com/repos/wfais/r/git/trees/a", json={"tree": [
        {"path": "README.md", "type": "blob", "sha": "1"}, {"path": "stary.py", "type": "blob", "sha": "2"},
        {"path": "same.py", "type": "blob", "sha": "3"}, {"path": "kat", "type": "tree", "sha": "4"},
    ]})
    responses.get("https://api.github.com/repos/wfais/r/git/trees/b", json={"tree": [
        {"path": "README.md", "type": "blob", "sha": "9"}, {"path": "kat/nowy.py", "type": "blob", "sha": "2"},
        {"path": "same.py", "type": "blob", "sha": "3"},
    ]})
    assert klient.porownaj("wfais/r", "a", "b") == ["README.md", "kat/nowy.py", "stary.py"]
    # drzewa sa zapamietywane
    klient.porownaj("wfais/r", "a", "b")
    assert len([c for c in responses.calls if "/git/trees/" in c.request.url]) == 2


@responses.activate
def test_ochrona_galezi(aplikacja):
    responses.post("https://api.github.com/repos/wfais/r/rulesets", json={"id": 1}, status=201)
    klient.chron_galaz("wfais/r", "feedback")
    import json as _json
    wyslane = _json.loads(responses.calls[-1].request.body)
    assert wyslane["conditions"]["ref_name"]["include"] == ["refs/heads/feedback"]
    assert {r["type"] for r in wyslane["rules"]} == {"deletion", "non_fast_forward", "update"}


@responses.activate
def test_stronicowanie(aplikacja):
    responses.get("https://api.github.com/repos/wfais/r/activity", json=[{"id": 1}],
                  headers={"Link": '<https://api.github.com/repos/wfais/r/activity?after=X>; rel="next"'})
    responses.get("https://api.github.com/repos/wfais/r/activity?after=X", json=[{"id": 2}])
    assert [w["id"] for w in klient.aktywnosc("wfais/r", "main")] == [1, 2]


@responses.activate
def test_readme_brak(aplikacja):
    responses.get("https://api.github.com/repos/wfais/r/readme", status=404, json={"message": "Not Found"})
    assert klient.readme("wfais/r", "main") is None


@responses.activate
def test_pr_juz_istnieje(aplikacja):
    responses.post("https://api.github.com/repos/wfais/r/pulls", status=422,
                   json={"message": "Validation Failed", "errors": [{"message": "A pull request already exists"}]})
    responses.get("https://api.github.com/repos/wfais/r/pulls", json=[{"number": 3}])
    assert klient.utworz_pr("wfais/r", "main", "feedback", "Feedback", "") == 3


def test_brak_klucza(settings, tmp_path):
    settings.GITHUB_APP_ID = "1"
    settings.GITHUB_APP_PRIVATE_KEY = ""
    settings.GITHUB_APP_PRIVATE_KEY_PATH = str(tmp_path / "brak.pem")
    with pytest.raises(klient.BladGitHub):
        klient.jwt_aplikacji()
