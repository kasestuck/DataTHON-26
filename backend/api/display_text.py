"""Conservative Russian copy editing after inference; never alter a Finding."""
import json
import logging
from pathlib import Path
import re
import unicodedata


ANALYSIS_ERROR = "Не удалось завершить анализ видеозаписи. Повторите попытку позже."

# A few unequivocal spelling corrections and whole noun phrases only.
# No global money/role/negation or action replacements.
# The surrounding sentence, including uncertainty and negation, is preserved.
_PHRASES = {
    "наблюдаеться": "наблюдается",
    "находяться": "находятся",
    "передача наличности": "передача наличных денежных средств",
    "передаче наличности": "передаче наличных денежных средств",
    "передачи наличности": "передачи наличных денежных средств",
    "разделение наличности": "разделение наличных денежных средств",
    "разделения наличности": "разделения наличных денежных средств",
    "выдача наличности": "выдача наличных денежных средств",
    "выдаче наличности": "выдаче наличных денежных средств",
    "выдачи наличности": "выдачи наличных денежных средств",
    "с наличностью": "с наличными денежными средствами",
}
_PHRASE_PATTERN = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(key) for key in _PHRASES) + r")(?!\w)", re.IGNORECASE)


def normalize_observation(text: str) -> str:
    """Normalize typography and a small set of unambiguous inflected phrases.

    Never infer roles, unattended objects, absence of a check or legal meaning.
    Empty text stays empty; the existing Finding validator rejects it.
    """
    if not isinstance(text, str):
        raise TypeError("Observation must be text")
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()
    # Limit punctuation cleanup to letters, preserving numbers, URLs and IDs.
    text = re.sub(r"(?<=[А-Яа-яЁё][А-Яа-яЁё]) +([,.;:!?])", r"\1", text)
    text = re.sub(r"(?<=[А-Яа-яЁё]),(?=[А-Яа-яЁё])", ", ", text)

    def replacement(match):
        original = match.group()
        # Mixed case may be an identifier; leave it alone.
        if not (original.islower() or original.isupper() or original[0].isupper() and original[1:].islower()):
            return original
        value = _PHRASES[original.lower()]
        if original.isupper():
            return value.upper()
        return value[0].upper() + value[1:] if original[0].isupper() else value

    return _PHRASE_PATTERN.sub(replacement, text)


def save_description_diagnostics(path: Path, observations: list[dict]) -> None:
    """Private DATA_DIR only, never include originals in browser JSON or logs."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        # A diagnostics storage problem must not change an analysis verdict.
        logging.getLogger(__name__).warning("Could not save private description diagnostics")
