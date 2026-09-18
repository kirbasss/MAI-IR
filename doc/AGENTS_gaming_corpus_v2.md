# AGENTS.md

## Project

This repository contains laboratory work for the MAI course
**Information Retrieval (Информационный поиск)**.

The long-term goal is to build an information retrieval system incrementally
across multiple laboratory assignments.

The current work is **Lab 1 — Corpus acquisition and analysis
(Добыча корпуса документов)**.

---

## Corpus theme

The corpus theme has been changed from song lyrics to:

> **Russian-language publications about video games and the gaming industry:
> news, articles, reviews, previews and analytical materials.**

This theme is preferable for the course because documents are longer,
contain richer natural-language text, and have useful metadata.

Target corpus size: **more than 1,000,000 documents** if feasible.

The corpus should be multi-source. Do not assume that any one source must
provide one million documents.

Current candidate sources:

1. PlayGround.ru
2. StopGame.ru
3. IXBT.games
4. Igromania.ru

Additional gaming-media sources may be added later if URL discovery shows
that the first four sources are insufficient.

---

## Current source status

### PlayGround.ru

Currently the strongest initial crawling candidate.

Verified current `robots.txt` behavior:

- `User-agent: *` is not globally blocked.
- Ordinary news/article pages are not globally disallowed.
- Service and duplicate-producing paths are restricted, including examples
  such as `/search/`, `/download/`, `/account/`, `/api/`, add/edit/upload
  endpoints and several query-parameter variants.
- Sitemap is declared:
  `https://www.playground.ru/sitemap.xml`

The crawler must obey the actual current robots.txt at runtime rather than
hard-coding these observations forever.

### StopGame.ru

Candidate source.

External checks indicate:

- robots.txt exists;
- the site is indexable;
- sitemap exists at `https://stopgame.ru/sitemap.xml`.

The exact current robots.txt contents have not yet been captured locally.
Before large-scale crawling, fetch and save the current robots.txt from the
student's machine and verify the article/news paths explicitly.

Useful discovery mechanisms may include the sitemap and public RSS feeds.

### IXBT.games

Candidate source.

External checks indicate that the site is indexable and that the general
`User-agent: *` group is not fully blocked.

The exact current robots.txt contents have not yet been captured locally.
Verify it explicitly before large-scale crawling.

The site has clear sections such as news, reviews and articles.

### Igromania.ru

Verified as a strong crawling candidate.

Current `robots.txt` rules for `User-agent: *`:

Explicitly allowed:

- `/*?years=`
- `/*?page=`
- `?p=`
- `*.css`
- `*.js`
- common image/font assets

Disallowed service paths:

- `/sse/`
- `/accounts/`
- `/flowplayer/`
- `/video_converter/`
- `/search/`
- `/api/`
- `/select2/`
- `/autocomplete/`
- `/oembed`
- `/oauth/`

There is no global `Disallow: /` for ordinary crawlers, so regular public
content pages are crawlable subject to the specific exclusions above.

Other notable rules:

- `GPTBot` -> `Allow: /`
- `ClaudeBot` -> `Allow: /`
- `Bytespider` -> `Disallow: /`
- Yandex News-specific RSS paths are explicitly allowed.

Declared clean parameters:

`PreferDesktop&cpage&preview&date&ondate&clear_cache&type&ID&nw&v`

These parameters should be considered during URL canonicalization/deduplication.

Declared sitemap:

`https://www.igromania.ru/sitemap.xml`

The sitemap is a sitemap index and currently exposes these groups:

- `sitemap-tags.xml`
- `sitemap-reviews.xml`
- `sitemap-articles.xml`
- `sitemap-articles.xml?p=2`
- `sitemap-news.xml`
- `sitemap-news.xml?p=2` through `?p=17`
- `sitemap-rubrics.xml`
- `sitemap-games.xml`
- `sitemap-games.xml?p=2` through `?p=39`
- `sitemap-game-reviews.xml`
- `sitemap-game-news.xml`
- `sitemap-game-news.xml?p=2`
- `sitemap-game-articles.xml`
- `sitemap-games-calendar.xml`
- `sitemap-google-news.xml`

For the corpus, prioritize content-bearing publication sitemaps:

- news;
- articles;
- reviews;
- game-news;
- game-articles;
- game-reviews.

Treat `games`, `tags`, `rubrics`, and calendar pages primarily as navigation
or metadata sources unless manual inspection shows that they contain substantial
article-like text suitable for the corpus.

For Lab 1 and initial crawling, Igromania should be considered one of the
primary sources alongside PlayGround, StopGame and IXBT.games.

---

## Important robots.txt rule

The crawler must check robots.txt before crawling a source.

Do not:

- spoof Googlebot/Bingbot or other named crawlers;
- bypass CAPTCHA or WAF restrictions;
- crawl paths disallowed for our own user agent;
- treat a CAPTCHA/challenge page as a valid article.

At runtime classify responses into at least:

- valid content page;
- redirect;
- 404/not found;
- 429/rate limited;
- 5xx/server error;
- anti-bot/CAPTCHA/challenge;
- disallowed by robots.txt.

Preserve useful diagnostics.

---

## Course constraints

Keep these requirements in mind for all future work.

- All text input/output must use UTF-8.
- Final corpus must use at least two independent sources.
- Target corpus size is >1,000,000 documents if feasible.
- For later labs, core search/index structures must be implemented in
  C or C++ without STL where required by the course.
- Python may be used for crawling, corpus preparation, HTML parsing,
  experiments, statistics, tests and auxiliary scripts.
- Search-index data structures in later labs must be implemented manually
  rather than replaced by equivalent library components.
- Design later indexing stages for corpora that do not fit fully in RAM.
- CLI tools are required for reproducible runs and test dumps.
- UI/search assignments will eventually need both CLI and web interfaces.
- Each programming lab should eventually have:
  - a test plan;
  - automated tests;
  - reproducible build/run scripts;
  - clearly separated test cases;
  - quantitative results;
  - critical analysis of limitations.

Do not prematurely force Lab 1 into C/C++. Lab 1 is intentionally Python.

---

## Lab 1 assignment

Lab 1 requires:

1. Download example documents locally.
2. State the data sources in the report.
3. Use at least two sources in the final corpus.
4. Study raw document format and metadata.
5. Identify markup containing useful text.
6. Extract the text.
7. Find existing search engines for the selected document collection.
8. Run example queries.
9. Describe shortcomings of existing search results.

Required statistics:

- total raw-document size;
- number of documents;
- total extracted-text size;
- average raw-document size;
- average extracted-text size.

Additional diagnostics we want:

- min/max/median text size;
- total/average/median/min/max word count;
- successful parses;
- parse failures;
- extraction ratio;
- SHA-256 of normalized article text;
- unique-text count;
- duplicate count.

---

## Unified document model

All sources should map to a common representation.

Conceptually:

```json
{
  "source": "playground",
  "url": "...",
  "title": "...",
  "author": "...",
  "published_at": "...",
  "category": "news",
  "tags": ["..."],
  "games": ["..."],
  "text": "...",
  "metadata": {},
  "parse_error": null,
  "http_status": 200,
  "raw_size_bytes": 123456,
  "text_size_bytes": 9876,
  "text_sha256": "..."
}
```

Not every source exposes every field.

Never fabricate missing metadata.

Possible document classes:

- news;
- article;
- review;
- preview;
- analysis.

Keep `category` normalized while preserving the original source category
in metadata when useful.

---

## Lab 1 architecture

Recommended structure:

```text
mai_ir_lab01/
├── AGENTS.md
├── README.md
├── urls.txt
├── requirements.txt
├── main.py
├── ir_lab/
│   ├── downloader.py
│   ├── model.py
│   ├── statistics.py
│   ├── utils.py
│   └── parsers/
│       ├── playground.py
│       ├── stopgame.py
│       ├── ixbt_games.py
│       └── igromania.py
├── data/
│   ├── raw/
│   └── parsed/
└── results/
```

Do not write source-specific selectors from guesses.

First save real HTML examples, inspect them, then implement selectors based
on evidence.

---

## Immediate Lab 1 workflow

Do NOT start a million-page crawl yet.

### Phase 1 — source validation

For each candidate source:

1. Save its current `robots.txt`.
2. Save sitemap/RSS URLs where available.
3. Select 2–5 representative publications.
4. Download their raw HTML.
5. Verify that ordinary HTTP downloading returns the actual article rather
   than a CAPTCHA/challenge.
6. Inspect HTML markup.
7. Identify selectors or structured data for:
   - title;
   - author;
   - publication date;
   - category;
   - tags;
   - related game(s), if present;
   - article body.

### Phase 2 — parser validation

For each source:

1. Parse the selected examples.
2. Compare extracted text against the visible article.
3. Check that navigation, ads, comments, recommendations and unrelated
   blocks are not included.
4. Record parser failures explicitly.
5. Add tests using saved HTML fixtures.

### Phase 3 — small sample

After validation, collect roughly 20–50 documents per source.

Run statistics and compare:

- parse success rate;
- raw/text sizes;
- average word count;
- extraction ratio;
- duplicate rate.

### Phase 4 — URL-volume estimation

Before a large crawl, use allowed discovery mechanisms such as:

- sitemap indexes;
- sitemap files;
- archive pages;
- RSS feeds;
- internal pagination/links where allowed.

Count discoverable candidate article URLs without downloading every article.

Produce a table such as:

```text
Source          Candidate URLs
--------------------------------
PlayGround      ...
StopGame        ...
IXBT.games      ...
Igromania       ...
--------------------------------
Total           ...
```

Only then decide whether more sources are necessary to exceed one million
documents.

---

## Search experiments for Lab 1

Existing site search and general web search can be compared.

Useful query categories:

1. exact game title;
2. game title + event/topic;
3. exact phrase from an article;
4. old/obscure game;
5. typo;
6. broad query;
7. synonymous wording;
8. same event reported by different outlets.

Possible examples:

- `gta 6 перенос`
- `elden ring дополнение`
- `сталкер системные требования`
- `"искусственный интеллект в разработке игр"`
- `ведьмак обзор`

Record:

- whether the expected document is found;
- result position;
- relevance of top results;
- behavior on typos;
- freshness bias;
- popularity bias;
- duplicate/near-duplicate results;
- differences between site search and Google `site:` search.

Do not invent results; record actual observations.

---

## Duplicate and near-duplicate considerations

Gaming news is often based on the same underlying announcement and may be
republished or rewritten across multiple outlets.

For Lab 1:

- exact duplicates can initially be detected with SHA-256 of normalized text;
- preserve source and URL;
- do not delete evidence needed for analysis.

Later labs may explore near-duplicate detection separately.

---

## Coding style

- Python 3.11+.
- Use type hints.
- Keep source-specific parsing isolated in `ir_lab/parsers/`.
- Keep downloading separate from parsing.
- Keep statistics source-independent.
- Prefer small testable functions.
- Preserve raw HTML fixtures.
- Do not silently swallow parser failures.
- Never fabricate metadata.
- Avoid unnecessary dependencies.
- Prefer standard library + `requests`, `beautifulsoup4`, `lxml`.
- Keep code simple enough to explain during an oral university defense.

When behavior changes, update README.

---

## What Codex should optimize for

Priority order:

1. correctness;
2. compliance with robots.txt and site restrictions;
3. reproducibility;
4. explainability during the lab defense;
5. robustness to HTML changes and malformed pages;
6. useful diagnostics;
7. reasonable performance.

Avoid overengineering Lab 1.
