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
  {},
  { _id: 0, url: 1, source: 1, fetched_at: 1, raw_page_id: 1,
    'parsed.title': 1, 'parsed.category': 1, 'parsed.author': 1, 'parsed.text': 1 }
).limit(2);

// 3. У сырой страницы есть URL, источник, время загрузки и HTML.
use('mai_ir');
db.raw_pages.find(
  {},
  { _id: 0, url: 1, source: 1, fetched_at: 1, content_sha256: 1, html: 1 }
).limit(2);

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
  emptyTexts: db.documents.countDocuments({ 'parsed.text': '' })
});
