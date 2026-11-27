"""Отдача собранного React-приложения (web/frontend/dist): файлы из dist, всё остальное — index.html
(маршруты /inbox, /docs… обрабатывает фронтенд). Отдельный веб-сервер для статики не нужен."""
import mimetypes
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponse

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")


def spa(request):
    dist = Path(settings.FRONTEND_DIST).resolve()
    index = dist / "index.html"
    if not index.is_file():
        return HttpResponse("Фронтенд не собран: в папке web/frontend выполните «npm install» и «npm run build».",
                            status=503, content_type="text/plain; charset=utf-8")
    requested = (dist / request.path.lstrip("/")).resolve()
    if request.path != "/" and requested.is_file() and dist in requested.parents:
        response = FileResponse(open(requested, "rb"))
        if "/assets/" in request.path:  # имена файлов с хешем — можно кешировать надолго
            response["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
    response = FileResponse(open(index, "rb"), content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    return response
