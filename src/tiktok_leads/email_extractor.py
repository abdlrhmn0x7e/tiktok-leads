from __future__ import annotations

import re

EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


def normalize_email(email: str) -> str:
    return email.strip().strip(".,;:)(").lower()


def extract_emails(*chunks: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        for match in EMAIL_RE.findall(chunk or ""):
            email = normalize_email(match)
            if email not in seen:
                seen.add(email)
                found.append(email)
    return found
