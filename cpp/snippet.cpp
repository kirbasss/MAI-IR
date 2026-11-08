#include "snippet.hpp"
#include "boolean_search.hpp"
#include "tokenizer.hpp"
#include <cstring>
#include <cstdlib>
#include <exception>
#include <string_view>
#ifdef _WIN32
#include <io.h>
#endif

namespace {
using U64=std::uint64_t;
constexpr U64 max_field=64ULL*1024*1024;
void seek(FILE* file,U64 offset) {
    if (offset>INT64_MAX) throw "Corpus offset overflow";
#ifdef _WIN32
    if (_fseeki64(file,static_cast<__int64>(offset),SEEK_SET)) throw "Cannot seek corpus";
#else
    if (fseeko(file,static_cast<off_t>(offset),SEEK_SET)) throw "Cannot seek corpus";
#endif
}
U64 tell(FILE* file) {
#ifdef _WIN32
    const auto result=_ftelli64(file);
#else
    const auto result=ftello(file);
#endif
    if (result<0) throw "Cannot tell corpus offset";
    return static_cast<U64>(result);
}
void read(FILE* file,void* data,std::size_t size) {
    if (size && std::fread(data,1,size,file)!=size) throw "Truncated corpus";
}
void write(FILE* file,const void* data,std::size_t size) {
    if (size && std::fwrite(data,1,size,file)!=size) throw "Cannot write snippet offsets";
}
void encode(unsigned char* bytes,U64 value) {
    for (unsigned i=0;i<8;++i) bytes[i]=static_cast<unsigned char>(value>>(8*i));
}
U64 decode(const unsigned char* bytes) {
    U64 value=0; for (unsigned i=0;i<8;++i) value|=U64(bytes[i])<<(8*i); return value;
}
U64 hash(U64 value,const unsigned char* bytes,std::size_t size) {
    for (std::size_t i=0;i<size;++i) { value^=bytes[i]; value*=1099511628211ULL; }
    return value;
}
U64 number(FILE* file,unsigned width=8) {
    unsigned char bytes[8]; read(file,bytes,width); U64 result=0;
    for (unsigned i=0;i<width;++i) result|=U64(bytes[i])<<(8*i);
    return result;
}
bool separator(char c) { return c==' ' || c=='\t' || c=='\r' || c=='\n'; }
unsigned popcount(U64 value) { unsigned n=0; while (value) { value&=value-1; ++n; } return n; }
bool boundary(const char* text,std::size_t at,std::size_t size) {
    const char c=text[at];
    return c=='\n' || ((c=='.' || c=='!' || c=='?') && (at+1==size || separator(text[at+1])));
}
struct Owned {
    char* data;
    explicit Owned(std::size_t size):data(static_cast<char*>(std::malloc(size))) {
        if (!data) throw "Out of memory";
    }
    ~Owned() { std::free(data); }
    Owned(const Owned&)=delete;
    Owned& operator=(const Owned&)=delete;
};
}

gaming::SnippetQuery::~SnippetQuery() { for (auto& term:terms_) std::free(term.text); }
void gaming::SnippetQuery::add(const char* word,std::size_t size,U64 group) {
    if (!size || size>256 || count_==512) return;
    char* copy=static_cast<char*>(std::malloc(size)); if (!copy) throw "Out of memory";
    std::memcpy(copy,word,size); terms_[count_++]={copy,size,group};
}
void gaming::SnippetQuery::prepare(const char* query,const BooleanSearch& search,bool boolean_mode) {
    search_=&search;
    gaming::tokenize(query,[&](const gaming::Token& token) {
        if (groups_==64) return;
        if (boolean_mode && ((token.term=="and") || (token.term=="or") || (token.term=="not"))) return;
        const U64 group=U64(1)<<groups_++;
        if (search.space()==TermSpace::surface) add(token.term.data(),token.term.size(),group);
        else {
            LemmaResult forms;
            if (!search.morphology().analyze(token.term.data(),token.term.size(),forms)) throw "Too many lemma alternatives";
            for (std::size_t i=0;i<forms.count;++i) add(forms.values[i].data,forms.values[i].size,group);
        }
    });
}
U64 gaming::SnippetQuery::match(const char* word,std::size_t size) const {
    if (!search_) return 0;
    LemmaResult forms;
    if (search_->space()==TermSpace::lemma && !search_->morphology().analyze(word,size,forms))
        throw "Too many lemma alternatives";
    U64 result=0;
    for (unsigned i=0;i<count_;++i) {
        const auto& query=terms_[i];
        if (search_->space()==TermSpace::surface) {
            if (query.size==size && !std::memcmp(query.text,word,size)) result|=query.group;
        } else for (std::size_t j=0;j<forms.count;++j)
            if (query.size==forms.values[j].size && !std::memcmp(query.text,forms.values[j].data,query.size))
                { result|=query.group; break; }
    }
    return result;
}

gaming::SnippetStore::~SnippetStore() { if (file_) std::fclose(file_); std::free(rows_); }
bool gaming::SnippetStore::open(const char* corpus,U64 expected_docs,const char** error,const char* offsets) {
    *error=nullptr;
    try {
        file_=std::fopen(corpus,"rb"); if (!file_) throw "Cannot open snippet corpus";
        seek(file_,0); unsigned char magic[8]; read(file_,magic,8);
        if (std::memcmp(magic,"MAIIR3\0\1",8)) throw "Snippet corpus must be MAIIR3";
        seek(file_,0);
#ifdef _WIN32
        if (_fseeki64(file_,0,SEEK_END)) throw "Cannot seek corpus";
#else
        if (fseeko(file_,0,SEEK_END)) throw "Cannot seek corpus";
#endif
        const U64 length=tell(file_); corpus_size_=length; seek(file_,8);
        if (expected_docs>SIZE_MAX/sizeof(Row)) throw "Too many corpus documents";
        rows_=static_cast<Row*>(std::malloc(static_cast<std::size_t>(expected_docs)*sizeof(Row)));
        if (expected_docs && !rows_) throw "Out of memory";
        if (offsets) {
            FILE* directory=std::fopen(offsets,"rb");
            if (!directory) throw "Cannot open snippet offsets";
            try {
                unsigned char header[32]; read(directory,header,sizeof(header));
                if (std::memcmp(header,"MAIIS14\1",8) || decode(header+8)!=length ||
                    decode(header+16)!=expected_docs) throw "Snippet offsets do not match corpus/index";
                U64 digest=14695981039346656037ULL;
                for (U64 i=0;i<expected_docs;++i) {
                    unsigned char bytes[40]; read(directory,bytes,sizeof(bytes));
                    digest=hash(digest,bytes,sizeof(bytes));
                    Row row{decode(bytes),decode(bytes+8),decode(bytes+16),decode(bytes+24),decode(bytes+32)};
                    if (!row.id || (i && row.id<=rows_[i-1].id) || row.title_size>max_field ||
                        row.body_size>max_field || row.title_offset>length || row.body_offset>length ||
                        row.title_size>length-row.title_offset || row.body_size>length-row.body_offset)
                        throw "Invalid snippet offsets";
                    rows_[count_++]=row;
                }
                if (digest!=decode(header+24) || std::fgetc(directory)!=EOF || std::ferror(directory))
                    throw "Snippet offsets checksum or length mismatch";
            } catch (...) { std::fclose(directory); throw; }
            std::fclose(directory); return true;
        }
        U64 previous=0;
        while (tell(file_)<length) {
            if (count_==expected_docs) throw "Snippet corpus has extra documents";
            Row row{}; row.id=number(file_);
            if (row.id<=previous) throw "Snippet IDs must increase";
            previous=row.id;
            const U64 source=number(file_,4); if (!source || source>64 || source>length-tell(file_)) throw "Invalid source length";
            seek(file_,tell(file_)+source);
            row.title_size=number(file_); if (row.title_size>max_field || row.title_size>length-tell(file_)) throw "Invalid title length";
            row.title_offset=tell(file_); seek(file_,row.title_offset+row.title_size);
            row.body_size=number(file_); if (row.body_size>max_field || row.body_size>length-tell(file_)) throw "Invalid body length";
            row.body_offset=tell(file_); seek(file_,row.body_offset+row.body_size);
            rows_[count_++]=row;
        }
        if (count_!=expected_docs) throw "Snippet corpus document count differs from index";
        return true;
    } catch (const char* e) { *error=e; return false; }
      catch (const std::exception&) { *error="Cannot allocate snippet corpus directory"; return false; }
}
bool gaming::SnippetStore::save(const char* output,const char** error) const {
    *error=nullptr;
    try {
        if (!file_) throw "Snippet corpus is not open";
        FILE* existing=std::fopen(output,"rb");
        if (existing) { std::fclose(existing); throw "Snippet offsets already exist"; }
        FILE* file=std::fopen(output,"wb"); if (!file) throw "Cannot create snippet offsets";
        try {
            unsigned char header[32]{}; std::memcpy(header,"MAIIS14\1",8);
            encode(header+8,corpus_size_); encode(header+16,count_);
            write(file,header,sizeof(header));
            U64 digest=14695981039346656037ULL;
            for (std::size_t i=0;i<count_;++i) {
                const Row& row=rows_[i]; unsigned char bytes[40];
                encode(bytes,row.id); encode(bytes+8,row.title_offset); encode(bytes+16,row.title_size);
                encode(bytes+24,row.body_offset); encode(bytes+32,row.body_size);
                digest=hash(digest,bytes,sizeof(bytes)); write(file,bytes,sizeof(bytes));
            }
            encode(header+24,digest); seek(file,0); write(file,header,sizeof(header));
            if (std::fflush(file)) throw "Cannot flush snippet offsets";
        } catch (...) { std::fclose(file); throw; }
        if (std::fclose(file)) throw "Cannot close snippet offsets";
        return true;
    } catch (const char* e) { *error=e; return false; }
}
void gaming::SnippetStore::make(U64 id,BooleanIndex& index,const DocumentInfo& document,const SnippetQuery& query,SnippetResult& result) {
    result={}; std::size_t left=0,right=count_;
    while (left<right) { const std::size_t mid=left+(right-left)/2;
        if (rows_[mid].id<id) left=mid+1; else right=mid; }
    if (left==count_ || rows_[left].id!=id) throw "Snippet document ID absent from corpus";
    const Row& row=rows_[left];
    if (row.title_size!=document.title_size) throw "Snippet corpus title differs from index";
    // Compare exact title bytes as an inexpensive per-result corpus binding.
    Owned title(static_cast<std::size_t>(row.title_size)+1);
    seek(file_,row.title_offset); read(file_,title.data,static_cast<std::size_t>(row.title_size));
    const char* error=nullptr;
    Owned indexed(static_cast<std::size_t>(row.title_size)+1);
    const bool valid=index.text(document.title_offset,static_cast<std::size_t>(row.title_size),indexed.data,&error);
    if (!valid || std::memcmp(title.data,indexed.data,static_cast<std::size_t>(row.title_size)))
        throw "Snippet corpus title differs from index";
    if (!row.body_size) return;
    Owned body_storage(static_cast<std::size_t>(row.body_size)+1);
    char* body=body_storage.data;
    seek(file_,row.body_offset); read(file_,body,static_cast<std::size_t>(row.body_size)); body[row.body_size]=0;
        std::size_t best_begin=0,best_end=0,best_hit=0; double best_score=-1;
        for (std::size_t begin=0;begin<row.body_size;) {
            std::size_t end=begin;
            while (end<row.body_size && !boundary(body,end,static_cast<std::size_t>(row.body_size))) ++end;
            if (end<row.body_size) ++end;
            while (begin<end && separator(body[begin])) ++begin;
            if (begin==end) { begin=end; continue; }
            U64 seen=0; unsigned hits=0; std::size_t first=0,last=0;
            gaming::tokenize(std::string_view(body+begin,end-begin),[&](const gaming::Token& token) {
                const U64 matched=query.match(token.term.data(),token.term.size());
                if (matched) {
                    if (!hits) first=token.begin_byte;
                    last=token.end_byte; ++hits;
                    seen|=matched;
                }
            });
            const double score=hits ? 10000.0*popcount(seen)+100.0*hits*hits/(last-first+1)-double(begin)/10000.0 : -1.0;
            if (score>best_score) { best_score=score; best_begin=begin; best_end=end; best_hit=begin+first; }
            begin=end;
        }
        if (best_score<0) {
            best_begin=0; while (best_begin<row.body_size && separator(body[best_begin])) ++best_begin;
            best_end=best_begin;
            while (best_end<row.body_size && !boundary(body,best_end,static_cast<std::size_t>(row.body_size))) ++best_end;
            if (best_end<row.body_size) ++best_end;
            best_hit=best_begin;
        }
        // The winning sentence identifies the relevant paragraph. Keep its
        // neighboring sentences, but do not cross a paragraph break into
        // recommendations or unrelated site boilerplate.
        std::size_t paragraph_begin=best_begin,paragraph_end=best_end;
        while (paragraph_begin>0 && body[paragraph_begin-1]!='\n' && body[paragraph_begin-1]!='\r') --paragraph_begin;
        while (paragraph_end<row.body_size && body[paragraph_end]!='\n' && body[paragraph_end]!='\r') ++paragraph_end;
        constexpr std::size_t limit=600;
        std::size_t start=paragraph_begin,finish=paragraph_end;
        if (finish-start>limit) {
            start=best_hit>200 ? best_hit-200 : paragraph_begin;
            if (start<paragraph_begin) start=paragraph_begin;
            finish=start+limit;
            if (finish>paragraph_end) { finish=paragraph_end; start=finish-limit; }
            while (start<finish && (static_cast<unsigned char>(body[start])&0xc0)==0x80) ++start;
            while (finish>start && (static_cast<unsigned char>(body[finish])&0xc0)==0x80) --finish;
        }
        while (start<finish && separator(body[start])) ++start;
        while (finish>start && separator(body[finish-1])) --finish;
        result.left=start>0; result.right=finish<row.body_size;
        result.size=finish-start; std::memcpy(result.text,body+start,result.size); result.text[result.size]=0;
        gaming::tokenize(std::string_view(result.text,result.size),[&](const gaming::Token& token) {
            if (result.match_count<64 && query.match(token.term.data(),token.term.size()))
                result.matches[result.match_count++]={token.begin_byte,token.end_byte};
        });
}
