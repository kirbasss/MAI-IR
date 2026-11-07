// 1. Очередь, разобранные документы и сырой HTML — разные коллекции.
use('mai_ir');
({
  queue: db.frontier.countDocuments({}),
  pending: db.frontier.countDocuments({ state: 'pending' }),
  processed: db.documents.countDocuments({}),
  rawPages: db.raw_pages.countDocuments({})
});

// 2. Быстрый просмотр разобранных документов без большого поля html.
use('mai_ir');
db.documents.find(
  { indexable: { $ne: false } },
  { _id: 0, url: 1, source: 1, fetched_at: 1, raw_page_id: 1,
    'parsed.title': 1, 'parsed.category': 1, 'parsed.author': 1, 'parsed.text': 1 }
).limit(2);

// 3. У сырой страницы есть HTML или ссылка на проверенный архив на внешнем диске.
use('mai_ir');
db.raw_pages.find(
  {},
  { _id: 0, url: 1, source: 1, fetched_at: 1, content_sha256: 1,
    parse_state: 1, raw_storage: 1, raw_archive: 1 }
).limit(2);

// Готовые архивные пачки (HTML здесь нет).
use('mai_ir');
db.raw_archives.find({}, { directory: 1, records: 1, compressed_bytes: 1, status: 1, verified_at: 1 });

// 4. Связь разобранного документа с его сырой страницей по URL и хешу.
use('mai_ir');
const document = db.documents.findOne(
  { raw_page_id: { $exists: true } },
  { _id: 0, url: 1, raw_page_id: 1, content_sha256: 1, 'parsed.title': 1 }
);
const rawPage = document && db.raw_pages.findOne(
  { _id: document.raw_page_id },
  { _id: 0, url: 1, content_sha256: 1 }
);
({ document, rawPage, hashesMatch: Boolean(rawPage && document.content_sha256 === rawPage.content_sha256) });

// 5. Сколько публикаций уже загружено с каждого сайта.
use('mai_ir');
db.documents.aggregate([
  { $match: { indexable: { $ne: false } } },
  { $group: { _id: '$source', documents: { $sum: 1 } } },
  { $sort: { documents: -1 } }
]);

// 6. Примеры URL, которые требуют внимания (ошибка, запрет, удаление).
use('mai_ir');
db.frontier.find(
  { state: { $in: ['retry', 'blocked', 'gone'] } },
  { _id: 0, url: 1, source: 1, state: 1, attempts: 1,
    last_error: 1, available_at: 1 }
).limit(5);

// 7. Проверка качества разбора: ошибки парсера и пустые тексты.
use('mai_ir');
({
  parseErrors: db.documents.countDocuments({ 'parsed.parse_error': { $type: 'string' } }),
  // Старые parse_error у исключённых каталогов остаются как диагностика.
  // Здесь считаем только публикации, которым ещё нужен разбор, не перезагрузка.
  unresolvedParseErrors: db.documents.countDocuments({
    'parsed.parse_error': { $type: 'string' },
    exclusion_reason: { $exists: false }, response_error: { $exists: false }
  }),
  awaitingRefetch: db.documents.countDocuments({ response_error: { $exists: true } }),
  emptyTexts: db.documents.countDocuments({ 'parsed.text': '' }),
  indexable: db.documents.countDocuments({ indexable: { $ne: false } }),
  titleOnly: db.documents.countDocuments({ indexable: { $ne: false }, 'parsed.text': '' }),
  excluded: db.documents.countDocuments({ indexable: false })
});

// 8. Очередь офлайн-парсинга живёт в raw_pages. Сначала запустить parse_worker.py:
// он создаёт индекс. hint не даёт агрегату читать десятки ГБ сырого HTML.
use('mai_ir');
db.raw_pages.aggregate([
  { $group: { _id: '$parse_state', pages: { $sum: 1 } } },
  { $sort: { pages: -1 } }
], { hint: { parse_state: 1, parse_available_at: 1, fetched_at: 1, _id: 1 }, maxTimeMS: 10000 });

// 9. Ошибки парсинга не являются поводом заново скачивать страницу.
use('mai_ir');
db.raw_pages.find(
  { parse_state: 'error' },
  { _id: 0, url: 1, source: 1, parse_error: 1, parse_attempts: 1, raw_generation: 1 }
).limit(5);

// 10. Измеренные стадии; это примеры по URL, не хронологический список.
// Ограничиваем время: до появления первых timings фильтр может просмотреть очередь.
use('mai_ir');
db.frontier.find(
  { 'timings_ms.total_ms': { $exists: true } },
  { _id: 0, url: 1, source: 1, state: 1, timings_ms: 1 }
).sort({ _id: 1 }).limit(5).maxTimeMS(5000);
