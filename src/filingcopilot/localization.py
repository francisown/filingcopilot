"""Presentation language is scoped to one request, never to source evidence."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps
from importlib.resources import files

_language = ContextVar("filingcopilot_output_language", default="zh")


def current_language() -> str:
    return _language.get()


def header_language(value: str | None) -> str:
    choices = []
    for index, item in enumerate((value or "").split(",")):
        name, _, parameters = item.strip().partition(";")
        language = name.lower().split("-")[0]
        quality = 1.0
        if parameters:
            try:
                quality = float(parameters.strip().removeprefix("q="))
            except ValueError:
                continue
        if language in {"zh", "en"} and 0 < quality <= 1:
            choices.append((quality, -index, language))
    return max(choices)[2] if choices else "en"


def request_from_header(request, value: str | None):
    if value and "language" not in request.model_fields_set:
        return request.model_copy(update={"language": header_language(value)})
    return request


@contextmanager
def language_scope(language: str):
    if language not in {"zh", "en"}:
        raise ValueError("Supported output languages: zh, en")
    token = _language.set(language)
    try:
        yield
    finally:
        _language.reset(token)


def request_language(function):
    @wraps(function)
    def wrapped(self, request, *args, **kwargs):
        with language_scope(request.language):
            return function(self, request, *args, **kwargs)
    return wrapped


def state_language(function):
    @wraps(function)
    def wrapped(self, state, *args, **kwargs):
        with language_scope(state.get("output_language", current_language())):
            return function(self, state, *args, **kwargs)
    return wrapped


@lru_cache(maxsize=1)
def english_catalog() -> dict[str, str]:
    return json.loads(files("filingcopilot").joinpath("locale-en.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _fragments():
    keys = sorted((key for key in english_catalog() if len(key) >= 2), key=len, reverse=True)
    return re.compile("|".join(re.escape(key) for key in keys)) if keys else None


def text(value: str, language: str | None = None) -> str:
    """Translate known UI messages only; callers must not pass original source text."""
    if (language or current_language()) != "en":
        return value
    catalog = english_catalog()
    if value in catalog:
        return catalog[value]
    pattern = _fragments()
    def fragment(match):
        result = catalog[match[0]]
        if match.start() and re.match(r"[A-Za-z0-9]", result) and re.match(r"[A-Za-z0-9\u4e00-\u9fff]", value[match.start()-1]):
            result = " " + result
        if match.end() < len(value) and re.search(r"[A-Za-z0-9]$", result) and re.match(r"[A-Za-z0-9\u4e00-\u9fff]", value[match.end()]):
            result += " "
        return result
    translated = pattern.sub(fragment, value) if pattern else value
    for before, after in ((" 至 ", " to "), ("/股", "/share"), (" 倍", "x"),
                          ("（", "("), ("）", ")"), ("。", ". "), ("，", ", "),
                          ("：", ": "), ("；", "; "), ("、", ", ")):
        translated = translated.replace(before, after)
    return re.sub(r" {2,}", " ", re.sub(r"(?<=[A-Za-z]):(?=[A-Za-z])", ": ", translated)).strip()


def localized_prompt(prompt: str, language: str) -> str:
    if language == "zh":
        return prompt
    prompt = prompt.replace("Answer in Chinese", "Answer in English")
    prompt = prompt.replace("Write Chinese evidence-grounded", "Write English evidence-grounded")
    prompt = prompt.replace("in Chinese", "in English").replace("Chinese narratives", "English narratives")
    prompt = prompt.replace("Chinese narrative", "English narrative")
    prompt = prompt.replace("100-220 Chinese characters", "60-120 English words")
    prompt = prompt.replace("140 Chinese characters", "90 English words")
    prompt = prompt.replace("120 Chinese characters", "80 English words")
    prompt = prompt.replace("100 Chinese characters", "80 English words")
    prompt = prompt.replace("中文分析", "English analysis")
    return prompt + (
        "\nOUTPUT LANGUAGE: English. Write all new analysis, narrative claims and gaps in English. "
        "Preserve original source quotations, evidence IDs, ticker symbols, currencies and periods. "
        "Do not translate source evidence or change any verification or numeric rules."
    )


def prepare_answer(answer, language: str):
    answer.language = language
    answer.gaps = [text(gap, language) for gap in answer.gaps]
    if answer.contrarian.get("note"):
        answer.contrarian = {**answer.contrarian, "note": text(answer.contrarian["note"], language)}
    return answer
