#include "search_filters.hpp"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>

namespace {
constexpr const char* sources[]={"gamemag","eurogamer","gamingonlinux","igromania","ixbt_games","pcgamer","playground","stopgame"};
constexpr const char* types[]={"news","article","review","blog","unknown"};
unsigned choose(const char* value,const char* const* names,unsigned count,const char* error) {
    if (!value || !*value) return 0;
    for (unsigned i=0;i<count;++i) if (!std::strcmp(value,names[i])) return i+1;
    throw error;
}
std::uint64_t number(const unsigned char* p) {
    std::uint64_t n=0; for (unsigned i=0;i<8;++i) n|=std::uint64_t(p[i])<<(8*i); return n;
}
}
gaming::SearchFilters::~SearchFilters() { std::free(rows_); }
bool gaming::SearchFilters::open(const char* path,std::uint64_t documents,const char** error) {
    *error=nullptr;
    FILE* file=std::fopen(path,"rb"); if (!file) { *error="Cannot open search filters"; return false; }
    try {
        unsigned char header[16];
        if (std::fread(header,1,16,file)!=16 || std::memcmp(header,"MAIFLT1\0",8) ||
            number(header+8)!=documents || documents>SIZE_MAX/4) throw "Search filters header/count mismatch";
        rows_=static_cast<unsigned char*>(std::malloc(static_cast<std::size_t>(documents)*4));
        if (!rows_) throw "Cannot allocate search filters";
        count_=documents;
        if (std::fread(rows_,4,static_cast<std::size_t>(count_),file)!=count_ ||
            std::fgetc(file)!=EOF || std::ferror(file)) throw "Search filters file truncated or oversized";
        for (std::uint64_t i=0;i<count_;++i) {
            const auto* row=rows_+i*4;
            if (row[2]<1 || row[2]>5 || row[3]<1 || row[3]>8) throw "Invalid search filter record";
        }
        std::fclose(file); return true;
    } catch (const char* message) {
        std::fclose(file); std::free(rows_); rows_=nullptr; count_=0; *error=message; return false;
    }
}
void gaming::SearchFilters::select(const char* source,const char* type,const char* year) {
    source_=choose(source,sources,8,"Unknown source filter");
    type_=choose(type,types,5,"Unknown publication type filter");
    year_=0;
    if (year && *year) {
        for (const char* p=year;*p;++p) {
            if (*p<'0' || *p>'9' || year_>6553) throw "Invalid publication year filter";
            year_=year_*10+unsigned(*p-'0');
        }
        if (year_<1900 || year_>2100) throw "Invalid publication year filter";
    }
}
bool gaming::SearchFilters::match(std::uint64_t id) const {
    if (!id || id>count_) return false;
    const auto* row=rows_+(id-1)*4;
    return (!year_ || (unsigned(row[0])|(unsigned(row[1])<<8))==year_) &&
           (!type_ || row[2]==type_) && (!source_ || row[3]==source_);
}
