// Kopiowanie do schowka, potwierdzanie operacji i blokada podwojnego wyslania formularzy
document.addEventListener("click", function (zdarzenie) {
  var przycisk = zdarzenie.target.closest("[data-kopiuj]");
  if (!przycisk) return;
  var pole = document.getElementById(przycisk.getAttribute("data-kopiuj"));
  if (!pole) return;
  var tekst = pole.value || pole.textContent;
  var gotowe = function () {
    var stary = przycisk.textContent;
    przycisk.textContent = "Skopiowano";
    setTimeout(function () { przycisk.textContent = stary; }, 1500);
  };
  if (navigator.clipboard) {
    navigator.clipboard.writeText(tekst).then(gotowe, function () { pole.select && pole.select(); });
  } else if (pole.select) {
    pole.select();
    document.execCommand("copy");
    gotowe();
  }
});

document.addEventListener("submit", function (zdarzenie) {
  var formularz = zdarzenie.target;
  var pytanie = formularz.getAttribute("data-potwierdz");
  if (pytanie && !window.confirm(pytanie)) {
    zdarzenie.preventDefault();
    return;
  }
  var dzisiejsze = terminyNaDzisiaj(formularz);
  if (dzisiejsze && !window.confirm(dzisiejsze)) {
    zdarzenie.preventDefault();
    return;
  }
  var przycisk = formularz.querySelector("[data-czekaj]");
  if (przycisk) {
    setTimeout(function () {
      przycisk.disabled = true;
      przycisk.textContent = przycisk.getAttribute("data-czekaj");
    }, 0);
  }
});

document.addEventListener("change", function (zdarzenie) {
  var nazwa = zdarzenie.target.getAttribute("data-zaznacz-wszystkie");
  if (!nazwa) return;
  // Nazwa pola albo (z kropka na poczatku) klasa pol
  var wybor = nazwa.charAt(0) === "." ? "input[type=checkbox]" + nazwa : 'input[type=checkbox][name="' + nazwa + '"]';
  document.querySelectorAll(wybor).forEach(function (pole) {
    pole.checked = zdarzenie.target.checked;
  });
});

document.addEventListener("click", function (zdarzenie) {
  var wiersz = zdarzenie.target.closest("tr[data-href]");
  if (!wiersz || zdarzenie.target.closest("a, button, form, input")) return;
  window.location = wiersz.getAttribute("data-href");
});

// Termin oddania ustawiony wlasnie na dzisiaj: pytanie przed zapisem (pole zmienione i data dzisiejsza)
function terminyNaDzisiaj(formularz) {
  var d = new Date();
  var dwa = function (n) { return (n < 10 ? "0" : "") + n; };
  var dzis = d.getFullYear() + "-" + dwa(d.getMonth() + 1) + "-" + dwa(d.getDate());
  var opisy = [];
  formularz.querySelectorAll('input[name$="termin_miekki"], input[name$="termin_twardy"]').forEach(function (pole) {
    if (pole.disabled || !pole.value || pole.value === pole.defaultValue || pole.value.slice(0, 10) !== dzis) return;
    var wiersz = pole.closest("tr");
    var wybor = wiersz && wiersz.querySelector('input[type=checkbox][name$="wybrana"]');
    if (wybor && !wybor.checked) return;  // grupa niezaznaczona przy kopiowaniu
    var grupa = wiersz && wiersz.querySelector("label");
    var rodzaj = /termin_twardy$/.test(pole.name) ? "termin ostateczny" : "termin";
    opisy.push("- " + (grupa ? grupa.textContent.trim() + ", " : "") + rodzaj + ", godz. " + pole.value.slice(11, 16));
  });
  if (!opisy.length) return "";
  return "Termin oddania przypada na dzisiaj (" + dwa(d.getDate()) + "." + dwa(d.getMonth() + 1) + "):\n"
    + opisy.join("\n") + "\n\nCzy na pewno?";
}

// Terminy: "godzina 23:59" przy terminie i "tydzien pozniej" przy terminie ostatecznym
(function () {
  var KLUCZ = "sprawdzarka.godzina2359";

  function dwa(n) { return (n < 10 ? "0" : "") + n; }

  function zapamietane() {
    try { return window.localStorage.getItem(KLUCZ) !== "0"; } catch (e) { return true; }
  }

  function zapamietaj(wartosc) {
    try { window.localStorage.setItem(KLUCZ, wartosc ? "1" : "0"); } catch (e) { /* bez pamieci przegladarki */ }
  }

  function zGodzina2359(wartosc) {
    return wartosc ? wartosc.slice(0, 10) + "T23:59" : wartosc;
  }

  function dzisiaj2359() {
    var d = new Date();
    return d.getFullYear() + "-" + dwa(d.getMonth() + 1) + "-" + dwa(d.getDate()) + "T23:59";
  }

  function tydzienPozniej(wartosc) {
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(wartosc || "");
    if (!m) return "";
    // Czas lokalny: przy zmianie czasu letniego godzina zostaje ta sama
    var d = new Date(+m[1], +m[2] - 1, +m[3] + 7, +m[4], +m[5]);
    return d.getFullYear() + "-" + dwa(d.getMonth() + 1) + "-" + dwa(d.getDate()) + "T" + dwa(d.getHours()) + ":" + dwa(d.getMinutes());
  }

  function wTabeli(pole) { return !!pole.closest("td"); }

  function zmienione(pole) {
    pole.dispatchEvent(new Event("change", { bubbles: true }));
  }

  document.querySelectorAll('input[type="datetime-local"][name$="termin_miekki"]').forEach(function (pole) {
    var etykieta = document.createElement("label");
    etykieta.className = "pomoc-terminu";
    etykieta.title = "Po wybraniu dnia godzina ustawia się sama na 23:59";
    var wybor = document.createElement("input");
    wybor.type = "checkbox";
    wybor.checked = zapamietane();
    etykieta.appendChild(wybor);
    etykieta.appendChild(document.createTextNode(" godzina 23:59"));
    pole.insertAdjacentElement("afterend", etykieta);

    pole.addEventListener("focus", function () {
      if (wybor.checked && !pole.value) pole.value = dzisiaj2359();
    });
    pole.addEventListener("change", function () {
      if (wybor.checked && pole.value && pole.value.slice(11, 16) !== "23:59") pole.value = zGodzina2359(pole.value);
    });
    wybor.addEventListener("change", function () {
      zapamietaj(wybor.checked);
      if (wybor.checked && pole.value) {
        pole.value = zGodzina2359(pole.value);
        zmienione(pole);
      }
    });
  });

  document.querySelectorAll('input[type="datetime-local"][name$="termin_twardy"]').forEach(function (pole) {
    var nazwa = pole.name.replace(/termin_twardy$/, "termin_miekki");
    var miekki = pole.form && pole.form.querySelector('input[name="' + nazwa + '"]');
    if (!miekki) return;
    var przycisk = document.createElement("button");
    przycisk.type = "button";
    przycisk.className = "maly pomoc-terminu";
    przycisk.textContent = wTabeli(pole) ? "+7 dni" : "tydzień później";
    przycisk.title = "Termin ostateczny: dokładnie tydzień po terminie";
    pole.insertAdjacentElement("afterend", przycisk);
    przycisk.addEventListener("click", function () {
      var wartosc = tydzienPozniej(miekki.value);
      if (!wartosc) {
        miekki.focus();
        return;
      }
      pole.value = wartosc;
      zmienione(pole);
    });
  });
})();

// Studenci: "pokaż" na poziomie uczelni, roku albo przedmiotu rozwija wszystkie jego grupy, "ukryj" zwija
(function () {
  var przyciski = document.querySelectorAll("[data-przelacz-grupy]");
  if (!przyciski.length) return;

  function grupy(przycisk) {
    var zakres = przycisk.closest(".zakres-przelacznika");
    return zakres ? Array.prototype.slice.call(zakres.querySelectorAll("details.grupa-studentow")) : [];
  }

  function wszystkieOtwarte(lista) {
    return lista.length > 0 && lista.every(function (d) { return d.open; });
  }

  function odswiez() {
    przyciski.forEach(function (przycisk) {
      var otwarte = wszystkieOtwarte(grupy(przycisk));
      przycisk.textContent = otwarte ? "ukryj" : "pokaż";
      przycisk.setAttribute("aria-expanded", otwarte ? "true" : "false");
    });
  }

  przyciski.forEach(function (przycisk) {
    przycisk.addEventListener("click", function () {
      var lista = grupy(przycisk);
      var otworz = !wszystkieOtwarte(lista);
      lista.forEach(function (d) { d.open = otworz; });
      odswiez();
    });
  });
  // Zdarzenie toggle nie przechodzi w gore drzewa, wiec nasluch w fazie przechwytywania
  document.addEventListener("toggle", odswiez, true);
  odswiez();
})();
