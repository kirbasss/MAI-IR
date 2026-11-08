#pragma once
#include "boolean_index.hpp"
#include <cstdint>
#include <cstdio>

namespace gaming {
class BooleanIndex;
class PositionalIndex;
enum class CompactKind : unsigned { postings=0, coordinates=1 };

// Each term has a byte offset/length in a separately bound compressed stream.
// Objects retain only metadata FILE positions; cursors own independent streams.
class CompactIndex {
public:
    ~CompactIndex();
    CompactIndex()=default;
    CompactIndex(const CompactIndex&)=delete;
    CompactIndex& operator=(const CompactIndex&)=delete;
    bool open(const char* directory,const char* boolean_directory,const char* position_directory,
              const BooleanIndex& boolean,const PositionalIndex& positions,const char** error);
    FILE* stream(CompactKind kind,std::uint64_t ordinal,std::uint64_t& bytes,const char** error) const;
    bool verify(const char** error);
    bool has_vocabulary() const { return vocabulary_[0]!=nullptr && vocabulary_[1]!=nullptr; }
    bool lookup(const char* term,std::size_t size,TermSpace space,PostingList& result,const char** error) const;
    std::uint64_t postings_bytes() const { return data_bytes_[0]; }
    std::uint64_t coordinate_bytes() const { return data_bytes_[1]; }
private:
    FILE* terms_[2]{};
    FILE* vocabulary_[2]{};
    char directory_[4096]{};
    std::uint64_t count_=0,data_bytes_[2]{},binding_=0;
    std::uint64_t vocabulary_bytes_=0,postings_count_=0;
};

// Lecture-style Variable Byte: the high bit marks the final byte.
bool read_compact_uint(FILE* file,std::uint64_t& remaining,std::uint64_t& result);
// The same codec over an already loaded byte range, for isolated CPU measurements.
bool read_compact_uint(const unsigned char*& data,std::uint64_t& remaining,std::uint64_t& result);
void benchmark_compact_lists(const char* base,const char* positions,const char* compact,
                             const char* term,TermSpace space,unsigned repeats,FILE* output);
int run_compact_index(int argc,const char* const* argv,FILE* output,FILE* errors);
}
