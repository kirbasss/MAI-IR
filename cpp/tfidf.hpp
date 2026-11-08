#pragma once
#include "boolean_search.hpp"

namespace gaming {
struct RankedHit { std::uint64_t id=0; double score=0; };
struct RankedResult {
    RankedHit hits[1000];
    unsigned count=0,terms=0;
    std::uint64_t total=0,coordinates=0;
};
// lnc.ltc: document log-TF/cosine; query log-TF * IDF/cosine.
// Norms use the entire vocabulary of each namespace, not just query terms.
class Tfidf {
public:
    ~Tfidf();
    Tfidf()=default;
    Tfidf(const Tfidf&)=delete;
    Tfidf& operator=(const Tfidf&)=delete;
    bool open(const char* norms,const char* index,const char* positions,BooleanSearch& engine,const char** error);
    bool search(BooleanSearch& engine,const char* query,unsigned limit,RankedResult& result,const char** error);
private:
    double* norms_=nullptr;
    std::uint64_t documents_=0;
};
int run_tfidf_index(int argc,const char* const* argv,FILE* output,FILE* errors);
}
