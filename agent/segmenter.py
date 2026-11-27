"""Агент-нарезчик: выделяет из документа отдельные требования.

Модель находит требования и их цитаты, код страхует:
- каждую цитату ищет в документе и при небольших искажениях заменяет точной строкой;
- требования с номерами из документа (например «Ф-1») добавляет сам, если модель их пропустила;
- сортирует требования в порядке документа и присваивает идентификаторы отчёта Т-01, Т-02…
"""
from __future__ import annotations

import re

from . import prompts
from .verify import Document, clean_markdown, norm

EXPLICIT_ID = re.compile(r"^[\s>*\-•]*\**\s*([А-ЯЁA-Z]{1,4}-\d{1,3})\.?\**\s+(.+)$")
KINDS = {"functional", "nonfunctional", "data", "integration", "ui", "api", "acceptance", "other"}


def explicit_requirements(doc: Document) -> list[dict]:
    """Требования, у которых в документе есть свой номер: «Ф-1. …», «НФ-2 …»."""
    out = []
    for line in doc.lines:
        m = EXPLICIT_ID.match(line)
        if m:
            out.append({"doc_id": m.group(1), "quote": clean_markdown(m.group(2)), "line": line})
    return out


def segment(doc: Document, llm) -> dict:
    data = llm.chat_json(prompts.SEGMENT_SYSTEM, "Документ:\n<<<\n" + doc.text + "\n>>>", step="segment")
    data = data if isinstance(data, dict) else {}
    reqs, notes, seen = [], [], set()

    for r in data.get("requirements") or []:
        if not isinstance(r, dict):
            continue
        quote = clean_markdown(str(r.get("quote", "")))
        status = doc.check(quote)
        if status == "not_found":
            snapped = doc.best_line(quote)
            if not snapped:
                notes.append(f"Отброшено требование без подтверждённой цитаты: «{quote[:80]}»")
                continue
            quote, status = snapped, "восстановлена по тексту"
        key = norm(quote)
        if key in seen:
            continue
        seen.add(key)
        kind = str(r.get("kind", "other")).strip().lower()
        reqs.append({
            "doc_id": str(r.get("doc_id") or "").strip(),
            "section": str(r.get("section") or "").strip() or "—",
            "quote": quote,
            "summary": str(r.get("summary") or "").strip() or quote[:90],
            "kind": kind if kind in KINDS else "other",
            "quote_check": status,
        })

    # страховка: все пронумерованные в документе требования должны попасть в отчёт
    have = {r["doc_id"] for r in reqs if r["doc_id"]}
    for ex in explicit_requirements(doc):
        if ex["doc_id"] not in have:
            reqs.append({"doc_id": ex["doc_id"], "section": "—", "quote": ex["quote"],
                         "summary": ex["quote"][:90], "kind": "functional", "quote_check": "добавлено кодом"})
            notes.append(f"Требование {ex['doc_id']} модель пропустила — код добавил его по номеру в документе.")

    reqs.sort(key=lambda r: (doc.position(r["quote"]) if doc.position(r["quote"]) >= 0 else 10 ** 9))
    for n, r in enumerate(reqs, 1):
        r["id"] = f"Т-{n:02d}"

    not_reqs = []
    for x in data.get("not_requirements") or []:
        if isinstance(x, dict) and (x.get("section") or x.get("note")):
            not_reqs.append({"section": str(x.get("section", "")).strip(), "note": str(x.get("note", "")).strip()})
    return {"requirements": reqs, "not_requirements": not_reqs, "notes": notes}
