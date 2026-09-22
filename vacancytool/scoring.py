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
    "iOS": {"Senior": 100, "Lead": 96, "Middle": 92, "Junior": 88, "Не вказано": 86},
    "iOS + Android": {"Middle": 84, "Senior": 80, "Lead": 78, "Junior": 76, "Не вказано": 74},
    "KMP": {"Middle": 72, "Junior": 68, "Senior": 64, "Lead": 62, "Не вказано": 61},
    "Flutter": {"Middle": 60, "Junior": 56, "Senior": 52, "Lead": 50, "Не вказано": 49},
    "React Native": {"Middle": 48, "Junior": 44, "Senior": 40, "Lead": 38, "Не вказано": 37},
    "Python": {"Junior": 36, "Middle": 33, "Senior": 30, "Lead": 29, "Не вказано": 28},
    "Go": {"Junior": 27, "Middle": 24, "Senior": 21, "Lead": 20, "Не вказано": 19},
    "Java": {"Junior": 18, "Middle": 15, "Senior": 12, "Lead": 11, "Не вказано": 11},
    "JavaScript": {"Junior": 10, "Middle": 10, "Senior": 10, "Lead": 10, "Не вказано": 10},
    "C++": {"Junior": 6, "Middle": 6, "Senior": 6, "Lead": 6, "Не вказано": 6},
}

PLATFORM_ORDER = ("iOS", "Android", "KMP", "Flutter", "React Native", "Python", "Go", "Java", "JavaScript", "C++")

STACK_PLATFORMS = {
    "iOS": ("iOS",),
    "iOS + Android": ("iOS", "Android"),
    "KMP": ("KMP",),
    "Flutter": ("Flutter",),
    "React Native": ("React Native",),
    "Python": ("Python",),
    "Go": ("Go",),
    "Java": ("Java",),
    "JavaScript": ("JavaScript",),
    "C++": ("C++",),
}

PLATFORM_PATTERNS = {
    "iOS": r"\bios\b|\biphone\b|\bswift(?:ui)?\b|\buikit\b|objective[ -]?c",
    "Android": r"\bandroid\b|android sdk|jetpack compose|\bkotlin (?:developer|engineer)\b",
    "KMP": r"\bkmp\b|kotlin multiplatform|compose multiplatform",
    "Flutter": r"\bflutter\b|\bdart\b",
    "React Native": r"\breact[ -]?native\b",
    "Python": r"\bpython\b|\bdjango\b|\bflask\b|\bfastapi\b",
    "Go": r"\bgolang\b|\bgo (?:developer|engineer|backend|programming|language)\b",
    "Java": r"\bjava\b(?!script)",
    "JavaScript": r"\bjavascript\b|\btypescript\b|\bnode(?:\.js)?\b|\bfront[ -]?end\b",
    "C++": r"c\+\+|\bembedded\b",
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


def detect_platforms(title: str, description: str, stack: str) -> tuple[str, ...]:
    """Return primary technology tags used by the dashboard platform filter."""
    found = set(STACK_PLATFORMS.get(stack, ()))
    title_text = title or ""
    for platform, pattern in PLATFORM_PATTERNS.items():
        if has(title_text, pattern):
            found.add(platform)
    if not found:
        sample = (description or "")[:2500]
        for platform, pattern in PLATFORM_PATTERNS.items():
            if has(sample, pattern):
                found.add(platform)
    return tuple(platform for platform in PLATFORM_ORDER if platform in found)


def assess(title: str, description: str, work_format: str | None = None) -> Assessment:
    t = title or ""
    body = (description or "")[:30000]
    all_text = t + "\n" + body
    if has(t, r"\b(design(?:er)?|ux|ui|support|publisher|recruit|sales|marketing|manager|acquisition|buyer|growth|analytics|analyst|qa|aqa|sdet|tester|devops|sre|researcher|animator|producer|owner|aso)\b"):
        return Assessment(0, "—", "—", "Інша професія", "Назва вказує на роль поза розробкою")

    if has(t, r"\b(lead|tech lead|team lead|principal|staff)\b"):
        level = "Lead"
    elif has(t, r"\b(senior|sr\.?|старш[ийа])\b"):
        level = "Senior"
    elif has(t, r"\b(junior|jr\.?|trainee|intern)\b"):
        level = "Junior"
    elif has(t, r"\b(middle|mid[ -]?level|medior)\b"):
        level = "Middle"
    else:
        level = "Не вказано"

    engineering = has(t, r"\b(developer|engineer|programmer|розробник|програміст|software|back.?end|front.?end)\b") or has(t, r"\bmobile tech lead\b")
    if not engineering:
        return Assessment(0, level, "—", "Не визначено", "У назві немає ознак інженерної ролі")

    stack = None
    role = "Розробка"
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
        return Assessment(0, level, "—", "Не класифіковано", "Стек не входить до визначених пріоритетів")
    score = SCORES[stack][level]
    reason = f"{role}: {stack}, рівень {level}"
    if onsite_only(work_format):
        score = max(0, score - 5)
        reason += "; без Remote: −5"
    return Assessment(score, level, stack, role, reason)
