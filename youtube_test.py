import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from telegram import NoRedirect, latest, save_video

SOURCE = "ptencoff"
TARGET = "UCtrJinITCp7CpFtHSK9Go7w"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
OAUTH = "https://oauth2.googleapis.com/token"
HTTP = build_opener(NoRedirect())


def access_token():
    secrets = {key: os.environ.get("YOUTUBE_" + key.upper()) for key in (
        "client_id", "client_secret", "refresh_token"
    )}
    if not all(secrets.values()):
        raise RuntimeError("В GitHub ещё не настроены данные для OAuth YouTube.")
    form = urlencode({**secrets, "grant_type": "refresh_token"})
    with HTTP.open(Request(OAUTH, data=form.encode()), timeout=20) as response:
        return json.load(response)["access_token"]


def private_upload(token, post_id, path):
    headers = {"Authorization": "Bearer " + token}
    profile = Request(API + "/channels?part=id&mine=true", headers=headers)
    with HTTP.open(profile, timeout=20) as response:
        channels = json.load(response)["items"]
    if {channel["id"] for channel in channels} != {TARGET}:
        raise RuntimeError("Google OAuth не указывает однозначно на @Generator_yomora.")

    size = path.stat().st_size
    data = {
        "snippet": {"title": f"Проверка загрузки #{post_id}", "categoryId": "22"},
        "status": {"privacyStatus": "private"},
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
    upload = subprocess.run(
        ["curl", "--fail-with-body", "--silent", "--show-error", "--max-time", "360",
         "--config", "-", "--header", "Content-Type: video/mp4", "--upload-file", str(path)],
        input=curl_config, text=True, capture_output=True, timeout=370,
    )
    if upload.returncode != 0:
        raise RuntimeError("Загрузка не подтверждена. Проверьте YouTube Studio перед повтором.")
    result = json.loads(upload.stdout)
    if result["snippet"]["channelId"] != TARGET or result["status"]["privacyStatus"] != "private":
        raise RuntimeError("YouTube вернул неожиданный канал или режим доступа.")
    print("YouTube Studio: https://studio.youtube.com/video/" + result["id"] + "/edit")


def main():
    args = argparse.ArgumentParser()
    args.add_argument("post_id", type=int, help="Номер согласованного тестового поста в @ptencoff")
    post_id = args.parse_args().post_id
    try:
        token = access_token()
        post = latest(SOURCE)
        if not post or post["id"] != post_id:
            raise RuntimeError("Указанный ролик уже не последний доступный в публичном превью.")
        with tempfile.TemporaryDirectory(prefix="youtube-test-") as folder:
            path = save_video(post, SOURCE, Path(folder))
            private_upload(token, post_id, path)
    except HTTPError as error:
        args.exit(1, f"Google или Telegram отклонил запрос: HTTP {error.code}.\n")
    except RuntimeError as error:
        args.exit(1, f"{error}\n")
    except (OSError, KeyError, ValueError, subprocess.SubprocessError):
        args.exit(1, "Тест не подтверждён. Проверьте YouTube Studio перед повтором.\n")


if __name__ == "__main__":
    main()
