#include "tfidf.hpp"
#include "search_filters.hpp"
#include "tokenizer.hpp"
#include <cmath>
#include <cstring>
#include <cstdlib>
#include <chrono>
#include <exception>
#include <limits>
#include <sys/stat.h>
#ifdef _WIN32
#include <direct.h>
#include <windows.h>
#else
#include <sys/statvfs.h>
#endif

namespace {
using U64=std::uint64_t;
constexpr U64 basis=14695981039346656037ULL;
static_assert(sizeof(double)==8 && std::numeric_limits<double>::is_iec559,"IEEE binary64 required");
void require(bool ok,const char* const& error) { if (!ok) throw (error ? error : "TF-IDF operation failed"); }
void path(char* out,const char* dir,const char* file) {
    if (!dir || std::strlen(dir)+std::strlen(file)+2>4096) throw "Invalid TF-IDF path";
    std::strcpy(out,dir); std::strcat(out,"/"); std::strcat(out,file);
}
struct File {
    FILE* f;
    explicit File(const char* name,const char* mode="rb"):f(std::fopen(name,mode)) { if (!f) throw "Cannot open TF-IDF file"; }
    ~File() { if (f) std::fclose(f); }
    void close() { auto* old=f; f=nullptr; if (std::fclose(old)) throw "Cannot close TF-IDF file"; }
};
void read(FILE* f,void* p,std::size_t n) { if (n && std::fread(p,1,n,f)!=n) throw "Truncated TF-IDF input"; }
void write(FILE* f,const void* p,std::size_t n) { if (n && std::fwrite(p,1,n,f)!=n) throw "Cannot write TF-IDF output"; }
U64 hash(U64 h,const void* p,std::size_t n) {
    const auto* b=static_cast<const unsigned char*>(p);
    for (std::size_t i=0;i<n;++i) { h^=b[i]; h*=1099511628211ULL; } return h;
}
U64 decode(const unsigned char* p,unsigned n=8) { U64 v=0; for (unsigned i=0;i<n;++i) v|=U64(p[i])<<(8*i); return v; }
void encode(unsigned char* p,U64 v) { for (unsigned i=0;i<8;++i) p[i]=static_cast<unsigned char>(v>>(8*i)); }
void seek(FILE* f,U64 offset) {
    if (offset>INT64_MAX) throw "TF-IDF offset overflow";
#ifdef _WIN32
    if (_fseeki64(f,static_cast<__int64>(offset),SEEK_SET)) throw "Cannot seek TF-IDF input";
#else
    if (fseeko(f,static_cast<off_t>(offset),SEEK_SET)) throw "Cannot seek TF-IDF input";
#endif
}
struct Doubles {
    double* p;
    explicit Doubles(U64 n):p(nullptr) {
        if (n>SIZE_MAX/sizeof(double)) throw "TF-IDF array too large";
        p=static_cast<double*>(std::calloc(n ? static_cast<std::size_t>(n) : 1,sizeof(double)));
        if (!p) throw "Cannot allocate TF-IDF array";
    }
    ~Doubles() { std::free(p); }
};
U64 binding(const char* base,const char* positions) {
    char p[4096]; unsigned char bytes[80]; U64 h=basis;
    // These original headers are retained in the packed index. The positional
    // binding alone changes on packing, so omit its field from our fingerprint.
    const char* names[]={"terms.bin","documents.bin"};
    for (const char* name:names) {
        path(p,base,name); File f(p); read(f.f,bytes,80); h=hash(h,bytes,80);
    }
    path(p,positions,"position_terms.bin"); File f(p); read(f.f,bytes,80);
    std::memset(bytes+32,0,8); return hash(h,bytes,80);
}
double logtf(U64 n) { return n ? 1.0+std::log10(static_cast<double>(n)) : 0.0; }
void reserve(const char* dir,U64 bytes) {
    U64 available=0;
#ifdef _WIN32
    ULARGE_INTEGER n; if (!GetDiskFreeSpaceExA(dir,&n,nullptr,nullptr)) throw "Cannot check TF-IDF disk space"; available=n.QuadPart;
#else
    struct statvfs n{}; if (statvfs(dir,&n)) throw "Cannot check TF-IDF disk space"; available=U64(n.f_bavail)*n.f_frsize;
#endif
    if (available<bytes) throw "TF-IDF disk reserve reached";
}
void build(const char* base,const char* positions,const char* output,U64 reserve_bytes,FILE* log) {
    const auto begin=std::chrono::steady_clock::now();
    const char* error=nullptr; gaming::BooleanIndex index; require(index.open(base,&error),error);
    gaming::PositionalIndex pos; require(pos.open(positions,base,index,&error),error);
    require(index.verify(&error),error); require(pos.verify(&error),error);
    if (index.documents()>SIZE_MAX/16) throw "Too many documents for TF-IDF norms";
#ifdef _WIN32
    if (_mkdir(output)) throw "TF-IDF output must be a NEW directory";
#else
    if (mkdir(output,0755)) throw "TF-IDF output must be a NEW directory";
#endif
    char p[4096]; path(p,output,"INCOMPLETE"); { File marker(p,"wb"); write(marker.f,"Incomplete\n",11); }
    const U64 payload=index.documents()*16;
    if (payload>UINT64_MAX-80-reserve_bytes) throw "TF-IDF reserve overflow";
    reserve(output,reserve_bytes+payload+80);
    Doubles norms(index.documents()*2);
    path(p,base,"terms.bin"); File terms(p); seek(terms.f,80);
    path(p,positions,"position_terms.bin"); File lists(p); seek(lists.f,80);
    path(p,positions,"positions.bin"); File coordinates(p); seek(coordinates.f,80);
    U64 total=0,expected=0;
    for (U64 term=0;term<index.terms();++term) {
        unsigned char t[40],list[16]; read(terms.f,t,40); read(lists.f,list,16);
        const U64 space=decode(t,4),df=decode(t+24),offset=decode(list),count=decode(list+8);
        if (space>1 || offset!=expected || !count) throw "Invalid norm source list";
        U64 previous=UINT64_MAX,tf=0,documents=0,last_key=0;
        auto flush=[&]() {
            if (!tf) return;
            const double weight=logtf(tf); norms.p[space*index.documents()+previous]+=weight*weight; ++documents;
        };
        for (U64 i=0;i<count;++i) {
            unsigned char c[8]; read(coordinates.f,c,8);
            const U64 doc=decode(c,4),key=(doc<<32)|decode(c+4,4);
            if (doc>=index.documents() || (i && key<=last_key)) throw "Invalid norm source coordinate";
            if (doc!=previous) { flush(); previous=doc; tf=0; }
            ++tf; last_key=key;
        }
        flush(); if (documents!=df) throw "Coordinate DF differs from Boolean index";
        expected+=count; total+=count;
    }
    if (std::fgetc(coordinates.f)!=EOF || std::ferror(coordinates.f)) throw "Trailing or unreadable norm coordinates";
    path(p,output,"norms.bin"); File out(p,"wb"); unsigned char header[80]{}; write(out.f,header,80); U64 checksum=basis;
    for (U64 i=0;i<index.documents()*2;++i) {
        const double value=std::sqrt(norms.p[i]); U64 bits; std::memcpy(&bits,&value,8); unsigned char b[8]; encode(b,bits);
        write(out.f,b,8); checksum=hash(checksum,b,8);
    }
    std::memcpy(header,"MAITF13\1",8);
    const U64 fields[]={index.documents(),index.terms(),binding(base,positions),payload,checksum,1,2,0,0};
    for (unsigned i=0;i<9;++i) encode(header+8+i*8,fields[i]);
    seek(out.f,0); write(out.f,header,80); out.close();
    path(p,output,"INCOMPLETE"); if (std::remove(p)) throw "Cannot finish TF-IDF norms";
    std::fprintf(log,"{\"documents\":%llu,\"terms\":%llu,\"coordinates\":%llu,\"bytes\":%llu,\"elapsed_ms\":%.3f}\n",
        static_cast<unsigned long long>(index.documents()),static_cast<unsigned long long>(index.terms()),
        static_cast<unsigned long long>(total),static_cast<unsigned long long>(payload+80),
        std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-begin).count());
}
bool better(const gaming::RankedHit& a,const gaming::RankedHit& b) { return a.score>b.score || (a.score==b.score && a.id<b.id); }
void swap(gaming::RankedHit& a,gaming::RankedHit& b) { auto tmp=a; a=b; b=tmp; }
void down(gaming::RankedHit* hits,unsigned size,unsigned i) {
    for (;;) {
        unsigned child=2*i+1; if (child>=size) return;
        if (child+1<size && better(hits[child],hits[child+1])) ++child;
        if (!better(hits[i],hits[child])) return;
        swap(hits[i],hits[child]); i=child;
    }
}
}
gaming::Tfidf::~Tfidf() { std::free(norms_); }
bool gaming::Tfidf::open(const char* directory,const char* base,const char* positions,BooleanSearch& engine,const char** error) {
    *error=nullptr; std::free(norms_); norms_=nullptr; documents_=0;
    try {
        if (!engine.positions().available()) throw "TF-IDF requires --positions";
        char p[4096]; path(p,directory,"INCOMPLETE");
        if (FILE* marker=std::fopen(p,"rb")) { std::fclose(marker); throw "TF-IDF norms are INCOMPLETE"; }
        path(p,directory,"norms.bin"); File f(p); unsigned char h[80]; read(f.f,h,80);
        const U64 n=engine.index().documents();
        if (std::memcmp(h,"MAITF13\1",8) || decode(h+8)!=n || decode(h+16)!=engine.index().terms() ||
            decode(h+24)!=binding(base,positions) || n>SIZE_MAX/16 || decode(h+32)!=n*16 ||
            decode(h+48)!=1 || decode(h+56)!=2 || decode(h+64) || decode(h+72)) throw "TF-IDF norms header/binding mismatch";
        Doubles data(n*2); U64 checksum=basis;
        for (U64 i=0;i<n*2;++i) {
            unsigned char b[8]; read(f.f,b,8); checksum=hash(checksum,b,8); const U64 bits=decode(b); double value; std::memcpy(&value,&bits,8);
            if (!std::isfinite(value) || value<0) throw "Invalid TF-IDF norm";
            data.p[i]=value;
        }
        if (checksum!=decode(h+40) || std::fgetc(f.f)!=EOF || std::ferror(f.f)) throw "TF-IDF norms checksum/size mismatch";
        norms_=data.p; data.p=nullptr; documents_=n; return true;
    } catch (const char* e) { *error=e; return false; }
}
bool gaming::Tfidf::search(BooleanSearch& engine,const char* query,unsigned limit,RankedResult& result,const char** error,
                           const SearchFilters* filters) {
    *error=nullptr; result={};
    try {
        if (!norms_ || engine.index().documents()!=documents_) throw "TF-IDF norms not open";
        if (!query || std::strlen(query)>8192 || limit>1000) throw "Invalid TF-IDF query/limit";
        struct Term { PositionList list; U64 tf=0,df=0; double weight=0; } terms[512]; unsigned used=0,tokens=0;
        tokenize(query,[&](const Token& token) {
            if (++tokens>64) throw "TF-IDF query exceeds 64 tokens";
            LemmaResult alternatives;
            if (engine.space()==TermSpace::lemma) require(engine.morphology().analyze(token.term.data(),token.term.size(),alternatives),"Too many lemma alternatives");
            else { alternatives.count=1; alternatives.values[0]={token.term.data(),token.term.size()}; }
            for (std::size_t a=0;a<alternatives.count;++a) {
                const auto word=alternatives.values[a]; PostingList posting;
                require(engine.index().lookup(word.data,word.size,engine.space(),posting,error),*error);
                if (!posting.count) continue; // project the query onto the corpus vocabulary
                unsigned i=0; while (i<used && terms[i].list.ordinal!=posting.ordinal) ++i;
                if (i==used) {
                    if (used==512) throw "TF-IDF query exceeds 512 terms";
                    require(engine.positions().lookup(word.data,word.size,engine.space(),terms[i].list,error),*error);
                    terms[i].df=posting.count; ++used;
                }
                ++terms[i].tf;
            }
        });
        if (!tokens) throw "TF-IDF query contains no tokens";
        double query_norm=0;
        for (unsigned i=0;i<used;++i) {
            terms[i].weight=logtf(terms[i].tf)*std::log10(static_cast<double>(documents_)/static_cast<double>(terms[i].df));
            query_norm+=terms[i].weight*terms[i].weight;
        }
        result.terms=used; if (!query_norm) return true;
        query_norm=std::sqrt(query_norm); Doubles scores(documents_);
        for (unsigned i=0;i<used;++i) {
            if (!terms[i].weight) continue;
            PositionCursor cursor; require(cursor.open(engine.positions(),terms[i].list,error),*error);
            U64 key=0,previous=UINT64_MAX,tf=0,df=0;
            auto flush=[&]() { if (tf) { scores.p[previous]+=logtf(tf)*(terms[i].weight/query_norm); ++df; } };
            while (cursor.next(key,error)) {
                const U64 doc=key>>32; if (doc>=documents_) throw "TF-IDF coordinate outside document universe";
                if (doc!=previous) { flush(); previous=doc; tf=0; } ++tf; ++result.coordinates;
            }
            if (*error) throw *error;
            flush(); if (df!=terms[i].df) throw "TF-IDF coordinate DF mismatch";
        }
        const U64 base=static_cast<unsigned>(engine.space())*documents_;
        for (U64 doc=0;doc<documents_;++doc) {
            if (scores.p[doc]<=0) continue;
            if (norms_[base+doc]<=0) throw "Nonempty document has zero TF-IDF norm";
            DocumentInfo info;
            if (filters) {
                require(engine.index().document_at(doc,info,error),*error);
                if (!info.id || !filters->match(info.id)) continue;
            }
            ++result.total;
            if (!limit) continue;
            const double score=scores.p[doc]/norms_[base+doc];
            if (!std::isfinite(score)) throw "Invalid TF-IDF score";
            // Only documents that can enter the bounded heap need metadata lookup.
            if (result.count==limit && score<result.hits[0].score) continue;
            if (!filters) require(engine.index().document_at(doc,info,error),*error);
            if (!info.id) throw "TF-IDF result missing from document table";
            RankedHit hit{info.id,score};
            if (result.count<limit) {
                unsigned at=result.count++; result.hits[at]=hit;
                while (at && better(result.hits[(at-1)/2],result.hits[at])) { swap(result.hits[(at-1)/2],result.hits[at]); at=(at-1)/2; }
            } else if (better(hit,result.hits[0])) { result.hits[0]=hit; down(result.hits,result.count,0); }
        }
        // Heap sort produces descending score, then ascending original uint64 ID.
        for (unsigned n=result.count;n>1;--n) { swap(result.hits[0],result.hits[n-1]); down(result.hits,n-1,0); }
        return true;
    } catch (const char* e) { *error=e; return false; }
      catch (const std::exception&) { *error="TF-IDF tokenizer or allocation failure"; return false; }
}
int gaming::run_tfidf_index(int argc,const char* const* argv,FILE* output,FILE* errors) {
    try {
        if (argc==2 && !std::strcmp(argv[1],"--help")) {
            std::fputs("UTF-8 TF-IDF lnc.ltc norms:\ntfidf_index build --index RAW_DIR --positions RAW_DIR --output NEW_DIR [--reserve-mib 4608]\ntfidf_index verify --index DIR --positions DIR --norms DIR [--compact DIR]\nSearch: boolean_search --ranking tfidf --norms DIR --positions DIR ...\n",output); return 0;
        }
        if (argc<2) throw "Missing TF-IDF mode";
        const bool building=!std::strcmp(argv[1],"build"),verifying=!std::strcmp(argv[1],"verify");
        if (!building && !verifying) throw "Unknown TF-IDF mode";
        const char *base=nullptr,*positions=nullptr,*destination=nullptr,*compact=nullptr; U64 reserve_mib=4608; unsigned seen=0;
        for (int i=2;i<argc;i+=2) {
            if (i+1==argc) throw "Missing TF-IDF option value";
            const char *arg=argv[i],*value=argv[i+1]; unsigned bit=0;
            if (!std::strcmp(arg,"--index")) { bit=1; base=value; }
            else if (!std::strcmp(arg,"--positions")) { bit=2; positions=value; }
            else if (!std::strcmp(arg,building ? "--output" : "--norms")) { bit=4; destination=value; }
            else if (verifying && !std::strcmp(arg,"--compact")) { bit=8; compact=value; }
            else if (building && !std::strcmp(arg,"--reserve-mib")) {
                bit=16; reserve_mib=0; if (!*value) throw "Invalid TF-IDF reserve";
                for (const char* p=value;*p;++p) { if (*p<'0' || *p>'9' || reserve_mib>UINT64_MAX/10485760) throw "Invalid TF-IDF reserve"; reserve_mib=reserve_mib*10+unsigned(*p-'0'); }
                if (reserve_mib>UINT64_MAX/1048576) throw "Invalid TF-IDF reserve";
            } else throw "Unknown TF-IDF option";
            if (seen&bit) throw "Duplicate TF-IDF option";
            seen|=bit;
        }
        if (!base || !positions || !destination) throw "Missing required TF-IDF paths";
        if (building) build(base,positions,destination,reserve_mib*1048576,output);
        else {
            const char* error=nullptr; BooleanSearch engine; require(engine.open(base,TermSpace::surface,nullptr,&error,positions,compact),error);
            Tfidf tfidf; require(tfidf.open(destination,base,positions,engine,&error),error);
            std::fputs("TF-IDF norms verified: binding, sizes, checksum, finite norms\n",output);
        }
        return 0;
    } catch (const char* e) { std::fprintf(errors,"[tfidf_index] %s\n",e); return 1; }
      catch (const std::exception&) { std::fputs("[tfidf_index] Allocation failure\n",errors); return 1; }
}
