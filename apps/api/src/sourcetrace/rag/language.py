import re
from enum import StrEnum

_HAN_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_CHARACTER = re.compile(r"[A-Za-z]")
_CITATION_LABEL = re.compile(
    r"\[[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\]"
)
_ANSWER_UNIT_SPLIT = re.compile(
    r"(?<=[.!?;])\s+|(?<=[\u3002\uff01\uff1f\uff1b])\s*|\n+"
)


class AnswerLanguage(StrEnum):
    CHINESE = "Chinese"
    ENGLISH = "English"


def detect_answer_language(question: str) -> AnswerLanguage | None:
    if _HAN_CHARACTER.search(question):
        return AnswerLanguage.CHINESE
    if _LATIN_CHARACTER.search(question):
        return AnswerLanguage.ENGLISH
    return None


def answer_language_instruction(question: str) -> str:
    language = detect_answer_language(question)
    if language is AnswerLanguage.CHINESE:
        return (
            "The question language is Chinese. Write every answer claim in Chinese, even when "
            "all evidence is in English. Preserve technical names and citation IDs verbatim. "
            "Translate only evidence-supported meaning and do not add explanatory details."
        )
    if language is AnswerLanguage.ENGLISH:
        return (
            "The question language is English. Write every answer claim in English. Preserve "
            "technical names and citation IDs verbatim. Translate only evidence-supported "
            "meaning and do not add explanatory details."
        )
    return (
        "Use the same language as the question. Preserve technical names and citation IDs "
        "verbatim. Do not add explanatory details while translating evidence-supported meaning."
    )


def answer_matches_question_language(*, question: str, answer: str) -> bool:
    language = detect_answer_language(question)
    if language is None:
        return True
    claim_text = _CITATION_LABEL.sub("", answer)
    units = [unit.strip() for unit in _ANSWER_UNIT_SPLIT.split(claim_text) if unit.strip()]
    if not units:
        return False
    if language is AnswerLanguage.CHINESE:
        return all(_HAN_CHARACTER.search(unit) is not None for unit in units)
    return all(
        _LATIN_CHARACTER.search(unit) is not None and _HAN_CHARACTER.search(unit) is None
        for unit in units
    )
