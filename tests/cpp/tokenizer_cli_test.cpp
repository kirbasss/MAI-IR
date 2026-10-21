#include "tokenizer_cli.hpp"

#include <gtest/gtest.h>
#include <nlohmann/json.hpp>

#include <filesystem>
#include <cstdint>
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

struct Result { int code; std::string out, err; };

Result cli(std::vector<std::string> args, const std::string& data = "") {
    args.insert(args.begin(), "tokenizer");
    std::vector<const char*> argv;
    for (const auto& arg : args) argv.push_back(arg.c_str());
    std::istringstream input(data);
    std::ostringstream output, errors;
    const int code = gaming::run_tokenizer(static_cast<int>(argv.size()), argv.data(), input, output, errors);
    return {code, output.str(), errors.str()};
}

void write_bytes(const fs::path& path, const std::string& data) {
    std::ofstream out(path, std::ios::binary);
    out.exceptions(std::ios::badbit | std::ios::failbit);
    out.write(data.data(), static_cast<std::streamsize>(data.size()));
}

std::string read_bytes(const fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("Cannot read test output: " + path.string());
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

void append_integer(std::string& data, std::uint64_t value, std::size_t size) {
    for (std::size_t i = 0; i < size; ++i) data.push_back(static_cast<char>((value >> (8 * i)) & 255));
}

void append_field(std::string& data, const std::string& value) {
    append_integer(data, value.size(), 8);
    data += value;
}

void append_record(std::string& data, std::uint64_t id, const std::string& source,
                   const std::string& title, const std::string& text) {
    append_integer(data, id, 8);
    append_integer(data, source.size(), 4);
    data += source;
    append_field(data, title);
    append_field(data, text);
}

std::string magic() { return std::string("MAIIR3\0\1", 8); }

class TokenizerCli : public ::testing::Test {
protected:
    fs::path root;
    void SetUp() override {
        std::random_device random;
        for (int attempt = 0; attempt < 100; ++attempt) {
            const auto candidate = fs::temp_directory_path() /
                ("mai-ir-gtest-" + std::to_string(random()) + "-" + std::to_string(random()));
            if (fs::create_directory(candidate)) { root = candidate; return; }
        }
        FAIL() << "Cannot create a unique temporary test directory";
    }
    void TearDown() override {
        // Only remove the exact unique directory successfully created by this test.
        if (!root.empty()) { std::error_code ignored; fs::remove_all(root, ignored); }
    }
};

TEST_F(TokenizerCli, TextReturnsTsvAndJsonStatistics) {
    const std::string input = u8"🎮 ЁЖ Baldur’s PS5";
    const auto result = cli({"text"}, input);
    ASSERT_EQ(result.code, 0) << result.err;
    EXPECT_NE(result.out.find("document_id\tfield\tposition\tbegin_byte\tend_byte\tterm\n"), std::string::npos);
    const auto stats = Json::parse(result.err);
    EXPECT_EQ(stats.at("total").at("tokens"), 3);
    EXPECT_EQ(stats.at("total").at("token_characters"), 13);
    EXPECT_EQ(stats.at("total").at("input_utf8_bytes"), input.size());
    EXPECT_NEAR(stats.at("total").at("mean_token_characters").get<double>(), 13.0 / 3, 1e-10);
}

TEST_F(TokenizerCli, EmptyInputHasZeroCountersAndValidJson) {
    const auto result = cli({"text"});
    ASSERT_EQ(result.code, 0) << result.err;
    const auto stats = Json::parse(result.err);
    EXPECT_EQ(stats.at("total").at("tokens"), 0);
    EXPECT_EQ(stats.at("total").at("mean_token_characters"), 0);
}

TEST_F(TokenizerCli, CorpusFieldsTitleOnlyLimitAndStatistics) {
    std::string corpus = magic();
    append_record(corpus, 1, "ru", u8"Заголовок", u8"Игра PS5");
    append_record(corpus, 2, "en", "Gallery", "");
    const auto path = root / "corpus with spaces.bin", tokens = root / "tokens.tsv", stats_file = root / "stats.json";
    write_bytes(path, corpus);
    const auto result = cli({"corpus", path.string(), "--output", tokens.string(), "--stats", stats_file.string()});
    ASSERT_EQ(result.code, 0) << result.err;
    const auto stats = Json::parse(result.out);
    EXPECT_EQ(stats, Json::parse(read_bytes(stats_file)));
    EXPECT_EQ(stats.at("documents"), 2);
    EXPECT_EQ(stats.at("title_only_documents"), 1);
    EXPECT_EQ(stats.at("total").at("tokens"), 4);
    const auto tsv = read_bytes(tokens);
    EXPECT_NE(tsv.find("1\ttitle\t0\t"), std::string::npos);
    EXPECT_NE(tsv.find("1\ttext\t0\t"), std::string::npos);
    EXPECT_NE(tsv.find("1\ttext\t1\t"), std::string::npos);
    EXPECT_NE(tsv.find("2\ttitle\t0\t"), std::string::npos);
    const auto limited = cli({"corpus", path.string(), "--limit", "1"});
    ASSERT_EQ(limited.code, 0) << limited.err;
    EXPECT_EQ(Json::parse(limited.out).at("documents"), 1);
    EXPECT_FALSE(Json::parse(limited.out).at("token_output_enabled").get<bool>());
}

TEST_F(TokenizerCli, CorruptCorpusAndOversizedFieldAreRejected) {
    std::vector<std::string> cases = {"bad", magic() + "\x01"};
    auto raw = magic(); append_integer(raw, 1, 8); append_integer(raw, 1000, 4); cases.push_back(raw);
    raw = magic(); append_integer(raw, 1, 8); append_integer(raw, 1, 4); raw += 'x';
    append_integer(raw, std::uint64_t{1} << 40, 8); cases.push_back(raw);
    raw = magic(); append_integer(raw, 0, 8); append_integer(raw, 1, 4); cases.push_back(raw);
    const auto path = root / "bad.bin";
    for (const auto& bytes : cases) {
        SCOPED_TRACE(bytes.size());
        write_bytes(path, bytes);
        EXPECT_NE(cli({"corpus", path.string()}).code, 0);
    }
}

TEST_F(TokenizerCli, InvalidSourceAndRepeatedDocumentIdAreRejected) {
    const auto path = root / "bad.bin";
    auto raw = magic(); append_record(raw, 1, "Wrong-source", "title", "text"); write_bytes(path, raw);
    EXPECT_NE(cli({"corpus", path.string()}).code, 0);
    raw = magic(); append_record(raw, 1, "ru", "title", "text");
    append_record(raw, 1, "ru", "duplicate", "text"); write_bytes(path, raw);
    EXPECT_NE(cli({"corpus", path.string()}).code, 0);
}

TEST_F(TokenizerCli, FailedUtf8DoesNotPublishPartialFile) {
    const auto output = root / "tokens.tsv";
    const auto result = cli({"text", "--output", output.string()}, "hello \xff");
    EXPECT_NE(result.code, 0);
    EXPECT_NE(result.err.find("Invalid UTF-8"), std::string::npos);
    EXPECT_FALSE(fs::exists(output));
    EXPECT_TRUE(fs::is_empty(root));
}

TEST_F(TokenizerCli, ExistingOutputAndStatisticsAreNeverOverwritten) {
    const auto output = root / "tokens.tsv", stats = root / "stats.json";
    write_bytes(output, "user data");
    EXPECT_NE(cli({"text", "--output", output.string()}, "hello").code, 0);
    EXPECT_EQ(read_bytes(output), "user data");
    write_bytes(stats, "saved statistics");
    EXPECT_NE(cli({"text", "--stats", stats.string()}, "hello").code, 0);
    EXPECT_EQ(read_bytes(stats), "saved statistics");
}

TEST_F(TokenizerCli, IdenticalOutputPathsAreRejected) {
    const auto path = (root / "same.tsv").string();
    EXPECT_NE(cli({"text", "--output", path, "--stats", path}, "hello").code, 0);
    EXPECT_FALSE(fs::exists(path));
}

TEST_F(TokenizerCli, InvalidArgumentsReturnNonzero) {
    const std::vector<std::vector<std::string>> cases = {
        {}, {"unknown"}, {"text", "--limit", "1"}, {"corpus", "x", "--limit", "0"},
        {"corpus", "x", "--limit", "-1"}, {"corpus", "x", "--limit", "1x"},
        {"text", "--output"}, {"text", "--output", "x", "--output", "y"},
    };
    for (const auto& args : cases) EXPECT_NE(cli(args).code, 0);
    EXPECT_EQ(cli({"--help"}).code, 0);
}

}  // namespace
