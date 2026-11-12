"""Настройки бота. Меняй значения здесь — остальной код трогать не нужно."""
import os

# --- Канал и расписание -------------------------------------------------------
TIMEZONE = "Europe/Madrid"

# Во сколько бот готовит черновики и присылает их тебе на одобрение
PREPARE_TIME = "08:00"

# Во сколько публиковать посты в канал (1 пост на слот)
PUBLISH_TIMES = ["09:00", "14:00", "20:00"]

# Сколько черновиков готовить в день (3 основных + 1 запасной)
DRAFTS_PER_DAY = 4

# Автопубликация без одобрения: через сколько часов после отправки черновика
# считать его одобренным, если ты не ответил. 0 = никогда (только по кнопке).
AUTO_APPROVE_AFTER_HOURS = float(os.getenv("AUTO_APPROVE_AFTER_HOURS", "0"))

# Подпись на креативе и в постах, например "@ai_github_finds"
CHANNEL_HANDLE = os.getenv("CHANNEL_HANDLE") or os.getenv("TELEGRAM_CHANNEL") or "@your_channel"

# --- Поиск проектов -----------------------------------------------------------
# Ниша: AI-инструменты, которыми может пользоваться обычный человек
SEARCH_TOPICS = [
    "ai", "llm", "ai-agents", "ai-assistant", "local-llm", "chatgpt",
    "stable-diffusion", "text-to-speech", "speech-to-text", "automation",
    "mcp", "self-hosted",
]
SEARCH_LOOKBACK_DAYS = 60     # проекты, созданные за последние N дней
MIN_STARS = 200               # минимум звёзд, чтобы попасть в кандидаты
GENERAL_MIN_STARS = 800       # для общего поиска без темы

# --- Фильтр безопасности ------------------------------------------------------
MIN_OWNER_AGE_DAYS = 180      # аккаунт автора старше N дней
MIN_README_CHARS = 1200       # README не пустышка
MIN_CODE_BYTES = 15_000       # в репозитории реально есть код
MAX_DAYS_SINCE_PUSH = 30      # проект живой

BANNED_WORDS = [
    "crack", "keygen", "cheat", "aimbot", "wallhack", "spoofer", "executor",
    "stealer", "grabber", "rat ", "botnet", "ddos", "free premium",
    "unlock premium", "bypass", "jailbreak", "account generator", "nitro",
    "injector", "exploit", "hwid", "кряк", " читы", "взлом",
]
SUSPICIOUS_LINK_HOSTS = [
    "mediafire.com", "mega.nz", "bit.ly", "tinyurl.com", "anonfiles",
    "gofile.io", "pixeldrain", "dropbox.com/s", "sendspace", "t.me/+",
]

# --- Gemini -------------------------------------------------------------------
GEMINI_MODELS = [
    m for m in [os.getenv("GEMINI_MODEL", "").strip()] if m
] + ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash"]
GEMINI_PAUSE_SEC = 13         # пауза между запросами (бесплатный лимит)

# --- Внешний вид креатива -----------------------------------------------------
IMAGE_WIDTH = 1080
IMAGE_HEIGHT = 1350           # 4:5 — подходит и для Telegram, и для Instagram
