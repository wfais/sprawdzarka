"""Dostep do modeli jezykowych oceniajacych prace: Anthropic, OpenAI, xAI, OpenRouter oraz model demonstracyjny.

Model identyfikowany jest napisem dostawca:nazwa, np. anthropic:claude-sonnet-5-5
albo openrouter:anthropic/claude-sonnet-5-5. Lista dostepnych modeli: OCENIAJACE_MODELE w pliku .env.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import requests
from django.conf import settings

DOSTAWCY = ("anthropic", "openai", "xai", "openrouter", "demo")
LIMIT_CZASU = 300


class BladModelu(Exception):
    pass


@dataclass(frozen=True)
class Model:
    dostawca: str
    nazwa: str

    @property
    def identyfikator(self) -> str:
        return f"{self.dostawca}:{self.nazwa}"

    @classmethod
    def z_napisu(cls, napis: str) -> "Model":
        dostawca, _, nazwa = napis.strip().partition(":")
        dostawca = dostawca.strip().lower()
        if dostawca not in DOSTAWCY or not nazwa.strip():
            raise BladModelu(f"Niepoprawny model: {napis!r} (oczekiwano dostawca:nazwa, dostawcy: {', '.join(DOSTAWCY)})")
        return cls(dostawca, nazwa.strip())

    @property
    def klucz(self) -> str:
        return {
            "anthropic": settings.ANTHROPIC_API_KEY,
            "openai": settings.OPENAI_API_KEY,
            "xai": settings.XAI_API_KEY,
            "openrouter": settings.OPENROUTER_API_KEY,
            "demo": "demo",
        }[self.dostawca]

    @property
    def skonfigurowany(self) -> bool:
        return bool(self.klucz)


@dataclass
class Odpowiedz:
    tekst: str
    tokeny_we: int = 0
    tokeny_wy: int = 0
    czas_s: float = 0.0


def skonfigurowane() -> list[Model]:
    modele = []
    for napis in settings.SPRAWDZARKA_MODELE:
        try:
            modele.append(Model.z_napisu(napis))
        except BladModelu:
            continue
    return modele


def dla_zestawu(zestaw) -> list[Model]:
    """Modele wybrane dla zestawu (puste pole = wszystkie skonfigurowane z kluczem)."""
    dostepne = [m for m in skonfigurowane() if m.skonfigurowany]
    if not (zestaw.modele or "").strip():
        return dostepne
    wybrane = {n.strip() for n in zestaw.modele.split(",") if n.strip()}
    return [m for m in dostepne if m.identyfikator in wybrane]


def _wyslij(url: str, naglowki: dict, tresc: dict, proby: int = 3) -> dict:
    ostatni_blad = ""
    for proba in range(proby):
        try:
            odp = requests.post(url, headers=naglowki, json=tresc, timeout=LIMIT_CZASU)
        except requests.RequestException as blad:
            ostatni_blad = f"Brak połączenia: {blad}"
            time.sleep(2 + 3 * proba)
            continue
        if odp.status_code in (429, 500, 502, 503, 504, 529) and proba < proby - 1:
            ostatni_blad = f"HTTP {odp.status_code}"
            opoznienie = odp.headers.get("retry-after", "")
            time.sleep(min(int(opoznienie), 60) if opoznienie.isdigit() else 5 + 10 * proba)
            continue
        if odp.status_code >= 400:
            raise BladModelu(f"HTTP {odp.status_code}: {odp.text[:500]}")
        try:
            return odp.json()
        except ValueError as blad:
            raise BladModelu(f"Niepoprawna odpowiedź: {odp.text[:300]}") from blad
    raise BladModelu(ostatni_blad or "Nie udało się połączyć z modelem.")


def zapytaj(model: Model, system: str, tresc: str, max_tokeny: Optional[int] = None) -> Odpowiedz:
    """Wysyla polecenie do modelu i zwraca tekst odpowiedzi."""
    if not model.skonfigurowany:
        raise BladModelu(f"Brak klucza API dla dostawcy {model.dostawca}.")
    max_tokeny = max_tokeny or settings.SPRAWDZARKA_LIMIT_ODPOWIEDZI
    start = time.monotonic()
    if model.dostawca == "demo":
        raise BladModelu("Model demonstracyjny nie odpowiada na dowolne polecenia.")
    if model.dostawca == "anthropic":
        dane = _wyslij(
            settings.ANTHROPIC_API_URL,
            {"x-api-key": model.klucz, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            {"model": model.nazwa, "max_tokens": max_tokeny, "temperature": 0, "system": system,
             "messages": [{"role": "user", "content": tresc}]},
        )
        tekst = "".join(b.get("text", "") for b in dane.get("content", []) if b.get("type") == "text")
        uzycie = dane.get("usage", {})
        return Odpowiedz(tekst, uzycie.get("input_tokens", 0), uzycie.get("output_tokens", 0),
                         time.monotonic() - start)

    url = {"openai": settings.OPENAI_API_URL, "xai": settings.XAI_API_URL,
           "openrouter": settings.OPENROUTER_API_URL}[model.dostawca]
    naglowki = {"Authorization": f"Bearer {model.klucz}", "Content-Type": "application/json"}
    zapytanie = {
        "model": model.nazwa,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": tresc}],
    }
    if model.dostawca == "openai":
        # Nowsze modele OpenAI nie przyjmuja temperature ani max_tokens
        zapytanie["max_completion_tokens"] = max_tokeny
        zapytanie["response_format"] = {"type": "json_object"}
    else:
        zapytanie["max_tokens"] = max_tokeny
        zapytanie["temperature"] = 0
    if model.dostawca == "openrouter":
        naglowki["X-Title"] = "Sprawdzarka"
        naglowki["HTTP-Referer"] = settings.PUBLICZNY_ADRES
    dane = _wyslij(url, naglowki, zapytanie)
    wybory = dane.get("choices") or []
    if not wybory:
        raise BladModelu(f"Pusta odpowiedź modelu: {json.dumps(dane)[:300]}")
    tekst = wybory[0].get("message", {}).get("content") or ""
    if isinstance(tekst, list):
        tekst = "".join(c.get("text", "") for c in tekst if isinstance(c, dict))
    uzycie = dane.get("usage", {}) or {}
    return Odpowiedz(tekst, uzycie.get("prompt_tokens", 0), uzycie.get("completion_tokens", 0),
                     time.monotonic() - start)


def wyciagnij_json(tekst: str) -> dict:
    """Odczytuje obiekt JSON z odpowiedzi modelu (rowniez otoczony blokiem ```json lub komentarzem)."""
    tekst = (tekst or "").strip()
    blok = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", tekst, re.DOTALL)
    kandydaci = [blok.group(1)] if blok else []
    poczatek, koniec = tekst.find("{"), tekst.rfind("}")
    if poczatek != -1 and koniec > poczatek:
        kandydaci.append(tekst[poczatek:koniec + 1])
    for kandydat in kandydaci:
        try:
            wynik = json.loads(kandydat)
        except ValueError:
            continue
        if isinstance(wynik, dict):
            return wynik
    raise BladModelu("Odpowiedź modelu nie zawiera poprawnego obiektu JSON.")


def opinia_demo(model: Model, zadania: list[dict]) -> dict:
    """Deterministyczna ocena bez modelu jezykowego, do wyprobowania systemu bez kluczy API.

    Punkty wynikaja z testow; zadania bez testow dostaja polowe punktow z losowym (stalym) odchyleniem
    zaleznym od nazwy modelu, dzieki czemu da sie zobaczyc przypadki sporne.
    """
    wynik = []
    for zadanie in zadania:
        maks = Decimal(str(zadanie["punkty_max"]))
        if zadanie.get("testy_wszystkie"):
            punkty = maks * Decimal(zadanie["testy_zdane"]) / Decimal(zadanie["testy_wszystkie"])
            uzasadnienie = f"Zaliczone testy: {zadanie['testy_zdane']} z {zadanie['testy_wszystkie']}."
        elif zadanie.get("zmienione_pliki"):
            skrot = int(hashlib.sha256(f"{model.nazwa}{zadanie['kod']}".encode()).hexdigest(), 16)
            punkty = maks * Decimal(skrot % 5 + 3) / Decimal(10)
            uzasadnienie = "Ocena demonstracyjna: zadanie bez testów."
        else:
            punkty, uzasadnienie = Decimal(0), "Brak zmian w plikach zadania."
        wynik.append({
            "kod": zadanie["kod"], "punkty": float(punkty), "uzasadnienie": uzasadnienie,
            "komentarz": "ocena demonstracyjna", "uwagi": [],
        })
    return {"zadania": wynik, "podejrzane": ""}
