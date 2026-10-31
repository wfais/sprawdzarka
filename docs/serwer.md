# Sprawdzarka na serwerze (Ubuntu, Docker)

Instrukcja dla serwera z Ubuntu 24.04 lub 26.04 (np. VPS Hostinger) z dostępem `root` przez SSH.

## Jak to działa

```
/opt/brama               wspólna brama HTTPS (Caddy), jedna na cały serwer, porty 80 i 443
/opt/sprawdzarka-wp      instancja Sprawdzarki (kod, .env, dane/), na 127.0.0.1:8001
/opt/sprawdzarka-xyz     kolejna instancja (np. innego prowadzącego), na 127.0.0.1:8002
```

- Każda instancja to osobny projekt Docker Compose: własna baza PostgreSQL, serwis WWW, robot i odizolowany tester.
  Instancje nie widzą nawzajem swoich danych.
- Brama przyjmuje ruch z internetu, sama zdobywa i odnawia certyfikaty HTTPS i kieruje każdą domenę do jej instancji.
- Wszystkie kontenery mają `restart: unless-stopped`, a Docker startuje razem z systemem, więc po restarcie maszyny
  wszystko podnosi się samo.
- Każda instancja potrzebuje własnej domeny (albo subdomeny) wskazującej na serwer i własnej aplikacji GitHub
  (instrukcja: [aplikacja-github.md](aplikacja-github.md)).
- Jedna instancja zajmuje około 1 GB pamięci (baza, serwis WWW, robot, tester z limitem 1 GB w czasie testów).

## 1. Logowanie na serwer

Na swoim komputerze (Git Bash):

```bash
ssh root@srv2095410.hstgr.cloud
```

## 2. Aktualizacja systemu i zapora

```bash
apt update && apt upgrade -y
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
```

Jeśli dostawca ma też własną zaporę w panelu VPS (Hostinger: ustawienia zapory, Firewall), muszą być w niej otwarte
porty 22, 80 i 443.
Porty instancji (8001, 8002...) są dostępne tylko z samego serwera (127.0.0.1), z zewnątrz nie.

## 3. Docker

```bash
apt install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker run --rm hello-world
```

Ostatnie polecenie powinno wypisać „Hello from Docker!”.

## 4. Kod instancji

```bash
git clone https://github.com/wfais/sprawdzarka.git /opt/sprawdzarka-wp
mkdir -p /opt/sprawdzarka-wp/dane
```

Klucz aplikacji GitHub skopiuj ze swojego komputera (Git Bash, w katalogu Sprawdzarki):

```bash
scp dane/github-app.pem root@srv2095410.hstgr.cloud:/opt/sprawdzarka-wp/dane/github-app.pem
```

## 5. Plik `.env` instancji

```bash
cd /opt/sprawdzarka-wp
cp .env.przyklad .env
nano .env
```

Wartości inne niż na komputerze:

```
DJANGO_SECRET_KEY=<nowy losowy ciąg>
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=srv2095410.hstgr.cloud
PUBLICZNY_ADRES=https://srv2095410.hstgr.cloud
GITHUB_WEBHOOK_SECRET=<nowy losowy ciąg, ten sam wpiszesz w aplikacji GitHub>
POSTGRES_PASSWORD=<nowy losowy ciąg>
ADRES_WEB=127.0.0.1
PORT_WEB=8001
PANEL_KONTA_GITHUB=<Twój login GitHub>
PANEL_LOGOWANIE_HASLEM=0
```

`GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_ORG` i klucze modeli są takie same jak
na komputerze. `DATABASE_URL` zostaw puste (instancja ma własną bazę PostgreSQL). Losowe ciągi wygenerujesz poleceniem
`openssl rand -hex 32` (każdy osobno). Zapis w nano: Ctrl+O, Enter, wyjście: Ctrl+X.

## 6. Uruchomienie instancji

```bash
cd /opt/sprawdzarka-wp
docker compose up -d --build
docker compose ps
curl -s http://127.0.0.1:8001/zdrowie/
```

Pierwsze budowanie trwa kilka minut. `docker compose ps` powinien pokazać `baza`, `web`, `robot` i `tester`
w stanie `running` (web: `healthy`), a ostatnie polecenie wypisać `ok`. Migracje bazy wykonują się same przy starcie.

## 7. Brama HTTPS (raz na serwer)

```bash
cp -r /opt/sprawdzarka-wp/docker/brama /opt/brama
cd /opt/brama
cp Caddyfile.przyklad Caddyfile
nano Caddyfile          # domena i port instancji (PORT_WEB)
docker compose up -d
docker compose logs -f  # po chwili: "certificate obtained successfully", wyjście: Ctrl+C
```

Domena musi już wskazywać na serwer (`srv2095410.hstgr.cloud` wskazuje na VPS od początku).

## 8. Aplikacja GitHub

W ustawieniach aplikacji (`https://github.com/organizations/wfais/settings/apps`, Edit):

- **Callback URL**: dodaj `https://srv2095410.hstgr.cloud/auth/github/powrot/` (adres `localhost` możesz zostawić do prób),
- **Homepage URL**: `https://srv2095410.hstgr.cloud`,
- **Webhook**: pole **Active** zaznaczone,
- **Webhook URL**: `https://srv2095410.hstgr.cloud/github/webhook/`,
- **Webhook secret**: ten sam ciąg co `GITHUB_WEBHOOK_SECRET` w `.env` serwera.

Zapisz (Save changes). Potem w zakładce **Permissions & events**, w sekcji **Subscribe to events**, zaznacz **Push**
i znów zapisz (ta sekcja pojawia się dopiero po włączeniu Active). Bez tego GitHub nie wysyła powiadomień, a panel
widzi nowe zmiany studentów dopiero po przebiegu robota (co 30 minut) albo po „Odśwież z GitHub”.
W zakładce **Advanced** aplikacji widać dostarczone powiadomienia i odpowiedź serwera (200 albo 202).

## 9. Sprawdzenie

1. `https://srv2095410.hstgr.cloud/panel/`, „Zaloguj przez GitHub”.
2. Restart maszyny: `reboot`, po minucie strona działa jak wcześniej.

Gdyby logowanie przez GitHub nie działało, awaryjnie: w `.env` `PANEL_LOGOWANIE_HASLEM=1`, potem
`docker compose up -d` i `docker compose exec web python manage.py createsuperuser`. Po naprawie wróć do `0`.

Od tej chwili pracujesz na serwerze. Na komputerze zostaje środowisko do prób (najlepiej na poligonie),
a linki dla studentów wysyłasz tylko z serwera. Rok z komputera przeniesiesz przyciskiem „Pobierz archiwum”
(strona roku) i „Wczytaj archiwum” (Struktura na serwerze); potem nie używaj go już na komputerze, żeby dwa roboty
nie obsługiwały tych samych repozytoriów.

## Aktualizacja do nowej wersji

```bash
cd /opt/sprawdzarka-wp
git pull
docker compose up -d --build
```

Zmiana samego `.env`: `docker compose up -d` (kontenery z nową konfiguracją uruchomią się od nowa).

## Kopie zapasowe

Baza i pliki zestawów (PDF) jednej instancji:

```bash
mkdir -p /root/kopie
cd /opt/sprawdzarka-wp
docker compose exec -T baza pg_dump -U sprawdzarka sprawdzarka | gzip > /root/kopie/sprawdzarka-wp-$(date +%F).sql.gz
tar czf /root/kopie/sprawdzarka-wp-dane-$(date +%F).tgz dane
```

Codziennie o 3:00 (polecenie `crontab -e`, dopisz jeden wiersz):

```
0 3 * * * cd /opt/sprawdzarka-wp && docker compose exec -T baza pg_dump -U sprawdzarka sprawdzarka | gzip > /root/kopie/sprawdzarka-wp-$(date +\%F).sql.gz && find /root/kopie -mtime +30 -delete
```

Kopie warto co jakiś czas pobrać na inny komputer (`scp`). Archiwum roku z panelu jest dodatkową kopią danych roku.

## Kolejna instancja (np. dla innego prowadzącego)

1. Domena albo subdomena wskazująca na serwer i osobna aplikacja GitHub w organizacji tego prowadzącego.
2. `git clone https://github.com/wfais/sprawdzarka.git /opt/sprawdzarka-xyz`, katalog `dane/` z kluczem aplikacji.
3. `.env` jak w kroku 5, z jego domeną, kluczami, `GITHUB_ORG` i **innym** `PORT_WEB` (8002, 8003...).
4. `docker compose up -d --build` w katalogu instancji.
5. W `/opt/brama/Caddyfile` nowy blok (domena i port), potem `docker compose restart` w `/opt/brama`.

## Przydatne polecenia

```bash
docker compose ps                    # stan kontenerów instancji
docker compose logs -f web robot     # dziennik serwisu i robota
docker compose restart               # ponowne uruchomienie instancji
docker compose exec web python manage.py <polecenie>   # np. poligon, createsuperuser
docker compose exec web python manage.py diagnoza <login>   # powiadomienia GitHub i wypchnięcia studenta
docker system df                     # miejsce zajęte przez Dockera
```
