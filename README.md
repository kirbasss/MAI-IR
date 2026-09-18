# MAI-IR — ЛР 1: добыча игрового корпуса

Корпус: русскоязычные публикации о видеоиграх и игровой индустрии: новости,
статьи, обзоры, превью и аналитика. Исходные сайты — PlayGround.ru,
StopGame.ru, IXBT.games и Игромания. Список прежних lyrics-URL сохранён в
`urls_lyrics_legacy.txt` только для справки; новый корпус изолирован в
`data`.

## Установка

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Python 3.11+ и UTF-8 обязательны.

## Сбор образцов

`urls.txt` содержит проверочную выборку: 3 страницы PlayGround, 5 StopGame,
4 IXBT.games и 3 Игромании. Формат строки:

```text
source<TAB>url
```

Запуск:

```powershell
python main.py collect urls.txt --delay 2
```

Перед запросом к каждому источнику сборщик:

1. загружает и сохраняет актуальный `robots.txt` в
   `data/discovery/<source>/`;
2. проверяет URL для собственного User-Agent;
3. сохраняет каждый полученный ответ как сырой HTML;
4. классифицирует результат: документ, редирект, 404, 429, 5xx, challenge
   или запрет robots.txt;
5. сохраняет разобранный JSON, в том числе ошибку разбора, размеры и
   SHA-256 нормализованного текста.

Скрипт не маскируется под поисковые роботы, не обходит CAPTCHA/WAF и не
скачивает URL, запрещённые `robots.txt`.

## Повторный разбор сохранённых HTML

После изменения парсера не нужно повторно скачивать страницы. Эта команда
повторно разбирает все JSON, у которых есть сохранённый `raw_file`, и не
делает сетевых запросов:

```powershell
python main.py reparse
```

Старый механизм точечного разбора fixture-манифеста также сохранён:

```powershell
python main.py parse-fixtures data/fixtures.tsv
```

На текущих 15 HTML подтверждены специальные селекторы, поэтому семантический
резерв для этих источников не используется: PlayGround —
`div.article-content`, StopGame — `article#material_content`, IXBT.games —
`div[id^="publication-"].prose`, Игромания —
`div[class*="material-content_"]`.

## Статистика

```powershell
python main.py stats
```

Результаты находятся в `results/statistics.json` и
`results/statistics.csv`. В них есть требуемые для ЛР показатели и
диагностика: медиана/минимум/максимум, слова, коэффициент извлечения,
успехи/ошибки разбора и точные дубликаты.

## Текущая структура

```text
data/
  discovery/<source>/robots.txt
  raw/<source>/*.html
  parsed/<source>/*.json
results/
src/parsers/
```

## Тесты

```powershell
python run_tests.py
```

Подробный отчёт находится в `doc/lab1_gaming_report.md`; его LaTeX-версия —
`report_gaming.tex`. Перед массовой выкачкой следует выполнить
проверку 2–5 HTML-образцов от каждого источника и оценить число доступных URL
по sitemap/RSS.
