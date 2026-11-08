"""Small fixtures model both downloaded GameMAG layouts without a network dependency."""
from __future__ import annotations

import unittest

from src.parsers.gamemag import LEGACY_BODY, NEWS_BODY, REVIEW_BODY, parse_gamemag


class GameMAGParserTests(unittest.TestCase):
    def parse(self, body, path="news/123/example"):
        return parse_gamemag(body, "https://gamemag.ru/" + path)

    def test_legacy_blocks_and_inline_links_keep_order(self):
        parsed = self.parse('<h1>Игра</h1><div class="content-text"><p>Первая <a>новость</a>.</p></div>'
                            '<div class="content-text"><p>Вторая часть.</p></div>')
        self.assertIsNone(parsed.parse_error)
        self.assertEqual(parsed.metadata["body_selector"], LEGACY_BODY)
        self.assertEqual(parsed.metadata["body_blocks"], 2)
        self.assertLess(parsed.text.index("Первая"), parsed.text.index("Вторая"))

    def test_new_news_plain_text_and_paragraphs_exclude_widgets_and_sidebar(self):
        parsed = self.parse('''
        <meta property="article:tag" content="Kali">
        <h1>Kali больше нет</h1><article class="news-details">
          <div class="overview"><p>SUMMARY_NOISE</p></div>
          <aside class="sidebar-content__inner">SIDEBAR_NOISE</aside>
          <div class="content-row__content">Создатель Kali объявил о закрытии проекта.
            <p>Служба поддерживала <strong>Warcraft 2</strong>.</p>
            <script>SCRIPT_NOISE</script><style>STYLE_NOISE</style>
            <div class="news-details__games">GAMES_NOISE</div>
            <div class="list-tags"><a>TAGS_NOISE</a></div>
            <div class="subscription-channel">SUBSCRIPTION_NOISE</div>
            <div class="news-details__emotion-rating"><section class="emotion-rating">RATING_NOISE</section></div>
          </div>
        </article><div class="content-row__center--lg js-comment-section">COMMENTS_NOISE</div>
        ''')
        self.assertIsNone(parsed.parse_error)
        self.assertIn("Создатель Kali", parsed.text)
        self.assertIn("Warcraft 2", parsed.text)
        self.assertNotIn("NOISE", parsed.text)
        self.assertEqual(parsed.tags, ["Kali"])
        self.assertEqual(parsed.metadata["body_selector"], NEWS_BODY)

    def test_specials_keep_article_category_and_scoped_body(self):
        parsed = self.parse('<h1>Юбилей</h1><article class="news-details">'
                            '<div class="content-row__content"><p>История портала.</p></div></article>',
                            "specials/1/history")
        self.assertEqual(parsed.category, "article")
        self.assertEqual(parsed.text, "История портала.")
        self.assertIsNone(parsed.parse_error)

    def test_review_preserves_sections_and_final_prose_without_ratings(self):
        parsed = self.parse('''<h1>Обзор</h1><article class="reviews-details">
          <div class="overview">SUMMARY_NOISE</div>
          <div class="content-row__section"><p>Вступление.</p></div>
          <aside>SIDEBAR_NOISE</aside>
          <div class="content-row__section"><p>Основная часть.</p></div>
          <div class="content-row__center content-row__center--lg">
            <p>Заключение.</p>
            <div class="visual-overview">VISUAL_NOISE</div>
            <div class="reviews-details__score">SCORE_NOISE</div>
            <section class="emotion-rating">EMOTIONS_NOISE</section>
          </div>
          <div class="content-row__center--lg js-comment-section">INNER_COMMENTS_NOISE</div>
        </article><div class="content-row__center--lg">OUTER_COMMENTS_NOISE</div>''', "reviews/example")
        self.assertIsNone(parsed.parse_error)
        self.assertEqual(parsed.category, "review")
        self.assertEqual(parsed.metadata["body_selector"], REVIEW_BODY)
        self.assertEqual(parsed.metadata["body_blocks"], 3)
        self.assertLess(parsed.text.index("Вступление"), parsed.text.index("Основная"))
        self.assertLess(parsed.text.index("Основная"), parsed.text.index("Заключение"))
        self.assertNotIn("NOISE", parsed.text)

    def test_nested_review_matches_do_not_duplicate_text(self):
        parsed = self.parse('''<h1>Review</h1><article class="reviews-details">
          <div class="content-row__center--lg"><div class="content-row__section">
            <p>Unique paragraph.</p></div><p>Final paragraph.</p></div></article>''', "reviews/example")
        self.assertIsNone(parsed.parse_error)
        self.assertEqual(parsed.text.count("Unique paragraph"), 1)
        self.assertEqual(parsed.metadata["body_blocks"], 1)

    def test_media_only_page_checks_cleaned_body_not_service_thumbnails(self):
        for media in ('<img src="screen.jpg">', '<iframe src="video"></iframe>'):
            with self.subTest(media=media):
                parsed = self.parse('<h1>Видео</h1><article class="news-details">'
                                    '<div class="content-row__content">' + media +
                                    '<div class="subscription-channel">NOISE</div></div></article>')
                self.assertEqual(parsed.text, "")
                self.assertIsNone(parsed.parse_error)
                self.assertEqual(parsed.metadata["content_kind"], "media_only")
        empty = self.parse('''<h1>Empty</h1><article class="news-details">
          <div class="content-row__content"><div class="news-details__games"><img src="service.jpg">NOISE</div>
          <section class="emotion-rating">NOISE</section></div></article>''')
        self.assertTrue(empty.parse_error)
        self.assertEqual(empty.text, "")
        self.assertNotIn("content_kind", empty.metadata)

    def test_generic_layout_column_outside_an_article_is_not_a_body(self):
        parsed = self.parse('<h1>Category</h1><div class="content-row__content">Navigation only.</div>')
        self.assertTrue(parsed.parse_error)
        self.assertEqual(parsed.text, "")

    def test_legacy_and_modern_variants_are_not_concatenated(self):
        parsed = self.parse('<h1>Story</h1><div class="content-text"><p>Article text.</p></div>'
                            '<article class="news-details"><div class="content-row__content">Article text.</div></article>')
        self.assertEqual(parsed.text.count("Article text"), 1)
        self.assertEqual(parsed.metadata["body_selector"], LEGACY_BODY)

    def test_empty_legacy_placeholder_can_fall_back_to_new_body(self):
        parsed = self.parse('<h1>Story</h1><div class="content-text"></div><article class="news-details">'
                            '<div class="content-row__content">Actual body.</div></article>')
        self.assertIsNone(parsed.parse_error)
        self.assertEqual(parsed.text, "Actual body.")
        self.assertEqual(parsed.metadata["body_selector"], NEWS_BODY)

    def test_metadata_still_comes_from_structured_data(self):
        parsed = self.parse('''<script type="application/ld+json">{
          "@type":"NewsArticle", "headline":"Headline", "datePublished":"2025-06-07",
          "author":{"name":"Автор"}, "articleSection":"Индустрия", "keywords":["PS5","Death Stranding"]
        }</script><article class="news-details"><div class="content-row__content">Текст публикации.</div></article>''')
        self.assertIsNone(parsed.parse_error)
        self.assertEqual(parsed.title, "Headline")
        self.assertEqual(parsed.author, "Автор")
        self.assertEqual(parsed.published_at, "2025-06-07")
        self.assertEqual(parsed.tags, ["PS5", "Death Stranding"])
        self.assertEqual(parsed.metadata["source_category"], "Индустрия")


if __name__ == "__main__":
    unittest.main()
