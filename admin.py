"""Администрирование: версия, проверка компьютера, обновление с откатом, архив для переноса.

Обновление. Пакет обновления — папка «Проект» новой версии или её .zip (внутри — VERSION и app.py).
Перед заменой текущие файлы программы копируются в data/backups/<версия>_<дата> — из этой копии
можно откатиться. Заменяются только файлы программы (PROGRAM_PATTERNS). Данные организации — база знаний,
входящие, справочники, журналы, учётные записи — не трогаются; новые справочники копируются, только если их ещё нет.

Архив для переноса — один .zip: «Проект/» (программа + данные) и «Документы/» (Word): роли и пользователи
с логинами, инструкция по переносу с перечнем нужных программ, перечень файлов с контрольными суммами.
Пароли в документы не попадают: в архиве только их хеши (data/users.json) — после переноса пароли прежние.

Проверка компьютера: python admin.py --check (или «Проверить_установку.cmd»).
"""
import fnmatch
import hashlib
import importlib.metadata
import json
import platform
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from config import BACKUPS_DIR, BASE_DIR, DATA_DIR, EMBED_MODEL, LLM_BASE_URL, LLM_MODEL

# Файлы программы: заменяются при обновлении и сохраняются в резервную копию
PROGRAM_PATTERNS = ["*.py", "*.cmd", "VERSION", "requirements.txt", "README.md", "icon.ico",
                    "tests/*.py", "tests/fixtures/база_знаний/*", "tests/fixtures/*.json", "eval/*.py", "eval/questions.jsonl",
                    # веб-версия: Django (web/backend) и React (исходники web/frontend/src и сборка web/frontend/dist)
                    "web/*.md", "web/*.txt", "web/*.cmd", "web/*.py", "web/backend/*.py", "web/backend/konsultant/*.py",
                    "web/backend/core/*.py", "web/backend/core/migrations/*.py", "web/backend/core/management/*.py",
                    "web/backend/core/management/commands/*.py", "web/frontend/*.json", "web/frontend/*.ts",
                    "web/frontend/index.html", "web/frontend/src/*", "web/frontend/src/*/*", "web/frontend/public/*",
                    "web/frontend/dist/*", "web/frontend/dist/assets/*"]
# Папки, которые не просматриваются: зависимости, кеши, данные, сданные этапы, архивы
SKIP_DIRS = {"node_modules", "__pycache__", ".git", "data", "backups", "Архив_для_переноса", "Проверка_удобства",
             "Обновления", "Перенос веб-системы на другой компьютер"}
# Данные организации: при обновлении не заменяются (копируются, только если их нет)
ORG_FILES = ["employees.json", "process_map.json", "synonyms.json"]
ORG_DIRS = ["база_знаний", "входящие"]
REQUIRED_PACKAGES = {"openai": "openai", "python-docx": "docx", "pypdf": "pypdf", "pymorphy3": "pymorphy3"}
MIN_PYTHON = (3, 10)


class UpdateError(Exception):
    """Пакет обновления не подходит или повреждён."""


# ---------- версия ----------

def read_version(base_dir: Path = BASE_DIR) -> str:
    try:
        return (Path(base_dir) / "VERSION").read_text(encoding="utf-8").strip() or "0"
    except FileNotFoundError:
        return "0"


def version_tuple(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in version.strip().split("."))


APP_VERSION = read_version()


# ---------- файлы программы ----------

def _is_program_file(rel: str) -> bool:
    rel = rel.replace("\\", "/")
    return any(fnmatch.fnmatch(rel, p) and rel.count("/") == p.count("/") for p in PROGRAM_PATTERNS)


def program_files(base_dir: Path) -> list[str]:
    import os
    base_dir = Path(base_dir)
    found = []
    for folder, dirs, files in os.walk(base_dir):
        # node_modules (десятки тысяч файлов), данные и папки «Замечание N» не просматриваются
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith("Замечание")]
        for name in files:
            rel = (Path(folder) / name).relative_to(base_dir).as_posix()
            if _is_program_file(rel):
                found.append(rel)
    return sorted(found)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------- обновление и откат ----------

def _unpack_package(source: Path, work: Path) -> Path:
    """Папка проекта новой версии: сама папка или распакованный .zip (VERSION может лежать во вложенной папке)."""
    source = Path(source)
    if source.is_dir():
        root = source
    elif zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            for name in archive.namelist():  # защита от путей вида ../../ в архиве
                target = (work / name).resolve()
                if not str(target).startswith(str(work.resolve())):
                    raise UpdateError(f"Недопустимый путь в архиве: {name}")
            archive.extractall(work)
        root = work
    else:
        raise UpdateError("Нужна папка «Проект» новой версии или .zip-архив с ней")
    candidates = [root] + [p.parent for p in root.rglob("VERSION")]
    for candidate in candidates:
        if (candidate / "VERSION").is_file() and (candidate / "app.py").is_file():
            _verify_manifest(candidate)
            return candidate
    raise UpdateError("В пакете нет файлов VERSION и app.py — это не пакет обновления")


def _verify_manifest(root: Path) -> None:
    """Пакет, собранный build_update_package, несёт контрольные суммы: повреждённый файл — отказ."""
    manifest = root / "update_manifest.json"
    if not manifest.is_file():
        return
    expected = json.loads(manifest.read_text(encoding="utf-8")).get("files", {})
    broken = [rel for rel, digest in expected.items() if not (root / rel).is_file() or _sha256(root / rel) != digest]
    if broken:
        raise UpdateError(f"Пакет повреждён ({len(broken)} файл(ов) не совпадает с контрольной суммой, например {broken[0]}) "
                          "— скопируйте его заново")


def inspect_update(source: Path, base_dir: Path = BASE_DIR) -> dict:
    """Что изменится при обновлении — для подтверждения администратором."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _unpack_package(source, Path(tmp))
        new_files = program_files(root)
        current = set(program_files(base_dir))
        changed = [f for f in new_files if f in current and _sha256(root / f) != _sha256(Path(base_dir) / f)]
        return {"from_version": read_version(base_dir), "to_version": read_version(root),
                "changed": changed, "added": [f for f in new_files if f not in current],
                "new_org_files": [f for f in ORG_FILES if (root / f).exists() and not (Path(base_dir) / f).exists()]}


def backup_program(base_dir: Path = BASE_DIR, backups_dir: Path = BACKUPS_DIR, note: str = "") -> Path:
    version = read_version(base_dir)
    target = Path(backups_dir) / f"{version}_{datetime.now():%Y%m%d-%H%M%S}"
    for rel in program_files(base_dir):
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(base_dir) / rel, target / rel)
    (target / "backup.json").write_text(json.dumps({"version": version, "created": datetime.now().isoformat(timespec="seconds"),
                                                    "note": note}, ensure_ascii=False, indent=1), encoding="utf-8")
    return target


def apply_update(source: Path, base_dir: Path = BASE_DIR, backups_dir: Path = BACKUPS_DIR,
                 allow_same_or_older: bool = False) -> dict:
    """Резервная копия → замена файлов программы → новые справочники (если их нет). Данные не трогаются."""
    base_dir = Path(base_dir)
    with tempfile.TemporaryDirectory() as tmp:
        root = _unpack_package(source, Path(tmp))
        old, new = read_version(base_dir), read_version(root)
        if version_tuple(new) <= version_tuple(old) and not allow_same_or_older:
            raise UpdateError(f"Версия пакета {new} не новее установленной {old}")
        backup = backup_program(base_dir, backups_dir, note=f"перед обновлением до {new}")
        replaced = []
        for rel in program_files(root):
            (base_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, base_dir / rel)
            replaced.append(rel)
        added_org = []
        for rel in ORG_FILES:
            if (root / rel).exists() and not (base_dir / rel).exists():
                shutil.copy2(root / rel, base_dir / rel)
                added_org.append(rel)
        return {"from_version": old, "to_version": new, "replaced": replaced, "added_org": added_org,
                "backup": str(backup)}


UPDATE_README = """ИИ-консультант по внутренним процессам — пакет обновления до версии {version} (собран {created}).

Как установить (администратор):
1. Войдите в веб-версию учётной записью администратора.
2. «Администрирование» → «Версия» → «Выбрать пакет обновления» → этот .zip-файл.
3. Проверьте, что изменится, и нажмите «Установить обновление».
   Программа сделает резервную копию, заменит файлы программы и сама перезапустится (около 30 секунд).
Данные (база знаний, обращения, учётные записи, справочники, журналы) не меняются.
Вернуть прежнюю версию — там же, «Резервные копии» → «Вернуть эту версию».
"""


def build_update_package(dest_dir: Path, base_dir: Path = BASE_DIR) -> Path:
    """Пакет обновления: только файлы программы (без данных организации) — «Проект/…» в .zip."""
    base_dir, dest_dir = Path(base_dir), Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    version = read_version(base_dir)
    path = dest_dir / f"ИИ-консультант_обновление_{version}.zip"
    files = program_files(base_dir)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel in files:
            archive.write(base_dir / rel, f"Проект/{rel}")
        archive.writestr("ПРОЧИТАЙ_МЕНЯ.txt", UPDATE_README.format(version=version, created=f"{datetime.now():%d.%m.%Y %H:%M}"))
        archive.writestr("Проект/update_manifest.json", json.dumps(
            {"version": version, "created": datetime.now().isoformat(timespec="seconds"),
             "files": {rel: _sha256(base_dir / rel) for rel in files}}, ensure_ascii=False, indent=1))
    return path


def list_backups(backups_dir: Path = BACKUPS_DIR) -> list[dict]:
    items = []
    for folder in sorted(Path(backups_dir).glob("*"), reverse=True):
        meta = folder / "backup.json"
        if meta.is_file():
            info = json.loads(meta.read_text(encoding="utf-8"))
            items.append({"path": str(folder), "version": info.get("version", "?"), "created": info.get("created", ""),
                          "note": info.get("note", "")})
    return items


def rollback(backup: Path, base_dir: Path = BASE_DIR, backups_dir: Path = BACKUPS_DIR) -> dict:
    """Вернуть файлы программы из резервной копии (текущие тоже сохраняются — откат можно отменить)."""
    backup = Path(backup)
    if not (backup / "backup.json").is_file():
        raise UpdateError("Это не резервная копия программы")
    current = backup_program(base_dir, backups_dir, note="перед откатом")
    restored = []
    for rel in program_files(backup):
        (Path(base_dir) / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup / rel, Path(base_dir) / rel)
        restored.append(rel)
    return {"restored": restored, "version": read_version(base_dir), "saved_current": str(current)}


# ---------- новые документы базы знаний ----------

SUPPORTED_DOCS = (".docx", ".pdf", ".eml", ".msg", ".txt", ".md")
CARD_FIELDS = ("type", "number", "title", "date", "owner", "effective_from", "review_date")
# Ещё поля карточки (веб): дата размещения и статус «Устарел», отмеченный вручную («Утратил силу»)
EXTRA_CARD_FIELDS = ("placed", "status", "id")  # id — закрепляется при правке текста (файл меняет формат)


from doc_loader import QUALITY_ANSWER_TYPE, quality_answer_text  # noqa: E402,F401 — формат «Ответа службы качества»


def add_quality_answer(question: str, answer: str, knowledge_dir: Path, request_id: str, author: str = "",
                       related: dict | None = None, departments: list[str] | None = None) -> Path:
    """Пополнение базы знаний: ответ службы качества на вопрос, которого не было в регламентах.

    Сохраняется документом базы знаний (вопрос — п. 1, ответ — п. 2, к какому регламенту — п. 3) с карточкой:
    вид «Ответ службы качества», дата, автор, «Для кого», связь с регламентом. Дальше он ищется как обычный
    документ, а на тот же вопрос система отвечает им сама (agent: answer_source «quality»)."""
    question, answer = " ".join(question.split()), answer.strip()
    if not question or not answer:
        raise ValueError("Нужны и вопрос, и ответ")
    today = datetime.now().strftime("%d.%m.%Y")
    safe = re.sub(r"[^\w\-]", "_", request_id) or datetime.now().strftime("%Y%m%d-%H%M%S")
    path = Path(knowledge_dir) / f"Ответ_службы_качества_{safe}.md"
    # Номер обращения и дата — только в карточке: числа в тексте участвуют в проверке противоречий и фактов
    lines = ["Ответ службы качества", f"1. Вопрос: {question}", "2. Ответ: " + " ".join(answer.split())]
    if related:
        lines.append(f"3. Дополняет документ: {related.get('title', '')}" + (f" от {related['date']}" if related.get("date") else ""))
    Path(knowledge_dir).mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    card = {"id": f"QA-{safe}", "type": QUALITY_ANSWER_TYPE, "title": f"Ответ службы качества: {question[:120]}",
            "date": today, "placed": today, "owner": author or "Служба качества", "request": request_id}
    if related and related.get("id"):
        card["related"] = related["id"]
    if departments:
        card["departments"] = list(departments)
    path.with_name(path.name + ".meta.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def delete_document(document_path: Path, trash_dir: Path) -> Path:
    """Убрать документ из базы: файл и его карточка переносятся в папку удалённых (можно вернуть)."""
    document_path = Path(document_path)
    target = Path(trash_dir) / f"{datetime.now():%Y%m%d-%H%M%S}_{document_path.name}"
    target.mkdir(parents=True, exist_ok=False)
    shutil.move(str(document_path), str(target / document_path.name))
    card = document_path.with_name(document_path.name + ".meta.json")
    if card.exists():
        shutil.move(str(card), str(target / card.name))
    return target


def list_deleted(trash_dir: Path) -> list[dict]:
    items = []
    for folder in sorted(Path(trash_dir).glob("*"), reverse=True):
        files = [p for p in folder.iterdir() if not p.name.endswith(".meta.json")] if folder.is_dir() else []
        if files:
            stamp = folder.name.split("_", 1)[0]
            items.append({"id": folder.name, "file": files[0].name,
                          "deleted": f"{stamp[6:8]}.{stamp[4:6]}.{stamp[:4]} {stamp[9:11]}:{stamp[11:13]}"})
    return items


EDITABLE_AS_IS = {".txt", ".md"}  # текст сохраняется как есть; .docx — заново по абзацам; остальные — в .docx / .txt


def replace_document_text(document_path: Path, text: str, versions_dir: Path, keep: dict | None = None) -> Path:
    """Правка текста документа администратором. Прежняя версия (файл и карточка) копируется в versions_dir.
    .txt/.md — текст как есть; .docx — новый документ Word по строкам (оформление прежнего не сохраняется);
    .pdf — сохраняется как .docx, .eml/.msg — как .txt (отредактировать PDF и письмо на месте нельзя).
    keep — поля карточки, которые нужно закрепить (номер документа, вид, название, дата, владелец), чтобы после смены
    файла документ остался тем же (ссылки карты процессов и истории не ломаются). Возвращает путь файла."""
    from docx import Document

    document_path = Path(document_path)
    card = document_path.with_name(document_path.name + ".meta.json")
    backup = Path(versions_dir) / f"{datetime.now():%Y%m%d-%H%M%S}_{document_path.name}"
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(document_path, backup / document_path.name)
    if card.exists():
        shutil.copy2(card, backup / card.name)

    suffix = document_path.suffix.lower()
    target = document_path
    if suffix == ".pdf":
        target = document_path.with_suffix(".docx")
    elif suffix in (".eml", ".msg"):
        target = document_path.with_suffix(".txt")
    if target != document_path and target.exists():
        raise FileExistsError(target.name)

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if target.suffix.lower() == ".docx":
        doc = Document()
        for line in lines:
            doc.add_paragraph(line)
        doc.save(target)
    else:
        target.write_text("\n".join(lines), encoding="utf-8")

    if target != document_path:  # файл другого формата: карточка — к новому имени, старый файл — только в версиях
        new_card = target.with_name(target.name + ".meta.json")
        if card.exists():
            shutil.move(str(card), str(new_card))
        document_path.unlink()
    if keep:
        # Номер документа закрепляется всегда (он мог зависеть от даты и номера в тексте). Вид, название, дата и
        # владелец — только если сменился формат: у письма они были в заголовках, у PDF — определены по тексту
        new_card = target.with_name(target.name + ".meta.json")
        current = json.loads(new_card.read_text(encoding="utf-8")) if new_card.exists() else {}
        pinned = {k: v for k, v in keep.items() if v and k not in current and (k == "id" or target != document_path)}
        if pinned:
            write_card(target, pinned)
    return target


def purge_deleted(entry: Path, trash_dir: Path) -> str:
    """Полностью удалить документ из «Удалённых» (файл и карточку) — вернуть его будет нельзя.
    Удаляется только папка внутри trash_dir. Возвращает имя файла документа."""
    entry, trash_dir = Path(entry).resolve(), Path(trash_dir).resolve()
    if entry.parent != trash_dir or not entry.is_dir():
        raise FileNotFoundError(entry.name)
    files = [p.name for p in entry.iterdir() if not p.name.endswith(".meta.json")]
    shutil.rmtree(entry)
    return files[0] if files else entry.name


def restore_document(entry: Path, knowledge_dir: Path) -> Path:
    """Вернуть удалённый документ (с карточкой) в базу знаний."""
    entry = Path(entry)
    files = [p for p in entry.iterdir() if not p.name.endswith(".meta.json")]
    if not files:
        raise FileNotFoundError(entry.name)
    target = Path(knowledge_dir) / files[0].name
    if target.exists():
        raise FileExistsError(target.name)
    for item in entry.iterdir():
        shutil.move(str(item), str(Path(knowledge_dir) / item.name))
    entry.rmdir()
    return target


def add_document_file(source: Path, knowledge_dir: Path, overwrite: bool = False) -> Path:
    """Копия файла в папку базы знаний. База пересобирается сама: она следит за изменениями папки."""
    source = Path(source)
    if source.suffix.lower() not in SUPPORTED_DOCS:
        raise ValueError(f"Формат {source.suffix or 'без расширения'} не поддерживается. Можно: {', '.join(SUPPORTED_DOCS)}")
    target = Path(knowledge_dir) / source.name
    if target.exists() and not overwrite:
        raise FileExistsError(target.name)
    Path(knowledge_dir).mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def write_card(document_path: Path, card: dict) -> Path | None:
    """Карточка «<файл>.meta.json»: переданные поля записываются (пустое значение — поле убирается, система
    определит его сама), остальное в карточке сохраняется (id, статус, связи, прежние поля).
    departments — список подразделений, для которых действует документ; пустой — для всех."""
    path = Path(document_path).with_name(Path(document_path).name + ".meta.json")
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError:
        current = {}
    for key, value in card.items():
        if key == "departments":
            from doc_loader import departments_list
            value = departments_list(value)
        elif key in CARD_FIELDS or key in EXTRA_CARD_FIELDS:
            value = str(value or "").strip()
        else:
            continue
        if value:
            current[key] = value
        else:
            current.pop(key, None)
    if not current:
        if path.exists():
            path.unlink()
        return None
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------- проверка компьютера ----------

def _ollama(path: str, timeout: float = 2.0):
    root = LLM_BASE_URL.rstrip("/").removesuffix("/v1")
    with urllib.request.urlopen(f"{root}{path}", timeout=timeout) as response:  # noqa: S310 — адрес из настроек
        return json.loads(response.read().decode("utf-8"))


def system_check() -> list[dict]:
    """Что установлено на этом компьютере: [{item, required, found, ok}]."""
    rows = [{"item": "Операционная система", "required": "Windows 10 или 11 (64 бит)",
             "found": f"{platform.system()} {platform.release()} ({platform.machine()})",
             "ok": platform.system() == "Windows"}]
    py = sys.version_info
    rows.append({"item": "Python", "required": f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]} или новее",
                 "found": f"{py.major}.{py.minor}.{py.micro}", "ok": (py.major, py.minor) >= MIN_PYTHON})
    for package in REQUIRED_PACKAGES:
        try:
            found, ok = importlib.metadata.version(package), True
        except importlib.metadata.PackageNotFoundError:
            found, ok = "не установлен", False
        rows.append({"item": f"Пакет Python {package}", "required": "pip install -r requirements.txt",
                     "found": found, "ok": ok})
    from config import LLM_API_KEY
    has_key = bool(LLM_API_KEY and LLM_API_KEY != "ollama")
    rows.append({"item": "API-ключ языковой модели", "required": "задан в LLM_API_KEY",
                 "found": "задан" if has_key else "не задан", "ok": has_key})
    rows.append({"item": f"Модель ответов ({LLM_MODEL})", "required": LLM_BASE_URL,
                 "found": "настроена", "ok": has_key})
    if EMBED_MODEL and EMBED_MODEL.lower() != "off":
        rows.append({"item": f"Модель поиска ({EMBED_MODEL})", "required": LLM_BASE_URL,
                     "found": "настроена", "ok": has_key})

    free = shutil.disk_usage(BASE_DIR).free / 1e9
    rows.append({"item": "Свободное место на диске", "required": "не меньше 10 ГБ (модели и база)",
                 "found": f"{free:.0f} ГБ", "ok": free >= 10})
    return rows


def verify_manifest(base_dir: Path = BASE_DIR) -> list[str]:
    """Сверка файлов с перечнем из архива переноса: список расхождений (пусто — всё на месте)."""
    manifest = Path(base_dir) / "transfer_manifest.json"
    if not manifest.is_file():
        return []
    problems = []
    for rel, digest in json.loads(manifest.read_text(encoding="utf-8"))["files"].items():
        path = Path(base_dir) / rel
        if not path.is_file():
            problems.append(f"нет файла: {rel}")
        # Программа должна совпадать байт в байт; данные (обращения, журналы) меняются при работе — только наличие
        elif _is_program_file(rel) and _sha256(path) != digest:
            problems.append(f"файл изменён или повреждён: {rel}")
    return problems


# ---------- архив для переноса ----------

def transfer_files(base_dir: Path = BASE_DIR, data_dir: Path = DATA_DIR) -> list[str]:
    """Что переносится: программа, данные организации и журналы (без резервных копий и временных файлов)."""
    base_dir = Path(base_dir)
    files = set(program_files(base_dir))
    for rel in ORG_FILES:
        if (base_dir / rel).is_file():
            files.add(rel)
    for folder in ORG_DIRS:
        for path in (base_dir / folder).rglob("*"):
            if path.is_file():
                files.add(path.relative_to(base_dir).as_posix())
    data_dir = Path(data_dir)
    if data_dir.is_dir():
        for path in data_dir.rglob("*"):
            in_backups = Path(BACKUPS_DIR) in path.parents or path.relative_to(data_dir).parts[0] == "backups"
            if path.is_file() and not in_backups:
                try:
                    files.add(path.relative_to(base_dir).as_posix())
                except ValueError:  # папка данных вне проекта — кладём как data/…
                    files.add("data/" + path.relative_to(data_dir).as_posix())
    # Документ «Пользователи, логины и пароли» в архив не попадает, где бы ни лежал, — его кладут только по явной
    # просьбе через passwords_doc (документы базы знаний вроде «Политика паролей» переносятся как обычно)
    return sorted(f for f in files if "логины и пароли" not in Path(f).name.lower())


def _doc():
    import docx
    from docx.shared import Pt
    document = docx.Document()
    style = document.styles["Normal"]
    style.font.name, style.font.size = "Calibri", Pt(11)
    return document


def _table(document, header: list[str], rows: list[list[str]]):
    table = document.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, header):
        cell.text = text
        for run in cell.paragraphs[0].runs:
            run.bold = True
    for row in rows:
        cells = table.add_row().cells
        for cell, text in zip(cells, row):
            cell.text = str(text)
    return table


def build_roles_document(users: list[dict], path: Path) -> None:
    document = _doc()
    document.add_heading("Роли и пользователи", 0)
    document.add_paragraph(f"ИИ-консультант по внутренним процессам, версия {APP_VERSION}. "
                           f"Сформировано {datetime.now():%d.%m.%Y %H:%M}.")
    document.add_heading("Роли и права", 1)
    from users import ROLES
    _table(document, ["Роль", "Что может"], [[role, "; ".join(rights)] for role, rights in ROLES.items()])
    document.add_heading("Учётные записи", 1)
    _table(document, ["Логин", "ФИО", "Роль", "Состояние", "Создана", "Последний вход"],
           [[u["login"], u.get("name", ""), u["role"], "активна" if u.get("active", True) else "заблокирована",
             u.get("created") or "—", u.get("last_login") or "—"] for u in users])
    document.add_heading("Пароли", 1)
    document.add_paragraph("Пароли в этот документ не включены и в системе не хранятся: хранится только их "
                           "хеш (PBKDF2-HMAC-SHA256, 200 000 итераций) в файле data/users.json. После переноса "
                           "пользователи входят со своими прежними паролями. Забытый пароль администратор "
                           "меняет в разделе «Администрирование» → «Сменить пароль».")
    document.save(path)


def build_transfer_instruction(checks: list[dict], files: list[str], archive_name: str, path: Path,
                               docs: list[str] | None = None, version: str = APP_VERSION,
                               passwords_doc: str | None = None) -> None:
    document = _doc()
    document.add_heading("Инструкция по переносу на другой компьютер", 0)
    document.add_paragraph(f"ИИ-консультант по внутренним процессам, версия {version}. Архив: {archive_name}. "
                           f"Сформировано {datetime.now():%d.%m.%Y %H:%M}.")
    document.add_heading("1. Что должно быть установлено", 1)
    document.add_paragraph("Слева — что нужно, справа — что стоит на компьютере, где сделан архив "
                           "(ставьте те же или более новые версии).")
    _table(document, ["Программа", "Нужно", "На исходном компьютере"],
           [[c["item"], c["required"], c["found"]] for c in checks])
    document.add_heading("2. Перенос по шагам", 1)
    steps = [
        "Установите Python 3.10 или новее: https://www.python.org/downloads/ (при установке отметьте "
        "«Add python.exe to PATH»).",
        "Установите Ollama: https://ollama.com/download (после установки она запускается сама; программа "
        "ИИ-консультант тоже умеет её запускать).",
        f"Скачайте модели: в PowerShell выполните «ollama pull {LLM_MODEL}»"
        + (f" и «ollama pull {EMBED_MODEL}»." if EMBED_MODEL and EMBED_MODEL.lower() != "off" else ".")
        + " Модели в архив не входят — это несколько гигабайт.",
        "Распакуйте архив, например в C:\\ИИ-консультант. Папка «Проект» — это программа со всеми данными; "
        "папка «Документы» — инструкции.",
        "В папке «Проект» дважды щёлкните «Создать_ярлыки_на_рабочем_столе.cmd» — на рабочем столе появятся "
        "ярлыки «ИИ-консультант» и «Спросить ИИ-консультанта» (ярлыки со старого компьютера не переносятся: "
        "в них записан старый путь).",
        "Дважды щёлкните ярлык «ИИ-консультант (веб)» (веб-версия: откроется браузер с адресом http://127.0.0.1:8000; "
        "при первом запуске база создаётся и данные переносятся сами). Если пакетов Python нет, программа предложит "
        "установить их сама (нужен интернет, 1–3 минуты). Вручную: в PowerShell в папке «Проект\\web» — "
        "«python -m pip install -r requirements.txt». Окно на Tkinter — ярлык «ИИ-консультант».",
        "Доступ с других компьютеров сети: запускайте «Запустить_веб_для_сети.cmd» и попросите ИТ-службу открыть порт 8000 "
        "в брандмауэре; адрес для сотрудников — http://ИМЯ-КОМПЬЮТЕРА:8000.",
        "Проверьте компьютер и целостность файлов: двойной щелчок «Проверить_установку.cmd» (или "
        "«python admin.py --check»). Все строки должны быть «OK», расхождений файлов — нет.",
        ("Войдите своей учётной записью: логины и пароли — в документе «" + passwords_doc + "» в папке «Документы» "
         "(конфиденциально); если пользователь уже сменил пароль — действует его новый пароль."
         if passwords_doc else
         "Войдите своей учётной записью: логины и роли — в «Роли_и_пользователи.docx», пароли прежние "
         "(они не входят в архив; у администратора — документ «Пользователи, логины и пароли»)."),
        "Окно для сотрудников без входа — ярлык «Спросить ИИ-консультанта».",
        "Если на новом компьютере другой путь к базе знаний или почтовому серверу — задайте переменные "
        "окружения (KNOWLEDGE_DIR, SMTP_HOST и др.), список — в README.md.",
    ]
    for n, step in enumerate(steps, 1):
        document.add_paragraph(f"{n}. {step}")
    document.add_heading("3. Что в архиве", 1)
    groups = {"Программа": [f for f in files if _is_program_file(f)],
              "База знаний": [f for f in files if f.startswith("база_знаний/")],
              "Входящие письма": [f for f in files if f.startswith("входящие/")],
              "Справочники": [f for f in files if f in ORG_FILES],
              "Данные и журналы (обращения, история, учётные записи, настройки)": [f for f in files if f.startswith("data/")]}
    _table(document, ["Часть", "Файлов", "Примеры"],
           [[name, str(len(items)), ", ".join(items[:4]) + (" …" if len(items) > 4 else "")] for name, items in groups.items()])
    document.add_paragraph("Полный перечень с контрольными суммами — «Перечень_файлов.docx»; та же сверка "
                           "выполняется командой «python admin.py --check».")
    document.add_heading("4. Документы в папке «Документы»", 1)
    descriptions = {
        "Инструкция_по_переносу.docx": "Этот документ",
        "Роли_и_пользователи.docx": "Роли и права, логины пользователей (без паролей)",
        "Перечень_файлов.docx": "Все файлы архива с размером и контрольной суммой SHA-256",
        "КАК_ЗАПУСТИТЬ.docx": "Как запускать программу каждый день, первый вход, что делать, если не запускается",
        "ИНСТРУКЦИЯ_АДМИНИСТРАТОРА.docx": "Учётные записи и пароли, добавление документов, обновление версии, перенос",
        "ИНСТРУКЦИЯ_РАЗРАБОТЧИКА.docx": "Для разработчика: что сделано по этапам, модули, библиотеки, модели, методы",
        "ИНСТРУКЦИЯ_АДМИНИСТРАТОРА_ВЕБ.docx": "Администратору веб-версии: учётные записи, документы, справочник, перенос, сеть",
        "РАЗРАБОТКА_ДЛЯ_НОВИЧКА_Django_и_React.docx": "Подробное обучение разработке веб-версии на Django и React",
    }
    if passwords_doc:
        descriptions[passwords_doc] = ("КОНФИДЕНЦИАЛЬНО: логины и пароли пользователей на момент выдачи. Храните архив "
                                       "в защищённом месте; после переноса раздайте пароли лично и удалите документ")
    names = ["Инструкция_по_переносу.docx", "Роли_и_пользователи.docx", "Перечень_файлов.docx"] + list(docs or [])
    _table(document, ["Документ", "Что в нём"], [[n, descriptions.get(n, "")] for n in names])
    document.add_heading("5. Если что-то не так", 1)
    _table(document, ["Признак", "Что сделать"], [
        ["«Не найден Python» при запуске", "Установите Python (шаг 1) и отметьте «Add python.exe to PATH»"],
        ["«Ollama не установлена» или «не ответила»", "Установите или запустите Ollama; проверьте «ollama list»"],
        ["Ошибка «No module named …»", "python -m pip install -r requirements.txt"],
        ["Не удаётся войти", "Проверьте раскладку (латиница) и Caps Lock; администратор может назначить новый пароль"],
        ["«файл изменён или повреждён» при проверке", "Распакуйте архив заново"],
    ])
    document.save(path)


def build_file_list(files: list[str], base_dir: Path, path: Path, digests: dict) -> None:
    document = _doc()
    document.add_heading("Перечень файлов для переноса", 0)
    document.add_paragraph(f"Версия {APP_VERSION}, файлов: {len(files)}. Контрольная сумма — SHA-256 "
                           "(сверяется командой «python admin.py --check»).")
    _table(document, ["Файл", "Размер, КБ", "SHA-256"],
           [[rel, f"{(Path(base_dir) / rel).stat().st_size / 1024:.1f}" if (Path(base_dir) / rel).exists() else "—",
             digests.get(rel, "")] for rel in files])
    document.save(path)


# Инструкции из папки программы, которые кладутся в архив переноса (если они есть).
# Документ с паролями сюда намеренно не входит: архив копируют, а пароли выдаются лично.
TRANSFER_DOCS = ["КАК_ЗАПУСТИТЬ.docx", "ИНСТРУКЦИЯ_АДМИНИСТРАТОРА_ВЕБ.docx", "ИНСТРУКЦИЯ_АДМИНИСТРАТОРА.docx",
                 "ИНСТРУКЦИЯ_РАЗРАБОТЧИКА.docx", "РАЗРАБОТКА_ДЛЯ_НОВИЧКА_Django_и_React.docx"]

README_TEXT = """ИИ-консультант по внутренним процессам, версия {version}
Архив для переноса на другой компьютер, создан {created}.

С чего начать: откройте «Документы\\Инструкция_по_переносу.docx».

Коротко:
1. Установите Python (python.org) и Ollama (ollama.com), скачайте модели: ollama pull {llm}{embed}
2. Распакуйте архив, в папке «Проект» дважды щёлкните «Создать_ярлыки_на_рабочем_столе.cmd».
3. Запускайте ярлыком «ИИ-консультант» на рабочем столе. Пароли — прежние.
4. Проверка: «Проект\\Проверить_установку.cmd».
"""


def build_transfer_archive(dest_dir: Path, users: list[dict], base_dir: Path = BASE_DIR, data_dir: Path = DATA_DIR,
                           checks: list[dict] | None = None, extra_docs: list[Path] | None = None,
                           passwords_doc: Path | None = None) -> Path:
    """Один .zip: «Проект/» (программа и данные) + «Документы/» (Word) + «ПРОЧИТАЙ_МЕНЯ.txt».
    extra_docs — дополнительные инструкции; по умолчанию — TRANSFER_DOCS из папки программы.
    passwords_doc — документ с логинами и паролями: кладётся только по явной просьбе, в имени архива — «с_паролями»."""
    base_dir, dest_dir = Path(base_dir), Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    version = read_version(base_dir)
    if extra_docs is None:
        extra_docs = [base_dir / n for n in TRANSFER_DOCS if (base_dir / n).is_file()]
    if passwords_doc is not None:
        extra_docs = list(extra_docs) + [Path(passwords_doc)]
    suffix = "_с_паролями" if passwords_doc is not None else ""
    name = f"ИИ-консультант_перенос_{version}_{datetime.now():%Y%m%d-%H%M}{suffix}.zip"
    archive_path = dest_dir / name
    files = transfer_files(base_dir, data_dir)
    sources = {}
    for rel in files:
        source = base_dir / rel
        if not source.exists() and rel.startswith("data/"):
            source = Path(data_dir) / rel[len("data/"):]
        sources[rel] = source
    digests = {rel: _sha256(src) for rel, src in sources.items()}
    checks = system_check() if checks is None else checks
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        build_roles_document(users, tmp / "Роли_и_пользователи.docx")
        build_transfer_instruction(checks, files, name, tmp / "Инструкция_по_переносу.docx",
                                   docs=[Path(d).name for d in extra_docs], version=version,
                                   passwords_doc=Path(passwords_doc).name if passwords_doc is not None else None)
        build_file_list(files, base_dir, tmp / "Перечень_файлов.docx", digests)
        manifest = {"version": version, "created": datetime.now().isoformat(timespec="seconds"), "files": digests}
        embed = f" и ollama pull {EMBED_MODEL}" if EMBED_MODEL and EMBED_MODEL.lower() != "off" else ""
        readme = README_TEXT.format(version=version, created=f"{datetime.now():%d.%m.%Y %H:%M}", llm=LLM_MODEL, embed=embed)
        if passwords_doc is not None:
            readme += ("\nКОНФИДЕНЦИАЛЬНО: в папке «Документы» есть «" + Path(passwords_doc).name + "» — логины и пароли "
                       "всех пользователей. Храните архив в защищённом месте и не пересылайте его по почте и в чатах.\n")
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("ПРОЧИТАЙ_МЕНЯ.txt", "﻿" + readme.replace("\n", "\r\n"))  # BOM — для Блокнота
            for rel, source in sources.items():
                archive.write(source, f"Проект/{rel}")
            archive.writestr("Проект/transfer_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
            for doc in ("Инструкция_по_переносу.docx", "Роли_и_пользователи.docx", "Перечень_файлов.docx"):
                archive.write(tmp / doc, f"Документы/{doc}")
            for doc in extra_docs:
                archive.write(doc, f"Документы/{Path(doc).name}")
    return archive_path


def main() -> int:
    if "--package" in sys.argv:  # разработчику: собрать пакет обновления из текущей папки
        dest = Path(sys.argv[sys.argv.index("--package") + 1]) if len(sys.argv) > sys.argv.index("--package") + 1 \
            else BASE_DIR / "Обновления"
        print(f"Пакет обновления: {build_update_package(dest)}")
        return 0
    if "--rollback-last" in sys.argv:  # на крайний случай: обновление не запускается — вернуть прежнюю версию
        backups = list_backups()
        if not backups:
            print("Резервных копий нет")
            return 1
        result = rollback(Path(backups[0]["path"]))
        print(f"Возвращена версия {result['version']} из {backups[0]['path']}; файлов: {len(result['restored'])}")
        return 0
    if "--check" not in sys.argv:
        print("Использование: python admin.py --check | --package [папка] | --rollback-last")
        return 2
    print(f"ИИ-консультант по внутренним процессам, версия {APP_VERSION}\n")
    rows = system_check()
    for row in rows:
        print(f"{'OK ' if row['ok'] else 'НЕТ'}  {row['item']}: {row['found']}  (нужно: {row['required']})")
    problems = verify_manifest()
    if (BASE_DIR / "transfer_manifest.json").is_file():
        print("\nСверка файлов с архивом переноса: " + ("все файлы на месте" if not problems else ""))
        for problem in problems:
            print("  " + problem)
    ok = all(r["ok"] for r in rows) and not problems
    print("\nИтог: " + ("всё готово к работе" if ok else "есть замечания — см. строки «НЕТ»"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
