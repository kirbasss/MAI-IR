#include "tokenizer.hpp"

#include <stdexcept>
#include <string>

namespace gaming {
namespace {

struct Rune { std::uint32_t value; std::size_t bytes; };

Rune decode(std::string_view input, std::size_t offset) {
    const auto first = static_cast<unsigned char>(input[offset]);
    if (first < 0x80) return {first, 1};
    std::size_t count = 0;
    std::uint32_t value = 0, minimum = 0;
    if (first >= 0xC2 && first <= 0xDF) {
        count = 2; value = first & 0x1F; minimum = 0x80;
    } else if (first >= 0xE0 && first <= 0xEF) {
        count = 3; value = first & 0x0F; minimum = 0x800;
    } else if (first >= 0xF0 && first <= 0xF4) {
        count = 4; value = first & 0x07; minimum = 0x10000;
    }
    auto fail = [offset]() -> Rune {
        throw std::runtime_error("Invalid UTF-8 at byte " + std::to_string(offset));
    };
    if (count == 0 || input.size() - offset < count) return fail();
    for (std::size_t i = 1; i < count; ++i) {
        const auto next = static_cast<unsigned char>(input[offset + i]);
        if ((next & 0xC0) != 0x80) return fail();
        value = (value << 6) | (next & 0x3F);
    }
    if (value < minimum || value > 0x10FFFF || (value >= 0xD800 && value <= 0xDFFF))
        return fail();
    return {value, count};
}

bool word(std::uint32_t c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
           (c >= '0' && c <= '9') || (c >= 0x0410 && c <= 0x044F) ||
           c == 0x0401 || c == 0x0451 ||
           (c >= 0x00C0 && c <= 0x00D6) || (c >= 0x00D8 && c <= 0x00F6) ||
           (c >= 0x00F8 && c <= 0x017F);
}

bool digit(std::uint32_t c) { return c >= '0' && c <= '9'; }
bool combining(std::uint32_t c) { return c >= 0x0300 && c <= 0x036F; }
bool letter(std::uint32_t c) { return word(c) && !digit(c); }

// A whole chain of >= 3 single supported letters, with an optional final dot.
// Reject a chain containing a multi-letter segment, rather than accepting
// a prefix of a dotted hostname or word. The caller never retries at a dot's
// immediate successor, so a long rejected chain is still scanned in O(bytes).
std::size_t dotted_acronym_end(std::string_view input, std::size_t offset) {
    std::size_t cursor = offset, letters = 0;
    while (cursor < input.size()) {
        const auto current = decode(input, cursor);
        if (!letter(current.value)) return 0;
        ++letters;
        const auto end = cursor + current.bytes;
        if (end == input.size()) return letters >= 3 ? end : 0;
        const auto next = decode(input, end);
        if (word(next.value) || combining(next.value)) return 0;
        if (next.value != '.') return letters >= 3 ? end : 0;
        const auto after_dot = end + 1;
        if (after_dot == input.size()) return letters >= 3 ? after_dot : 0;
        const auto following = decode(input, after_dot);
        if (!letter(following.value)) return letters >= 3 ? after_dot : 0;
        cursor = after_dot;
    }
    return 0;
}

// Recognize whole C++ / C# names and optional standard versions (C++17, C#12).
// Reject prefixes of C+++ and C++Builder rather than silently consuming part.
std::size_t language_suffix(std::string_view input, std::size_t offset) {
    std::size_t end = offset;
    if (input[offset] == '#') ++end;
    else if (input[offset] == '+' && offset + 1 < input.size() && input[offset + 1] == '+') end += 2;
    else return 0;
    while (end < input.size() && digit(static_cast<unsigned char>(input[end]))) ++end;
    if (end < input.size()) {
        const auto next = decode(input, end).value;
        if (word(next) || combining(next) || next == '+' || next == '#') return 0;
    }
    return end;
}

std::uint32_t lower(std::uint32_t c) {
    if ((c >= 'A' && c <= 'Z') || (c >= 0x0410 && c <= 0x042F) ||
        (c >= 0x00C0 && c <= 0x00D6) || (c >= 0x00D8 && c <= 0x00DE)) return c + 32;
    if (c == 0x0401) return 0x0451;
    if (c == 0x0178) return 0x00FF;
    if (c == 0x017F) return 's';
    // Extended-A pairs. U+0130 is intentionally preserved (no locale-specific fold).
    if (((c >= 0x0100 && c <= 0x012F) || (c >= 0x0132 && c <= 0x0137) ||
         (c >= 0x014A && c <= 0x0177)) && c % 2 == 0) return c + 1;
    if (((c >= 0x0139 && c <= 0x0148) || (c >= 0x0179 && c <= 0x017E)) && c % 2 == 1)
        return c + 1;
    return c;
}

std::uint32_t connector(std::uint32_t c) {
    if (c == '-' || c == 0x2010 || c == 0x2011) return '-';
    if (c == '\'' || c == 0x2019 || c == 0x02BC) return '\'';
    return 0;
}

void append_utf8(std::string& output, std::uint32_t c) {
    if (c < 0x80) output.push_back(static_cast<char>(c));
    else if (c < 0x800) {
        output.push_back(static_cast<char>(0xC0 | (c >> 6)));
        output.push_back(static_cast<char>(0x80 | (c & 0x3F)));
    } else {
        output.push_back(static_cast<char>(0xE0 | (c >> 12)));
        output.push_back(static_cast<char>(0x80 | ((c >> 6) & 0x3F)));
        output.push_back(static_cast<char>(0x80 | (c & 0x3F)));
    }
}

}  // namespace

void tokenize(std::string_view input, const Consumer& emit) {
    std::string normalized;
    std::uint64_t position = 0;
    std::size_t begin = 0, end = 0, characters = 0;
    enum class Numeric { none, prefix_v, digits };
    Numeric numeric = Numeric::none;
    auto flush = [&]() {
        if (normalized.empty()) return;
        emit({normalized, position++, begin, end, characters});
        normalized.clear();
        characters = 0;
        numeric = Numeric::none;
    };
    for (std::size_t i = 0; i < input.size();) {
        const auto rune = decode(input, i);
        if (normalized.empty() && letter(rune.value) && i + rune.bytes < input.size() &&
            input[i + rune.bytes] == '.' && (i == 0 || input[i - 1] != '.')) {
            const auto acronym_end = dotted_acronym_end(input, i);
            if (acronym_end) {
                begin = i;
                for (std::size_t cursor = i; cursor < acronym_end;) {
                    const auto part = decode(input, cursor);
                    if (part.value != '.') {
                        append_utf8(normalized, lower(part.value));
                        ++characters;
                    }
                    cursor += part.bytes;
                }
                end = acronym_end;
                flush();
                i = acronym_end;
                continue;
            }
        }
        if (word(rune.value) || (!normalized.empty() && combining(rune.value))) {
            if (normalized.empty()) {
                begin = i;
                numeric = digit(rune.value) ? Numeric::digits :
                    (rune.value == 'v' || rune.value == 'V' ? Numeric::prefix_v : Numeric::none);
            } else if (!(digit(rune.value) && (numeric == Numeric::digits || numeric == Numeric::prefix_v))) {
                numeric = Numeric::none;
            } else numeric = Numeric::digits;
            append_utf8(normalized, lower(rune.value));
            ++characters;
            end = i + rune.bytes;
        } else if (normalized == "c" && (rune.value == '+' || rune.value == '#')) {
            const auto suffix_end = language_suffix(input, i);
            if (suffix_end) {
                normalized.append(input.substr(i, suffix_end - i));
                characters += suffix_end - i;  // The entire suffix is ASCII.
                end = suffix_end;
                numeric = Numeric::none;
                i = suffix_end;
                continue;
            }
            flush();
        } else if (rune.value == '.' && numeric == Numeric::digits &&
                   i + 1 < input.size() && digit(decode(input, i + 1).value)) {
            // Numeric dotted sequences, optionally prefixed with v. A final
            // sentence dot and repeated dots remain separators.
            normalized.push_back('.');
            ++characters;
            end = i + 1;
        } else if (!normalized.empty() && connector(rune.value) && i + rune.bytes < input.size()
                   && word(decode(input, i + rune.bytes).value)) {
            append_utf8(normalized, connector(rune.value));
            ++characters;
            end = i + rune.bytes;
            numeric = Numeric::none;
        } else flush();
        i += rune.bytes;
    }
    flush();
}

}  // namespace gaming
