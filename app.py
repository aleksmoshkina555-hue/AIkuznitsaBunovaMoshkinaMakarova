import json
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk

from agent import (SYSTEM_NAME, LLMModelNotFoundError, LLMUnavailableError, describe_doc, logger,
                   process_corporate_query)
from config import (BASE_DIR, BASELINE_SEARCH_MINUTES, ESCALATION_CONTACT, INBOX_DIR, KNOWLEDGE_DIR, LLM_BASE_URL,
                    LLM_MODEL, LOG_FILE, REQUEST_SLA_HOURS, SMTP_HOST)
from inbox import CLOSED, ESCALATED, NEW, READY, REJECTED, SENT, WORK, RequestStore
from knowledge_base import SOURCE_GROUPS, get_knowledge_base, source_types_for
from mailer import extract_email, send_reply
from process_map import extract_amount
import admin
import employees as directory
import users as accounts
from self_service import (ANSWER, BLOCKED, CHECK, CLARIFY, ESCALATE, PERIODS, REVIEW, decide, hand_off, inbox_summary,
                          load_records, log_question, mark_handoff, mark_helped, needs_specialist, summary, to_review)
from ui_support import (CONFIDENCE, append_history, confidence, load_eml, load_history, load_ui_settings, open_file,
                        parse_pasted_email, question_key, record_feedback, save_ui_settings)

# Цвета оболочки (в духе систем электронного документооборота)
NAVY, NAVY_BUTTON, NAVY_TEXT = "#1f3a56", "#2d7fc1", "#ffffff"
SIDEBAR, SIDEBAR_ACTIVE, SIDEBAR_TEXT = "#eef1f4", "#dbe2ea", "#1f2937"
PAGE, CARD, MUTED, LINE = "#f8fafc", "#ffffff", "#6b7280", "#e5e7eb"
HINT_COLOR = "#111827"  # подсказки справа — чёрным и обычным размером шрифта, чтобы читались
UI_FONT = "Tahoma"  # шрифт интерфейса (сделан для экрана — чёткий и компактный); другой — поле "font_family" в data/ui_settings.json
ANSWER_BG = "#f0fdf4"  # светло-зелёная область «Найденный ответ и источники»

SEARCH_BUTTON_TEXT = "Найти ответ и подготовить письмо"
EXAMPLES = [
    ("Кто подписывает договор на 300 тыс.?", "Кто подписывает агентский договор на 300 000 руб.?"),
    ("Где шаблон договора?", "Где взять актуальный шаблон агентского договора?"),
    ("Действует ли регламент 2024?", "Регламент согласования договоров 2024 года ещё действует?"),
    ("Кто проверяет контрагента?", "Кто отвечает за проверку контрагента в ЭДО?"),
]
STATUS_COLORS = {"Действует": "#166534", "Устарел": "#991b1b", "Противоречит": "#991b1b", "Не вступил в силу": "#92400e"}
STATUS_FILTERS = ["Все", "Действует", "Устарел", "Не вступил в силу", "Противоречит"]
SOURCE_NAMES = {"process_map": "готовый ответ по теме", "actuality": "граф версий документов",
                "model": f"модель {LLM_MODEL}", "sources": "текст документа", "escalation": "передано специалисту",
                "blocked": "фильтр безопасности"}
VERDICTS = {"верно": "Верно", "неверно": "Неверно", "устарело": "Устарело"}
STATUS_DOT_COLORS = {NEW: "#2563eb", WORK: "#f59e0b", READY: "#16a34a", SENT: "#9ca3af", ESCALATED: "#ea580c",
                     REJECTED: "#dc2626"}
QUICK_FILTERS = ["Все", "Новые", "В работе", "Просроченные", "30 дней"]

# Значки: символ системного шрифта значков Windows (Segoe Fluent Icons / Segoe MDL2 Assets — чёткие,
# векторные) и запасной обычный символ, если такого шрифта нет
ICON_FONTS = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
ICONS = {"home": ("", "⌂"), "inbox": ("", "✉"), "ready": ("", "✓"), "escalated": ("", "⚑"),
         "sent": ("", "➦"), "attention": ("", "⚠"), "docs": ("", "▤"), "history": ("", "↺"),
         "menu": ("", "☰"), "search": ("", "⌕"), "import": ("", "✉"), "help": ("", "?"),
         "refresh": ("", "⟳"), "open": ("", "▣"), "read": ("", "✉"), "play": ("", "▶"),
         "download": ("", "⇩")}
ICONS.update({"ask": ("", "✎"), "summary": ("", "▦"),  # «Задать вопрос» (сообщение), «Сводка» (диаграмма)
              "employees": ("", "☺"), "add": ("", "+"), "edit": ("", "✎"),
              "delete": ("", "✕"),  # «Сотрудники» (люди), добавить, изменить, удалить
              "admin": ("", "⚙"), "user": ("", "☺"), "key": ("", "🔑"),
              "lock": ("", "🔒"), "signout": ("", "⏻")})  # администрирование, пользователь, пароль, блок, выход
ICON_COLOR = "#1f3a56"

# Пункты меню слева: ключ, значок, подпись. Первый — «Задать вопрос»: сотрудник получает ответ сам и сразу
NAV_ASK = [("ask", "ask", "Задать вопрос")]
NAV_MAIN = [("home", "home", "Главная"), ("inbox", "inbox", "Журнал вопросов"), ("ready", "ready", "Ответ готов"),
            ("escalated", "escalated", "Переданы специалисту")]  # «Отправленные» — в «Журнале вопросов»
NAV_KB = [("attention", "attention", "Требует внимания"),  # «Сводка» убрана из меню (не нужна)
          ("docs", "docs", "База знаний"), ("employees", "employees", "Сотрудники"),
          ("history", "history", "История запросов")]
NAV_ADMIN = [("admin", "admin", "Администрирование")]  # только для роли «Администратор»
ASK_EXAMPLES = ["За сколько дней нужно инициировать заявку на расчёт?", "Кто назначает руководителя проекта?",
                "В какой срок РП создаёт смету в ДБРЦТ?", "Когда сотрудник 1 линии закрывает заявку как «Дисквалифицировано»?"]
INBOX_SCOPES = {"inbox": "Журнал вопросов", "ready": "Ответ готов", "escalated": "Переданы специалисту", "sent": "Отправленные"}


class CorpAgentApp:
    def __init__(self, root: tk.Tk, employee_mode: bool = False, user: dict | None = None,
                 user_store: "accounts.UserStore | None" = None):
        """employee_mode — окно сотрудника: только «Задать вопрос», без входящих и меню оператора (вход не нужен).
        user — вошедший пользователь {"login", "name", "role"}; права — по роли (users.PERMISSIONS).
        Без user — права роли «Пользователь»."""
        self.root = root
        self.employee_mode = employee_mode
        self.user = user or {"login": "", "name": "", "role": accounts.USER}
        self.user_store = user_store or accounts.UserStore()
        self.settings = load_ui_settings()
        # Масштаб экрана: 1.0 при 100 %, 1.5 при 150 % (если включена поддержка масштаба — enable_dpi_awareness)
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96)
        self.font_family = self.settings.get("font_family") or UI_FONT
        self.root.title(f"{SYSTEM_NAME} (ИИкузница)")
        # Сохранённый размер окна — только если он сохранён при том же масштабе
        saved = self.settings.get("geometry") if self.settings.get("scale") == round(self.scale, 2) else None
        self.root.geometry(saved or self._default_geometry())
        self.root.minsize(self.px(1000), self.px(620))
        self.root.configure(bg=PAGE)

        self.store = RequestStore()
        if not employee_mode:
            self.store.import_folder(INBOX_DIR)
        self.ask_busy = False          # идёт поиск ответа на вопрос сотрудника
        self.ask_question_data = None  # вопрос сотрудника, на который показан ответ
        self.ask_record = None         # его запись в журнале self_service.jsonl
        self.ask_last_result = None    # ответ агента на этот вопрос
        self.search_source_types = None  # источники последнего поиска на главном экране (None — все)
        self.ask_source_types = None     # то же на экране «Задать вопрос»
        self.summary_period = "Месяц"
        self.current_request_id = None
        self.current_view = None
        self.inbox_scope = "inbox"
        self.quick_filter = "Все"
        self.batch_running = False

        self.last_result = None
        self.last_question = None
        self.busy_since = None
        self.session_cache = {}      # ключ вопроса → (отпечаток базы, результат): мгновенный ответ на повтор
        self.history_items = {}      # строка истории → (вопрос, результат или None)
        self.source_window = None
        self.trace_visible = False
        self._links = []
        self.font_size = int(self.settings.get("font_size", 10))

        self._make_fonts()
        self._make_styles()
        self.setup_hotkeys()
        self.create_widgets()
        self.refresh_sources()
        self.load_history_into_tab()
        self.refresh_inbox()
        self.show_view("ask")  # главное — сотрудник спрашивает сам; оператору — «Главная» и «Входящие»
        self.ask_input.focus_set()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- размеры, шрифты, стили ----------

    def _default_geometry(self) -> str:
        """Окно не больше экрана (с запасом на панель задач), иначе низ окна с кнопками уходит за край."""
        width = min(self.px(1320), self.root.winfo_screenwidth() - self.px(40))
        height = min(self.px(880), self.root.winfo_screenheight() - self.px(90))
        return f"{width}x{height}+{self.px(20)}+{self.px(10)}"

    def px(self, value: float) -> int:
        """Размер в пикселях с учётом масштаба экрана (шрифты масштабируются сами, отступы и ширины — нет)."""
        return round(value * self.scale)

    def _make_fonts(self):
        size = self.font_size
        self.f_text = tkfont.Font(family=self.font_family, size=size)
        self.f_bold = tkfont.Font(family=self.font_family, size=size, weight="bold")
        self.f_small = tkfont.Font(family=self.font_family, size=max(size - 1, 7))
        self.f_header = tkfont.Font(family=self.font_family, size=size + 5, weight="bold")
        self.f_title = tkfont.Font(family=self.font_family, size=size + 6)
        self.f_subheader = tkfont.Font(family=self.font_family, size=size + 5)  # как f_header, но не жирный
        families = set(tkfont.families(self.root))
        self.icon_family = next((f for f in ICON_FONTS if f in families), None)
        self.f_icon = tkfont.Font(family=self.icon_family or "Segoe UI Symbol", size=size + 2)
        self.f_icon_small = tkfont.Font(family=self.icon_family or "Segoe UI Symbol", size=size)
        self.f_row_bold = tkfont.Font(family=self.font_family, size=size, weight="bold")
        self.f_row_strike = tkfont.Font(family=self.font_family, size=size, overstrike=1)
        self.f_kpi = tkfont.Font(family=self.font_family, size=size + 10, weight="bold")  # крупные числа «Сводки»
        self.f_question = tkfont.Font(family=self.font_family, size=size + 2)  # поле вопроса сотрудника
        self._scaled = [(self.f_text, 0), (self.f_bold, 0), (self.f_small, -1), (self.f_header, 5), (self.f_title, 6), (self.f_subheader, 5),
                        (self.f_icon, 2), (self.f_icon_small, 0), (self.f_row_bold, 0), (self.f_row_strike, 0),
                        (self.f_kpi, 10), (self.f_question, 2)]

    def _dot_image(self, color: str) -> tk.PhotoImage:
        """Кружок статуса со сглаженным краем (смешивание с белым фоном списка); размер — по масштабу экрана."""
        size = self.px(13)
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        image = tk.PhotoImage(master=self.root, width=size, height=size)
        center, radius = (size - 1) / 2, size / 2 - 1
        rows = []
        for y in range(size):
            row = []
            for x in range(size):
                # доля пикселя внутри круга: 4×4 подвыборки
                inside = sum(((x + (i + .5) / 4 - .5 - center) ** 2 + (y + (j + .5) / 4 - .5 - center) ** 2) <= radius ** 2
                             for i in range(4) for j in range(4)) / 16
                mix = [round(c * inside + 255 * (1 - inside)) for c in (r, g, b)]
                row.append("#%02x%02x%02x" % tuple(mix))
            rows.append("{" + " ".join(row) + "}")
        image.put(" ".join(rows))
        for y in range(size):
            for x in range(size):
                if image.get(x, y) == (255, 255, 255):
                    image.transparency_set(x, y, True)
        return image

    def icon(self, name: str) -> str:
        glyph, fallback = ICONS[name]
        return glyph if self.icon_family else fallback

    def _make_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=PAGE)
        # Карточка — одна тонкая светлая рамка; строки внутри карточки — без рамок (иначе видны лишние линии)
        style.configure("Card.TFrame", background=CARD, relief="solid", borderwidth=1,
                        bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        style.configure("Inner.TFrame", background=CARD, relief="flat", borderwidth=0)
        style.configure("TLabel", background=PAGE, foreground="#0f172a", font=self.f_text)
        style.configure("Card.TLabel", background=CARD, foreground="#0f172a", font=self.f_text)
        style.configure("CardBold.TLabel", background=CARD, foreground="#0f172a", font=self.f_bold)
        style.configure("Sub.TLabel", background=PAGE, foreground="#475569", font=self.f_small)
        style.configure("TButton", font=self.f_text)
        style.configure("Treeview", font=self.f_text, rowheight=self.px(self.font_size * 2.4))
        style.configure("Treeview.Heading", font=self.f_bold)
        # Список обращений: белый фон, без рамок, мягкое выделение строки
        style.configure("Inbox.Treeview", background=CARD, fieldbackground=CARD, foreground="#111827",
                        font=self.f_text, rowheight=self.px(self.font_size * 3.2), borderwidth=0)
        style.configure("Inbox.Treeview.Heading", background=CARD, foreground="#374151", font=self.f_bold,
                        relief="flat", borderwidth=0)
        style.map("Inbox.Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", "#111827")])
        style.map("Inbox.Treeview.Heading", background=[("active", "#f3f4f6")])
        self.style = style

    def change_font_size(self, delta: int):
        self.font_size = min(16, max(8, self.font_size + delta))
        for font, offset in self._scaled:
            font.configure(size=max(self.font_size + offset, 7))
        self.style.configure("Treeview", rowheight=self.px(self.font_size * 2.4))
        self.style.configure("Inbox.Treeview", rowheight=self.px(self.font_size * 3.2))
        self.settings["font_size"] = self.font_size
        save_ui_settings(self.settings)

    def on_close(self):
        self.settings["geometry"] = self.root.geometry()
        self.settings["scale"] = round(self.scale, 2)
        self.settings["font_size"] = self.font_size
        save_ui_settings(self.settings)
        self.root.destroy()

    # ---------- горячие клавиши ----------

    def setup_hotkeys(self):
        # Ctrl+C/V/X/A по коду клавиши — работают и в русской раскладке
        def handle_ctrl_key(event):
            widget = event.widget
            if not isinstance(widget, (tk.Entry, tk.Text)):
                return None
            if event.keycode == 67:
                widget.event_generate("<<Copy>>")
                return "break"
            if event.keycode == 86:
                widget.event_generate("<<Paste>>")
                return "break"
            if event.keycode == 88:
                widget.event_generate("<<Cut>>")
                return "break"
            if event.keycode == 65:
                if isinstance(widget, tk.Entry):
                    widget.select_range(0, "end")
                    widget.icursor("end")
                elif isinstance(widget, tk.Text):
                    widget.tag_add("sel", "1.0", "end")
                return "break"
            return None

        def on_home(event) -> bool:
            return str(event.widget.winfo_toplevel()) == str(self.root) and self.current_view == "home"

        def on_ctrl_enter(event):
            if str(event.widget.winfo_toplevel()) != str(self.root):
                return None
            if self.current_view == "home":
                self.on_search_click()
            elif self.current_view == "ask":
                self.ask_question()
            return "break"

        self.root.bind_all("<Control-KeyPress>", handle_ctrl_key)
        self.root.bind_all("<Control-Return>", on_ctrl_enter)
        self.root.bind_all("<Control-Shift-Return>", lambda e: (self.send_email() if on_home(e) else None) or "break")
        self.root.bind_all("<Escape>", lambda e: self.clear_form() if on_home(e) else None)

    # ---------- оболочка: верхняя панель, меню, экраны ----------

    def create_widgets(self):
        self._build_topbar()
        body = tk.Frame(self.root, bg=PAGE)
        body.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(body, bg=SIDEBAR, width=self.px(250))
        if not self.employee_mode:  # окну сотрудника меню оператора не нужно
            self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.main = tk.Frame(body, bg=PAGE)
        self.main.pack(side="left", fill="both", expand=True)
        self._build_sidebar()

        self.views = {}
        for key in ("ask", "summary", "home", "inbox", "docs", "employees", "attention", "history", "admin"):
            self.views[key] = ttk.Frame(self.main, padding=(14, 10))
        self.answer_tab = self.views["home"]   # совместимость: главный экран — бывшая вкладка «Ответ»
        self.docs_tab, self.attention_tab, self.history_tab = self.views["docs"], self.views["attention"], self.views["history"]
        self._build_ask_view()
        self._build_summary_view()
        self._build_answer_tab()
        self._build_inbox_view()
        self._build_docs_tab()
        self._build_employees_view()
        self._build_attention_tab()
        if accounts.can(self.user, "manage_users"):
            self._build_admin_view()
        self._build_history_tab()

    def _build_topbar(self):
        bar = tk.Frame(self.root, bg=NAVY, height=self.px(54))
        bar.pack(fill="x")
        bar.pack_propagate(False)

        burger = tk.Label(bar, text=self.icon("menu"), bg=NAVY, fg=NAVY_TEXT, font=self.f_icon, cursor="hand2", padx=14)
        if not self.employee_mode:
            burger.pack(side="left")
        else:
            tk.Frame(bar, bg=NAVY, width=self.px(16)).pack(side="left")
        burger.bind("<Button-1>", lambda e: self.toggle_sidebar())
        tk.Label(bar, text="ИИ-консультант", bg=NAVY, fg=NAVY_TEXT, font=self.f_header).pack(side="left")
        tk.Label(bar, text="по внутренним процессам", bg=NAVY, fg=NAVY_TEXT, font=self.f_subheader).pack(side="left", padx=(8, 18))
        new_button = tk.Button(bar, text="＋ Новое обращение", bg=NAVY_BUTTON, fg="white", activebackground="#3b8fd4",
                               activeforeground="white", relief="flat", font=self.f_bold, padx=12, pady=4, cursor="hand2",
                               command=self.new_request)
        if not self.employee_mode:
            new_button.pack(side="left", pady=10)

        # Поиск по обращениям (только у оператора)
        search = tk.Frame(bar, bg="#2b4c6f")
        if not self.employee_mode:
            search.pack(side="left", padx=18, pady=10, fill="y")
        tk.Label(search, text=self.icon("search"), bg="#2b4c6f", fg="#ffffff", font=self.f_icon, padx=8).pack(side="left")
        self.global_search = tk.StringVar()
        entry = tk.Entry(search, textvariable=self.global_search, width=22, relief="flat", bg="#2b4c6f", fg="white",
                         insertbackground="white", font=self.f_text)
        entry.pack(side="left", fill="y", padx=(0, 6))
        entry.insert(0, "Искать обращения…")
        entry.config(fg="#9fb6cc")
        entry.bind("<FocusIn>", lambda e: self._search_focus(entry, True))
        entry.bind("<FocusOut>", lambda e: self._search_focus(entry, False))
        entry.bind("<Return>", lambda e: self.on_global_search())
        self.global_search_entry = entry
        tk.Button(search, text="Найти", bg="#36597e", fg="white", relief="flat", font=self.f_small, padx=10,
                  cursor="hand2", command=self.on_global_search).pack(side="left", fill="y")

        right = [(self.icon("help"), lambda: self._open_path(str(BASE_DIR / "README.md")), True),
                 (self.icon("import"), self.on_import_eml, True),
                 ("A+", lambda: self.change_font_size(1), False),
                 ("A−", lambda: self.change_font_size(-1), False)]
        if self.employee_mode:
            right.pop(1)  # загрузка писем — дело оператора
        else:
            # Кто вошёл и «Выйти» (сменить пользователя) — справа
            out = tk.Label(bar, text=self.icon("signout"), bg=NAVY, fg=NAVY_TEXT, font=self.f_icon, padx=10, cursor="hand2")
            out.pack(side="right")
            out.bind("<Button-1>", lambda e: self.sign_out())
            short = self.user.get("name") or self.user.get("login") or ""
            parts = short.split()
            if len(parts) >= 2:  # «Иванов Иван Сергеевич» → «Иванов И.С.»
                short = parts[0] + " " + "".join(p[0] + "." for p in parts[1:3])
            self.user_label = tk.Label(bar, text=f"{short} · {self.user['role']}", bg=NAVY, fg="#dbe7f3",
                                       font=self.f_text, padx=6, cursor="hand2")
            self.user_label.pack(side="right")
            # Ключ (или щелчок по имени) — «Сменить свой пароль»: любой вошедший пользователь
            key = tk.Label(bar, text=self.icon("key"), bg=NAVY, fg=NAVY_TEXT, font=self.f_icon, padx=6, cursor="hand2")
            key.pack(side="right")
            for widget in (key, self.user_label):
                widget.bind("<Button-1>", lambda e: self.change_own_password())
        for text, command, is_icon in right:
            label = tk.Label(bar, text=text, bg=NAVY, fg=NAVY_TEXT, font=self.f_icon if is_icon else self.f_bold,
                             padx=10, cursor="hand2")
            label.pack(side="right")
            label.bind("<Button-1>", lambda e, c=command: c())
        # Подпись «Шрифт:» убрана — после имени пользователя на панели не хватало места; A− / A+ понятны и так

    def _search_focus(self, entry, focused: bool):
        placeholder = "Искать обращения…"
        if focused and entry.get() == placeholder:
            entry.delete(0, "end")
            entry.config(fg="white")
        elif not focused and not entry.get():
            entry.insert(0, placeholder)
            entry.config(fg="#9fb6cc")

    def search_text(self) -> str:
        text = self.global_search.get().strip()
        return "" if text.startswith("Искать обращения") else text.lower()

    def toggle_sidebar(self):
        if self.sidebar.winfo_manager():
            self.sidebar.pack_forget()
        else:
            self.sidebar.pack(side="left", fill="y", before=self.main)

    def _build_sidebar(self):
        self.nav_rows = {}  # счётчиков рядом с пунктами меню нет (по замечанию — лишние цифры)

        def add_item(key, icon, label, badge_color=None):
            row = tk.Frame(self.sidebar, bg=SIDEBAR, cursor="hand2")
            row.pack(fill="x")
            if badge_color:
                icon_widget = tk.Label(row, text=icon, bg="#e5e7eb", fg=badge_color, font=self.f_small, width=3)
                icon_widget.pack(side="left", padx=(self.px(16), self.px(10)), pady=self.px(7))
            else:
                icon_widget = tk.Label(row, text=self.icon(icon), bg=SIDEBAR, fg=ICON_COLOR, font=self.f_icon, width=2)
                icon_widget.pack(side="left", padx=(self.px(14), self.px(8)), pady=self.px(6))
            shown = label if len(label) <= 26 else label[:25].rstrip() + "…"
            text_widget = tk.Label(row, text=shown, bg=SIDEBAR, fg=SIDEBAR_TEXT, font=self.f_text, anchor="w")
            text_widget.pack(side="left", fill="x", expand=True, padx=(0, self.px(14)))
            for widget in (row, icon_widget, text_widget):
                widget.bind("<Button-1>", lambda e, k=key: self.on_nav(k))
            self.nav_rows[key] = (row, [w for w in (icon_widget, text_widget) if w is not icon_widget or not badge_color])

        tk.Frame(self.sidebar, bg=SIDEBAR, height=self.px(8)).pack(fill="x")
        for key, icon, label in NAV_ASK:
            add_item(key, icon, label)
        tk.Frame(self.sidebar, bg="#d1d5db", height=1).pack(fill="x", padx=self.px(12), pady=self.px(8))
        for key, icon, label in NAV_MAIN:
            add_item(key, icon, label)
        tk.Frame(self.sidebar, bg="#d1d5db", height=1).pack(fill="x", padx=self.px(12), pady=self.px(8))
        for key, icon, label in NAV_KB + (NAV_ADMIN if accounts.can(self.user, "manage_users") else []):
            add_item(key, icon, label)
        # Список «Процессы» в меню убран (не нужен): фильтр по процессу остаётся в «Журнале вопросов»

    def _highlight_nav(self, active_key):
        for key, (row, widgets) in self.nav_rows.items():
            color = SIDEBAR_ACTIVE if key == active_key else SIDEBAR
            row.config(bg=color)
            for widget in widgets:
                widget.config(bg=color)

    def on_nav(self, key: str, keep_search: bool = False):
        if not keep_search and self.search_text():  # выбор пункта меню сбрасывает поиск
            self.global_search_entry.delete(0, "end")
            self._search_focus(self.global_search_entry, False)
        if key == "home":
            self.show_view("home")
        elif key in INBOX_SCOPES or key.startswith("proc:"):
            self.inbox_scope = key
            self.show_view("inbox")
        else:
            self.show_view(key)
        self._highlight_nav(key)

    def show_view(self, name: str):
        for key, view in self.views.items():
            if key != name:
                view.pack_forget()
        self.views[name].pack(fill="both", expand=True)
        self.current_view = name
        if name == "inbox":
            self.store.reload()  # обращения, переданные из окна сотрудника
            self.refresh_inbox()
            self._highlight_nav(self.inbox_scope)
        else:
            if name == "summary":
                self.refresh_summary()
            elif name == "employees":
                self.directory = directory.load_directory()  # файл могли поправить вручную
                self.refresh_employees()
            elif name == "admin":
                self.refresh_admin()
            self._highlight_nav(name)

    def refresh_counts(self):
        """Счётчики в меню убраны (лишние цифры); количество — на «Сводке» и в самих списках.
        Метод оставлен пустым: его вызывают места, где меняются обращения и база."""

    # ---------- «Задать вопрос»: сотрудник спрашивает сам и сразу получает ответ ----------

    def _build_ask_view(self):
        view = self.views["ask"]
        self._view_title(view, "Задать вопрос")
        ttk.Label(view, text="Ответ приходит сразу и опирается только на действующие приказы, регламенты, письма и чаты. "
                             "Если система не уверена в ответе, вопрос сам уйдёт специалисту.",
                  foreground=HINT_COLOR, font=self.f_text, wraplength=self.px(1000), justify="left").pack(anchor="w", pady=(0, 8))

        form = ttk.Frame(view, style="Card.TFrame", padding=12)
        form.pack(fill="x")
        who = ttk.Frame(form, style="Inner.TFrame")
        who.pack(fill="x", pady=(0, 8))
        ttk.Label(who, text="Кто спрашивает:", style="CardBold.TLabel").pack(side="left")
        self.ask_employee = tk.Entry(who, font=self.f_text, bg=PAGE, relief="solid", borderwidth=1, width=40)
        self.ask_employee.pack(side="left", padx=(6, 10))
        self.ask_employee.insert(0, self.settings.get("employee", ""))
        ttk.Label(who, text="имя и e-mail — если вопрос уйдёт специалисту, ответ придёт на почту",
                  style="Card.TLabel", foreground=HINT_COLOR).pack(side="left")

        self.ask_sources_note = self._build_source_row(form)
        ttk.Label(form, text="Ваш вопрос:", style="CardBold.TLabel").pack(anchor="w")
        self.ask_input = tk.Text(form, height=3, font=self.f_question, bg=PAGE, relief="solid", borderwidth=1,
                                 wrap="word", padx=8, pady=6)
        self.ask_input.pack(fill="x", pady=(4, 8))
        self.ask_input.bind("<Control-Return>", lambda e: self.ask_question() or "break")

        controls = ttk.Frame(form, style="Inner.TFrame")
        controls.pack(fill="x")
        self.ask_button = tk.Button(controls, text="Спросить", bg="#2563eb", fg="white", disabledforeground="white",
                                    font=self.f_bold, relief="flat", padx=28, pady=6, cursor="hand2",
                                    command=self.ask_question)
        self.ask_button.pack(side="left")
        self.ask_status = ttk.Label(controls, text="", style="Card.TLabel", foreground="#64748b")
        self.ask_status.pack(side="left", padx=12)
        self.ask_progress = ttk.Progressbar(controls, mode="indeterminate", length=self.px(130))
        ttk.Label(controls, text="Ctrl+Enter — спросить", style="Card.TLabel", foreground=HINT_COLOR).pack(side="right")

        examples = ttk.Frame(form, style="Inner.TFrame")
        examples.pack(fill="x", pady=(8, 0))
        ttk.Label(examples, text="Часто спрашивают:", style="Card.TLabel", foreground=HINT_COLOR).pack(side="left")
        for (label, _), question in zip(EXAMPLES, ASK_EXAMPLES):
            tk.Button(examples, text=label, font=self.f_small, relief="flat", bg="#eef2ff", fg="#3730a3",
                      activebackground="#e0e7ff", cursor="hand2", padx=8,
                      command=lambda q=question: self.use_ask_example(q)).pack(side="left", padx=(6, 0))

        # Карточка ответа — появляется после первого вопроса
        self.ask_result = ttk.Frame(view, style="Card.TFrame", padding=12)
        self.ask_banner = tk.Label(self.ask_result, text="", font=self.f_bold, anchor="w", justify="left", padx=10, pady=6,
                                   wraplength=self.px(1000))
        self.ask_banner.pack(fill="x")
        # перенос строк — по ширине окна, а не по заданной заранее
        self.ask_banner.bind("<Configure>", lambda e: self.ask_banner.config(wraplength=max(e.width - self.px(24), 200)))
        self.ask_clarify = ttk.Frame(self.ask_result, style="Inner.TFrame")
        ttk.Label(self.ask_clarify, text="Сумма договора, руб.:", style="CardBold.TLabel").pack(side="left")
        self.ask_amount = tk.Entry(self.ask_clarify, font=self.f_text, width=16, relief="solid", borderwidth=1)
        self.ask_amount.pack(side="left", padx=6)
        self.ask_amount.bind("<Return>", lambda e: self.on_ask_amount())
        ttk.Button(self.ask_clarify, text="Уточнить (Enter)", command=self.on_ask_amount).pack(side="left")

        actions = ttk.Frame(self.ask_result, style="Inner.TFrame")
        actions.pack(side="bottom", fill="x", pady=(8, 0))
        self.ask_buttons = {}
        for key, text, color in (("helped", "Помогло", "#16a34a"), ("not_helped", "Не помогло", "#dc2626"),
                                 ("handoff", "Передать специалисту", "#9a3412")):
            command = (lambda: self.on_ask_helped(True)) if key == "helped" else \
                      (lambda: self.on_ask_helped(False)) if key == "not_helped" else (lambda: self.on_ask_handoff())
            button = tk.Button(actions, text=text, font=self.f_text, fg=color, bg=CARD, relief="groove", padx=12,
                               state="disabled", command=command)
            button.pack(side="left", padx=(0, 6))
            self.ask_buttons[key] = button
        self.ask_feedback = ttk.Label(actions, text="", style="Card.TLabel", foreground=HINT_COLOR)
        self.ask_feedback.pack(side="left", padx=8)
        ttk.Label(actions, text="Щёлкните по документу, чтобы открыть пункт", style="Card.TLabel",
                  foreground=HINT_COLOR).pack(side="right")
        self.ask_responsible = tk.Label(self.ask_result, text="", bg=CARD, fg="#2563eb", font=self.f_bold, anchor="w",
                                        justify="left", width=1)
        self.ask_responsible.pack(side="bottom", fill="x", pady=(6, 0))
        self.ask_responsible.bind("<Configure>", lambda e: self.ask_responsible.config(wraplength=max(e.width - 4, 150)))

        self.ask_output = tk.Text(self.ask_result, height=8, font=self.f_question, bg=ANSWER_BG, fg="#14532d",
                                  relief="solid", borderwidth=1, wrap="word", padx=10, pady=8, cursor="arrow")
        self.ask_output.pack(fill="both", expand=True, pady=(8, 0))
        self.ask_output.tag_configure("heading", font=self.f_bold, foreground="#166534")
        self.ask_output.tag_configure("link", foreground="#1d4ed8", underline=True)
        self.ask_output.tag_configure("muted", foreground="#64748b")
        self.ask_output.tag_configure("warning", foreground="#92400e")
        self.ask_output.tag_bind("link", "<Enter>", lambda e: self.ask_output.config(cursor="hand2"))
        self.ask_output.tag_bind("link", "<Leave>", lambda e: self.ask_output.config(cursor="arrow"))

    def use_ask_example(self, question: str):
        self.ask_input.delete("1.0", "end")
        self.ask_input.insert("1.0", question)
        self.ask_question()

    def ask_question(self):
        """Вопрос сотрудника: поиск в фоне, затем ответ сразу или передача специалисту."""
        text = self.ask_input.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning("Вопрос пустой", "Напишите вопрос в поле «Ваш вопрос».")
            return
        if not any(var.get() for var in self.source_vars.values()):
            messagebox.showwarning("Источники", "Отметьте хотя бы один источник в строке «Искать в».")
            return
        if self.ask_busy or self.busy_since is not None or self.batch_running:
            return
        self.ask_source_types = self.selected_source_types()
        employee = self.ask_employee.get().strip()
        if employee != self.settings.get("employee", ""):
            self.settings["employee"] = employee  # запомнить, чтобы не вводить каждый раз
            save_ui_settings(self.settings)
        question = {"sender": employee or "Сотрудник", "subject": "", "body": text}
        self.ask_question_data = question

        cached = self.session_cache.get(self._cache_key(question, self.ask_source_types))
        if cached and cached[0] == get_knowledge_base().signature:
            self.show_ask_result(cached[1], from_cache=True)
            return

        self.ask_busy = True
        self.ask_button.config(state="disabled", text="Ищу ответ...")
        self.ask_status.config(text="Ищу в действующих документах...", foreground="#2563eb")
        self.ask_progress.pack(side="left", padx=(0, 8), after=self.ask_status)
        self.ask_progress.start(12)

        def worker():
            def progress(stage: str):
                self.root.after(0, lambda: self.ask_status.config(text=f"{stage}...", foreground="#2563eb"))
            try:
                result = process_corporate_query(question["sender"], "", question["body"], progress=progress,
                                                 source_types=self.ask_source_types)
                self.root.after(0, lambda: self.show_ask_result(result))
            except Exception as exc:
                if not isinstance(exc, (LLMUnavailableError, LLMModelNotFoundError)):
                    logger.exception("internal error while processing employee question")
                self.root.after(0, lambda error=exc: self._ask_error(error))

        threading.Thread(target=worker, daemon=True).start()

    def _ask_stop_busy(self):
        self.ask_busy = False
        self.ask_progress.stop()
        self.ask_progress.pack_forget()
        self.ask_button.config(state="normal", text="Спросить")

    def _ask_error(self, error: Exception):
        self._ask_stop_busy()
        if isinstance(error, (LLMUnavailableError, LLMModelNotFoundError)):
            self.ask_status.config(text="Консультант сейчас недоступен — попробуйте позже или напишите специалисту",
                                   foreground="#dc2626")
            logger.warning("employee question: model unavailable: %s", error)
        else:
            self.ask_status.config(text=f"Ошибка: {type(error).__name__} — подробности в журнале", foreground="#dc2626")

    def _employee_email_hint(self) -> str:
        return "" if extract_email(self.ask_employee.get()) else \
            " Укажите e-mail в поле «Кто спрашивает» — иначе специалист не сможет ответить на почту."

    def show_ask_result(self, result: dict, from_cache: bool = False):
        self._ask_stop_busy()
        question = self.ask_question_data
        self.ask_last_result = result
        decision = decide(result)
        if not from_cache:
            record = append_history(question, result)
            self._add_history_row(record, result)
            if not result.get("blocked"):
                self.session_cache[self._cache_key(question, self.ask_source_types)] = (get_knowledge_base().signature, result)
        employee = self.ask_employee.get().strip()
        self.ask_record = log_question(employee, question["body"], result, decision)

        request = None
        if needs_specialist(decision):  # ответа нет или его должен проверить человек — сразу во «Входящие»
            request = hand_off(self.store, employee, question["body"], result)
            mark_handoff(self.ask_record["id"], request["id"], decision)
            self.ask_record["request_id"] = request["id"]
            self.refresh_counts()

        level, _ = confidence(result)
        sla = f"в течение {REQUEST_SLA_HOURS} ч"  # неразрывный пробел: «24 ч» не разрывается при переносе
        texts = {
            ANSWER: ("Ответ по действующему документу — высокая надёжность" if level == "high" else
                     "Ответ сверен с документом — основание по ссылке ниже"),
            CHECK: "Точный ответ не сформулирован — ниже текст подходящего пункта документа. "
                   "Если не помогло — нажмите «Передать специалисту».",
            CLARIFY: "Кто подписывает, зависит от суммы договора — укажите сумму ниже.",
            REVIEW: f"Ответ подготовлен и передан специалисту на проверку (обращение {request and request['id']}). "
                    f"Ответ придёт на почту {sla}.",
            ESCALATE: f"В действующих документах ответа нет. Вопрос передан специалисту (обращение "
                      f"{request and request['id']}), ответ придёт на почту {sla}.",
            BLOCKED: "Вопрос не принят: он похож на попытку обойти защиту. Сформулируйте рабочий вопрос иначе.",
        }
        colors = {ANSWER: CONFIDENCE[level] if level in ("high", "checked") else CONFIDENCE["checked"],
                  CHECK: CONFIDENCE["document"], CLARIFY: CONFIDENCE["document"], REVIEW: CONFIDENCE["checked"],
                  ESCALATE: CONFIDENCE["escalation"], BLOCKED: CONFIDENCE["blocked"]}
        banner = texts[decision] + (self._employee_email_hint() if needs_specialist(decision) else "")
        bg, fg = colors[decision]
        self.ask_banner.config(text="● " + banner, bg=bg, fg=fg)

        out = self.ask_output
        out.config(state="normal")
        out.delete("1.0", "end")
        basis_docs = result.get("basis_docs") or result.get("active_docs", [])[:1]
        inactive = result.get("inactive_docs", [])
        if decision in (ANSWER, CHECK, CLARIFY):
            out.insert("end", result.get("answer", ""))
            if basis_docs:
                self._insert_doc_links("Основание:", basis_docs, widget=out)
            if result.get("conflicts"):
                out.insert("end", "\nОбратите внимание:\n", "heading")
                for conflict in result["conflicts"]:
                    out.insert("end", f"• {conflict}\n", "warning")
        elif decision == REVIEW:
            out.insert("end", "Специалист проверит подготовленный ответ и пришлёт его вам.")
        elif decision == ESCALATE:
            out.insert("end", "В действующих приказах, регламентах и переписке ответа на этот вопрос нет.")
        else:
            out.insert("end", "Если это обычный рабочий вопрос — переформулируйте его или напишите специалисту: "
                              f"{ESCALATION_CONTACT}.")
        if inactive and decision != BLOCKED:
            self._insert_doc_links("Не применяются (устарели или ещё не действуют):", inactive, widget=out,
                                   note=lambda d: d.get("status_reason") or d.get("status", "устарел"))
        out.config(state="disabled")

        if decision in (ANSWER, CHECK, CLARIFY):
            label = result.get("responsible_label", "Ответственный")
            contacts = self.contact_text(result.get("responsible", ""))
            self.ask_responsible.config(text=f"{label}: {result.get('responsible', '—')}"
                                             + (f"  ·  {contacts}" if contacts else ""))
        elif needs_specialist(decision):
            contacts = self.contact_text(ESCALATION_CONTACT)
            self.ask_responsible.config(text=f"Передано: {ESCALATION_CONTACT}" + (f"  ·  {contacts}" if contacts else ""))
        else:
            self.ask_responsible.config(text="")

        answered = decision in (ANSWER, CHECK)
        self.ask_buttons["helped"].config(state="normal" if answered else "disabled")
        self.ask_buttons["not_helped"].config(state="normal" if answered else "disabled")
        self.ask_buttons["handoff"].config(state="normal" if decision in (ANSWER, CHECK, CLARIFY) else "disabled")
        self.ask_feedback.config(text="")

        if decision == CLARIFY:
            self.ask_clarify.pack(fill="x", pady=(8, 0), after=self.ask_banner)
            self.ask_amount.focus_set()
        else:
            self.ask_clarify.pack_forget()
        if not self.ask_result.winfo_manager():
            self.ask_result.pack(fill="both", expand=True, pady=(10, 0))
        latency = f"{float(result.get('latency') or 0):.1f}".replace(".", ",")
        took = "ответ из истории: база не менялась" if from_cache else f"ответ за {latency} с"
        self.ask_status.config(text=f"Готово — {took}", foreground="#16a34a" if decision != BLOCKED else "#dc2626")

    def on_ask_amount(self):
        """Сотрудник указал сумму — она добавляется к вопросу, ответ ищется заново."""
        amount_text = self.ask_amount.get().strip()
        if extract_amount(f"{amount_text} руб.") is None:
            messagebox.showwarning("Сумма", "Введите сумму, например: 300 000, 750 тыс., 1,2 млн.")
            return
        body = self.ask_input.get("1.0", "end").strip()
        self.ask_input.delete("1.0", "end")
        self.ask_input.insert("1.0", f"{body} Сумма договора: {amount_text} руб.")
        self.ask_amount.delete(0, "end")
        self.ask_question()

    def on_ask_helped(self, helped: bool, comment: str | None = None):
        if not self.ask_record:
            return
        if not helped and comment is None:
            comment = simpledialog.askstring("Что не так?", "Коротко: чего не хватило в ответе (можно оставить пустым)",
                                             parent=self.root) or ""
        mark_helped(self.ask_record["id"], helped, comment or "")
        self.ask_record["helped"] = helped
        self.ask_buttons["helped"].config(state="disabled")
        self.ask_buttons["not_helped"].config(state="disabled")
        self.ask_feedback.config(text="Спасибо! Отметка сохранена" if helped else
                                 "Отметка сохранена, ответ разберут. Нужен ответ человека — «Передать специалисту»")

    def on_ask_handoff(self, comment: str | None = None):
        """Сотрудник сам передаёт вопрос специалисту — обращение «Передано специалисту» во «Входящих»."""
        if not self.ask_record or self.ask_record.get("request_id"):
            return
        if comment is None:
            comment = simpledialog.askstring("Передать специалисту", "Что уточнить у специалиста? (можно оставить пустым)",
                                             parent=self.root)
            if comment is None:  # «Отмена»
                return
        request = hand_off(self.store, self.ask_employee.get().strip(), self.ask_question_data["body"], self.ask_last_result,
                           comment=comment or "сотрудник передал вопрос специалисту", escalate=True)
        mark_handoff(self.ask_record["id"], request["id"], "сотрудник передал сам")
        self.ask_record["request_id"] = request["id"]
        for button in self.ask_buttons.values():
            button.config(state="disabled")
        self.ask_feedback.config(text=f"Передано специалисту: обращение {request['id']}, ответ придёт на почту "
                                      f"в течение {REQUEST_SLA_HOURS} ч.{self._employee_email_hint()}")
        self.refresh_counts()

    # ---------- «Сводка»: окупается ли система ----------

    def _build_summary_view(self):
        view = self.views["summary"]
        head = ttk.Frame(view)
        head.pack(fill="x")
        tk.Label(head, text="Сводка", bg=PAGE, fg="#111827", font=self.f_title).pack(side="left")
        self.period_buttons = {}
        for name in reversed(list(PERIODS)):
            label = tk.Label(head, text=name, bg=PAGE, fg="#374151", font=self.f_text, padx=10, pady=4, cursor="hand2")
            label.pack(side="right")
            label.bind("<Button-1>", lambda e, n=name: self.set_summary_period(n))
            self.period_buttons[name] = label

        self.kpi, self.review_items = {}, {}

        def section(title, items):
            tk.Label(view, text=title, bg=PAGE, fg="#111827", font=self.f_bold, anchor="w").pack(fill="x", pady=(12, 6))
            row = tk.Frame(view, bg=PAGE)
            row.pack(fill="x")
            for key, caption in items:
                tile = tk.Frame(row, bg=CARD, highlightthickness=1, highlightbackground=LINE)
                tile.pack(side="left", fill="both", expand=True, padx=(0, self.px(10)))
                value = tk.Label(tile, text="—", bg=CARD, fg="#111827", font=self.f_kpi, anchor="w")
                value.pack(fill="x", padx=self.px(14), pady=(self.px(10), 0))
                tk.Label(tile, text=caption, bg=CARD, fg="#111827", font=self.f_text, anchor="w", justify="left",
                         wraplength=self.px(160)).pack(fill="x", padx=self.px(14))
                note = tk.Label(tile, text="", bg=CARD, fg=MUTED, font=self.f_small, anchor="w")
                note.pack(fill="x", padx=self.px(14), pady=(self.px(2), self.px(10)))
                self.kpi[key] = (value, note)

        section("Сотрудники спрашивают сами", [
            ("ss_total", "Вопросов задано"), ("ss_closed", "Закрыто сразу, без специалиста"),
            ("ss_handed", "Передано специалисту"), ("ss_helped", "Отметили «Помогло»"),
            ("ss_hours", "Сэкономлено времени сотрудников"), ("ss_latency", "Среднее время ответа")])
        section("Обращения по почте (работа оператора)", [
            ("in_total", "Обращений"), ("in_sent", "Ответ отправлен"), ("in_open", "Ещё не закрыто"),
            ("in_escalated", "Ждут специалиста"), ("in_overdue", "Просрочено")])
        self.summary_note = tk.Label(view, text="", bg=PAGE, fg=HINT_COLOR, font=self.f_text, anchor="w", justify="left",
                                     wraplength=self.px(1050))
        self.summary_note.pack(fill="x", pady=(10, 0))

        tk.Label(view, text="Разобрать: ответы «Не помогло» и вопросы, переданные специалисту", bg=PAGE, fg="#111827",
                 font=self.f_bold, anchor="w").pack(fill="x", pady=(12, 4))
        columns = ("time", "employee", "question", "what")
        self.review_tree = ttk.Treeview(view, columns=columns, show="headings", selectmode="browse",
                                        style="Inbox.Treeview", height=5)
        for column, title, width in (("time", "Когда", 140), ("employee", "Кто", 200), ("question", "Вопрос", 420),
                                     ("what", "Что произошло", 330)):
            self.review_tree.heading(column, text=title, anchor="w")
            self.review_tree.column(column, width=self.px(width), anchor="w", stretch=column == "question")
        self.review_tree.pack(fill="both", expand=True)
        self.review_tree.bind("<Double-1>", lambda e: self.open_review_item())
        self.review_empty = tk.Label(view, text="", bg=PAGE, fg=HINT_COLOR, font=self.f_text, anchor="w")
        self.review_empty.pack(fill="x", pady=(4, 0))

    def set_summary_period(self, name: str):
        self.summary_period = name
        self.refresh_summary()

    def refresh_summary(self):
        records = load_records()
        s = summary(records, self.summary_period)
        self.store.reload()
        i = inbox_summary(self.store, self.summary_period)

        def put(key, value, note=""):
            self.kpi[key][0].config(text=str(value))
            self.kpi[key][1].config(text=note)

        number = lambda x: f"{x:g}".replace(".", ",")  # noqa: E731 — 12.5 → «12,5»
        put("ss_total", s["total"], f"отклонено защитой: {s['blocked']}" if s["blocked"] else "")
        put("ss_closed", s["closed"], f"{s['closed_share']} % вопросов")
        put("ss_handed", s["handed"], f"{s['handed_share']} % вопросов")
        put("ss_helped", f"{s['helped_share']} %" if s["helped_share"] is not None else "—",
            f"из {s['rated']} отмеченных ответов" if s["rated"] else "отметок пока нет")
        put("ss_hours", f"{number(s['hours_saved'])} ч", f"по {BASELINE_SEARCH_MINUTES} мин на вопрос")
        put("ss_latency", f"{number(s['avg_latency'])} с" if s["avg_latency"] is not None else "—", "вместо 30 мин – 2 ч")
        put("in_total", i["total"])
        put("in_sent", i["sent"])
        put("in_open", i["open"], f"ответ готов: {i['ready']}" if i["ready"] else "")
        put("in_escalated", i["escalated"])
        put("in_overdue", i["overdue"], "срок ответа — 24 ч" if i["overdue"] else "")
        self.kpi["in_overdue"][0].config(fg="#dc2626" if i["overdue"] else "#111827")

        self.summary_note.config(text=(
            f"Цель проекта — вернуть сотрудникам 50–300 человеко-часов в месяц, которые уходят на поиск, и сократить "
            f"долю вопросов без ответа (было до 40 %). Сейчас без ответа сразу остаются {s['handed_share']} % вопросов — "
            f"они переданы специалисту. Экономия — оценка: {BASELINE_SEARCH_MINUTES} мин ручного поиска на каждый "
            f"вопрос, закрытый без специалиста (настройка BASELINE_SEARCH_MINUTES)."))
        for name, label in self.period_buttons.items():
            active = name == self.summary_period
            label.config(bg="#e0e7ef" if active else PAGE, font=self.f_bold if active else self.f_text)

        self.review_tree.delete(*self.review_tree.get_children())
        self.review_items = {}
        for record in to_review(records):
            if record.get("helped") is False:
                what = "Не помогло" + (f": {record['comment']}" if record.get("comment") else "")
            else:
                what = record["decision"].capitalize()
            if record.get("request_id"):
                what += f" → {record['request_id']}"
            employee = (record.get("employee") or "").split("<")[0].strip().strip('"') or record.get("employee") or "—"
            item = self.review_tree.insert("", "end", values=(
                record["time"].replace("T", " ")[:16], employee, record["question"][:150], what))
            self.review_items[item] = record
        self.review_empty.config(text="" if self.review_items else
                                 "Пока разбирать нечего: все вопросы закрыты без специалиста.")

    def open_review_item(self):
        selection = self.review_tree.selection()
        record = self.review_items.get(selection[0]) if selection else None
        if record and record.get("request_id") and self.store.get(record["request_id"]):
            self.open_request(record["request_id"], auto_search=False)

    # ---------- справочник «Сотрудники» ----------

    ALL_DEPARTMENTS = "Все подразделения"

    def _build_employees_view(self):
        view = self.views["employees"]
        self._view_title(view, "Сотрудники")
        ttk.Label(view, text="Справочник по подразделениям: ФИО, должность, e-mail, телефон. Контакты ответственного "
                             "из справочника показываются в ответе и добавляются в письмо.",
                  foreground=HINT_COLOR, font=self.f_text, wraplength=self.px(1050), justify="left").pack(anchor="w", pady=(0, 8))

        bar = ttk.Frame(view)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Подразделение:").pack(side="left")
        self.department_filter = ttk.Combobox(bar, state="readonly", width=34, font=self.f_text)
        self.department_filter.pack(side="left", padx=(4, 12))
        self.department_filter.bind("<<ComboboxSelected>>", lambda e: self.refresh_employees())
        ttk.Label(bar, text="Поиск:").pack(side="left")
        self.employee_search = tk.StringVar()
        self.employee_search.trace_add("write", lambda *a: self.refresh_employees())
        tk.Entry(bar, textvariable=self.employee_search, font=self.f_text, width=30, relief="solid",
                 borderwidth=1).pack(side="left", padx=(4, 12))
        editable = accounts.can(self.user, "edit_employees")  # править справочник — право администратора
        if editable:
            ttk.Button(bar, text="Открыть файл справочника",
                       command=lambda: self._open_path(str(directory.EMPLOYEES_FILE))).pack(side="right")
            self._toolbar(view, (("add", "Добавить", lambda: self.edit_employee(None)),
                                 ("edit", "Изменить", self.edit_selected_employee),
                                 ("delete", "Удалить", self.delete_selected_employee)))

        # Подписи под списком пакуются до него: при нехватке места сжимается список, а не подписи
        ttk.Label(view, text="Двойной щелчок — изменить карточку сотрудника. Delete — удалить." if editable else
                  "Справочник изменяет администратор.", style="Sub.TLabel").pack(side="bottom", anchor="w", pady=(2, 0))
        self.employees_empty = tk.Label(view, text="", bg=PAGE, fg=HINT_COLOR, font=self.f_text, anchor="w")
        self.employees_empty.pack(side="bottom", fill="x", pady=(4, 0))
        holder = ttk.Frame(view)
        holder.pack(fill="both", expand=True, pady=(2, 0))
        self.employees_tree = ttk.Treeview(holder, columns=("position", "email", "phone"), show="tree headings",
                                           selectmode="browse", style="Inbox.Treeview")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.employees_tree.yview)
        self.employees_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.employees_tree.heading("#0", text="Подразделение / ФИО", anchor="w")
        self.employees_tree.column("#0", width=self.px(290), minwidth=self.px(200), stretch=True)
        for column, title, width in (("position", "Должность", 260), ("email", "E-mail", 210), ("phone", "Телефон", 250)):
            self.employees_tree.heading(column, text=title, anchor="w")
            self.employees_tree.column(column, width=self.px(width), minwidth=self.px(width), anchor="w", stretch=False)
        self.employees_tree.tag_configure("department", font=self.f_row_bold, background="#f3f4f6")
        self.employees_tree.pack(side="left", fill="both", expand=True)
        if editable:
            self.employees_tree.bind("<Double-1>", lambda e: self.edit_selected_employee())
            self.employees_tree.bind("<Delete>", lambda e: self.delete_selected_employee())
        self.employee_items = {}
        self.directory = directory.load_directory()
        self.refresh_employees()

    def refresh_employees(self):
        names = [d["department"] for d in self.directory]
        self.department_filter.config(values=[self.ALL_DEPARTMENTS] + names)
        if self.department_filter.get() not in names:
            self.department_filter.set(self.ALL_DEPARTMENTS)
        chosen = self.department_filter.get()
        shown = directory.search(self.directory, self.employee_search.get())
        if chosen != self.ALL_DEPARTMENTS:
            shown = [d for d in shown if d["department"] == chosen]

        tree = self.employees_tree
        tree.delete(*tree.get_children())
        self.employee_items = {}
        for n, department in enumerate(shown):
            parent = tree.insert("", "end", iid=f"dep{n}", text=department["department"], open=True, tags=("department",))
            for m, person in enumerate(department["employees"]):
                item = tree.insert(parent, "end", iid=f"dep{n}emp{m}", text=person["fio"],
                                   values=(person["position"], person["email"], person["phone"]))
                self.employee_items[item] = dict(person, department=department["department"])
        self.employees_empty.config(text="" if shown else "Никого не найдено — измените поиск или подразделение.")

    def selected_employee(self) -> dict | None:
        selection = self.employees_tree.selection()
        return self.employee_items.get(selection[0]) if selection else None

    def edit_selected_employee(self):
        person = self.selected_employee()
        if person is None:
            messagebox.showinfo("Сотрудники", "Выберите сотрудника в списке.", parent=self.root)
            return
        self.edit_employee(person)

    def delete_selected_employee(self):
        person = self.selected_employee()
        if person is None:
            messagebox.showinfo("Сотрудники", "Выберите сотрудника в списке.", parent=self.root)
            return
        if messagebox.askyesno("Удалить сотрудника", f"Удалить из справочника: {person['fio']} "
                                                     f"({person['department']})?", parent=self.root):
            self.save_employee(None, old=person)

    def _toolbar(self, parent, items) -> tk.Frame:
        """Серая панель кнопок «значок + подпись» (как во «Входящих»)."""
        toolbar = tk.Frame(parent, bg="#f3f4f6")
        toolbar.pack(fill="x")
        for icon, text, command in items:
            button = tk.Frame(toolbar, bg="#f3f4f6", cursor="hand2")
            button.pack(side="left", padx=(self.px(10), self.px(6)), pady=self.px(6))
            parts = (tk.Label(button, text=self.icon(icon), bg="#f3f4f6", fg=ICON_COLOR, font=self.f_icon_small),
                     tk.Label(button, text=text, bg="#f3f4f6", fg="#1f2937", font=self.f_text))
            parts[0].pack(side="left", padx=(0, 6))
            parts[1].pack(side="left")
            for widget in (button, *parts):
                widget.bind("<Button-1>", lambda e, c=command: c())
                widget.bind("<Enter>", lambda e, p=(button, *parts): [w.config(bg="#e5e7eb") for w in p])
                widget.bind("<Leave>", lambda e, p=(button, *parts): [w.config(bg="#f3f4f6") for w in p])
        return toolbar

    def save_employee(self, person: dict | None, old: dict | None = None) -> list[str]:
        """Добавить (old=None), изменить (оба) или удалить (person=None). Возвращает ошибки заполнения."""
        if not accounts.can(self.user, "edit_employees"):
            return ["справочник изменяет администратор"]
        if person is not None:
            errors = directory.validate(person)
            if errors:
                return errors
            directory.upsert(self.directory, person, old)
        elif old is not None:
            directory.remove(self.directory, old)
        directory.save_directory(self.directory)
        action = "сотрудник добавлен" if old is None else "сотрудник удалён" if person is None else "сотрудник изменён"
        accounts.audit(self.user.get("login", ""), action, (person or old or {}).get("fio", ""))
        self.refresh_employees()
        return []

    def edit_employee(self, person: dict | None):
        """Карточка сотрудника: новая (person=None) или изменение существующей."""
        win = tk.Toplevel(self.root)
        win.title("Новый сотрудник" if person is None else f"Сотрудник: {person['fio']}")
        win.configure(bg=CARD)
        win.transient(self.root)
        win.resizable(False, False)
        entries = {}
        for row, key in enumerate(("department", *directory.FIELDS)):
            tk.Label(win, text=directory.FIELD_NAMES[key] + ":", bg=CARD, font=self.f_bold, anchor="w").grid(
                row=row, column=0, sticky="w", padx=(self.px(14), self.px(8)), pady=self.px(5))
            if key == "department":
                widget = ttk.Combobox(win, values=[d["department"] for d in self.directory], width=44, font=self.f_text)
            else:
                widget = tk.Entry(win, width=46, font=self.f_text, relief="solid", borderwidth=1)
            widget.grid(row=row, column=1, sticky="we", padx=(0, self.px(14)), pady=self.px(5))
            widget.insert(0, (person or {}).get(key, ""))
            entries[key] = widget
        error = tk.Label(win, text="", bg=CARD, fg="#dc2626", font=self.f_text, anchor="w", justify="left")
        error.grid(row=len(entries), column=0, columnspan=2, sticky="we", padx=self.px(14))
        buttons = tk.Frame(win, bg=CARD)
        buttons.grid(row=len(entries) + 1, column=0, columnspan=2, sticky="e", padx=self.px(14), pady=(self.px(4), self.px(12)))

        def save():
            card = {key: widget.get().strip() for key, widget in entries.items()}
            errors = self.save_employee(card, old=person)
            if errors:
                error.config(text="Проверьте: " + "; ".join(errors) + ".")
                return
            win.destroy()

        tk.Button(buttons, text="Сохранить (Enter)", bg="#2563eb", fg="white", font=self.f_bold, relief="flat",
                  padx=14, pady=4, cursor="hand2", command=save).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Отмена (Esc)", command=win.destroy).pack(side="left")
        win.bind("<Return>", lambda e: save() or "break")
        win.bind("<Escape>", lambda e: win.destroy() or "break")
        entries["department" if person is None else "fio"].focus_set()
        self.employee_dialog = win  # для тестов
        try:
            win.grab_set()
        except tk.TclError:  # окно ещё не показано (скрытый режим тестов)
            pass

    def contact_text(self, responsible: str) -> str:
        """Контакты ответственного из справочника; ФИО не повторяется, если оно уже есть в подписи."""
        person = directory.find_person(responsible, self.directory)
        if not person:
            return ""
        surname = person["fio"].split()[0]
        parts = ([] if surname in (responsible or "") else [person["fio"]]) + [p for p in (person["email"], person["phone"]) if p]
        return " · ".join(parts)

    # ---------- новые документы в базе знаний (администратор) ----------

    def add_document(self, path: str | None = None):
        """«＋ Добавить документ»: копия файла в папку базы → база обновляется сама → карточка документа."""
        if not accounts.can(self.user, "add_documents"):
            messagebox.showwarning("Нет прав", "Документы в базу добавляет администратор.", parent=self.root)
            return None
        path = path or filedialog.askopenfilename(
            title="Документ для базы знаний",
            filetypes=[("Документы", " ".join("*" + s for s in admin.SUPPORTED_DOCS)), ("Все файлы", "*.*")])
        if not path:
            return None
        try:
            target = admin.add_document_file(Path(path), KNOWLEDGE_DIR)
        except FileExistsError as exc:
            if not messagebox.askyesno("Документ уже есть", f"В базе уже есть файл «{exc}». Заменить его новой версией?",
                                       parent=self.root):
                return None
            target = admin.add_document_file(Path(path), KNOWLEDGE_DIR, overwrite=True)
        except ValueError as exc:
            messagebox.showwarning("Документ не добавлен", str(exc), parent=self.root)
            return None
        accounts.audit(self.user.get("login", ""), "добавлен документ", target.name)
        self.refresh_sources()  # база пересобирается: папка изменилась
        doc = next((d for d in get_knowledge_base().documents if Path(d["path"]) == target), None)
        if doc is None:
            messagebox.showwarning("Документ не прочитан", f"Файл «{target.name}» скопирован, но прочитать его не удалось. "
                                                           f"Подробности — в журнале {LOG_FILE}.", parent=self.root)
            return target
        self.edit_document_card(target, doc)
        return target

    def edit_document_card(self, target: Path, doc: dict):
        """Карточка документа: что система определила сама — можно поправить (сохраняется в «<файл>.meta.json»)."""
        win = tk.Toplevel(self.root)
        win.title(f"Карточка документа: {target.name}")
        win.configure(bg=CARD)
        win.transient(self.root)
        win.resizable(False, False)
        tk.Label(win, text="Система определила данные документа сама. Проверьте и при необходимости поправьте.\n"
                           "Дата вступления в силу и срок пересмотра — если есть.",
                 bg=CARD, fg=HINT_COLOR, font=self.f_text, justify="left").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=self.px(14), pady=(self.px(10), self.px(6)))
        values = {"type": doc.get("source_type", ""), "number": doc.get("number", ""), "title": doc.get("title", ""),
                  "date": doc.get("date", ""), "owner": doc.get("owner", ""),
                  "effective_from": doc.get("effective_from") or "", "review_date": doc.get("review_date") or ""}
        names = {"type": "Вид документа", "number": "Номер", "title": "Название", "date": "Дата",
                 "owner": "Владелец (контакт)", "effective_from": "Вступает в силу", "review_date": "Пересмотреть до"}
        entries = {}
        for row, key in enumerate(admin.CARD_FIELDS, 1):
            tk.Label(win, text=names[key] + ":", bg=CARD, font=self.f_bold, anchor="w").grid(
                row=row, column=0, sticky="w", padx=(self.px(14), self.px(8)), pady=self.px(4))
            if key == "type":
                widget = ttk.Combobox(win, values=sorted({t for g in SOURCE_GROUPS.values() for t in g}), width=50,
                                      font=self.f_text)
            elif key == "owner":
                people = [f"{p['position']} {p['fio']}" for p in directory.all_employees(self.directory)]
                widget = ttk.Combobox(win, values=people, width=50, font=self.f_text)
            else:
                widget = tk.Entry(win, width=52, font=self.f_text, relief="solid", borderwidth=1)
            widget.grid(row=row, column=1, sticky="we", padx=(0, self.px(14)), pady=self.px(4))
            widget.insert(0, values[key])
            entries[key] = widget
        buttons = tk.Frame(win, bg=CARD)
        buttons.grid(row=len(entries) + 1, column=0, columnspan=2, sticky="e", padx=self.px(14), pady=self.px(12))

        def save():
            card = {k: w.get().strip() for k, w in entries.items()}
            # В карточку — только то, что отличается от определённого автоматически (остальное система найдёт сама)
            changed = {k: v for k, v in card.items() if v != (values[k] or "")}
            admin.write_card(target, changed)
            accounts.audit(self.user.get("login", ""), "карточка документа", f"{target.name}: {', '.join(changed) or 'без изменений'}")
            self.refresh_sources()
            win.destroy()

        tk.Button(buttons, text="Сохранить (Enter)", bg="#2563eb", fg="white", font=self.f_bold, relief="flat",
                  padx=14, pady=4, cursor="hand2", command=save).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Оставить как определено (Esc)", command=win.destroy).pack(side="left")
        win.bind("<Return>", lambda e: save() or "break")
        win.bind("<Escape>", lambda e: win.destroy() or "break")
        self.document_dialog = win
        self.document_card_entries = entries

    # ---------- «Администрирование» (только администратор) ----------

    def _build_admin_view(self):
        view = self.views["admin"]
        head = ttk.Frame(view)
        head.pack(fill="x")
        tk.Label(head, text="Администрирование", bg=PAGE, fg="#111827", font=self.f_title).pack(side="left")
        self.admin_tabs = ttk.Notebook(view)
        self.admin_tabs.pack(fill="both", expand=True, pady=(8, 0))

        # Учётные записи
        tab = ttk.Frame(self.admin_tabs, padding=8)
        self.admin_tabs.add(tab, text="  Учётные записи  ")
        self._toolbar(tab, (("add", "Добавить", lambda: self.edit_account(None)),
                            ("edit", "Изменить", self.edit_selected_account),
                            ("key", "Сменить пароль", self.change_selected_password),
                            ("lock", "Заблокировать / разблокировать", self.toggle_selected_account),
                            ("delete", "Удалить", self.delete_selected_account)))
        rights = ("Администратор — всё, что может пользователь, а также учётные записи, добавление документов в базу, "
                  "справочник «Сотрудники», обновление версии и архив для переноса.  Пользователь — вопросы, обращения, "
                  "просмотр документов и справочника.")
        ttk.Label(tab, text=rights, foreground=HINT_COLOR, font=self.f_text, wraplength=self.px(1000),
                  justify="left").pack(side="bottom", anchor="w", pady=(6, 0))  # до списка: список сжимается, подпись — нет
        self.accounts_tree = ttk.Treeview(tab, columns=("login", "name", "role", "state", "last"), show="headings",
                                          selectmode="browse", style="Inbox.Treeview", height=6)
        for column, title, width in (("login", "Логин", 160), ("name", "ФИО", 330), ("role", "Роль", 170),
                                     ("state", "Состояние", 150), ("last", "Последний вход", 190)):
            self.accounts_tree.heading(column, text=title, anchor="w")
            self.accounts_tree.column(column, width=self.px(width), anchor="w", stretch=column == "name")
        self.accounts_tree.tag_configure("blocked", foreground="#9ca3af")
        self.accounts_tree.pack(fill="both", expand=True, pady=(2, 0))
        self.accounts_tree.bind("<Double-1>", lambda e: self.edit_selected_account())

        # Версия и обновление
        tab = ttk.Frame(self.admin_tabs, padding=12)
        self.admin_tabs.add(tab, text="  Версия и обновление  ")
        # Номер версии — только здесь (у администратора); на остальных экранах и в окне входа его нет
        self.version_label = tk.Label(tab, text="", bg=PAGE, fg="#111827", font=self.f_bold, anchor="w")
        self.version_label.pack(fill="x", pady=(0, 6))
        ttk.Label(tab, text="Пакет обновления — папка «Проект» новой версии или .zip-архив с ней. Перед заменой текущая "
                            "версия сохраняется — к ней можно откатиться. База знаний, обращения, справочники, учётные "
                            "записи и настройки при обновлении не меняются.", foreground=HINT_COLOR, font=self.f_text,
                  wraplength=self.px(1000), justify="left").pack(anchor="w")
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(10, 6))
        ttk.Button(row, text="Обновить из папки…", command=lambda: self.update_system(folder=True)).pack(side="left")
        ttk.Button(row, text="Обновить из архива .zip…", command=lambda: self.update_system(folder=False)).pack(side="left", padx=8)
        ttk.Button(row, text="Откатить к выбранной копии", command=self.rollback_selected).pack(side="left")
        tk.Label(tab, text="Сохранённые версии (резервные копии перед обновлением):", bg=PAGE, font=self.f_bold,
                 anchor="w").pack(fill="x", pady=(8, 2))
        self.backups_tree = ttk.Treeview(tab, columns=("version", "created", "note"), show="headings",
                                         selectmode="browse", style="Inbox.Treeview", height=6)
        for column, title, width in (("version", "Версия", 120), ("created", "Когда сохранена", 220), ("note", "Зачем", 500)):
            self.backups_tree.heading(column, text=title, anchor="w")
            self.backups_tree.column(column, width=self.px(width), anchor="w", stretch=column == "note")
        self.backups_tree.pack(fill="both", expand=True)
        self.update_status = ttk.Label(tab, text="", foreground="#166534", font=self.f_text, wraplength=self.px(1000),
                                       justify="left")
        self.update_status.pack(anchor="w", pady=(6, 0))

        # Перенос на другой компьютер
        tab = ttk.Frame(self.admin_tabs, padding=12)
        self.admin_tabs.add(tab, text="  Перенос на другой компьютер  ")
        ttk.Label(tab, text="Архив для переноса — один .zip: папка «Проект» (программа, база знаний, входящие, справочники, "
                            "обращения, история, учётные записи, настройки) и папка «Документы» с файлами Word: роли и "
                            "пользователи с логинами, инструкция по переносу с перечнем нужных программ, перечень файлов "
                            "с контрольными суммами. Пароли в документы не попадают.", foreground=HINT_COLOR,
                  font=self.f_text, wraplength=self.px(1000), justify="left").pack(anchor="w")
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(10, 6))
        tk.Button(row, text="Создать архив для переноса…", bg="#2563eb", fg="white", font=self.f_bold, relief="flat",
                  padx=14, pady=4, cursor="hand2", command=self.export_archive).pack(side="left")
        ttk.Button(row, text="Проверить этот компьютер", command=self.refresh_system_check).pack(side="left", padx=8)
        self.archive_status = ttk.Label(tab, text="", foreground="#166534", font=self.f_text, wraplength=self.px(1000),
                                        justify="left")
        self.archive_status.pack(anchor="w", pady=(0, 6))
        tk.Label(tab, text="Что нужно на компьютере (проверка этого компьютера):", bg=PAGE, font=self.f_bold,
                 anchor="w").pack(fill="x", pady=(4, 2))
        self.check_tree = ttk.Treeview(tab, columns=("state", "item", "found", "required"), show="headings",
                                       selectmode="none", style="Inbox.Treeview", height=9)
        for column, title, width in (("state", "", 60), ("item", "Что", 330), ("found", "На этом компьютере", 260),
                                     ("required", "Нужно", 330)):
            self.check_tree.heading(column, text=title, anchor="w")
            self.check_tree.column(column, width=self.px(width), anchor="w", stretch=column == "required")
        self.check_tree.tag_configure("bad", foreground="#dc2626")
        self.check_tree.pack(fill="both", expand=True)

        # Журнал действий
        tab = ttk.Frame(self.admin_tabs, padding=8)
        self.admin_tabs.add(tab, text="  Журнал  ")
        self.audit_tree = ttk.Treeview(tab, columns=("time", "user", "action", "detail"), show="headings",
                                       selectmode="none", style="Inbox.Treeview")
        for column, title, width in (("time", "Когда", 180), ("user", "Кто", 140), ("action", "Действие", 260),
                                     ("detail", "Подробности", 480)):
            self.audit_tree.heading(column, text=title, anchor="w")
            self.audit_tree.column(column, width=self.px(width), anchor="w", stretch=column == "detail")
        self.audit_tree.pack(fill="both", expand=True)
        self.admin_tabs.bind("<<NotebookTabChanged>>", lambda e: self.refresh_admin())

    def refresh_admin(self):
        if not hasattr(self, "accounts_tree"):
            return
        self.accounts_tree.delete(*self.accounts_tree.get_children())
        for user in self.user_store.users:
            active = user.get("active", True)
            self.accounts_tree.insert("", "end", iid=f"user::{user['login']}", tags=() if active else ("blocked",), values=(
                user["login"], user.get("name", ""), user["role"], "активна" if active else "заблокирована",
                user.get("last_login") or "—"))
        self.backups_tree.delete(*self.backups_tree.get_children())
        for backup in admin.list_backups():
            self.backups_tree.insert("", "end", iid=backup["path"], values=(
                backup["version"], backup["created"].replace("T", " "), backup["note"]))
        self.audit_tree.delete(*self.audit_tree.get_children())
        try:
            lines = self.user_store.audit_path.read_text(encoding="utf-8").splitlines()[-200:]
        except FileNotFoundError:
            lines = []
        for line in reversed(lines):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.audit_tree.insert("", "end", values=(record.get("time", "").replace("T", " "), record.get("user", ""),
                                                      record.get("action", ""), record.get("detail", "")))
        self.version_label.config(text=f"Установлена версия {admin.read_version()}")

    def refresh_system_check(self):
        self.check_tree.delete(*self.check_tree.get_children())
        for row in admin.system_check():
            self.check_tree.insert("", "end", tags=() if row["ok"] else ("bad",), values=(
                "OK" if row["ok"] else "НЕТ", row["item"], row["found"], row["required"]))

    # учётные записи

    def _selected_account(self) -> dict | None:
        selection = self.accounts_tree.selection()
        return self.user_store.get(selection[0].split("::", 1)[1]) if selection else None

    def _need_account(self) -> dict | None:
        user = self._selected_account()
        if user is None:
            messagebox.showinfo("Учётные записи", "Выберите учётную запись в списке.", parent=self.root)
        return user

    def save_account(self, login: str, name: str, role: str, password: str | None = None,
                     existing: dict | None = None) -> list[str]:
        if not accounts.can(self.user, "manage_users"):
            return ["учётными записями управляет администратор"]
        actor = self.user.get("login", "")
        if existing is None:
            errors = self.user_store.add(login, name, role, password or "", actor=actor)
        else:
            errors = self.user_store.update(existing["login"], actor, name=name, role=role)
        self.refresh_admin()
        return errors

    def edit_account(self, existing: dict | None):
        """Карточка учётной записи: логин, ФИО (из справочника или вручную), роль; пароль — для новой."""
        win = tk.Toplevel(self.root)
        win.title("Новая учётная запись" if existing is None else f"Учётная запись: {existing['login']}")
        win.configure(bg=CARD)
        win.transient(self.root)
        win.resizable(False, False)
        fields = [("login", "Логин"), ("name", "ФИО"), ("role", "Роль")]
        if existing is None:
            fields += [("password", "Пароль"), ("password2", "Пароль ещё раз")]
        entries = {}
        for row, (key, title) in enumerate(fields):
            tk.Label(win, text=title + ":", bg=CARD, font=self.f_bold, anchor="w").grid(
                row=row, column=0, sticky="w", padx=(self.px(14), self.px(8)), pady=self.px(5))
            if key == "role":
                widget = ttk.Combobox(win, values=list(accounts.ROLES), state="readonly", width=40, font=self.f_text)
                widget.set((existing or {}).get("role", accounts.USER))
            elif key == "name":
                widget = ttk.Combobox(win, values=[p["fio"] for p in directory.all_employees(self.directory)], width=40,
                                      font=self.f_text)
                widget.insert(0, (existing or {}).get("name", ""))
            else:
                widget = tk.Entry(win, width=42, font=self.f_text, relief="solid", borderwidth=1,
                                  show="•" if key.startswith("password") else "")
                widget.insert(0, (existing or {}).get(key, "") if key == "login" else "")
                if key == "login" and existing is not None:
                    widget.config(state="disabled")
            widget.grid(row=row, column=1, sticky="we", padx=(0, self.px(14)), pady=self.px(5))
            entries[key] = widget
        hint = tk.Label(win, text=f"Пароль — не короче {accounts.MIN_PASSWORD} символов, буквы и цифры. "
                                  "Пароль хранится только в виде хеша." if existing is None else "",
                        bg=CARD, fg=HINT_COLOR, font=self.f_small, anchor="w")
        hint.grid(row=len(fields), column=0, columnspan=2, sticky="w", padx=self.px(14))
        if existing is None:
            # «Придумать пароль»: случайный пароль сразу в оба поля и на экран — передать пользователю лично
            shown = tk.Label(win, text="", bg=CARD, fg="#166534", font=self.f_bold, anchor="w")
            shown.grid(row=len(fields) + 1, column=0, columnspan=3, sticky="w", padx=self.px(14))

            def generate():
                password = accounts.generate_password()
                for key in ("password", "password2"):
                    entries[key].delete(0, "end")
                    entries[key].insert(0, password)
                shown.config(text=f"Пароль: {password} — передайте его пользователю лично")
            ttk.Button(win, text="Придумать пароль", command=generate).grid(
                row=fields.index(("password", "Пароль")), column=2, padx=(0, self.px(14)))
        error = tk.Label(win, text="", bg=CARD, fg="#dc2626", font=self.f_text, anchor="w", justify="left",
                         wraplength=self.px(520))
        error.grid(row=len(fields) + 2, column=0, columnspan=3, sticky="we", padx=self.px(14))
        buttons = tk.Frame(win, bg=CARD)
        buttons.grid(row=len(fields) + 3, column=0, columnspan=3, sticky="e", padx=self.px(14), pady=self.px(12))

        def save():
            values = {k: w.get() for k, w in entries.items()}
            if existing is None and values["password"] != values["password2"]:
                error.config(text="Проверьте: пароли не совпадают.")
                return
            errors = self.save_account(values["login"], values["name"], values["role"], values.get("password"), existing)
            if errors:
                error.config(text="Проверьте: " + "; ".join(errors) + ".")
                return
            win.destroy()

        tk.Button(buttons, text="Сохранить (Enter)", bg="#2563eb", fg="white", font=self.f_bold, relief="flat",
                  padx=14, pady=4, cursor="hand2", command=save).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Отмена (Esc)", command=win.destroy).pack(side="left")
        win.bind("<Return>", lambda e: save() or "break")
        win.bind("<Escape>", lambda e: win.destroy() or "break")
        self.account_dialog = win

    def edit_selected_account(self):
        user = self._need_account()
        if user:
            self.edit_account(user)

    def _password_dialog(self, title: str, intro: str, need_old: bool, on_save, allow_generate: bool):
        """Окно смены пароля. need_old — свой пароль (нужен текущий); allow_generate — «Придумать пароль»
        (для администратора: пароль показывается, чтобы передать его пользователю лично)."""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=CARD)
        win.transient(self.root)
        win.resizable(False, False)
        tk.Label(win, text=intro, bg=CARD, font=self.f_text, justify="left", wraplength=self.px(520)).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=self.px(14), pady=(self.px(10), self.px(6)))
        fields = ([("old", "Текущий пароль")] if need_old else []) + [("new", "Новый пароль"), ("new2", "Ещё раз")]
        entries = {}
        for row, (key, name) in enumerate(fields, 1):
            tk.Label(win, text=name + ":", bg=CARD, font=self.f_bold, anchor="w").grid(
                row=row, column=0, sticky="w", padx=(self.px(14), self.px(8)), pady=self.px(4))
            entry = tk.Entry(win, width=30, font=self.f_text, relief="solid", borderwidth=1, show="•")
            entry.grid(row=row, column=1, sticky="we", pady=self.px(4), padx=(0, 0 if allow_generate else self.px(14)))
            entries[key] = entry
        shown = tk.Label(win, text="", bg=CARD, fg="#166534", font=self.f_bold, anchor="w")
        if allow_generate:
            def generate():
                password = accounts.generate_password()
                for key in ("new", "new2"):
                    entries[key].delete(0, "end")
                    entries[key].insert(0, password)
                shown.config(text=f"Пароль: {password} — передайте его пользователю лично")
            ttk.Button(win, text="Придумать пароль", command=generate).grid(
                row=len(fields) - 1, column=2, padx=(self.px(8), self.px(14)))
        shown.grid(row=len(fields) + 1, column=0, columnspan=3, sticky="w", padx=self.px(14))
        tk.Label(win, text=f"Не короче {accounts.MIN_PASSWORD} символов, буквы и цифры.", bg=CARD, fg=HINT_COLOR,
                 font=self.f_small, anchor="w").grid(row=len(fields) + 2, column=0, columnspan=3, sticky="w", padx=self.px(14))
        error = tk.Label(win, text="", bg=CARD, fg="#dc2626", font=self.f_text, anchor="w", justify="left",
                         wraplength=self.px(520))
        error.grid(row=len(fields) + 3, column=0, columnspan=3, sticky="w", padx=self.px(14))
        buttons = tk.Frame(win, bg=CARD)
        buttons.grid(row=len(fields) + 4, column=0, columnspan=3, sticky="e", padx=self.px(14), pady=self.px(12))

        def save():
            values = {k: e.get() for k, e in entries.items()}
            if values["new"] != values["new2"]:
                error.config(text="Проверьте: новые пароли не совпадают.")
                return
            errors = on_save(values.get("old", ""), values["new"])
            if errors:
                error.config(text="Проверьте: " + "; ".join(errors) + ".")
                return
            win.destroy()

        tk.Button(buttons, text="Сохранить (Enter)", bg="#2563eb", fg="white", font=self.f_bold, relief="flat",
                  padx=14, pady=4, cursor="hand2", command=save).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Отмена (Esc)", command=win.destroy).pack(side="left")
        win.bind("<Return>", lambda e: save() or "break")
        win.bind("<Escape>", lambda e: win.destroy() or "break")
        entries[fields[0][0]].focus_set()
        self.password_dialog, self.password_entries = win, entries
        return win

    def change_own_password(self):
        """«Сменить свой пароль» — любой вошедший пользователь (нужен текущий пароль)."""
        login = self.user.get("login")
        if not login or self.user_store.get(login) is None:
            return None

        def on_save(old, new):
            errors = self.user_store.change_own_password(login, old, new)
            if not errors:
                messagebox.showinfo("Пароль изменён", "Ваш пароль изменён. В следующий раз входите с новым паролем.",
                                    parent=self.root)
                if hasattr(self, "accounts_tree"):
                    self.refresh_admin()
            return errors

        return self._password_dialog("Сменить свой пароль", f"Учётная запись: {login}", need_old=True,
                                     on_save=on_save, allow_generate=False)

    def change_selected_password(self):
        """Администратор назначает новый пароль выбранной учётной записи."""
        user = self._need_account()
        if not user:
            return None
        if user["login"].lower() == self.user.get("login", "").lower():
            return self.change_own_password()  # свой пароль — через текущий

        def on_save(old, new):
            errors = self.user_store.set_password(user["login"], new, self.user.get("login", ""))
            if not errors:
                messagebox.showinfo("Пароль изменён", f"Новый пароль для «{user['login']}» сохранён. "
                                                      "Сообщите его пользователю лично.", parent=self.root)
                self.refresh_admin()
            return errors

        return self._password_dialog("Новый пароль", f"Новый пароль для «{user['login']}» ({user.get('name', '')}).",
                                     need_old=False, on_save=on_save, allow_generate=True)

    def toggle_selected_account(self):
        user = self._need_account()
        if not user:
            return
        errors = self.user_store.update(user["login"], self.user.get("login", ""), active=not user.get("active", True))
        if errors:
            messagebox.showwarning("Не изменено", "; ".join(errors).capitalize() + ".", parent=self.root)
        self.refresh_admin()

    def delete_selected_account(self):
        user = self._need_account()
        if not user:
            return
        if not messagebox.askyesno("Удалить учётную запись", f"Удалить учётную запись «{user['login']}» "
                                   f"({user.get('name', '')})? Вместо удаления можно заблокировать.", parent=self.root):
            return
        errors = self.user_store.delete(user["login"], self.user.get("login", ""))
        if errors:
            messagebox.showwarning("Не удалено", "; ".join(errors).capitalize() + ".", parent=self.root)
        self.refresh_admin()

    # версия, обновление, перенос

    def update_system(self, folder: bool, source: str | None = None, confirm: bool = True) -> dict | None:
        if not accounts.can(self.user, "update_system"):
            return None
        source = source or (filedialog.askdirectory(title="Папка «Проект» новой версии") if folder else
                            filedialog.askopenfilename(title="Архив обновления", filetypes=[("Архив", "*.zip")]))
        if not source:
            return None
        try:
            info = admin.inspect_update(Path(source))
            if confirm and not messagebox.askyesno(
                    "Обновление",
                    f"Установлена версия {info['from_version']}, в пакете — {info['to_version']}.\n"
                    f"Изменится файлов: {len(info['changed'])}, добавится: {len(info['added'])}.\n\n"
                    "Перед заменой текущая версия будет сохранена. Данные (база, обращения, справочники, "
                    "учётные записи) не меняются. Обновить?", parent=self.root):
                return None
            result = admin.apply_update(Path(source))
        except admin.UpdateError as exc:
            messagebox.showwarning("Обновление не выполнено", str(exc), parent=self.root)
            return None
        accounts.audit(self.user.get("login", ""), "обновление системы",
                       f"{result['from_version']} → {result['to_version']}, файлов {len(result['replaced'])}")
        self.update_status.config(text=f"Обновлено до версии {result['to_version']} (файлов: {len(result['replaced'])}). "
                                       f"Копия прежней версии сохранена. Перезапустите программу, чтобы работать в новой версии.")
        self.refresh_admin()
        return result

    def rollback_selected(self, confirm: bool = True) -> dict | None:
        selection = self.backups_tree.selection()
        if not selection:
            messagebox.showinfo("Откат", "Выберите сохранённую версию в списке.", parent=self.root)
            return None
        backup = selection[0]
        version = self.backups_tree.set(backup, "version")
        if confirm and not messagebox.askyesno("Откат", f"Вернуть файлы программы версии {version}? Текущая версия тоже "
                                               "будет сохранена — откат можно отменить. Данные не меняются.", parent=self.root):
            return None
        try:
            result = admin.rollback(Path(backup))
        except admin.UpdateError as exc:
            messagebox.showwarning("Откат не выполнен", str(exc), parent=self.root)
            return None
        accounts.audit(self.user.get("login", ""), "откат версии", f"к {result['version']}")
        self.update_status.config(text=f"Возвращена версия {result['version']}. Перезапустите программу.")
        self.refresh_admin()
        return result

    def export_archive(self, dest: str | None = None) -> Path | None:
        if not accounts.can(self.user, "export_archive"):
            return None
        dest = dest or filedialog.askdirectory(title="Куда сохранить архив для переноса")
        if not dest:
            return None
        self.archive_status.config(text="Собираю архив…", foreground="#2563eb")
        self.root.update_idletasks()
        try:
            path = admin.build_transfer_archive(Path(dest), [self.user_store.public(u) for u in self.user_store.users])
        except Exception as exc:
            logger.exception("transfer archive failed")
            self.archive_status.config(text=f"Архив не создан: {type(exc).__name__}: {exc}", foreground="#dc2626")
            return None
        accounts.audit(self.user.get("login", ""), "архив для переноса", str(path))
        size = path.stat().st_size / 1e6
        self.archive_status.config(text=f"Архив создан: {path} ({size:.1f} МБ). Внутри — «Проект» и «Документы» "
                                        "(роли и пользователи, инструкция по переносу, перечень файлов).", foreground="#166534")
        self.refresh_admin()
        return path

    def sign_out(self):
        """«Выйти»: окно закрывается и открывается снова — с окном входа."""
        if not messagebox.askyesno("Выйти", "Выйти и войти другим пользователем?", parent=self.root):
            return
        accounts.audit(self.user.get("login", ""), "выход")
        self.on_close()
        import subprocess
        subprocess.Popen([sys.executable, str(BASE_DIR / "app.py")], cwd=str(BASE_DIR))

    # ---------- главный экран: вопрос, найденный ответ, черновик ----------

    def _build_answer_tab(self):
        tab = self.answer_tab

        # Заголовок карточки: «Новый вопрос» или обращение из входящих
        head = ttk.Frame(tab)
        head.pack(fill="x", pady=(0, 6))
        self.card_back = tk.Label(head, text="← Журнал вопросов", bg=PAGE, fg="#2563eb", font=self.f_text, cursor="hand2")
        self.card_back.bind("<Button-1>", lambda e: self.on_nav("inbox"))
        self.card_title = tk.Label(head, text="Новый вопрос", bg=PAGE, fg="#111827", font=self.f_title)
        self.card_title.pack(side="left")
        self.card_meta = tk.Label(head, text="", bg=PAGE, fg=MUTED, font=self.f_small)
        self.card_meta.pack(side="left", padx=12)

        form = ttk.Frame(tab, style="Card.TFrame", padding=10)
        form.pack(fill="x")
        # Вместо полей «От кого» и «Тема» — выбор источников для поиска. Отправитель и тема не вводятся вручную:
        # они берутся из обращения или письма («Вставить письмо», «Открыть .eml») и видны в заголовке над формой
        self.form_sender, self.form_subject = "", ""
        self.sources_note = self._build_source_row(form)

        ttk.Label(form, text="Текст вопроса / письма:", style="CardBold.TLabel").pack(anchor="w")
        self.message_input = tk.Text(form, height=3, font=self.f_text, bg=PAGE, relief="solid", borderwidth=1,
                                     wrap="word", padx=6, pady=4)
        self.message_input.pack(fill="x", pady=(4, 6))
        # Ctrl+Enter в поле текста — поиск, а не перевод строки
        self.message_input.bind("<Control-Return>", lambda e: self.on_search_click() or "break")

        controls = ttk.Frame(form, style="Inner.TFrame")
        controls.pack(fill="x")
        self.search_button = tk.Button(controls, text=SEARCH_BUTTON_TEXT, bg="#2563eb", fg="white",
                                       disabledforeground="white", font=self.f_bold, relief="flat", padx=16, pady=6,
                                       cursor="hand2", command=self.on_search_click)
        self.search_button.pack(side="left")
        self.status_text = ttk.Label(controls, text="Готов к работе", style="Card.TLabel", foreground="#64748b")
        self.status_text.pack(side="left", padx=12)
        self.timer_label = ttk.Label(controls, text="", style="Card.TLabel", foreground=HINT_COLOR)
        self.progress = ttk.Progressbar(controls, mode="indeterminate", length=self.px(130))
        for text, command in (("Очистить (Esc)", self.clear_form), ("Открыть .eml", self.on_open_eml),
                              ("Вставить письмо", self.on_paste_email)):
            ttk.Button(controls, text=text, command=command).pack(side="right", padx=(4, 0))
        self.timer_label.pack(side="right", padx=8)

        examples = ttk.Frame(form, style="Inner.TFrame")
        examples.pack(fill="x", pady=(6, 0))
        ttk.Label(examples, text="Примеры:", style="Card.TLabel", foreground="#64748b").pack(side="left")
        for label, question in EXAMPLES:
            tk.Button(examples, text=label, font=self.f_small, relief="flat", bg="#eef2ff", fg="#3730a3",
                      activebackground="#e0e7ff", cursor="hand2", padx=8,
                      command=lambda q=question: self.use_example(q)).pack(side="left", padx=(6, 0))
        ttk.Label(examples, text="Ctrl+Enter — найти", style="Card.TLabel",
                  foreground=HINT_COLOR, font=self.f_text).pack(side="right")

        self.badge = tk.Label(tab, text="", font=self.f_bold, anchor="w", padx=10, pady=5)
        self.warning_banner = tk.Label(tab, text="", font=self.f_text, wraplength=self.px(1100), justify="left", anchor="w",
                                       padx=10, pady=6)
        self.clarify_frame = ttk.Frame(tab, style="Card.TFrame", padding=(10, 6))
        ttk.Label(self.clarify_frame, text="Подписант зависит от суммы. Сумма договора, руб.:",
                  style="CardBold.TLabel").pack(side="left")
        self.amount_input = tk.Entry(self.clarify_frame, font=self.f_text, width=16, relief="solid", borderwidth=1)
        self.amount_input.pack(side="left", padx=6)
        self.amount_input.bind("<Return>", lambda e: self.on_recalculate())
        ttk.Button(self.clarify_frame, text="Пересчитать (Enter)", command=self.on_recalculate).pack(side="left")

        # Нижняя строка (метрики, «Подробнее») пакуется ДО области ответа: при нехватке места
        # сжимаются текстовые поля, а не кнопки и метрики
        footer = ttk.Frame(tab, style="Card.TFrame", padding=8)
        footer.pack(side="bottom", fill="x", pady=(8, 0))
        top = ttk.Frame(footer, style="Inner.TFrame")
        top.pack(fill="x")
        # Кнопка — до метрик: длинная строка метрик переносится, а не выталкивает кнопку за край
        self.trace_button = ttk.Button(top, text="Подробнее ▸", command=self.toggle_trace)
        self.trace_button.pack(side="right")
        self.metrics_text = ttk.Label(top, text="Метрики: —", style="CardBold.TLabel", wraplength=self.px(950))
        self.metrics_text.pack(side="left", fill="x", expand=True)
        self.trace_text = ttk.Label(footer, text="Трассировка: ожидание запуска...", style="Card.TLabel",
                                    foreground="#475569", wraplength=self.px(1100), justify="left")

        self.results_container = ttk.Frame(tab)
        self.results_container.pack(fill="both", expand=True, pady=(8, 0))

        answer_panel = ttk.Frame(self.results_container, style="Card.TFrame", padding=10)
        answer_panel.pack(side="left", fill="both", expand=True, padx=(0, 6))
        ttk.Label(answer_panel, text="Найденный ответ и источники", style="CardBold.TLabel").pack(anchor="w")
        feedback = ttk.Frame(answer_panel, style="Inner.TFrame")
        feedback.pack(side="bottom", fill="x")
        self.answer_output = tk.Text(answer_panel, height=6, font=self.f_text, bg=ANSWER_BG, fg="#14532d",
                                     relief="solid", borderwidth=1, wrap="word", padx=8, pady=8, cursor="arrow")
        self.answer_output.pack(fill="both", expand=True, pady=(6, 6))
        self.answer_output.tag_configure("heading", font=self.f_bold, foreground="#166534")
        self.answer_output.tag_configure("link", foreground="#1d4ed8", underline=True)
        self.answer_output.tag_configure("muted", foreground="#64748b")
        self.answer_output.tag_bind("link", "<Enter>", lambda e: self.answer_output.config(cursor="hand2"))
        self.answer_output.tag_bind("link", "<Leave>", lambda e: self.answer_output.config(cursor="arrow"))

        ttk.Label(feedback, text="Ответ:", style="Card.TLabel", foreground="#64748b").pack(side="left")
        self.feedback_buttons = {}
        for verdict, color in (("верно", "#16a34a"), ("неверно", "#dc2626"), ("устарело", "#d97706")):
            button = tk.Button(feedback, text=VERDICTS[verdict], font=self.f_small, fg=color, bg=CARD,
                               relief="groove", padx=8, state="disabled",
                               command=lambda v=verdict: self.on_feedback(v))
            button.pack(side="left", padx=(6, 0))
            self.feedback_buttons[verdict] = button
        self.feedback_label = ttk.Label(feedback, text="", style="Card.TLabel", foreground="#64748b")
        self.feedback_label.pack(side="left", padx=8)
        ttk.Label(feedback, text="Щёлкните по документу, чтобы открыть пункт", style="Card.TLabel",
                  foreground=HINT_COLOR, font=self.f_text).pack(side="right")

        draft_panel = ttk.Frame(self.results_container, style="Card.TFrame", padding=10)
        draft_panel.pack(side="left", fill="both", expand=True, padx=(6, 0))
        ttk.Label(draft_panel, text="Черновик ответа", style="CardBold.TLabel").pack(anchor="w")
        self.assignee_label = tk.Label(draft_panel, text="Ответственный: —", bg=CARD, fg="#2563eb", font=self.f_bold,
                                       anchor="w", justify="left", width=1)
        # Длинная подпись с контактами переносится по ширине панели, а не обрезается. width=1 — подпись не
        # расширяет панель сама, ширину задаёт панель (fill="x"), иначе перенос и ширина «раскачивают» друг друга
        self.assignee_label.bind("<Configure>", lambda e: self.assignee_label.config(wraplength=max(e.width - 4, 150)))
        self.assignee_label.pack(fill="x", pady=(2, 4))
        send_text = ("Подтвердить и отправить по почте" if SMTP_HOST
                     else "Подтвердить и сохранить письмо (демо)")
        self.send_button = tk.Button(draft_panel, text=send_text, bg="#16a34a", fg="white", disabledforeground="#dcfce7",
                                     font=self.f_bold, relief="flat", padx=12, pady=6, cursor="hand2",
                                     command=self.send_email, state="disabled")
        self.send_button.pack(side="bottom", fill="x")  # кнопка — до поля черновика, чтобы не выдавливалась
        self.draft_output = tk.Text(draft_panel, height=6, font=self.f_text, bg=CARD, fg="#0f172a",
                                    relief="solid", borderwidth=1, wrap="word", padx=8, pady=8)
        self.draft_output.pack(fill="both", expand=True, pady=(0, 8))

    def _update_card_header(self):
        request = self.store.get(self.current_request_id) if self.current_request_id else None
        sender = self.form_sender.split("<")[0].strip().strip('"') or self.form_sender
        if request is None:
            self.card_back.pack_forget()
            self.card_title.config(text=self.form_subject or "Новый вопрос")
            self.card_meta.config(text=f"от {sender}" if sender else "", fg=MUTED)
            return
        if not self.card_back.winfo_manager():
            self.card_back.pack(side="left", padx=(0, 12), before=self.card_title)
        self.card_title.config(text=request["subject"] or request["body"][:60])
        overdue = " · просрочено" if self.store.is_overdue(request) else ""
        who = f"от {sender} · " if sender else ""
        self.card_meta.config(text=f"{who}{request['status']}{overdue} · {request['id']} · получено {request['created']} · "
                                   f"срок {request['due']}",
                              fg="#dc2626" if overdue else MUTED)

    def _build_source_row(self, parent) -> ttk.Label:
        """Строка «Искать в: Приказы / Регламенты / Письма / Чаты». Отметки общие для всех экранов
        (одни и те же переменные): выбор на «Задать вопрос» сразу виден на «Главной» и наоборот."""
        if not hasattr(self, "source_vars"):
            saved = self.settings.get("search_sources")
            self.source_vars = {g: tk.BooleanVar(master=self.root, value=saved is None or g in saved) for g in SOURCE_GROUPS}
            self.sources_notes = []
        row = ttk.Frame(parent, style="Inner.TFrame")
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="Искать в:", style="CardBold.TLabel").pack(side="left", padx=(0, 6))
        for group, var in self.source_vars.items():
            tk.Checkbutton(row, text=group, variable=var, bg=CARD, activebackground=CARD, font=self.f_text,
                           cursor="hand2", command=self._sources_changed).pack(side="left", padx=(0, 10))
        all_link = tk.Label(row, text="все источники", bg=CARD, fg="#2563eb", font=self.f_text, cursor="hand2")
        all_link.pack(side="left", padx=(4, 0))
        all_link.bind("<Button-1>", lambda e: self.select_all_sources())
        note = ttk.Label(row, text="", style="Card.TLabel", foreground="#92400e")
        note.pack(side="left", padx=(12, 0))
        self.sources_notes.append(note)
        self._sources_changed()
        return note

    def selected_source_types(self) -> set[str] | None:
        """Типы документов для поиска по отметкам «Искать в:»; None — все источники."""
        return source_types_for(g for g, var in self.source_vars.items() if var.get())

    def _sources_changed(self):
        chosen = [g for g, var in self.source_vars.items() if var.get()]
        self.settings["search_sources"] = chosen
        save_ui_settings(self.settings)
        text = ("" if len(chosen) == len(SOURCE_GROUPS) else
                "Отметьте хотя бы один источник" if not chosen else "Поиск только в отмеченных источниках")
        for note in self.sources_notes:
            note.config(text=text)

    def select_all_sources(self):
        for var in self.source_vars.values():
            var.set(True)
        self._sources_changed()

    # ---------- входящие обращения ----------

    def _build_inbox_view(self):
        view = self.views["inbox"]
        head = ttk.Frame(view)
        head.pack(fill="x")
        self.inbox_title = tk.Label(head, text="Журнал вопросов", bg=PAGE, fg="#111827", font=self.f_title)
        self.inbox_title.pack(side="left")
        self.quick_buttons = {}
        for name in reversed(QUICK_FILTERS):
            label = tk.Label(head, text=name, bg=PAGE, fg="#374151", font=self.f_text, padx=10, pady=4, cursor="hand2")
            label.pack(side="right")
            label.bind("<Button-1>", lambda e, n=name: self.set_quick_filter(n))
            self.quick_buttons[name] = label

        toolbar = tk.Frame(view, bg="#f3f4f6")
        toolbar.pack(fill="x", pady=(8, 0))
        # Кнопка панели = значок (шрифт значков) + подпись (обычный шрифт): символы в тексте выглядят мутно
        for icon, text, command in (("refresh", "Обновить", self.on_refresh_inbox),
                                    ("open", "Открыть карточку", self.open_selected_request),
                                    ("read", "Прочитано", self.toggle_read),
                                    ("play", "Обработать новые", self.process_new_requests),
                                    ("download", "Загрузить .eml", self.on_import_eml)):
            button = tk.Frame(toolbar, bg="#f3f4f6", cursor="hand2")
            button.pack(side="left", padx=(self.px(10), self.px(6)), pady=self.px(6))
            parts = (tk.Label(button, text=self.icon(icon), bg="#f3f4f6", fg=ICON_COLOR, font=self.f_icon_small),
                     tk.Label(button, text=text, bg="#f3f4f6", fg="#1f2937", font=self.f_text))
            parts[0].pack(side="left", padx=(0, 6))
            parts[1].pack(side="left")
            for widget in (button, *parts):
                widget.bind("<Button-1>", lambda e, c=command: c())
                widget.bind("<Enter>", lambda e, p=(button, *parts): [w.config(bg="#e5e7eb") for w in p])
                widget.bind("<Leave>", lambda e, p=(button, *parts): [w.config(bg="#f3f4f6") for w in p])
        self.inbox_status = tk.Label(toolbar, text="", bg="#f3f4f6", fg=MUTED, font=self.f_small)
        self.inbox_status.pack(side="right", padx=10)

        columns = ("subject", "sender", "due", "created", "status")
        # Колонка #0 — цветной кружок статуса (картинка: чётче, чем символ шрифта)
        self.status_images = {status: self._dot_image(color) for status, color in STATUS_DOT_COLORS.items()}
        self.inbox_tree = ttk.Treeview(view, columns=columns, show="tree headings", selectmode="browse",
                                       style="Inbox.Treeview")
        self.inbox_tree.heading("#0", text="")
        self.inbox_tree.column("#0", width=self.px(44), minwidth=self.px(44), stretch=False, anchor="center")
        for column, title, width, anchor in (("subject", "Тема", 380, "w"),
                                             ("sender", "От", 190, "w"), ("due", "Срок", 150, "w"),
                                             ("created", "Создано", 150, "w"), ("status", "Статус", 200, "w")):
            self.inbox_tree.heading(column, text=title, anchor="w")
            self.inbox_tree.column(column, width=self.px(width), minwidth=self.px(width if column == "status" else 30), anchor=anchor,
                                   stretch=column == "subject")
        self.inbox_tree.tag_configure("unread", font=self.f_row_bold)
        self.inbox_tree.tag_configure("closed", font=self.f_row_strike, foreground="#9ca3af")
        self.inbox_tree.tag_configure("overdue", foreground="#dc2626")
        self.inbox_tree.tag_configure("escalated", foreground="#b45309")
        self.inbox_tree.pack(fill="both", expand=True, pady=(2, 0))
        self.inbox_tree.bind("<Double-1>", lambda e: self.open_selected_request())
        self.inbox_tree.bind("<Return>", lambda e: self.open_selected_request())
        ttk.Label(view, text="Двойной щелчок — открыть обращение. Жирным — непрочитанные, зачёркнуто — закрытые, "
                             "красным — просроченные. Письма из папки «входящие» загружаются автоматически.",
                  style="Sub.TLabel").pack(anchor="w", pady=(6, 0))

    def set_quick_filter(self, name: str):
        self.quick_filter = name
        self.refresh_inbox()

    def visible_requests(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now()
        scope, quick, needle = self.inbox_scope, self.quick_filter, self.search_text()
        result = []
        for request in self.store.items:
            status = request["status"]
            if scope == "ready" and status != READY:
                continue
            if scope == "escalated" and status != ESCALATED:
                continue
            if scope == "sent" and status != SENT:
                continue
            if scope.startswith("proc:") and request.get("process") != scope[5:]:
                continue
            if quick == "Новые" and status != NEW:
                continue
            if quick == "В работе" and status not in (WORK, READY, ESCALATED):
                continue
            if quick == "Просроченные" and not self.store.is_overdue(request, now):
                continue
            if quick == "30 дней" and self.store.created_at(request) < now - timedelta(days=30):
                continue
            if needle and needle not in f"{request['subject']} {request['sender']} {request['body']}".lower():
                continue
            result.append(request)
        return sorted(result, key=self.store.created_at, reverse=True)

    def refresh_inbox(self):
        selected = self.inbox_tree.selection()
        self.inbox_tree.delete(*self.inbox_tree.get_children())
        now = datetime.now()
        requests = self.visible_requests(now)
        for request in requests:
            tags = []
            if not request["read"]:
                tags.append("unread")
            if request["status"] in CLOSED:
                tags.append("closed")
            elif self.store.is_overdue(request, now):
                tags.append("overdue")
            elif request["status"] == ESCALATED:
                tags.append("escalated")
            sender = request["sender"].split("<")[0].strip().strip('"') or request["sender"]
            image = self.status_images.get(request["status"], self.status_images[NEW])
            self.inbox_tree.insert("", "end", iid=request["id"], tags=tuple(tags), image=image, values=(
                request["subject"] or request["body"][:70], sender, request["due"], request["created"],
                request["status"]))
        if selected and self.inbox_tree.exists(selected[0]):
            self.inbox_tree.selection_set(selected[0])

        scope = self.inbox_scope
        title = INBOX_SCOPES.get(scope) or scope[5:]
        self.inbox_title.config(text=title)
        for name, label in self.quick_buttons.items():
            active = name == self.quick_filter
            label.config(bg="#e0e7ef" if active else PAGE, font=self.f_bold if active else self.f_text)
        self.refresh_counts()

    def on_refresh_inbox(self):
        added = self.store.import_folder(INBOX_DIR)
        self.refresh_inbox()
        self.inbox_status.config(text=f"Новых писем: {added}" if added else "Новых писем нет")

    def on_global_search(self):
        self.on_nav("inbox", keep_search=True)

    def on_import_eml(self):
        paths = filedialog.askopenfilenames(title="Письма", filetypes=[("Письма", "*.eml"), ("Все файлы", "*.*")])
        added = 0
        for path in paths:
            try:
                added += 1 if self.store.import_eml(path, source="загружено вручную") else 0
            except Exception as exc:
                messagebox.showerror("Не удалось прочитать письмо", f"{path}\n\n{type(exc).__name__}: {exc}")
        if paths:
            self.on_nav("inbox")
            self.inbox_status.config(text=f"Загружено писем: {added}")

    def toggle_read(self):
        selection = self.inbox_tree.selection()
        if selection:
            request = self.store.get(selection[0])
            self.store.update(request["id"], read=not request["read"])
            self.refresh_inbox()

    def open_selected_request(self):
        selection = self.inbox_tree.selection()
        if selection:
            self.open_request(selection[0])

    def open_request(self, request_id: str, auto_search: bool = True):
        """Обращение из входящих → главный экран. Нет готового ответа — поиск запускается сразу."""
        if self.busy_since is not None or self.batch_running:
            messagebox.showinfo("Подождите", "Идёт обработка — дождитесь её окончания.")
            return
        request = self.store.update(request_id, read=True)
        self.current_request_id = request_id
        question = {k: request[k] for k in ("sender", "subject", "body")}
        self._reset_answer_area()
        self.fill_form(question)
        self.last_question = question
        self._update_card_header()
        self.show_view("home")
        self.refresh_counts()
        if request.get("result"):
            self.show_result(request["result"], saved=True)
        elif auto_search and request["status"] not in CLOSED:
            self.on_search_click()

    def new_request(self):
        """«Новое обращение»: пустой главный экран."""
        self.current_request_id = None
        self.form_sender, self.form_subject = "", ""
        self.message_input.delete("1.0", "end")
        self._reset_answer_area()
        self._update_card_header()
        self.show_view("home")
        self.message_input.focus_set()
        self.status_text.config(text="Готов к работе", foreground="#64748b")

    def _reset_answer_area(self):
        self.answer_output.config(state="normal")
        self.answer_output.delete("1.0", "end")
        self.draft_output.delete("1.0", "end")
        self.badge.pack_forget()
        self.warning_banner.pack_forget()
        self.clarify_frame.pack_forget()
        self.assignee_label.config(text="Ответственный: —")
        self.send_button.config(state="disabled")
        for button in self.feedback_buttons.values():
            button.config(state="disabled")
        self.feedback_label.config(text="")
        self.last_result = None

    def process_new_requests(self):
        """«Обработать новые»: ответы на все новые обращения в фоне, по очереди."""
        pending = [r["id"] for r in self.store.items if r["status"] == NEW]
        if not pending:
            self.inbox_status.config(text="Новых обращений нет")
            return
        if self.busy_since is not None or self.batch_running:
            return
        if not any(var.get() for var in self.source_vars.values()):
            self.inbox_status.config(text="Отметьте хотя бы один источник в строке «Искать в»")
            return
        self.batch_running = True
        source_types = self.selected_source_types()  # те же отметки «Искать в», что на «Главной»
        only = "" if source_types is None else \
            " (только: " + ", ".join(g.lower() for g, var in self.source_vars.items() if var.get()) + ")"

        def worker():
            for n, request_id in enumerate(pending, 1):
                request = self.store.get(request_id)
                self.root.after(0, lambda n=n: self.inbox_status.config(text=f"Обработка {n} из {len(pending)}...{only}"))
                self.store.update(request_id, status=WORK)
                try:
                    result = process_corporate_query(request["sender"], request["subject"], request["body"],
                                                     source_types=source_types)
                except Exception as exc:  # модель недоступна и т. п. — остальные оставляем новыми
                    self.store.update(request_id, status=NEW)
                    self.root.after(0, lambda error=exc: self._batch_done(error))
                    return
                self.store.set_result(request_id, result)
                self.root.after(0, self.refresh_inbox)
            self.root.after(0, lambda: self._batch_done(None))

        threading.Thread(target=worker, daemon=True).start()

    def _batch_done(self, error):
        self.batch_running = False
        self.refresh_inbox()
        if error:
            self.inbox_status.config(text=f"Обработка остановлена: {type(error).__name__}")
            logger.warning("batch stopped: %s", error)
        else:
            self.inbox_status.config(text="Новые обращения обработаны")

    # ---------- документы, внимание, история ----------

    def _view_title(self, view, text):
        tk.Label(view, text=text, bg=PAGE, fg="#111827", font=self.f_title).pack(anchor="w", pady=(0, 6))

    def _build_docs_tab(self):
        tab = self.docs_tab
        self._view_title(tab, "База знаний")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Статус:").pack(side="left")
        self.status_filter = ttk.Combobox(bar, values=STATUS_FILTERS, state="readonly", width=20, font=self.f_text)
        self.status_filter.set("Все")
        self.status_filter.pack(side="left", padx=(4, 12))
        self.status_filter.bind("<<ComboboxSelected>>", lambda e: self.refresh_sources())
        ttk.Label(bar, text="Поиск:").pack(side="left")
        self.docs_search = tk.StringVar()
        self.docs_search.trace_add("write", lambda *a: self.refresh_sources())
        tk.Entry(bar, textvariable=self.docs_search, font=self.f_text, width=30, relief="solid",
                 borderwidth=1).pack(side="left", padx=(4, 12))
        if accounts.can(self.user, "add_documents"):  # пополнение базы — право администратора
            ttk.Button(bar, text="Открыть папку базы", command=lambda: self._open_path(str(KNOWLEDGE_DIR))).pack(side="right")
            tk.Button(bar, text="＋ Добавить документ", bg="#2563eb", fg="white", activebackground="#3b82f6",
                      activeforeground="white", relief="flat", font=self.f_bold, padx=12, cursor="hand2",
                      command=self.add_document).pack(side="right", padx=(0, 8))
        self.search_mode_label = ttk.Label(tab, text="", style="Sub.TLabel")
        self.search_mode_label.pack(anchor="w", pady=(0, 4))

        columns = ("status", "type", "title", "date", "owner", "reason")
        self.docs_tree = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse")
        for column, title, width in (("status", "Статус", 130), ("type", "Тип", 140), ("title", "Название", 360),
                                     ("date", "Дата", 90), ("owner", "Владелец", 240), ("reason", "Причина статуса", 360)):
            self.docs_tree.heading(column, text=title)
            self.docs_tree.column(column, width=self.px(width), anchor="w")
        for status, color in STATUS_COLORS.items():
            self.docs_tree.tag_configure(status, foreground=color)
        self.docs_tree.pack(fill="both", expand=True)
        self.docs_tree.bind("<Double-1>", lambda e: self._open_selected_doc(self.docs_tree))
        ttk.Label(tab, text="Двойной щелчок — открыть документ. Новые документы добавляет администратор "
                            "(«＋ Добавить документ» или файл в папке базы) — база обновляется сама.",
                  style="Sub.TLabel").pack(anchor="w", pady=(4, 0))

    def _build_attention_tab(self):
        tab = self.attention_tab
        self._view_title(tab, "Требует внимания")
        ttk.Label(tab, text="Противоречия между документами и сроки пересмотра. Владельцы уведомлены "
                            "(журнал data/notifications.jsonl). Двойной щелчок — открыть документ.",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 6))
        self.attention_tree = ttk.Treeview(tab, columns=("kind", "text"), show="headings", selectmode="browse")
        self.attention_tree.heading("kind", text="Тип")
        self.attention_tree.heading("text", text="Что требует внимания")
        self.attention_tree.column("kind", width=self.px(130), anchor="w")
        self.attention_tree.column("text", width=self.px(1000), anchor="w")
        self.attention_tree.tag_configure("Противоречие", foreground="#991b1b")
        self.attention_tree.tag_configure("Пересмотр", foreground="#92400e")
        self.attention_tree.pack(fill="both", expand=True)
        self.attention_tree.bind("<Double-1>", lambda e: self._open_selected_doc(self.attention_tree))

    def _build_history_tab(self):
        tab = self.history_tab
        self._view_title(tab, "История запросов")
        ttk.Label(tab, text="Двойной щелчок — вернуть вопрос на главный экран (ответы этой сессии показываются сразу).",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 6))
        self.history_tree = ttk.Treeview(tab, columns=("time", "question", "source", "answer"), show="headings",
                                         selectmode="browse")
        for column, title, width in (("time", "Время", 140), ("question", "Вопрос", 420),
                                     ("source", "Откуда ответ", 180), ("answer", "Ответ", 520)):
            self.history_tree.heading(column, text=title)
            self.history_tree.column(column, width=self.px(width), anchor="w")
        self.history_tree.pack(fill="both", expand=True)
        self.history_tree.bind("<Double-1>", lambda e: self.on_history_open())

    def refresh_sources(self):
        """«Документы» и «Требует внимания» — из базы знаний (она пересобирается при изменении папки)."""
        kb = get_knowledge_base()
        self.search_mode_label.config(text=f"Поиск: {kb.search_mode}")
        status = self.status_filter.get()
        needle = self.docs_search.get().strip().lower()
        self.docs_tree.delete(*self.docs_tree.get_children())
        for doc in sorted(kb.documents, key=lambda d: (-d["priority"], d["title"])):
            if status != "Все" and doc["status"] != status:
                continue
            if needle and needle not in f"{doc['title']} {doc['source_type']} {doc['owner']}".lower():
                continue
            self.docs_tree.insert("", "end", iid=f"doc::{doc['id']}", tags=(doc["status"],), values=(
                doc["status"], doc["source_type"], doc["title"], doc["date"], doc["owner"], doc["status_reason"]))

        self.attention_tree.delete(*self.attention_tree.get_children())
        items = [("Противоречие", c) for c in kb.conflicts] + [("Пересмотр", r) for r in kb.reminders]
        for n, (kind, item) in enumerate(items):
            self.attention_tree.insert("", "end", iid=f"att{n}::{item['doc_id']}", tags=(kind,), values=(kind, item["text"]))
        if hasattr(self, "nav_rows"):
            self.refresh_counts()

    def _open_selected_doc(self, tree):
        selection = tree.selection()
        if not selection:
            return
        doc = get_knowledge_base().get_doc(selection[0].split("::", 1)[1])
        if doc:
            self.show_source(doc)

    def load_history_into_tab(self):
        for record in load_history():
            self._add_history_row(record, None, at_top=True)

    def _add_history_row(self, record: dict, result: dict | None, at_top: bool = True):
        question = {k: record.get(k, "") for k in ("sender", "subject", "body")}
        item = self.history_tree.insert("", 0 if at_top else "end", values=(
            record.get("time", "").replace("T", " "), record.get("body", "")[:120],
            SOURCE_NAMES.get(record.get("answer_source"), record.get("answer_source") or "—"),
            record.get("answer", "").replace("\n", " ")[:160]))
        self.history_items[item] = (question, result)
        return item

    def on_history_open(self):
        selection = self.history_tree.selection()
        if not selection:
            return
        question, result = self.history_items.get(selection[0], (None, None))
        if not question:
            return
        self.current_request_id = None
        self._update_card_header()
        self.fill_form(question)
        self.show_view("home")
        if result is not None:
            self.last_question = question
            self.show_result(result, from_cache=True)
        else:
            self.status_text.config(text="Вопрос из истории — нажмите Ctrl+Enter", foreground="#2563eb")

    # ---------- окно источника ----------

    def show_source(self, doc: dict):
        """Окно с текстом документа: пункты, на которые ссылается ответ, подсвечены; кнопка «Открыть файл»."""
        full = get_knowledge_base().get_doc(doc["id"]) or doc
        cited = set(doc.get("points") or [])
        if self.source_window is not None and self.source_window.winfo_exists():
            self.source_window.destroy()
        win = tk.Toplevel(self.root)
        self.source_window = win
        win.title(full["title"])
        win.geometry(f"{self.px(760)}x{self.px(520)}")
        win.configure(bg=CARD)
        win.bind("<Escape>", lambda e: win.destroy() or "break")

        status = full.get("status", "")
        reason = f" — {full['status_reason']}" if full.get("status_reason") else ""
        tk.Label(win, text=full["title"], font=self.f_bold, bg=CARD, anchor="w", wraplength=self.px(720),
                 justify="left").pack(fill="x", padx=12, pady=(10, 2))
        tk.Label(win, text=f"{full['source_type']} · {full['date']} · Владелец: {full['owner']}", font=self.f_small,
                 bg=CARD, fg="#475569", anchor="w").pack(fill="x", padx=12)
        tk.Label(win, text=f"Статус: {status}{reason}", font=self.f_small, bg=CARD,
                 fg=STATUS_COLORS.get(status, "#475569"), anchor="w", wraplength=self.px(720), justify="left").pack(fill="x", padx=12)

        buttons = tk.Frame(win, bg=CARD)  # кнопки пакуются до текста, чтобы не выдавливались вниз
        buttons.pack(side="bottom", fill="x", padx=12, pady=(0, 10))
        text = tk.Text(win, height=10, font=self.f_text, wrap="word", padx=10, pady=8, relief="solid", borderwidth=1)
        text.pack(fill="both", expand=True, padx=12, pady=8)
        text.tag_configure("cited", background="#fef08a")
        text.tag_configure("point", font=self.f_bold)
        first_cited = None
        for chunk in full.get("chunks") or [{"point": None, "text": full.get("content", "")}]:
            start = text.index("end-1c")
            if chunk["point"]:
                text.insert("end", f"п. {chunk['point']}. ", "point")
            text.insert("end", chunk["text"] + "\n\n")
            if chunk["point"] in cited:
                text.tag_add("cited", start, text.index("end-2c"))
                first_cited = first_cited or start
        if first_cited:
            text.see(first_cited)
        text.config(state="disabled")

        if full.get("path"):
            ttk.Button(buttons, text="Открыть файл", command=lambda: self._open_path(full["path"])).pack(side="left")
        note = "Подсвечен пункт, на который ссылается ответ" if cited else ""
        tk.Label(buttons, text=note, font=self.f_small, bg=CARD, fg="#854d0e").pack(side="left", padx=10)
        ttk.Button(buttons, text="Закрыть (Esc)", command=win.destroy).pack(side="right")

    def _open_path(self, path: str):
        try:
            open_file(path)
        except Exception as exc:
            messagebox.showerror("Не удалось открыть", f"{path}\n\n{type(exc).__name__}: {exc}")

    # ---------- форма ----------

    def current_question(self) -> dict:
        return {"sender": self.form_sender, "subject": self.form_subject,
                "body": self.message_input.get("1.0", "end").strip()}

    def fill_form(self, question: dict):
        """Вопрос на главный экран: текст — в поле, отправитель и тема — в заголовок над формой."""
        self.form_sender = (question.get("sender") or "").strip()
        self.form_subject = (question.get("subject") or "").strip()
        self.message_input.delete("1.0", "end")
        self.message_input.insert("1.0", question.get("body", ""))
        self._update_card_header()

    def clear_form(self):
        self.form_subject = ""
        self.message_input.delete("1.0", "end")
        self.clarify_frame.pack_forget()
        self.message_input.focus_set()
        self._update_card_header()
        self.status_text.config(text="Форма очищена", foreground="#64748b")

    def use_example(self, question: str):
        self.current_request_id = None
        self.form_sender, self.form_subject = "", ""
        self._update_card_header()
        self.message_input.delete("1.0", "end")
        self.message_input.insert("1.0", question)
        self.on_search_click()

    def on_paste_email(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showwarning("Буфер пуст", "Скопируйте письмо из почты (вместе со строками «От:» и «Тема:»).")
            return
        self.fill_form(parse_pasted_email(text))
        self.status_text.config(text="Письмо вставлено — отправитель и тема в заголовке", foreground="#2563eb")

    def on_open_eml(self):
        path = filedialog.askopenfilename(title="Письмо", filetypes=[("Письма", "*.eml"), ("Все файлы", "*.*")])
        if not path:
            return
        try:
            self.fill_form(load_eml(path))
        except Exception as exc:
            messagebox.showerror("Не удалось прочитать письмо", f"{type(exc).__name__}: {exc}")
            return
        self.status_text.config(text="Письмо загружено — отправитель и тема в заголовке", foreground="#2563eb")

    def on_recalculate(self):
        """Уточнение суммы прямо в окне: сумма добавляется к вопросу, ответ пересчитывается."""
        amount_text = self.amount_input.get().strip()
        if extract_amount(f"{amount_text} руб.") is None:
            messagebox.showwarning("Сумма", "Введите сумму, например: 300 000, 750 тыс., 1,2 млн.")
            return
        body = self.message_input.get("1.0", "end").strip()
        self.message_input.delete("1.0", "end")
        self.message_input.insert("1.0", f"{body} Сумма договора: {amount_text} руб.")
        self.amount_input.delete(0, "end")
        self.on_search_click()

    # ---------- поиск ----------

    def on_search_click(self):
        question = self.current_question()
        if not question["body"]:
            messagebox.showwarning("Заполните форму", "Напишите вопрос или вставьте письмо в поле «Текст вопроса / письма».")
            return
        if not any(var.get() for var in self.source_vars.values()):
            messagebox.showwarning("Источники", "Отметьте хотя бы один источник в строке «Искать в».")
            return
        if self.busy_since is not None or self.batch_running:
            return
        self.search_source_types = self.selected_source_types()

        # Каждый вопрос — обращение во «Входящих»: новое (вручную) или уже открытое
        if self.current_request_id is None:
            request = self.store.add(question["sender"], question["subject"], question["body"])
            self.current_request_id = request["id"]
        self.store.update(self.current_request_id, read=True, status=WORK, **question)
        self._update_card_header()
        self.last_question = question

        cached = self.session_cache.get(self._cache_key(question))
        if cached and cached[0] == get_knowledge_base().signature:
            self.show_result(cached[1], from_cache=True)
            return

        self.last_result = None
        self.send_button.config(state="disabled")
        self.search_button.config(state="disabled", text="Ищу ответ...")
        self.status_text.config(text="Проверка безопасности", foreground="#2563eb")
        self.trace_text.config(text="Трассировка: выполняется...")
        self.badge.pack_forget()
        self.warning_banner.pack_forget()
        self.clarify_frame.pack_forget()
        self.progress.pack(side="left", padx=(0, 8), after=self.status_text)
        self.progress.start(12)
        self.busy_since = time.time()
        self._tick()
        threading.Thread(target=self.process_in_background, args=(question,), daemon=True).start()

    _MAIN = object()  # «источники последнего поиска на главном экране»

    def _cache_key(self, question: dict, types=_MAIN) -> str:
        """Ключ мгновенного повтора: вопрос + выбранные источники (другие источники — другой ответ)."""
        if types is CorpAgentApp._MAIN:
            types = self.search_source_types
        return question_key(question) + " | " + (",".join(sorted(types)) if types is not None else "все")

    def _tick(self):
        if self.busy_since is None:
            return
        self.timer_label.config(text=f"{time.time() - self.busy_since:.1f} с")
        self.root.after(100, self._tick)

    def _stop_busy(self):
        self.busy_since = None
        self.progress.stop()
        self.progress.pack_forget()
        self.search_button.config(state="normal", text=SEARCH_BUTTON_TEXT)

    def process_in_background(self, question: dict):
        def progress(stage: str):
            self.root.after(0, lambda: self.status_text.config(text=f"{stage}...", foreground="#2563eb"))

        try:
            result = process_corporate_query(question["sender"], question["subject"], question["body"], progress=progress,
                                             source_types=self.search_source_types)
            self.root.after(0, lambda: self.show_result(result))
        except Exception as exc:
            if not isinstance(exc, (LLMUnavailableError, LLMModelNotFoundError)):
                logger.exception("internal error while processing query")
            self.root.after(0, lambda error=exc: self.show_error(error))

    def show_error(self, error: Exception):
        self._stop_busy()
        if self.current_request_id:
            self.store.update(self.current_request_id, status=NEW)
            self._update_card_header()
        self.trace_text.config(text=f"Трассировка: ошибка — {type(error).__name__}")
        if isinstance(error, LLMModelNotFoundError):
            self.status_text.config(text="Модель не найдена в Ollama", foreground="#dc2626")
            messagebox.showerror("Модель не найдена",
                                 f"В Ollama нет модели {LLM_MODEL}.\n\nСкачайте её командой: ollama pull {LLM_MODEL}\n"
                                 f"или укажите другую модель в переменной окружения LLM_MODEL.\n\nДетали: {error}")
        elif isinstance(error, LLMUnavailableError):
            self.status_text.config(text="Локальная модель недоступна", foreground="#dc2626")
            messagebox.showerror("Локальная модель не отвечает",
                                 f"Не удалось связаться с моделью {LLM_MODEL} ({LLM_BASE_URL}).\n\n"
                                 f"1. Убедитесь, что Ollama запущена в трее.\n"
                                 f"2. Проверьте, что модель скачана: ollama pull {LLM_MODEL}\n"
                                 f"3. Проверьте, что установлен пакет openai: pip install -r requirements.txt\n\nДетали: {error}")
        else:
            self.status_text.config(text="Внутренняя ошибка", foreground="#dc2626")
            messagebox.showerror("Внутренняя ошибка",
                                 f"При обработке запроса произошла ошибка: {type(error).__name__}: {error}\n\n"
                                 f"Подробности записаны в журнал: {LOG_FILE}")

    def _show_banner(self, text: str, bg: str, fg: str):
        self.warning_banner.config(text=text, bg=bg, fg=fg)
        self.warning_banner.pack(fill="x", before=self.results_container, pady=(6, 0))

    def _insert_doc_links(self, title: str, docs: list, note=None, widget: tk.Text | None = None):
        """Список документов в ответе; каждая строка — ссылка на окно источника."""
        widget = widget or self.answer_output
        gap = "\n" if widget.get("end-2c", "end-1c") == "\n" else "\n\n"
        widget.insert("end", f"{gap}{title}\n", "heading")
        for doc in docs:
            tag = f"link{len(self._links)}"
            self._links.append(tag)
            widget.insert("end", "• ")
            try:
                name = describe_doc(doc)
            except KeyError:  # неполная карточка — показываем хотя бы название
                name = doc.get("title", doc.get("id", "документ"))
            widget.insert("end", name, ("link", tag))
            if note:
                widget.insert("end", f" — {note(doc)}", "muted")
            widget.insert("end", "\n")
            widget.tag_bind(tag, "<Button-1>", lambda e, d=doc: self.show_source(d))

    def show_result(self, result: dict, from_cache: bool = False, saved: bool = False):
        """saved — ответ, сохранённый у обращения (открыто из входящих); from_cache — повтор вопроса в сессии."""
        self._stop_busy()
        self.timer_label.config(text="")
        self.answer_output.config(state="normal")
        self.answer_output.delete("1.0", "end")
        self.draft_output.delete("1.0", "end")
        self._links = []
        self.trace_text.config(text="Трассировка: " + "  ->  ".join(result.get("logs", [])))
        self.refresh_sources()  # база могла пересобраться: файлы в папке изменились

        question = self.last_question or self.current_question()
        if not saved:
            if not from_cache:
                record = append_history(question, result)
                self._add_history_row(record, result)
                if not result.get("blocked"):
                    self.session_cache[self._cache_key(question)] = (get_knowledge_base().signature, result)
            if self.current_request_id and self.store.get(self.current_request_id):
                self.store.set_result(self.current_request_id, result)
        self._update_card_header()
        self.refresh_inbox()

        level, label = confidence(result)
        bg, fg = CONFIDENCE[level]
        suffix = ("  ·  ответ из истории: база не менялась" if from_cache else
                  "  ·  сохранённый ответ по обращению" if saved else "")
        self.badge.config(text="● " + label + suffix, bg=bg, fg=fg)
        self.badge.pack(fill="x", before=self.results_container, pady=(8, 0))

        for button in self.feedback_buttons.values():
            button.config(state="disabled" if result.get("blocked") else "normal")
        self.feedback_label.config(text="")

        if result.get("blocked"):
            self.status_text.config(text="Запрос отклонён фильтром безопасности", foreground="#dc2626")
            self._show_banner(f"ФИЛЬТР БЕЗОПАСНОСТИ: {result.get('reason', '')}. Запрос не передан модели, документы не раскрыты.",
                              bg="#fee2e2", fg="#991b1b")
            self.answer_output.insert("1.0", "Запрос отклонён фильтром безопасности. Если это обычное письмо сотрудника — "
                                             "ответьте вручную; срабатывание записано в журнал для разбора.")
            self.answer_output.config(state="disabled")
            self.assignee_label.config(text="Ответственный: Служба ИБ")
            self.metrics_text.config(text=f"Метрики: ответ — фильтр безопасности | время {result.get('latency', 0)} сек")
            self.send_button.config(state="disabled")
            self.clarify_frame.pack_forget()
            return

        self.last_result = result
        self.status_text.config(text="Готово" + (" (из истории)" if from_cache else ""), foreground="#16a34a")

        if result.get("needs_clarification"):
            self.clarify_frame.pack(fill="x", before=self.results_container, pady=(6, 0))
            self.amount_input.focus_set()
        else:
            self.clarify_frame.pack_forget()

        basis_docs = result.get("basis_docs") or result.get("active_docs", [])[:1]
        warnings = []
        inactive = result.get("inactive_docs", [])
        if inactive and not basis_docs:  # при наличии основания недействующие и так перечислены в ответе
            warnings.append("По теме найдены только недействующие документы (см. «Не применяются» в ответе).")
        for conflict in result.get("conflicts", []):
            warnings.append(f"ПРОТИВОРЕЧИЕ: {conflict}")
        if result.get("escalation_reason") and result["escalation_reason"] != "Ответ в базе не найден":
            warnings.append(f"Передано специалисту: {result['escalation_reason']} ({result.get('relevance') or 'см. «Подробнее»'}).")
        if result.get("fact_issues"):
            warnings.append("ПРОВЕРКА ФАКТОВ: в ответе модели были данные, которых нет в указанном источнике ("
                            + ", ".join(result["fact_issues"]) + "). Показан текст самого документа.")
        if warnings:
            self._show_banner("\n".join(warnings), bg="#fef3c7", fg="#92400e")
        else:
            self.warning_banner.pack_forget()

        self.answer_output.insert("end", result.get("answer", ""))
        if basis_docs:
            self._insert_doc_links("Основание:", basis_docs)
        others = [d for d in result.get("active_docs", []) if d.get("id") not in {b.get("id") for b in basis_docs}]
        if others:
            self._insert_doc_links("Другие найденные источники:", others)
        if inactive:
            self._insert_doc_links("Не применяются:", inactive,
                                   note=lambda d: d.get("status_reason") or d.get("status", "устарел"))
        self.answer_output.config(state="disabled")

        label = result.get("responsible_label", "Ответственный")
        contacts = self.contact_text(result.get("responsible", ""))
        self.assignee_label.config(text=f"{label}: {result.get('responsible', '—')}" + (f"\n{contacts}" if contacts else ""))
        self.draft_output.insert("1.0", result.get("email_draft", ""))
        request = self.store.get(self.current_request_id) if self.current_request_id else None
        self.send_button.config(state="disabled" if request and request["status"] == SENT else "normal")

        metrics = (f"Метрики: ответ — {SOURCE_NAMES.get(result.get('answer_source'), '—')} | "
                   f"время {result.get('latency', 0)} сек | токены {result.get('tokens', 0)} | стоимость 0.00 ₽")
        if result.get("found") and BASELINE_SEARCH_MINUTES > 0:
            saved_min = max(0, round(BASELINE_SEARCH_MINUTES - float(result.get("latency", 0)) / 60))
            metrics += f" | оценка экономии ~{saved_min} мин (ручной поиск от {BASELINE_SEARCH_MINUTES} мин)"
        self.metrics_text.config(text=metrics)

    def toggle_trace(self):
        self.trace_visible = not self.trace_visible
        if self.trace_visible:
            self.trace_text.pack(anchor="w", pady=(4, 0))
            self.trace_button.config(text="Скрыть ▾")
        else:
            self.trace_text.pack_forget()
            self.trace_button.config(text="Подробнее ▸")

    # ---------- оценка ответа ----------

    def on_feedback(self, verdict: str):
        comment = ""
        if verdict in ("неверно", "устарело"):
            comment = simpledialog.askstring("Что не так?", "Коротко: что неверно или устарело (можно оставить пустым)",
                                             parent=self.root) or ""
        self.record_feedback_verdict(verdict, comment)

    def record_feedback_verdict(self, verdict: str, comment: str = ""):
        if not self.last_result:
            return
        record_feedback(self.last_result, self.last_question or self.current_question(), verdict, comment)
        for button in self.feedback_buttons.values():
            button.config(state="disabled")
        self.feedback_label.config(text=f"Спасибо: отметка «{VERDICTS[verdict]}» сохранена")

    # ---------- отправка ----------

    def mark_sent(self):
        if self.current_request_id and self.store.get(self.current_request_id):
            self.store.update(self.current_request_id, status=SENT, sent_at=datetime.now().strftime("%d.%m.%Y %H:%M"))
            self._update_card_header()
            self.refresh_inbox()
        self.send_button.config(state="disabled")

    def send_email(self):
        recipient = self.form_sender
        draft = self.draft_output.get("1.0", "end").strip()
        result = self.last_result

        if not result or result.get("blocked") or str(self.send_button["state"]) == "disabled":
            messagebox.showwarning("Нельзя отправить", "Нет подготовленного ответа, он уже отправлен или запрос был заблокирован.")
            return
        if not extract_email(recipient):
            # Вопрос задан не из письма — адресата спрашиваем при отправке (полей «От кого» на экране больше нет)
            recipient = (simpledialog.askstring("Кому отправить", "E-mail адресата, например: ivanov@company.ru "
                                                                  "или Иванов И.И. <ivanov@company.ru>", parent=self.root)
                         or "").strip()
            if not extract_email(recipient):
                if recipient:
                    messagebox.showwarning("Нельзя отправить", "Нужен e-mail адресата, например: ivanov@company.ru")
                return
            self.form_sender = recipient
            if self.current_request_id and self.store.get(self.current_request_id):
                self.store.update(self.current_request_id, sender=recipient)
            self._update_card_header()
        if not draft:
            messagebox.showwarning("Нельзя отправить", "Черновик пустой.")
            return

        action = "Отправить" if SMTP_HOST else "Сохранить (демо-режим, SMTP не настроен)"
        if not messagebox.askyesno("Подтверждение", f"{action} ответ адресату {recipient}?"):
            return
        try:
            mode, destination = send_reply(recipient, self.form_subject, draft,
                                           source_ids=[d["id"] for d in result.get("active_docs", []) if "id" in d])
        except Exception as exc:
            logger.exception("send failed")
            messagebox.showerror("Письмо не отправлено", f"{type(exc).__name__}: {exc}")
            return

        self.mark_sent()
        if mode == "smtp":
            messagebox.showinfo("Письмо отправлено", f"Ответ отправлен адресату {recipient} через {destination}.")
        else:
            messagebox.showinfo("Письмо сохранено (демо)",
                                f"SMTP не настроен, поэтому письмо не отправлено, а сохранено в файл:\n{destination}\n\n"
                                "Для реальной отправки задайте переменные SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM.")


class LoginWindow:
    """Окно входа. При первом запуске (учётных записей нет) — создание учётной записи администратора.
    После 5 неудачных попыток вход блокируется на 30 секунд."""

    MAX_ATTEMPTS, LOCK_SECONDS = 5, 30

    def __init__(self, root: tk.Tk, store: "accounts.UserStore"):
        self.root, self.store, self.user = root, store, None
        self.failures, self.locked_until = 0, 0.0
        self.first_run = store.empty
        family = load_ui_settings().get("font_family") or UI_FONT
        self.font = tkfont.Font(family=family, size=11)
        self.bold = tkfont.Font(family=family, size=11, weight="bold")
        self.title_font = tkfont.Font(family=family, size=16, weight="bold")
        win = self.win = tk.Toplevel(root)
        win.title(f"{SYSTEM_NAME} — вход")
        win.configure(bg=CARD)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        top = tk.Frame(win, bg=NAVY)
        top.pack(fill="x")
        tk.Label(top, text="ИИ-консультант", bg=NAVY, fg=NAVY_TEXT, font=self.title_font, padx=20, pady=12).pack(side="left")
        # Номер версии в окне входа не показывается: он нужен только администратору («Администрирование»)
        body = tk.Frame(win, bg=CARD, padx=24, pady=16)
        body.pack(fill="both")
        intro = ("Первый запуск: создайте учётную запись администратора.\nОн добавит остальных пользователей в "
                 "разделе «Администрирование»." if self.first_run else "Войдите под своей учётной записью.")
        tk.Label(body, text=intro, bg=CARD, font=self.font, justify="left").grid(row=0, column=0, columnspan=2,
                                                                              sticky="w", pady=(0, 10))
        fields = [("login", "Логин"), ("password", "Пароль")]
        if self.first_run:
            fields = [("login", "Логин"), ("name", "ФИО"), ("password", "Пароль"), ("password2", "Пароль ещё раз")]
        self.entries = {}
        for row, (key, title) in enumerate(fields, 1):
            tk.Label(body, text=title + ":", bg=CARD, font=self.bold, anchor="w").grid(row=row, column=0, sticky="w",
                                                                                      padx=(0, 10), pady=4)
            entry = tk.Entry(body, width=32, font=self.font, relief="solid", borderwidth=1,
                             show="•" if key.startswith("password") else "")
            entry.grid(row=row, column=1, sticky="we", pady=4)
            self.entries[key] = entry
        if self.first_run:
            self.entries["login"].insert(0, "admin")
            tk.Label(body, text=f"Пароль — не короче {accounts.MIN_PASSWORD} символов, буквы и цифры.", bg=CARD,
                     fg=HINT_COLOR, font=self.font).grid(row=len(fields) + 1, column=0, columnspan=2, sticky="w")
        self.error = tk.Label(body, text="", bg=CARD, fg="#dc2626", font=self.font, justify="left", wraplength=420)
        self.error.grid(row=len(fields) + 2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        tk.Button(body, text="Создать и войти" if self.first_run else "Войти", bg="#2563eb", fg="white",
                  activebackground="#3b82f6", activeforeground="white", font=self.bold, relief="flat", padx=20, pady=5,
                  cursor="hand2", command=self.submit).grid(row=len(fields) + 3, column=1, sticky="e", pady=(10, 0))
        tk.Label(body, text="Окно для вопросов без входа — «Спросить_консультанта.cmd»", bg=CARD, fg=MUTED,
                 font=self.font).grid(row=len(fields) + 4, column=0, columnspan=2, sticky="w", pady=(12, 0))
        win.bind("<Return>", lambda e: self.submit())
        win.bind("<Escape>", lambda e: win.destroy())
        (self.entries["password"] if not self.first_run else self.entries["name"]).focus_set()
        if not self.first_run:
            self.entries["login"].focus_set()

    def submit(self):
        values = {k: e.get() for k, e in self.entries.items()}
        if self.first_run:
            if values["password"] != values["password2"]:
                self.error.config(text="Пароли не совпадают.")
                return
            errors = self.store.add(values["login"], values["name"], accounts.ADMIN, values["password"])
            if errors:
                self.error.config(text="Проверьте: " + "; ".join(errors) + ".")
                return
        elif time.time() < self.locked_until:
            self.error.config(text=f"Слишком много попыток. Подождите {int(self.locked_until - time.time()) + 1} с.")
            return
        user = self.store.authenticate(values["login"], values["password"])
        if user is None:
            self.failures += 1
            self.entries["password"].delete(0, "end")
            if self.failures >= self.MAX_ATTEMPTS:
                self.failures, self.locked_until = 0, time.time() + self.LOCK_SECONDS
                self.error.config(text=f"Неверный логин или пароль. Вход заблокирован на {self.LOCK_SECONDS} с.")
            else:
                self.error.config(text="Неверный логин или пароль, либо учётная запись заблокирована.")
            return
        self.user = user
        self.win.destroy()

    def run(self) -> dict | None:
        self.root.wait_window(self.win)
        return self.user


def enable_dpi_awareness():
    """Сообщить Windows, что программа сама учитывает масштаб экрана (125 %, 150 % …).

    Без этого Windows растягивает окно как картинку, и весь текст выглядит размытым.
    Вызывать до создания окна tk.Tk().
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # чёткий текст с учётом масштаба основного экрана
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # Windows 7/8
        except (AttributeError, OSError):
            pass


def main(argv: list[str] | None = None) -> None:
    """Запуск окна. --employee (или --сотрудник) — окно сотрудника: только «Задать вопрос», без входа."""
    argv = sys.argv[1:] if argv is None else argv
    enable_dpi_awareness()
    root = tk.Tk()
    icon = BASE_DIR / "icon.ico"  # значок программы (создаёт launcher.py) — для всех окон вместо «пёрышка» Tk
    if icon.exists():
        try:
            root.iconbitmap(default=str(icon))
        except tk.TclError:
            pass
    employee_mode = any(a in ("--employee", "--сотрудник") for a in argv)
    store, user = accounts.UserStore(), None
    if not employee_mode:
        root.withdraw()  # сначала — окно входа
        user = LoginWindow(root, store).run()
        if user is None:
            root.destroy()
            return
        root.deiconify()
    CorpAgentApp(root, employee_mode=employee_mode, user=user, user_store=store)
    root.mainloop()


if __name__ == "__main__":
    main()
