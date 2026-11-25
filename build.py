from html import escape
from pathlib import Path
from string import Template

from telegram import latest

ROOT = Path(__file__).resolve().parent
post = latest("ptencoff")
if post is None:
    raise SystemExit("В открытом превью @ptencoff нет доступного видео.")

page = Template((ROOT / "page.html").read_text(encoding="utf-8"))
output = ROOT / "site"
output.mkdir(exist_ok=True)
(output / "index.html").write_text(page.substitute(media_url=escape(post["media"], quote=True)), encoding="utf-8")
print(post["post"])
