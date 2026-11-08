# ЛР11: сжатие индекса

## Назначение

Списки ID документов ЛР7 и координаты вхождений ЛР10 хранились записями фиксированной длины по 8 байт.
Сжатая рабочая копия уменьшает также словарь терминов без изменения семантики поиска.
Исходные файлы остаются отдельно для проверки и сравнения. Сырой HTML и диск E не нужны.
Терминологически **словарь терминов** (`terms.bin`, строки терминов в `strings.bin`),
**таблица документов** (`documents.bin`) и **корпус текстов** (`corpus.bin`, HTML)
отличаются от списков ID документов (`postings.bin`). В ЛР11 сжаты **словарь терминов**,
списки ID и координаты (`positions.bin`). Таблица документов и морфологический
словарь ЛР4 (`data/lemmas/.../dictionary.bin`) не сжимались.

Для каждого термина возрастающие ID документов представлены разностями соседних ID.
Координаты представлены разностями порядковых номеров документов и позиций токенов.
Каждое неотрицательное число кодируется **Variable Byte (VB) как на лекции**:
семь информационных бит на байт, младшие группы записываются первыми, старший бит
равен 0 у промежуточных байтов и 1 у последнего. Например, 128 кодируется
байтами `00 81`. **Simple9 (S9) не использовался.**
Поле координаты (заголовок/текст) хранится младшим битом второго числа.
При смене документа или поля позиция отсчитывается заново. Восстановление точное.

Четыре файла числового расширения: `compact_posting_terms.bin` и `compact_position_terms.bin` содержат
смещение и длину сжатого списка каждого термина; `compact_postings.bin` и
`compact_positions.bin` содержат сами переменные записи. Заголовки имеют версию, контрольную сумму
FNV-64 и привязку к исходному булеву и координатному индексам. Читатель не загружает словарь
или списки целиком в память.
Команда `pack` создаёт самостоятельную рабочую копию и добавляет два файла словаря:
`compact_vocabulary_blocks.bin` хранит смещения блоков по 16 терминов, а
`compact_vocabulary.bin` хранит строки с общей длиной префикса и новым суффиксом,
пространство термина и число документов. Таблица исходных `terms.bin` заменяется
80-байтным заголовком. В `strings.bin` остаются только строки метаданных документов;
строки терминов доступны из сжатого словаря. От `postings.bin` и `positions.bin`
также остаются только 80-байтные заголовки. Для поиска не требуются исходные
большие массивы или исходные строки терминов.

## Сборка и тесты

```powershell
cmake -S . -B build/boolean-native -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DBUILD_DEMO=OFF
cmake --build build/boolean-native --parallel 4
python run_tests.py --lab 11 --cpp-only
```

Последняя команда запускает GoogleTest в Docker, плюс проверку справки CLI.
Тесты используют собственный маленький корпус и словарь, поэтому основной корпус для них не нужен.
Проверяются оба пространства терминов, булевы и фразовые запросы, совпадение исходной и сжатой
выдачи, самостоятельная рабочая копия без исходных потоков и строк терминов,
проверка каждой восстановленной строки, полная проверка декодирования,
большие ID, неправильные кодовые слова, усечение,
защита существующего результата и контроль свободного места.

## Построение на полном корпусе

```powershell
New-Item -ItemType Directory -Force data/compact | Out-Null
build/boolean-native/compact_index.exe build --index data/boolean/lab7_2026-11-07/index --positions data/positions/lab10_2026-11-07/positions --output data/compact/new_lab11_vb --reserve-mib 4608
build/boolean-native/compact_index.exe verify --index data/boolean/lab7_2026-11-07/index --positions data/positions/lab10_2026-11-07/positions --compact data/compact/new_lab11_vb
build/boolean-native/compact_index.exe pack --index data/boolean/lab7_2026-11-07/index --positions data/positions/lab10_2026-11-07/positions --compact data/compact/new_lab11_vb --output data/compact/new_lab11_vb_packed --reserve-mib 4608
build/boolean-native/compact_index.exe verify-packed --index data/boolean/lab7_2026-11-07/index --positions data/positions/lab10_2026-11-07/positions --packed data/compact/new_lab11_vb_packed
```

`--output` должен указывать на **новый каталог**. При неудаче остаётся маркер `INCOMPLETE`;
такой результат нельзя открыть для поиска. `verify` проверяет все контрольные суммы,
границы списков и каждое восстановленное значение против исходного индекса.
Резерв свободного места составляет 4608 МиБ. Готовый результат находится в
`data/compact/lab11_vb_2026-11-08`; самостоятельная рабочая копия --- в
`data/compact/lab11_vb_packed_2026-11-08`. `verify-packed` сравнивает с оригиналом
каждую строку словаря, каждый ID, каждую координату и метаданные документов.
Каталоги без `vb` относятся к прежнему несовместимому формату; для текущей
программы используйте каталоги с `vb` или заново выполните `build` и `pack`.

## Демонстрация

Исходный поиск:

```powershell
build/boolean-native/boolean_search.exe --index data/boolean/lab7_2026-11-07/index --positions data/positions/lab10_2026-11-07/positions --query '"god of war"' --limit 5
```

Поиск по самостоятельной сжатой копии:

```powershell
build/boolean-native/boolean_search.exe --index data/compact/lab11_vb_packed_2026-11-08/index --positions data/compact/lab11_vb_packed_2026-11-08/positions --compact data/compact/lab11_vb_packed_2026-11-08/compact --query '"god of war"' --limit 5
```

Режим лемм добавляется к любой команде:

```powershell
--space lemma --dict data/lemmas/ru_en_v1/dictionary.bin
```

Веб-форма с тем же движком:

```powershell
python serve_search.py --index data/compact/lab11_vb_packed_2026-11-08/index --positions data/compact/lab11_vb_packed_2026-11-08/positions --compact data/compact/lab11_vb_packed_2026-11-08/compact --dict data/lemmas/ru_en_v1/dictionary.bin
```

Открыть <http://127.0.0.1:8080/>. Полное сравнение на настоящем индексе и запись
замеров в `doc/lab11/`:

```powershell
python run_lab11.py --index data/boolean/lab7_2026-11-07/index --positions data/positions/lab10_2026-11-07/positions --compact data/compact/lab11_vb_2026-11-08 --packed data/compact/lab11_vb_packed_2026-11-08 --dict data/lemmas/ru_en_v1/dictionary.bin
```

В Git Bash для команд `docker compose run` нужен префикс `MSYS_NO_PATHCONV=1`.
В Docker сжатая рабочая копия доступна по пути `/compact/lab11_vb_packed_2026-11-08`.

## Отдельные замеры чтения и декодирования

После сборки нативных утилит:

```powershell
python benchmark_lab11.py --index data/boolean/lab7_2026-11-07/index --positions data/positions/lab10_2026-11-07/positions --compact data/compact/lab11_vb_2026-11-08 --packed data/compact/lab11_vb_packed_2026-11-08 --dict data/lemmas/ru_en_v1/dictionary.bin
```

Результаты сохраняются в `doc/lab11/profile/`: исходные JSONL, медианы и таблицы
для отчёта. Скрипт измеряет постинги и координаты `god`, `of`, `war` в обоих
пространствах, а затем полный поиск четырёх булевых и фразовых запросов.
По умолчанию выполняется семь повторений после прогрева. Сравнение значений
и контрольных сумм выполняется вне измеряемого участка.

Нативный режим для отдельного термина:

```powershell
build/boolean-native/compact_index.exe benchmark --index data/boolean/lab7_2026-11-07/index --positions data/positions/lab10_2026-11-07/positions --compact data/compact/lab11_vb_2026-11-08 --term of --space surface --repeats 7
```

Он отдельно измеряет `seek + fread` в готовый буфер, декодирование из памяти
с восстановлением значений и контрольной суммой, полный проход штатного
файлового курсора. VB-декодер для памяти и файла использует общий алгоритм.
Буферы выделяются и заполняются перед замерами, порядок исходного/VB вариантов
чередуется. Для диагностики загружается один список за раз (не весь индекс);
каждый входной буфер ограничен 512 МиБ. Кэш ОС не очищается.
Полный проход списка и фразовый поиск — разные нагрузки; времена проходов
нельзя складывать или вычитать из времени запроса.

## Ограничения

Исходные списки сохранены в отдельном каталоге как эталон. При хранении сразу
обеих версий общий расход диска растёт; измеренная экономия относится к одной
самостоятельной рабочей копии. Проверка `verify` и создание `pack` требуют исходную
версию, тогда как поиск по готовому `pack` работает без её потоков постингов и координат.
Документные метаданные и морфологический словарь не сжимаются. Прыжки по спискам
и блоковое декодирование относятся к следующему этапу ускорения.
