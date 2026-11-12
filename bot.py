"""Оркестратор бота.

python bot.py tick      — обработать нажатия кнопок, опубликовать пост, если подошёл слот,
                          и сообщить GitHub Actions, нужно ли готовить черновики.
python bot.py prepare   — найти проекты, написать тексты, сделать креативы, прислать черновики админу.
"""
import argparse
import datetime as dt
import html
import json
import os
import pathlib
import secrets
import sys
import tempfile
import traceback
from zoneinfo import ZoneInfo

import config
import telegram_api as tg

DATA = pathlib.Path(__file__).parent / "data"
TZ = ZoneInfo(config.TIMEZONE)
QUIET_AFTER = "23:00"          # после этого времени не догоняем пропущенные слоты
DRAFT_TTL_DAYS = 2             # черновик старше — истекает


# ---------- хранилище -----------------------------------------------------------
def load(name, default):
    path = DATA / name
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save(name, obj):
    DATA.mkdir(exist_ok=True)
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def now_local():
    return dt.datetime.now(TZ)


def hhmm(t):
    return t.strftime("%H:%M")


def env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Не задан секрет {name} (Settings → Secrets and variables → Actions)")
    return value


# ---------- тексты --------------------------------------------------------------
def esc(text):
    return html.escape(str(text or ""), quote=False)


def build_caption(d):
    """Подпись к посту в канале (лимит Telegram — 1024 символа)."""
    feats = "\n".join(f"• {esc(f)}" for f in d["features"])
    tags = " ".join(f"#{t}" for t in d["hashtags"])
    meta = " · ".join(x for x in [f"⭐ {d['stars_short']}", d.get("language"), d.get("license")] if x)
    parts = [f"<b>{esc(d['title'])}</b>", esc(d["intro"])]
    if feats:
        parts.append(f"<b>Что умеет:</b>\n{feats}")
    if d.get("who"):
        parts.append(f"👤 <b>Кому пригодится:</b> {esc(d['who'])}")
    parts.append(f"{esc(meta)}\n🔗 <a href=\"{d['url']}\">Открыть на GitHub</a>")
    if tags:
        parts.append(tags)
    caption = "\n\n".join(parts)
    while len(caption) > 1000 and d["features"]:
        d["features"] = d["features"][:-1]
        return build_caption(d)
    return caption[:1024]


def channel_buttons(d):
    return {"inline_keyboard": [[{"text": "⭐ Смотреть на GitHub", "url": d["url"]}]]}


def review_buttons(draft_id):
    return {"inline_keyboard": [[
        {"text": "✅ Опубликовать", "callback_data": f"ok:{draft_id}"},
        {"text": "❌ Пропустить", "callback_data": f"no:{draft_id}"},
    ]]}


def status_button(text):
    return {"inline_keyboard": [[{"text": text, "callback_data": "noop"}]]}


# ---------- обработка кнопок и команд -------------------------------------------
def process_updates(state, queue):
    admin = int(env("TELEGRAM_ADMIN_ID"))
    by_id = {d["id"]: d for d in queue}
    while True:
        updates = tg.get_updates(state.get("update_offset", 0))
        print(f"[tick] обновлений от Telegram: {len(updates)} "
              f"{[k for u in updates for k in u if k != 'update_id']}")
        if not updates:
            break
        for u in updates:
            state["update_offset"] = u["update_id"] + 1
            cb = u.get("callback_query")
            if cb:
                if cb["from"]["id"] != admin:
                    continue
                action, _, draft_id = (cb.get("data") or "").partition(":")
                d = by_id.get(draft_id)
                if not d or d["status"] not in ("pending", "approved", "rejected"):
                    tg.answer_callback(cb["id"], "Уже неактуально")
                    continue
                if action == "ok":
                    d["status"] = "approved"
                    tg.set_buttons(admin, d["admin_msg_id"], status_button("✅ Одобрено — выйдет по расписанию"))
                    tg.answer_callback(cb["id"], "Одобрено")
                elif action == "no":
                    d["status"] = "rejected"
                    tg.set_buttons(admin, d["admin_msg_id"], status_button("❌ Пропущено"))
                    tg.answer_callback(cb["id"], "Пропущено")
                continue
            msg = u.get("message") or {}
            if (msg.get("from") or {}).get("id") != admin:
                continue
            text = (msg.get("text") or "").strip().lower()
            if text.startswith("/status"):
                tg.send_message(admin, status_text(state, queue))
            elif text.startswith("/prepare"):
                state["force_prepare"] = True
                tg.send_message(admin, "Ок, подготовлю новые черновики при следующем запуске (до часа).")
            elif text.startswith("/start") or text.startswith("/help"):
                tg.send_message(admin, HELP)


HELP = ("Я готовлю посты про AI-инструменты с GitHub.\n\n"
        "Каждое утро присылаю черновики — жми ✅ или ❌.\n"
        "Одобренные выходят в канал по расписанию: " + ", ".join(config.PUBLISH_TIMES) + ".\n\n"
        "Команды:\n/status — что в очереди\n/prepare — подготовить новые черновики\n\n"
        "Кнопки срабатывают при следующем запуске бота (в течение часа).")


def status_text(state, queue):
    today = now_local().date().isoformat()
    counts = {}
    for d in queue:
        counts[d["status"]] = counts.get(d["status"], 0) + 1
    names = {"pending": "ждут решения", "approved": "одобрены", "posted": "опубликованы",
             "rejected": "пропущены", "expired": "истекли", "failed": "ошибка"}
    lines = [f"{names.get(k, k)}: {v}" for k, v in counts.items()] or ["очередь пуста"]
    return (f"📊 Сегодня опубликовано: {state.get('published_count', {}).get(today, 0)}"
            f" из {len(config.PUBLISH_TIMES)}\n" + "\n".join(lines))


# ---------- публикация ----------------------------------------------------------
def expire_and_autoapprove(queue):
    now = dt.datetime.now(dt.timezone.utc)
    admin = os.environ.get("TELEGRAM_ADMIN_ID")
    for d in queue:
        created = dt.datetime.fromisoformat(d["created_at"])
        age_h = (now - created).total_seconds() / 3600
        if d["status"] in ("pending", "approved") and age_h > DRAFT_TTL_DAYS * 24:
            d["status"] = "expired"
            if admin:
                tg.set_buttons(int(admin), d["admin_msg_id"], status_button("⌛ Истёк"))
        elif (d["status"] == "pending" and config.AUTO_APPROVE_AFTER_HOURS > 0
              and age_h >= config.AUTO_APPROVE_AFTER_HOURS and not d.get("red_flags")):
            d["status"] = "approved"
            if admin:
                tg.set_buttons(int(admin), d["admin_msg_id"], status_button("✅ Одобрено автоматически"))


def publish_one(state, queue, posted, force=False):
    now = now_local()
    today = now.date().isoformat()
    passed = sum(1 for t in config.PUBLISH_TIMES if t <= hhmm(now))
    done = state.setdefault("published_count", {}).get(today, 0)
    if not force and (done >= passed or hhmm(now) >= QUIET_AFTER):
        return None
    draft = next((d for d in queue if d["status"] == "approved"), None)
    if not draft:
        return None
    channel = env("TELEGRAM_CHANNEL")
    try:
        msg = tg.send_photo(channel, draft["photo_file_id"], draft["caption"],
                            reply_markup=channel_buttons(draft))
    except tg.TelegramError as e:
        draft["status"] = "failed"
        draft["error"] = str(e)
        tg.send_message(int(env("TELEGRAM_ADMIN_ID")), f"⚠️ Не удалось опубликовать {draft['full_name']}: {esc(e)}")
        return None
    draft["status"] = "posted"
    draft["posted_at"] = now.isoformat()
    state["published_count"][today] = done + 1
    # чистим счётчики старше недели
    for day in list(state["published_count"]):
        if day < (now.date() - dt.timedelta(days=7)).isoformat():
            del state["published_count"][day]
    posted.append({"full_name": draft["full_name"], "date": today, "message_id": msg.get("message_id")})
    tg.set_buttons(int(env("TELEGRAM_ADMIN_ID")), draft["admin_msg_id"], status_button("📢 Опубликовано"))
    print(f"[publish] {draft['full_name']}")
    return draft


def prepare_due(state):
    now = now_local()
    today = now.date().isoformat()
    if state.pop("force_prepare", False):
        return True
    fails = state.get("prepare_fails", {})
    if fails.get("date") == today and fails.get("count", 0) >= 2:
        return False  # дважды упало сегодня — не спамим, ждём завтра или /prepare
    return hhmm(now) >= config.PREPARE_TIME and state.get("last_prepare_date") != today


# ---------- подготовка черновиков -----------------------------------------------
def prepare(state, queue, posted):
    import github_source as gh
    import render
    import writer

    admin = int(env("TELEGRAM_ADMIN_ID"))
    exclude = {p["full_name"].lower() for p in posted} | {d["full_name"].lower() for d in queue}
    candidates = gh.search_candidates(exclude)[:45]
    print(f"[prepare] кандидатов: {len(candidates)}")

    safe, rejected = [], []
    for item in candidates:
        ok, reason, extra = gh.safety_check(item)
        if ok:
            item["_extra"] = extra
            safe.append(item)
        else:
            rejected.append(f"{item['full_name']}: {reason}")
        if len(safe) >= 12:
            break
    print(f"[prepare] прошли проверку: {len(safe)}; отсеяно: {len(rejected)}")
    if not safe:
        tg.send_message(admin, "⚠️ Сегодня не нашёл проектов, прошедших проверку. Попробую завтра.")
        return

    try:
        picks = writer.pick_best(safe, config.DRAFTS_PER_DAY)
    except writer.WriterError as e:
        print(f"[prepare] отбор через Gemini не сработал: {e}")
        picks = []
    if len(picks) < config.DRAFTS_PER_DAY:  # добиваем по скорости роста звёзд
        picks += [s for s in safe if s not in picks][: config.DRAFTS_PER_DAY - len(picks)]

    tmp = pathlib.Path(tempfile.mkdtemp())
    sent = 0
    problems = []
    for n, item in enumerate(picks, 1):
        extra = item["_extra"]
        try:
            post = writer.write_post(item, extra["readme"])
        except writer.WriterError as e:
            print(f"[prepare] текст для {item['full_name']} не получился: {e}")
            problems.append(f"текст {item['full_name']}: {e}")
            continue
        draft_id = secrets.token_hex(4)
        d = {
            "id": draft_id, "full_name": item["full_name"], "url": item["html_url"],
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "status": "pending",
            "stars_short": render.short_num(item["stargazers_count"]),
            "language": item.get("language") or "",
            "license": ((item.get("license") or {}).get("spdx_id") or "").replace("NOASSERTION", ""),
            **{k: post[k] for k in ("title", "intro", "features", "who", "hashtags", "category",
                                     "card_text", "install", "red_flags")},
        }
        d["caption"] = build_caption(dict(d))
        png = tmp / f"{draft_id}.png"
        try:
            render.render(item, post, png, palette=render.PALETTES[(len(posted) + n) % len(render.PALETTES)])
        except Exception as e:  # noqa: BLE001
            print(f"[prepare] рендер {item['full_name']} упал: {e}")
            problems.append(f"картинка {item['full_name']}: {str(e)[:200]}")
            continue

        checks = (f"🛡 Проверка: автор на GitHub с {extra.get('owner_since') or '?'}, "
                  f"лицензия {d['license'] or '?'}, кода {extra['code_bytes'] // 1024} КБ")
        if item.get("_velocity", 0) > config.SUSPICIOUS_STARS_PER_DAY:
            checks += (f"\n⚠️ <b>Звёзды растут подозрительно быстро</b> (~{int(item['_velocity'])}/день) — "
                       "возможна накрутка, проверь перед публикацией")
            d["red_flags"] = (d["red_flags"] + " накрутка звёзд?").strip()
        if d["red_flags"]:
            checks += f"\n⚠️ <b>Внимание:</b> {esc(d['red_flags'])}"
        review = f"📝 <b>Черновик {n}/{len(picks)}</b>\n{checks}\n\n{d['caption']}"
        if len(review) > 1024:
            review = f"📝 <b>Черновик {n}/{len(picks)}</b>\n{checks}\n\n<b>{esc(d['title'])}</b>\n{esc(d['intro'])}\n{d['url']}"
        msg = tg.send_photo(admin, png, review[:1024], reply_markup=review_buttons(draft_id))
        d["admin_msg_id"] = msg["message_id"]
        d["photo_file_id"] = msg["photo"][-1]["file_id"]
        queue.append(d)
        sent += 1

    state["last_prepare_date"] = now_local().date().isoformat()
    report = f"Готово: {sent} черновиков. Одобренные выйдут в {', '.join(config.PUBLISH_TIMES)}."
    if problems:
        report += "\n\n⚠️ Проблемы:\n" + "\n".join(f"• {esc(p)}" for p in problems[:6])
    tg.send_message(admin, report[:4000])


# ---------- точка входа -----------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["tick", "prepare"])
    ap.add_argument("--action", default="tick", help="tick | prepare | publish-now")
    args = ap.parse_args()

    state = load("state.json", {})
    queue = load("queue.json", [])
    posted = load("posted.json", [])
    try:
        if args.command == "tick":
            try:
                me = tg.call("getMe")
                hook = tg.call("getWebhookInfo")
                print(f"[tick] бот @{me.get('username')}, webhook: {hook.get('url') or 'нет'}, "
                      f"ожидает обновлений: {hook.get('pending_update_count')}")
            except tg.TelegramError as e:
                print(f"[tick] диагностика: {e}")
            process_updates(state, queue)
            expire_and_autoapprove(queue)
            publish_one(state, queue, posted, force=args.action == "publish-now")
            need = args.action == "prepare" or prepare_due(state)
            out = os.environ.get("GITHUB_OUTPUT")
            if out:
                with open(out, "a") as f:
                    f.write(f"need_prepare={'true' if need else 'false'}\n")
            print(f"[tick] need_prepare={need}")
        else:
            prepare(state, queue, posted)
    except Exception:
        traceback.print_exc()
        admin = os.environ.get("TELEGRAM_ADMIN_ID")
        if admin:
            try:
                tg.send_message(int(admin), f"⚠️ Ошибка бота ({args.command}):\n<code>{esc(traceback.format_exc()[-700:])}</code>")
            except Exception:  # noqa: BLE001
                pass
        if args.command == "prepare":
            today = now_local().date().isoformat()
            fails = state.get("prepare_fails", {})
            state["prepare_fails"] = {"date": today,
                                      "count": (fails.get("count", 0) if fails.get("date") == today else 0) + 1}
        save("state.json", state)
        save("queue.json", queue[-200:])
        save("posted.json", posted)
        sys.exit(1)
    save("state.json", state)
    save("queue.json", queue[-200:])
    save("posted.json", posted)


if __name__ == "__main__":
    main()
