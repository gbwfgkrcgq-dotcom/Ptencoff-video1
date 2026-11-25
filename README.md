# Последнее видео из @ptencoff

Страница показывает один ролик через виджет Telegram. При сборке GitHub Actions ищет последнее видео в публичном превью. В репозиторий попадает HTML со ссылкой на пост.

## Развёртывание

1. Загрузите файлы в корень репозитория, включая `.github/workflows/pages.yml`.
2. В **Settings → Pages → Build and deployment → Source** выберите **GitHub Actions**.
3. В **Actions → Telegram feed and GitHub Pages** нажмите **Run workflow**.
4. Адрес страницы появится в **Settings → Pages**.

Расписание проверяет канал в 17-ю и 47-ю минуты каждого часа. Если Telegram временно недоступен, сохраняется предыдущая версия сайта. Плеер работает, пока сам пост доступен в Telegram.

## Локальная проверка

```bash
python3 build.py
python3 -m http.server 8766 --bind 127.0.0.1 --directory site
```

Откройте [страницу](http://127.0.0.1:8766/).
