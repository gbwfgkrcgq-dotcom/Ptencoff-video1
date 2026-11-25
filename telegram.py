#!/usr/bin/env python3
"""Find and optionally save the newest playable video from a public Telegram preview."""

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


SOURCE_CHANNEL = "voinaga"


class Preview(HTMLParser):
    def __init__(self, channel):
        super().__init__()
        self.channel = channel.lower()
        self.posts = {}
        self.current = None
        self.depth = 0
        self.post_depth = -1

    def handle_starttag(self, tag, items):
        attrs = dict(items)
        classes = (attrs.get("class") or "").split()
        if tag == "div":
            self.depth += 1
            post = (attrs.get("data-post") or "").split("/")
            if "tgme_widget_message" in classes and len(post) == 2:
                if post[0].lower() == self.channel and post[1].isascii() and post[1].isdigit():
                    self.current = int(post[1])
                    self.post_depth = self.depth
        if self.current is None:
            return
        if tag == "video" and attrs.get("src") and allowed_media(attrs["src"]):
            self.posts.setdefault(self.current, attrs["src"])

    def handle_endtag(self, tag):
        if tag == "div":
            if self.post_depth == self.depth:
                self.current = None
                self.post_depth = -1
            self.depth -= 1


def allowed_media(url):
    parts = urlsplit(url)
    host = parts.hostname or ""
    return (
        parts.scheme == "https"
        and parts.netloc == host
        and (host.endswith(".telesco.pe") or host.endswith(".telegram-cdn.org"))
        and parts.path.startswith("/file/")
    )


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def latest(channel):
    req = Request(f"https://t.me/s/{channel}", headers={"User-Agent": "Mozilla/5.0"})
    with build_opener(NoRedirect()).open(req, timeout=20) as response:
        html = response.read(4_000_001)
    if len(html) > 4_000_000:
        raise ValueError("Превью Telegram слишком большое.")
    parser = Preview(channel)
    parser.feed(html.decode("utf-8", errors="replace"))
    if not parser.posts:
        return None
    post_id = max(parser.posts)
    return {
        "post": f"https://t.me/{channel}/{post_id}",
        "id": post_id,
        "media": parser.posts[post_id],
    }


def save_video(info, channel, folder):
    folder.mkdir(parents=True, exist_ok=True)
    filename = folder / f"{channel.lower()}-{info['id']}.mp4"
    if filename.is_file():
        return filename
    request = Request(info["media"], headers={"User-Agent": "Mozilla/5.0"})
    temporary = None
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {"video/mp4", "application/octet-stream"}:
                raise ValueError("Видео недоступно для скачивания.")
            with tempfile.NamedTemporaryFile(dir=folder, suffix=".part", delete=False) as file:
                temporary = Path(file.name)
                first = response.read(64)
                if len(first) < 12 or first[4:8] != b"ftyp":
                    raise ValueError("Telegram вернул файл в другом формате.")
                file.write(first)
                size = len(first)
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    size += len(block)
                    if size > 250 * 1024 * 1024:
                        raise ValueError("Видео превышает лимит 250 МБ.")
                    file.write(block)
                length = response.headers.get("Content-Length")
                if length and int(length) != size:
                    raise ValueError("Видео скачалось не полностью.")
        temporary.replace(filename)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return filename


def check(args, channel):
    info = latest(channel) if channel else None
    if args.json:
        data = {"channel": channel, "post": info["post"] if info else None}
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(data) + "\n", encoding="utf-8")
    if info:
        if args.download:
            return str(save_video(info, channel, args.download))
        return info["post"]
    return "В превью канала нет доступного видео." if channel else "Укажите канал-источник."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("channel", nargs="?", default="", help="Публичный @канал или https://t.me/канал")
    parser.add_argument("--json", type=Path, help="Путь к файлу данных для панели")
    parser.add_argument("--download", type=Path, help="Папка для скачивания MP4")
    parser.add_argument("--watch", type=int, metavar="MINUTES", help="Интервал повторной проверки в минутах")
    args = parser.parse_args()
    channel = re.sub(r"^(?:@|https://t\.me/(?:s/)?)", "", args.channel.strip()).rstrip("/")
    if channel and not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{3,31}", channel):
        parser.error("Нужен публичный Telegram-канал без ссылки на конкретный пост.")
    if not channel and (args.download or args.watch is not None):
        parser.error("Укажите канал-источник.")
    if args.watch is not None and args.watch < 1:
        parser.error("Интервал должен быть не меньше одной минуты.")

    previous = None
    while True:
        try:
            result = check(args, channel)
            if result != previous:
                print(result, flush=True)
                previous = result
        except (OSError, ValueError) as error:
            print(f"Ошибка проверки Telegram: {error}", file=sys.stderr, flush=True)
            if args.watch is None:
                return 1
        if args.watch is None:
            return 0
        try:
            time.sleep(args.watch * 60)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
