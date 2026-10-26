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
