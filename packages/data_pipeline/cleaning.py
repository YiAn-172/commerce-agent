from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class CleanResult:
    text: str
    pii_status: str
    redactions: tuple[str, ...]


_PATTERNS = (
    ("email", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"), "[邮箱]"),
    ("mobile", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号]"),
    ("cn_id", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[身份证]"),
    ("order_id", re.compile(r"(?i)\b(?:ORD|ORDER)[-_ ]?\d{4,}\b"), "[订单号]"),
    ("user_id", re.compile(r"(?i)\b(?:USR|USER)[-_ ]?\d{3,}\b"), "[用户标识]"),
    (
        "address",
        re.compile(
            r"(?:(?:收货|配送|家庭|公司)?地址)\s*[:：]\s*"
            r"[\u4e00-\u9fffA-Za-z0-9#号栋单元室路街巷区县市省\- ]{6,80}"
        ),
        "地址:[地址]",
    ),
)
_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def clean_text(value: str) -> CleanResult:
    text = unicodedata.normalize("NFKC", html.unescape(value))
    text = _HTML_TAG.sub(" ", text)
    text = "".join(character for character in text if character.isprintable())
    redactions: list[str] = []
    for name, pattern, replacement in _PATTERNS:
        text, count = pattern.subn(replacement, text)
        if count:
            redactions.extend([name] * count)
    text = _WHITESPACE.sub(" ", text).strip()
    status = "redacted" if redactions else "clear"
    return CleanResult(text=text, pii_status=status, redactions=tuple(redactions))


def normalized_text_hash(text: str) -> str:
    normalized = re.sub(r"[\W_]+", "", text.casefold(), flags=re.UNICODE)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def detect_pii(text: str) -> tuple[str, ...]:
    return tuple(name for name, pattern, _ in _PATTERNS if pattern.search(text))
