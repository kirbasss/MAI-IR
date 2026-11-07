#include "lemma_search.hpp"
#include "lemmatizer.hpp"
#include "tokenizer.hpp"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>

namespace {
constexpr unsigned max_queries=64, max_terms=32, max_hits=100;
struct File {
    FILE* value=nullptr;
    explicit File(const char* path) : value(std::fopen(path,"rb")) { if (!value) throw "Cannot open input"; }
    ~File() { std::fclose(value); }
};
struct Text {
    char* value=nullptr; std::size_t size=0;
    ~Text() { std::free(value); }
    void resize(std::size_t n) {
        if (n>64*1024*1024) throw "Field size limit exceeded";
        void* p=std::realloc(value,n+1); if (!p) throw "Allocation failed";
        value=static_cast<char*>(p); size=n; value[n]=0;
    }
};
struct Term {
    char* word=nullptr; std::size_t size=0; gaming::LemmaResult lemmas;
    ~Term() { std::free(word); }
};
struct Hit { std::uint64_t id=0; unsigned score=0, exact_terms=0, title_terms=0; };
struct Query {
    char id[64]{}; Term terms[max_terms]; unsigned count=0;
    bool exact[max_terms]{}, lemma[max_terms]{}, exact_title[max_terms]{}, lemma_title[max_terms]{};
    Hit hits[3][max_hits]{}; unsigned used[3]{}; std::uint64_t found[3]{};
};
struct Queries {
    // Own storage, no STL collection. new[] calls Term destructors on failure.
    Query* data=new Query[max_queries]; unsigned size=0;
    ~Queries() { delete[] data; }
};
bool same(const char* a,std::size_t an,const char* b,std::size_t bn) {
    return an==bn && !std::memcmp(a,b,an);
}
bool intersects(const gaming::LemmaResult& a,const gaming::LemmaResult& b) {
    for (std::size_t i=0;i<a.count;++i) for (std::size_t j=0;j<b.count;++j)
        if (same(a.values[i].data,a.values[i].size,b.values[j].data,b.values[j].size)) return true;
    return false;
}
void exact(FILE* f,char* p,std::size_t n) { if (n && std::fread(p,1,n,f)!=n) throw "Truncated corpus"; }
std::uint64_t integer(FILE* f,unsigned size) {
    unsigned char p[8]{}; exact(f,reinterpret_cast<char*>(p),size);
    std::uint64_t n=0; for (unsigned i=0;i<size;++i) n|=std::uint64_t(p[i])<<(8*i); return n;
}
void read_field(FILE* f,Text& text) {
    const auto n=integer(f,8); if (n>64*1024*1024) throw "Field size limit exceeded";
    text.resize(static_cast<std::size_t>(n)); exact(f,text.value,text.size);
}
void load_queries(const char* path,Queries& queries,gaming::Lemmatizer& dict) {
    File f(path); char line[8194];
    while (std::fgets(line,sizeof(line),f.value)) {
        std::size_t n=std::strlen(line);
        if (!n || (line[n-1]!='\n' && !std::feof(f.value))) throw "Query line too long";
        while (n && (line[n-1]=='\n' || line[n-1]=='\r')) line[--n]=0;
        if (!n || line[0]=='#') continue;
        if (queries.size==max_queries) throw "Too many queries (maximum 64)";
        char* tab=std::strchr(line,'\t');
        if (!tab || tab==line || tab-line>=63 || std::strchr(tab+1,'\t')) throw "Expected query_id TAB query";
        for (char* p=line;p<tab;++p)
            if (!((*p>='a' && *p<='z') || (*p>='A' && *p<='Z') || (*p>='0' && *p<='9') || *p=='_' || *p=='-'))
                throw "Invalid query ID";
        *tab=0;
        for (unsigned q=0;q<queries.size;++q) if (!std::strcmp(queries.data[q].id,line)) throw "Duplicate query ID";
        Query& query=queries.data[queries.size++]; std::strcpy(query.id,line);
        gaming::tokenize(std::string_view(tab+1,n-static_cast<std::size_t>(tab+1-line)),[&](const gaming::Token& token) {
            for (unsigned i=0;i<query.count;++i)
                if (same(query.terms[i].word,query.terms[i].size,token.term.data(),token.term.size())) return;
            if (query.count==max_terms) throw "Too many query terms (maximum 32)";
            Term& t=query.terms[query.count++]; t.size=token.term.size();
            t.word=static_cast<char*>(std::malloc(t.size+1)); if (!t.word) throw "Query allocation failed";
            std::memcpy(t.word,token.term.data(),t.size); t.word[t.size]=0;
            // Analyze the OWNED word: unknown lemma views must not borrow a
            // tokenizer callback's temporary normalized buffer.
            if (!dict.analyze(t.word,t.size,t.lemmas)) throw "Excessive query ambiguity";
        });
        if (!query.count) throw "Query has no tokens";
    }
    if (std::ferror(f.value)) throw "Cannot read queries";
    if (!queries.size) throw "No queries";
}
void scan_field(const Text& text,Queries& queries,gaming::Lemmatizer& dict,bool title) {
    gaming::tokenize(std::string_view(text.value,text.size),[&](const gaming::Token& token) {
        gaming::LemmaResult lemmas;
        if (!dict.analyze(token.term.data(),token.term.size(),lemmas)) throw "Excessive document ambiguity";
        for (unsigned q=0;q<queries.size;++q) {
            auto& query=queries.data[q];
            for (unsigned i=0;i<query.count;++i) {
                const auto& t=query.terms[i];
                if (same(t.word,t.size,token.term.data(),token.term.size())) {
                    query.exact[i]=true; if (title) query.exact_title[i]=true;
                }
                if (intersects(t.lemmas,lemmas)) {
                    query.lemma[i]=true; if (title) query.lemma_title[i]=true;
                }
            }
        }
    });
}
void insert(Query& q,unsigned mode,Hit hit,unsigned k) {
    ++q.found[mode]; unsigned position=0;
    while (position<q.used[mode] &&
           (q.hits[mode][position].score>hit.score ||
            (q.hits[mode][position].score==hit.score && q.hits[mode][position].id<hit.id))) ++position;
    if (position>=k) return;
    unsigned end=q.used[mode]<k ? q.used[mode]++ : k-1;
    while (end>position) { q.hits[mode][end]=q.hits[mode][end-1]; --end; }
    q.hits[mode][position]=hit;
}
void finish_document(Queries& queries,std::uint64_t id,unsigned k) {
    for (unsigned q=0;q<queries.size;++q) {
        auto& query=queries.data[q]; unsigned exact_count=0, lemma_count=0, et=0, lt=0;
        for (unsigned i=0;i<query.count;++i) {
            exact_count+=query.exact[i]; lemma_count+=query.lemma[i];
            et+=query.exact_title[i]; lt+=query.lemma_title[i];
        }
        // AND between query words; OR between a word's lemma alternatives.
        // Exact and lemma modes use IDENTICAL title-based ranking and ID ties.
        if (exact_count==query.count) insert(query,0,{id,et*(query.count+1),exact_count,et},k);
        if (lemma_count==query.count) {
            insert(query,1,{id,lt*(query.count+1),exact_count,lt},k);
            // Bonus is only a tie-break within the same title coverage group.
            insert(query,2,{id,lt*(query.count+1)+exact_count,exact_count,lt},k);
        }
    }
}
unsigned positive(const char* value) {
    unsigned n=0;
    for (const char* p=value;*p;++p) {
        if (*p<'0' || *p>'9' || n>100) throw "Invalid --top-k";
        n=n*10+unsigned(*p-'0');
    }
    if (!n || n>max_hits) throw "--top-k must be in 1..100";
    return n;
}
}

int gaming::run_lemma_search(int argc,const char* const* argv,FILE* output,FILE* errors) {
    try {
        if (argc==2 && !std::strcmp(argv[1],"--help")) {
            std::fputs("lemma_search --corpus FILE --dict FILE --queries TSV [--top-k 5] [--field title|both]\n"
                       "UTF-8. Streaming diagnostic search; no inverted index or STL containers.\n"
                       "TSV query file: unique ASCII ID, TAB, query text. Three modes: exact, lemma, hybrid.\n",output); return 0;
        }
        const char *corpus=nullptr,*dictionary=nullptr,*query_path=nullptr,*field="both";
        unsigned k=5; bool got_k=false,got_field=false;
        for (int i=1;i<argc;) {
            const char* option=argv[i++]; if (i==argc) throw "Missing option value";
            const char* value=argv[i++];
            if (!std::strcmp(option,"--corpus") && !corpus) corpus=value;
            else if (!std::strcmp(option,"--dict") && !dictionary) dictionary=value;
            else if (!std::strcmp(option,"--queries") && !query_path) query_path=value;
            else if (!std::strcmp(option,"--top-k") && !got_k) { k=positive(value); got_k=true; }
            else if (!std::strcmp(option,"--field") && !got_field) { field=value; got_field=true; }
            else throw "Unknown or duplicate option";
        }
        if (!corpus || !dictionary || !query_path) throw "Missing --corpus, --dict or --queries";
        if (std::strcmp(field,"title") && std::strcmp(field,"both")) throw "Invalid --field";
        Lemmatizer dict; const char* error=nullptr;
        if (!dict.load(dictionary,&error)) throw error;
        Queries queries; load_queries(query_path,queries,dict);
        File input(corpus); char magic[8]; exact(input.value,magic,8);
        if (std::memcmp(magic,"MAIIR3\0\1",8)) throw "Invalid corpus magic";
        std::uint64_t previous=0, documents=0; Text text;
        while (true) {
            const int c=std::fgetc(input.value);
            if (c==EOF) { if (std::ferror(input.value)) throw "Cannot read corpus"; break; }
            if (std::ungetc(c,input.value)==EOF) throw "Cannot read corpus prefix";
            const auto id=integer(input.value,8); if (id<=previous) throw "Document IDs must increase"; previous=id;
            const auto size=integer(input.value,4); if (!size || size>64) throw "Invalid source size";
            char source[64]; exact(input.value,source,static_cast<std::size_t>(size));
            for (std::size_t i=0;i<size;++i)
                if (!((source[i]>='a' && source[i]<='z') ||
                    (i && ((source[i]>='0' && source[i]<='9') || source[i]=='_')))) throw "Invalid source";
            for (unsigned q=0;q<queries.size;++q) {
                auto& query=queries.data[q];
                std::memset(query.exact,0,sizeof(query.exact)); std::memset(query.lemma,0,sizeof(query.lemma));
                std::memset(query.exact_title,0,sizeof(query.exact_title)); std::memset(query.lemma_title,0,sizeof(query.lemma_title));
            }
            read_field(input.value,text); scan_field(text,queries,dict,true);
            read_field(input.value,text); if (!std::strcmp(field,"both")) scan_field(text,queries,dict,false);
            finish_document(queries,id,k); ++documents;
        }
        // Publish stdout only AFTER successful validation/processing of the corpus.
        std::fputs("query_id\tmode\trank\tdocument_id\tscore\texact_terms\ttitle_terms\n",output);
        const char* names[]={"exact","lemma","hybrid"};
        for (unsigned q=0;q<queries.size;++q) for (unsigned mode=0;mode<3;++mode) {
            const auto& query=queries.data[q];
            for (unsigned i=0;i<query.used[mode];++i) {
                const auto& hit=query.hits[mode][i];
                std::fprintf(output,"%s\t%s\t%u\t%llu\t%u\t%u\t%u\n",query.id,names[mode],i+1,
                             static_cast<unsigned long long>(hit.id),hit.score,hit.exact_terms,hit.title_terms);
            }
            std::fprintf(errors,"[search] %s %s matches=%llu\n",query.id,names[mode],
                         static_cast<unsigned long long>(query.found[mode]));
        }
        if (std::fflush(output) || std::ferror(output)) throw "Cannot write results";
        std::fprintf(errors,"[search] documents=%llu; field=%s; top_k=%u\n",static_cast<unsigned long long>(documents),field,k);
        return 0;
    } catch (const char* error) { std::fprintf(errors,"[lemma-search] %s\n",error); return 1; }
      catch (const std::exception& error) { std::fprintf(errors,"[lemma-search] %s\n",error.what()); return 1; }
}
