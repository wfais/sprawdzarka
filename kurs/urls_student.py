from django.urls import path

from . import widoki_student as w

app_name = "student"

urlpatterns = [
    path("z/<str:token>/", w.zestaw, name="zestaw"),
    path("auth/github/", w.logowanie, name="logowanie"),
    path("auth/github/powrot/", w.powrot, name="powrot"),
    path("auth/wyloguj/", w.wyloguj, name="wyloguj"),
]
