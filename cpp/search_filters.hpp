#pragma once
#include <cstdint>

namespace gaming {
class SearchFilters {
public:
    ~SearchFilters();
    SearchFilters()=default;
    SearchFilters(const SearchFilters&)=delete;
    SearchFilters& operator=(const SearchFilters&)=delete;
    bool open(const char* path,std::uint64_t documents,const char** error);
    void select(const char* source,const char* type,const char* year);
    bool match(std::uint64_t id) const;
private:
    unsigned char* rows_=nullptr;
    std::uint64_t count_=0;
    unsigned source_=0,type_=0,year_=0;
};
}
