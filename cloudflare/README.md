# Модуль на Cloudflare: мгновенные кнопки и надёжный будильник

Бесплатный модуль, который работает круглосуточно:
- мгновенно отвечает на кнопки ✅ / ❌ и команды `/status`, `/prepare`;
- каждые 10 минут будит основного бота на GitHub.

## 1. Ключ GitHub (для модуля)
1. github.com → аватар справа вверху → **Settings** → внизу слева **Developer settings**.
2. **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
3. Token name: `cloudflare-git-hacker`. Expiration: **Custom** → дата через год.
4. Repository access: **Only select repositories** → `ai-github-bot`.
5. Permissions → Repository permissions:
   - **Actions** → Read and write
   - **Contents** → Read and write
6. **Generate token** → скопируй (`github_pat_...`). Никому не отправляй, только в Cloudflare.

## 2. Worker в Cloudflare
1. Зарегистрируйся на **dash.cloudflare.com** (бесплатно).
2. Слева **Compute (Workers)** → **Workers & Pages** → **Create** → **Create Worker** (Start with Hello World).
3. Имя: `git-hacker-bot` → **Deploy**.
4. **Edit code** → удали всё → вставь содержимое `cloudflare/worker.js` → **Deploy**.

## 3. Переменные
Worker → **Settings** → **Variables and Secrets** → **Add**:

| Type | Name | Value |
|---|---|---|
| Secret | `TELEGRAM_BOT_TOKEN` | токен от BotFather |
| Text | `TELEGRAM_ADMIN_ID` | твой Telegram ID |
| Secret | `GITHUB_TOKEN` | ключ из шага 1 |
| Text | `GITHUB_REPO` | `andriyko0441/ai-github-bot` |

→ **Deploy**.

## 4. Будильник
Worker → **Settings** → **Trigger Events** (или Triggers) → **Add** → **Cron Triggers** → выражение `*/10 * * * *` → **Add**.

## 5. Подключение
Открой в браузере `https://git-hacker-bot.<твой-поддомен>.workers.dev/setup`.
Все строки должны быть с ✅. После этого кнопки в боте отвечают мгновенно.
