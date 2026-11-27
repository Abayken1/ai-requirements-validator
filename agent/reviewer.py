"""Агенты-ревьюеры.

1. Ревьюер требований — проверяет каждое требование по 9 критериям ISO 29148 (п. 5.2.5).
   Требования идут пачками, чтобы уложиться в лимиты бесплатного тарифа.
2. Два ревьюера набора — проверяют документ целиком (ISO 29148 п. 5.2.6, IEEE 830 п. 4.3):
   один ищет противоречия и непонятные места, второй — пропуски и проблемы структуры.
Все они работают параллельно.
"""
from __future__ import annotations

from . import prompts

STATUSES = {"ok", "violation", "na"}
SEVERITY = {
    "critical": "critical", "критично": "critical", "критичный": "critical", "blocker": "critical", "high": "major",
    "major": "major", "существенно": "major", "существенный": "major", "medium": "major",
    "minor": "minor", "незначительно": "minor", "незначительный": "minor", "low": "minor",
}


def criteria_text(criteria: list[dict]) -> str:
    return "\n".join(f"- {c['id']} — {c['name']} ({c['source']}): {c['question']}" for c in criteria)


def _req_line(r: dict) -> str:
    where = r["doc_id"] or "без номера"
    return f"{r['id']} [{where}; {r['section']}]: {r['quote']}"


def _defect(d: dict, valid: set) -> dict | None:
    crit = str(d.get("criterion", "")).strip().lower()
    if crit not in valid:
        return None
    return {
        "criterion": crit,
        "severity": SEVERITY.get(str(d.get("severity", "")).strip().lower(), "major"),
        "quote": str(d.get("quote") or "").strip(),
        "problem": str(d.get("problem") or "").strip(),
        "fix": str(d.get("fix") or "").strip(),
    }


def review_requirements(batch: list[dict], doc_text: str, criteria: list[dict], llm, step: str) -> dict:
    """Возвращает {id требования: {"criteria": {...}, "defects": [...]}} для одной пачки."""
    system = prompts.fill(prompts.REVIEW_SYSTEM, criteria=criteria_text(criteria),
                          severity=prompts.SEVERITY_RULES, quotes=prompts.QUOTE_RULES)
    user = ("Проверяемый документ целиком (для контекста):\n<<<\n" + doc_text + "\n>>>\n\n"
            "Требования, которые нужно проверить сейчас:\n" + "\n".join(_req_line(r) for r in batch))
    data = llm.chat_json(system, user, step=step)
    data = data if isinstance(data, dict) else {}
    valid = {c["id"] for c in criteria}
    by_id = {str(x.get("id", "")).strip(): x for x in data.get("reviews") or [] if isinstance(x, dict)}

    out = {}
    for r in batch:
        raw = by_id.get(r["id"])
        if raw is None:
            out[r["id"]] = {"criteria": {}, "defects": [], "missing": True}
            continue
        statuses = {}
        for cid, st in (raw.get("criteria") or {}).items():
            cid, st = str(cid).strip().lower(), str(st).strip().lower()
            if cid in valid and st in STATUSES:
                statuses[cid] = st
        defects = [d for d in (_defect(x, valid) for x in raw.get("defects") or [] if isinstance(x, dict)) if d]
        for d in defects:  # дефект без отметки — значит критерий нарушен
            statuses[d["criterion"]] = "violation"
        out[r["id"]] = {"criteria": statuses, "defects": defects, "missing": False}
    return out


def review_set(group: str, reqs: list[dict], doc_text: str, set_criteria: list[dict], llm) -> list[dict]:
    crit = [c for c in set_criteria if c["group"] == group]
    system = prompts.fill(prompts.SET_SYSTEM, criteria=criteria_text(crit), focus=prompts.SET_FOCUS[group],
                          severity=prompts.SEVERITY_RULES, quotes=prompts.QUOTE_RULES)
    user = ("Проверяемый документ:\n<<<\n" + doc_text + "\n>>>\n\n"
            "Требования, которые выделил нарезчик (на них ссылайся в req_ids):\n" + "\n".join(_req_line(r) for r in reqs))
    data = llm.chat_json(system, user, step=f"set_{group}")
    data = data if isinstance(data, dict) else {}
    valid = {c["id"] for c in crit}
    req_ids = {r["id"] for r in reqs}
    out = []
    for x in data.get("findings") or []:
        if not isinstance(x, dict):
            continue
        d = _defect(x, valid)
        if not d:
            continue
        ids = x.get("req_ids") or []
        ids = [ids] if isinstance(ids, str) else ids
        d["req_ids"] = [str(i).strip() for i in ids if str(i).strip() in req_ids]
        d["bad_ids"] = [str(i).strip() for i in ids if str(i).strip() and str(i).strip() not in req_ids]
        d["section"] = str(x.get("section") or "").strip()
        out.append(d)
    return out
