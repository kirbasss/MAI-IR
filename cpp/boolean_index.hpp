#pragma once
#include <cstddef>
#include <cstdint>
#include <cstdio>

namespace gaming {
class CompactIndex;
enum class TermSpace : std::uint32_t { surface=0, lemma=1 };
struct PostingList { std::uint64_t offset=0, count=0, ordinal=UINT64_MAX; };
struct DocumentInfo {
    std::uint64_t id=0, title_offset=0, title_size=0, url_offset=0, url_size=0, source_offset=0, source_size=0;
};
// Own on-disk binary search. No complete vocabulary or postings arrays in RAM.
class BooleanIndex {
public:
    BooleanIndex()=default;
    ~BooleanIndex();
    BooleanIndex(const BooleanIndex&)=delete;
    BooleanIndex& operator=(const BooleanIndex&)=delete;
    bool open(const char* directory,const char** error,bool allow_compressed_postings=false);
    bool lookup(const char* term,std::size_t size,TermSpace space,PostingList& result,const char** error,
                std::uint64_t* term_number=nullptr);
    bool document(std::uint64_t id,DocumentInfo& result,const char** error);
    bool document_at(std::uint64_t position,DocumentInfo& result,const char** error);
    bool text(std::uint64_t offset,std::size_t size,char* destination,const char** error);
    bool verify(const char** error);
    std::uint64_t documents() const { return docs_; }
    std::uint64_t terms() const { return terms_; }
    std::uint64_t postings() const { return postings_; }
    std::uint64_t dictionary_hash() const { return dictionary_hash_; }
    bool has_lemmas() const { return (flags_&1)!=0; }
    void use_compressed(const CompactIndex* compressed) { compressed_=compressed; }
    const CompactIndex* compressed() const { return compressed_; }
    // Returns an independent binary stream; caller owns fclose().
    FILE* open_postings(const PostingList& list,const char** error) const;
private:
    FILE *term_file_=nullptr,*doc_file_=nullptr,*string_file_=nullptr;
    char directory_[4096]{};
    std::uint64_t docs_=0,terms_=0,postings_=0,strings_=0,dictionary_hash_=0,flags_=0;
    const CompactIndex* compressed_=nullptr;
    void close();
};
// Iterator keeps only the current ID; each iterator owns its stream.
class PostingCursor {
public:
    ~PostingCursor();
    PostingCursor()=default;
    PostingCursor(const PostingCursor&)=delete;
    PostingCursor& operator=(const PostingCursor&)=delete;
    bool open(const BooleanIndex& index,const PostingList& list,const char** error);
    bool next(std::uint64_t& id,const char** error);
    std::uint64_t remaining() const { return remaining_; }
private:
    FILE* file_=nullptr; std::uint64_t remaining_=0,previous_=0,bytes_left_=0;
    bool compressed_=false;
};
int run_boolean_index(int argc,const char* const* argv,FILE* output,FILE* errors);
}
