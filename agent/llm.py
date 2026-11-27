"""Подключение к языковой модели.

Используем OpenAI-совместимый API: один и тот же код работает с Gemini,
OpenAI, Claude, Ollama и другими — меняются только ключ и модель в .env.
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time

import requests

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODEL = "gemini-3.1-flash-lite"


class LLMError(RuntimeError):
    """Понятная ошибка для интерфейса: что сломалось и что с этим делать."""


# --------------------------------------------------------------------------- JSON
def extract_json(text: str):
    """Достаёт JSON из ответа модели, даже если он обёрнут в ```json ... ``` или пояснения."""
    if not text or not text.strip():
        raise ValueError("пустой ответ")
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    for candidate in (t, _first_json_object(t)):
        if not candidate:
            continue
        for variant in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):  # лишние запятые
            try:
                return json.loads(variant)
            except json.JSONDecodeError:
                continue
    raise ValueError("в ответе нет корректного JSON")


def _first_json_object(t: str) -> str:
    """Находит первый объект {...} с учётом вложенных скобок и строк."""
    start = t.find("{")
    if start == -1:
        return ""
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return t[start:i + 1]
    return ""


# ------------------------------------------------------------------- клиент API
class LLMClient:
    """Клиент любого OpenAI-совместимого API (Gemini, OpenAI, Groq, OpenRouter...)."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL,
                 temperature: float | None = 0.2, json_mode: bool = False,
                 timeout: int = 600, max_retries: int = 6):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
        self.model = (model or DEFAULT_MODEL).strip()
        self.temperature = temperature
        self.json_mode = json_mode
        # Стриминг: длинные ответы приходят частями, и шлюз провайдера не обрывает запрос.
        self.stream = os.getenv("LLM_STREAM", "0") == "1"
        self.timeout = int(os.getenv("LLM_TIMEOUT", timeout))  # долгие ответы рассуждающих моделей
        self.max_retries = max_retries
        self.backoff = float(os.getenv("LLM_BACKOFF", "5"))  # базовая пауза между повторами, секунды
        self.calls = 0
        self.provider = "OpenAI-совместимый API"
        self._lock = threading.Lock()

    @property
    def label(self) -> str:
        return self.model

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def chat(self, system: str, user: str, step: str = "") -> str:
        """Один запрос к модели. Сам повторяет запрос при перегрузке (429) и сбоях сервера."""
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{self.base_url}/chat/completions"
        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                if self.stream:
                    resp = requests.post(url, headers=self._headers(), json={**payload, "stream": True},
                                         timeout=(30, self.timeout), stream=True)
                else:
                    resp = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"нет связи с API ({exc.__class__.__name__})"
                self._sleep(attempt)
                continue

            if resp.status_code == 200:
                with self._lock:
                    self.calls += 1
                if self.stream:
                    try:
                        return _content_from_stream(resp)
                    except requests.RequestException as exc:
                        last_error = f"обрыв потока ({exc.__class__.__name__})"
                        self._sleep(attempt)
                        continue
                return _content_from_response(resp)

            body = _error_text(resp)
            low = body.lower()
            # Некоторые модели не принимают temperature или response_format — убираем и пробуем снова.
            if resp.status_code == 400 and "temperature" in payload and "temperature" in low:
                payload.pop("temperature")
                continue
            if resp.status_code == 400 and "response_format" in payload and ("response_format" in low or "json" in low):
                payload.pop("response_format")
                continue
            if resp.status_code == 429 and ("insufficient_quota" in low or "no credits" in low or "billing" in low):
                raise LLMError("На счёте API нет кредитов (insufficient_quota). Пополни баланс у провайдера или возьми другой ключ.")
            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = f"HTTP {resp.status_code}"
                self._sleep(attempt, resp.headers.get("Retry-After"))
                continue
            raise LLMError(_explain(resp.status_code, body, self.model, step))

        hint = " Похоже, упёрлись в лимит бесплатного тарифа: подожди минуту или поставь MAX_PARALLEL=1 в .env." \
            if "429" in last_error else ""
        raise LLMError(f"Модель не ответила после {self.max_retries} попыток ({last_error}).{hint}")

    def chat_json(self, system: str, user: str, step: str = ""):
        """Запрос, ответ на который должен быть JSON. Если модель ошиблась с форматом, просим ещё раз."""
        text = self.chat(system, user, step)
        try:
            return extract_json(text)
        except ValueError:
            retry = user + "\n\nТвой прошлый ответ не был корректным JSON. Верни ТОЛЬКО JSON по заданной схеме, без пояснений и без markdown."
            try:
                return extract_json(self.chat(system, retry, step))
            except ValueError as exc:
                raise LLMError(f"Агент «{step}» дважды вернул ответ не в формате JSON.") from exc

    def list_models(self) -> list[str]:
        """Список моделей, доступных по ключу (кнопка «Проверить подключение»)."""
        try:
            resp = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=30)
        except requests.RequestException as exc:
            raise LLMError(f"Нет связи с API: {exc.__class__.__name__}. Проверь интернет и адрес API.") from exc
        if resp.status_code != 200:
            raise LLMError(_explain(resp.status_code, _error_text(resp), self.model, "models"))
        ids = []
        for m in resp.json().get("data", []):
            mid = str(m.get("id", ""))
            ids.append(mid.split("/", 1)[1] if mid.startswith("models/") else mid)
        skip = ("embed", "image", "imagen", "veo", "tts", "audio", "live", "aqa", "lyria",
                "robotics", "computer-use", "native", "vision", "whisper", "dall-e", "moderation")
        return sorted({i for i in ids if i and not any(s in i.lower() for s in skip)})

    def _sleep(self, attempt: int, retry_after: str | None = None) -> None:
        if attempt >= self.max_retries:
            return
        try:
            wait = float(retry_after) if retry_after else self.backoff * (2 ** (attempt - 1))
        except ValueError:
            wait = self.backoff * (2 ** (attempt - 1))
        time.sleep(min(60.0, wait + random.random() * 0.5))


def _content_from_stream(resp: requests.Response) -> str:
    """Собирает текст ответа из потока Server-Sent Events (stream=true)."""
    if "text/event-stream" not in resp.headers.get("Content-Type", ""):
        return _content_from_response(resp)
    parts = []
    for raw in resp.iter_lines(decode_unicode=False):
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            delta = json.loads(data)["choices"][0].get("delta") or {}
        except (ValueError, KeyError, IndexError, TypeError):
            continue
        piece = delta.get("content")
        if isinstance(piece, str):
            parts.append(piece)
    return "".join(parts)


def _content_from_response(resp: requests.Response) -> str:
    try:
        data = resp.json()
        content = data["choices"][0]["message"].get("content")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LLMError("Модель вернула ответ в непонятном формате.") from exc
    if isinstance(content, list):  # некоторые провайдеры отдают список частей
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""


def _error_text(resp: requests.Response) -> str:
    try:
        data = resp.json()
        if isinstance(data, list) and data:
            data = data[0]
        err = data.get("error", data) if isinstance(data, dict) else data
        if isinstance(err, dict):
            return str(err.get("message") or err)
        return str(err)
    except ValueError:
        return resp.text[:500]


def _explain(status: int, body: str, model: str, step: str) -> str:
    low = body.lower()
    where = f" (шаг: {step})" if step else ""
    if "api key" in low or "api_key" in low or status == 401:
        return ("Ключ API не подходит" + where + ". Проверь ключ в файле .env. "
                "Бесплатный ключ Gemini: https://aistudio.google.com/apikey")
    if status == 404 or ("model" in low and ("not found" in low or "not supported" in low)):
        return (f"Модель «{model}» не найдена{where}. Посмотри доступные: python validate.py --list-models, "
                "и поменяй модель в .env.")
    if status == 403:
        return "Доступ запрещён" + where + ". Проверь ключ и что Gemini API включено для твоего проекта в Google AI Studio."
    return f"Ошибка API {status}{where}: {body[:300]}"


# ------------------------------------------------------------- настройки
PROVIDERS = [
    # (переменная с ключом, переменная с моделью, адрес API, модель по умолчанию, название)
    ("GEMINI_API_KEY", "GEMINI_MODEL", DEFAULT_BASE_URL, DEFAULT_MODEL, "Google Gemini"),
    ("OPENAI_API_KEY", "OPENAI_MODEL", "https://api.openai.com/v1", "gpt-4o-mini", "OpenAI"),
    ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "https://api.anthropic.com/v1", "", "Anthropic Claude"),
]


def client_from_env(model: str | None = None) -> LLMClient:
    """Выбирает провайдера по ключу в .env. Подходит любой OpenAI-совместимый API."""
    if os.getenv("LLM_API_KEY", "").strip():  # свой адрес: OpenRouter, Groq и т.п.
        c = LLMClient(os.environ["LLM_API_KEY"], os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URL,
                      model or os.getenv("LLM_MODEL") or DEFAULT_MODEL)
        c.provider = os.getenv("LLM_PROVIDER_NAME", "OpenAI-совместимый API")
        return c
    for key_var, model_var, url, default_model, name in PROVIDERS:
        key = os.getenv(key_var, "").strip()
        if key and not key.startswith("your_"):
            chosen = model or os.getenv(model_var) or default_model
            if not chosen:
                raise LLMError(f"Укажи модель в {model_var} в файле .env.")
            c = LLMClient(key, url, chosen)
            c.provider = name
            return c
    if os.getenv("OLLAMA_BASE_URL", "").strip():  # локальная модель
        c = LLMClient("ollama", os.environ["OLLAMA_BASE_URL"].rstrip("/") + "/v1",
                      model or os.getenv("OLLAMA_MODEL") or "qwen2.5:7b")
        c.provider = "Ollama (локально)"
        return c
    raise LLMError("Не найден ключ модели. Скопируй .env.example в .env и вставь GEMINI_API_KEY "
                   "(бесплатный ключ: https://aistudio.google.com/apikey).")
