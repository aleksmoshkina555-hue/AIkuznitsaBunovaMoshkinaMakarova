"""Отправка ответа сотруднику: через SMTP, если он настроен, иначе — сохранение .eml в папку outbox (демо)."""
import re
import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate, parseaddr

from config import OUTBOX_DIR, SENT_FILE, SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER
from journal import append_jsonl

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def extract_email(value: str) -> str | None:
    """«Иванов И.И. <ivanov@company.ru>» или «ivanov@company.ru» -> адрес; иначе None."""
    name, address = parseaddr(value.strip())
    return address if _EMAIL_RE.match(address or "") else None


def send_reply(recipient: str, subject: str, body: str, source_ids: list[str] | None = None) -> tuple[str, str]:
    """Отправляет письмо. Возвращает (режим, куда): ("smtp", хост) или ("demo", путь к .eml)."""
    address = extract_email(recipient)
    if not address:
        raise ValueError(f"Некорректный адрес получателя: {recipient!r}")
    name = parseaddr(recipient)[0]

    message = EmailMessage()
    message["From"] = SMTP_FROM
    message["To"] = formataddr((name, address)) if name else address
    subject = subject.strip()
    if not subject:
        message["Subject"] = "Ответ на ваш запрос"
    elif subject.lower().startswith("re:"):
        message["Subject"] = subject
    else:
        message["Subject"] = f"Re: {subject}"
    message["Date"] = formatdate(localtime=True)
    message.set_content(body)

    if SMTP_HOST:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            if smtp.has_extn("starttls"):
                smtp.starttls()
                smtp.ehlo()
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(message)
        mode, destination = "smtp", SMTP_HOST
    else:
        OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^\w.-]", "_", address)
        path = OUTBOX_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_{safe_name}.eml"
        path.write_bytes(bytes(message))
        mode, destination = "demo", str(path)

    append_jsonl(SENT_FILE, {
        "time": datetime.now().isoformat(timespec="seconds"),
        "to": address,
        "subject": message["Subject"],
        "mode": mode,
        "destination": destination,
        "sources": source_ids or [],
    })
    return mode, destination
