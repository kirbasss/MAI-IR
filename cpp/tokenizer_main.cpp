#include "tokenizer.hpp"

#include <array>
#include <charconv>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

namespace {
namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
constexpr std::uint64_t max_field_bytes = 64ULL * 1024 * 1024;

struct AtomicFile {
    fs::path target, temporary;
    std::ofstream stream;
    bool committed = false;
    explicit AtomicFile(const fs::path& path) : target(path), temporary(path.string() + ".part") {
        if (fs::exists(target) || fs::exists(temporary))
            throw std::runtime_error("Output already exists: " + path.string());
        if (!target.parent_path().empty()) fs::create_directories(target.parent_path());
        stream.exceptions(std::ios::failbit | std::ios::badbit);
        stream.open(temporary, std::ios::binary);
    }
    void commit() {
        stream.close();
        fs::rename(temporary, target);
        committed = true;
    }
    ~AtomicFile() {
        if (!committed) {
            // Remove only our own unfinished output, never the requested target.
            try { if (stream.is_open()) stream.close(); } catch (...) {}
            std::error_code ignored;
            fs::remove(temporary, ignored);
        }
    }
};

std::string json_string(std::string_view value) {
    std::ostringstream out;
    out << '"';
    for (const unsigned char c : value) {
        if (c == '"' || c == '\\') out << '\\' << static_cast<char>(c);
        else if (c < 0x20) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << unsigned(c);
        else out << static_cast<char>(c);
    }
    out << '"';
    return out.str();
}

struct Counts {
    std::uint64_t input_bytes = 0, tokens = 0, characters = 0, token_bytes = 0;
    void add(const Counts& other) {
        input_bytes += other.input_bytes; tokens += other.tokens;
        characters += other.characters; token_bytes += other.token_bytes;
    }
    void json(std::ostream& out) const {
        out << "{\"input_utf8_bytes\":" << input_bytes << ",\"tokens\":" << tokens
            << ",\"token_characters\":" << characters << ",\"token_utf8_bytes\":" << token_bytes
            << ",\"mean_token_characters\":" << (tokens ? double(characters) / tokens : 0) << '}';
    }
};

struct Statistics {
    std::uint64_t documents = 0, title_only = 0;
    double processing_ms = 0, elapsed_ms = 0;
    Counts total;
    std::map<std::string, Counts> fields, sources;
    std::map<std::string, std::uint64_t> source_documents;
    std::string json(bool output_enabled) const {
        std::ostringstream out;
        out << std::setprecision(12) << "{\"rules_version\":" << json_string(gaming::rules_version)
            << ",\"documents\":" << documents << ",\"title_only_documents\":" << title_only
            << ",\"token_output_enabled\":" << (output_enabled ? "true" : "false")
            << ",\"processing_ms\":" << processing_ms << ",\"elapsed_ms\":" << elapsed_ms
            << ",\"total\":";
        total.json(out);
        for (const auto& group : {std::make_pair("fields", &fields), std::make_pair("sources", &sources)}) {
            out << ',' << json_string(group.first) << ":{";
            bool first = true;
            for (const auto& [name, counts] : *group.second) {
                if (!first) out << ',';
                first = false;
                out << json_string(name) << ':';
                counts.json(out);
            }
            out << '}';
        }
        out << ",\"source_documents\":{";
        bool first = true;
        for (const auto& [source, count] : source_documents) {
            if (!first) out << ',';
            first = false;
            out << json_string(source) << ':' << count;
        }
        out << "}}\n";
        return out.str();
    }
};

void process_field(std::uint64_t id, const std::string& source, const std::string& field,
                   const std::string& text, Statistics& stats, std::ostream* output) {
    Counts counts;
    counts.input_bytes = text.size();
    const auto start = Clock::now();
    try {
        gaming::tokenize(text, [&](const gaming::Token& token) {
            ++counts.tokens;
            counts.characters += token.characters;
            counts.token_bytes += token.term.size();
            if (output) *output << id << '\t' << field << '\t' << token.position << '\t'
                                << token.begin_byte << '\t' << token.end_byte << '\t' << token.term << '\n';
        });
    } catch (const std::exception& error) {
        throw std::runtime_error("Document " + std::to_string(id) + ", field " + field + ": " + error.what());
    }
    stats.processing_ms += std::chrono::duration<double, std::milli>(Clock::now() - start).count();
    stats.total.add(counts);
    stats.fields[field].add(counts);
    stats.sources[source].add(counts);
}

void read_exact(std::istream& input, char* output, std::size_t size) {
    if (size == 0) return;
    input.read(output, static_cast<std::streamsize>(size));
    if (static_cast<std::size_t>(input.gcount()) != size)
        throw std::runtime_error("Truncated corpus record");
}

std::uint64_t read_integer(std::istream& input, std::size_t bytes) {
    std::array<unsigned char, 8> data{};
    read_exact(input, reinterpret_cast<char*>(data.data()), bytes);
    std::uint64_t value = 0;
    for (std::size_t i = 0; i < bytes; ++i) value |= std::uint64_t(data[i]) << (8 * i);
    return value;
}

std::string read_field(std::istream& input) {
    const auto size = read_integer(input, 8);
    if (size > max_field_bytes) throw std::runtime_error("Field size limit exceeded");
    std::string text(static_cast<std::size_t>(size), '\0');
    read_exact(input, text.data(), text.size());
    return text;
}

void process_corpus(std::istream& input, std::uint64_t limit, Statistics& stats, std::ostream* output) {
    const std::string magic("MAIIR3\0\1", 8);
    std::string header(8, '\0');
    read_exact(input, header.data(), header.size());
    if (header != magic) throw std::runtime_error("Unsupported corpus format");
    std::uint64_t previous = 0;
    while (!limit || stats.documents < limit) {
        if (input.peek() == std::char_traits<char>::eof()) {
            if (input.bad()) throw std::runtime_error("Cannot read corpus");
            break;
        }
        const auto id = read_integer(input, 8);
        if (id <= previous) throw std::runtime_error("Document IDs must increase");
        previous = id;
        const auto source_size = read_integer(input, 4);
        if (source_size == 0 || source_size > 64) throw std::runtime_error("Invalid source length");
        std::string source(static_cast<std::size_t>(source_size), '\0');
        read_exact(input, source.data(), source.size());
        for (std::size_t i = 0; i < source.size(); ++i) {
            const char c = source[i];
            if (!((c >= 'a' && c <= 'z') || (i > 0 && ((c >= '0' && c <= '9') || c == '_'))))
                throw std::runtime_error("Invalid source identifier");
        }
        const auto title = read_field(input);
        process_field(id, source, "title", title, stats, output);
        const auto text = read_field(input);
        process_field(id, source, "text", text, stats, output);
        ++stats.documents;
        ++stats.source_documents[source];
        if (text.find_first_not_of(" \t\r\n\v\f") == std::string::npos) ++stats.title_only;
    }
}

std::uint64_t positive_integer(const std::string& value) {
    std::uint64_t result = 0;
    const auto parsed = std::from_chars(value.data(), value.data() + value.size(), result);
    if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size() || result == 0)
        throw std::runtime_error("Expected a positive integer: " + value);
    return result;
}

}  // namespace

int main(int argc, char** argv) {
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    try {
        if (argc == 2 && std::string(argv[1]) == "--help") {
            std::cout << "tokenizer text [--output FILE] [--stats FILE] < input.txt\n"
                         "tokenizer corpus SNAPSHOT.bin [--limit N] [--output FILE] [--stats FILE]\n"
                         "UTF-8; TSV offsets are bytes in the original field, positions start at 0.\n"
                         "Existing output files are never overwritten.\n";
            return 0;
        }
        if (argc < 2) throw std::runtime_error("Missing mode; use --help");
        const std::string mode = argv[1];
        if (mode != "text" && mode != "corpus") throw std::runtime_error("Unknown mode: " + mode);
        std::string input_path, output_path, stats_path;
        std::uint64_t limit = 0;
        int index = 2;
        if (mode == "corpus") {
            if (argc < 3) throw std::runtime_error("Missing corpus path");
            input_path = argv[index++];
        }
        while (index < argc) {
            const std::string option = argv[index++];
            if (index == argc) throw std::runtime_error("Missing value for " + option);
            const std::string value = argv[index++];
            if (option == "--output" && output_path.empty()) output_path = value;
            else if (option == "--stats" && stats_path.empty()) stats_path = value;
            else if (option == "--limit" && mode == "corpus" && !limit) limit = positive_integer(value);
            else throw std::runtime_error("Unknown or duplicate option: " + option);
        }
        if (!output_path.empty() && !stats_path.empty() &&
            fs::absolute(output_path).lexically_normal() == fs::absolute(stats_path).lexically_normal())
            throw std::runtime_error("Token and statistics paths must differ");
        std::unique_ptr<AtomicFile> output_file, stats_file;
        if (!output_path.empty()) output_file = std::make_unique<AtomicFile>(output_path);
        if (!stats_path.empty()) stats_file = std::make_unique<AtomicFile>(stats_path);
        std::ostream* output = output_file ? &output_file->stream : (mode == "text" ? &std::cout : nullptr);
        Statistics stats;
        const auto start = Clock::now();
        if (output) *output << "document_id\tfield\tposition\tbegin_byte\tend_byte\tterm\n";
        if (mode == "text") {
            std::string text;
            std::array<char, 65536> buffer{};
            while (std::cin) {
                std::cin.read(buffer.data(), buffer.size());
                text.append(buffer.data(), static_cast<std::size_t>(std::cin.gcount()));
                if (text.size() > max_field_bytes) throw std::runtime_error("Field size limit exceeded");
            }
            if (std::cin.bad()) throw std::runtime_error("Cannot read standard input");
            process_field(1, "stdin", "text", text, stats, output);
            stats.documents = 1;
            stats.source_documents["stdin"] = 1;
        } else {
            std::ifstream input(input_path, std::ios::binary);
            if (!input) throw std::runtime_error("Cannot open corpus: " + input_path);
            process_corpus(input, limit, stats, output);
        }
        if (output) {
            output->flush();
            if (!*output) throw std::runtime_error("Cannot write tokens");
        }
        if (output_file) output_file->commit();
        stats.elapsed_ms = std::chrono::duration<double, std::milli>(Clock::now() - start).count();
        const auto summary = stats.json(output != nullptr);
        if (stats_file) { stats_file->stream << summary; stats_file->commit(); }
        (mode == "text" ? std::cerr : std::cout) << summary;
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "[tokenizer] " << error.what() << '\n';
        return 1;
    }
}
