# MAI-IR

Учебная поисковая система по курсу «Информационный поиск» МАИ. Корпус состоит
из русскоязычных и англоязычных публикаций о компьютерных играх.

## Состав репозитория

- [`doc/report_all.pdf`](doc/report_all.pdf) — итоговый отчёт по 13 лабораторным работам.
- [`doc/course_info.md`](doc/course_info.md) и `doc/homework_*.md` — условия курса и задания.
- `cpp/`, `src/`, `tests/` — поисковый движок, сбор корпуса и автотесты.
- `run.py` — общая точка запуска. Скрипты `run_lab*.py` нужны для воспроизведения
  отдельных экспериментов и используются частью тестов.

## Запуск

```bash
python -m pip install -r requirements.txt
python run.py search --help
python run.py crawl --help
python run.py test --help
```

Для поиска нужны локально построенные индексы и снимок корпуса: большие данные
не хранятся в Git. Пути к ним передаются через параметры `search`. Для C++
тестов нужны Docker и Compose; Python тесты запускаются через
`python run.py test --python-only`.
