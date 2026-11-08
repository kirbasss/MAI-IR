#pragma once

// No STL containers, strings, hash maps or morphology libraries in this module.
#include <cstddef>
#include <cstdint>

namespace gaming {
struct LemmaView { const char* data; std::size_t size; };
struct LemmaResult {
    LemmaView values[64];
    std::size_t count = 0;
    bool known = false;
};

class Lemmatizer {
public:
    Lemmatizer() = default;
    ~Lemmatizer();
    Lemmatizer(const Lemmatizer&) = delete;
    Lemmatizer& operator=(const Lemmatizer&) = delete;
    bool load(const char* path, const char** error);
    // Input MUST be a normalized gaming-v3 token. Unknown/special tokens survive.
    // Dictionary views live until destruction/reload; fallback borrows the input.
    // No allocation per call. False means excessive ambiguity, never truncation.
    bool analyze(const char* word, std::size_t size, LemmaResult& result) const;
    std::size_t entries() const { return count_; }
    std::size_t memory_bytes() const { return payload_size_ + capacity_ * sizeof(std::uint32_t); }
private:
    unsigned char* payload_ = nullptr;
    const char* strings_ = nullptr;
    std::uint32_t* slots_ = nullptr;
    std::size_t count_ = 0, payload_size_ = 0, capacity_ = 0;
    void clear();
    bool lookup(const char* key, std::size_t size, LemmaResult& out) const;
};
} // namespace gaming
