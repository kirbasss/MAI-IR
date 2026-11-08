# MAI-IR

Учебный проект по разработке поисковой системы в рамках курса
«Информационный поиск» МАИ.

Корпус документов составляют русскоязычные и англоязычные публикации о компьютерных играх:
новости, статьи, обзоры и блоги игровых изданий.

Тесты: `tests/cpp/` — C++/GoogleTest, `tests/python/` — Python,
`tests/fixtures/` — общие примеры. Запуск по работе:
`python run_tests.py --lab 4`. Все команды описаны в [doc/testing.md](doc/testing.md).

ЛР5: [запуск](doc/lab5_run.md), [результаты и графики](doc/lab5/README.md),
[отчёт PDF](doc/report_lab5.pdf). Подсчёт частот и внешнее слияние — на C++;
Python используется для экспорта и рисунков.
Отдельное [сравнение словоформ и лемматизации](doc/lab5/comparison/README.md)
выполнено на том же снимке корпуса с сохранением числа вхождений.

ЛР7: [построение и чтение булева индекса](doc/lab7_run.md),
[результаты](doc/lab7/README.md), [отчёт PDF](doc/report_lab7.pdf).
Прямые GoogleTest: `docker compose run --rm --no-deps tokenizer-tests boolean_index_tests`.

ЛР8: [булев поиск и веб-форма](doc/lab8_run.md), [результаты запросов](doc/lab8/README.md),
[отчёт PDF](doc/report_lab8.pdf). Прямые GoogleTest:
`docker compose run --rm --no-deps tokenizer-tests boolean_search_tests`.

ЛР10: [координаты слов и цитатный поиск](doc/lab10_run.md),
[результаты](doc/lab10/README.md), [отчёт PDF](doc/report_lab10.pdf).
ЛР11: [сжатие индекса](doc/lab11_run.md),
[измерения](doc/lab11/README.md), [отчёт PDF](doc/report_lab11.pdf).
ЛР12: [ускорение и прыжки по индексу](doc/lab12_run.md),
[замеры трёх режимов](doc/lab12/README.md), [отчёт PDF](doc/report_lab12.pdf).
ЛР13: [TF-IDF и ранжирование](doc/lab13_run.md),
[замеры и примеры](doc/lab13/README.md), [отчёт PDF](doc/report_lab13.pdf).
Прямые GoogleTest: `docker compose run --rm --no-deps tokenizer-tests phrase_search_tests`.
