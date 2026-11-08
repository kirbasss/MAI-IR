#pragma once
#include "boolean_index.hpp"
#include "lemmatizer.hpp"

namespace gaming {
class CompactIndex;
struct PositionList { std::uint64_t offset=0,count=0,ordinal=UINT64_MAX; };
class PositionalIndex {
public:
    ~PositionalIndex();
    PositionalIndex()=default;
    PositionalIndex(const PositionalIndex&)=delete;
    PositionalIndex& operator=(const PositionalIndex&)=delete;
    bool open(const char* directory,const char* boolean_directory,BooleanIndex& index,const char** error,bool allow_compressed_positions=false);
    bool lookup(const char* term,std::size_t size,TermSpace space,PositionList& list,const char** error);
    bool verify(const char** error);
    bool available() const { return terms_!=nullptr; }
    void use_compressed(const CompactIndex* compressed) { compressed_=compressed; }
    const CompactIndex* compressed() const { return compressed_; }
    FILE* stream(const PositionList& list,const char** error) const;
    BooleanIndex& documents() { return *index_; }
private:
    FILE* terms_=nullptr; BooleanIndex* index_=nullptr;
    char directory_[4096]{};
    std::uint64_t count_=0,occurrences_=0,binding_=0;
    const CompactIndex* compressed_=nullptr;
};
// Coordinates are ordered by document ordinal, field (title=0/body=1), position.
// Ordinals address document_at(); original arbitrary uint64 IDs are preserved.
class PositionCursor {
public:
    ~PositionCursor();
    PositionCursor()=default;
    PositionCursor(const PositionCursor&)=delete;
    PositionCursor& operator=(const PositionCursor&)=delete;
    bool open(const PositionalIndex& index,const PositionList& list,const char** error);
    bool next(std::uint64_t& coordinate,const char** error);
private:
    FILE* file_=nullptr; std::uint64_t remaining_=0,previous_=0,bytes_left_=0,previous_doc_=0,previous_position_=0;
    unsigned previous_field_=0;
    bool compressed_=false;
    bool first_=true;
};
class PhraseQuery {
public:
    PhraseQuery()=default;
    ~PhraseQuery();
    PhraseQuery(const PhraseQuery&)=delete;
    PhraseQuery& operator=(const PhraseQuery&)=delete;
    bool compile(PositionalIndex& index,TermSpace space,const Lemmatizer& dictionary,
                 const char* phrase,std::size_t size,const char** error);
    bool next(std::uint64_t& id,const char** error);
private:
    struct State; State* state_=nullptr;
};
int run_positional_index(int argc,const char* const* argv,FILE* output,FILE* errors);
}
