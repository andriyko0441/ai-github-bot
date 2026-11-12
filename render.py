"""Рендер креатива: HTML-шаблон -> PNG/JPG через Playwright (Chromium)."""
import html
import pathlib
import random
import secrets

import config

TEMPLATE = pathlib.Path(__file__).parent / "templates" / "card.html"

# Палитры фона: (основа, середина, низ, акцент)
PALETTES = [
    ("#3b3f9e", "#6c5ce7", "#3fc1c9", "#8a7cf0"),   # фиолетово-бирюзовый
    ("#5b2a86", "#c0398a", "#f5a623", "#e85d9f"),   # фиолетово-оранжевый
    ("#232a8f", "#5b6cf0", "#58c6f2", "#7a5cf0"),   # синий
    ("#1d3c78", "#3a8ee6", "#f28bb5", "#7b61ff"),   # сине-розовый
    ("#4a1f7a", "#9b4dca", "#ff7a59", "#d9468f"),   # пурпурно-коралловый
    ("#14524f", "#2fa58f", "#5dd39e", "#3a7bd5"),   # изумрудный
]


def short_num(n):
    n = int(n or 0)
    if n >= 1000:
        s = f"{n / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{s}K"
    return str(n)


def build_html(item, post, palette=None):
    owner, repo = item["full_name"].split("/", 1)
    c1, c2, c3, c4 = palette or random.choice(PALETTES)
    values = {
        "W": config.IMAGE_WIDTH, "H": config.IMAGE_HEIGHT,
        "c1": c1, "c2": c2, "c3": c3, "c4": c4,
        "category": post.get("category") or "AI · GitHub",
        "full_name": item["full_name"], "owner": owner, "repo": repo,
        # случайный хэш, чтобы GitHub отдал свежую карточку репозитория
        "og_url": f"https://opengraph.githubassets.com/{secrets.token_hex(8)}/{item['full_name']}",
        "avatar": item.get("owner", {}).get("avatar_url", ""),
        "description": item.get("description") or "",
        "stars_short": short_num(item.get("stargazers_count")),
        "forks_short": short_num(item.get("forks_count")),
        "language": item.get("language") or "—",
        "card_text": post.get("card_text") or post.get("intro") or "",
        "install": post.get("install") or f"git clone https://github.com/{item['full_name']}.git",
        "handle": config.CHANNEL_HANDLE,
    }
    page = TEMPLATE.read_text(encoding="utf-8")
    for key, val in values.items():
        safe = str(val) if key in ("W", "H") else html.escape(str(val), quote=True)
        page = page.replace("{{" + key + "}}", safe)
    return page


def render(item, post, out_png, out_jpg=None, palette=None):
    from playwright.sync_api import sync_playwright

    page_html = build_html(item, post, palette)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:  # noqa: BLE001 — локально может быть только системный Chrome
            browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": config.IMAGE_WIDTH, "height": config.IMAGE_HEIGHT})
        page.set_content(page_html, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(500)
        page.screenshot(path=str(out_png))
        if out_jpg:
            page.screenshot(path=str(out_jpg), type="jpeg", quality=92)
        browser.close()
    return out_png
