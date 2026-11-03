# Sprawdzarka w Oracle Cloud (ARM, darmowy plan): instalacja próbna

Maszyna `VM.Standard.A1.Flex` (procesor ARM), Ubuntu 26.04, logowanie jako `ubuntu` z `sudo`.
Wszystkie składniki Sprawdzarki (Python, PostgreSQL, Caddy, biblioteki) mają wersje na ARM.

Instalacja próbna:

- adres `https://sprawdzarka.130-61-129-125.sslip.io` (usługa sslip.io kieruje taką nazwę na adres IP zawarty
  w nazwie, więc nie trzeba zakładać domeny),
- HTTPS daje Caddy, który już działa na maszynie (ten od notatek); dochodzi do niego jeden wpis,
- logowanie przez GitHub tylko dla kont wpisanych w `PANEL_KONTA_GITHUB`, każde z nich jest prowadzącym,
- osobna aplikacja GitHub wyłącznie do logowania: bez instalacji w wfais i bez klucza prywatnego,
  więc z tej instancji nie da się dotknąć repozytoriów studentów,
- ćwiczenia na poligonie (dane fikcyjne) z modelami na niby (`demo`).

Wszyscy zalogowani pracują na tych samych danych.

## 1. Plik wymiany (jeśli jeszcze go nie ma)

```bash
ssh ubuntu@130.61.129.125
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swap.conf && sudo sysctl --system
free -h
```

Dalej pracujesz jako `root`:

```bash
sudo -i
```

## 2. Docker (jeśli jeszcze go nie ma)

Instalacja jak w [serwer.md](serwer.md), punkt 3 (polecenia same wykrywają procesor ARM).
Na Oracle nie włączaj `ufw`: zaporę tworzą tu reguły Oracle.

Sprawdzenie, czy kontenery mają dostęp do internetu (potrzebny przy budowaniu obrazu):

```bash
docker run --rm alpine wget -qO /dev/null https://pypi.org && echo "siec w kontenerach: ok"
```

Gdyby zamiast „ok” pojawił się błąd, przyczyną jest zwykle domyślna reguła Oracle odrzucająca ruch przekazywany
(łańcuch FORWARD). Usunięcie jej i zapis reguł:

```bash
iptables -D FORWARD -j REJECT --reject-with icmp-host-prohibited
netfilter-persistent save
systemctl restart docker
```

## 3. Aplikacja GitHub do logowania

Na swoim koncie (nie w organizacji wfais): `https://github.com/settings/apps/new`

| Pole | Wartość |
|---|---|
| GitHub App name | np. `Sprawdzarka test WP` (nazwa musi być unikalna w całym GitHubie) |
| Homepage URL | `https://sprawdzarka.130-61-129-125.sslip.io` |
| Callback URL | `https://sprawdzarka.130-61-129-125.sslip.io/auth/github/powrot/` |
| Expire user authorization tokens | zaznaczone (domyślnie) |
| Request user authorization (OAuth) during installation | niezaznaczone |
| Webhook: Active | **odznaczone** |
| Permissions | bez zmian (żadnych uprawnień) |
| Where can this GitHub App be installed | Only on this account |

Po utworzeniu (Create GitHub App) zapisz **Client ID** i kliknij **Generate a new client secret** (sekret widać tylko
raz). Klucza prywatnego nie generuj i aplikacji nigdzie nie instaluj: do logowania nie jest to potrzebne.

## 4. Kod i plik `.env`

```bash
git clone https://github.com/wfais/sprawdzarka.git /opt/sprawdzarka-test
mkdir -p /opt/sprawdzarka-test/dane
cd /opt/sprawdzarka-test
cat > .env <<EOF
DJANGO_SECRET_KEY=$(openssl rand -hex 32)
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=sprawdzarka.130-61-129-125.sslip.io
PUBLICZNY_ADRES=https://sprawdzarka.130-61-129-125.sslip.io
POSTGRES_PASSWORD=$(openssl rand -hex 32)
ADRES_WEB=127.0.0.1
PORT_WEB=8000
GITHUB_APP_CLIENT_ID=
GITHUB_APP_CLIENT_SECRET=
PANEL_KONTA_GITHUB=witoldprzygoda
PANEL_LOGOWANIE_HASLEM=0
OCENIAJACE_MODELE=demo:a,demo:b,demo:c
TESTER_CPU=$(nproc)
EOF
chmod 600 .env
nano .env
```

W `nano` uzupełnij `GITHUB_APP_CLIENT_ID` i `GITHUB_APP_CLIENT_SECRET` z punktu 3
(zapis: Ctrl+O, Enter, wyjście: Ctrl+X).

## 5. Uruchomienie

```bash
docker compose up -d --build
docker compose ps
curl -s http://127.0.0.1:8000/zdrowie/
```

Pierwsze budowanie na ARM trwa kilka minut. `docker compose ps` powinien pokazać `baza`, `web`, `robot` i `tester`
w stanie `running`, `curl` wypisać `ok`.

## 6. Wpis w Caddy

Caddy od notatek musi działać w sieci serwera (wtedy widzi `127.0.0.1:8000`) i trzeba znaleźć jego katalog:

```bash
docker inspect python-notatki-caddy-1 --format '{{ .HostConfig.NetworkMode }}'
docker inspect python-notatki-caddy-1 --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
```

Pierwsze polecenie powinno wypisać `host`. Przejdź do katalogu z drugiego polecenia i dopisz na końcu jego `Caddyfile`:

```
sprawdzarka.130-61-129-125.sslip.io {
	encode gzip
	reverse_proxy 127.0.0.1:8000
}
```

Potem w tym samym katalogu:

```bash
docker compose restart
docker compose logs --tail 30
```

Po chwili w dzienniku: „certificate obtained successfully” dla nowej nazwy. Notatki działają jak wcześniej.

## 7. Logowanie

`https://sprawdzarka.130-61-129-125.sslip.io/panel/`, „Zaloguj przez GitHub”, przy pierwszym razie GitHub zapyta
o zgodę dla aplikacji z punktu 3. Poligon tworzysz w Ustawieniach.

Kolejni testujący: dopisz ich loginy GitHub po przecinku, np. `PANEL_KONTA_GITHUB=witoldprzygoda,login2,login3`,
i w `/opt/sprawdzarka-test` wykonaj `docker compose up -d`.

## 8. Restart i zużycie zasobów

```bash
reboot
```

Po minucie strona działa jak wcześniej. Zużycie: `free -h`, `docker stats --no-stream`.

Oracle może zatrzymać darmową maszynę, gdy przez 7 dni procesor, sieć i pamięć są jednocześnie poniżej 20%.
Sprawdzarka z systemem zajmuje około 1,5 GB: przy 6 GB to około 25%, przy 12 GB około 12%.

## Aktualizacja

```bash
cd /opt/sprawdzarka-test && git pull && docker compose up -d --build
```
