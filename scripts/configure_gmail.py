#!/usr/bin/env python3
from __future__ import annotations

import getpass
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "mail.json"


def main() -> int:
    if not CONFIG_PATH.exists():
        print(f"Не найден файл {CONFIG_PATH}", file=sys.stderr)
        return 1

    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Не удалось прочитать {CONFIG_PATH}: {exc}", file=sys.stderr)
        return 1

    print("Введите 16-значный Gmail App Password.")
    print("Ввод будет скрыт; пробелы между группами символов можно оставить.")
    password = "".join(getpass.getpass("App Password: ").split())
    if len(password) != 16:
        print("Пароль приложения должен содержать 16 символов.", file=sys.stderr)
        return 1

    confirmation = "".join(getpass.getpass("Повторите App Password: ").split())
    if password != confirmation:
        print("Пароли не совпадают. Файл не изменён.", file=sys.stderr)
        return 1

    config["password"] = password
    config.pop("keychain_service", None)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=CONFIG_PATH.parent,
            prefix=".mail.json.",
            delete=False,
        ) as temp:
            temp_path = Path(temp.name)
            os.chmod(temp_path, 0o600)
            json.dump(config, temp, ensure_ascii=False, indent=2)
            temp.write("\n")
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_path, CONFIG_PATH)
        os.chmod(CONFIG_PATH, 0o600)
    except OSError as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        print(f"Не удалось сохранить настройки: {exc}", file=sys.stderr)
        return 1

    print(f"Готово. Настройки сохранены в {CONFIG_PATH}")
    print("Пароль не выводился на экран и не попал в историю команд.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
