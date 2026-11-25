import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from telegram import latest, save_video

CHANNEL = "ptencoff"
STATE_REF = "refs/heads/telegram-publish-state"
STATE_FILE = "state.json"
MAX_BYTES = 50_000_000


def git(*args, data=None, check=True):
    return subprocess.run(
        ["git", *args], input=data, text=True, capture_output=True, check=check
    )


def load_state():
    branch = git("ls-remote", "--exit-code", "origin", STATE_REF, check=False)
    if branch.returncode == 2:
        return None, {"sent": 0, "pending": None}
    if branch.returncode != 0:
        raise RuntimeError("Не удалось проверить ветку состояния.")
    git("fetch", "--no-tags", "origin", STATE_REF)
    revision = git("rev-parse", "FETCH_HEAD").stdout.strip()
    return revision, json.loads(git("show", f"{revision}:{STATE_FILE}").stdout)


def record_state(previous, sent, pending):
    content = json.dumps({"sent": sent, "pending": pending}) + "\n"
    blob = git("hash-object", "-w", "--stdin", data=content).stdout.strip()
    tree = git("mktree", data=f"100644 blob {blob}\t{STATE_FILE}\n").stdout.strip()
    args = ["commit-tree", tree, "-m", "Update Telegram publication status"]
    if previous:
        args.extend(["-p", previous])
    revision = git(*args).stdout.strip()
    git("push", f"--force-with-lease={STATE_REF}:{previous or ''}", "origin", f"{revision}:{STATE_REF}")
    return revision


def send_video(token, chat, path):
    # Supply the token to curl via stdin so it does not appear in process arguments.
    config = f'url = "https://api.telegram.org/bot{token}/sendVideo"\n'
    result = subprocess.run(
        ["curl", "--silent", "--show-error", "--max-time", "240", "--config", "-",
         "--form-string", f"chat_id={chat}", "--form", f"video=@{path};type=video/mp4",
         "--form-string", "supports_streaming=true"],
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


def publish(mode):
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

    revision, state = load_state()
    last_sent = state["sent"]
    pending = state["pending"]
    if mode != "publish":
        if pending is None:
            print("Публикаций с неизвестным результатом нет.")
            return
        record_state(revision, pending if mode == "confirm_sent" else last_sent, None)
        return
    if pending is not None:
        raise RuntimeError(
            f"Исходный пост #{pending} уже отправлялся, но результат неизвестен. "
            "Проверьте канал и выберите confirm_sent или clear_pending."
        )
    post = latest(CHANNEL)
    if not post or post["id"] <= last_sent:
        print("Новых доступных видео нет.")
        return

    post_id = post["id"]
    with tempfile.TemporaryDirectory(prefix="telegram-") as directory:
        path = save_video(post, CHANNEL, Path(directory))
        if path.stat().st_size > MAX_BYTES:
            raise RuntimeError(f"Пост #{post_id} превышает лимит 50 МБ для Bot API.")
        reserved = record_state(revision, last_sent, post_id)
        reply = send_video(token, chat, path)
        if reply.get("ok") is True:
            record_state(reserved, post_id, None)
            print(f"Видео из исходного поста #{post_id} опубликовано.")
            return
        code = reply.get("error_code")
        if reply.get("ok") is False and isinstance(code, int) and 400 <= code < 500:
            record_state(reserved, last_sent, None)
            raise RuntimeError(f"Telegram отклонил публикацию (код {code}).")
        raise RuntimeError("Telegram не подтвердил публикацию. Проверьте канал перед повтором.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["publish", "confirm_sent", "clear_pending"])
    args = parser.parse_args()
    try:
        publish(args.mode)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        if isinstance(error, RuntimeError):
            parser.exit(1, f"{error}\n")
        parser.exit(1, "Сбой проверки, скачивания или записи статуса. Подробности скрыты из журнала.\n")
