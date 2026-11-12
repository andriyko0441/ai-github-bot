"""Тексты постов через бесплатный Gemini API."""
import json
import os
import re
import time

import requests

import config

BASE = "https://generativelanguage.googleapis.com/v1beta"
ENDPOINT = BASE + "/models/{model}:generateContent"
_SKIP_WORDS = ("image", "tts", "audio", "live", "embedding", "native", "computer", "robotics", "vision", "exp", "omni")
_MODELS_CACHE = None


class WriterError(Exception):
    pass


_UNREACHABLE = False  # если Gemini не ответил ни одной моделью — в этом запуске больше не ждём


def _available_models(key):
    """Спрашивает у Google, какие модели доступны этому ключу, и выбирает текстовые Flash-модели.
    Так бот не ломается, когда Google переименовывает или отключает модели."""
    global _MODELS_CACHE
    if _MODELS_CACHE is not None:
        return _MODELS_CACHE
    found = []
    try:
        r = requests.get(f"{BASE}/models", headers={"x-goog-api-key": key},
                         params={"pageSize": 1000}, timeout=30)
        if r.status_code == 200:
            for m in r.json().get("models", []):
                name = m.get("name", "").removeprefix("models/")
                methods = m.get("supportedGenerationMethods") or []
                if (name.startswith("gemini") and "flash" in name and "generateContent" in methods
                        and not any(w in name for w in _SKIP_WORDS)):
                    found.append(name)
        else:
            print(f"[gemini] список моделей: {r.status_code} {r.text[:200]}")
    except requests.RequestException as e:
        print(f"[gemini] список моделей: {type(e).__name__}")

    def rank(name):
        ver = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
        # сначала быстрые Lite-модели: у них щедрее бесплатный лимит, и для коротких постов их хватает
        return ("preview" in name, "lite" not in name, "latest" not in name and "-0" in name,
                -float(ver.group(1)) if ver else 0)

    found.sort(key=rank)
    ordered = []
    for name in config.GEMINI_MODELS[:1] + found + config.GEMINI_MODELS[1:]:
        if name and name not in ordered:
            ordered.append(name)
    print(f"[gemini] модели по порядку: {', '.join(ordered[:8])}")
    _MODELS_CACHE = ordered
    return ordered


def _ask(prompt, temperature=0.7):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise WriterError("нет GEMINI_API_KEY")
    global _UNREACHABLE
    if _UNREACHABLE:
        raise WriterError("Gemini недоступен в этом запуске")
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }
    last = None
    for model in _available_models(key)[:6]:
        for attempt in range(3):
            try:
                r = requests.post(ENDPOINT.format(model=model), headers={"x-goog-api-key": key},
                                  json=body, timeout=60)
            except requests.RequestException as e:
                last = f"{model}: {type(e).__name__}"
                print(f"[gemini] {last}")
                time.sleep(5)
                break  # таймаут или обрыв — сразу пробуем другую модель
            if r.status_code == 429:
                if "limit: 0" in r.text or attempt >= 1:
                    last = f"{model}: лимит исчерпан"
                    print(f"[gemini] {last}")
                    break  # лимит этой модели кончился — пробуем следующую
                time.sleep(20)
                continue
            if r.status_code == 404 or (r.status_code == 400 and "model" in r.text.lower()):
                last = f"{model}: {r.status_code}"
                print(f"[gemini] {last}")
                break  # модель недоступна — пробуем следующую
            if r.status_code in (401, 403):
                last = f"{model}: {r.status_code} ключ не принят: {r.text[:150]}"
                print(f"[gemini] {last}")
                break
            if r.status_code != 200:
                last = f"{model}: {r.status_code} {r.text[:200]}"
                time.sleep(5)
                continue
            try:
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                time.sleep(config.GEMINI_PAUSE_SEC)
                return json.loads(_strip_fence(text))
            except Exception as e:  # noqa: BLE001
                last = f"{model}: bad response {e}"
                break
    _UNREACHABLE = True
    raise WriterError(last or "Gemini не ответил")


def _strip_fence(text):
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    return m.group(1) if m else text


def pick_best(candidates, how_many):
    """Gemini выбирает проекты, интересные широкой аудитории Telegram (не библиотеки для программистов)."""
    lines = []
    for i, c in enumerate(candidates):
        lines.append(
            f"{i}. {c['full_name']} | ⭐{c['stargazers_count']} | "
            f"{(c.get('description') or '')[:200]} | темы: {', '.join((c.get('topics') or [])[:6])}"
        )
    prompt = f"""Ты редактор популярного русскоязычного Telegram-канала про нейросети и полезные AI-инструменты с GitHub.
Аудитория — обычные пользователи, предприниматели, студенты, не только программисты.

Из списка выбери {how_many} проектов, которые вызовут больше всего интереса, сохранений и переходов.
Предпочитай: готовые приложения и инструменты, которыми можно пользоваться (ассистенты, генерация, автоматизация,
локальные нейросети, полезные утилиты). Избегай: узкие библиотеки для разработчиков, SDK, учебные репозитории,
списки ссылок (awesome-lists), датасеты, проекты без понятной пользы. Разнообразь темы.

Список:
{chr(10).join(lines)}

Ответ строго JSON: {{"picks": [номера по убыванию интереса]}}"""
    data = _ask(prompt, temperature=0.3)
    picks = [int(i) for i in data.get("picks", []) if str(i).isdigit() or isinstance(i, int)]
    return [candidates[i] for i in picks if 0 <= i < len(candidates)][:how_many]


def write_post(item, readme):
    prompt = f"""Ты пишешь пост для русскоязычного Telegram-канала про AI-инструменты с GitHub.
Стиль: живо, по делу, без воды и без кликбейт-вранья. Пиши только то, что следует из описания и README.
Не выдумывай функции. Не обещай «бесплатно навсегда», если это не сказано.

Проект: {item['full_name']}
Описание: {item.get('description')}
Темы: {', '.join(item.get('topics') or [])}
Язык: {item.get('language')}
README (фрагмент):
\"\"\"{readme[:7000]}\"\"\"

Верни строго JSON:
{{
 "title": "Название — суть в 3-6 словах (до 60 символов)",
 "card_text": "1-2 предложения для картинки: что это и чем полезно (до 160 символов)",
 "intro": "1-2 предложения для поста: что это и зачем (до 220 символов)",
 "features": ["3 коротких пункта, что умеет (до 70 символов каждый)"],
 "who": "кому пригодится, одной фразой (до 90 символов)",
 "install": "команда установки ДОСЛОВНО из README (pip/npm/brew/docker/git clone) или пустая строка",
 "category": "рубрика 1-2 слова, например: AI-агенты, Локальные нейросети, Генерация видео",
 "hashtags": ["3 хэштега без пробелов, на русском или английском, без #"],
 "red_flags": "если в README есть что-то подозрительное (скачать exe с левых сайтов, крипта, обход защиты) — опиши, иначе пустая строка"
}}"""
    data = _ask(prompt)
    # команда установки должна реально быть в README, иначе — безопасный git clone
    install = (data.get("install") or "").strip().strip("`").lstrip("$ ").strip()
    if not install or install not in readme or len(install) > 90:
        install = f"git clone https://github.com/{item['full_name']}.git"
    data["install"] = install
    data["features"] = [f for f in (data.get("features") or []) if f][:3]
    data["hashtags"] = [re.sub(r"[^\w]", "", h) for h in (data.get("hashtags") or [])][:3]
    for k in ("title", "card_text", "intro", "who", "category", "red_flags"):
        data[k] = (data.get(k) or "").strip()
    if not data["title"] or not data["intro"]:
        raise WriterError("пустой текст")
    return data
