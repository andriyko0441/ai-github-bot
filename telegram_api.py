"""Минимальная обёртка над Telegram Bot API."""
import json
import os

import requests


class TelegramError(Exception):
    pass


def _url(method):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise TelegramError("нет TELEGRAM_BOT_TOKEN")
    return f"https://api.telegram.org/bot{token}/{method}"


def call(method, files=None, **params):
    data = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
            for k, v in params.items() if v is not None}
    r = requests.post(_url(method), data=data, files=files, timeout=60)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not body.get("ok"):
        raise TelegramError(f"{method}: {r.status_code} {body.get('description') or r.text[:200]}")
    return body["result"]


def send_message(chat_id, text, reply_markup=None):
    return call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                disable_web_page_preview=True, reply_markup=reply_markup)


def send_photo(chat_id, photo, caption, reply_markup=None):
    """photo — путь к файлу или file_id уже загруженного фото."""
    params = dict(chat_id=chat_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
    if os.path.exists(str(photo)):
        with open(photo, "rb") as f:
            return call("sendPhoto", files={"photo": f}, **params)
    return call("sendPhoto", photo=photo, **params)


def send_document(chat_id, path, caption=None):
    with open(path, "rb") as f:
        return call("sendDocument", files={"document": f}, chat_id=chat_id, caption=caption)


def get_updates(offset):
    return call("getUpdates", offset=offset, timeout=0,
                allowed_updates=["message", "callback_query"])


def answer_callback(callback_id, text):
    try:
        call("answerCallbackQuery", callback_query_id=callback_id, text=text)
    except TelegramError:
        pass  # нажатие старше пары минут — Telegram уже не принимает ответ, это нормально


def set_buttons(chat_id, message_id, reply_markup):
    try:
        call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
    except TelegramError as e:
        print(f"[tg] не удалось обновить кнопки: {e}")
