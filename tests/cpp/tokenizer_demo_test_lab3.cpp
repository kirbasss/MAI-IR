#include "tokenizer_demo.hpp"

#include <gtest/gtest.h>
#include <nlohmann/json.hpp>

#include <filesystem>
#include <fstream>
#include <iterator>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
namespace fs = std::filesystem;
using Json = nlohmann::json;

void write(const fs::path& path, const std::string& text) {
    std::ofstream file(path, std::ios::binary);
    file.exceptions(std::ios::failbit | std::ios::badbit);
    file.write(text.data(), static_cast<std::streamsize>(text.size()));
}

void number(std::string& bytes, std::uint64_t value, std::size_t size) {
    for (std::size_t i = 0; i < size; ++i) bytes.push_back(static_cast<char>((value >> (i * 8)) & 255));
}

void record(std::string& bytes, std::uint64_t id, const std::string& source,
            const std::string& title, const std::string& text) {
    number(bytes, id, 8); number(bytes, source.size(), 4); bytes += source;
    for (const auto& field : {title, text}) { number(bytes, field.size(), 8); bytes += field; }
}

class TokenizerDemo : public ::testing::Test {
protected:
    fs::path root, snapshot;
    const std::string version_text = u8"Игра обновилась до версии 3.0.1. Исправлены ошибки.";
    void SetUp() override {
        std::random_device random;
        for (int i = 0; i < 100; ++i) {
            auto candidate = fs::temp_directory_path() / ("mai-ir-demo-test-" + std::to_string(random()));
            if (fs::create_directory(candidate)) { root = candidate; break; }
        }
        ASSERT_FALSE(root.empty());
        snapshot = root / "snapshot"; fs::create_directory(snapshot);
        std::string corpus("MAIIR3\0\1", 8);
        record(corpus, 1, "gamemag", u8"Обновление игры", version_text);
        record(corpus, 2, "pcgamer", "Languages", "Written in C, C++ and C#. The game uses C++17.");
        record(corpus, 3, "stopgame", u8"Обзор", u8"Новая игра S.T.A.L.K.E.R. 2 вышла.");
        write(snapshot / "corpus.bin", corpus);
        write(snapshot / "manifest.json", Json({{"format", "MAIIR3-v1"}, {"created_at", "2026-10-21"}, {"documents", 3}}).dump());
        std::string metadata;
        for (int id = 1; id <= 3; ++id)
            metadata += Json({{"document_id", id}, {"url", "https://example.test/doc/" + std::to_string(id)}}).dump() + "\n";
        write(snapshot / "documents.jsonl", metadata);
    }
    void TearDown() override {
        if (!root.empty()) { std::error_code ignored; fs::remove_all(root, ignored); }
    }
    int cli(std::vector<std::string> args, std::ostringstream& out, std::ostringstream& err) {
        args.insert(args.begin(), "tokenizer_demo");
        std::vector<const char*> argv;
        for (const auto& arg : args) argv.push_back(arg.c_str());
        return gaming::run_tokenizer_demo(static_cast<int>(argv.size()), argv.data(), out, err);
    }
};

TEST_F(TokenizerDemo, DefaultsShowActualMetadataAndAllThreeRules) {
    const auto text = gaming::demonstrate(snapshot);
    EXPECT_NE(text.find("DOCUMENTS: 3"), std::string::npos);
    EXPECT_NE(text.find("SOURCE: GameMAG"), std::string::npos);
    EXPECT_NE(text.find(u8"TITLE: Обновление игры"), std::string::npos);
    EXPECT_NE(text.find("https://example.test/doc/2"), std::string::npos);
    EXPECT_NE(text.find("RULES: gaming-v3"), std::string::npos);
    EXPECT_NE(text.find(u8"Версия сохранена одним токеном: 3.0.1."), std::string::npos);
    EXPECT_NE(text.find("c++, c#"), std::string::npos);
    EXPECT_NE(text.find(u8"Точки удалены"), std::string::npos);
}

TEST_F(TokenizerDemo, ByteOffsetsAndPositionsReferToTheWholeOriginalField) {
    gaming::DemoOptions options;
    options.document_id = 1; options.find = "3.0.1"; options.context = 0;
    const auto text = gaming::demonstrate(snapshot, options);
    const auto start = version_text.find("3.0.1");
    EXPECT_NE(text.find("ORIGINAL [UTF-8 bytes " + std::to_string(start) + ":" + std::to_string(start + 5) + "]:\n3.0.1\n"), std::string::npos);
    EXPECT_NE(text.find("4         [" + std::to_string(start) + "," + std::to_string(start + 5) + ")"), std::string::npos);
}

TEST_F(TokenizerDemo, CustomTitleAndAbsentFragment) {
    gaming::DemoOptions options;
    options.document_id = 1; options.field = "title"; options.context = 0;
    const auto text = gaming::demonstrate(snapshot, options);
    EXPECT_NE(text.find("FIELD: title"), std::string::npos);
    EXPECT_NE(text.find(u8"Обновление"), std::string::npos);
    options.find = "not in article";
    EXPECT_THROW(gaming::demonstrate(snapshot, options), std::runtime_error);
}

TEST_F(TokenizerDemo, LatestSnapshotIgnoresUnpublishedAndInvalidExports) {
    const auto staging = root / ".unfinished"; fs::create_directory(staging);
    write(staging / "manifest.json", Json({{"format", "MAIIR3-v1"}, {"created_at", "2099"}}).dump());
    write(staging / "corpus.bin", "unfinished");
    const auto invalid = root / "bad"; fs::create_directory(invalid);
    write(invalid / "manifest.json", "invalid json");
    EXPECT_EQ(gaming::latest_snapshot(root), snapshot);
}

TEST_F(TokenizerDemo, CliOutputIsUtf8AndDoesNotOverwriteTranscript) {
    const auto output = root / "demo.txt";
    const std::vector<std::string> args = {"--root", root.string(), "--snapshot", "snapshot", "--document-id", "1",
                                         "--find", "3.0.1", "--output", output.string()};
    std::ostringstream out, err;
    ASSERT_EQ(cli(args, out, err), 0) << err.str();
    std::ifstream file(output, std::ios::binary);
    const std::string saved{std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
    file.close();
    EXPECT_EQ(saved, out.str());
    EXPECT_NE(cli(args, out, err), 0);
    std::ifstream again(output, std::ios::binary);
    EXPECT_EQ(saved, (std::string{std::istreambuf_iterator<char>(again), std::istreambuf_iterator<char>()}));
}

TEST_F(TokenizerDemo, BadOptionsCorruptCorpusAndMissingMetadataFail) {
    for (const auto& args : std::vector<std::vector<std::string>>{
             {"--context", "-1"}, {"--context", "21"}, {"--document-id", "0"},
             {"--find", ""}, {"--field", "unknown"}}) {
        std::ostringstream out, err;
        EXPECT_NE(cli(args, out, err), 0);
    }
    write(snapshot / "documents.jsonl", "");
    EXPECT_THROW(gaming::demonstrate(snapshot), std::runtime_error);
    write(snapshot / "corpus.bin", "bad");
    EXPECT_THROW(gaming::demonstrate(snapshot), std::runtime_error);
}

}  // namespace
