from __future__ import annotations

import re


SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), "sk-***REDACTED***"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "AWS_KEY_***REDACTED***"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|token|secret|password|authorization)\b\s*[:=]\s*['\"]?[^'\"\s,;]+"
        ),
        r"\1=***REDACTED***",
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-.=]{16,}"), "Bearer ***REDACTED***"),
)


def redact_secrets(text: str) -> tuple[str, bool]:
    redacted = text
    changed = False
    for pattern, replacement in SECRET_PATTERNS:
        redacted, count = pattern.subn(replacement, redacted)
        changed = changed or count > 0
    return redacted, changed
