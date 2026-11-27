"""Агент проверки требований — запуск одной командой.

    python validate.py docs/requirements.md reports/report.pdf
    python validate.py docs/requirements.pdf reports/report.pdf --model gemini-3.5-flash
    python validate.py --list-models

Первый аргумент — файл требований (.pdf, .md, .docx, .txt), второй — куда записать отчёт (.pdf или .md).
Рядом с отчётом появятся копия в Markdown (.md) и все данные прогона (.json).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from agent.llm import LLMError, client_from_env
from agent.pipeline import run
from agent.report import write_outputs

ROOT = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description="Статическая проверка документа с требованиями (ISO/IEC/IEEE 29148, IEEE 830)")
    ap.add_argument("input", nargs="?", help="файл требований: .pdf, .md, .docx или .txt")
    ap.add_argument("output", nargs="?", default="reports/report.pdf", help="файл отчёта: .pdf или .md (по умолчанию reports/report.pdf)")
    ap.add_argument("--model", help="модель вместо указанной в .env")
    ap.add_argument("--list-models", action="store_true", help="показать модели, доступные по ключу")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")

    try:
        llm = client_from_env(args.model)
        if args.list_models:
            print(f"{llm.provider}: доступные модели")
            for m in llm.list_models():
                print("  ", m)
            return 0
        if not args.input:
            ap.error("укажи файл требований, например: python validate.py docs/requirements.md reports/report.pdf")
        src, out = Path(args.input), Path(args.output)
        if out.suffix.lower() not in (".pdf", ".md"):
            ap.error("отчёт можно записать в .pdf или .md")
        print(f"Модель: {llm.model} ({llm.provider})")
        res = run(src, llm, progress=print)
        files = write_outputs(res, out)
    except (LLMError, ValueError, OSError) as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        return 1

    s, v = res["summary"], res["verdict"]
    print(f"\nТребований: {s['requirements']} · нарушений: {s['violations']} "
          f"(критичных {s['by_severity']['critical']}, существенных {s['by_severity']['major']}, "
          f"незначительных {s['by_severity']['minor']})")
    print(f"Можно ли начинать разработку и тестирование: {v['decision']}")
    for e in res["errors"]:
        print("⚠️ ", e)
    print("\nОтчёт:\n" + "\n".join(f"  {f}" for f in files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
