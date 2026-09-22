from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Assessment:
    score: int
    level: str
    stack: str
    role: str
    reason: str


SCORES = {
    "iOS": {"Senior": 100, "Lead": 96, "Middle": 92, "Junior": 88, "Не указан": 86},
    "iOS + Android": {"Middle": 84, "Senior": 80, "Lead": 78, "Junior": 76, "Не указан": 74},
    "KMP": {"Middle": 72, "Junior": 68, "Senior": 64, "Lead": 62, "Не указан": 61},
    "Flutter": {"Middle": 60, "Junior": 56, "Senior": 52, "Lead": 50, "Не указан": 49},
    "React Native": {"Middle": 48, "Junior": 44, "Senior": 40, "Lead": 38, "Не указан": 37},
    "Python": {"Junior": 36, "Middle": 33, "Senior": 30, "Lead": 29, "Не указан": 28},
    "Go": {"Junior": 27, "Middle": 24, "Senior": 21, "Lead": 20, "Не указан": 19},
    "Java": {"Junior": 18, "Middle": 15, "Senior": 12, "Lead": 11, "Не указан": 11},
    "JavaScript": {"Junior": 10, "Middle": 10, "Senior": 10, "Lead": 10, "Не указан": 10},
    "C++": {"Junior": 6, "Middle": 6, "Senior": 6, "Lead": 6, "Не указан": 6},
}


def has(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text, re.I))


def onsite_only(work_format: str | None) -> bool:
    """Return True when the format requires office presence and offers no remote option."""
    if not work_format:
        return False
    value = work_format.casefold()
    if has(value, r"\bremote\b|віддал|удален"):
        return False
    return has(value, r"\bhybrid\b|\bonsite\b|\bon[ -]site\b|\boffice\b|офіс|офис")


def assess(title: str, description: str, work_format: str | None = None) -> Assessment:
    t = title or ""
    body = (description or "")[:30000]
    all_text = t + "\n" + body
    if has(t, r"\b(design(?:er)?|ux|ui|support|publisher|recruit|sales|marketing|manager|acquisition|buyer|growth|analytics|analyst|qa|aqa|sdet|tester|devops|sre|researcher|animator|producer|owner|aso)\b"):
        return Assessment(0, "—", "—", "Другая профессия", "Название указывает на роль вне разработки")

    if has(t, r"\b(lead|tech lead|team lead|principal|staff)\b"):
        level = "Lead"
    elif has(t, r"\b(senior|sr\.?|старш[ийа])\b"):
        level = "Senior"
    elif has(t, r"\b(junior|jr\.?|trainee|intern)\b"):
        level = "Junior"
    elif has(t, r"\b(middle|mid[ -]?level|medior)\b"):
        level = "Middle"
    else:
        level = "Не указан"

    engineering = has(t, r"\b(developer|engineer|programmer|розробник|програміст|software|back.?end|front.?end)\b") or has(t, r"\bmobile tech lead\b")
    if not engineering:
        return Assessment(0, level, "—", "Не определена", "Нет признаков инженерной роли в названии")

    stack = None
    role = "Разработка"
    if has(t, r"\b(kmp|kotlin multiplatform|compose multiplatform)\b"):
        stack = "KMP"
        role = "Cross-platform mobile"
    elif has(t, r"\bflutter\b"):
        stack = "Flutter"
        role = "Cross-platform mobile"
    elif has(t, r"\breact[ -]?native\b"):
        stack = "React Native"
        role = "Cross-platform mobile"
    elif has(t, r"\bios\b|iphone|swift|objective[ -]?c") and has(t, r"\bandroid\b"):
        stack = "iOS + Android"
        role = "Native mobile"
    elif has(t, r"\bios\b|iphone|swift|objective[ -]?c"):
        stack = "iOS"
        role = "Native iOS"
    elif has(t, r"\bmobile\b") and has(body, r"\b(kmp|kotlin multiplatform|compose multiplatform)\b"):
        stack = "KMP"
        role = "Cross-platform mobile"
    elif has(t, r"\bmobile\b") and has(body, r"\bflutter\b"):
        stack = "Flutter"
        role = "Cross-platform mobile"
    elif has(t, r"\bmobile\b") and has(body, r"\breact[ -]?native\b"):
        stack = "React Native"
        role = "Cross-platform mobile"
    elif has(t, r"\b(mobile|sdk)\b") and has(body[:2500], r"\bios\b") and has(body[:2500], r"\bandroid\b"):
        stack = "iOS + Android"
        role = "Native mobile"
    elif has(t, r"\bmobile\b") and has(body, r"\b(swift|swiftui|uikit|objective[ -]?c|ios development)\b"):
        stack = "iOS"
        role = "Native iOS"
    elif has(t, r"\b(back.?end|python|golang|go|java)\b") or has(body, r"\bbackend\b"):
        role = "Backend"
        if has(t, r"\bpython\b") or has(body, r"\bpython\b"):
            stack = "Python"
        elif has(t, r"\b(golang|go)\b") or has(body, r"\b(golang|go programming)\b"):
            stack = "Go"
        elif has(t, r"\bjava\b") or has(body, r"\bjava\b"):
            stack = "Java"
    if stack is None and (has(t, r"\b(front.?end|javascript|typescript|react|vue|angular)\b") or
                          has(body[:2500], r"\b(javascript|typescript|react\.js|vue\.js|angular)\b")):
        stack = "JavaScript"
        role = "Other software"
    if stack is None and (has(t, r"c\+\+|embedded") or has(body[:2500], r"c\+\+")):
        stack = "C++"
        role = "Other software"
    if stack is None:
        return Assessment(0, level, "—", "Не классифицирована", "Стек не попал в заданные приоритеты")
    score = SCORES[stack][level]
    reason = f"{role}: {stack}, уровень {level}"
    if onsite_only(work_format):
        score = max(0, score - 5)
        reason += "; без Remote: −5"
    return Assessment(score, level, stack, role, reason)
