#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string_view>

namespace gaming {

inline constexpr const char* rules_version = "gaming-v3";

// term is a borrowed view, valid only for the duration of the callback.
// Offsets refer to the ORIGINAL UTF-8 field; position starts at zero per field.
struct Token {
    std::string_view term;
    std::uint64_t position;
    std::size_t begin_byte;
    std::size_t end_byte;
    std::size_t characters;
};

using Consumer = std::function<void(const Token&)>;
void tokenize(std::string_view input, const Consumer& emit);

}  // namespace gaming
