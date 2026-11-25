import argparse
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from publication_state import load_state, record_state
from telegram import NoRedirect, SOURCE_CHANNEL, allowed_media, save_video

SOURCE_PAGE = "https://gbwfgkrcgq-dotcom.github.io/Ptencoff-video1/"
TARGET = "UC8t5d8k9otR3-sR3y8n35iw"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
OAUTH = "https://oauth2.googleapis.com/token"
HTTP = build_opener(NoRedirect())
STATE_REF = "refs/heads/youtube-publish-state-" + SOURCE_CHANNEL


class PublishedPlayer(HTMLParser):
    def __init__(self):
        super().__init__()
        self.videos = []

    def handle_starttag(self, tag, attrs):
        if tag == "video":
            self.videos.append(dict(attrs))


def page_video(post_id=None):
    url = SOURCE_PAGE + "?" + urlencode({"source": SOURCE_CHANNEL, "check": time.time_ns()})
    with HTTP.open(url, timeout=20) as response:
        html = response.read(100_001)
    player = PublishedPlayer()
    player.feed(html.decode("utf-8"))
    if len(player.videos) != 1:
        raise RuntimeError("На странице GitHub Pages должен быть один плеер.")
    item = player.videos[0]
    page_post = item.get("data-post-id", "")
    if item.get("data-source") != SOURCE_CHANNEL or not page_post.isdecimal():
        raise RuntimeError("Плеер GitHub Pages не показывает текущий канал-источник.")
    if post_id is not None and page_post != str(post_id):
        raise RuntimeError("На странице GitHub Pages пока нет указанного видео.")
    source = item.get("src") or ""
    if not allowed_media(source):
        raise RuntimeError("Плеер GitHub Pages не содержит ожидаемого адреса MP4.")
    return {"id": int(page_post), "media": source}


def access_token():
    secrets = {key: os.environ.get("YOUTUBE_" + key.upper()) for key in (
        "client_id", "client_secret", "refresh_token"
    )}
    if not all(secrets.values()):
        raise RuntimeError("В GitHub ещё не настроены данные для OAuth YouTube.")
    form = urlencode({**secrets, "grant_type": "refresh_token"})
    with HTTP.open(Request(OAUTH, data=form.encode()), timeout=20) as response:
        return json.load(response)["access_token"]


def upload_video(token, path, visibility, title, before_send=None):
    headers = {"Authorization": "Bearer " + token}
    profile = Request(API + "/channels?part=id&mine=true", headers=headers)
    with HTTP.open(profile, timeout=20) as response:
        channels = json.load(response)["items"]
    if {channel["id"] for channel in channels} != {TARGET}:
        raise RuntimeError("Google OAuth не указывает однозначно на @Filkina_Gramata.")

    size = path.stat().st_size
    data = {
        "snippet": {"title": title, "categoryId": "22"},
        "status": {"privacyStatus": visibility},
    }
    start = Request(
        UPLOAD + "?uploadType=resumable&part=snippet,status&notifySubscribers=false",
        data=json.dumps(data).encode(),
        headers={
            **headers,
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(size),
        },
    )
    with HTTP.open(start, timeout=20) as response:
        location = response.headers.get("Location", "")
    parsed = urlsplit(location)
    if (parsed.scheme, parsed.netloc, parsed.path) != (
        "https", "www.googleapis.com", "/upload/youtube/v3/videos"
    ) or '"' in location or not location.isascii():
        raise RuntimeError("YouTube вернул неожиданный адрес загрузки.")

    # Keep the OAuth token and upload session out of process arguments.
    curl_config = f'url = "{location}"\nheader = "Authorization: Bearer {token}"\n'
    if before_send is not None:
        before_send()
    upload = subprocess.run(
        ["curl", "--fail-with-body", "--silent", "--show-error", "--max-time", "360",
         "--config", "-", "--header", "Content-Type: video/mp4", "--upload-file", str(path)],
        input=curl_config, text=True, capture_output=True, timeout=370,
    )
    if upload.returncode != 0:
        raise RuntimeError("Загрузка не подтверждена. Проверьте YouTube Studio перед повтором.")
    result = json.loads(upload.stdout)
    if result["snippet"]["channelId"] != TARGET or result["status"]["privacyStatus"] != visibility:
        raise RuntimeError("YouTube вернул неожиданный канал или режим доступа.")
    print("YouTube Studio: https://studio.youtube.com/video/" + result["id"] + "/edit")


def publish(options):
    if options.mode != "upload":
        revision, state = load_state(STATE_REF)
        pending = state["pending"]
        if pending is not None:
            last_sent = pending if options.mode == "confirm_sent" else state["sent"]
            record_state(STATE_REF, revision, last_sent, None)
        return

    post = page_video(options.post_id)
    post_id = post["id"]
    reservation = None
    before_send = None
    if options.visibility == "public":
        revision, state = load_state(STATE_REF)
        if state["pending"] is not None:
            raise RuntimeError(
                f"Отправка видео #{state['pending']} не подтверждена. Проверьте YouTube Studio "
                "и выберите confirm_sent или clear_pending."
            )
        if post_id <= state["sent"]:
            print(f"Видео #{post_id} уже опубликовано на YouTube.")
            return

        def reserve():
            nonlocal reservation
            reservation = record_state(STATE_REF, revision, state["sent"], post_id)

        before_send = reserve

    token = access_token()
    with tempfile.TemporaryDirectory(prefix="youtube-upload-") as folder:
        path = save_video(post, SOURCE_CHANNEL, Path(folder))
        upload_video(token, path, options.visibility, options.title.strip() or f"Видео {post_id}", before_send)
    if reservation is not None:
        record_state(STATE_REF, reservation, post_id, None)


def main():
    args = argparse.ArgumentParser()
    args.add_argument("post_id", type=int, nargs="?", help=f"Номер поста в @{SOURCE_CHANNEL}")
    args.add_argument("--visibility", choices=["private", "public"], default="private")
    args.add_argument("--title", default="")
    args.add_argument("--mode", choices=["upload", "confirm_sent", "clear_pending"], default="upload")
    try:
        publish(args.parse_args())
    except HTTPError as error:
        args.exit(1, f"Google или Telegram отклонил запрос: HTTP {error.code}.\n")
    except RuntimeError as error:
        args.exit(1, f"{error}\n")
    except (OSError, KeyError, ValueError, subprocess.SubprocessError):
        args.exit(1, "Тест не подтверждён. Проверьте YouTube Studio перед повтором.\n")


if __name__ == "__main__":
    main()
