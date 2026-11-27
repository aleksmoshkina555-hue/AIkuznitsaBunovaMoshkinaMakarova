"""Запуск веб-версии ИИ-консультанта (двойной щелчок «Запустить_веб-версию.cmd» или ярлык).

1. Проверяет пакеты (Django, DRF, waitress и пакеты агента); нет — предлагает установить.
2. Запускает Ollama, если она не запущена (как запускатель прежней версии).
3. Готовит базу: миграции; при первом запуске — перенос данных прежней версии (учётные записи с прежними
   паролями, обращения, журналы, справочник).
4. Запускает веб-сервер waitress и открывает браузер. Маленькое окно «Сервер работает» — закройте его,
   чтобы остановить сервер.

Адрес по умолчанию — только этот компьютер: http://127.0.0.1:8000. Если порт 8000 занят другой программой,
берётся следующий свободный (8001, 8002 …) — адрес показан в окне «Сервер работает». Для доступа из сети:
WEB_HOST=0.0.0.0 и WEB_ALLOWED_HOSTS=<имя или адрес компьютера> (см. README.md в папке web).
"""
import importlib.util
import io
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Ярлык запускает через pythonw — без консоли sys.stdout/sys.stderr равны None, и любая печать
# (например, итог переноса данных в import_legacy) роняла первый запуск
for _stream in ("stdout", "stderr"):
    if getattr(sys, _stream) is None:
        setattr(sys, _stream, open(os.devnull, "w", encoding="utf-8"))

WEB = Path(__file__).resolve().parent
ROOT = WEB.parent
BACKEND = WEB / "backend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "konsultant.settings")

HOST = os.getenv("WEB_HOST", "127.0.0.1")
PORT = int(os.getenv("WEB_PORT", "8000"))
RESTART = os.getenv("WEB_WAIT_PORT_FREE") == "1"   # запущен после обновления, отката или «Перезапустить»
NO_BROWSER = os.getenv("WEB_NO_BROWSER") == "1"
PACKAGES = {"django": "django", "rest_framework": "djangorestframework", "waitress": "waitress", "openai": "openai",
            "docx": "python-docx", "pypdf": "pypdf", "pymorphy3": "pymorphy3"}


def missing_packages() -> list[str]:
    return [pip for module, pip in PACKAGES.items() if importlib.util.find_spec(module) is None]


def prepare_database() -> str:
    """Миграции; если в базе ещё нет учётных записей, а прежняя версия есть — перенос данных."""
    import django
    django.setup()
    from django.contrib.auth.models import User
    from django.core.management import call_command
    quiet = io.StringIO()  # вывод команд не нужен: окна консоли нет
    call_command("migrate", verbosity=0, stdout=quiet)
    from config import DATA_DIR
    if not User.objects.exists() and ((DATA_DIR / "users.json").exists() or (DATA_DIR / "requests.json").exists()):
        call_command("import_legacy", verbosity=0, stdout=quiet)
        return "данные прежней версии перенесены"
    return ""


def main() -> None:
    import tkinter as tk
    from tkinter import messagebox

    import launcher  # проверки и автозапуск Ollama — общие с прежней версией

    local_host = "127.0.0.1" if HOST == "0.0.0.0" else HOST
    root = tk.Tk()
    root.withdraw()
    if launcher.ICON_FILE.exists():
        try:
            root.iconbitmap(default=str(launcher.ICON_FILE))
        except tk.TclError:
            pass

    missing = missing_packages()
    if missing:
        if not messagebox.askyesno("ИИ-консультант", "Не установлены пакеты: " + ", ".join(missing) +
                                   ".\n\nУстановить сейчас? Нужен интернет, 1–3 минуты.", parent=root):
            return
        subprocess.run([launcher.console_python(), "-m", "pip", "install", "-r", str(WEB / "requirements.txt")],
                       creationflags=launcher.NEW_CONSOLE, check=False, cwd=str(WEB))
        if missing_packages():
            messagebox.showerror("ИИ-консультант", "Пакеты не установились. В PowerShell выполните:\n\n"
                                 f"cd \"{WEB}\"\npython -m pip install -r requirements.txt", parent=root)
            return

    # Перезапуск после обновления: прежний сервер ещё завершается — дождаться, пока он освободит порт
    if RESTART:
        for _ in range(60):
            if not launcher.port_busy(PORT, local_host):
                break
            time.sleep(0.5)

    # Порт: наш сервер уже работает (второй щелчок по ярлыку) — просто открыть браузер; порт занят другой
    # программой — взять следующий свободный (иначе браузер открыл бы чужую страницу)
    port, running = launcher.choose_web_port(PORT, busy=lambda p: launcher.port_busy(p, local_host),
                                             ours=lambda p: launcher.is_our_web(p, local_host))
    url = f"http://{local_host}:{port}/"
    if running and RESTART:
        return  # прежний сервер так и не завершился — второй не нужен
    if running:
        webbrowser.open(url)
        return
    port_note = (f"Порт {PORT} занят другой программой — использован порт {port}."
                 + (f"\nДля сотрудников адрес: http://ИМЯ-КОМПЬЮТЕРА:{port}" if HOST == "0.0.0.0" else "")
                 if port != PORT else "")

    if not launcher.ollama_running():
        status = launcher.start_ollama()
        if status in ("not_installed", "timeout") and not messagebox.askyesno(
                "ИИ-консультант", "Ollama не запущена. Без неё работают ответы по карте ответственности и по версиям "
                "документов, остальные вопросы будут передаваться специалисту.\n\nПродолжить?", parent=root):
            return

    note = prepare_database()
    from waitress import serve
    from konsultant.wsgi import application
    threading.Thread(target=lambda: serve(application, host=HOST, port=port, threads=8), daemon=True).start()
    # Прогрев: индекс базы знаний собирается сразу, векторы новых документов начинают считаться в фоне
    from knowledge_base import get_knowledge_base
    threading.Thread(target=get_knowledge_base, daemon=True, name="kb-warmup").start()
    note = "\n".join(n for n in (note, port_note) if n)

    # Окно «Сервер работает»: адрес, кнопка «Открыть», закрытие окна — остановка сервера
    root.deiconify()
    root.title("ИИ-консультант — сервер")
    root.resizable(False, False)
    tk.Label(root, text="ИИ-консультант (веб-версия) работает", font=("Tahoma", 12, "bold"), padx=20, pady=10).pack()
    tk.Label(root, text=f"Адрес: {url}" + (f"\n{note}" if note else "") + "\nЗакройте это окно, чтобы остановить сервер.",
             font=("Tahoma", 10), padx=20, justify="center").pack()
    tk.Button(root, text="Открыть в браузере", font=("Tahoma", 10, "bold"), bg="#2563eb", fg="white", relief="flat",
              padx=16, pady=4, command=lambda: webbrowser.open(url)).pack(pady=12)
    if not NO_BROWSER:  # после обновления браузер уже открыт — страница сама дождётся сервера
        root.after(1200, lambda: webbrowser.open(url))
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import tkinter as tk
        from tkinter import messagebox
        try:
            from journal import get_logger
            get_logger().exception("web start failed")
        except Exception:
            pass
        window = tk.Tk()
        window.withdraw()
        messagebox.showerror("ИИ-консультант", f"Веб-версия не запустилась: {type(exc).__name__}: {exc}", parent=window)
