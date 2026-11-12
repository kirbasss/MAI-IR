#include "boolean_search.hpp"
#include "tfidf.hpp"
#include "snippet.hpp"
#include "search_filters.hpp"
#include "tokenizer.hpp"
#include <chrono>
#include <cstring>
#include <cstdlib>
#include <exception>
#include <new>
#ifdef _WIN32
#include <windows.h>
#include <io.h>
#include <fcntl.h>
#endif

namespace {
using U64=std::uint64_t;
constexpr unsigned max_nodes=1024,max_depth=64,max_operands=64;
constexpr std::size_t max_query=8192;
FILE* temporary_file() {
#ifdef _WIN32
    // MinGW tmpfile() may try the root of the current drive instead of TEMP.
    wchar_t directory[MAX_PATH+1],name[MAX_PATH+1];
    const DWORD n=GetTempPathW(MAX_PATH,directory);
    if (!n || n>MAX_PATH || !GetTempFileNameW(directory,L"ir8",0,name)) return nullptr;
    const int fd=_wopen(name,_O_RDWR|_O_BINARY|_O_TEMPORARY);
    if (fd<0) { DeleteFileW(name); return nullptr; }
    FILE* file=_fdopen(fd,"w+b"); if (!file) _close(fd); return file;
#else
    return std::tmpfile();
#endif
}
void check(bool ok,const char* const& error) { if (!ok) throw (error ? error : "Index operation failed"); }
bool whitespace(char c) { return c==' ' || c=='\t' || c=='\r' || c=='\n'; }
U64 hash_file(const char* path) {
    FILE* file=std::fopen(path,"rb"); if (!file) throw "Cannot open dictionary";
    unsigned char block[65536]; U64 hash=14695981039346656037ULL; std::size_t n;
    while ((n=std::fread(block,1,sizeof(block),file))) for (std::size_t i=0;i<n;++i) { hash^=block[i]; hash*=1099511628211ULL; }
    const bool failed=std::ferror(file)!=0; std::fclose(file); if (failed) throw "Cannot read dictionary";
    return hash;
}
// Validate even text outside operands (tokenizer would otherwise only see operands).
bool utf8(const char* p) {
    const auto* s=reinterpret_cast<const unsigned char*>(p);
    while (*s) {
        unsigned c=*s++,n=0,min=0;
        if (c<128) { if (c<32 && !whitespace(static_cast<char>(c))) return false; continue; }
        if (c>=0xc2 && c<=0xdf) { c&=31; n=1; min=128; }
        else if (c>=0xe0 && c<=0xef) { c&=15; n=2; min=2048; }
        else if (c>=0xf0 && c<=0xf4) { c&=7; n=3; min=65536; }
        else return false;
        for (unsigned i=0;i<n;++i) { if ((*s&0xc0)!=0x80) return false; c=(c<<6)|(*s++&63); }
        if (c<min || c>0x10ffff || (c>=0xd800 && c<=0xdfff)) return false;
    }
    return true;
}
void json(FILE* out,const char* p,std::size_t size) {
    std::fputc('"',out);
    for (std::size_t i=0;i<size;++i) {
        const auto c=static_cast<unsigned char>(p[i]);
        if (c=='"' || c=='\\') { std::fputc('\\',out); std::fputc(c,out); }
        else if (c<32) std::fprintf(out,"\\u%04x",unsigned(c));
        else std::fputc(c,out);
    }
    std::fputc('"',out);
}
struct Text {
    char* data=nullptr;
    ~Text() { std::free(data); }
    void read(gaming::BooleanIndex& index,U64 offset,U64 size) {
        if (size>SIZE_MAX-1) throw "Metadata too large";
        void* p=std::realloc(data,static_cast<std::size_t>(size)+1); if (!p) throw "Out of memory";
        data=static_cast<char*>(p); const char* error=nullptr;
        check(index.text(offset,static_cast<std::size_t>(size),data,&error),error); data[size]=0;
    }
};
void print_document(FILE* out,gaming::BooleanIndex& index,U64 id,double score=-1,
                    gaming::SnippetStore* snippets=nullptr,const gaming::SnippetQuery* terms=nullptr) {
    const char* error=nullptr; gaming::DocumentInfo d;
    check(index.document(id,d,&error),error); if (!d.id) throw "Result ID absent from index";
    std::fprintf(out,"{\"id\":%llu",static_cast<unsigned long long>(id)); Text t;
    if (score>=0) std::fprintf(out,",\"score\":%.12g",score);
    const char* names[]={"title","url","source"};
    const U64 offsets[]={d.title_offset,d.url_offset,d.source_offset},sizes[]={d.title_size,d.url_size,d.source_size};
    for (unsigned i=0;i<3;++i) { t.read(index,offsets[i],sizes[i]); std::fprintf(out,",\"%s\":",names[i]); json(out,t.data,static_cast<std::size_t>(sizes[i])); }
    if (snippets && terms) {
        gaming::SnippetResult result; snippets->make(id,index,d,*terms,result);
        std::fputs(",\"snippet\":",out); json(out,result.text,result.size);
        std::fputs(",\"snippet_matches\":[",out);
        for (unsigned i=0;i<result.match_count;++i) {
            if (i) std::fputc(',',out);
            std::fprintf(out,"[%zu,%zu]",result.matches[i].begin,result.matches[i].end);
        }
        std::fprintf(out,"],\"snippet_left\":%s,\"snippet_right\":%s",
                     result.left ? "true" : "false",result.right ? "true" : "false");
    }
    std::fputc('}',out);
}
}

bool gaming::BooleanSearch::open(const char* directory,TermSpace space,const char* dictionary,const char** error,const char* positions,const char* compact,const char* skips,bool optimized) {
    *error=nullptr;
    try {
        optimized_=optimized; stats_={};
        check(index_.open(directory,error,compact!=nullptr),*error);
        if (space==TermSpace::lemma) {
            if (!index_.has_lemmas()) throw "Index has no lemma namespace";
            if (!dictionary) throw "Lemma search requires --dict used to build the index";
            if (hash_file(dictionary)!=index_.dictionary_hash()) throw "Dictionary fingerprint differs from index";
            check(morphology_.load(dictionary,error),*error);
        }
        if (positions) check(positions_.open(positions,directory,index_,error,compact!=nullptr),*error);
        if (compact) {
            if (!positions) throw "Compressed search requires --positions DIR";
            check(compact_.open(compact,directory,positions,index_,positions_,error),*error);
            index_.use_compressed(&compact_); positions_.use_compressed(&compact_);
        }
        if (skips) {
            if (!compact) throw "Skip search requires --compact DIR";
            check(skips_.open(skips,compact,index_,error),*error);
            if (optimized) index_.use_skips(&skips_);
        }
        index_.set_stats(&stats_);
        space_=space; return true;
    } catch (const char* e) { *error=e; return false; }
}

struct gaming::BooleanQuery::State {
    enum Kind { term,phrase,universe,intersection,unite,difference };
    struct Node {
        Kind kind=term; int left=-1,right=-1;
        PostingCursor cursor;
        PhraseQuery* phrase_query=nullptr;
        ~Node() { delete phrase_query; }
        U64 position=0,head=0;
        bool ready=false,ended=false;
    };
    enum Lexeme { end,word,quoted,land,lor,lnot,lp,rp };
    BooleanSearch& engine;
    Node nodes[max_nodes]; unsigned used=0,operands=0;
    int root=-1; const char* position; const char* start=nullptr; std::size_t size=0; Lexeme lex=end;
    explicit State(BooleanSearch& e,const char* query):engine(e),position(query) {}
    int node(Kind kind,int left=-1,int right=-1) {
        if (used==max_nodes) throw "Query expands to too many nodes (maximum 1024)";
        const int id=static_cast<int>(used++); nodes[id].kind=kind; nodes[id].left=left; nodes[id].right=right; return id;
    }
    void scan() {
        while (whitespace(*position)) ++position;
        start=position; size=1;
        switch (*position) {
        case 0: lex=end; size=0; return;
        case '(': lex=lp; ++position; return;
        case ')': lex=rp; ++position; return;
        case '&': lex=land; ++position; return;
        case '|': lex=lor; ++position; return;
        case '!': lex=lnot; ++position; return;
        case '"':
            start=++position;
            while (*position && *position!='"') ++position;
            if (!*position) throw "Missing closing quote";
            size=static_cast<std::size_t>(position-start); ++position; lex=quoted; return;
        default: break;
        }
        while (*position && !whitespace(*position) && !std::strchr("()&|!\"",*position)) ++position;
        size=static_cast<std::size_t>(position-start); lex=word;
        if (size==3 && !std::memcmp(start,"AND",3)) lex=land;
        if (size==2 && !std::memcmp(start,"OR",2)) lex=lor;
        if (size==3 && !std::memcmp(start,"NOT",3)) lex=lnot;
    }
    int leaf(const char* text,std::size_t length) {
        const char* error=nullptr; PostingList list;
        check(engine.index().lookup(text,length,engine.space(),list,&error),error);
        const int n=node(term); check(nodes[n].cursor.open(engine.index(),list,&error),error); return n;
    }
    int alternatives(const LemmaResult& r,std::size_t begin,std::size_t finish) {
        if (finish-begin==1) return leaf(r.values[begin].data,r.values[begin].size);
        const auto mid=begin+(finish-begin)/2;
        const int a=alternatives(r,begin,mid),b=alternatives(r,mid,finish); return node(unite,a,b);
    }
    int atom() {
        if (++operands>max_operands) throw "Too many operands (maximum 64)";
        if (lex==quoted) {
            const int n=node(phrase); nodes[n].phrase_query=new PhraseQuery; const char* error=nullptr;
            check(nodes[n].phrase_query->compile(engine.positions(),engine.space(),engine.morphology(),start,size,&error,engine.optimized()),error);
            scan(); return n;
        }
        char normalized[max_query+1]; std::size_t length=0; unsigned count=0;
        gaming::tokenize(std::string_view(start,size),[&](const Token& t) {
            if (++count!=1 || t.term.size()>max_query) throw "Each operand must produce one token; join words with AND";
            length=t.term.size(); std::memcpy(normalized,t.term.data(),length);
        });
        if (!count) throw "Operand does not contain a searchable token";
        int n;
        if (engine.space()==TermSpace::lemma) {
            LemmaResult result;
            if (!engine.morphology().analyze(normalized,length,result) || !result.count) throw "Cannot analyze query token";
            n=alternatives(result,0,result.count);
        } else n=leaf(normalized,length);
        scan(); return n;
    }
    int unary(unsigned depth) {
        if (depth>max_depth) throw "Query nesting too deep (maximum 64)";
        if (lex==lnot) { scan(); const int child=unary(depth+1),all=node(universe); return node(difference,all,child); }
        if (lex==lp) { scan(); const int n=expression(depth+1); if (lex!=rp) throw "Missing closing parenthesis"; scan(); return n; }
        if (lex!=word && lex!=quoted) throw "Expected a word, quoted phrase, NOT or opening parenthesis";
        return atom();
    }
    int conjunction(unsigned depth) {
        int n=unary(depth);
        while (lex==land) {
            scan(); const int b=unary(depth);
            if (engine.optimized() && nodes[b].kind==difference && nodes[nodes[b].left].kind==universe)
                n=node(difference,n,nodes[b].right);
            else if (engine.optimized() && nodes[n].kind==difference && nodes[nodes[n].left].kind==universe)
                n=node(difference,b,nodes[n].right);
            else n=node(intersection,n,b);
        }
        return n;
    }
    int expression(unsigned depth) {
        int n=conjunction(depth);
        while (lex==lor) { scan(); const int b=conjunction(depth); n=node(unite,n,b); }
        return n;
    }
    bool fill(int id) {
        Node& n=nodes[id]; if (n.ready) return true; if (n.ended) return false;
        U64 value=0; const char* error=nullptr; bool found=false;
        if (n.kind==term) { found=n.cursor.next(value,&error); if (error) throw error; }
        else if (n.kind==phrase) { found=n.phrase_query->next(value,&error); if (error) throw error; }
        else if (n.kind==universe) {
            if (n.position<engine.index().documents()) {
                DocumentInfo d; check(engine.index().document_at(n.position++,d,&error),error);
                ++engine.stats().universe_documents;
                if (!d.id) throw "Invalid universe document";
                value=d.id; found=true;
            }
        } else {
            for (;;) {
                const bool a=fill(n.left),b=fill(n.right); Node& l=nodes[n.left]; Node& r=nodes[n.right];
                if (n.kind==unite) {
                    if (!a && !b) break;
                    value=(!b || (a && l.head<r.head)) ? l.head : r.head;
                    if (a && l.head==value) l.ready=false;
                    if (b && r.head==value) r.ready=false;
                    found=true; break;
                }
                if (!a || (n.kind==intersection && !b)) break;
                if (n.kind==difference && (!b || l.head<r.head)) { value=l.head; l.ready=false; found=true; break; }
                if (l.head==r.head) {
                    value=l.head; l.ready=r.ready=false;
                    if (n.kind==intersection) { found=true; break; }
                } else if (l.head<r.head) {
                    if (engine.optimized()) advance(n.left,r.head); else l.ready=false;
                } else {
                    if (engine.optimized()) advance(n.right,l.head); else r.ready=false;
                }
            }
        }
        n.ready=found; n.ended=!found; n.head=value; return found;
    }
    bool advance(int id,U64 target) {
        Node& n=nodes[id];
        if (n.ended) return false;
        if (n.ready && n.head>=target) return true;
        n.ready=false; const char* error=nullptr;
        if (n.kind==term || n.kind==phrase) {
            const bool found=n.kind==term ? n.cursor.advance(target,n.head,&error) : n.phrase_query->advance(target,n.head,&error);
            if (error) throw error;
            n.ready=found; n.ended=!found; return found;
        }
        if (n.kind==universe) {
            U64 left=n.position,right=engine.index().documents();
            while (left<right) {
                const U64 mid=left+(right-left)/2; DocumentInfo d;
                check(engine.index().document_at(mid,d,&error),error);
                if (d.id<target) left=mid+1; else right=mid;
            }
            n.position=left;
        } else {
            advance(n.left,target); advance(n.right,target);
        }
        return fill(id);
    }
};
gaming::BooleanQuery::~BooleanQuery() { delete state_; }
bool gaming::BooleanQuery::compile(BooleanSearch& engine,const char* query,const char** error) {
    delete state_; state_=nullptr; *error=nullptr;
    try {
        if (!query || std::strlen(query)>max_query) throw "Query is too long (maximum 8192 UTF-8 bytes)";
        if (!utf8(query)) throw "Invalid query UTF-8 or control character";
        state_=new State(engine,query); state_->scan(); state_->root=state_->expression(0);
        if (state_->lex!=State::end) throw "Unexpected token; use explicit AND/OR between operands";
        return true;
    } catch (const char* e) { *error=e; }
      catch (const std::exception&) { *error="Cannot tokenize query or allocate memory"; }
    delete state_; state_=nullptr; return false;
}
bool gaming::BooleanQuery::next(U64& id,const char** error) {
    *error=nullptr; id=0;
    try {
        if (!state_) throw "Query is not compiled";
        if (!state_->fill(state_->root)) return false;
        auto& n=state_->nodes[state_->root]; id=n.head; n.ready=false; return true;
    } catch (const char* e) { *error=e; return false; }
}

namespace {
void run_query(gaming::BooleanSearch& engine,const char* query,unsigned limit,FILE* output,bool stats=false,
               gaming::Tfidf* tfidf=nullptr,gaming::SnippetStore* snippets=nullptr,
               const gaming::SearchFilters* filters=nullptr) {
    engine.stats()={};
    const auto begin=std::chrono::steady_clock::now();
    gaming::BooleanQuery search; const char* error=nullptr;
    if (!utf8(query)) throw "Query must be valid UTF-8";
    // At most 1000 displayed IDs, independent of total result size.
    U64 ids[1000],id=0,total=0,digest=14695981039346656037ULL;
    gaming::RankedResult ranked;
    gaming::SnippetQuery terms;
    if (snippets) terms.prepare(query,engine,tfidf==nullptr);
    if (tfidf) {
        check(tfidf->search(engine,query,limit,ranked,&error,filters),error); total=ranked.total;
    } else {
        check(search.compile(engine,query,&error),error);
        while (search.next(id,&error)) {
            if (filters && !filters->match(id)) continue;
            if (total<limit) ids[total]=id;
            ++total;
            digest=(digest^id)*1099511628211ULL;
        }
        if (error) throw error;
    }
    const double ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-begin).count();
    // Buffer one response so a metadata/read error cannot leave half a JSON line.
    FILE* buffer=temporary_file(); if (!buffer) throw "Cannot create response buffer";
    try {
        std::fputs("{\"query\":",buffer); json(buffer,query,std::strlen(query));
        std::fprintf(buffer,",\"space\":\"%s\",\"total\":%llu,\"elapsed_ms\":%.3f,\"results\":[",
                     engine.space()==gaming::TermSpace::lemma ? "lemma" : "surface",static_cast<unsigned long long>(total),ms);
        for (U64 i=0;i<total && i<limit;++i) {
            if (i) std::fputc(',',buffer);
            if (tfidf) print_document(buffer,engine.index(),ranked.hits[i].id,ranked.hits[i].score,snippets,&terms);
            else print_document(buffer,engine.index(),ids[i],-1,snippets,&terms);
        }
        std::fputc(']',buffer);
        if (tfidf) std::fprintf(buffer,",\"ranking\":\"tfidf\",\"weighting\":\"lnc.ltc\",\"query_terms\":%u,\"decoded_coordinates\":%llu",ranked.terms,static_cast<unsigned long long>(ranked.coordinates));
        if (stats && !tfidf) {
            const auto& s=engine.stats();
            std::fprintf(buffer,",\"stats\":{\"decoded_postings\":%llu,\"decoded_coordinates\":%llu,\"jumps\":%llu,\"skipped\":%llu,\"universe_documents\":%llu,\"result_digest\":\"%016llx\"}",
                         static_cast<unsigned long long>(s.decoded_postings),static_cast<unsigned long long>(s.decoded_coordinates),
                         static_cast<unsigned long long>(s.jumps),static_cast<unsigned long long>(s.skipped),static_cast<unsigned long long>(s.universe_documents),static_cast<unsigned long long>(digest));
        }
        std::fputs("}\n",buffer);
        if (std::ferror(buffer) || std::fflush(buffer)) throw "Cannot buffer response";
        std::rewind(buffer); char block[8192]; std::size_t n;
        while ((n=std::fread(block,1,sizeof(block),buffer))) if (std::fwrite(block,1,n,output)!=n) throw "Cannot write response";
        if (std::ferror(buffer) || std::fflush(output)) throw "Cannot write response";
        std::fclose(buffer);
    } catch (...) { std::fclose(buffer); throw; }
}
}
int gaming::run_boolean_search(int argc,const char* const* argv,FILE* input,FILE* output,FILE* errors) {
    try {
        const char *directory=nullptr,*dictionary=nullptr,*query=nullptr,*positions=nullptr,*compact=nullptr,*skips=nullptr,*norms=nullptr,*corpus=nullptr,*snippet_index=nullptr,*build_snippet_index=nullptr,*filters_path=nullptr,*filter_source=nullptr,*filter_type=nullptr,*filter_year=nullptr;
        bool ranked=false;
        TermSpace space=TermSpace::surface; unsigned limit=10; bool stats=false,optimized=true;
        for (int i=1;i<argc;++i) {
            const char* arg=argv[i];
            if (!std::strcmp(arg,"--help")) {
                std::fputs("UTF-8 Boolean/phrase/TF-IDF search: boolean_search --index DIR [--positions DIR] [--compact DIR] [--skips DIR] [--execution linear|optimized] [--stats] [--space surface|lemma] [--dict FILE] [--limit 0..1000] [--query QUERY] [--ranking boolean|tfidf] [--norms DIR] [--corpus MAIIR3 --snippet-index OFFSETS] [--build-snippet-index OFFSETS] [--filters FILE --filter-source SITE --filter-type TYPE --filter-year YYYY]\n"
                           "Without --query: one query per stdin line; one JSON result per stdout line.\n"
                           "Operators: NOT > AND > OR, parentheses, aliases ! & |. Explicit operators required.\n"
                           "Double quotes require adjacent tokens in order within one field; --positions is required.\n"
                           "TF-IDF: lnc.ltc cosine, positive scores only; quoted phrases are mandatory exact filters, unquoted words form a bag, tie by ID; requires --positions and --norms.\n"
                           "--corpus adds query-dependent text snippets and UTF-8 byte match spans to every result.\n",output); return 0;
            }
            if (!std::strcmp(arg,"--stats")) { stats=true; continue; }
            if (i+1==argc) throw "Missing option value";
            const char* value=argv[++i];
            if (!std::strcmp(arg,"--index")) directory=value;
            else if (!std::strcmp(arg,"--positions")) positions=value;
            else if (!std::strcmp(arg,"--compact")) compact=value;
            else if (!std::strcmp(arg,"--skips")) skips=value;
            else if (!std::strcmp(arg,"--norms")) norms=value;
            else if (!std::strcmp(arg,"--corpus")) corpus=value;
            else if (!std::strcmp(arg,"--snippet-index")) snippet_index=value;
            else if (!std::strcmp(arg,"--build-snippet-index")) build_snippet_index=value;
            else if (!std::strcmp(arg,"--filters")) filters_path=value;
            else if (!std::strcmp(arg,"--filter-source")) filter_source=value;
            else if (!std::strcmp(arg,"--filter-type")) filter_type=value;
            else if (!std::strcmp(arg,"--filter-year")) filter_year=value;
            else if (!std::strcmp(arg,"--ranking")) {
                if (!std::strcmp(value,"tfidf")) ranked=true;
                else if (!std::strcmp(value,"boolean")) ranked=false;
                else throw "--ranking must be boolean or tfidf";
            }
            else if (!std::strcmp(arg,"--execution")) {
                if (!std::strcmp(value,"linear")) optimized=false;
                else if (!std::strcmp(value,"optimized")) optimized=true;
                else throw "--execution must be linear or optimized";
            }
            else if (!std::strcmp(arg,"--dict")) dictionary=value;
            else if (!std::strcmp(arg,"--query")) query=value;
            else if (!std::strcmp(arg,"--space")) {
                if (!std::strcmp(value,"surface")) space=TermSpace::surface;
                else if (!std::strcmp(value,"lemma")) space=TermSpace::lemma;
                else throw "Unknown term space";
            } else if (!std::strcmp(arg,"--limit")) {
                limit=0; if (!*value) throw "Invalid limit";
                for (const char* p=value;*p;++p) { if (*p<'0' || *p>'9' || limit>1000) throw "Invalid limit"; limit=limit*10+unsigned(*p-'0'); }
                if (limit>1000) throw "Invalid limit (maximum 1000)";
            } else throw "Unknown option";
        }
        if (!directory) throw "--index is required";
        if ((snippet_index || build_snippet_index) && !corpus) throw "Snippet offsets require --corpus";
        if (snippet_index && build_snippet_index) throw "Cannot read and build snippet offsets together";
        if ((filter_source || filter_type || filter_year) && !filters_path) throw "Filters require --filters FILE";
        BooleanSearch engine; const char* error=nullptr; check(engine.open(directory,space,dictionary,&error,positions,compact,skips,optimized),error);
        Tfidf tfidf;
        if (ranked) {
            if (!norms || !positions) throw "TF-IDF requires --norms DIR and --positions DIR";
            check(tfidf.open(norms,directory,positions,engine,&error),error);
        }
        gaming::SnippetStore snippets;
        if (corpus) check(snippets.open(corpus,engine.index().documents(),&error,snippet_index),error);
        gaming::SearchFilters filters;
        if (filters_path) { check(filters.open(filters_path,engine.index().documents(),&error),error); filters.select(filter_source,filter_type,filter_year); }
        if (build_snippet_index) {
            check(snippets.save(build_snippet_index,&error),error);
            std::fprintf(output,"Built snippet offsets for %llu documents\n",
                         static_cast<unsigned long long>(engine.index().documents()));
            return 0;
        }
        bool failed=false;
        auto process=[&](const char* text) {
            try { run_query(engine,text,limit,output,stats,ranked ? &tfidf : nullptr,corpus ? &snippets : nullptr,
                            (filter_source || filter_type || filter_year) ? &filters : nullptr); }
            catch (const char* e) {
                failed=true; std::fputs("{\"error\":",output); json(output,e,std::strlen(e)); std::fputs("}\n",output); std::fflush(output);
            }
        };
        if (query) process(query);
        else {
            char line[max_query+1]; std::size_t n=0; bool oversized=false,invalid=false; int c;
            while ((c=std::fgetc(input))!=EOF) {
                if (c=='\n') {
                    if (oversized || invalid) { failed=true; std::fputs("{\"error\":\"Query too long or contains NUL\"}\n",output); }
                    else { line[n]=0; process(line); }
                    n=0; oversized=invalid=false;
                } else if (c==0) invalid=true;
                else if (n==max_query) oversized=true;
                else line[n++]=static_cast<char>(c);
            }
            if (std::ferror(input)) throw "Cannot read stdin";
            if (oversized || invalid) { failed=true; std::fputs("{\"error\":\"Query too long or contains NUL\"}\n",output); }
            else if (n) { line[n]=0; process(line); }
        }
        if (std::ferror(output) || std::fflush(output)) throw "Cannot write stdout";
        return failed ? 1 : 0;
    } catch (const char* e) { std::fprintf(errors,"[boolean_search] %s\n",e); return 1; }
      catch (const std::exception&) { std::fputs("[boolean_search] Out of memory or tokenizer failure\n",errors); return 1; }
}
