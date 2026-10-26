# Aplikacja GitHub dla Sprawdzarki

Sprawdzarka działa w organizacji **wfais** jako *aplikacja GitHub* (konto-automat). Aplikacja:

- tworzy prywatne repozytoria (startowe grup i repozytoria studentów),
- wysyła studentom zaproszenia do ich repozytoriów,
- zakłada pull request „Feedback” i zamyka go z oceną,
- dostaje od GitHuba powiadomienia o każdym wypchnięciu (`git push`),
- pozwala studentom zalogować się kontem GitHub na stronie akceptacji zestawu.

Klucze aplikacji trafiają wyłącznie do pliku `.env` i katalogu `dane/`, nigdy do repozytorium.

## 1. Ustawienia organizacji

W `https://github.com/organizations/wfais/settings/member_privileges`:

1. **Base permissions**: `No permission`. Dzięki temu studenci widzą tylko własne repozytoria.
2. Zaproszenia współpracowników z zewnątrz (*outside collaborators*) muszą być dozwolone dla administratorów repozytoriów
   (w niektórych organizacjach tylko właściciele mogą zapraszać; wtedy aplikacja nie zaprosi studenta).

## 2. Utworzenie aplikacji

Otwórz `https://github.com/organizations/wfais/settings/apps/new` i wypełnij:

| Pole | Wartość |
|---|---|
| GitHub App name | np. `Sprawdzarka WFAIS` (nazwa musi być unikalna w całym GitHubie) |
| Homepage URL | adres serwisu, np. `https://sprawdzarka.twoja-domena.pl` |
| Callback URL | `PUBLICZNY_ADRES/auth/github/powrot/`, np. `https://sprawdzarka.twoja-domena.pl/auth/github/powrot/` |
| Expire user authorization tokens | zaznaczone (domyślnie) |
| Request user authorization (OAuth) during installation | **niezaznaczone** |
| Enable Device Flow | niezaznaczone |
| Webhook: Active | zaznaczone |
| Webhook URL | `PUBLICZNY_ADRES/github/webhook/` |
| Webhook secret | długi losowy ciąg (ten sam wpiszesz w `.env` jako `GITHUB_WEBHOOK_SECRET`) |

Do testów na własnym komputerze możesz dodać drugi adres powrotu: `http://localhost:8000/auth/github/powrot/`
(GitHub pozwala na kilka *Callback URL*). Webhook nie dotrze na adres lokalny, ale nie jest konieczny:
robot co kilka minut sam pobiera historię wypchnięć z GitHuba.

### Uprawnienia (Permissions)

**Repository permissions:**

| Uprawnienie | Poziom | Do czego |
|---|---|---|
| Administration | Read and write | tworzenie repozytoriów, zapraszanie studentów, archiwizacja |
| Contents | Read and write | wypchnięcie stanu startowego, pobieranie kodu do oceny |
| Pull requests | Read and write | pull request „Feedback”, uwagi do linii kodu |
| Issues | Read and write | komentarz z oceną w pull requeście |
| Metadata | Read-only | wymagane zawsze (ustawia się samo) |
| Workflows | Read and write | **tylko** jeśli któryś zestaw ma włączone „kopiuj konfigurację GitHub Actions” |

**Organization permissions** i **Account permissions**: bez zmian (brak dostępu).

### Zdarzenia (Subscribe to events)

Zaznacz **Push**.

### Gdzie można zainstalować

`Any account` (aplikacja publiczna). Aplikacji prywatnej (`Only on this account`) nie mogą autoryzować osoby
spoza organizacji, więc studenci dostaliby stronę 404 przy logowaniu. Publiczność aplikacji nie daje nikomu
dostępu do wfais: Sprawdzarka używa wyłącznie instalacji w organizacji wfais.
Istniejącą aplikację prywatną zmienisz w ustawieniach aplikacji: Advanced, Danger zone, **Make public**.

Kliknij **Create GitHub App**.

## 3. Klucze aplikacji

Na stronie utworzonej aplikacji:

1. Zapisz **App ID** i **Client ID**.
2. **Generate a new client secret**: skopiuj sekret (widoczny tylko raz).
3. **Generate a private key**: pobierze się plik `.pem`. Zapisz go jako `dane/github-app.pem` w katalogu Sprawdzarki.

## 4. Instalacja w organizacji

Na stronie aplikacji: **Install App**, wybierz **wfais**, opcja **All repositories**.
Dostęp do wszystkich repozytoriów jest potrzebny, bo aplikacja tworzy nowe repozytoria i czyta Twoje repozytoria źródłowe zestawów.

## 5. Plik `.env`

```
GITHUB_ORG=wfais
GITHUB_APP_ID=123456
GITHUB_APP_CLIENT_ID=Iv23...
GITHUB_APP_CLIENT_SECRET=...
GITHUB_APP_PRIVATE_KEY_PATH=dane/github-app.pem
GITHUB_WEBHOOK_SECRET=...
PUBLICZNY_ADRES=https://sprawdzarka.twoja-domena.pl
```

## 6. Sprawdzenie

1. W zakładce **Advanced** aplikacji GitHub widać dostarczenie zdarzenia `ping`; po starcie serwisu ponów je (Redeliver), odpowiedź powinna mieć kod 200.
2. W panelu Sprawdzarki dodaj grupę i zestaw, wydaj go grupie: w organizacji powinno pojawić się repozytorium `py-...-start`.
3. Otwórz link dla studentów w oknie prywatnym przeglądarki, zaloguj się drugim (testowym) kontem GitHub i zaakceptuj zestaw.
