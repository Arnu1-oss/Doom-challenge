"""Local same-origin HTTP adapter for the Doom-challenge CQA evaluator.

No authentication is implemented. Binds to 127.0.0.1 by default. Do not expose
on a public interface without adding authentication, authorization and CSRF
protection. Reads the existing CSV and evaluations.jsonl, optionally invokes
main.evaluate_row(), and persists checklist/appeals/reviewer adjustments.
"""
from __future__ import annotations

import argparse
import csv
from contextlib import ExitStack
from html.parser import HTMLParser
import json
import mimetypes
import shutil
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit, urljoin
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError


ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
FILE_LOCK = threading.RLock()

DEFAULT_CRITERIA = [
    {"id": 1, "name": "Установление контакта", "description": "Приветствие на языке клиента и представление в первом ответе.", "weight": 10, "type": "binary", "levels": [0, 10], "na_allowed": False},
    {"id": 2, "name": "Выявление потребности", "description": "Учесть известное и уточнить недостающие сведения до ответа.", "weight": 15, "type": "binary", "levels": [0, 15], "na_allowed": True},
    {"id": 3, "name": "Идентификация", "description": "Проверка личности до раскрытия защищённых данных или действий.", "weight": 20, "type": "binary", "levels": [0, 20], "na_allowed": True},
    {"id": 4, "name": "Корректная и полная консультация", "description": "20 — все существенные вопросы закрыты; 15 — пропущена деталь; 10 — важный вопрос открыт; 5 — общий совет; 0 — ошибки/нет ответа.", "weight": 20, "type": "scale", "levels": [0, 5, 10, 15, 20], "na_allowed": True},
    {"id": 5, "name": "Эмпатия и клиентоориентированность", "description": "Вежливость и реакция на конкретное недовольство, если оно было.", "weight": 15, "type": "binary", "levels": [0, 15], "na_allowed": False},
    {"id": 6, "name": "Ведение диалога, скорость и грамотность", "description": "Ясность и последовательность, предупреждение об ожидании; порог 3 минуты не оценивается.", "weight": 15, "type": "scale", "levels": [0, 4, 7, 10], "na_allowed": False},
    {"id": 7, "name": "Завершение", "description": "Итог или предложение помощи и прощание; N/A если чат оборвался.", "weight": 5, "type": "binary", "levels": [0, 5], "na_allowed": True},
]
DEFAULT_CHECKLIST = {
    "version": 1,
    "source": "CQA_Chat_Quality_Checklist.pdf",
    "criteria": DEFAULT_CRITERIA,
    "gross_errors": {
        "name": "Грубые ошибки",
        "description": "Оскорбление, платные услуги в обход компании или раскрытие конфиденциальной информации.",
    },
}


class ApiError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def data_root() -> Path:
    return Path(os.environ.get("CQA_DATA_DIR", ROOT)).resolve()


def read_json(path: Path, fallback):
    if not path.exists():
        return json.loads(json.dumps(fallback, ensure_ascii=False))
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as out:
            json.dump(value, out, indent=2, ensure_ascii=False)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def read_jsonl(path: Path) -> dict:
    records = {}
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if isinstance(record, dict) and record.get("conversation_id"):
                        records[str(record["conversation_id"])] = record
                except json.JSONDecodeError:
                    raise ApiError(500, f"Invalid JSONL line {no} in {path.name}")
    return records


def write_jsonl(path: Path, records: dict) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as f:
            for record in records.values():
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def normalize_language(value):
    v = str(value or "").strip().lower()
    return {"ru": "RU", "русский": "RU", "kz": "KZ", "kk": "KZ", "kaz": "KZ", "en": "EN"}.get(v, v.upper())


def normalize_channel(value):
    v = str(value or "").strip().lower()
    return "voice call" if v in {"voice", "voice call", "call"} else "agent chat" if v in {"agent chat", "chat"} else v


def conversations() -> list[dict]:
    path = data_root() / "conversations_202611071332.csv"
    if not path.exists():
        raise ApiError(503, "CSV не найден. Поместите conversations_202611071332.csv рядом с api_server.py.")
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"conversation_id", "channel", "language", "transcript"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ApiError(500, "В CSV отсутствуют необходимые столбцы")
        return list(reader)


def format_messages(raw):
    try:
        items = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as ex:
        raise ApiError(500, f"Некорректный transcript: {ex}")
    if not isinstance(items, list):
        raise ApiError(500, "transcript должен быть JSON-массивом")
    return [{
        "id": f"m{i}",
        "timestamp": str(item.get("timestamp", "")),
        "speaker": str(item.get("speaker", "system")).lower(),
        "text": str(item.get("text", "")),
    } for i, item in enumerate(items, 1) if isinstance(item, dict)]


def validate_checklist(candidate):
    if not isinstance(candidate, dict) or not isinstance(candidate.get("criteria"), list):
        raise ApiError(400, "Неверная структура чек-листа")
    rows = candidate["criteria"]
    if not 7 <= len(rows) <= 40:
        raise ApiError(400, "В чек-листе должно быть от 7 до 40 критериев")
    seen, cleaned = set(), []
    for item in rows:
        if not isinstance(item, dict):
            raise ApiError(400, "Критерий должен быть объектом")
        idx = item.get("id")
        if type(idx) is not int or idx < 1 or idx == 8 or idx > 9999 or idx in seen:
            raise ApiError(400, "Некорректный или повторяющийся ID критерия (8 зарезервирован)")
        seen.add(idx)
        typ = item.get("type")
        if typ not in ("binary", "scale"):
            raise ApiError(400, f"Неверный тип критерия {idx}")
        try:
            weight = int(item.get("weight"))
        except (TypeError, ValueError):
            raise ApiError(400, f"Неверный вес критерия {idx}")
        if type(item.get("weight")) is not int or not 0 <= weight <= 100:
            raise ApiError(400, f"Вес {idx} должен быть целым числом 0–100")
        levels = item.get("levels")
        if (not isinstance(levels, list) or len(levels) < 2 or len(levels) > 15 or
            any(type(v) is not int or v < 0 or v > 100 for v in levels) or
            sorted(set(levels)) != levels or levels[0] != 0 or levels[-1] <= 0):
            raise ApiError(400, f"Неверная шкала критерия {idx} (0, ..., max)")
        if typ == "binary" and len(levels) != 2:
            raise ApiError(400, f"Бинарный критерий {idx} должен иметь две ступени")
        name = item.get("name", "")
        description = item.get("description", "")
        if not isinstance(name, str) or not name.strip() or len(name) > 120 or not isinstance(description, str) or len(description) > 1000:
            raise ApiError(400, f"Некорректное название/описание критерия {idx}")
        if type(item.get("na_allowed")) is not bool:
            raise ApiError(400, f"na_allowed для критерия {idx} должен быть boolean")
        cleaned.append({"id": idx, "name": name.strip(), "description": description.strip(), "weight": weight,
                        "type": typ, "levels": levels, "na_allowed": item["na_allowed"]})
    if not set(range(1, 8)).issubset(seen):
        raise ApiError(400, "Базовые критерии 1–7 нельзя удалить")
    if sum(c["weight"] for c in cleaned) != 100:
        raise ApiError(400, "Сумма весов должна быть ровно 100%")
    gross = candidate.get("gross_errors", DEFAULT_CHECKLIST["gross_errors"])
    if not isinstance(gross, dict) or not isinstance(gross.get("description"), str) or not 10 <= len(gross["description"]) <= 1000:
        raise ApiError(400, "Описание грубых ошибок должно содержать 10–1000 символов")
    return {"version": int(candidate.get("version", 1)) + 1, "criteria": cleaned,
            "source": "customized-from-CQA", "gross_errors": {"name": "Грубые ошибки", "description": gross["description"]}}


def checklist():
    return read_json(data_root() / "checklist.json", DEFAULT_CHECKLIST)


def normalized_result(criterion, value):
    if value == "N/A":
        if not criterion["na_allowed"]:
            raise ApiError(400, "N/A не разрешено для этого критерия")
        return None, "N/A"
    if criterion["type"] == "binary":
        if value not in ("YES", "NO"):
            raise ApiError(400, "Ожидается YES или NO")
        return (criterion["levels"][-1] if value == "YES" else 0), value
    if type(value) is not int or value not in criterion["levels"]:
        raise ApiError(400, "Значение не соответствует шкале критерия")
    return value, value


def reweight_evaluation(record, config):
    """Recompute from original results and CURRENT configuration; never trust old points/weight."""
    if not record or not isinstance(record.get("criteria"), list) or record.get("error"):
        return record
    matching = {c.get("id"): c for c in record["criteria"] if isinstance(c, dict)}
    total_weight, weighted, pending = 0, 0.0, []
    items = []
    for rule in config["criteria"]:
        criterion = matching.get(rule["id"])
        if criterion is None:
            pending.append(rule["id"])
            items.append({"id": rule["id"], "name": rule["name"], "weight": rule["weight"], "result": None,
                          "points": None, "max_points": rule["levels"][-1], "explanation": "Оценка отсутствует", "evidence_message_ids": [], "evidence": [], "pending": True})
            continue
        item = dict(criterion)
        item.update({"name": rule["name"], "weight": rule["weight"], "max_points": rule["levels"][-1], "pending": False})
        try:
            points, value = normalized_result(rule, item.get("result"))
            item["points"] = points
            item["result"] = value
            if points is not None:
                total_weight += rule["weight"]
                weighted += rule["weight"] * points / rule["levels"][-1]
        except ApiError:
            item["points"] = None
            item["pending"] = True
            item["explanation"] = "Результат не соответствует изменённой шкале; требуется пересмотр"
            pending.append(rule["id"])
        items.append(item)
    output = dict(record)
    for item in items:
        if item.get("pending") or item.get("result") == "N/A" or not total_weight:
            item["effective_weight"] = None
            item["effective_points"] = None
        else:
            item["effective_weight"] = round(item["weight"] * 100 / total_weight, 2)
            item["effective_points"] = round(item["weight"] * item["points"] / item["max_points"] * 100 / total_weight, 2)
    output["criteria"] = items
    # Incomplete checklists never receive a deceptively complete total.
    output["total_score"] = round(100 * weighted / total_weight, 2) if total_weight and not pending else None
    output["pending_criteria"] = pending
    output["scored_weight"] = total_weight
    output["checklist_version"] = config["version"]
    return output


def summaries():
    cfg, results = checklist(), read_jsonl(data_root() / "evaluations.jsonl")
    output = []
    for row in conversations():
        cid = row["conversation_id"]
        try:
            messages = format_messages(row["transcript"])
        except ApiError:
            messages = []
        evaluation = reweight_evaluation(results.get(cid), cfg)
        output.append({
            "conversation_id": cid,
            "channel": normalize_channel(row.get("channel")),
            "language": normalize_language(row.get("language")),
            "audio_url": public_audio_url(row),
            "message_count": len(messages),
            "preview": next((m["text"] for m in messages if m["speaker"] == "client"), ""),
            "total_score": evaluation.get("total_score") if evaluation else None,
            "evaluation_status": "error" if evaluation and evaluation.get("error") else "pending" if evaluation and evaluation.get("pending_criteria") else "scored" if evaluation else "none",
            "gross_error": bool((evaluation or {}).get("gross_errors", {}).get("detected", False)),
        })
    return output


# Audio sources can be provided in CSV audio_url or audio_sources.json.
# Local files are served only from CQA_AUDIO_DIR (default: <data>/audio).
def audio_mapping():
    mapping = read_json(data_root() / "audio_sources.json", {})
    if not isinstance(mapping, dict):
        raise ApiError(500, "audio_sources.json должен быть JSON-объектом")
    return mapping


def audio_root():
    return Path(os.environ.get("CQA_AUDIO_DIR", str(data_root() / "audio"))).resolve()


def audio_source(row):
    cid = str(row.get("conversation_id", ""))
    value = audio_mapping().get(cid) or row.get("audio_url") or ""
    if not isinstance(value, str):
        return ""
    return value.strip()


def public_audio_url(row):
    source = audio_source(row)
    if not source:
        return ""
    if urlsplit(source).scheme.lower() in ("https", "http"):
        # Keep signed Power Automate URLs server-side, out of browser markup.
        return "/api/conversations/" + str(row["conversation_id"]) + "/audio"
    # Local paths and object keys are resolved by the backend, never by the browser.
    return "/api/conversations/" + str(row["conversation_id"]) + "/audio"


def local_audio_path(row):
    source = audio_source(row)
    if not source or urlsplit(source).scheme.lower() in ("http", "https"):
        raise ApiError(404, "Локальная аудиозапись не указана")
    if "://" in source:
        raise ApiError(400, "Для object storage используйте подписанную HTTPS-ссылку")
    root = audio_root()
    path = (root / source).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ApiError(404, "Аудиофайл не найден в CQA_AUDIO_DIR")
    if path.suffix.lower() not in (".mp3", ".wav", ".ogg", ".m4a", ".aac", ".webm", ".mp4"):
        raise ApiError(415, "Неподдерживаемый аудиоформат")
    return path


def detail(cid):
    row = next((x for x in conversations() if str(x.get("conversation_id")) == cid), None)
    if not row:
        raise ApiError(404, "Диалог не найден")
    record = read_jsonl(data_root() / "evaluations.jsonl").get(cid)
    return {
        "conversation": {
            "conversation_id": cid, "channel": normalize_channel(row.get("channel")),
            "language": normalize_language(row.get("language")), "audio_url": public_audio_url(row),
            "messages": format_messages(row["transcript"]),
        },
        "evaluation": reweight_evaluation(record, checklist()),
    }


def appeals():
    value = read_json(data_root() / "appeals.json", [])
    if not isinstance(value, list):
        raise ApiError(500, "Неверная структура appeals.json")
    return value


def now():
    return datetime.now(timezone.utc).isoformat()


def new_appeal(payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Некорректное обращение")
    cid, criterion_id = payload.get("conversation_id"), payload.get("criterion_id")
    reason = payload.get("comment", "")
    if not isinstance(cid, str) or not any(r["conversation_id"] == cid for r in conversations()):
        raise ApiError(404, "Диалог не найден")
    if type(criterion_id) is not int or criterion_id not in ({c["id"] for c in checklist()["criteria"]} | {8}):
        raise ApiError(400, "Неизвестный критерий")
    if not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 2000:
        raise ApiError(400, "Комментарий: от 5 до 2000 символов")
    record = detail(cid)["evaluation"]
    criterion = next((x for x in (record or {}).get("criteria", []) if x["id"] == criterion_id), None)
    if criterion_id == 8 and (record or {}).get("gross_errors") is not None:
        criterion = {"id": 8, "name": "Грубые ошибки", "result": "YES" if record["gross_errors"].get("detected") else "NO"}
    if not criterion or criterion.get("pending") or record.get("error"):
        raise ApiError(409, "Нельзя оспорить ещё не выставленную оценку")
    with FILE_LOCK:
        old = appeals()
        if any(a["conversation_id"] == cid and a["criterion_id"] == criterion_id and a["status"] == "open" for a in old):
            raise ApiError(409, "По этому критерию уже есть открытая апелляция")
        appeal = {"id": uuid.uuid4().hex[:12], "conversation_id": cid, "criterion_id": criterion_id,
                  "criterion_name": criterion["name"], "original_result": criterion["result"], "comment": reason.strip(),
                  "status": "open", "created_at": now(), "reviewer_comment": "", "resolved_at": None,
                  "revised_result": None}
        old.insert(0, appeal)
        atomic_json(data_root() / "appeals.json", old)
        return appeal


def review_appeal(appeal_id, payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Некорректный ответ")
    action = payload.get("action")
    reason = payload.get("comment", "")
    if action not in ("confirm", "change") or not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 2000:
        raise ApiError(400, "Выберите решение и укажите причину (от 5 до 2000 символов)")
    with FILE_LOCK:
        all_appeals = appeals()
        match = next((x for x in all_appeals if x["id"] == appeal_id), None)
        if not match:
            raise ApiError(404, "Апелляция не найдена")
        if match["status"] != "open":
            raise ApiError(409, "Апелляция уже рассмотрена")
        revised = None
        if action == "change":
            records = read_jsonl(data_root() / "evaluations.jsonl")
            evaluation = records.get(match["conversation_id"])
            if not evaluation or not isinstance(evaluation.get("criteria"), list):
                raise ApiError(409, "Исходная оценка отсутствует")
            if match["criterion_id"] == 8:
                revised = payload.get("result")
                if revised not in ("YES", "NO"):
                    raise ApiError(400, "Для грубой ошибки выберите ДА или НЕТ")
                evidence = []
                if revised == "YES":
                    message_id = payload.get("evidence_message_id", "")
                    operator_messages = {m["id"]: m for m in detail(match["conversation_id"])["conversation"]["messages"] if m["speaker"] == "operator"}
                    if not isinstance(message_id, str) or message_id not in operator_messages:
                        raise ApiError(400, "Подтвердите грубую ошибку репликой оператора")
                    selected = operator_messages[message_id]
                    evidence = [{"message_id": message_id, "timestamp": selected["timestamp"], "speaker": selected["speaker"], "text": selected["text"]}]
                evaluation["gross_errors"] = {"detected": revised == "YES", "explanation": reason.strip(),
                                              "evidence_message_ids": [x["message_id"] for x in evidence], "evidence": evidence,
                                              "reviewed_at": now()}
            else:
                rule = next(c for c in checklist()["criteria"] if c["id"] == match["criterion_id"])
                _, revised = normalized_result(rule, payload.get("result"))
                target = next((c for c in evaluation["criteria"] if c.get("id") == match["criterion_id"]), None)
                if not target:
                    raise ApiError(409, "Оценка критерия отсутствует")
                target["result"] = revised
                target["explanation"] = reason.strip()
                target["reviewer_comment"] = reason.strip()
                target["reviewed_at"] = now()
                points, _ = normalized_result(rule, revised)
                target["points"] = points
                adjusted = reweight_evaluation(evaluation, checklist())
                evaluation["total_score"] = adjusted.get("total_score")
            # Gross flags have no score; numerical total is unchanged.
            write_jsonl(data_root() / "evaluations.jsonl", records)
        match.update({"status": "changed" if action == "change" else "confirmed", "reviewer_comment": reason.strip(),
                      "resolved_at": now(), "revised_result": revised})
        atomic_json(data_root() / "appeals.json", all_appeals)
        return match


def assign_manual_result(cid, criterion_id, payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Некорректная ручная оценка")
    rule = next((c for c in checklist()["criteria"] if c["id"] == criterion_id), None)
    if not rule:
        raise ApiError(404, "Критерий не найден")
    points, value = normalized_result(rule, payload.get("result"))
    explanation = payload.get("explanation", "")
    if not isinstance(explanation, str) or not 5 <= len(explanation.strip()) <= 2000:
        raise ApiError(400, "Обоснование: от 5 до 2000 символов")
    ids = payload.get("evidence_message_ids", [])
    if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids) or len(ids) > 30:
        raise ApiError(400, "Некорректные ID доказательств")
    messages = detail(cid)["conversation"]["messages"]
    index = {m["id"]: m for m in messages}
    if any(k not in index for k in ids):
        raise ApiError(400, "ID доказательства отсутствует в транскрипте")
    with FILE_LOCK:
        records = read_jsonl(data_root() / "evaluations.jsonl")
        evaluation = records.get(cid)
        if not evaluation or not isinstance(evaluation.get("criteria"), list):
            raise ApiError(409, "Сначала выполните базовую AI-оценку диалога")
        matched = next((c for c in evaluation["criteria"] if c.get("id") == criterion_id), None)
        if matched is None:
            matched = {"id": criterion_id}
            evaluation["criteria"].append(matched)
        matched.update({"result": value, "points": points, "weight": rule["weight"],
                        "name": rule["name"], "max_points": rule["levels"][-1],
                        "explanation": explanation.strip(), "evidence_message_ids": ids,
                        "evidence": [{"message_id": k, "timestamp": index[k]["timestamp"],
                                      "speaker": index[k]["speaker"], "text": index[k]["text"]} for k in ids],
                        "reviewer_comment": explanation.strip(), "source": "manual", "reviewed_at": now()})
        records[cid] = evaluation
        evaluation["total_score"] = reweight_evaluation(evaluation, checklist())["total_score"]
        write_jsonl(data_root() / "evaluations.jsonl", records)
    return detail(cid)


def run_evaluation(cid):
    row = next((r for r in conversations() if r["conversation_id"] == cid), None)
    if row is None:
        raise ApiError(404, "Диалог не найден")
    try:
        import pandas as pd
        from dotenv import load_dotenv
        from main import evaluate_row
    except (ImportError, AttributeError) as e:
        raise ApiError(503, f"Не найден рабочий evaluator main.py или зависимости: {e}")
    load_dotenv(data_root() / ".env")
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ApiError(503, "Добавьте OPENROUTER_API_KEY в .env для AI-оценки")
    try:
        evaluated = evaluate_row(pd.Series(row), api_key)
    except Exception as e:
        # Do not leak request headers, full exception trace or secrets to the UI.
        raise ApiError(502, f"AI-оценка завершилась ошибкой: {type(e).__name__}: {str(e)[:250]}")
    evaluated["conversation_id"] = cid
    with FILE_LOCK:
        records = read_jsonl(data_root() / "evaluations.jsonl")
        records[cid] = evaluated
        write_jsonl(data_root() / "evaluations.jsonl", records)
    return detail(cid)


class CQAHandler(BaseHTTPRequestHandler):
    server_version = "CQA-DEV/1.0"

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_remote_audio(self, source):
        """Proxy signed Power Automate audio with no redirects or URL disclosure."""
        url = urlsplit(source)
        host = (url.hostname or "").lower()
        # Restrict remote fetches to the Power Platform environment in the CSV.
        if (url.scheme != "https" or not (host.endswith(".environment.api.powerplatform.com") or host == "freedomholdingcorporation.crm11.dynamics.com")
                or url.username or url.password or url.port not in (None, 443)):
            raise ApiError(400, "Удалённый аудиоисточник не разрешён")

        class NoRedirect(HTTPRedirectHandler):
            def http_error_302(self, req, fp, code, msg, headers):
                raise ApiError(502, "Источник аудио вернул перенаправление")
            http_error_301 = http_error_302
            http_error_303 = http_error_302
            http_error_307 = http_error_302
            http_error_308 = http_error_302

        headers = {"Accept": "audio/*,application/octet-stream"}
        if self.headers.get("Range"):
            requested = self.headers["Range"]
            if not re.fullmatch(r"bytes=(?:\d+-\d*|-\d+)", requested):
                raise ApiError(416, "Неверный Range")
            headers["Range"] = requested
        try:
            opener = build_opener(NoRedirect())
            with ExitStack() as stack:
                # Resolve the HTML player without applying the media byte range to HTML.
                response = stack.enter_context(opener.open(Request(source, headers={"Accept": "text/html,audio/*,application/octet-stream"}, method="GET"), timeout=15))
                mime = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if mime in {"text/html", "application/xhtml+xml"}:
                    page = response.read(1_000_001)
                    if len(page) > 1_000_000:
                        raise ApiError(502, "Страница аудиоплеера слишком большая")

                    class AudioSources(HTMLParser):
                        def __init__(self):
                            super().__init__(convert_charrefs=True)
                            self.sources = []
                            self.in_audio = False

                        def handle_starttag(self, tag, attrs):
                            attrs = dict(attrs)
                            if tag == "audio":
                                self.in_audio = True
                            if (tag == "audio" or (tag == "source" and self.in_audio)) and attrs.get("src"):
                                self.sources.append(attrs["src"])

                        def handle_endtag(self, tag):
                            if tag == "audio":
                                self.in_audio = False

                    parser = AudioSources()
                    parser.feed(page.decode(response.headers.get_content_charset() or "utf-8", errors="replace"))
                    if not parser.sources:
                        raise ApiError(502, "В странице плеера не найдена ссылка на аудиозапись")
                    media_url = urljoin(source, parser.sources[0])
                    media = urlsplit(media_url)
                    if (media.scheme != "https" or media.hostname != "freedomholdingcorporation.crm11.dynamics.com"
                            or media.username or media.password or media.port not in (None, 443)):
                        raise ApiError(502, "Источник записи в плеере не разрешён")
                    response.close()
                    response = stack.enter_context(opener.open(Request(media_url, headers=headers, method="GET"), timeout=30))
                elif "Range" in headers:
                    response.close()
                    response = stack.enter_context(opener.open(Request(source, headers=headers, method="GET"), timeout=30))
                mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if mime not in {"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/ogg",
                                "audio/mp4", "audio/aac", "audio/webm", "video/mp4", "application/octet-stream"}:
                    raise ApiError(502, "Power Automate не вернул аудиофайл (возможно JSON или HTML)")
                length = response.headers.get("Content-Length")
                if length and int(length) > 100_000_000:
                    raise ApiError(413, "Аудиозапись слишком большая")
                self.send_response(206 if response.status == 206 else 200)
                self.send_header("Content-Type", mime)
                self.send_header("Cache-Control", "private, no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Accept-Ranges", response.headers.get("Accept-Ranges", "bytes"))
                if length:
                    self.send_header("Content-Length", length)
                if response.headers.get("Content-Range"):
                    self.send_header("Content-Range", response.headers["Content-Range"])
                self.end_headers()
                total = 0
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > 100_000_000:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # Seeking or leaving the page cancels the previous media request.
            return
        except HTTPError as e:
            # Do not reveal signed source URL in error text.
            raise ApiError(502, f"Power Automate: HTTP {e.code}")
        except (URLError, TimeoutError) as e:
            print(f"[AUDIO ERROR] {type(e).__name__}", flush=True)
            if isinstance(e, URLError):
                print(f"[AUDIO REASON] {type(e.reason).__name__}", flush=True)
            raise ApiError(502, "Не удалось получить аудио из Power Automate")

    def send_audio(self, cid):
        row = next((r for r in conversations() if str(r.get("conversation_id")) == cid), None)
        if row is None:
            raise ApiError(404, "Диалог не найден")
        source = audio_source(row)
        if urlsplit(source).scheme.lower() in ("https", "http"):
            return self.send_remote_audio(source)
        path = local_audio_path(row)
        size = path.stat().st_size
        start, end = 0, size - 1
        requested = self.headers.get("Range", "")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested.strip())
            if not match or not any(match.groups()):
                raise ApiError(416, "Неверный Range")
            a, b = match.groups()
            if a:
                start = int(a)
                end = min(int(b), end) if b else end
            else:
                suffix = int(b)
                start = max(0, size - suffix)
            if start >= size or end < start:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        count = end - start + 1
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(206 if requested else 200)
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(count))
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if requested:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start)
            remaining = count
            while remaining:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def payload(self):
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise ApiError(415, "Content-Type должен быть application/json")
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(400, "Неверный Content-Length")
        if size < 2 or size > 128_000:
            raise ApiError(413, "JSON должен быть не более 128 KB")
        try:
            return json.loads(self.rfile.read(size))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError(400, "Неверный JSON")

    def dispatch(self):
        path = unquote(urlsplit(self.path).path)
        if path.startswith("/api/"):
            self.api_route(path)
        elif self.command == "GET":
            self.static(path)
        else:
            raise ApiError(404, "Маршрут не найден")

    def api_route(self, path):
        parts = [part for part in path.strip("/").split("/") if part]
        if self.command == "GET" and parts == ["api", "health"]:
            return self.send_json({"ok": True, "service": "CQA local bridge", "model_api": "OpenRouter via main.py"})
        if self.command == "GET" and parts == ["api", "bootstrap"]:
            return self.send_json({"conversations": summaries(), "checklist": checklist(), "appeals": appeals()})
        if parts == ["api", "checklist"]:
            if self.command == "GET":
                return self.send_json(checklist())
            if self.command == "PUT":
                with FILE_LOCK:
                    value = validate_checklist(self.payload())
                    atomic_json(data_root() / "checklist.json", value)
                    return self.send_json(value)
        if parts == ["api", "conversations"] and self.command == "GET":
            return self.send_json(summaries())
        if len(parts) >= 3 and parts[:2] == ["api", "conversations"]:
            cid = parts[2]
            if len(parts) == 3 and self.command == "GET":
                return self.send_json(detail(cid))
            if len(parts) == 4 and parts[3] == "audio" and self.command == "GET":
                return self.send_audio(cid)
            if len(parts) == 4 and parts[3] == "evaluate" and self.command == "POST":
                return self.send_json(run_evaluation(cid))
            if len(parts) == 5 and parts[3] == "criteria" and self.command == "PATCH":
                if not parts[4].isdigit():
                    raise ApiError(400, "Некорректный ID критерия")
                return self.send_json(assign_manual_result(cid, int(parts[4]), self.payload()))
        if parts == ["api", "appeals"]:
            if self.command == "GET":
                return self.send_json(appeals())
            if self.command == "POST":
                return self.send_json(new_appeal(self.payload()), status=201)
        if len(parts) == 4 and parts[:2] == ["api", "appeals"] and parts[3] == "review" and self.command == "PATCH":
            return self.send_json(review_appeal(parts[2], self.payload()))
        raise ApiError(404, "API-эндпоинт не найден")

    def static(self, path):
        if path == "/":
            path = "/index.html"
        try:
            requested = (FRONTEND / path.lstrip("/")).resolve()
            if not requested.is_relative_to(FRONTEND.resolve()) or not requested.is_file() or requested.name.startswith("."):
                raise ApiError(404, "Статический файл не найден")
        except (ValueError, OSError):
            raise ApiError(404, "Файл не найден")
        data = requested.read_bytes()
        mime = mimetypes.guess_type(requested.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith(("text/", "application/javascript")) else ""))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_PUT(self):
        self.handle_request()

    def do_PATCH(self):
        self.handle_request()

    def handle_request(self):
        try:
            self.dispatch()
        except ApiError as e:
            self.send_json({"error": str(e)}, e.code)
        except (OSError, ValueError, KeyError) as e:
            self.send_json({"error": f"Ошибка сервера: {type(e).__name__}: {str(e)[:200]}"}, 500)


def main():
    parser = argparse.ArgumentParser(description="Run CQA frontend/API with existing CSV and evaluator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), CQAHandler)
    print(f"CQA interface: http://{args.host}:{args.port}")
    print(f"Data folder: {data_root()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
