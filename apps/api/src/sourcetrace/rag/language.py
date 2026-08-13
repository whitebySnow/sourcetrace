import re
from enum import StrEnum

from sourcetrace.rag.answer_text import split_answer_units

_HAN_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*")
_CITATION_LABEL = re.compile(
    r"\[[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\]"
)
class AnswerLanguage(StrEnum):
    CHINESE = "Chinese"
    ENGLISH = "English"


def detect_question_language(question: str) -> AnswerLanguage | None:
    han_count = len(_HAN_CHARACTER.findall(question))
    latin_word_count = len(_LATIN_WORD.findall(question))
    if han_count and han_count > latin_word_count:
        return AnswerLanguage.CHINESE
    if latin_word_count:
        return AnswerLanguage.ENGLISH
    if han_count:
        return AnswerLanguage.CHINESE
    return None


def answer_language_instruction(question: str) -> str:
    language = detect_question_language(question)
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
    language = detect_question_language(question)
    if language is None:
        return True
    claim_text = _CITATION_LABEL.sub("", answer)
    units = split_answer_units(claim_text)
    if not units:
        return False
    if language is AnswerLanguage.CHINESE:
        return all(_HAN_CHARACTER.search(unit) is not None for unit in units)
    return all(detect_question_language(unit) is AnswerLanguage.ENGLISH for unit in units)
