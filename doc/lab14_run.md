# ЛР14: сниппеты поисковой выдачи

В файле `doc/lesson-11. Сниппеты.pdf` внутри указано «Урок №10».
Для реализации использована схема со слайдов 25, 34–43: предложения
с терминами запроса становятся кандидатами, оцениваются по числу разных
терминов и компактности их вхождений, затем лучший фрагмент обрезается.

## Сборка и запуск

Команды ниже выполняются из корня проекта в PowerShell или Git Bash.
Для C++ нужны CMake и MinGW; для тестов также Docker Desktop и Python
с пакетами из `requirements-dev.txt`.

```bash
cmake -S . -B build/boolean-native -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DBUILD_DEMO=OFF
cmake --build build/boolean-native --parallel 4
python run_tests.py --lab 14
```

Снимок `data/zipf/run_2026-11-07/snapshot/corpus.bin` содержит исходные
тексты MAIIR3 и соответствует индексам ЛР7–ЛР13. Один раз подготовить
смещения к текстам по ID (19 153 352 байта для 478 833 документов):

```bash
mkdir -p data/snippets
build/boolean-native/boolean_search.exe --index data/boolean/lab7_2026-11-07/index --corpus data/zipf/run_2026-11-07/snapshot/corpus.bin --build-snippet-index data/snippets/lab14_offsets.bin
```

В PowerShell вместо `mkdir -p` используйте
`New-Item -ItemType Directory -Force data/snippets`.
Готовый файл локальный и не входит в Git. Повторно строить его при каждом
запросе не нужно. Без `--snippet-index` CLI тоже работает, но просматривает
заголовки всего снимка при открытии.

Пример для TF-IDF; одна строка запроса на stdin также поддерживается:

```bash
build/boolean-native/boolean_search.exe --index data/compact/lab11_vb_packed_2026-11-08/index --positions data/compact/lab11_vb_packed_2026-11-08/positions --compact data/compact/lab11_vb_packed_2026-11-08/compact --ranking tfidf --norms data/tfidf/lab13_2026-11-08 --corpus data/zipf/run_2026-11-07/snapshot/corpus.bin --snippet-index data/snippets/lab14_offsets.bin --query "doom eternal" --limit 5
```

Булев режим: замените `--ranking tfidf --norms ...` на
`--query "doom AND eternal"`. Режим лемм: добавьте
`--space lemma --dict data/lemmas/ru_en_v1/dictionary.bin`.
При `--corpus` каждый результат содержит `snippet`, `snippet_matches`
(диапазоны UTF-8 байтов), `snippet_left`, `snippet_right`. Исходные
поля выдачи и порядок документов не меняются.

Веб-форма:

```bash
python serve_search.py --index data/compact/lab11_vb_packed_2026-11-08/index --positions data/compact/lab11_vb_packed_2026-11-08/positions --compact data/compact/lab11_vb_packed_2026-11-08/compact --dict data/lemmas/ru_en_v1/dictionary.bin --norms data/tfidf/lab13_2026-11-08 --corpus data/zipf/run_2026-11-07/snapshot/corpus.bin --snippet-index data/snippets/lab14_offsets.bin
```

Откройте <http://127.0.0.1:8080/>. В HTML совпадения подсвечены через
`<mark>` после экранирования текста. Для повторения примеров на полном
снимке: `python run_lab14.py`; результаты сохраняются в `doc/lab14/`.

## Проверка

| Случай | Ожидаемый результат |
|---|---|
| Несколько предложений с разными терминами | Выбирается предложение с большим покрытием; добавляется контекст того же абзаца |
| Русский UTF-8 | Диапазоны подсветки соответствуют байтам исходного фрагмента |
| Прямое чтение и файл смещений | Одинаковые сниппеты и порядок выдачи |
| Повреждённый файл смещений | Ошибка контрольной суммы до обработки запроса |
| Документ без текста | Пустой фрагмент без ошибки поиска |
| HTML в документе | Теги экранируются, подсвечиваются только слова |

`python run_tests.py --lab 14` запускает 6 GoogleTest и 3 Python unittest.
Общая регрессия всех лабораторных запускается
`python run_lab17.py --output doc/lab14/full`: этот сценарий сам поднимает отдельную тестовую
MongoDB для интеграционных тестов.

## Границы метода

Сниппет показывает фрагмент уже найденного документа и не меняет TF-IDF
или булеву выдачу. Если в снимке сохранён только заголовок, текстового
фрагмента нет. Границы предложений определяются по `.`, `!`, `?` и переводу
строки; сокращения и нестандартная пунктуация могут разделяться неточно.
Фрагмент включает соседние предложения того же абзаца и ограничен 600 байтами
на границе UTF-8. В длинном абзаце перед первым совпадением по возможности
сохраняется до 200 байт контекста. Соседние абзацы не добавляются, чтобы
не захватывать блоки «Читайте также» и другие рекомендации. Файл смещений привязан
к размеру снимка, числу документов и контрольной сумме; заголовок каждого
показанного документа дополнительно сверяется с поисковым индексом.
