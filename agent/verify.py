"""Проверка цитат кодом: модель не может сослаться на текст, которого нет в документе.

Каждая цитата из ответа модели ищется в исходном документе. Не нашлась — замечание
считается неподтверждённым, в итоги не входит и выносится в приложение отчёта.
"""
from __future__ import annotations

import re

_QUOTES = dict.fromkeys(map(ord, "«»“”„\"'’`*|#>"), " ")


def norm(s: str) -> str:
    s = (s or "").lower().replace("ё", "е").translate(_QUOTES)
    s = s.replace(" ", " ").replace(" ", " ").replace("—", "-").replace("–", "-").replace("→", "->")
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .,;:-")


def _words(s: str) -> list[str]:
    return re.findall(r"[а-яa-z0-9]+", norm(s))


def _bigrams(words: list[str]) -> set:
    return {(a, b) for a, b in zip(words, words[1:])}


def check_quote(quote: str, doc_norm: str, doc_bigrams: set) -> str:
    """exact — цитата есть в документе; fuzzy — почти дословно; not_found — нет; empty — цитаты нет."""
    parts = [norm(p) for p in re.split(r"\s*(?:…|\.\.\.)\s*", quote or "")]
    parts = [p for p in parts if len(p) >= 5]
    if not parts:
        return "empty"
    if all(p in doc_norm for p in parts):
        return "exact"
    words = _words(quote)
    grams = _bigrams(words)
    if len(grams) >= 2 and len(grams & doc_bigrams) / len(grams) >= 0.7:
        return "fuzzy"
    return "not_found"


class Document:
    """Текст документа и всё, что нужно для поиска цитат."""

    def __init__(self, text: str):
        self.text = text
        self.norm = norm(text)
        self.bigrams = _bigrams(_words(text))
        self.lines = [l.strip() for l in text.splitlines() if l.strip()]
        self._line_norms = [norm(l) for l in self.lines]

    def check(self, quote: str) -> str:
        return check_quote(quote, self.norm, self.bigrams)

    def position(self, quote: str) -> int:
        """Где цитата стоит в документе (для сортировки); -1 если не нашлась."""
        q = norm(quote)[:60]
        return self.norm.find(q) if q else -1

    def best_line(self, quote: str, min_overlap: float = 0.6) -> str:
        """Строка документа, больше всего похожая на цитату (если модель её чуть исказила)."""
        target = set(_words(quote))
        if not target:
            return ""
        best, best_score = "", 0.0
        for line, ln in zip(self.lines, self._line_norms):
            words = set(re.findall(r"[а-яa-z0-9]+", ln))
            if not words:
                continue
            score = len(target & words) / len(target)
            if score > best_score:
                best, best_score = line, score
        return clean_markdown(best) if best_score >= min_overlap else ""


def clean_markdown(s: str) -> str:
    s = re.sub(r"^\s*(?:[*\-•]|\d+\.)\s+", "", s)
    s = s.replace("**", "").replace("`", "").strip()
    s = re.sub(r"\s*\|\s*", " | ", s).strip(" |")
    return re.sub(r"\s+", " ", s)
