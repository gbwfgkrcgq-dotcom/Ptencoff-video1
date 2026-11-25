import argparse
from html import unescape
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from xml.etree import ElementTree

from publication_state import load_state, record_state
from telegram import SOURCE_CHANNEL, latest, save_video

STATE_REF = "refs/heads/telegram-publish-state"
if SOURCE_CHANNEL != "ptencoff":
    STATE_REF += "-" + SOURCE_CHANNEL
MAX_BYTES = 50_000_000
TREND_URL = "https://trends24.in/russia/"
TREND_RSS = "https://trends.google.com/trending/rss?geo=RU"


def fetch_trends(url):
    try:
        result = subprocess.run(
            ["curl", "--fail", "--silent", "--show-error", "--location", "--max-redirs", "3",
             "--proto-redir", "=https", "--max-time", "10", "--max-filesize", "2000000", url],
            text=True, encoding="utf-8", capture_output=True, check=True, timeout=12,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return ""
    return result.stdout


def video_caption():
    page = fetch_trends(TREND_URL)
    names = re.findall(r"""<a\b[^>]*\bclass=(?:"trend-link"|'trend-link'|trend-link(?=[\s>]))[^>]*>([^<]+)</a>""", page)
    tags = {}
    for name in names:
        tag = unescape(name).strip()
        if re.fullmatch(r"#\w{1,64}", tag):
            tags.setdefault(tag.casefold(), tag)
        if len(tags) == 5:
            break
    if len(tags) < 5:
        rss = fetch_trends(TREND_RSS)
        try:
            titles = ElementTree.fromstring(rss).findall("./channel/item/title")
        except ElementTree.ParseError:
            titles = []
        for title in titles:
            name = "_".join(re.findall(r"[^\W_]+", title.text or ""))
            if 0 < len(name) <= 64:
                tag = "#" + name
                tags.setdefault(tag.casefold(), tag)
            if len(tags) == 5:
                break
    if not tags:
        return "Видос топ"
    return "Видос топ, а актуальное ниже\n\n" + " ".join(tags.values())


def send_video(token, chat, path, caption):
    # Supply the token to curl via stdin so it does not appear in process arguments.
    config = f'url = "https://api.telegram.org/bot{token}/sendVideo"\n'
    result = subprocess.run(
        ["curl", "--silent", "--show-error", "--max-time", "240", "--config", "-",
         "--form-string", f"chat_id={chat}", "--form", f"video=@{path};type=video/mp4",
         "--form-string", "supports_streaming=true", "--form-string", f"caption={caption}"],
        input=config, text=True, capture_output=True, timeout=250,
    )
    if result.returncode != 0:
        raise RuntimeError("Не удалось подтвердить ответ Telegram. Проверьте канал перед повтором.")
    try:
        reply = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError(
            "Telegram вернул неожиданный ответ. Проверьте канал перед повтором."
        ) from None
    return reply


def publish(mode, expected_post=None):
    if mode == "preview_caption":
        print(video_caption())
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
            print("Публикация ожидает настройки секретов GitHub.")
            return
        raise RuntimeError("Добавьте секреты TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID.")
    valid_channel = re.fullmatch(r"-[0-9]{13}", chat) and -1997852516352 <= int(chat) <= -1000000000001
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) or not valid_channel:
        raise RuntimeError("Проверьте формат токена и числового ID канала.")

    revision, state = load_state(STATE_REF)
    last_sent = state["sent"]
    pending = state["pending"]
    if mode != "publish":
        if pending is None:
            print("Публикаций с неизвестным результатом нет.")
            return
        record_state(STATE_REF, revision, pending if mode == "confirm_sent" else last_sent, None)
        return
    if pending is not None:
        raise RuntimeError(
            f"Исходный пост #{pending} уже отправлялся, но результат неизвестен. "
            "Проверьте канал и выберите confirm_sent или clear_pending."
        )
    post = latest(SOURCE_CHANNEL)
    if expected_post is not None and (not post or post["id"] != expected_post):
        raise RuntimeError("Последний ролик в источнике отличается от указанного.")
    if not post or post["id"] <= last_sent:
        print("Новых доступных видео нет.")
        return

    post_id = post["id"]
    with tempfile.TemporaryDirectory(prefix="telegram-") as directory:
        path = save_video(post, SOURCE_CHANNEL, Path(directory))
        if path.stat().st_size > MAX_BYTES:
            raise RuntimeError(f"Пост #{post_id} превышает лимит 50 МБ для Bot API.")
        caption = video_caption()
        reserved = record_state(STATE_REF, revision, last_sent, post_id)
        reply = send_video(token, chat, path, caption)
        if reply.get("ok") is True:
            record_state(STATE_REF, reserved, post_id, None)
            print(f"Видео из исходного поста #{post_id} опубликовано.")
            return
        code = reply.get("error_code")
        if reply.get("ok") is False and isinstance(code, int) and 400 <= code < 500:
            record_state(STATE_REF, reserved, last_sent, None)
            raise RuntimeError(f"Telegram отклонил публикацию (код {code}).")
        raise RuntimeError("Telegram не подтвердил публикацию. Проверьте канал перед повтором.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["publish", "preview_caption", "confirm_sent", "clear_pending"])
    parser.add_argument("--post-id", type=int)
    args = parser.parse_args()
    try:
        publish(args.mode, args.post_id)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        if isinstance(error, RuntimeError):
            parser.exit(1, f"{error}\n")
        parser.exit(1, "Сбой проверки, скачивания или записи статуса. Подробности скрыты из журнала.\n")
