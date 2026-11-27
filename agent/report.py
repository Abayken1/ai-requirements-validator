"""Отчёт: одна структура блоков → Markdown и PDF.

Разделы идут в порядке из задания (README организаторов, п. 6):
паспорт прогона → нарезка → проверка каждого требования → проверка набора → сводка → итог.
"""
from __future__ import annotations

import json
from pathlib import Path

SEV = {"critical": "критично", "major": "существенно", "minor": "незначительно"}
MARK = {"ok": "✓", "violation": "✗", "na": "—", "unconfirmed": "?"}
CHECK = {"exact": "цитата найдена в документе", "fuzzy": "цитата найдена почти дословно",
         "empty": "без цитаты", "not_found": "цитаты нет в документе"}


def _q(s: str, limit: int = 0) -> str:
    s = (s or "").strip()
    if limit and len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0] + "…"
    return f"«{s}»" if s else "—"


def build_blocks(res: dict) -> list[tuple]:
    p, s, v = res["passport"], res["summary"], res["verdict"]
    crit_req = res["criteria"]["requirement"]
    crit_set = res["criteria"]["set"]
    names = {c["id"]: c["name"] for c in crit_req}
    set_names = {c["id"]: c["name"] for c in crit_set}
    B: list[tuple] = [("title", "Отчёт о проверке требований"),
                      ("p", f"Документ: **{p['title']}**")]

    # 1. Паспорт
    B += [("h1", "1. Паспорт прогона"),
          ("kv", [("Документ", p["title"]), ("Файл", p["file"]), ("Дата прогона", p["date"]),
                  ("Модель", f"{p['model']} ({p['provider']})" if p["provider"] else p["model"]),
                  ("Версия агента", p["agent_version"]),
                  ("Критерии", "ISO/IEC/IEEE 29148:2018, п. 5.2.5 и 5.2.6; IEEE Std 830-1998, п. 4.3"),
                  ("Время работы", f"{p['duration']} с, запросов к модели: {p['llm_calls']}")])]
    if res["errors"]:
        B += [("note", "Замечания о прогоне:"), ("bullets", res["errors"])]

    # 2. Нарезка
    reqs = res["requirements"]
    no_id = sum(1 for r in reqs if not r["doc_id"])
    B += [("h1", "2. Как агент нарезал требования"),
          ("p", f"Агент выделил {len(reqs)} требований. Номера из документа сохранены"
                + (f"; у {no_id} требований номера не было — агент присвоил свой идентификатор и сохранил цитату." if no_id else ".")),
          ("table", ["ID в отчёте", "ID в документе", "Раздел", "Краткая формулировка", "Цитата из документа"],
           [[r["id"], r["doc_id"] or "нет номера", r["section"], r["summary"], _q(r["quote"], 160)] for r in reqs],
           [0.09, 0.11, 0.2, 0.27, 0.33])]
    if res["not_requirements"]:
        B += [("p", "Не считаются требованиями (служебные разделы):"),
              ("bullets", [f"**{x['section']}** — {x['note']}" if x["section"] else x["note"] for x in res["not_requirements"]])]
    if res["segment_notes"]:
        B += [("bullets", res["segment_notes"])]

    # 3. Каждое требование
    B += [("h1", "3. Проверка каждого требования"),
          ("p", "Каждое требование проверено по 9 критериям ISO/IEC/IEEE 29148:2018, п. 5.2.5. "
                "✓ — нарушений нет, ✗ — нарушение, — — неприменимо, ? — модель отметила нарушение, "
                "но не подтвердила его цитатой (не учитывается)."),
          ("matrix", ["ID"] + [c["short"] for c in crit_req],
           [[r["id"]] + [MARK.get(r["criteria"].get(c["id"]), "·") for c in crit_req] for r in reqs])]
    for r in reqs:
        head = f"{r['id']} · {r['doc_id'] or 'без номера'} · {r['section']}"
        B += [("h3", head), ("quote", r["quote"])]
        if not r["reviewed"]:
            B += [("note", "Требование не проверено: модель не вернула результат.")]
        elif not r["defects"]:
            B += [("ok", "Нарушений не найдено.")]
        for d in r["defects"]:
            B += [("defect", f"{d['id']} · {names[d['criterion']]} · {SEV[d['severity']]}",
                   [("Проблема", d["problem"]), ("Место", _q(d["quote"] or r["quote"], 220)),
                    ("Исправить", d["fix"] or "—")])]

    # 4. Набор
    B += [("h1", "4. Проверка всего набора требований"),
          ("p", "Набор проверен целиком по ISO/IEC/IEEE 29148:2018, п. 5.2.6 и IEEE Std 830-1998, п. 4.3."),
          ("table", ["Критерий", "Источник", "Результат"],
           [[c["name"], c["source"], (f"✗ замечаний: {s['by_set_criterion'][c['id']]}" if s["by_set_criterion"][c["id"]]
                                      else "✓ нарушений не найдено")] for c in crit_set],
           [0.35, 0.35, 0.3])]
    if not res["findings"]:
        B += [("ok", "Замечаний к набору нет.")]
    for f in res["findings"]:
        where = ", ".join(f["req_ids"]) or f["section"] or "документ целиком"
        B += [("defect", f"{f['id']} · {set_names[f['criterion']]} · {SEV[f['severity']]} · {where}",
               [("Проблема", f["problem"]), ("Место", _q(f["quote"], 260)), ("Исправить", f["fix"] or "—")])]

    # 5. Сводка
    sev = s["by_severity"]
    B += [("h1", "5. Сводка"),
          ("kv", [("Требований выделено", str(s["requirements"])),
                  ("С нарушениями", str(s["with_defects"])),
                  ("Без нарушений", str(s["clean"])),
                  ("Не проверено", str(s["not_reviewed"])),
                  ("Нарушений всего", f"{s['violations']}: к требованиям — {s['req_defects']}, к набору — {s['set_findings']}"),
                  ("По серьёзности", f"критичных — {sev['critical']}, существенных — {sev['major']}, незначительных — {sev['minor']}")]),
          ("table", ["Критерий требования", "Нарушений"],
           [[names[k], str(n)] for k, n in s["by_criterion"].items()], [0.7, 0.3])]
    if s["critical_groups"]:
        lookup = {d["id"]: d for d in res["defects"] + res["findings"]}
        B += [("p", "**Критичные дефекты:**")]
        for group, ids in s["critical_groups"].items():
            items = []
            for i in ids:
                d = lookup[i]
                where = d.get("req_id") or ", ".join(d.get("req_ids", [])) or d.get("section", "")
                items.append(f"**{i}** ({where}): {d['problem']}")
            B += [("p", f"*{group}*"), ("bullets", items)]
    else:
        B += [("ok", "Критичных дефектов не найдено.")]

    # 6. Итог
    B += [("h1", "6. Итог"),
          ("verdict", f"Можно ли начинать разработку и тестирование: {v['decision']}", v["rule"]),
          ("p", v["conclusion"]), ("p", "**Что исправить в первую очередь:**"), ("numbers", v["fix_first"])]

    # Приложение
    B += [("h1", "Приложение. Методика"),
          ("bullets", [
              "Нарезчик выделяет требования; код проверяет, что цитата каждого требования есть в документе, и добавляет "
              "пронумерованные требования, если модель их пропустила.",
              "Ревьюер проверяет требования пачками по критериям ниже, в контексте всего документа. "
              "Два ревьюера набора отдельно ищут противоречия и пропуски.",
              "Каждое замечание должно цитировать документ. Код ищет цитату в тексте; если не нашёл — замечание "
              "считается неподтверждённым и не входит в сводку и итог.",
              "Решение «можно ли начинать» принимает код по правилу: есть критичные дефекты — «Нет»; "
              "только существенные — «С оговорками»; иначе — «Да».",
          ]),
          ("table", ["Критерий", "Источник", "Что проверяется"],
           [[c["name"], c["source"], c["question"]] for c in crit_req + crit_set], [0.2, 0.22, 0.58])]
    if res["unverified"]:
        B += [("p", "**Неподтверждённые замечания** (не учитываются):"),
              ("bullets", [f"{u['id']} · {names.get(u['criterion']) or set_names.get(u['criterion'], u['criterion'])}: "
                           f"{u['problem']} — {CHECK.get(u.get('check'), '')}" for u in res["unverified"]])]
    return B


# ---------------------------------------------------------------- Markdown
def _md_cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def to_markdown(blocks: list[tuple]) -> str:
    L: list[str] = []
    for b in blocks:
        kind = b[0]
        if kind == "title":
            L += [f"# {b[1]}", ""]
        elif kind == "h1":
            L += [f"## {b[1]}", ""]
        elif kind == "h3":
            L += [f"#### {b[1]}", ""]
        elif kind in ("p", "note"):
            L += [b[1], ""]
        elif kind == "ok":
            L += [f"✓ {b[1]}", ""]
        elif kind == "quote":
            L += [f"> {b[1]}", ""]
        elif kind == "bullets":
            L += [f"- {x}" for x in b[1]] + [""]
        elif kind == "numbers":
            L += [f"{n}. {x}" for n, x in enumerate(b[1], 1)] + [""]
        elif kind == "kv":
            L += ["| Поле | Значение |", "|---|---|"] + [f"| {_md_cell(k)} | {_md_cell(v)} |" for k, v in b[1]] + [""]
        elif kind in ("table", "matrix"):
            head, rows = b[1], b[2]
            L += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
            L += ["| " + " | ".join(_md_cell(c) for c in r) + " |" for r in rows] + [""]
        elif kind == "defect":
            L += [f"**{b[1]}**", ""] + [f"- {k}: {v}" for k, v in b[2]] + [""]
        elif kind == "verdict":
            L += [f"### {b[1]}", "", f"Правило: {b[2]}.", ""]
    return "\n".join(L).rstrip() + "\n"


def write_outputs(res: dict, out_path: Path) -> list[Path]:
    """Пишет отчёт в out_path (.pdf или .md) и рядом копию .md и сырые данные .json."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    blocks = build_blocks(res)
    md_path = out_path.with_suffix(".md")
    json_path = out_path.with_suffix(".json")
    md_path.write_text(to_markdown(blocks), encoding="utf-8")
    json_path.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    written = [md_path, json_path]
    if out_path.suffix.lower() == ".pdf":
        from .pdf import render_pdf
        render_pdf(blocks, out_path, footer=f"{res['passport']['title']} · {res['passport']['date']}")
        written.insert(0, out_path)
    return written
