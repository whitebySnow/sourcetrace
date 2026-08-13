from sourcetrace.rag.language import (
    answer_language_instruction,
    answer_matches_question_language,
)


def test_chinese_question_with_english_technical_terms_requires_chinese_claims() -> None:
    question = "BGE-M3 的向量在写入 pgvector 前如何处理?"

    assert "question language is Chinese" in answer_language_instruction(question)
    assert answer_matches_question_language(
        question=question,
        answer="BGE-M3 的向量会先归一化。 [00000000-0000-0000-0000-000000000001]",
    )
    assert not answer_matches_question_language(
        question=question,
        answer="BGE-M3 vectors are normalized before indexing.",
    )


def test_english_question_rejects_a_chinese_claim() -> None:
    question = "How are BGE-M3 vectors stored?"

    assert "question language is English" in answer_language_instruction(question)
    assert answer_matches_question_language(
        question=question,
        answer="BGE-M3 vectors are normalized before indexing.",
    )
    assert not answer_matches_question_language(
        question=question,
        answer="BGE-M3 的向量在写入索引前会被归一化。",
    )


def test_symbol_only_question_does_not_guess_a_language() -> None:
    assert answer_matches_question_language(question="???", answer="任意回答")
