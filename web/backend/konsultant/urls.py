from django.urls import include, path, re_path

from core.views_spa import spa

urlpatterns = [
    path("api/", include("core.urls")),
    # Всё остальное — React-приложение (собранное в web/frontend/dist): маршруты /inbox, /docs… обрабатывает фронтенд
    re_path(r"^(?!api/).*$", spa),
]
