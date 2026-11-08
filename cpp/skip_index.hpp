#pragma once
#include <cstdint>
#include <cstdio>

namespace gaming {
class BooleanIndex;
class PositionalIndex;
class CompactIndex;
struct SkipPoint { std::uint64_t value=0,bytes=0,consumed=0; };
class SkipIndex {
public:
    ~SkipIndex();
    SkipIndex()=default;
    SkipIndex(const SkipIndex&)=delete;
    SkipIndex& operator=(const SkipIndex&)=delete;
    bool open(const char* directory,const char* compact,const BooleanIndex& index,const char** error);
    bool load(unsigned kind,std::uint64_t ordinal,std::uint64_t count,std::uint64_t bytes,
              SkipPoint*& points,std::uint64_t& size,const char** error) const;
    bool verify(const char** error) const;
private:
    FILE* files_[4]{};
    std::uint64_t terms_=0,counts_[2]{};
};
int run_skip_index(int argc,const char* const* argv,FILE* output,FILE* errors);
}
