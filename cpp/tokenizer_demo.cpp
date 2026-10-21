#include "tokenizer_demo.hpp"
#include "tokenizer.hpp"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <charconv>
#include <fstream>
#include <limits>
#include <map>
#include <sstream>
#include <stdexcept>
#include <tuple>
#include <vector>

namespace {
namespace fs = std::filesystem;
using Json = nlohmann::json;

Json read_json(const fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("Cannot open " + path.string());
    return Json::parse(input);
}

std::string read_exact(std::istream& input, std::size_t size) {
    std::string bytes(size, '\0');
    if (size) {
        input.read(bytes.data(), static_cast<std::streamsize>(size));
        if (static_cast<std::size_t>(input.gcount()) != size)
            throw std::runtime_error("Truncated corpus record");
    }
    return bytes;
}

std::uint64_t read_integer(std::istream& input, std::size_t size) {
    const auto bytes = read_exact(input, size);
    std::uint64_t value = 0;
    for (std::size_t i = 0; i < size; ++i)
        value |= std::uint64_t(static_cast<unsigned char>(bytes[i])) << (8 * i);
    return value;
}

std::string read_field(std::istream& input) {
    const auto size = read_integer(input, 8);
    if (size > 64ULL * 1024 * 1024) throw std::runtime_error("Field size limit exceeded");
    return read_exact(input, static_cast<std::size_t>(size));
}

struct TokenCopy {
    std::string term;
    std::uint64_t position;
    std::size_t begin, end;
};

std::vector<TokenCopy> tokens(const std::string& text) {
    std::vector<TokenCopy> result;
    gaming::tokenize(text, [&](const gaming::Token& token) {
        result.push_back({std::string(token.term), token.position, token.begin_byte, token.end_byte});
    });
    return result;
}

struct Example {
    std::uint64_t id;
    std::string source, title, field, text, kind;
    std::vector<TokenCopy> rows;
    std::size_t first, last;
};

bool version(const std::string& term) {
    std::size_t i = !term.empty() && term[0] == 'v' ? 1 : 0;
    bool dot = false, digit = false;
    for (; i < term.size(); ++i) {
        const char c = term[i];
        if (c >= '0' && c <= '9') digit = true;
        else if (c == '.' && digit && i + 1 < term.size() && term[i + 1] >= '0' && term[i + 1] <= '9') {
            dot = true; digit = false;
        } else if (c == '-' && dot && digit) return true;
        else return false;
    }
    return dot && digit;
}

bool language(const std::string& term) {
    return term.rfind("c++", 0) == 0 || term.rfind("c#", 0) == 0;
}

std::vector<Example> select_examples(const fs::path& path, const gaming::DemoOptions& options,
                                      std::uint64_t expected_documents) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("Cannot open corpus: " + path.string());
    if (read_exact(input, 8) != std::string("MAIIR3\0\1", 8))
        throw std::runtime_error("Unsupported corpus format");
    using Priority = std::tuple<bool, bool, bool, std::uint64_t>;
    std::map<std::string, std::pair<Priority, Example>> best;
    std::optional<Example> fallback;
    std::uint64_t previous = 0, count = 0;
    const bool custom = options.document_id.has_value() || options.find.has_value();
    while (input.peek() != std::char_traits<char>::eof()) {
        const auto id = read_integer(input, 8);
        if (id <= previous) throw std::runtime_error("Document IDs must increase");
        previous = id; ++count;
        const auto source_size = read_integer(input, 4);
        if (!source_size || source_size > 64) throw std::runtime_error("Invalid source length");
        const auto source = read_exact(input, static_cast<std::size_t>(source_size));
        for (std::size_t i = 0; i < source.size(); ++i) {
            const char c = source[i];
            if (!((c >= 'a' && c <= 'z') || (i && ((c >= '0' && c <= '9') || c == '_'))))
                throw std::runtime_error("Invalid source identifier");
        }
        const auto title = read_field(input), text = read_field(input);
        if (options.document_id && *options.document_id != id) continue;
        for (const auto& field : {std::string("title"), std::string("text")}) {
            const auto& value = field == "title" ? title : text;
            if (value.empty() || (custom && field != options.field)) continue;
            auto rows = tokens(value);
            if (rows.empty()) continue;
            auto example = [&](std::string kind, std::size_t first, std::size_t last) {
                return Example{id, source, title, field, value, std::move(kind), rows, first, last};
            };
            if (custom) {
                std::size_t first = 0, last = 0;
                if (options.find) {
                    const auto start = value.find(*options.find);
                    if (start == std::string::npos) continue;
                    bool found = false;
                    for (std::size_t i = 0; i < rows.size(); ++i) {
                        if (rows[i].end > start && rows[i].begin < start + options.find->size()) {
                            if (!found) first = i;
                            last = i; found = true;
                        }
                    }
                    if (!found) throw std::runtime_error("The fragment contains no tokens");
                }
                return {example("custom", first, last)};
            }
            if (!fallback || (fallback->field == "title" && field == "text"))
                fallback = example("custom", 0, 0);
            auto keep = [&](const std::string& kind, std::size_t first, std::size_t last,
                            bool exact = false, bool preferred_source = false) {
                const Priority priority{exact, preferred_source, field == "text",
                                        std::numeric_limits<std::uint64_t>::max() - id};
                const auto old = best.find(kind);
                if (old == best.end() || priority > old->second.first)
                    best.insert_or_assign(kind, std::make_pair(priority, example(kind, first, last)));
            };
            std::optional<std::size_t> plus, sharp;
            for (std::size_t i = 0; i < rows.size(); ++i) {
                const auto& row = rows[i];
                if (version(row.term)) keep("version", i, i, row.term == "3.0.1", source == "gamemag");
                if (language(row.term)) {
                    const bool is_plus = row.term.rfind("c++", 0) == 0;
                    const auto other = is_plus ? sharp : plus;
                    const bool pair = other && row.end - rows[*other].begin <= 160;
                    keep("language", pair ? *other : i, i, pair, source == "pcgamer");
                    (is_plus ? plus : sharp) = i;
                }
                if (row.term == "stalker" && value.substr(row.begin, row.end - row.begin).find('.') != std::string::npos)
                    keep("stalker", i, i);
                if (row.term == "half-life") keep("half_life", i, i);
            }
        }
        if (custom && options.document_id) break;
    }
    if (input.bad()) throw std::runtime_error("Cannot read corpus");
    if (custom) throw std::runtime_error("Document or fragment not found in the selected field");
    if (count != expected_documents) throw std::runtime_error("Manifest document count does not match corpus");
    std::vector<Example> result;
    for (const auto& key : {"version", "language"})
        if (best.count(key)) result.push_back(std::move(best.at(key).second));
    if (best.count("stalker")) result.push_back(std::move(best.at("stalker").second));
    else if (best.count("half_life")) result.push_back(std::move(best.at("half_life").second));
    if (result.empty() && fallback) result.push_back(std::move(*fallback));
    return result;
}

std::size_t width(const std::string& value) {
    return static_cast<std::size_t>(std::count_if(value.begin(), value.end(), [](unsigned char c) {
        return (c & 0xc0) != 0x80;
    }));
}

std::string observation(const Example& e) {
    if (e.kind == "version") return u8"Версия сохранена одним токеном: " + e.rows[e.first].term + ".";
    if (e.kind == "stalker") return u8"Точки удалены, название сохранено одним токеном: stalker. STALKER даст тот же терм.";
    if (e.kind == "half_life") return u8"Название с дефисом сохранено. Half Life даст два отдельных токена.";
    if (e.kind == "language") {
        std::string names;
        for (auto i = e.first; i <= e.last; ++i) if (language(e.rows[i].term)) {
            if (!names.empty()) names += ", ";
            names += e.rows[i].term;
        }
        return u8"Специальные обозначения сохраняются и отличаются от c: " + names + ".";
    }
    return u8"Позиции и байтовые границы относятся к целому полю статьи, а не к показанному фрагменту.";
}

std::string render(const Example& e, const std::string& url, std::size_t context) {
    const std::map<std::string, std::string> sources = {
        {"gamemag", "GameMAG"}, {"pcgamer", "PC Gamer"}, {"stopgame", "StopGame"},
        {"playground", "PlayGround"}, {"ixbt_games", "IXBT.games"}, {"igromania", u8"Игромания"},
        {"eurogamer", "Eurogamer"}, {"gamingonlinux", "GamingOnLinux"},
    };
    const auto first = e.first > context ? e.first - context : 0;
    const auto last = std::min(e.rows.size() - 1, e.last + context);
    const auto start = e.rows[first].begin, end = e.rows[last].end;
    std::vector<std::array<std::string, 4>> table = {{{"position", "bytes", "original", "normalized"}}};
    for (auto i = first; i <= last; ++i) {
        const auto& row = e.rows[i];
        table.push_back({std::to_string(row.position), "[" + std::to_string(row.begin) + "," + std::to_string(row.end) + ")",
                         e.text.substr(row.begin, row.end - row.begin), row.term});
    }
    std::array<std::size_t, 4> widths{};
    for (const auto& row : table) for (std::size_t i = 0; i < 4; ++i) widths[i] = std::max(widths[i], width(row[i]));
    std::ostringstream out;
    out << std::string(76, '=') << "\nSOURCE: " << (sources.count(e.source) ? sources.at(e.source) : e.source)
        << "\nDOCUMENT: " << e.id << "\nTITLE: " << e.title << "\nURL: " << url
        << "\nFIELD: " << e.field << "    RULES: " << gaming::rules_version << "\n\nORIGINAL [UTF-8 bytes "
        << start << ':' << end << "]:\n" << e.text.substr(start, end - start) << "\n\nTOKENS:\n";
    for (std::size_t row = 0; row < table.size(); ++row) {
        for (std::size_t col = 0; col < 4; ++col) {
            out << table[row][col];
            if (col < 3) out << std::string(widths[col] - width(table[row][col]) + 2, ' ');
        }
        out << '\n';
        if (row == 0) out << std::string(widths[0] + widths[1] + widths[2] + widths[3] + 6, '-') << '\n';
    }
    return out.str() + "\nOBSERVATION:\n" + observation(e) + "\n\n";
}

std::uint64_t integer(const std::string& value) {
    std::uint64_t parsed = 0;
    const auto result = std::from_chars(value.data(), value.data() + value.size(), parsed);
    if (result.ec != std::errc{} || result.ptr != value.data() + value.size())
        throw std::runtime_error("Expected an unsigned integer: " + value);
    return parsed;
}
}  // namespace

fs::path gaming::latest_snapshot(const fs::path& root) {
    if (!fs::is_directory(root)) throw std::runtime_error("No snapshot directory: " + root.string());
    std::optional<std::pair<std::string, fs::path>> best;
    for (fs::recursive_directory_iterator it(root), end; it != end; ++it) {
        if (it->is_directory() && it->path().filename().string().front() == '.') {
            it.disable_recursion_pending(); continue;
        }
        if (it->path().filename() != "manifest.json") continue;
        try {
            const auto manifest = read_json(it->path());
            if (manifest.value("format", "") != "MAIIR3-v1" || !fs::is_regular_file(it->path().parent_path() / "corpus.bin")) continue;
            const auto candidate = std::make_pair(manifest.at("created_at").get<std::string>(), it->path().parent_path());
            if (!best || candidate > *best) best = candidate;
        } catch (const std::exception&) { /* Ignore incomplete/unrelated exports. */ }
    }
    if (!best) throw std::runtime_error("No published snapshot; first run export_corpus.py");
    return best->second;
}

std::string gaming::demonstrate(const fs::path& snapshot, const DemoOptions& options) {
    if (options.context > 20 || (options.document_id && *options.document_id == 0) ||
        (options.find && options.find->empty()) || (options.field != "title" && options.field != "text"))
        throw std::runtime_error("Invalid demo options");
    const auto manifest = read_json(snapshot / "manifest.json");
    if (manifest.value("format", "") != "MAIIR3-v1") throw std::runtime_error("Unsupported snapshot manifest");
    const auto examples = select_examples(snapshot / "corpus.bin", options, manifest.at("documents").get<std::uint64_t>());
    if (examples.empty()) throw std::runtime_error("Snapshot contains no tokens to demonstrate");
    std::map<std::uint64_t, std::string> urls;
    for (const auto& example : examples) urls.emplace(example.id, "");
    std::ifstream metadata(snapshot / "documents.jsonl", std::ios::binary);
    if (!metadata) throw std::runtime_error("Cannot open documents.jsonl");
    std::string line;
    while (std::getline(metadata, line)) {
        const auto document = Json::parse(line);
        const auto id = document.at("document_id").get<std::uint64_t>();
        if (urls.count(id)) urls[id] = document.at("url").get<std::string>();
    }
    std::ostringstream out;
    out << "SNAPSHOT: " << fs::absolute(snapshot).string() << "\nDOCUMENTS: " << manifest.at("documents")
        << u8"\nID относится к снимку; позиции с нуля и границы [начало,конец) относятся к целому UTF-8 полю статьи.\n\n";
    for (const auto& example : examples) {
        if (urls.at(example.id).empty()) throw std::runtime_error("Metadata missing for document " + std::to_string(example.id));
        out << render(example, urls.at(example.id), options.context);
    }
    return out.str();
}

int gaming::run_tokenizer_demo(int argc, const char* const* argv, std::ostream& output, std::ostream& errors) {
    try {
        fs::path root = fs::is_directory("/corpus") ? fs::path("/corpus") : fs::path("data/tokenization");
        std::optional<fs::path> snapshot, transcript;
        DemoOptions options;
        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            if (arg == "--help") {
                output << "tokenizer_demo [--root DIR] [--snapshot DIR] [--document-id N]\n"
                          "               [--find UTF8_LITERAL] [--field title|text] [--context 0..20] [--output NEW_FILE]\n"
                          "Default: latest published snapshot; literal --find is case-sensitive.\n"
                          "Relative --snapshot is resolved under --root. No MongoDB or network is used.\n";
                return 0;
            }
            if (++i == argc) throw std::runtime_error("Missing value for " + arg);
            const std::string value = argv[i];
            if (arg == "--root") root = value;
            else if (arg == "--snapshot") snapshot = value;
            else if (arg == "--document-id") options.document_id = integer(value);
            else if (arg == "--find") options.find = value;
            else if (arg == "--field") options.field = value;
            else if (arg == "--context") {
                const auto context = integer(value);
                if (context > 20) throw std::runtime_error("--context must be between 0 and 20");
                options.context = static_cast<std::size_t>(context);
            } else if (arg == "--output") transcript = value;
            else throw std::runtime_error("Unknown option: " + arg);
        }
        const auto selected = snapshot ? (snapshot->is_absolute() ? *snapshot : root / *snapshot) : latest_snapshot(root);
        const auto text = demonstrate(selected, options);
        if (transcript) {
            if (fs::exists(*transcript)) throw std::runtime_error("Output already exists: " + transcript->string());
            if (!transcript->parent_path().empty()) fs::create_directories(transcript->parent_path());
            std::ofstream file(*transcript, std::ios::binary);
            file.exceptions(std::ios::failbit | std::ios::badbit);
            file << text;
            file.close();
        }
        output << text;
        return 0;
    } catch (const std::exception& error) {
        errors << "[demo] " << error.what() << '\n';
        return 1;
    }
}
