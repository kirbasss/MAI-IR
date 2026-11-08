#pragma once
#include <cstddef>
#include <cstdint>
#include <cstdio>

namespace gaming {
class BooleanSearch;
class BooleanIndex;
struct DocumentInfo;

struct SnippetResult {
    char text[1024]{};
    std::size_t size=0;
    struct Span { std::size_t begin=0,end=0; } matches[64];
    unsigned match_count=0;
    bool left=false,right=false;
};

class SnippetQuery {
public:
    SnippetQuery()=default;
    ~SnippetQuery();
    SnippetQuery(const SnippetQuery&)=delete;
    SnippetQuery& operator=(const SnippetQuery&)=delete;
    void prepare(const char* query,const BooleanSearch& search,bool boolean_mode);
    std::uint64_t match(const char* term,std::size_t size) const;
private:
    struct Term { char* text=nullptr; std::size_t size=0; std::uint64_t group=0; } terms_[512];
    unsigned count_=0,groups_=0;
    const BooleanSearch* search_=nullptr;
    void add(const char* term,std::size_t size,std::uint64_t group);
};

class SnippetStore {
public:
    SnippetStore()=default;
    ~SnippetStore();
    SnippetStore(const SnippetStore&)=delete;
    SnippetStore& operator=(const SnippetStore&)=delete;
    bool open(const char* corpus,std::uint64_t expected_docs,const char** error,const char* offsets=nullptr);
    bool save(const char* output,const char** error) const;
    void make(std::uint64_t id,BooleanIndex& index,const DocumentInfo& document,const SnippetQuery& query,
              SnippetResult& result);
private:
    struct Row { std::uint64_t id,title_offset,title_size,body_offset,body_size; };
    FILE* file_=nullptr;
    Row* rows_=nullptr;
    std::size_t count_=0;
    std::uint64_t corpus_size_=0;
};
}
