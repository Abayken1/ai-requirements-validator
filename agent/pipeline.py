"""Порядок работы агентов:

документ → нарезчик → [ревьюер требований (пачками) + 2 ревьюера набора] параллельно
         → проверка цитат (код) → сводка (код) → итог (модель) → отчёт
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from . import __version__, prompts
from .extract import extract_text
from .llm import LLMError
from .reviewer import review_requirements, review_set
from .segmenter import segment
from .verify import Document

ROOT = Path(__file__).resolve().parent.parent
CRITERIA_FILE = ROOT / "config" / "criteria.json"
SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2}


def load_criteria() -> dict:
    return json.loads(CRITERIA_FILE.read_text(encoding="utf-8"))


def agent_version() -> str:
    """Версия агента для паспорта прогона: коммит git (или номер версии, если git нет)."""
    try:
        commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, timeout=5).stdout.strip()
        if not commit:
            raise OSError
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "agent", "config",
                                "validate.py", "requirements.txt"],
                               capture_output=True, text=True, timeout=5).stdout.strip()
        tag = subprocess.run(["git", "-C", str(ROOT), "describe", "--tags", "--exact-match"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        label = f"коммит {commit}" + (f", тег {tag}" if tag else "")
        return label + (" + незакоммиченные изменения" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return f"v{__version__} (без git)"


def _now() -> str:
    t = datetime.now().astimezone()
    z = t.strftime("%z")
    return t.strftime("%d.%m.%Y %H:%M") + f" (UTC{z[:3]}:{z[3:]})"


def _doc_title(text: str, filename: str) -> str:
    for line in text.splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return s[:150]
    return filename


def run(path: Path, llm, progress=None, batch_size: int | None = None, max_parallel: int | None = None) -> dict:
    say = progress or (lambda msg: None)
    started = time.time()
    batch_size = batch_size or int(os.getenv("REVIEW_BATCH", "8"))
    max_parallel = max_parallel or int(os.getenv("MAX_PARALLEL", "2"))
    criteria = load_criteria()
    errors: list[str] = []

    say(f"📄 Читаю документ: {path.name}")
    text = extract_text(path.read_bytes(), path.name)
    doc = Document(text)

    say("✂️  Нарезчик: выделяю отдельные требования…")
    seg = segment(doc, llm)
    reqs = seg["requirements"]
    if not reqs:
        raise LLMError("Нарезчик не нашёл в документе ни одного требования.")
    say(f"   выделено требований: {len(reqs)}")

    # --- ревью: пачки требований и проверка набора — параллельно
    batches = [reqs[i:i + batch_size] for i in range(0, len(reqs), batch_size)]
    say(f"🔍 Ревьюеры: {len(batches)} пачк(и) требований + проверка набора, параллельно по {max_parallel}…")
    reviews: dict[str, dict] = {}
    set_findings: list[dict] = []
    failed_batches: list[list[dict]] = []
    with ThreadPoolExecutor(max_workers=max(1, max_parallel)) as pool:
        futures = {}
        for n, b in enumerate(batches, 1):
            futures[pool.submit(review_requirements, b, text, criteria["requirement"], llm, f"review_{n}")] = ("batch", n, b)
        for group in ("consistency", "completeness"):
            futures[pool.submit(review_set, group, reqs, text, criteria["set"], llm)] = ("set", group, None)
        for fut in as_completed(futures):
            kind, name, batch = futures[fut]
            try:
                out = fut.result()
                if kind == "batch":
                    reviews.update(out)
                    say(f"   пачка {name}: {sum(len(v['defects']) for v in out.values())} дефектов")
                else:
                    set_findings += [dict(f, group=name) for f in out]
                    say(f"   набор ({'согласованность' if name == 'consistency' else 'полнота и структура'}): {len(out)} замечаний")
            except Exception as exc:  # одна часть упала — остальные продолжают
                errors.append(f"Не выполнена {'пачка ' + str(name) if kind == 'batch' else 'проверка набора (' + name + ')'}: {exc}")
                if kind == "batch":
                    failed_batches.append(batch)
                say(f"   ⚠️ {errors[-1]}")
    if len(errors) == len(futures):
        raise LLMError(errors[0])

    # повторяем упавшие пачки по одной, медленно
    for b in failed_batches:
        try:
            reviews.update(review_requirements(b, text, criteria["requirement"], llm, "review_retry"))
            errors.append(f"Пачку {b[0]['id']}–{b[-1]['id']} удалось проверить со второй попытки.")
        except Exception as exc:
            errors.append(f"Требования {b[0]['id']}–{b[-1]['id']} остались непроверенными: {exc}")

    # --- проверка цитат кодом
    say("🧾 Проверяю цитаты в замечаниях…")
    unverified: list[dict] = []
    defects: list[dict] = []
    for r in reqs:
        rv = reviews.get(r["id"], {"criteria": {}, "defects": [], "missing": True})
        r["criteria"] = rv["criteria"]
        r["reviewed"] = not rv.get("missing")
        kept = []
        for d in sorted(rv["defects"], key=lambda d: SEVERITY_ORDER[d["severity"]]):
            d["check"] = doc.check(d["quote"]) if d["quote"] else doc.check(r["quote"])
            d["req_id"] = r["id"]
            (unverified if d["check"] == "not_found" else kept).append(d)
        # нарушение засчитывается, только если есть подтверждённый дефект с цитатой
        kept_crit = {k["criterion"] for k in kept}
        for cid, st in list(r["criteria"].items()):
            if st == "violation" and cid not in kept_crit:
                r["criteria"][cid] = "unconfirmed"
        r["defects"] = kept
        defects += kept
    for n, d in enumerate(defects, 1):
        d["id"] = f"Д-{n:02d}"

    findings = []
    for f in sorted(set_findings, key=lambda f: (f["group"] != "consistency", SEVERITY_ORDER[f["severity"]])):
        f["check"] = doc.check(f["quote"])
        if f["check"] == "not_found" or (f["bad_ids"] and not f["req_ids"]):
            unverified.append(f)
        else:
            findings.append(f)
    for n, f in enumerate(findings, 1):
        f["id"] = f"Н-{n:02d}"
    for n, u in enumerate(unverified, 1):
        u["id"] = f"X-{n:02d}"

    summary = summarize(reqs, defects, findings, criteria)
    incomplete = any(not r["reviewed"] for r in reqs)
    summary["incomplete"] = incomplete

    say("🏁 Пишу итог…")
    verdict = make_verdict(summary, defects, findings, llm, errors)

    return {
        "passport": {
            "title": _doc_title(text, path.name),
            "file": path.name,
            "date": _now(),
            "model": llm.model,
            "provider": getattr(llm, "provider", ""),
            "agent_version": agent_version(),
            "duration": round(time.time() - started, 1),
            "llm_calls": llm.calls,
        },
        "requirements": reqs,
        "not_requirements": seg["not_requirements"],
        "segment_notes": seg["notes"],
        "defects": defects,
        "findings": findings,
        "unverified": unverified,
        "summary": summary,
        "verdict": verdict,
        "errors": errors,
        "criteria": criteria,
    }


def summarize(reqs: list[dict], defects: list[dict], findings: list[dict], criteria: dict) -> dict:
    allv = defects + findings
    by_sev = {s: sum(1 for d in allv if d["severity"] == s) for s in SEVERITY_ORDER}
    by_crit = {c["id"]: sum(1 for d in defects if d["criterion"] == c["id"]) for c in criteria["requirement"]}
    by_set = {c["id"]: sum(1 for f in findings if f["criterion"] == c["id"]) for c in criteria["set"]}
    critical = [d for d in allv if d["severity"] == "critical"]
    groups = {
        "Непроверяемые формулировки": [d for d in critical if d["criterion"] in ("verifiable",)],
        "Противоречия": [d for d in critical if d["criterion"] in ("correct", "consistent")],
        "Неполные формулировки и пропуски": [d for d in critical if d["criterion"] in ("complete",)],
        "Неоднозначные формулировки": [d for d in critical if d["criterion"] in ("unambiguous", "comprehensible")],
    }
    used = {id(d) for g in groups.values() for d in g}
    groups["Другие критичные"] = [d for d in critical if id(d) not in used]
    return {
        "requirements": len(reqs),
        "with_defects": sum(1 for r in reqs if r["defects"]),
        "clean": sum(1 for r in reqs if r["reviewed"] and not r["defects"]),
        "not_reviewed": sum(1 for r in reqs if not r["reviewed"]),
        "violations": len(allv),
        "req_defects": len(defects),
        "set_findings": len(findings),
        "by_severity": by_sev,
        "by_criterion": by_crit,
        "by_set_criterion": by_set,
        "critical_groups": {k: [d["id"] for d in v] for k, v in groups.items() if v},
    }


def make_verdict(summary: dict, defects: list[dict], findings: list[dict], llm, errors: list[str]) -> dict:
    sev = summary["by_severity"]
    if sev["critical"]:
        decision = "Нет"
        rule = "в документе есть критичные дефекты — до их исправления разработку и тестирование начинать нельзя"
    elif sev["major"]:
        decision = "С оговорками"
        rule = "критичных дефектов нет, но существенные замечания нужно закрыть до начала тестирования"
    else:
        decision = "Да"
        rule = "критичных и существенных дефектов не найдено"
    if summary["incomplete"]:
        rule += "; часть требований не проверена — итог предварительный"

    top = sorted(defects + findings, key=lambda d: SEVERITY_ORDER[d["severity"]])[:25]
    user = (f"Решение системы: «{decision}» — {rule}.\n"
            f"Требований: {summary['requirements']}, нарушений: {summary['violations']} "
            f"(критичных {sev['critical']}, существенных {sev['major']}, незначительных {sev['minor']}).\n\n"
            "Самые серьёзные замечания:\n" + "\n".join(
                f"- {d['id']} [{d['severity']}] {', '.join([d['req_id']] if 'req_id' in d else d.get('req_ids', [])) or d.get('section', '')}: {d['problem']}"
                for d in top))
    conclusion, fix_first = "", []
    try:
        data = llm.chat_json(prompts.VERDICT_SYSTEM, user, step="verdict")
        data = data if isinstance(data, dict) else {}
        conclusion = str(data.get("conclusion") or "").strip()
        fix_first = [str(x).strip() for x in data.get("fix_first") or [] if str(x).strip()][:5]
    except LLMError as exc:
        errors.append(f"Итог написан без модели: {exc}")
    if not conclusion:
        conclusion = f"Решение: {decision.lower()} — {rule}."
    if not fix_first:
        fix_first = [f"{d['id']}: {d['problem']}" for d in top[:5]]
    return {"decision": decision, "rule": rule, "conclusion": conclusion, "fix_first": fix_first}
