"""Проверки без интернета и без ключа:  python tests/test_offline.py"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fake_llm import FakeLLM  # noqa: E402

from agent.pipeline import run  # noqa: E402
from agent.report import write_outputs  # noqa: E402
from agent.segmenter import segment  # noqa: E402
from agent.verify import Document  # noqa: E402

DOC = ROOT / "docs" / "requirements.md"


def test_quotes():
    doc = Document(DOC.read_text(encoding="utf-8"))
    assert doc.check("REST type | GET") == "exact"
    assert doc.check("максимум один такой push за календарный месяц … независимо от истории") == "exact"
    assert doc.check("Держатель активной карты задаёт целый порог от 1 000 до 1 000 000 ₸") == "fuzzy"
    assert doc.check("Система отправляет SMS каждый час") == "not_found"


def test_explicit_ids_are_never_lost():
    class NoF2(FakeLLM):
        def answer(self, step, user):
            data = super().answer(step, user)
            if step == "segment":
                data["requirements"] = [r for r in data["requirements"] if r["doc_id"] != "Ф-2"]
            return data
    seg = segment(Document(DOC.read_text(encoding="utf-8")), NoF2())
    ids = [r["doc_id"] for r in seg["requirements"] if r["doc_id"]]
    assert ids == ["Ф-1", "Ф-2", "Ф-3", "Ф-4"], ids
    assert any("Ф-2" in n for n in seg["notes"])


def test_full_run_and_report():
    res = run(DOC, FakeLLM(), max_parallel=2, batch_size=6)
    s = res["summary"]
    assert s["requirements"] == 22, s["requirements"]
    assert [r["id"] for r in res["requirements"]][:2] == ["Т-01", "Т-02"]
    assert any("Отброшено" in n for n in res["segment_notes"])          # выдуманное требование
    assert len(res["unverified"]) == 2                                   # выдуманные цитаты
    assert res["requirements"][21]["criteria"]["correct"] == "unconfirmed"
    assert res["verdict"]["decision"] == "Нет" and s["by_severity"]["critical"] >= 5
    assert not res["errors"]
    with tempfile.TemporaryDirectory() as tmp:
        files = write_outputs(res, Path(tmp) / "report.pdf")
        assert [f.suffix for f in files] == [".pdf", ".md", ".json"]
        md = files[1].read_text(encoding="utf-8")
        for h in ["1. Паспорт прогона", "2. Как агент нарезал требования", "3. Проверка каждого требования",
                  "4. Проверка всего набора требований", "5. Сводка", "6. Итог"]:
            assert f"## {h}" in md, h
        from pypdf import PdfReader
        text = "".join(p.extract_text() for p in PdfReader(str(files[0])).pages)
        assert "Паспорт прогона" in text and "Ф-3" in text


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for n, f in tests:
        f()
        print(f"✓ {n}")
    print(f"\nВсе проверки пройдены: {len(tests)}")
