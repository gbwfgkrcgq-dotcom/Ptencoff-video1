from html import escape
from pathlib import Path
from string import Template

from telegram import SOURCE_CHANNEL, latest

ROOT = Path(__file__).resolve().parent
post = latest(SOURCE_CHANNEL)
if post is None:
    raise SystemExit(f"В открытом превью @{SOURCE_CHANNEL} нет доступного видео.")

page = Template((ROOT / "page.html").read_text(encoding="utf-8"))
output = ROOT / "site"
output.mkdir(exist_ok=True)
html = page.substitute(media_url=escape(post["media"], quote=True), post_id=post["id"], source=SOURCE_CHANNEL)
(output / "index.html").write_text(html, encoding="utf-8")
print(post["post"])
