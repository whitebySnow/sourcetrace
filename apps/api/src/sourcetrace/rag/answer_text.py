import re

_ANSWER_UNIT_SPLIT = re.compile(
    r"(?<=[.!?;])\s+|(?<=[\u3002\uff01\uff1f\uff1b])\s*|\n+"
)


def split_answer_units(answer: str) -> list[str]:
    return [unit.strip() for unit in _ANSWER_UNIT_SPLIT.split(answer) if unit.strip()]
