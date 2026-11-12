"""Поиск свежих проектов на GitHub и фильтр безопасности."""
import base64
import datetime as dt
import os
import re
import time

import requests

import config

API = "https://api.github.com"
SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/vnd.github+json",
    "User-Agent": "github-finds-bot",
})
if os.getenv("GITHUB_TOKEN"):
    SESSION.headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"


def _get(path, params=None, accept=None):
    headers = {"Accept": accept} if accept else None
    for attempt in range(3):
        r = SESSION.get(f"{API}{path}", params=params, headers=headers, timeout=30)
        if r.status_code in (403, 429) and "rate limit" in r.text.lower():
            time.sleep(20 * (attempt + 1))
            continue
        return r
    return r


def _utcnow():
    return dt.datetime.now(dt.timezone.utc)


def _parse_time(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def search_candidates(exclude):
    """Собирает кандидатов по AI-темам + общий тренд. exclude — уже опубликованные/в очереди."""
    since = (_utcnow() - dt.timedelta(days=config.SEARCH_LOOKBACK_DAYS)).date().isoformat()
    queries = [f"created:>{since} stars:>{config.MIN_STARS} topic:{t}" for t in config.SEARCH_TOPICS]
    queries.append(f"created:>{since} stars:>{config.GENERAL_MIN_STARS}")

    found = {}
    for q in queries:
        r = _get("/search/repositories", {"q": q, "sort": "stars", "order": "desc", "per_page": 30})
        if r.status_code != 200:
            print(f"[search] {q!r} -> {r.status_code}")
            continue
        for item in r.json().get("items", []):
            name = item["full_name"]
            if name.lower() in exclude or name in found:
                continue
            found[name] = item
        time.sleep(2.5)  # лимит Search API — 30 запросов в минуту

    now = _utcnow()
    for item in found.values():
        age_days = max((now - _parse_time(item["created_at"])).days, 1)
        item["_velocity"] = item["stargazers_count"] / age_days  # звёзд в день
    return sorted(found.values(), key=lambda i: i["_velocity"], reverse=True)


def _has_banned(text):
    low = f" {text.lower()} "
    return next((w for w in config.BANNED_WORDS if w in low), None)


def fetch_readme(full_name):
    r = _get(f"/repos/{full_name}/readme")
    if r.status_code != 200:
        return ""
    data = r.json()
    try:
        return base64.b64decode(data.get("content", "")).decode("utf-8", "ignore")
    except Exception:
        return ""


def safety_check(item):
    """Возвращает (ok, причина, доп. данные). Отсекает фейки и сомнительные проекты."""
    name = item["full_name"]
    if item.get("fork") or item.get("archived") or item.get("disabled"):
        return False, "форк/архив", {}
    if not item.get("description"):
        return False, "нет описания", {}
    if not item.get("license"):
        return False, "нет лицензии", {}
    text = " ".join([name, item.get("description") or "", " ".join(item.get("topics") or [])])
    word = _has_banned(text)
    if word:
        return False, f"стоп-слово '{word.strip()}'", {}

    pushed = _parse_time(item["pushed_at"])
    if (_utcnow() - pushed).days > config.MAX_DAYS_SINCE_PUSH:
        return False, "давно не обновлялся", {}

    owner = item["owner"]["login"]
    owner_since = ""
    r = _get(f"/users/{owner}")
    if r.status_code == 200:
        created = _parse_time(r.json()["created_at"])
        owner_since = str(created.year)
        owner_age = (_utcnow() - created).days
        if owner_age < config.MIN_OWNER_AGE_DAYS:
            return False, f"аккаунту автора {owner_age} дн.", {}

    r = _get(f"/repos/{name}/languages")
    code_bytes = sum(r.json().values()) if r.status_code == 200 else 0
    if code_bytes < config.MIN_CODE_BYTES:
        return False, "почти нет кода", {}

    readme = fetch_readme(name)
    if len(readme) < config.MIN_README_CHARS:
        return False, "пустой README", {}
    low = readme.lower()
    host = next((h for h in config.SUSPICIOUS_LINK_HOSTS if h in low), None)
    if host:
        return False, f"подозрительная ссылка ({host})", {}
    if re.search(r"(archive|zip|rar)[^\n]{0,60}(password|пароль)", low) or \
       re.search(r"(password|пароль)[^\n]{0,60}(archive|zip|rar)", low):
        return False, "архив с паролем", {}
    word = _has_banned(readme[:4000])
    if word:
        return False, f"стоп-слово в README '{word.strip()}'", {}

    return True, "ok", {"readme": readme, "code_bytes": code_bytes, "owner_since": owner_since}


def contributors_count(full_name):
    r = _get(f"/repos/{full_name}/contributors", {"per_page": 1, "anon": "true"})
    if r.status_code != 200:
        return None
    link = r.headers.get("Link", "")
    m = re.search(r'[?&]page=(\d+)>; rel="last"', link)
    return int(m.group(1)) if m else len(r.json())
