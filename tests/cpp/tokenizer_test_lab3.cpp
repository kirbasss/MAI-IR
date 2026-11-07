#include "tokenizer.hpp"

#include <gtest/gtest.h>
#include <nlohmann/json.hpp>

#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

struct OwnedToken {
    std::string term;
    std::uint64_t position;
    std::size_t begin, end, characters;
};

std::vector<OwnedToken> collect(std::string_view input) {
    std::vector<OwnedToken> result;
    gaming::tokenize(input, [&](const gaming::Token& token) {
        // Token.term is borrowed. Copy it BEFORE the callback returns.
        result.push_back({std::string(token.term), token.position, token.begin_byte,
                          token.end_byte, token.characters});
    });
    return result;
}

std::vector<std::string> terms(std::string_view input) {
    std::vector<std::string> result;
    for (const auto& token : collect(input)) result.push_back(token.term);
    return result;
}

void expect_terms(std::string_view input, std::initializer_list<std::string> expected) {
    SCOPED_TRACE(std::string(input));
    EXPECT_EQ(terms(input), std::vector<std::string>(expected));
}

void expect_spans(const std::string& input, const std::vector<std::string>& originals,
                  std::size_t characters) {
    const auto tokens = collect(input);
    ASSERT_EQ(tokens.size(), originals.size());
    std::size_t total = 0;
    for (std::size_t i = 0; i < tokens.size(); ++i) {
        SCOPED_TRACE(i);
        EXPECT_EQ(tokens[i].position, i);
        ASSERT_LE(tokens[i].begin, tokens[i].end);
        ASSERT_LE(tokens[i].end, input.size());
        EXPECT_EQ(input.substr(tokens[i].begin, tokens[i].end - tokens[i].begin), originals[i]);
        total += tokens[i].characters;
    }
    EXPECT_EQ(total, characters);
}

TEST(TokenizerRules, RussianEnglishCaseDigitsAndYo) {
    expect_terms(u8"ПРИВЕТ, Ёж и ЕЖ! GTA VI PS5 3D 2026",
                 {u8"привет", u8"ёж", u8"и", u8"еж", "gta", "vi", "ps5", "3d", "2026"});
}

TEST(TokenizerRules, InternalConnectorsAreNormalized) {
    expect_terms(u8"'Baldur’s' Half‑Life rockʼnʼroll а-б -test- a--b a—b a–b",
                 {"baldur's", "half-life", "rock'n'roll", u8"а-б", "test", "a", "b", "a", "b", "a", "b"});
}

TEST(TokenizerRules, OffsetsReferToOriginalUtf8Bytes) {
    expect_spans(u8"🎮 ЁЖ Baldur’s PS5", {u8"ЁЖ", u8"Baldur’s", "PS5"}, 13);
}

TEST(TokenizerRules, LatinAccentsAndCombiningMarksWithoutNfc) {
    expect_terms(u8"POKÉMON ŁÓDŹ Straße café cafe\u0301",
                 {u8"pokémon", u8"łódź", u8"straße", u8"café", u8"cafe\u0301"});
}

TEST(TokenizerRules, PunctuationEmojiNulBomAndLineBreaks) {
    std::string text = u8"\ufeffa\r\nb";
    text.push_back('\0');
    text += u8"c_д\t🎮中文";
    expect_terms(text, {"a", "b", "c", u8"д"});
}

TEST(TokenizerRules, EmptyInputAndSeparatorsHaveNoTokens) {
    for (const auto& input : {"", u8" \n—🎮...", u8"\u0301"}) {
        EXPECT_TRUE(collect(input).empty());
    }
}

TEST(TokenizerRules, DifficultGamingNames) {
    expect_terms("C++ C# S.T.A.L.K.E.R. v1.2.3", {"c++", "c#", "stalker", "v1.2.3"});
}

TEST(TokenizerRules, DottedAcronymsRequireThreeSingleLetters) {
    expect_terms(u8"S.T.A.L.K.E.R. S.T.A.L.K.E.R stalker A.B.C. a.b.c U.S.A. А.Б.В. Ё.Ж.И. É.T.É.",
                 {"stalker", "stalker", "stalker", "abc", "abc", "usa", u8"абв", u8"ёжи", u8"été"});
    expect_terms("S.T.A.L.K.E.R.2 A.B.C.3.0.1 (A.B.C), D.E.F!",
                 {"stalker", "2", "abc", "3.0.1", "abc", "def"});
}

TEST(TokenizerRules, ShortAndOrdinaryDottedSegmentsDoNotMerge) {
    expect_terms("A.B a.b ab.cd.ef A.B.Cword AB.C.D.E word.A.B.C A.2.C A..B.C",
                 {"a", "b", "a", "b", "ab", "cd", "ef", "a", "b", "cword", "ab", "c", "d", "e",
                  "word", "a", "b", "c", "a", "2", "c", "a", "b", "c"});
    expect_terms(u8"e.g. т.е. 3.0.1 v1.2.3", {"e", "g", u8"т", u8"е", "3.0.1", "v1.2.3"});
}

TEST(TokenizerRules, AcronymSpansIncludeDotsButNormalizedLengthDoesNot) {
    const std::string input = u8"Игра S.T.A.L.K.E.R. и Ё.Ж.И. 2";
    expect_spans(input, {u8"Игра", "S.T.A.L.K.E.R.", u8"и", u8"Ё.Ж.И.", "2"}, 16);
    expect_terms(input, {u8"игра", "stalker", u8"и", u8"ёжи", "2"});
}

TEST(TokenizerRules, LongDottedChains) {
    std::string chain;
    for (int i = 0; i < 10000; ++i) chain += "A.";
    auto result = collect(chain);
    ASSERT_EQ(result.size(), 1U);
    EXPECT_EQ(result[0].term, std::string(10000, 'a'));
    EXPECT_EQ(result[0].characters, 10000U);
    result = collect(chain + "word");
    ASSERT_EQ(result.size(), 10001U);
    EXPECT_EQ(result.back().term, "word");
}

TEST(TokenizerRules, LanguageNamesBoundariesAndStandards) {
    expect_terms("C c++ C# c++17 C#12 (C++), C#/CLI C+++ C## abc++ C++Builder",
                 {"c", "c++", "c#", "c++17", "c#12", "c++", "c#", "cli", "c", "c", "abc", "c", "builder"});
    expect_terms("a+b 2+2 C+ D++ F#", {"a", "b", "2", "2", "c", "d", "f"});
}

TEST(TokenizerRules, VersionsDecimalsAndSentenceDots) {
    expect_terms(u8"3.0.1 v1.2.3 V2.10 1.5 3.0.1-rc1 3.0.1. 3. Обзор 1..2 .3 1.",
                 {"3.0.1", "v1.2.3", "v2.10", "1.5", "3.0.1-rc1", "3.0.1", "3", u8"обзор", "1", "2", "3", "1"});
    expect_terms("word.next GTA1.2 127.0.0.1", {"word", "next", "gta1", "2", "127.0.0.1"});
}

TEST(TokenizerRules, SpecialTokenSpansAndCharacterCounts) {
    expect_spans(u8"ЁЖ: C++17, C# и версия V3.0.1.",
                 {u8"ЁЖ", "C++17", "C#", u8"и", u8"версия", "V3.0.1"}, 22);
}

TEST(TokenizerRules, RealCorpusFixtures) {
    std::ifstream input(std::filesystem::path(TOKENIZER_FIXTURES_DIR) / "tokenizer_cases.json");
    ASSERT_TRUE(input);
    const auto cases = nlohmann::json::parse(input);
    ASSERT_FALSE(cases.empty());
    for (const auto& item : cases) {
        SCOPED_TRACE(item.at("url").get<std::string>());
        EXPECT_EQ(terms(item.at("text").get<std::string>()), item.at("expected").get<std::vector<std::string>>());
    }
}

TEST(TokenizerRules, MalformedUtf8Throws) {
    for (const std::string input : {"\x80", "a\xc0\xaf", "\xed\xa0\x80", "\xf4\x90\x80\x80",
                                   "\xe2\x82", "a-\xff", "C++\xff", "1.\xff", "A.B.\xff", "A.B.C.\xff"}) {
        SCOPED_TRACE(input);
        EXPECT_THROW(collect(input), std::runtime_error);
    }
}

TEST(TokenizerRules, LargeMultilingualField) {
    std::string text;
    for (int i = 0; i < 10000; ++i) text += u8"Игра PS5 ";
    const auto result = collect(text);
    ASSERT_EQ(result.size(), 20000U);
    EXPECT_EQ(result.back().position, 19999U);
    EXPECT_EQ(result.front().term, u8"игра");
}

}  // namespace
