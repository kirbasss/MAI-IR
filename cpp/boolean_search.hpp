#pragma once
#include "boolean_index.hpp"
#include "lemmatizer.hpp"
#include "positional_index.hpp"

namespace gaming {
// One engine per thread. The index and optional morphology are loaded once.
class BooleanSearch {
public:
    bool open(const char* directory,TermSpace space,const char* dictionary,const char** error,const char* positions=nullptr);
    BooleanIndex& index() { return index_; }
    TermSpace space() const { return space_; }
    const Lemmatizer& morphology() const { return morphology_; }
    PositionalIndex& positions() { return positions_; }
private:
    BooleanIndex index_;
    Lemmatizer morphology_;
    PositionalIndex positions_;
    TermSpace space_=TermSpace::surface;
};
// Own bounded expression tree, streaming set operations. No result set in RAM.
class BooleanQuery {
public:
    BooleanQuery()=default;
    ~BooleanQuery();
    BooleanQuery(const BooleanQuery&)=delete;
    BooleanQuery& operator=(const BooleanQuery&)=delete;
    bool compile(BooleanSearch& engine,const char* query,const char** error);
    // False + null error = end of results; IDs are unique and increasing.
    bool next(std::uint64_t& id,const char** error);
private:
    struct State;
    State* state_=nullptr;
};
int run_boolean_search(int argc,const char* const* argv,FILE* input,FILE* output,FILE* errors);
}
