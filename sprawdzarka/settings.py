"""Ustawienia projektu Sprawdzarka.

Wszystkie wartosci zalezne od srodowiska czytane sa ze zmiennych
srodowiskowych lub z pliku .env w katalogu glownym projektu.
"""
import os
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(nazwa, domyslna=""):
    return os.environ.get(nazwa, domyslna).strip()


def _env_bool(nazwa, domyslna=False):
    wartosc = os.environ.get(nazwa)
    if wartosc is None or not wartosc.strip():
        return domyslna
    return wartosc.strip().lower() in ("1", "true", "tak", "yes", "on")


# Uruchomienie pod pytest (testy nie wymagaja pliku .env)
TESTY = "pytest" in sys.modules

DEBUG = _env_bool("DJANGO_DEBUG", False)
_KLUCZ_DEWELOPERSKI = "dev-tylko-lokalnie-ustaw-DJANGO_SECRET_KEY"
SECRET_KEY = _env("DJANGO_SECRET_KEY") or _KLUCZ_DEWELOPERSKI
if SECRET_KEY == _KLUCZ_DEWELOPERSKI and not (DEBUG or TESTY):
    raise ImproperlyConfigured(
        "Ustaw DJANGO_SECRET_KEY w pliku .env (lokalnie mozna tez ustawic DJANGO_DEBUG=1)."
    )

ALLOWED_HOSTS = [h.strip() for h in _env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
# Adresy lokalne zawsze dozwolone (kontrola stanu kontenera pod /zdrowie/)
ALLOWED_HOSTS += [h for h in ("localhost", "127.0.0.1") if h not in ALLOWED_HOSTS]
PUBLICZNY_ADRES = _env("PUBLICZNY_ADRES", "http://localhost:8000").rstrip("/")
if urlparse(PUBLICZNY_ADRES).hostname and urlparse(PUBLICZNY_ADRES).hostname not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(urlparse(PUBLICZNY_ADRES).hostname)
CSRF_TRUSTED_ORIGINS = [PUBLICZNY_ADRES]

# Katalog na dane trwale: baza SQLite, przeslane pliki PDF, klucz aplikacji GitHub
DANE_DIR = Path(_env("SPRAWDZARKA_DANE") or BASE_DIR / "dane")
DANE_DIR.mkdir(parents=True, exist_ok=True)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "kurs",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "sprawdzarka.urls"
WSGI_APPLICATION = "sprawdzarka.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


def _baza_danych():
    adres = _env("DATABASE_URL")
    if not adres:
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": DANE_DIR / "baza.sqlite3",
            "OPTIONS": {
                "timeout": 30,
                "transaction_mode": "IMMEDIATE",
                "init_command": "PRAGMA journal_mode=WAL;",
            },
        }
    czesci = urlparse(adres)
    if czesci.scheme not in ("postgres", "postgresql"):
        raise ImproperlyConfigured("DATABASE_URL musi zaczynac sie od postgres:// (albo byc puste dla SQLite).")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": czesci.path.lstrip("/"),
        "USER": unquote(czesci.username or ""),
        "PASSWORD": unquote(czesci.password or ""),
        "HOST": czesci.hostname or "",
        "PORT": str(czesci.port or ""),
        "CONN_MAX_AGE": 60,
    }


DATABASES = {"default": _baza_danych()}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "pl"
TIME_ZONE = "Europe/Warsaw"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "static_zebrane"
STATIC_ROOT.mkdir(exist_ok=True)
MEDIA_ROOT = DANE_DIR / "pliki"
MEDIA_URL = "/pliki/"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

LOGIN_URL = "panel:logowanie"
LOGIN_REDIRECT_URL = "panel:pulpit"
LOGOUT_REDIRECT_URL = "panel:logowanie"

# Za odwrotnym proxy (Caddy) z HTTPS
if PUBLICZNY_ADRES.startswith("https://") and not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"prosty": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"konsola": {"class": "logging.StreamHandler", "formatter": "prosty"}},
    "root": {"handlers": ["konsola"], "level": "INFO"},
    "loggers": {"django": {"handlers": ["konsola"], "level": "WARNING", "propagate": False}},
}

# --- Aplikacja GitHub ---
GITHUB_ORG = _env("GITHUB_ORG", "wfais")
GITHUB_APP_ID = _env("GITHUB_APP_ID")
GITHUB_APP_CLIENT_ID = _env("GITHUB_APP_CLIENT_ID")
GITHUB_APP_CLIENT_SECRET = _env("GITHUB_APP_CLIENT_SECRET")
GITHUB_APP_PRIVATE_KEY_PATH = _env("GITHUB_APP_PRIVATE_KEY_PATH") or str(DANE_DIR / "github-app.pem")
if not Path(GITHUB_APP_PRIVATE_KEY_PATH).is_absolute():
    GITHUB_APP_PRIVATE_KEY_PATH = str(BASE_DIR / GITHUB_APP_PRIVATE_KEY_PATH)
# Alternatywa dla pliku: tresc klucza w zmiennej srodowiskowej
GITHUB_APP_PRIVATE_KEY = _env("GITHUB_APP_PRIVATE_KEY")
GITHUB_WEBHOOK_SECRET = _env("GITHUB_WEBHOOK_SECRET")
GITHUB_INSTALLATION_ID = _env("GITHUB_INSTALLATION_ID")
GITHUB_API_URL = _env("GITHUB_API_URL", "https://api.github.com").rstrip("/")
GITHUB_URL = _env("GITHUB_URL", "https://github.com").rstrip("/")

# --- Zasady kursu ---
SPRAWDZARKA_GALAZ = "main"
SPRAWDZARKA_GALAZ_FEEDBACK = "feedback"
SPRAWDZARKA_MNOZNIK_SPOZNIENIA = Decimal("0.8")
SPRAWDZARKA_GIT_AUTOR = "Sprawdzarka"
SPRAWDZARKA_GIT_EMAIL = "sprawdzarka@users.noreply.github.com"

# --- Ocena automatyczna: testy ---
# lokalnie  - pytest uruchamiany przez robota na tym samym koncie; kod studenta ma wtedy dostep
#             do plikow (np. .env, klucz aplikacji GitHub) i sieci: tylko do prob na wlasnych przykladach!
# kolejka   - pytest uruchamiany przez osobny proces bez dostepu do sieci i kluczy (serwer, docker-compose)
# wylaczone - bez uruchamiania testow
SPRAWDZARKA_TESTY = _env("SPRAWDZARKA_TESTY", "wylaczone")
SPRAWDZARKA_KOLEJKA = Path(_env("SPRAWDZARKA_KOLEJKA") or DANE_DIR / "kolejka")
SPRAWDZARKA_LIMIT_TESTOW = int(_env("SPRAWDZARKA_LIMIT_TESTOW", "120"))

# --- Ocena automatyczna: modele jezykowe ---
# Lista modeli w postaci dostawca:nazwa, oddzielona przecinkami. Dostawcy: anthropic, openai, xai, openrouter, demo
SPRAWDZARKA_MODELE = [m.strip() for m in _env("OCENIAJACE_MODELE").split(",") if m.strip()]
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
OPENAI_API_KEY = _env("OPENAI_API_KEY")
XAI_API_KEY = _env("XAI_API_KEY")
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
ANTHROPIC_API_URL = _env("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages")
OPENAI_API_URL = _env("OPENAI_API_URL", "https://api.openai.com/v1/chat/completions")
XAI_API_URL = _env("XAI_API_URL", "https://api.x.ai/v1/chat/completions")
OPENROUTER_API_URL = _env("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions")
SPRAWDZARKA_LIMIT_ODPOWIEDZI = int(_env("SPRAWDZARKA_LIMIT_ODPOWIEDZI", "8000"))
SPRAWDZARKA_LIMIT_KODU = int(_env("SPRAWDZARKA_LIMIT_KODU", "150000"))

# Punkty przyznawane z dokladnoscia do kroku; spor, gdy rozrzut ocen przekracza tolerancje
SPRAWDZARKA_KROK_PUNKTOW = Decimal(_env("SPRAWDZARKA_KROK_PUNKTOW", "0.25"))
SPRAWDZARKA_TOLERANCJA_BEZWZGLEDNA = Decimal(_env("SPRAWDZARKA_TOLERANCJA_BEZWZGLEDNA", "0.25"))
SPRAWDZARKA_TOLERANCJA_WZGLEDNA = Decimal(_env("SPRAWDZARKA_TOLERANCJA_WZGLEDNA", "0.2"))
SPRAWDZARKA_ZASADY_OGOLNE = _env("SPRAWDZARKA_ZASADY_OGOLNE") or (
    "Oceniasz rozwiązania studentów laboratorium z programowania w Pythonie. "
    "Oceniaj zgodność z treścią zadania i kryteriami, poprawność działania oraz sensowność rozwiązania. "
    "Nie obniżaj oceny za drobiazgi stylistyczne, jeśli kryteria tego nie wymagają. "
    "Jeżeli zadanie ma testy automatyczne, ich wynik jest wiarygodny i rozstrzyga o poprawności działania; "
    "punkty za działanie przyznawaj proporcjonalnie do zaliczonych testów, chyba że kryteria mówią inaczej. "
    "Brak rozwiązania (pusty plik, niezmieniony szkielet) to 0 punktów."
)

# Ocena uruchamiana z panelu startuje od razu w watku serwera (robot i tak przejmie kolejke)
SPRAWDZARKA_OCENA_W_WATKU = _env_bool("SPRAWDZARKA_OCENA_W_WATKU", True)
# Ile prac oceniac jednoczesnie (kazda praca to rownolegle zapytania do wszystkich modeli)
SPRAWDZARKA_ROWNOLEGLE_OCENY = int(_env("SPRAWDZARKA_ROWNOLEGLE_OCENY", "3"))
