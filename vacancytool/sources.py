from __future__ import annotations

import json
import html as html_lib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "VacancyTool/1.0 (personal RSS reader; local use)"
SOURCE_HOSTS = {"djinni": "djinni.co", "dou": "jobs.dou.ua"}
TRACKING_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "source"}


@dataclass
class FeedItem:
    source: str
    source_key: str
    original_url: str
    canonical_url: str
    title: str
    company: str | None
    summary_html: str
    published_at: str | None


def client() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/html;q=0.9"})
    retry = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def allowed_url(source: str, url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    expected = SOURCE_HOSTS[source]
    return parsed.scheme == "https" and (host == expected or host.endswith("." + expected))


def canonicalize(url: str) -> str:
    parts = urlsplit(url)
    params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
              if k.lower() not in TRACKING_KEYS and not k.isdecimal()]
    path = re.sub(r"/+", "/", parts.path).rstrip("/") + "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(sorted(params)), ""))


def source_key(source: str, url: str, guid: str | None) -> str:
    path = urlsplit(url).path
    if source == "dou":
        match = re.search(r"/vacancies/(\d+)/?", path)
    else:
        match = re.search(r"/jobs/(\d+)-", path)
    if match:
        return match.group(1)
    return canonicalize(guid or url)


def dou_company(title: str) -> str | None:
    match = re.search(r"\sв\s(.+)$", title, re.I)
    if not match:
        return None
    value = match.group(1)
    value = re.split(r",\s*(?=(?:віддалено|remote|Київ|Львів|Дніпро|Вінниця|Івано|Тернопіль|Варшава|Краків|Україна|Ukraine|за кордоном|[$€£₴]|від\s*[$€£₴]))", value, maxsplit=1, flags=re.I)[0]
    return value.strip() or None


def parse_feed(source: str, data: bytes) -> list[FeedItem]:
    feed = feedparser.parse(data)
    if feed.bozo and not feed.entries:
        raise ValueError(f"RSS не разобран: {feed.bozo_exception}")
    if not feed.entries and not feed.feed:
        raise ValueError("Ответ не содержит RSS")
    items = []
    for entry in feed.entries:
        url = entry.get("link", "")
        if not allowed_url(source, url):
            continue
        title = html_lib.unescape(entry.get("title", "")).strip()
        if not title:
            continue
        company = None
        if source == "dou":
            company = dou_company(title)
        published = entry.get("published_parsed")
        published_at = datetime(*published[:6], tzinfo=timezone.utc).isoformat() if published else None
        items.append(FeedItem(source, source_key(source, url, entry.get("id")), url, canonicalize(url),
                              title, company, entry.get("summary", ""), published_at))
    return items


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "form", "button"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def without_salary(text: str) -> str:
    """Do not persist compensation lines from feed or detail text."""
    salary = re.compile(r"\b(salary|compensation|pay range|зарплат\w*|компенсац\w*|оплат\w*|винагород\w*|ставка)\b|[$€£₴]|\d[\d\s,.]*\s*(?:USD|EUR|UAH)\b|\b(?:USD|EUR|UAH)\s*\d", re.I)
    return "\n".join(line for line in text.splitlines() if not salary.search(line)).strip()


def without_salary_title(title: str) -> str:
    cleaned = re.sub(r"(?:\b(?:до|від|from|up to)\s*)?[$€£₴]\s*\d+(?:[.,]\d+)?(?:[-–]\s*[$€£₴]?\s*\d+(?:[.,]\d+)?)?", "", title, flags=re.I)
    cleaned = re.sub(r"\b(?:до|від|from|up to)?\s*\d+(?:[.,]\d+)?\s*(?:USD|EUR|UAH)\b", "", cleaned, flags=re.I)
    return re.sub(r",\s*,", ",", cleaned).strip(" ,–-")


def workplace_from_text(value: str) -> tuple[str | None, str | None]:
    """Read a compact location label, not arbitrary mentions in a job description."""
    value = re.sub(r"\s+", " ", value).strip(" ,")
    if not value:
        return None, None
    work_format = None
    if re.search(r"\bhybrid\b|гібрид|гибрид", value, re.I):
        work_format = "Hybrid"
    elif re.search(r"\bremote\b|віддален|удален|дистанційн|дистанцион|тільки віддалено", value, re.I):
        work_format = "Remote"
    elif re.search(r"\bon[ -]?site\b|\boffice\b|\bофіс", value, re.I):
        work_format = "Office"
    location = re.sub(r"\b(?:remote|hybrid|on[ -]?site|office)\b|віддалено|віддалена|удаленно|гібрид(?:ний)?|гибрид(?:ный)?|офіс(?:ний)?", "", value, flags=re.I)
    location = re.sub(r"\s*,\s*", ", ", location).strip(" ,–- ")
    return work_format, location or None


def extract_detail(source: str, html: str) -> tuple[str | None, str | None, str | None, str | None]:
    soup = BeautifulSoup(html, "html.parser")
    company = None
    description = None
    work_format = location = None
    if source == "dou":
        place = soup.select_one(".l-vacancy .sh-info .place")
        if place:
            work_format, location = workplace_from_text(place.get_text(" ", strip=True))
    elif source == "djinni":
        place = soup.select_one(".location-text")
        if place:
            location = place.get_text(" ", strip=True) or None
        if soup.find(string=re.compile(r"Тільки віддалено", re.I)):
            work_format = "Remote"
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
            nodes = data if isinstance(data, list) else [data]
            for node in nodes:
                if isinstance(node, dict) and "JobPosting" in str(node.get("@type", "")):
                    org = node.get("hiringOrganization") or {}
                    company = org.get("name") if isinstance(org, dict) else None
                    candidate = node.get("description")
                    if candidate and len(html_to_text(candidate)) > 100:
                        description = candidate
                    if node.get("jobLocationType") == "TELECOMMUTE":
                        work_format = "Remote"
                    requirements = node.get("applicantLocationRequirements") or node.get("jobLocation") or {}
                    if isinstance(requirements, list):
                        requirements = requirements[0] if requirements else {}
                    if isinstance(requirements, dict):
                        address = requirements.get("address", requirements)
                        if isinstance(address, dict):
                            location = location or address.get("addressRegion") or address.get("addressLocality") or address.get("addressCountry")
        except (ValueError, TypeError):
            pass
    if description:
        return description, company, work_format, location
    if source == "dou":
        selectors = [".vacancy-section", ".b-vacancy__description", "[itemprop='description']", "article"]
    else:
        selectors = [".job-description", ".profile-page-section", "[itemprop='description']", "article"]
    for selector in selectors:
        matches = soup.select(selector)
        if matches:
            best = max(matches, key=lambda tag: len(tag.get_text(" ", strip=True)))
            if len(best.get_text(" ", strip=True)) > 100:
                if source == "djinni":
                    link = soup.select_one('a[href*="/jobs/company-"]')
                    company = link.get_text(" ", strip=True) if link else None
                return str(best), company, work_format, location
    return None, company, work_format, location


def fetch_feed(session: requests.Session, url: str, etag: str | None = None, modified: str | None = None):
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified
    response = session.get(url, headers=headers, timeout=(5, 25))
    response.raise_for_status()
    if len(response.content) > 5_000_000:
        raise ValueError("RSS больше 5 МБ")
    return response


def fetch_detail(session: requests.Session, source: str, url: str) -> tuple[str | None, str | None, str | None, str | None]:
    if not allowed_url(source, url):
        raise ValueError("Недопустимый URL вакансии")
    response = session.get(url, timeout=(5, 20))
    response.raise_for_status()
    if not allowed_url(source, response.url):
        raise ValueError("Страница перенаправила на другой сайт")
    if len(response.content) > 3_000_000:
        raise ValueError("Страница вакансии больше 3 МБ")
    return extract_detail(source, response.text)
