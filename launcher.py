"""Удобный запуск ИИ-консультанта (двойной щелчок по «Запустить_ИИ-консультант.cmd» или по ярлыку).

1. Проверяет пакеты Python; если чего-то нет — предлагает установить (pip install -r requirements.txt).
2. Проверяет, запущена ли Ollama; если нет — запускает её сама и ждёт до 30 секунд.
   Нет Ollama на компьютере — предупреждает и всё равно открывает программу: ответы по карте
   ответственности и по графу версий работают и без модели.
3. Открывает окно программы (с входом) или окно сотрудника (--employee).

python launcher.py --create-shortcuts — ярлыки «ИИ-консультант» и «Спросить ИИ-консультанта» на рабочем столе.
Запускается через pythonw — без чёрного окна консоли; ошибки показываются в окне и пишутся в data/agent.log.
"""
import importlib.util
import os
import shutil
import struct
import subprocess
import sys
import time
import urllib.request
import zlib
from pathlib import Path

BASE = Path(__file__).resolve().parent
os.chdir(BASE)
sys.path.insert(0, str(BASE))

ICON_FILE = BASE / "icon.ico"
PACKAGES = {"openai": "openai", "docx": "python-docx", "pypdf": "pypdf", "pymorphy3": "pymorphy3"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


# ---------- значок программы ----------

def _png(size: int) -> bytes:
    """Значок: тёмно-синий скруглённый квадрат, белое «облачко» сообщения с тремя точками (без внешних библиотек)."""
    navy, white = (31, 58, 86), (255, 255, 255)
    rows = []
    for y in range(size):
        row = bytearray([0])  # тип фильтра PNG
        for x in range(size):
            u, v = (x + 0.5) / size, (y + 0.5) / size

            def rounded(x0, y0, x1, y1, r):
                cx, cy = min(max(u, x0 + r), x1 - r), min(max(v, y0 + r), y1 - r)
                return (u - cx) ** 2 + (v - cy) ** 2 <= r * r and x0 <= u <= x1 and y0 <= v <= y1

            color, alpha = None, 0
            if rounded(0.02, 0.02, 0.98, 0.98, 0.2):
                color, alpha = navy, 255
                bubble = rounded(0.16, 0.2, 0.84, 0.68, 0.12)
                tail = 0.28 <= u <= 0.44 and 0.66 <= v <= 0.84 and (u - 0.28) <= (0.84 - v) * 0.9
                if bubble or tail:
                    color = white
                    for dot_x in (0.34, 0.5, 0.66):
                        if (u - dot_x) ** 2 + (v - 0.44) ** 2 <= 0.055 ** 2:
                            color = navy
            row += bytes(color or (0, 0, 0)) + bytes([alpha])
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + chunk(b"IEND", b""))


def make_icon(path: Path = ICON_FILE) -> Path:
    """Файл .ico с картинками 16–256 пикселей (формат PNG внутри ICO поддерживается Windows Vista и новее)."""
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [_png(s) for s in sizes]
    offset = 6 + 16 * len(sizes)
    entries, data = b"", b""
    for size, image in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), offset + len(data))
        data += image
    path.write_bytes(struct.pack("<HHH", 0, 1, len(sizes)) + entries + data)
    return path


# ---------- проверки перед запуском ----------

def missing_packages() -> list[str]:
    return [pip_name for module, pip_name in PACKAGES.items() if importlib.util.find_spec(module) is None]


def console_python() -> str:
    """python.exe рядом с pythonw.exe — для установки пакетов в видимом окне."""
    exe = Path(sys.executable)
    candidate = exe.with_name("python.exe")
    return str(candidate if candidate.exists() else exe)


def ollama_running(timeout: float = 1.5) -> bool:
    from config import LLM_BASE_URL
    root = LLM_BASE_URL.rstrip("/").removesuffix("/v1")
    try:
        with urllib.request.urlopen(f"{root}/api/version", timeout=timeout):  # noqa: S310 — адрес из настроек
            return True
    except Exception:
        return False


WEB_APP_ID = "ai-konsultant"  # ответ /api/ping веб-версии


def port_busy(port: int, host: str = "127.0.0.1") -> bool:
    """На порту кто-то принимает соединения (наш сервер или другая программа)."""
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def is_our_web(port: int, host: str = "127.0.0.1", timeout: float = 1.5) -> bool:
    """На порту — веб-версия ИИ-консультанта (а не другая программа)."""
    import json
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/ping", timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8")).get("app") == WEB_APP_ID
    except Exception:
        return False


def choose_web_port(first: int, tries: int = 20, busy=port_busy, ours=is_our_web) -> tuple[int, bool]:
    """Порт для веб-версии: (порт, уже_запущена).

    Наш сервер уже работает на одном из портов — вернуть его (второй щелчок по ярлыку откроет браузер).
    Порт занят другой программой — пропустить. Иначе — первый свободный, начиная с first."""
    free = None
    for port in range(first, first + tries):
        if not busy(port):
            free = port if free is None else free
            continue
        if ours(port):
            return port, True
    if free is None:
        raise RuntimeError(f"порты {first}–{first + tries - 1} заняты другими программами; задайте WEB_PORT")
    return free, False


def ollama_command() -> list[str] | None:
    """Как запустить Ollama: приложение в трее (если установлено) или «ollama serve»."""
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama"
    for app in (local / "ollama app.exe", Path(os.environ.get("ProgramFiles", "")) / "Ollama" / "ollama app.exe"):
        if app.exists():
            return [str(app)]
    exe = shutil.which("ollama") or (str(local / "ollama.exe") if (local / "ollama.exe").exists() else None)
    return [exe, "serve"] if exe else None


def start_ollama(wait_seconds: float = 30, on_tick=None) -> str:
    """'running' — уже работает; 'started' — запущена; 'not_installed'; 'timeout' — не ответила вовремя."""
    if ollama_running():
        return "running"
    command = ollama_command()
    if command is None:
        return "not_installed"
    subprocess.Popen(command, creationflags=NO_WINDOW, close_fds=True, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if on_tick:
            on_tick()
        if ollama_running(timeout=1):
            return "started"
        time.sleep(0.5)
    return "timeout"


# ---------- ярлыки ----------

def create_shortcuts() -> list[Path]:
    """Ярлыки на рабочем столе: «ИИ-консультант» (вход) и «Спросить ИИ-консультанта» (окно сотрудника)."""
    if not ICON_FILE.exists():
        make_icon()
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    target = pythonw if pythonw.exists() else Path(sys.executable)
    shortcuts = [("ИИ-консультант", "launcher.py", "Вход в ИИ-консультант по внутренним процессам"),
                 ("Спросить ИИ-консультанта", "launcher.py --employee", "Задать вопрос без входа")]
    if (BASE / "web" / "start_web.py").exists():  # веб-версия: сервер + браузер
        shortcuts.append(("ИИ-консультант (веб)", r"web\start_web.py", "Веб-версия: открывается в браузере"))
    script = ["$desktop = [Environment]::GetFolderPath('Desktop')", "$shell = New-Object -ComObject WScript.Shell"]
    for name, args, description in shortcuts:
        script += [f"$s = $shell.CreateShortcut((Join-Path $desktop '{name}.lnk'))",
                   f"$s.TargetPath = '{target}'", f"$s.Arguments = '{args}'", f"$s.WorkingDirectory = '{BASE}'",
                   f"$s.IconLocation = '{ICON_FILE},0'", f"$s.Description = '{description}'", "$s.Save()"]
    script.append("[Environment]::GetFolderPath('Desktop')")
    result = subprocess.run(["powershell", "-NoProfile", "-Command", "; ".join(script)], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW, check=True)
    desktop = Path(result.stdout.strip().splitlines()[-1])
    return [desktop / f"{name}.lnk" for name, _, _ in shortcuts]


# ---------- запуск ----------

def _splash(text: str):
    import tkinter as tk
    root = tk.Tk()
    root.title("ИИ-консультант")
    root.resizable(False, False)
    if ICON_FILE.exists():
        try:
            root.iconbitmap(str(ICON_FILE))
        except tk.TclError:
            pass
    label = tk.Label(root, text=text, font=("Tahoma", 11), padx=30, pady=24)
    label.pack()
    root.update()
    return root, label


def run(argv: list[str]) -> None:
    import tkinter as tk
    from tkinter import messagebox

    if not ICON_FILE.exists():
        try:
            make_icon()
        except OSError:
            pass

    missing = missing_packages()
    if missing:
        root = tk.Tk()
        root.withdraw()
        install = messagebox.askyesno(
            "ИИ-консультант", "Не установлены пакеты Python: " + ", ".join(missing) + ".\n\nУстановить сейчас? "
            "Откроется окно установки (нужен интернет), это займёт 1–3 минуты.", parent=root)
        root.destroy()
        if not install:
            return
        subprocess.run([console_python(), "-m", "pip", "install", "-r", str(BASE / "requirements.txt")],
                       creationflags=NEW_CONSOLE, check=False)
        if missing_packages():
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("ИИ-консультант", "Пакеты не установились. Установите вручную в PowerShell:\n\n"
                                 f"cd \"{BASE}\"\npython -m pip install -r requirements.txt", parent=root)
            root.destroy()
            return

        import app
    app.main(argv)


def main() -> None:
    argv = sys.argv[1:]
    if "--create-shortcuts" in argv:
        paths = create_shortcuts()
        print("Ярлыки созданы:\n" + "\n".join(str(p) for p in paths))
        return
    try:
        run(argv)
    except Exception as exc:  # без консоли ошибку иначе не увидеть
        try:
            from journal import get_logger
            get_logger().exception("launcher failed")
        except Exception:
            pass
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("ИИ-консультант", f"Программа не запустилась: {type(exc).__name__}: {exc}\n\n"
                             f"Подробности — в файле {BASE / 'data' / 'agent.log'}", parent=root)
        root.destroy()


if __name__ == "__main__":
    main()
