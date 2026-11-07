#include "positional_index.hpp"
#include "tokenizer.hpp"
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <fcntl.h>
#include <sys/stat.h>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <direct.h>
#include <io.h>
#else
#include <unistd.h>
#include <sys/statvfs.h>
#endif

namespace {
using U64=std::uint64_t; using U32=std::uint32_t;
constexpr U64 basis=14695981039346656037ULL,header_size=80;
const char* base_names[]={"terms.bin","postings.bin","documents.bin","strings.bin"};
const char* names[]={"position_terms.bin","positions.bin"};
const char* magics[]={"MAIITX\0\1","MAIIPX\0\1"};
void require(bool ok,const char* const& error) { if (!ok) throw (error ? error : "Index operation failed"); }
U64 hash(U64 h,const void* data,std::size_t n) {
    const auto* p=static_cast<const unsigned char*>(data);
    for (std::size_t i=0;i<n;++i) { h^=p[i]; h*=1099511628211ULL; } return h;
}
U64 decode(const unsigned char* p,unsigned n=8) { U64 v=0; for (unsigned i=0;i<n;++i) v|=U64(p[i])<<(8*i); return v; }
void encode(unsigned char* p,U64 v,unsigned n=8) { for (unsigned i=0;i<n;++i) p[i]=static_cast<unsigned char>(v>>(8*i)); }
void read(FILE* f,void* p,std::size_t n) { if (n && std::fread(p,1,n,f)!=n) throw "Truncated or unreadable positional input"; }
void write(FILE* f,const void* p,std::size_t n) { if (n && std::fwrite(p,1,n,f)!=n) throw "Cannot write positional index"; }
void seek(FILE* f,U64 offset) {
    if (offset>INT64_MAX) throw "Position file offset overflow";
#ifdef _WIN32
    if (_fseeki64(f,static_cast<__int64>(offset),SEEK_SET)) throw "Cannot seek positional file";
#else
    if (fseeko(f,static_cast<off_t>(offset),SEEK_SET)) throw "Cannot seek positional file";
#endif
}
U64 file_size(FILE* f) {
#ifdef _WIN32
    if (_fseeki64(f,0,SEEK_END)) throw "Cannot size positional file";
    const auto n=_ftelli64(f);
#else
    if (fseeko(f,0,SEEK_END)) throw "Cannot size positional file";
    const auto n=ftello(f);
#endif
    if (n<0) throw "Cannot size positional file";
    return static_cast<U64>(n);
}
void path(char* out,const char* directory,const char* name) {
    const auto a=std::strlen(directory),b=std::strlen(name);
    if (a>4094 || b>4094-a) throw "Path too long";
    std::memcpy(out,directory,a); out[a]='/'; std::memcpy(out+a+1,name,b+1);
}
struct File {
    FILE* f=nullptr;
    File()=default;
    explicit File(const char* name,const char* mode="rb") { open(name,mode); }
    ~File() { if (f) std::fclose(f); }
    void open(const char* name,const char* mode="rb") {
        if (f) throw "File already open";
        if (!std::strcmp(mode,"new")) {
#ifdef _WIN32
            const int fd=_open(name,_O_CREAT|_O_EXCL|_O_WRONLY|_O_BINARY,_S_IREAD|_S_IWRITE);
            if (fd>=0) { f=_fdopen(fd,"wb"); if (!f) _close(fd); }
#else
            const int fd=::open(name,O_CREAT|O_EXCL|O_WRONLY,0644);
            if (fd>=0) { f=fdopen(fd,"wb"); if (!f) ::close(fd); }
#endif
        } else f=std::fopen(name,mode);
        if (!f) throw "Cannot open input or exclusively create positional output";
    }
    void close() { if (f) { FILE* old=f; f=nullptr; if (std::fclose(old)) throw "Cannot close positional output"; } }
};
struct Bytes {
    unsigned char* p=nullptr; std::size_t capacity=0;
    ~Bytes() { std::free(p); }
    void reserve(std::size_t n) {
        if (n<=capacity) return;
        void* v=std::realloc(p,n); if (!v) throw "Positional allocation failed";
        p=static_cast<unsigned char*>(v); capacity=n;
    }
};
struct Binding { U64 fingerprint=basis,corpus=0,dictionary=0,terms=0,documents=0; };
Binding binding(const char* directory) {
    Binding b; unsigned char p[80]; char name[4096];
    for (unsigned i=0;i<4;++i) {
        path(name,directory,base_names[i]); File file(name); read(file.f,p,80); b.fingerprint=hash(b.fingerprint,p,80);
        if (!i) { b.terms=decode(p+8); b.corpus=decode(p+32); b.dictionary=decode(p+40); }
        if (i==2) b.documents=decode(p+8);
    }
    return b;
}
U64 hash_file(const char* name) {
    File f(name); unsigned char p[65536]; U64 h=basis; std::size_t n;
    while ((n=std::fread(p,1,sizeof(p),f.f))) h=hash(h,p,n);
    if (std::ferror(f.f)) throw "Cannot hash file";
    return h;
}
struct Header { U64 count=0,bytes=0,checksum=basis,binding=0,corpus=0,dictionary=0,documents=0,width=0,terms=0; };
Header header(FILE* f,unsigned kind) {
    seek(f,0); unsigned char p[80]; read(f,p,80);
    if (std::memcmp(p,magics[kind],8)) throw "Invalid positional index format";
    Header h{decode(p+8),decode(p+16),decode(p+24),decode(p+32),decode(p+40),decode(p+48),decode(p+56),decode(p+64),decode(p+72)};
    if (h.width!=(kind ? 8U : 16U) || h.count>UINT64_MAX/h.width || h.bytes!=h.count*h.width ||
        h.bytes>INT64_MAX-80 || file_size(f)!=80+h.bytes) throw "Positional header/size mismatch";
    return h;
}
struct Writer {
    File f; Header h; unsigned kind;
    Writer(const char* directory,unsigned k):kind(k) {
        char name[4096]; path(name,directory,names[k]); f.open(name,"new");
        unsigned char zero[80]{}; write(f.f,zero,80); h.width=k ? 8 : 16;
    }
    void bytes(const void* p,std::size_t n) { write(f.f,p,n); h.checksum=hash(h.checksum,p,n); h.bytes+=n; }
    void finish(const Binding& b) {
        h.count=h.bytes/h.width; unsigned char p[80]{}; std::memcpy(p,magics[kind],8);
        const U64 v[]={h.count,h.bytes,h.checksum,b.fingerprint,b.corpus,b.dictionary,b.documents,h.width,b.terms};
        for (unsigned i=0;i<9;++i) encode(p+8+i*8,v[i]);
        seek(f.f,0); write(f.f,p,80); f.close();
    }
};
struct Work {
    const char* directory; U64 reserve;
    Work(const char* d,U64 r):directory(d),reserve(r) {
#ifdef _WIN32
        if (_mkdir(d)) throw "Positional output must be a NEW directory";
#else
        if (mkdir(d,0755)) throw "Positional output must be a NEW directory";
#endif
        char p[4096]; path(p,d,"INCOMPLETE"); File mark(p,"new"); write(mark.f,"Incomplete\n",11); check();
    }
    void check() const {
        U64 free=0;
#ifdef _WIN32
        ULARGE_INTEGER n; if (!GetDiskFreeSpaceExA(directory,&n,nullptr,nullptr)) throw "Cannot check disk reserve"; free=n.QuadPart;
#else
        struct statvfs n{}; if (statvfs(directory,&n)) throw "Cannot check disk reserve"; free=U64(n.f_bavail)*n.f_frsize;
#endif
        if (free<reserve) throw "Disk reserve reached; positional build stopped, partial output retained";
    }
    void run_path(char* p,U64 n) const { char name[80]; std::snprintf(name,sizeof(name),"run_%llu.bin",static_cast<unsigned long long>(n)); path(p,directory,name); }
};
// Load only vocabulary strings and an own hash table for O(token length) build lookup.
// No per-token disk binary search during the corpus pass.
struct Vocabulary {
    struct Entry { U64 offset; U32 size,space; };
    Entry* entries=nullptr; U32* slots=nullptr; Bytes strings; U64 count=0; std::size_t capacity=0;
    ~Vocabulary() { std::free(entries); std::free(slots); }
    void load(const char* directory,const Binding& b) {
        if (b.terms>=UINT32_MAX || b.documents>UINT32_MAX) throw "Positional v1 supports fewer than 2^32 terms/documents";
        count=b.terms; capacity=2; while (capacity<count*2) capacity*=2;
        slots=static_cast<U32*>(std::calloc(capacity,sizeof(U32)));
        entries=static_cast<Entry*>(std::calloc(count ? static_cast<std::size_t>(count) : 1,sizeof(Entry)));
        if (!slots || !entries) throw "Cannot allocate positional vocabulary";
        char p[4096]; path(p,directory,base_names[0]); File terms(p); seek(terms.f,80);
        U64 bytes=0; unsigned char record[40];
        for (U64 i=0;i<count;++i) { read(terms.f,record,40); bytes+=decode(record+4,4); }
        if (bytes>SIZE_MAX) throw "Vocabulary too large";
        strings.reserve(static_cast<std::size_t>(bytes));
        path(p,directory,base_names[3]); File source(p); seek(terms.f,80); U64 used=0;
        for (U64 i=0;i<count;++i) {
            read(terms.f,record,40); Entry& e=entries[i]; e.space=static_cast<U32>(decode(record,4)); e.size=static_cast<U32>(decode(record+4,4)); e.offset=used;
            seek(source.f,80+decode(record+8)); read(source.f,strings.p+used,e.size);
            auto slot=static_cast<std::size_t>(hash(basis^e.space,strings.p+used,e.size))&(capacity-1);
            while (slots[slot]) slot=(slot+1)&(capacity-1);
            slots[slot]=static_cast<U32>(i+1); used+=e.size;
        }
    }
    U32 lookup(unsigned space,const char* word,std::size_t n) const {
        auto slot=static_cast<std::size_t>(hash(basis^space,word,n))&(capacity-1);
        while (slots[slot]) {
            const auto i=slots[slot]-1; const auto& e=entries[i];
            if (e.space==space && e.size==n && !std::memcmp(strings.p+e.offset,word,n)) return i;
            slot=(slot+1)&(capacity-1);
        }
        throw "Corpus token absent from bound Boolean vocabulary";
    }
};
struct Row { U32 term,doc,position; };
bool less(const Row& a,const Row& b) { return a.term!=b.term ? a.term<b.term : (a.doc!=b.doc ? a.doc<b.doc : a.position<b.position); }
bool equal(const Row& a,const Row& b) { return a.term==b.term && a.doc==b.doc && a.position==b.position; }
void write_row(FILE* f,const Row& r) { unsigned char p[12]; encode(p,r.term,4); encode(p+4,r.doc,4); encode(p+8,r.position,4); write(f,p,12); }
bool read_row(FILE* f,Row& r) {
    unsigned char p[12]; const auto n=std::fread(p,1,12,f);
    if (!n && !std::ferror(f)) return false;
    if (n!=12) throw "Truncated positional run";
    r={static_cast<U32>(decode(p,4)),static_cast<U32>(decode(p+4,4)),static_cast<U32>(decode(p+8,4))}; return true;
}
struct Runs {
    U64* ids=nullptr; std::size_t size=0,capacity=0;
    ~Runs() { std::free(ids); }
    void append(U64 id) {
        if (size==capacity) { const auto n=capacity ? capacity*2 : 16; void* p=std::realloc(ids,n*sizeof(U64)); if (!p) throw "Run allocation failed"; ids=static_cast<U64*>(p); capacity=n; }
        ids[size++]=id;
    }
};
struct Sorter {
    Row *rows=nullptr,*scratch=nullptr; std::size_t used=0,capacity=0; U64 serial=0; Runs& runs; Work& work;
    Sorter(std::size_t bytes,Runs& r,Work& w):runs(r),work(w) {
        capacity=bytes/(2*sizeof(Row)); rows=static_cast<Row*>(std::malloc(capacity*sizeof(Row))); scratch=static_cast<Row*>(std::malloc(capacity*sizeof(Row)));
        if (!capacity || !rows || !scratch) { std::free(rows); std::free(scratch); throw "Cannot allocate positional sort buffers"; }
    }
    ~Sorter() { std::free(rows); std::free(scratch); }
    void flush() {
        if (!used) return;
        work.check();
        // Stable LSD radix sort: position, document ordinal, term ordinal; 16-bit digits.
        std::size_t buckets[65536]; Row *a=rows,*b=scratch;
        for (unsigned pass=0;pass<6;++pass) {
            std::memset(buckets,0,sizeof(buckets));
            const auto digit=[pass](const Row& r) { const U32 v=pass<2 ? r.position : (pass<4 ? r.doc : r.term); return (v>>((pass%2)*16))&65535U; };
            for (std::size_t i=0;i<used;++i) ++buckets[digit(a[i])];
            std::size_t prefix=0; for (auto& bucket:buckets) { const auto n=bucket; bucket=prefix; prefix+=n; }
            for (std::size_t i=0;i<used;++i) b[buckets[digit(a[i])]++]=a[i];
            Row* t=a; a=b; b=t;
        }
        char p[4096]; work.run_path(p,serial); File f(p,"new");
        for (std::size_t i=0;i<used;++i) if (!i || !equal(a[i],a[i-1])) write_row(f.f,a[i]);
        f.close(); runs.append(serial++); used=0;
    }
    void add(U32 term,U32 doc,U32 position) { if (used==capacity) flush(); rows[used++]={term,doc,position}; }
};
// Own minimum heap; at most 24 open input streams per merge.
struct Merger {
    File files[24]; Row rows[24]; unsigned heap[24]{},size=0;
    void sift(unsigned root) {
        while (root<size/2) {
            unsigned child=root*2+1;
            if (child+1<size && less(rows[heap[child+1]],rows[heap[child]])) ++child;
            if (!less(rows[heap[child]],rows[heap[root]])) break;
            const auto t=heap[root]; heap[root]=heap[child]; heap[child]=t; root=child;
        }
    }
    Merger(Work& work,const U64* ids,std::size_t count) {
        for (unsigned i=0;i<count;++i) { char p[4096]; work.run_path(p,ids[i]); files[i].open(p); if (read_row(files[i].f,rows[i])) heap[size++]=i; }
        for (auto i=size/2;i>0;--i) sift(i-1);
    }
    bool next(Row& result) {
        if (!size) return false;
        const auto i=heap[0]; result=rows[i];
        if (!read_row(files[i].f,rows[i])) heap[0]=heap[--size];
        sift(0); return true;
    }
};
void remove_runs(Work& work,const U64* ids,std::size_t n) {
    for (std::size_t i=0;i<n;++i) { char p[4096]; work.run_path(p,ids[i]); if (std::remove(p)) throw "Cannot remove completed intermediate run"; }
}
U64 parse(const char* p) {
    if (!*p) throw "Invalid integer";
    U64 n=0;
    for (;*p;++p) { if (*p<'0' || *p>'9' || n>(UINT64_MAX-unsigned(*p-'0'))/10) throw "Invalid integer"; n=n*10+unsigned(*p-'0'); } return n;
}
void build(const char* corpus_path,const char* base,const char* output,const char* dictionary,U64 memory,U64 reserve,FILE* log) {
    const auto begin=std::chrono::steady_clock::now();
    gaming::BooleanIndex index; const char* error=nullptr; require(index.open(base,&error),error); require(index.verify(&error),error);
    const Binding bind=binding(base); gaming::Lemmatizer morphology;
    if (index.has_lemmas()) {
        if (!dictionary || hash_file(dictionary)!=bind.dictionary) throw "Positions require the same morphology dictionary as Boolean index";
        require(morphology.load(dictionary,&error),error);
    }
    Vocabulary vocabulary; vocabulary.load(base,bind);
    Work work(output,reserve); Runs runs; U64 serial=0,tokens=0,emissions=0,docs=0,corpus_hash=basis;
    {
        Sorter sorter(static_cast<std::size_t>(memory)*1024*1024,runs,work); File source(corpus_path); Bytes field;
        const auto input=[&](void* p,std::size_t n) { read(source.f,p,n); corpus_hash=hash(corpus_hash,p,n); };
        const auto number=[&](unsigned n=8) { unsigned char p[8]; input(p,n); return decode(p,n); };
        char magic[8]; input(magic,8); if (std::memcmp(magic,"MAIIR3\0\1",8)) throw "Invalid positional source corpus";
        while (true) {
            const int c=std::fgetc(source.f); if (c==EOF) { if (std::ferror(source.f)) throw "Cannot read corpus"; break; }
            if (std::ungetc(c,source.f)==EOF) throw "Cannot read corpus prefix";
            const auto id=number(); gaming::DocumentInfo d;
            if (docs>=bind.documents) throw "Corpus has extra documents";
            require(index.document_at(docs,d,&error),error); if (id!=d.id) throw "Corpus IDs differ from Boolean index";
            const auto n=number(4); if (!n || n>64) throw "Invalid source size"; char name[64]; input(name,static_cast<std::size_t>(n));
            for (unsigned f=0;f<2;++f) {
                const auto bytes=number(); if (bytes>64*1024*1024) throw "Corpus field exceeds 64 MiB";
                field.reserve(static_cast<std::size_t>(bytes)); input(field.p,static_cast<std::size_t>(bytes));
                gaming::tokenize(std::string_view(field.p ? reinterpret_cast<const char*>(field.p) : "",static_cast<std::size_t>(bytes)),[&](const gaming::Token& token) {
                    if (token.position>=0x80000000ULL) throw "Position exceeds v1 field limit";
                    const U32 position=static_cast<U32>(token.position)|(f<<31);
                    sorter.add(vocabulary.lookup(0,token.term.data(),token.term.size()),static_cast<U32>(docs),position); ++tokens; ++emissions;
                    if (index.has_lemmas()) {
                        gaming::LemmaResult r; if (!morphology.analyze(token.term.data(),token.term.size(),r)) throw "Too many lemma alternatives";
                        for (std::size_t i=0;i<r.count;++i) { sorter.add(vocabulary.lookup(1,r.values[i].data,r.values[i].size),static_cast<U32>(docs),position); ++emissions; }
                    }
                });
            }
            ++docs;
            if (docs%50000==0) { work.check(); std::fprintf(log,"positioned documents: %llu\n",static_cast<unsigned long long>(docs)); std::fflush(log); }
        }
        if (docs!=bind.documents || corpus_hash!=bind.corpus) throw "Corpus fingerprint/count differs from Boolean index";
        sorter.flush(); serial=sorter.serial;
    }
    const auto initial_runs=runs.size; unsigned passes=0;
    while (runs.size>24) {
        Runs merged;
        for (std::size_t begin_run=0;begin_run<runs.size;begin_run+=24) {
            work.check(); const auto count=runs.size-begin_run<24 ? runs.size-begin_run : 24;
            const auto out_id=serial++; char p[4096]; work.run_path(p,out_id);
            {
                File f(p,"new"); Merger merge(work,runs.ids+begin_run,count); Row row{},previous{}; bool first=true; U64 written=0;
                while (merge.next(row)) if (first || !equal(row,previous)) {
                    write_row(f.f,row); previous=row; first=false; if (++written%1000000==0) work.check();
                }
                f.close();
            }
            merged.append(out_id); remove_runs(work,runs.ids+begin_run,count);
        }
        std::free(runs.ids); runs.ids=merged.ids; runs.size=merged.size; runs.capacity=merged.capacity; merged.ids=nullptr; ++passes;
    }
    Writer terms(output,0),positions(output,1); U64 current=0,start=0,count=0,occurrences=0; Row previous{}; bool first=true;
    const auto term_record=[&]() { unsigned char p[16]; encode(p,start); encode(p+8,count); terms.bytes(p,16); start=occurrences; count=0; ++current; };
    {
        Merger merge(work,runs.ids,runs.size); Row row{};
        while (merge.next(row)) {
            if (!first && equal(row,previous)) continue;
            if (row.term>=bind.terms || row.doc>=bind.documents) throw "Invalid positional run coordinate";
            while (current<row.term) term_record();
            unsigned char p[8]; encode(p,row.doc,4); encode(p+4,row.position,4); positions.bytes(p,8); ++count; ++occurrences;
            previous=row; first=false; if (occurrences%1000000==0) work.check();
        }
    }
    while (current<bind.terms) term_record();
    positions.finish(bind); terms.finish(bind); remove_runs(work,runs.ids,runs.size); if (runs.size) ++passes;
    char p[4096]; path(p,output,"INCOMPLETE"); if (std::remove(p)) throw "Cannot finish positional index";
    const auto ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-begin).count();
    std::fprintf(log,"{\"format\":\"MAIIR-POSITIONS-v1\",\"documents\":%llu,\"tokens\":%llu,\"emissions\":%llu,\"coordinates\":%llu,\"terms\":%llu,\"initial_runs\":%llu,\"merge_passes\":%u,\"sort_buffer_bytes\":%llu,\"elapsed_ms\":%.3f}\n",
        static_cast<unsigned long long>(docs),static_cast<unsigned long long>(tokens),static_cast<unsigned long long>(emissions),
        static_cast<unsigned long long>(occurrences),static_cast<unsigned long long>(bind.terms),static_cast<unsigned long long>(initial_runs),passes,
        static_cast<unsigned long long>(memory*1024*1024),ms);
}
}

gaming::PositionalIndex::~PositionalIndex() { if (terms_) std::fclose(terms_); }
bool gaming::PositionalIndex::open(const char* directory,const char* boolean_directory,BooleanIndex& index,const char** error) {
    *error=nullptr; if (terms_) std::fclose(terms_); terms_=nullptr; index_=nullptr;
    try {
        char p[4096]; path(p,directory,"INCOMPLETE"); FILE* mark=std::fopen(p,"rb"); if (mark) { std::fclose(mark); throw "Positional index is incomplete"; }
        const auto b=binding(boolean_directory); Header h[2];
        for (unsigned k=0;k<2;++k) {
            path(p,directory,names[k]); File file(p); h[k]=header(file.f,k);
            if (h[k].binding!=b.fingerprint || h[k].corpus!=b.corpus || h[k].dictionary!=b.dictionary || h[k].documents!=b.documents || h[k].terms!=b.terms)
                throw "Positional index belongs to a different Boolean index";
        }
        if (h[0].count!=b.terms) throw "Positional vocabulary size mismatch";
        path(p,directory,names[0]); terms_=std::fopen(p,"rb"); if (!terms_) throw "Cannot open positional vocabulary";
        std::strcpy(directory_,directory); index_=&index; count_=h[0].count; occurrences_=h[1].count; binding_=b.fingerprint; return true;
    } catch (const char* e) { *error=e; return false; }
}
bool gaming::PositionalIndex::lookup(const char* term,std::size_t size,TermSpace space,PositionList& list,const char** error) {
    *error=nullptr; list={};
    try {
        if (!terms_) throw "Phrase search requires --positions DIR";
        PostingList posting; U64 ordinal=UINT64_MAX; require(index_->lookup(term,size,space,posting,error,&ordinal),*error);
        if (ordinal==UINT64_MAX) return true;
        if (ordinal>=count_) throw "Invalid term ordinal";
        seek(terms_,80+ordinal*16); unsigned char p[16]; read(terms_,p,16); list={decode(p),decode(p+8)};
        if (list.offset>occurrences_ || list.count>occurrences_-list.offset) throw "Position list exceeds file";
        return true;
    } catch (const char* e) { *error=e; return false; }
}
FILE* gaming::PositionalIndex::stream(const PositionList& list,const char** error) const {
    *error=nullptr;
    try {
        if (!terms_ || list.offset>occurrences_ || list.count>occurrences_-list.offset) throw "Invalid position list";
        char p[4096]; path(p,directory_,names[1]); File file(p); const auto h=header(file.f,1);
        if (h.binding!=binding_ || h.count!=occurrences_) throw "Position file changed";
        seek(file.f,80+list.offset*8); FILE* result=file.f; file.f=nullptr; return result;
    } catch (const char* e) { *error=e; return nullptr; }
}
bool gaming::PositionalIndex::verify(const char** error) {
    *error=nullptr;
    try {
        if (!terms_) throw "Positional index is not open";
        char p[4096]; unsigned char block[65536];
        for (unsigned k=0;k<2;++k) {
            path(p,directory_,names[k]); File file(p); const auto h=header(file.f,k); seek(file.f,80); U64 checksum=basis; std::size_t n;
            while ((n=std::fread(block,1,sizeof(block),file.f))) checksum=hash(checksum,block,n);
            if (std::ferror(file.f) || checksum!=h.checksum) throw "Positional checksum mismatch";
        }
        path(p,directory_,names[1]); File positions(p); seek(positions.f,80); seek(terms_,80); U64 expected=0;
        for (U64 i=0;i<count_;++i) {
            unsigned char record[16]; read(terms_,record,16); const U64 offset=decode(record),count=decode(record+8);
            if (offset!=expected || count>occurrences_-expected) throw "Invalid positional list boundaries";
            U64 previous=0; bool first=true;
            for (U64 j=0;j<count;++j) {
                unsigned char r[8]; read(positions.f,r,8); const U64 doc=decode(r,4),key=(doc<<32)|decode(r+4,4);
                if (doc>=index_->documents() || (!first && key<=previous)) throw "Invalid positional coordinate order";
                previous=key; first=false;
            }
            expected+=count;
        }
        if (expected!=occurrences_) throw "Unreferenced positional coordinates";
        return true;
    } catch (const char* e) { *error=e; return false; }
}
gaming::PositionCursor::~PositionCursor() { if (file_) std::fclose(file_); }
bool gaming::PositionCursor::open(const PositionalIndex& index,const PositionList& list,const char** error) {
    if (file_) std::fclose(file_);
    file_=index.stream(list,error); remaining_=file_ ? list.count : 0; first_=true; return file_!=nullptr;
}
bool gaming::PositionCursor::next(U64& coordinate,const char** error) {
    *error=nullptr; coordinate=0;
    try {
        if (!remaining_) return false;
        unsigned char p[8]; read(file_,p,8); --remaining_;
        coordinate=(decode(p,4)<<32)|decode(p+4,4);
        if (!first_ && coordinate<=previous_) throw "Position coordinates must strictly increase";
        first_=false; previous_=coordinate; return true;
    } catch (const char* e) { *error=e; remaining_=0; return false; }
}
int gaming::run_positional_index(int argc,const char* const* argv,FILE* output,FILE* errors) {
    try {
        if (argc<2) throw "Expected build or verify";
        if (!std::strcmp(argv[1],"--help")) {
            std::fputs("UTF-8 positional index: positional_index build --corpus FILE --index BOOLEAN_DIR --output NEW_DIR [--dict FILE] [--memory-mib 64] [--reserve-mib 4608]\n"
                       "positional_index verify --index BOOLEAN_DIR --positions DIR\n"
                       "positional_index inspect --index BOOLEAN_DIR --positions DIR --term TOKEN [--space surface|lemma] [--limit 10]\n",output); return 0;
        }
        const char *corpus=nullptr,*base=nullptr,*out=nullptr,*dict=nullptr,*positions=nullptr,*term=nullptr;
        U64 memory=64,reserve=4608,limit=10; TermSpace space=TermSpace::surface;
        for (int i=2;i<argc;++i) {
            const char* arg=argv[i]; if (++i==argc) throw "Missing positional option value"; const char* value=argv[i];
            if (!std::strcmp(arg,"--corpus")) corpus=value; else if (!std::strcmp(arg,"--index")) base=value;
            else if (!std::strcmp(arg,"--output")) out=value; else if (!std::strcmp(arg,"--dict")) dict=value;
            else if (!std::strcmp(arg,"--positions")) positions=value;
            else if (!std::strcmp(arg,"--term")) term=value;
            else if (!std::strcmp(arg,"--limit")) limit=parse(value);
            else if (!std::strcmp(arg,"--space")) {
                if (!std::strcmp(value,"surface")) space=TermSpace::surface;
                else if (!std::strcmp(value,"lemma")) space=TermSpace::lemma;
                else throw "Unknown positional term space";
            }
            else if (!std::strcmp(arg,"--memory-mib")) memory=parse(value); else if (!std::strcmp(arg,"--reserve-mib")) reserve=parse(value);
            else throw "Unknown positional option";
        }
        if (!base) throw "--index is required";
        if (!std::strcmp(argv[1],"build")) {
            if (!corpus || !out || !memory || memory>2048 || reserve>UINT64_MAX/(1024*1024)) throw "Invalid positional build options";
            build(corpus,base,out,dict,memory,reserve*1024*1024,output);
        } else if (!std::strcmp(argv[1],"verify") || !std::strcmp(argv[1],"inspect")) {
            if (!positions) throw "--positions is required";
            BooleanIndex index; const char* error=nullptr; require(index.open(base,&error),error); PositionalIndex p;
            require(p.open(positions,base,index,&error),error);
            if (!std::strcmp(argv[1],"verify")) {
                require(p.verify(&error),error);
                std::fputs("Positions verified: checksums, binding, boundaries, coordinate order\n",output);
            } else {
                if (!term || !*term) throw "--term is required";
                PositionList list; require(p.lookup(term,std::strlen(term),space,list,&error),error);
                std::fprintf(output,"occurrences\t%llu\ndocument_id\tfield\tposition\n",static_cast<unsigned long long>(list.count));
                PositionCursor cursor; require(cursor.open(p,list,&error),error); U64 key=0,n=0;
                while (n<limit && cursor.next(key,&error)) {
                    DocumentInfo d; require(index.document_at(key>>32,d,&error),error);
                    if (!d.id) throw "Invalid coordinate document";
                    std::fprintf(output,"%llu\t%s\t%u\n",static_cast<unsigned long long>(d.id),
                                 (key&0x80000000ULL) ? "body" : "title",static_cast<unsigned>(key&0x7fffffffULL)); ++n;
                }
                if (error) throw error;
            }
        } else throw "Unknown positional mode";
        return 0;
    } catch (const char* e) { std::fprintf(errors,"[positional_index] %s\n",e); return 1; }
      catch (const std::exception&) { std::fputs("[positional_index] Allocation/tokenizer failure\n",errors); return 1; }
}
