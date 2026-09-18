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

`urls.txt` содержит по две страницы каждого источника. Формат строки:

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

## Разбор уже сохранённых образцов

Для повторяемой проверки HTML без нового сетевого обращения:

```powershell
python main.py parse-fixtures data/fixtures.tsv
```

В манифесте используются строки `source<TAB>url<TAB>raw_file`. В репозитории
есть две реальные страницы StopGame; они служат закреплёнными образцами
разметки. На них подтверждён селектор основного текста
`article#material_content` и JSON-LD `NewsArticle`.

Для PlayGround, IXBT.games и Игромании пока используется только осторожный
семантический резервный алгоритм (`article`, `itemprop=articleBody`, `main`).
После сохранения их HTML-образцов его результат нужно сверить с видимой
страницей, а затем заменить на подтверждённые селекторы. Такой режим отмечен
в поле `metadata.parser_mode`, чтобы не спутать его с валидированным парсером.

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
