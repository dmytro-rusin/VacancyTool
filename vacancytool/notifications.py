from __future__ import annotations

import json
import smtplib
import ssl
import stat
import subprocess
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

from . import database as db


def load_mail_settings(root: Path) -> dict | None:
    path = root / "mail.json"
    if not path.exists():
        return None
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("mail.json должен быть доступен только владельцу (chmod 600)")
    config = json.loads(path.read_text(encoding="utf-8"))
    for field in ("host", "username", "from", "to"):
        if not isinstance(config.get(field), str) or not config[field].strip():
            raise ValueError(f"mail.json: заполните {field}")
    password = config.get("password")
    service = config.get("keychain_service")
    if not password and not service:
        raise ValueError("mail.json: укажите password или keychain_service")
    if service:
        if not isinstance(service, str) or not service.strip() or len(service) > 100:
            raise ValueError("mail.json: некорректный keychain_service")
        try:
            result = subprocess.run(
                ["/usr/bin/security", "find-generic-password", "-w", "-a", config["username"],
                 "-s", service], check=True, capture_output=True, text=True, timeout=10)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ValueError("Пароль приложения Gmail не найден в Связке ключей") from exc
        config["password"] = result.stdout.rstrip("\n")
    if not isinstance(config.get("password"), str) or not config["password"]:
        raise ValueError("Пустой пароль SMTP")
    for field in ("from", "to"):
        address = config[field]
        if "\n" in address or "\r" in address or parseaddr(address)[1] != address or "@" not in address:
            raise ValueError(f"mail.json: некорректный адрес {field}")
    config["port"] = int(config.get("port", 465))
    if not 1 <= config["port"] <= 65535 or config.get("security", "ssl") not in {"ssl", "starttls"}:
        raise ValueError("mail.json: нужен SSL или STARTTLS и корректный порт")
    return config


def build_digest(config: dict, vacancies: list[dict]) -> EmailMessage:
    message = EmailMessage()
    message["From"] = config["from"]
    message["To"] = config["to"]
    message["Subject"] = f"{len(vacancies)} new vacancies found"
    message.set_content("\n\n".join(f'{row["score"]} | {row["title"]}\n{row["original_url"]}'
                                    for row in vacancies))
    return message


def send_digest(config: dict, vacancies: list[dict]) -> None:
    message = build_digest(config, vacancies)
    context = ssl.create_default_context()
    if config.get("security", "ssl") == "ssl":
        with smtplib.SMTP_SSL(config["host"], config["port"], timeout=20, context=context) as smtp:
            smtp.login(config["username"], config["password"])
            smtp.send_message(message)
    else:
        with smtplib.SMTP(config["host"], config["port"], timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(config["username"], config["password"])
            smtp.send_message(message)


def send_pending(root: Path, database_path: Path) -> int:
    vacancies = db.pending_notifications(database_path)
    if not vacancies:
        return 0
    config = load_mail_settings(root)
    if config is None:
        return 0
    send_digest(config, vacancies)
    db.mark_notifications_sent(database_path, [row["id"] for row in vacancies])
    return len(vacancies)
