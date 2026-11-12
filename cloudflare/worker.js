/**
 * Git_Hacker — «дежурный» модуль на Cloudflare Workers (бесплатно, работает 24/7).
 *
 * 1. Мгновенно отвечает на кнопки ✅ / ❌ в Telegram и записывает решение
 *    в репозиторий (data/decisions/<id>.json). Основной бот применит его при запуске.
 * 2. Команды админа: /status, /prepare, /help.
 * 3. Cron Trigger раз в 10 минут: в 09:00, 14:00, 20:00 и 21:00 (Мадрид) будит основного бота на GitHub.
 * 4. Страница /setup подключает Telegram к этому модулю (webhook) и проверяет настройки.
 *    Она ничего не запускает и не показывает ключи — открыть её может кто угодно без вреда.
 *
 * Переменные (Settings → Variables and Secrets):
 *   TELEGRAM_BOT_TOKEN  (Secret) — токен от BotFather
 *   TELEGRAM_ADMIN_ID   (Text)   — твой Telegram ID
 *   GITHUB_TOKEN        (Secret) — fine-grained токен: Actions + Contents (Read and write)
 *   GITHUB_REPO         (Text)   — andriyko0441/ai-github-bot
 */

const WORKFLOW = "bot.yml";
const BRANCH = "main";

// Когда будить основного бота (время Мадрида): слоты публикации и подготовка черновиков.
// Будим только в первые 20 минут после этих моментов — так бот укладывается
// в бесплатный лимит GitHub Actions даже для приватного репозитория (~8 запусков в день).
const WAKE_TIMES = ["09:00", "14:00", "20:00", "21:00"];
const WAKE_WINDOW_MIN = 20;
const TIMEZONE = "Europe/Madrid";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/setup") {
      return setup(url, env);
    }

    if (url.pathname === "/tg" && request.method === "POST") {
      const expected = await webhookSecret(env);
      if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== expected) {
        return new Response("forbidden", { status: 403 });
      }
      const update = await request.json();
      ctx.waitUntil(handleUpdate(update, env).catch((e) => console.log("update error:", e.stack || e)));
      return new Response("ok");
    }

    return new Response("Git_Hacker worker работает. Настройка: /setup", {
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  },

  async scheduled(event, env, ctx) {
    if (!shouldWake(new Date(event.scheduledTime || Date.now()))) return;
    ctx.waitUntil(dispatch(env, "tick").catch((e) => console.log("dispatch error:", e.stack || e)));
  },
};

function madridMinutes(date) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: TIMEZONE, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
  const get = (t) => Number(parts.find((p) => p.type === t).value);
  return get("hour") * 60 + get("minute");
}

export function shouldWake(date) {
  const now = madridMinutes(date);
  return WAKE_TIMES.some((t) => {
    const [h, m] = t.split(":").map(Number);
    const diff = now - (h * 60 + m);
    return diff >= 0 && diff < WAKE_WINDOW_MIN;
  });
}

// ---------------------------------------------------------------- Telegram ---

async function tg(env, method, params) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(params),
  });
  const body = await r.json().catch(() => ({}));
  if (!body.ok) console.log(`tg ${method}:`, JSON.stringify(body).slice(0, 300));
  return body;
}

function statusButton(text) {
  return { inline_keyboard: [[{ text, callback_data: "noop" }]] };
}

async function handleUpdate(update, env) {
  const admin = String(env.TELEGRAM_ADMIN_ID || "").trim();

  const cb = update.callback_query;
  if (cb) {
    if (String(cb.from.id) !== admin) {
      return tg(env, "answerCallbackQuery", { callback_query_id: cb.id, text: "Нет доступа" });
    }
    const [action, id] = String(cb.data || "").split(":");
    if (action !== "ok" && action !== "no") {
      return tg(env, "answerCallbackQuery", { callback_query_id: cb.id, text: "Решение уже принято" });
    }
    const approved = action === "ok";
    // 1) мгновенный ответ — кнопка перестаёт «крутиться»
    await tg(env, "answerCallbackQuery", {
      callback_query_id: cb.id,
      text: approved ? "✅ Одобрено" : "❌ Пропущено",
    });
    if (cb.message) {
      await tg(env, "editMessageReplyMarkup", {
        chat_id: cb.message.chat.id,
        message_id: cb.message.message_id,
        reply_markup: statusButton(approved ? "✅ Одобрено — выйдет по расписанию" : "❌ Пропущено"),
      });
    }
    // 2) решение — в репозиторий, основной бот применит его при следующем запуске
    const saved = await saveDecision(env, id, approved ? "approved" : "rejected");
    if (!saved && cb.message) {
      await tg(env, "sendMessage", {
        chat_id: cb.message.chat.id,
        text: "⚠️ Не удалось записать решение в GitHub. Проверь GITHUB_TOKEN в Cloudflare (страница /setup).",
      });
    }
    return;
  }

  const msg = update.message;
  if (!msg || String((msg.from || {}).id) !== admin) return;
  const text = String(msg.text || "").trim().toLowerCase();
  const chat_id = msg.chat.id;

  if (text.startsWith("/prepare")) {
    const ok = await dispatch(env, "prepare");
    return tg(env, "sendMessage", {
      chat_id,
      text: ok ? "Готовлю новые черновики — пришлю через 3–10 минут." : "⚠️ Не удалось запустить бота на GitHub.",
    });
  }
  if (text.startsWith("/status")) {
    return tg(env, "sendMessage", { chat_id, text: await statusText(env) });
  }
  if (text.startsWith("/start") || text.startsWith("/help")) {
    return tg(env, "sendMessage", {
      chat_id,
      text:
        "Я готовлю посты про AI-инструменты с GitHub.\n\n" +
        "Каждый вечер в 21:00 присылаю черновики на завтра — жми ✅ или ❌, ответ мгновенный.\n" +
        "Одобренные выходят в 09:00, 14:00 и 20:00.\n\n" +
        "/status — что в очереди\n/prepare — подготовить новые черновики сейчас",
    });
  }
}

// ------------------------------------------------------------------ GitHub ---

function gh(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPO}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "git-hacker-worker",
      ...(init.headers || {}),
    },
  });
}

async function dispatch(env, action) {
  const r = await gh(env, `/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ref: BRANCH, inputs: { action } }),
  });
  if (r.status !== 204) console.log("dispatch:", r.status, (await r.text()).slice(0, 300));
  return r.status === 204;
}

function toBase64(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

function fromBase64(b64) {
  const bin = atob(String(b64).replace(/\n/g, ""));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}

async function saveDecision(env, id, status) {
  if (!/^[0-9a-f]{4,16}$/i.test(id || "")) return false;
  const path = `data/decisions/${id}.json`;
  const content = toBase64(JSON.stringify({ id, status, at: new Date().toISOString() }) + "\n");
  for (let attempt = 0; attempt < 4; attempt++) {
    // если по этому черновику уже было решение — нужен sha, чтобы перезаписать
    let sha;
    const cur = await gh(env, `/contents/${path}?ref=${BRANCH}`);
    if (cur.status === 200) sha = (await cur.json()).sha;
    const r = await gh(env, `/contents/${path}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: `decision ${id}: ${status}`, content, branch: BRANCH, ...(sha ? { sha } : {}) }),
    });
    if (r.status === 200 || r.status === 201) return true;
    console.log("saveDecision:", r.status, (await r.text()).slice(0, 200));
    await new Promise((res) => setTimeout(res, 700 * (attempt + 1)));
  }
  return false;
}

async function statusText(env) {
  const names = {
    pending: "ждут решения", approved: "одобрены", posted: "опубликованы",
    rejected: "пропущены", expired: "истекли", failed: "ошибка",
    decided: "решение принято, применится в течение 10 минут",
  };
  try {
    const r = await gh(env, `/contents/data/queue.json?ref=${BRANCH}`);
    const queue = r.status === 200 ? JSON.parse(fromBase64((await r.json()).content)) : [];
    // решения, которые бот ещё не успел применить
    const dr = await gh(env, `/contents/data/decisions?ref=${BRANCH}`);
    const files = dr.status === 200 ? await dr.json() : [];
    const pendingIds = new Set(files.filter((f) => f.name.endsWith(".json")).map((f) => f.name.replace(".json", "")));
    const counts = {};
    for (const d of queue.slice(-30)) {
      let st = d.status;
      if (st === "pending" && pendingIds.has(d.id)) st = "decided";
      counts[st] = (counts[st] || 0) + 1;
    }
    const lines = Object.entries(counts).map(([k, v]) => `${names[k] || k}: ${v}`);
    return "📊 Очередь (последние 30):\n" + (lines.join("\n") || "пусто");
  } catch (e) {
    return "⚠️ Не удалось прочитать очередь: " + e;
  }
}

// ------------------------------------------------------------------- setup ---

async function webhookSecret(env) {
  const data = new TextEncoder().encode("git-hacker:" + (env.TELEGRAM_BOT_TOKEN || ""));
  const hash = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 48);
}

async function setup(url, env) {
  const lines = [];
  const missing = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_ID", "GITHUB_TOKEN", "GITHUB_REPO"].filter((k) => !env[k]);
  if (missing.length) {
    lines.push("❌ Не заданы переменные: " + missing.join(", "));
  } else {
    const me = await tg(env, "getMe", {});
    lines.push(me.ok ? `✅ Telegram: бот @${me.result.username}` : "❌ Telegram: токен не подошёл");

    const repo = await gh(env, "");
    lines.push(repo.status === 200 ? `✅ GitHub: доступ к ${env.GITHUB_REPO}` : `❌ GitHub: ${repo.status} — проверь GITHUB_TOKEN и GITHUB_REPO`);

    if (me.ok) {
      const hook = await tg(env, "setWebhook", {
        url: `${url.origin}/tg`,
        secret_token: await webhookSecret(env),
        allowed_updates: ["message", "callback_query"],
      });
      lines.push(hook.ok ? "✅ Кнопки подключены к этому модулю (webhook)" : "❌ Webhook: " + (hook.description || "ошибка"));
    }
    // страница /setup ничего не запускает и не раскрывает — её безопасно открывать повторно
  }
  lines.push("", "Не забудь: Settings → Triggers → Cron Triggers → */10 * * * *");
  return new Response(lines.join("\n"), { headers: { "content-type": "text/plain; charset=utf-8" } });
}
