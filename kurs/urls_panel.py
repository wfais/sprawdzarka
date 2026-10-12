from django.contrib.auth import views as auth_views
from django.urls import path

from . import widoki_panel as w

app_name = "panel"

urlpatterns = [
    path("", w.pulpit, name="pulpit"),
    path("logowanie/", auth_views.LoginView.as_view(template_name="kurs/panel/logowanie.html"), name="logowanie"),
    path("wyloguj/", auth_views.LogoutView.as_view(), name="wyloguj"),
    path("grupy/", w.grupy, name="grupy"),
    path("grupy/<int:pk>/", w.grupa_edycja, name="grupa_edycja"),
    path("zestawy/", w.zestawy, name="zestawy"),
    path("zestawy/nowy/", w.zestaw_nowy, name="zestaw_nowy"),
    path("zestawy/<int:pk>/", w.zestaw, name="zestaw"),
    path("zestawy/<int:pk>/edycja/", w.zestaw_edycja, name="zestaw_edycja"),
    path("zestawy/<int:pk>/pdf/", w.zestaw_pdf, name="zestaw_pdf"),
    path("przydzialy/<int:pk>/", w.przydzial, name="przydzial"),
    path("przydzialy/<int:pk>/edycja/", w.przydzial_edycja, name="przydzial_edycja"),
    path("przydzialy/<int:pk>/repo-startowe/", w.przydzial_repo_startowe, name="przydzial_repo_startowe"),
    path("przydzialy/<int:pk>/odswiez/", w.przydzial_odswiez, name="przydzial_odswiez"),
    path("przydzialy/<int:pk>/repozytoria.txt", w.przydzial_lista_repo, name="przydzial_lista_repo"),
    path("zgloszenia/<int:pk>/", w.zgloszenie, name="zgloszenie"),
    path("zgloszenia/<int:pk>/termin/", w.zgloszenie_termin, name="zgloszenie_termin"),
    path("zgloszenia/<int:pk>/ocena/", w.zgloszenie_ocena, name="zgloszenie_ocena"),
    path("zgloszenia/<int:pk>/zeruj/", w.zgloszenie_zeruj, name="zgloszenie_zeruj"),
    path("zgloszenia/<int:pk>/ponow/", w.zgloszenie_ponow, name="zgloszenie_ponow"),
    path("zgloszenia/<int:pk>/odswiez/", w.zgloszenie_odswiez, name="zgloszenie_odswiez"),
    path("zgloszenia/<int:pk>/zaproszenie/", w.zgloszenie_zaproszenie, name="zgloszenie_zaproszenie"),
    path("zestawy/<int:pk>/zadania/", w.zestaw_zadania, name="zestaw_zadania"),
    path("zestawy/<int:pk>/zaproponuj/", w.zestaw_zaproponuj, name="zestaw_zaproponuj"),
    path("zestawy/<int:pk>/tekst-z-pdf/", w.zestaw_tekst_z_pdf, name="zestaw_tekst_z_pdf"),
    path("przydzialy/<int:pk>/ocen/", w.przydzial_ocen, name="przydzial_ocen"),
    path("zgloszenia/<int:pk>/ocen/", w.zgloszenie_ocen, name="zgloszenie_ocen"),
    path("ewaluacje/<int:pk>/zatwierdz/", w.ewaluacja_zatwierdz, name="ewaluacja_zatwierdz"),
    path("modele/", w.modele, name="modele"),
    path("modele/sprawdz/", w.modele_sprawdz, name="modele_sprawdz"),
    path("kalibracja/", w.kalibracja, name="kalibracja"),
    path("kalibracja/<int:pk>/", w.precedens_zmien, name="precedens_zmien"),
    path("studenci/", w.studenci, name="studenci"),
    path("studenci/<int:pk>/", w.student_nazwisko, name="student_nazwisko"),
]
