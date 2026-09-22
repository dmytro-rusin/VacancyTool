from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from . import database as db
from .service import Collector


def create_app(root: Path, start_scheduler: bool = False) -> Flask:
    app = Flask(__name__, template_folder=str(root / "templates"), static_folder=str(root / "static"))
    collector = Collector(root)
    access_path = root / "access.json"
    access = json.loads(access_path.read_text(encoding="utf-8")) if access_path.exists() else {}
    public_host = access.get("tailscale_host")
    if public_host is not None and (not isinstance(public_host, str) or
                                    not public_host.endswith(".ts.net") or
                                    ":" in public_host or "/" in public_host):
        raise ValueError("access.json: укажите домен устройства Tailscale (*.ts.net)")
    allowed_hosts = {"127.0.0.1:5090", "localhost:5090", "localhost", "127.0.0.1"}
    allowed_origins = {"http://127.0.0.1:5090", "http://localhost:5090"}
    if public_host:
        allowed_hosts.add(public_host)
        allowed_origins.add(f"https://{public_host}")
    app.extensions["collector"] = collector
    if start_scheduler:
        collector.start()

    @app.before_request
    def local_only():
        if request.host not in allowed_hosts:
            return jsonify({"error": "Недопустимый адрес сайта"}), 403
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("Origin")
            if origin and origin not in allowed_origins:
                return jsonify({"error": "Недопустимый источник запроса"}), 403
            if public_host and request.host == public_host and not origin:
                return jsonify({"error": "Не указан источник запроса"}), 403

    @app.get("/")
    def index():
        rows = db.list_vacancies(collector.database_path)
        return render_template("index.html", vacancies=rows, statuses=db.STATUSES,
                               state=collector.state())

    @app.get("/api/state")
    def state():
        return jsonify(collector.state())

    @app.post("/api/refresh")
    def refresh():
        if collector.manual_refresh():
            return jsonify({"started": True}), 202
        return jsonify({"error": "Обновление уже выполняется"}), 409

    @app.post("/api/vacancies/<int:vacancy_id>/status")
    def change_status(vacancy_id: int):
        payload = request.get_json(silent=True) or {}
        status = payload.get("status")
        if status not in db.STATUSES:
            return jsonify({"error": "Некорректный статус"}), 400
        if not db.set_status(collector.database_path, vacancy_id, status):
            return jsonify({"error": "Вакансия не найдена"}), 404
        return jsonify({"status": status, "counts": db.counts(collector.database_path)})

    @app.post("/api/schedule")
    def update_schedule():
        payload = request.get_json(silent=True) or {}
        try:
            collector.update_schedule(payload["schedule"])
        except (KeyError, TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(collector.state())

    return app
