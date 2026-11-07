# Тесты по лабораторным

Все команды выполняются из корня проекта с активированной `.venv`.
Для C++-проверок ЛР3–ЛР5 и ЛР7 нужен запущенный Docker Desktop. Если тестовый образ ещё
не собран: `docker compose --profile tests build tokenizer-tests`.

## Основные команды

| Работа | Что проверяется | Запуск |
|---|---|---|
| ЛР1 | HTML-парсеры, выделение текста, статистика, выборка корпуса | `python run_tests.py --lab 1` |
| ЛР2 | Робот, sitemap, очереди, повторный обход, парсер, архивы | `python run_tests.py --lab 2` |
| ЛР3 | C++ токенизатор, CLI, демо; Python экспорт и обвязка | `python run_tests.py --lab 3` |
| ЛР4 | C++ лемматизатор, CLI, демо и диагностический поиск; подготовка словарей и метрики | `python run_tests.py --lab 4` |
| ЛР5 | C++ частоты, внешняя сортировка/слияние, модели Ципфа; экспорт типов и подготовка рисунков | `python run_tests.py --lab 5` |
| ЛР7 | C++ булев индекс, списки документов, словарь, чтение и целостность; подготовка URL | `python run_tests.py --lab 7` |
| Все работы | Все группы C++ и Python | `python run_tests.py --lab all` |

Эти команды одинаковы в **Git Bash и PowerShell**. Скрипт сам запускает
Docker и пересобирает C++ перед проверками. Он не запускает обкачку или
замеры на корпусе. Полный морфологический словарь для автотестов не нужен:
GoogleTest создаёт маленькие собственные тестовые словари.

Состав групп и дополнительные варианты:

```bash
python run_tests.py --list
python run_tests.py --lab 4 --cpp-only
python run_tests.py --lab 4 --python-only
```

`--cpp-only` включает GoogleTest и проверки запуска CLI через CTest.
Для ЛР1 и ЛР2 тесты написаны на Python. Без аргументов `python run_tests.py`
сохраняет прежнее поведение: запускает все Python-тесты, без Docker/C++.

## Где находятся тесты

Python-тесты находятся в `tests/python/`, C++-тесты — в `tests/cpp/`,
общие входные данные — в `tests/fixtures/`. Распределение Python-модулей
задано в `PYTHON_SUITES` файла `run_tests.py`; `--list` печатает полный список
для каждой работы. Прямой запуск Python-набора:

```bash
python -m unittest discover -s tests/python -t . -v
```

| Работа | C++ файлы | Метка CTest |
|---|---|---|
| ЛР3 | `tests/cpp/tokenizer_test_lab3.cpp`, `tokenizer_cli_test_lab3.cpp`, `tokenizer_demo_test_lab3.cpp` | `lab3` |
| ЛР4 | `tests/cpp/lemmatizer_test_lab4.cpp` | `lab4` |
| ЛР5 | `tests/cpp/zipf_test_lab5.cpp` | `lab5` |
| ЛР7 | `tests/cpp/boolean_index_test_lab7.cpp` | `lab7` |

## Прямой запуск GoogleTest без Python

После изменения исходников сначала пересобрать тестовые бинарники.
Сборка сохраняется в подключённом каталоге `/build`; команда для Git Bash:

```bash
MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps tokenizer-tests sh -ec 'cmake -S /src -B /build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build /build --target tokenizer_tests lemmatizer_tests zipf_tests boolean_index_tests --parallel 2'
```

В PowerShell удалить префикс `MSYS_NO_PATHCONV=1`. После сборки каждый набор
запускается отдельно, непосредственно через `gtest_main`:

```bash
# ЛР3: 32 GoogleTest токенизатора, CLI и демонстрации
docker compose run --rm --no-deps tokenizer-tests tokenizer_tests --gtest_color=yes

# ЛР4: 30 GoogleTest лемматизатора, CLI, демонстрации и поиска
docker compose run --rm --no-deps tokenizer-tests lemmatizer_tests --gtest_color=yes

# ЛР5: GoogleTest частот, моделей и внешнего слияния
docker compose run --rm --no-deps tokenizer-tests zipf_tests --gtest_color=yes

# ЛР7: GoogleTest булева индекса
docker compose run --rm --no-deps tokenizer-tests boolean_index_tests --gtest_color=yes
```

Эти две команды одинаковы в Git Bash и PowerShell: абсолютных Linux-путей
в аргументах нет. Они запускают уже собранные бинарники и сами не пересобирают код.
Имя сервиса `tokenizer-tests` общее: оно не выбирает лабораторную;
выбор задаётся именем исполняемого файла после сервиса.

Для отдельного теста добавьте, например,
`--gtest_filter=Fixture.ProperNameInflectionsDoNotInventNicknameSynonyms`
к команде ЛР4. Список имён выводит `--gtest_list_tests`.
Две дополнительные процессные проверки каждой работы относятся к CTest,
поэтому при прямом запуске GoogleTest счётчики равны 32 и 30, а не 34 и 32.

На 07.11.2026: ЛР3 — 34 проверки CTest, ЛР4 — 32 проверки CTest
(30 GoogleTest и 2 проверки CLI). Старые журналы с числом 66 содержат
**обе** лабораторные вместе. `run_lab3.py` и `run_lab4.py` теперь тоже
запускают только свою группу C++.

В ЛР2 проверки с настоящей MongoDB по умолчанию пропускаются. Для них
предназначена переменная `MAI_IR_TEST_MONGO_URI`; тесты создают собственные
временные базы. Локальные HTML-образцы для части ЛР1 не включаются в Git:
без них соответствующие тесты также могут иметь статус `skipped`.

## Демонстрация лемматизации в Git Bash

```bash
MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps tokenizer-tests lemmatizer demo --dict /lemmas/ru_en_v1/dictionary.bin
```

В PowerShell та же команда выполняется **без** `MSYS_NO_PATHCONV=1`.
Git Bash иначе преобразует `/lemmas/...` в Windows-путь до передачи Docker,
из-за чего программа сообщает `Cannot open dictionary`. На 07.11.2026
ошибка воспроизведена, вариант с префиксом проверен и работает.

Свой UTF-8 пример из подключённых тестовых файлов:

```bash
MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps tokenizer-tests lemmatizer demo --dict /lemmas/ru_en_v1/dictionary.bin --input /src/tests/fixtures/lab4_demo.txt
```

Для демонстрации нужен настоящий словарь `data/lemmas/ru_en_v1/dictionary.bin`.
Он подключается в контейнер как `/lemmas/ru_en_v1/dictionary.bin`.
Автотесты и демонстрация — разные запуски: успешные тесты не создают
полный словарь. Инструкция подготовки находится в `lab4_run.md`.

## Сохранить журнал ЛР4

```bash
python run_lab4.py --output data/lemmatization/checks_003
```

Выберите **новый** каталог. Без `--snapshot` и `--dict` команда выполняет
только проверки ЛР4 и сохраняет `tests.txt` и `tests_summary.json`.
Проверенный прогон отдельной группы от 07.11.2026 сохранён в
`lab4/verification_grouped_2026-11-07/`: 32 CTest и 17 Python-тестов.
