"""Тексты постов через бесплатный Gemini API."""
import json
import os
import re
import time

import requests

import config

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class WriterError(Exception):
    pass


def _ask(prompt, temperature=0.7):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise WriterError("нет GEMINI_API_KEY")
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }
    last = None
    for model in config.GEMINI_MODELS:
        for attempt in range(3):
            r = requests.post(ENDPOINT.format(model=model), params={"key": key}, json=body, timeout=90)
            if r.status_code == 429:
                time.sleep(30 * (attempt + 1))
                continue
            if r.status_code in (404, 400) and "model" in r.text.lower():
                last = f"{model}: {r.status_code}"
                break  # модель недоступна — пробуем следующую
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
