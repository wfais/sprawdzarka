# Sprawdzarka w Oracle Cloud (ARM, darmowy plan): instalacja próbna

Maszyna `VM.Standard.A1.Flex` (procesor ARM), Ubuntu 26.04, logowanie jako `ubuntu` z `sudo`.
Wszystkie składniki Sprawdzarki (Python, PostgreSQL, Caddy, biblioteki) mają wersje na ARM.

Instalacja próbna nie wymaga domeny, otwierania portów ani aplikacji GitHub: panel otwierasz przez tunel SSH,
a ćwiczysz na poligonie (dane fikcyjne, bez GitHuba) z modelami na niby albo prawdziwymi kluczami.

## 1. Logowanie i plik wymiany

Na swoim komputerze (Git Bash):

```bash
ssh ubuntu@130.61.129.125
```

Na serwerze (plik wymiany 4 GB, używany tylko w razie braku pamięci):

```bash
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

## 2. Docker

```bash
apt update && apt upgrade -y
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

Ostatnie polecenie powinno wypisać „Hello from Docker!”. Na Oracle nie włączaj `ufw`: zaporę tworzą tu reguły Oracle.

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

## 3. Kod i plik `.env`

```bash
git clone https://github.com/wfais/sprawdzarka.git /opt/sprawdzarka-test
mkdir -p /opt/sprawdzarka-test/dane
cd /opt/sprawdzarka-test
cat > .env <<EOF
DJANGO_SECRET_KEY=$(openssl rand -hex 32)
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
PUBLICZNY_ADRES=http://localhost:8100
POSTGRES_PASSWORD=$(openssl rand -hex 32)
ADRES_WEB=127.0.0.1
PORT_WEB=8000
PANEL_LOGOWANIE_HASLEM=1
OCENIAJACE_MODELE=demo:a,demo:b,demo:c
TESTER_CPU=$(nproc)
EOF
chmod 600 .env
```

`TESTER_CPU` przyjmuje liczbę rdzeni maszyny (przy jednym rdzeniu Docker nie uruchomi testera z limitem 2).
Prawdziwe modele: zamiast `demo:...` wpisz listę modeli i klucze tak jak na komputerze (`nano .env`).

## 4. Uruchomienie

```bash
docker compose up -d --build
docker compose ps
curl -s http://127.0.0.1:8000/zdrowie/
docker compose exec web python manage.py createsuperuser
```

Pierwsze budowanie na ARM trwa kilka minut. `docker compose ps` powinien pokazać `baza`, `web`, `robot` i `tester`
w stanie `running`, `curl` wypisać `ok`. Ostatnie polecenie zakłada konto do panelu (login i hasło).

## 5. Panel przez tunel SSH

Na swoim komputerze, w osobnym oknie Git Bash (okno zostaje otwarte, dopóki pracujesz):

```bash
ssh -N -L 8100:127.0.0.1:8000 ubuntu@130.61.129.125
```

W przeglądarce: `http://localhost:8100/panel/`, logowanie hasłem. Poligon tworzysz w Ustawieniach.
Port 8100 (a nie 8000) nie koliduje ze Sprawdzarką uruchomioną na komputerze.

## 6. Sprawdzenie po restarcie i zużycie zasobów

```bash
reboot
```

Po minucie tunel i panel działają jak wcześniej. Zużycie pamięci i procesora: `docker stats --no-stream`, `free -h`.

Oracle może zatrzymać darmową maszynę, gdy przez 7 dni procesor, sieć i pamięć są jednocześnie poniżej 20%.
Sprawdzarka zajmuje około 1,5 GB, czyli około 25% z 5,8 GB. Przy zmianie kształtu maszyny nie zwiększaj
pamięci do 12 GB (zajętość spadłaby do około 12%); rozsądnie: 2 OCPU i około 6 GB.

## Aktualizacja

```bash
cd /opt/sprawdzarka-test && git pull && docker compose up -d --build
```

## Później: pełna instancja z domeną

Do prawdziwego obiegu ze studentami potrzebne będą: subdomena wskazująca na 130.61.129.125 (rekord A),
osobna aplikacja GitHub ([aplikacja-github.md](aplikacja-github.md)), porty 80 i 443 otwarte w obu zaporach
(lista reguł w konsoli Oracle i iptables na maszynie) oraz brama HTTPS jak w [serwer.md](serwer.md), punkt 7.
