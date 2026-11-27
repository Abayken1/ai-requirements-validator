"""Модель-заглушка для тестов: отвечает записанными ответами из tests/fixtures/responses.json."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRITERIA = json.loads((HERE.parent / "config" / "criteria.json").read_text(encoding="utf-8"))


class FakeLLM:
    model = "fake-model"
    provider = "тестовая заглушка"

    def __init__(self, path: Path = HERE / "fixtures" / "responses.json"):
        self.data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.calls = 0

    def answer(self, step: str, user: str):
        d = self.data
        if step == "segment":
            return copy.deepcopy(d["segment"])
        if step.startswith("review"):
            ids = re.findall(r"^(Т-\d{2}) \[", user, re.M)
            out = []
            for i in ids:
                defects = copy.deepcopy(d["reviews"].get(i, []))
                statuses = {c["id"]: "ok" for c in CRITERIA["requirement"]}
                for x in defects:
                    statuses[x["criterion"]] = "violation"
                out.append({"id": i, "criteria": statuses, "defects": defects})
            return {"reviews": out}
        if step.startswith("set_"):
            return copy.deepcopy(d[step])
        if step == "verdict":
            return copy.deepcopy(d["verdict"])
        return {}

    def chat_json(self, system: str, user: str, step: str = ""):
        self.calls += 1
        return self.answer(step, user)
