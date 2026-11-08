#include "boolean_index.hpp"
#include "compact_index.hpp"
#include "skip_index.hpp"
#include "tokenizer.hpp"
#include "lemmatizer.hpp"
#include <chrono>
#include <cstdio>
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
using U64=std::uint64_t;
constexpr U64 header_size=80,max_field=64*1024*1024;
constexpr unsigned fan_in=24;
constexpr U64 fnv_start=14695981039346656037ULL;
U64 hash_bytes(U64 hash,const void* data,std::size_t size) {
    const auto* p=static_cast<const unsigned char*>(data);
    for (std::size_t i=0;i<size;++i) { hash^=p[i]; hash*=1099511628211ULL; }
    return hash;
}
void add(U64& value,U64 n=1) { if (n>UINT64_MAX-value) throw "Integer overflow"; value+=n; }
void write(FILE* f,const void* p,std::size_t size) {
    if (size && std::fwrite(p,1,size,f)!=size) throw "Cannot write index; check free disk space";
}
void read(FILE* f,void* p,std::size_t size) {
    if (size && std::fread(p,1,size,f)!=size) throw "Truncated or unreadable file";
}
void encode(unsigned char* p,U64 n,unsigned size=8) { for (unsigned i=0;i<size;++i) p[i]=static_cast<unsigned char>(n>>(i*8)); }
U64 decode(const unsigned char* p,unsigned size=8) {
    U64 value=0; for (unsigned i=0;i<size;++i) value|=U64(p[i])<<(i*8); return value;
}
void put_integer(FILE* f,U64 n,unsigned size=8) { unsigned char p[8]; encode(p,n,size); write(f,p,size); }
U64 integer(FILE* f,unsigned size=8) { unsigned char p[8]; read(f,p,size); return decode(p,size); }
void seek(FILE* f,U64 offset) {
    if (offset>INT64_MAX) throw "File offset overflow";
#ifdef _WIN32
    const int code=_fseeki64(f,static_cast<__int64>(offset),SEEK_SET);
#else
    const int code=fseeko(f,static_cast<off_t>(offset),SEEK_SET);
#endif
    if (code) throw "Cannot seek file";
}
U64 file_size(FILE* f) {
#ifdef _WIN32
    if (_fseeki64(f,0,SEEK_END)) throw "Cannot seek file";
    const auto size=_ftelli64(f);
#else
    if (fseeko(f,0,SEEK_END)) throw "Cannot seek file";
    const auto size=ftello(f);
#endif
    if (size<0) throw "Cannot determine file size";
    return static_cast<U64>(size);
}
void path(char* result,const char* directory,const char* name) {
    const auto a=std::strlen(directory),b=std::strlen(name);
    if (a>=4095 || b>=4095-a) throw "Path too long";
    std::memcpy(result,directory,a); result[a]='/'; std::memcpy(result+a+1,name,b+1);
}
struct File {
    FILE* f=nullptr;
    explicit File(const char* name,const char* mode="rb") {
        if (!std::strcmp(mode,"new")) {
#ifdef _WIN32
            const int fd=_open(name,_O_WRONLY|_O_CREAT|_O_EXCL|_O_BINARY,_S_IREAD|_S_IWRITE);
            if (fd>=0) { f=_fdopen(fd,"wb"); if (!f) _close(fd); }
#else
            const int fd=::open(name,O_WRONLY|O_CREAT|O_EXCL,0644);
            if (fd>=0) { f=fdopen(fd,"wb"); if (!f) ::close(fd); }
#endif
        } else f=std::fopen(name,mode);
        if (!f) throw "Cannot open input or exclusively create output file";
    }
    ~File() { if (f) std::fclose(f); }
    void finish() { const int code=std::fclose(f); f=nullptr; if (code) throw "Cannot close output"; }
};
struct Buffer {
    char* data=nullptr; std::size_t size=0,capacity=0;
    ~Buffer() { std::free(data); }
    void resize(std::size_t n) {
        if (n>max_field) throw "Field/token exceeds 64 MiB";
        if (n>capacity) {
            void* p=std::realloc(data,n); if (!p) throw "Allocation failed";
            data=static_cast<char*>(p); capacity=n;
        }
        size=n;
    }
};
bool utf8(const char* text,std::size_t n) {
    const auto* p=reinterpret_cast<const unsigned char*>(text);
    for (std::size_t i=0;i<n;) {
        const unsigned c=p[i++]; unsigned extra=0,code=0,min=0;
        if (c<128) { if (!c) return false; continue; }
        if (c>=0xc2 && c<=0xdf) { extra=1; code=c&31; min=0x80; }
        else if (c>=0xe0 && c<=0xef) { extra=2; code=c&15; min=0x800; }
        else if (c>=0xf0 && c<=0xf4) { extra=3; code=c&7; min=0x10000; }
        else return false;
        if (extra>n-i) return false;
        for (unsigned j=0;j<extra;++j) { const auto t=p[i++]; if ((t&0xc0)!=0x80) return false; code=(code<<6)|(t&63); }
        if (code<min || code>0x10ffff || (code>=0xd800 && code<=0xdfff)) return false;
    }
    return true;
}
struct Input {
    FILE* f; U64 hash=fnv_start;
    void bytes(void* p,std::size_t n) { read(f,p,n); hash=hash_bytes(hash,p,n); }
    U64 num(unsigned n=8) { unsigned char p[8]; bytes(p,n); return decode(p,n); }
    void field(Buffer& b) { const auto size=num(); if (size>max_field) throw "Invalid corpus field size"; b.resize(static_cast<std::size_t>(size)); bytes(b.data,b.size); }
};
struct Header {
    U64 count=0,bytes=0,hash=fnv_start,corpus=0,dictionary=0,tokenizer=0,flags=0,record_size=0,catalog=0;
};
const char* const names[]={"terms.bin","postings.bin","documents.bin","strings.bin"};
const char* const magics[]={"MAIIT7\0\1","MAIIP7\0\1","MAIID7\0\1","MAIIS7\0\1"};
constexpr U64 record_sizes[]={40,8,64,1};
Header get_header(FILE* f,unsigned kind,bool allow_compressed_postings=false) {
    seek(f,0); unsigned char p[80]; read(f,p,sizeof(p));
    if (std::memcmp(p,magics[kind],8)) throw "Invalid index header/version";
    Header h{decode(p+8),decode(p+16),decode(p+24),decode(p+32),decode(p+40),decode(p+48),decode(p+56),decode(p+64),decode(p+72)};
    if (h.record_size!=record_sizes[kind] || h.flags>3 || h.tokenizer!=hash_bytes(fnv_start,gaming::rules_version,std::strlen(gaming::rules_version)) ||
        h.count>UINT64_MAX/h.record_size || h.bytes!=h.count*h.record_size || h.bytes>INT64_MAX-header_size ||
        (file_size(f)!=header_size+h.bytes && !(allow_compressed_postings && (kind==0 || kind==1) && file_size(f)==header_size)))
        throw "Index header/size/tokenizer mismatch";
    return h;
}
struct Writer {
    File file; Header h; unsigned kind;
    Writer(const char* directory,unsigned k) : file(filename(directory,k),"new"),kind(k) {
        unsigned char zero[80]{}; write(file.f,zero,sizeof(zero)); h.record_size=record_sizes[k];
    }
    static const char* filename(const char* directory,unsigned kind) {
        // Used immediately by File; builders create writers sequentially in one thread.
        static char value[4096]; path(value,directory,names[kind]); return value;
    }
    void bytes(const void* p,std::size_t n) { write(file.f,p,n); h.hash=hash_bytes(h.hash,p,n); add(h.bytes,n); }
    U64 string(const char* p,std::size_t n) { const auto offset=h.bytes; bytes(p,n); return offset; }
    void num(U64 n) { unsigned char p[8]; encode(p,n); bytes(p,8); }
    void finish(U64 corpus,U64 dictionary,U64 flags,U64 catalog) {
        if (h.bytes%h.record_size) throw "Unaligned output record";
        h.count=h.bytes/h.record_size; h.corpus=corpus; h.dictionary=dictionary; h.flags=flags;
        h.tokenizer=hash_bytes(fnv_start,gaming::rules_version,std::strlen(gaming::rules_version));
        unsigned char p[80]{}; std::memcpy(p,magics[kind],8);
        const U64 values[]={h.count,h.bytes,h.hash,h.corpus,h.dictionary,h.tokenizer,h.flags,h.record_size,catalog};
        for (unsigned i=0;i<9;++i) encode(p+8+i*8,values[i]);
        seek(file.f,0); write(file.f,p,sizeof(p)); file.finish();
    }
};
struct Work {
    const char* directory; U64 serial=0,reserve;
    Work(const char* name,U64 min_free) : directory(name),reserve(min_free) {
        if (!*name || std::strlen(name)>3500) throw "Invalid output directory";
#ifdef _WIN32
        const int code=_mkdir(name);
#else
        const int code=mkdir(name,0755);
#endif
        if (code) throw "Output directory must be NEW and its parent must exist";
        char p[4096]; path(p,name,"INCOMPLETE"); File f(p,"new");
        const char note[]="Incomplete Boolean index; not readable.\n"; write(f.f,note,sizeof(note)-1); f.finish();
        check();
    }
    void check() const {
        U64 available=0;
#ifdef _WIN32
        ULARGE_INTEGER n;
        if (!GetDiskFreeSpaceExA(directory,&n,nullptr,nullptr)) throw "Cannot check free disk space";
        available=n.QuadPart;
#else
        struct statvfs n{};
        if (statvfs(directory,&n)) throw "Cannot check free disk space";
        available=U64(n.f_bavail)*n.f_frsize;
#endif
        if (available<reserve) throw "Disk reserve reached; index marked INCOMPLETE, corpus unchanged";
    }
    void next(char* p) { check(); std::snprintf(p,4096,"%s/run_%llu.bin",directory,static_cast<unsigned long long>(++serial)); }
};
struct Paths {
    char** data=nullptr; std::size_t size=0,capacity=0;
    ~Paths() { clear(); }
    void clear() { for (std::size_t i=0;i<size;++i) std::free(data[i]); std::free(data); data=nullptr; size=capacity=0; }
    void add_path(const char* path) {
        if (size==capacity) {
            const auto n=capacity ? 2*capacity : 16;
            void* p=std::realloc(data,n*sizeof(char*)); if (!p) throw "Path allocation failed";
            data=static_cast<char**>(p); capacity=n;
        }
        auto* p=static_cast<char*>(std::malloc(std::strlen(path)+1)); if (!p) throw "Path allocation failed";
        std::strcpy(p,path); data[size++]=p;
    }
    void take(Paths& other) { clear(); data=other.data; size=other.size; capacity=other.capacity; other.data=nullptr; other.size=other.capacity=0; }
};
struct Pair { std::uint32_t space=0,size=0; U64 doc=0; const char* word=nullptr; };
int term_compare(unsigned space,const char* word,std::size_t size,const Pair& b) {
    if (space!=b.space) return space<b.space ? -1 : 1;
    const auto length=size<b.size ? size : b.size;
    const int c=std::memcmp(word,b.word,length);
    if (c) return c;
    return size==b.size ? 0 : (size<b.size ? -1 : 1);
}
int compare(const Pair& a,const Pair& b) {
    const int c=term_compare(a.space,a.word,a.size,b);
    return c ? c : (a.doc==b.doc ? 0 : (a.doc<b.doc ? -1 : 1));
}
void swap(Pair& a,Pair& b) { const Pair t=a; a=b; b=t; }
void sift(Pair* rows,std::size_t n,std::size_t root) {
    while (root<n/2) {
        auto child=root*2+1;
        if (child+1<n && compare(rows[child],rows[child+1])<0) ++child;
        if (compare(rows[root],rows[child])>=0) return;
        swap(rows[root],rows[child]); root=child;
    }
}
void sort(Pair* rows,std::size_t n) {
    for (auto i=n/2;i>0;--i) sift(rows,n,i-1);
    for (auto i=n;i>1;--i) { swap(rows[0],rows[i-1]); sift(rows,i-1,0); }
}
void write_pair(FILE* f,const Pair& p) { put_integer(f,U64(p.space),4); put_integer(f,U64(p.size),4); put_integer(f,p.doc); write(f,p.word,p.size); }
struct RunReader {
    FILE* f=nullptr; Buffer buffer; Pair row;
    ~RunReader() { if (f) std::fclose(f); }
    bool next() {
        const int c=std::fgetc(f);
        if (c==EOF) { if (std::ferror(f)) throw "Run read error"; return false; }
        if (std::ungetc(c,f)==EOF) throw "Cannot read run prefix";
        row.space=static_cast<std::uint32_t>(integer(f,4)); row.size=static_cast<std::uint32_t>(integer(f,4)); row.doc=integer(f);
        if (row.space>1 || !row.size || !row.doc) throw "Invalid intermediate posting";
        buffer.resize(row.size); read(f,buffer.data,buffer.size); row.word=buffer.data; return true;
    }
};
class PairBuffer {
    std::uint32_t* slots_=nullptr; Pair* rows_=nullptr; char* pool_=nullptr;
    std::size_t capacity_=0,used_=0,pool_size_=0,pool_used_=0;
    Paths& runs_; Work& work_;
public:
    PairBuffer(std::size_t bytes,Paths& runs,Work& work) : runs_(runs),work_(work) {
        capacity_=2; while (capacity_*2<=bytes/32) capacity_*=2;
        pool_size_=bytes-capacity_*sizeof(std::uint32_t)-capacity_/2*sizeof(Pair);
        slots_=static_cast<std::uint32_t*>(std::calloc(capacity_,sizeof(std::uint32_t)));
        rows_=static_cast<Pair*>(std::malloc(capacity_/2*sizeof(Pair))); pool_=static_cast<char*>(std::malloc(pool_size_));
        if (!slots_ || !rows_ || !pool_) { std::free(slots_); std::free(rows_); std::free(pool_); throw "Pair-buffer allocation failed"; }
    }
    ~PairBuffer() { std::free(slots_); std::free(rows_); std::free(pool_); }
    void insert(unsigned space,const char* word,std::size_t size,U64 doc) {
        if (!size || size>pool_size_ || size>UINT32_MAX) throw "Token exceeds counting buffer; increase --memory-mib";
        U64 h=hash_bytes(fnv_start,word,size); h^=doc; h*=1099511628211ULL; h^=space;
        auto slot=static_cast<std::size_t>(h)&(capacity_-1);
        while (slots_[slot]) {
            const auto& p=rows_[slots_[slot]-1];
            if (p.doc==doc && p.space==space && p.size==size && !std::memcmp(p.word,word,size)) return;
            slot=(slot+1)&(capacity_-1);
        }
        if (used_==capacity_/2 || size>pool_size_-pool_used_) { flush(); slot=static_cast<std::size_t>(h)&(capacity_-1); }
        std::memcpy(pool_+pool_used_,word,size);
        rows_[used_]={space,static_cast<std::uint32_t>(size),doc,pool_+pool_used_};
        slots_[slot]=static_cast<std::uint32_t>(++used_); pool_used_+=size;
    }
    void flush() {
        if (!used_) return;
        sort(rows_,used_); char p[4096]; work_.next(p); File f(p,"new");
        for (std::size_t i=0;i<used_;++i) write_pair(f.f,rows_[i]);
        f.finish(); runs_.add_path(p); used_=pool_used_=0; std::memset(slots_,0,capacity_*sizeof(std::uint32_t));
    }
};
void merge(const Paths& runs,std::size_t begin,std::size_t end,const char* output) {
    RunReader readers[fan_in]; unsigned heap[fan_in]; std::size_t used=0;
    for (std::size_t i=begin;i<end;++i) {
        auto& r=readers[i-begin]; r.f=std::fopen(runs.data[i],"rb"); if (!r.f) throw "Cannot open sorted run";
        if (!r.next()) continue;
        std::size_t child=used++; heap[child]=static_cast<unsigned>(i-begin);
        while (child) {
            const auto parent=(child-1)/2;
            if (compare(readers[heap[parent]].row,readers[heap[child]].row)<=0) break;
            const auto t=heap[parent]; heap[parent]=heap[child]; heap[child]=t; child=parent;
        }
    }
    File file(output,"new"); Pair previous; Buffer word; bool have=false;
    while (used) {
        auto& r=readers[heap[0]];
        if (!have || compare(previous,r.row)!=0) {
            write_pair(file.f,r.row); word.resize(r.row.size); std::memcpy(word.data,r.row.word,word.size);
            previous=r.row; previous.word=word.data; have=true;
        }
        if (!r.next()) heap[0]=heap[--used];
        std::size_t parent=0;
        while (parent<used/2) {
            auto child=parent*2+1;
            if (child+1<used && compare(readers[heap[child+1]].row,readers[heap[child]].row)<0) ++child;
            if (compare(readers[heap[parent]].row,readers[heap[child]].row)<=0) break;
            const auto t=heap[parent]; heap[parent]=heap[child]; heap[child]=t; parent=child;
        }
    }
    file.finish();
}
unsigned reduce(Paths& runs,Work& work) {
    unsigned passes=0;
    while (runs.size>1) {
        Paths next;
        for (std::size_t begin=0;begin<runs.size;begin+=fan_in) {
            const auto end=runs.size-begin>fan_in ? begin+fan_in : runs.size;
            char p[4096]; work.next(p); merge(runs,begin,end,p); next.add_path(p);
            for (auto i=begin;i<end;++i) if (std::remove(runs.data[i])) throw "Cannot remove owned temporary file";
        }
        runs.take(next); ++passes;
    }
    return passes;
}
U64 positive(const char* p) {
    U64 n=0; if (!*p) throw "Expected positive integer";
    for (;*p;++p) { if (*p<'0' || *p>'9' || n>(UINT64_MAX-unsigned(*p-'0'))/10) throw "Invalid integer"; n=n*10+unsigned(*p-'0'); }
    if (!n) throw "Expected positive integer";
    return n;
}
U64 hash_file(const char* name) {
    File file(name); char buffer[65536]; U64 h=fnv_start;
    while (const auto n=std::fread(buffer,1,sizeof(buffer),file.f)) h=hash_bytes(h,buffer,n);
    if (std::ferror(file.f)) throw "Cannot hash input";
    return h;
}
bool source_valid(const char* p,std::size_t n) {
    for (std::size_t i=0;i<n;++i) if (!((p[i]>='a' && p[i]<='z') || (i && ((p[i]>='0' && p[i]<='9') || p[i]=='_')))) return false;
    return n>0;
}
struct Options {
    const char *corpus=nullptr,*output=nullptr,*dictionary=nullptr,*urls=nullptr;
    U64 memory=64,reserve=4608;
};
void build(const Options& options,FILE* output) {
    File corpus(options.corpus); Input input{corpus.f}; char magic[8]; input.bytes(magic,8);
    if (std::memcmp(magic,"MAIIR3\0\1",8)) throw "Unsupported corpus format";
    gaming::Lemmatizer dictionary; U64 dictionary_hash=0;
    if (options.dictionary) {
        const char* error=nullptr; if (!dictionary.load(options.dictionary,&error)) throw error;
        dictionary_hash=hash_file(options.dictionary);
    }
    struct OptionalFile { FILE* f=nullptr; ~OptionalFile() { if (f) std::fclose(f); } } urls;
    if (options.urls) {
        urls.f=std::fopen(options.urls,"rb"); if (!urls.f) throw "Cannot open URL catalog";
        read(urls.f,magic,8); if (std::memcmp(magic,"MAIIU7\0\1",8)) throw "Invalid URL catalog header";
    }
    const U64 catalog_hash=options.urls ? hash_file(options.urls) : 0;
    Work work(options.output,options.reserve*1024*1024);
    Writer terms(options.output,0),postings(options.output,1),documents(options.output,2),strings(options.output,3);
    const auto start=std::chrono::steady_clock::now(); Paths runs;
    U64 doc_count=0,tokens=0,emissions=0,input_bytes=0,last_doc=0,known=0,ambiguous=0;
    {
        PairBuffer buffer(static_cast<std::size_t>(options.memory)*1024*1024,runs,work); Buffer field,url;
        for (;;) {
            const int c=std::fgetc(corpus.f);
            if (c==EOF) { if (std::ferror(corpus.f)) throw "Cannot read corpus"; break; }
            if (std::ungetc(c,corpus.f)==EOF) throw "Cannot read corpus prefix";
            const auto id=input.num(); if (id<=last_doc) throw "Document IDs must strictly increase"; last_doc=id;
            const auto n=input.num(4); if (!n || n>64) throw "Invalid source length";
            char source[64]; input.bytes(source,static_cast<std::size_t>(n)); if (!source_valid(source,static_cast<std::size_t>(n))) throw "Invalid source";
            U64 url_offset=0,url_size=0;
            if (urls.f) {
                if (integer(urls.f)!=id) throw "URL catalog and corpus IDs differ";
                url_size=integer(urls.f,4); if (!url_size || url_size>1024*1024) throw "Invalid URL size";
                url.resize(static_cast<std::size_t>(url_size)); read(urls.f,url.data,url.size);
                if (!utf8(url.data,url.size)) throw "Invalid URL UTF-8";
                url_offset=strings.string(url.data,url.size);
            }
            const auto source_offset=strings.string(source,static_cast<std::size_t>(n));
            input.field(field); const auto title_size=field.size,title_offset=strings.string(field.data,field.size);
            documents.num(id); documents.num(title_offset); documents.num(title_size); documents.num(url_offset);
            documents.num(url_size); documents.num(source_offset); documents.num(n); documents.num(0);
            for (unsigned f=0;f<2;++f) {
                if (f) input.field(field);
                add(input_bytes,field.size);
                gaming::tokenize(std::string_view(field.data ? field.data : "",field.size),[&](const gaming::Token& token) {
                    add(tokens); buffer.insert(0,token.term.data(),token.term.size(),id);
                    if (options.dictionary) {
                        gaming::LemmaResult result;
                        if (!dictionary.analyze(token.term.data(),token.term.size(),result)) throw "Too many lemma alternatives";
                        add(known,result.known); add(ambiguous,result.count>1);
                        for (std::size_t i=0;i<result.count;++i) {
                            buffer.insert(1,result.values[i].data,result.values[i].size,id); add(emissions);
                        }
                    }
                });
            }
            add(doc_count);
            if (doc_count%50000==0) { work.check(); std::fprintf(output,"indexed documents: %llu\n",static_cast<unsigned long long>(doc_count)); std::fflush(output); }
        }
        buffer.flush();
    }
    if (urls.f && (std::fgetc(urls.f)!=EOF || std::ferror(urls.f))) throw "URL catalog has extra records/read error";
    const auto initial_runs=runs.size; const auto passes=reduce(runs,work);
    U64 vocabulary[2]{},posting_counts[2]{};
    if (runs.size) {
        RunReader reader; reader.f=std::fopen(runs.data[0],"rb"); if (!reader.f) throw "Cannot open merged postings";
        Pair previous; Buffer word; bool have=false; U64 offset=0,df=0,term_offset=0;
        auto save_term=[&]() {
            unsigned char row[40]{}; encode(row,previous.space,4); encode(row+4,previous.size,4);
            encode(row+8,term_offset); encode(row+16,offset); encode(row+24,df); terms.bytes(row,sizeof(row));
            add(vocabulary[previous.space]);
        };
        while (reader.next()) {
            const auto& p=reader.row;
            if (!have || term_compare(p.space,p.word,p.size,previous)) {
                if (have) save_term();
                word.resize(p.size); std::memcpy(word.data,p.word,p.size); previous=p; previous.word=word.data;
                offset=postings.h.bytes; df=0; term_offset=strings.string(p.word,p.size); have=true;
            } else if (p.doc<=previous.doc) throw "Merged postings are not unique/increasing";
            postings.num(p.doc); previous.doc=p.doc; add(df); add(posting_counts[p.space]);
            if ((posting_counts[0]+posting_counts[1])%1000000==0) work.check();
        }
        if (have) save_term();
    }
    if (runs.size && std::remove(runs.data[0])) throw "Cannot remove owned merged run";
    const U64 flags=(options.dictionary ? 1ULL : 0ULL)|(options.urls ? 2ULL : 0ULL);
    terms.finish(input.hash,dictionary_hash,flags,catalog_hash); postings.finish(input.hash,dictionary_hash,flags,catalog_hash);
    documents.finish(input.hash,dictionary_hash,flags,catalog_hash); strings.finish(input.hash,dictionary_hash,flags,catalog_hash);
    char p[4096]; path(p,options.output,"statistics.json");
    {
        File stats(p,"new");
        const auto elapsed=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
        std::fprintf(stats.f,"{\n\"format\":\"MAIIR-BOOLEAN-v1\",\"tokenizer\":\"%s\",\"documents\":%llu,\"tokens\":%llu,"
            "\"input_utf8_bytes\":%llu,\"surface_terms\":%llu,\"lemma_terms\":%llu,\"surface_postings\":%llu,\"lemma_postings\":%llu,"
            "\"lemma_emissions\":%llu,\"known_tokens\":%llu,\"ambiguous_tokens\":%llu,\"buffer_bytes\":%llu,"
            "\"dictionary_memory_bytes\":%llu,\"initial_runs\":%llu,\"merge_passes\":%u,\"temporary_files_created\":%llu,"
            "\"elapsed_ms\":%.6f,\"has_lemmas\":%s,\"has_urls\":%s,\"corpus_fnv64\":\"%016llx\",\"dictionary_fnv64\":\"%016llx\"\n}\n",
            gaming::rules_version,static_cast<unsigned long long>(doc_count),static_cast<unsigned long long>(tokens),
            static_cast<unsigned long long>(input_bytes),static_cast<unsigned long long>(vocabulary[0]),static_cast<unsigned long long>(vocabulary[1]),
            static_cast<unsigned long long>(posting_counts[0]),static_cast<unsigned long long>(posting_counts[1]),
            static_cast<unsigned long long>(emissions),static_cast<unsigned long long>(known),static_cast<unsigned long long>(ambiguous),
            static_cast<unsigned long long>(options.memory*1024*1024),static_cast<unsigned long long>(dictionary.memory_bytes()),
            static_cast<unsigned long long>(initial_runs),passes,static_cast<unsigned long long>(work.serial),elapsed,
            options.dictionary ? "true" : "false",options.urls ? "true" : "false",
            static_cast<unsigned long long>(input.hash),static_cast<unsigned long long>(dictionary_hash));
        if (std::ferror(stats.f)) throw "Cannot write statistics";
        stats.finish();
    }
    path(p,options.output,"INCOMPLETE"); if (std::remove(p)) throw "Cannot finalize index";
    std::fprintf(output,"Boolean index complete: %llu documents, %llu terms, %llu postings\n",
        static_cast<unsigned long long>(doc_count),static_cast<unsigned long long>(vocabulary[0]+vocabulary[1]),
        static_cast<unsigned long long>(posting_counts[0]+posting_counts[1]));
}
struct TermRecord { std::uint32_t space=0,size=0; U64 string=0,offset=0,count=0; };
TermRecord term_record(FILE* f,U64 position) {
    seek(f,header_size+position*40); unsigned char p[40]; read(f,p,sizeof(p));
    if (decode(p+32)) throw "Invalid term reserved field";
    return {static_cast<std::uint32_t>(decode(p,4)),static_cast<std::uint32_t>(decode(p+4,4)),decode(p+8),decode(p+16),decode(p+24)};
}
void bounds(U64 offset,U64 n,U64 total) { if (offset>total || n>total-offset) throw "Index offset/length outside file"; }
gaming::DocumentInfo doc_record(FILE* f,U64 position,U64 strings) {
    seek(f,header_size+position*64); unsigned char p[64]; read(f,p,sizeof(p));
    if (decode(p+56)) throw "Invalid document reserved field";
    gaming::DocumentInfo d{decode(p),decode(p+8),decode(p+16),decode(p+24),decode(p+32),decode(p+40),decode(p+48)};
    if (!d.id || d.title_size>max_field || d.url_size>1024*1024 || !d.source_size || d.source_size>64) throw "Invalid document record";
    bounds(d.title_offset,d.title_size,strings); bounds(d.url_offset,d.url_size,strings); bounds(d.source_offset,d.source_size,strings); return d;
}
}

gaming::BooleanIndex::~BooleanIndex() { close(); }
void gaming::BooleanIndex::close() {
    if (term_file_) std::fclose(term_file_);
    if (doc_file_) std::fclose(doc_file_);
    if (string_file_) std::fclose(string_file_);
    term_file_=doc_file_=string_file_=nullptr; docs_=terms_=postings_=strings_=dictionary_hash_=flags_=0; directory_[0]=0; compressed_=nullptr; skips_=nullptr; stats_=nullptr;
}
bool gaming::BooleanIndex::open(const char* directory,const char** error,bool allow_compressed_postings) {
    close(); *error=nullptr;
    try {
        char p[4096]; path(p,directory,"INCOMPLETE");
        if (FILE* marker=std::fopen(p,"rb")) { std::fclose(marker); throw "Index is INCOMPLETE"; }
        Header h[4];
        for (unsigned i=0;i<4;++i) { path(p,directory,names[i]); File f(p); h[i]=get_header(f.f,i,allow_compressed_postings); }
        for (unsigned i=1;i<4;++i)
            if (h[i].corpus!=h[0].corpus || h[i].dictionary!=h[0].dictionary || h[i].flags!=h[0].flags || h[i].catalog!=h[0].catalog)
                throw "Index files belong to different builds";
        if (!(h[0].flags&1) && h[0].dictionary) throw "Invalid dictionary metadata";
        path(p,directory,names[0]); term_file_=std::fopen(p,"rb"); path(p,directory,names[2]); doc_file_=std::fopen(p,"rb");
        path(p,directory,names[3]); string_file_=std::fopen(p,"rb");
        if (!term_file_ || !doc_file_ || !string_file_) throw "Cannot open index streams";
        std::strcpy(directory_,directory); terms_=h[0].count; postings_=h[1].count; docs_=h[2].count; strings_=h[3].bytes;
        dictionary_hash_=h[0].dictionary; flags_=h[0].flags; return true;
    } catch (const char* e) { close(); *error=e; return false; }
}
bool gaming::BooleanIndex::lookup(const char* word,std::size_t size,TermSpace space,PostingList& result,const char** error,U64* term_number) {
    result={}; *error=nullptr;
    if (term_number) *term_number=UINT64_MAX;
    try {
        if (!term_file_) throw "Index is not open";
        if (!word || !size || size>max_field || !utf8(word,size)) throw "Invalid lookup token";
        if (static_cast<unsigned>(space)>1 || (space==TermSpace::lemma && !has_lemmas())) throw "Requested term representation is unavailable";
        if (compressed_ && compressed_->has_vocabulary()) {
            if (!compressed_->lookup(word,size,space,result,error)) throw *error;
            if (term_number) *term_number=result.ordinal;
            return true;
        }
        Buffer buffer; U64 left=0,right=terms_;
        while (left<right) {
            const auto mid=left+(right-left)/2; const auto r=term_record(term_file_,mid);
            if (r.space>1 || !r.size || r.size>max_field || !r.count || r.count>docs_ || r.offset%8) throw "Invalid vocabulary record";
            bounds(r.string,r.size,strings_); bounds(r.offset,r.count*8,postings_*8);
            buffer.resize(r.size); seek(string_file_,header_size+r.string); read(string_file_,buffer.data,buffer.size);
            const Pair p{r.space,r.size,0,buffer.data}; const int c=term_compare(static_cast<unsigned>(space),word,size,p);
            if (!c) { result={r.offset,r.count,mid}; if (term_number) *term_number=mid; return true; }
            if (c<0) right=mid; else left=mid+1;
        }
        return true; // a valid absent term has an empty posting list
    } catch (const char* e) { *error=e; return false; }
}
bool gaming::BooleanIndex::document(U64 id,DocumentInfo& result,const char** error) {
    result={}; *error=nullptr;
    try {
        if (!doc_file_) throw "Index is not open";
        U64 left=0,right=docs_;
        while (left<right) {
            const auto mid=left+(right-left)/2; const auto d=doc_record(doc_file_,mid,strings_);
            if (d.id==id) { result=d; return true; }
            if (id<d.id) right=mid; else left=mid+1;
        }
        return true;
    } catch (const char* e) { *error=e; return false; }
}
bool gaming::BooleanIndex::document_at(U64 position,DocumentInfo& result,const char** error) {
    result={}; *error=nullptr;
    try {
        if (!doc_file_) throw "Index is not open";
        if (position<docs_) result=doc_record(doc_file_,position,strings_);
        return true;
    } catch (const char* e) { *error=e; return false; }
}
bool gaming::BooleanIndex::text(U64 offset,std::size_t size,char* destination,const char** error) {
    *error=nullptr;
    try { if (!string_file_) throw "Index is not open"; bounds(offset,size,strings_); seek(string_file_,header_size+offset); read(string_file_,destination,size); return true; }
    catch (const char* e) { *error=e; return false; }
}
FILE* gaming::BooleanIndex::open_postings(const PostingList& list,const char** error) const {
    *error=nullptr;
    try {
        if (!term_file_) throw "Index is not open";
        if (list.count>postings_ || list.offset%8) throw "Invalid posting handle";
        bounds(list.offset,list.count*8,postings_*8); char p[4096]; path(p,directory_,names[1]);
        FILE* file=std::fopen(p,"rb"); if (!file) throw "Cannot open postings";
        try { seek(file,header_size+list.offset); } catch (...) { std::fclose(file); throw; }
        return file;
    } catch (const char* e) { *error=e; return nullptr; }
}
gaming::PostingCursor::~PostingCursor() { if (file_) std::fclose(file_); std::free(skips_); }
bool gaming::PostingCursor::open(const BooleanIndex& index,const PostingList& list,const char** error) {
    if (file_) std::fclose(file_);
    std::free(skips_); skips_=nullptr; skip_count_=0; stats_=index.stats(); total_=list.count;
    file_=nullptr; previous_=remaining_=bytes_left_=0; compressed_=index.compressed()!=nullptr;
    if (compressed_ && list.ordinal!=UINT64_MAX)
        file_=index.compressed()->stream(CompactKind::postings,list.ordinal,bytes_left_,error);
    else { compressed_=false; file_=index.open_postings(list,error); }
    if (!file_) return false;
    remaining_=list.count; total_bytes_=bytes_left_;
    if (compressed_ && index.skips()) {
#ifdef _WIN32
        stream_start_=static_cast<U64>(_ftelli64(file_));
#else
        stream_start_=static_cast<U64>(ftello(file_));
#endif
        if (!index.skips()->load(0,list.ordinal,list.count,total_bytes_,skips_,skip_count_,error)) return false;
    }
    return true;
}
bool gaming::PostingCursor::next(U64& id,const char** error) {
    *error=nullptr; id=0;
    if (!remaining_) return false;
    try {
        if (compressed_) {
            U64 delta=0;
            if (!read_compact_uint(file_,bytes_left_,delta) || !delta || delta>UINT64_MAX-previous_) throw "Invalid compressed posting";
            id=previous_+delta;
        } else id=integer(file_);
        if (id<=previous_) throw "Posting IDs must strictly increase";
        previous_=id; --remaining_;
        if (stats_) ++stats_->decoded_postings;
        if (compressed_ && !remaining_ && bytes_left_) throw "Trailing compressed posting bytes";
        return true;
    }
    catch (const char* e) { *error=e; remaining_=0; return false; }
}
bool gaming::PostingCursor::advance(U64 target,U64& id,const char** error) {
    *error=nullptr; id=0;
    try {
        const U64 used=total_-remaining_;
        if (used && previous_>=target) { id=previous_; return true; }
        U64 left=0,right=skip_count_;
        while (left<right) { const U64 mid=left+(right-left)/2; if (skips_[mid].value<=target) left=mid+1; else right=mid; }
        if (left && skips_[left-1].consumed>used) {
            const auto& p=skips_[left-1]; seek(file_,stream_start_+p.bytes);
            if (stats_) { ++stats_->jumps; stats_->skipped+=p.consumed-used; }
            previous_=p.value; remaining_=total_-p.consumed; bytes_left_=total_bytes_-p.bytes;
            if (previous_==target) { id=previous_; return true; }
        }
        while (next(id,error)) if (id>=target) return true;
        return false;
    } catch (const char* e) { *error=e; remaining_=0; return false; }
}
bool gaming::BooleanIndex::verify(const char** error) {
    *error=nullptr;
    try {
        if (!term_file_) throw "Index is not open";
        char p[4096],block[65536];
        for (unsigned i=0;i<4;++i) {
            path(p,directory_,names[i]); File file(p); const auto h=get_header(file.f,i); seek(file.f,header_size);
            U64 hash=fnv_start; std::size_t n;
            while ((n=std::fread(block,1,sizeof(block),file.f))!=0) hash=hash_bytes(hash,block,n);
            if (std::ferror(file.f) || hash!=h.hash) throw "Index payload checksum mismatch";
        }
        U64 previous_id=0;
        for (U64 i=0;i<docs_;++i) {
            const auto d=doc_record(doc_file_,i,strings_); if (d.id<=previous_id) throw "Document IDs not increasing"; previous_id=d.id;
        }
        // Merge-validate every posting against the sorted document universe, once per term.
        // Consecutive snapshot IDs use the O(1) check; arbitrary IDs use binary search.
        bool contiguous=true;
        for (U64 i=0;i<docs_;++i) if (doc_record(doc_file_,i,strings_).id!=i+1) { contiguous=false; break; }
        Pair previous; Buffer word,current; bool have=false; U64 expected_offset=0;
        path(p,directory_,names[1]); File posting_file(p); seek(posting_file.f,header_size);
        for (U64 i=0;i<terms_;++i) {
            const auto r=term_record(term_file_,i);
            if (r.space>1 || (r.space && !has_lemmas()) || !r.size || !r.count || r.count>docs_ || r.offset!=expected_offset) throw "Invalid vocabulary ordering/list bounds";
            bounds(r.string,r.size,strings_); bounds(r.offset,r.count*8,postings_*8);
            current.resize(r.size); seek(string_file_,header_size+r.string); read(string_file_,current.data,current.size);
            if (!utf8(current.data,current.size)) throw "Invalid UTF-8 term";
            Pair row{r.space,r.size,0,current.data}; if (have && term_compare(row.space,row.word,row.size,previous)<=0) throw "Vocabulary is not strictly sorted";
            U64 last=0;
            for (U64 n=0;n<r.count;++n) {
                const auto id=integer(posting_file.f);
                if (id<=last) throw "Posting IDs must strictly increase";
                last=id;
                if (contiguous) { if (id>docs_) throw "Posting references unknown document"; }
                else { DocumentInfo d; if (!document(id,d,error)) throw *error; if (!d.id) throw "Posting references unknown document"; }
            }
            word.resize(row.size); std::memcpy(word.data,row.word,row.size); previous=row; previous.word=word.data; have=true;
            add(expected_offset,r.count*8);
        }
        if (expected_offset!=postings_*8) throw "Unreferenced posting bytes";
        return true;
    } catch (const char* e) { *error=e; return false; }
}

int gaming::run_boolean_index(int argc,const char* const* argv,FILE* output,FILE* errors) {
    try {
        if (argc==2 && !std::strcmp(argv[1],"--help")) {
            std::fputs("boolean_index build --corpus FILE --output NEW_DIR [--urls CATALOG.bin] [--dict FILE] [--memory-mib 64] [--reserve-mib 4608]\n"
                "boolean_index inspect --index DIR --term UTF8_TOKEN [--space surface|lemma] [--limit 10]\n"
                "boolean_index verify --index DIR\nUTF-8; inspect expects an already normalized single token. No Boolean query parser (Lab 8).\n",output); return 0;
        }
        if (argc<2) throw "Missing mode; use --help";
        const bool building=!std::strcmp(argv[1],"build"),inspecting=!std::strcmp(argv[1],"inspect"),verifying=!std::strcmp(argv[1],"verify");
        if (!building && !inspecting && !verifying) throw "Unknown mode";
        Options options; const char *directory=nullptr,*term=nullptr,*space="surface"; U64 limit=10; unsigned seen=0;
        for (int i=2;i<argc;i+=2) {
            if (i+1==argc) throw "Missing option value";
            const char *key=argv[i],*value=argv[i+1]; unsigned bit=0;
            if (building && !std::strcmp(key,"--corpus")) { bit=1; options.corpus=value; }
            else if (building && !std::strcmp(key,"--output")) { bit=2; options.output=value; }
            else if (building && !std::strcmp(key,"--dict")) { bit=4; options.dictionary=value; }
            else if (building && !std::strcmp(key,"--urls")) { bit=8; options.urls=value; }
            else if (building && !std::strcmp(key,"--memory-mib")) { bit=16; options.memory=positive(value); }
            else if (building && !std::strcmp(key,"--reserve-mib")) { bit=32; options.reserve=positive(value); }
            else if (!building && !std::strcmp(key,"--index")) { bit=64; directory=value; }
            else if (inspecting && !std::strcmp(key,"--term")) { bit=128; term=value; }
            else if (inspecting && !std::strcmp(key,"--space")) { bit=256; space=value; }
            else if (inspecting && !std::strcmp(key,"--limit")) { bit=512; limit=positive(value); }
            else throw "Unknown/inapplicable option";
            if (seen&bit) throw "Duplicate option";
            seen|=bit;
        }
        if (building) {
            if (!options.corpus || !options.output || options.memory>2048 || options.reserve>UINT64_MAX/(1024*1024)) throw "Invalid build options";
            build(options,output); return 0;
        }
        if (!directory) throw "--index is required";
        BooleanIndex index; const char* error=nullptr; if (!index.open(directory,&error)) throw error;
        if (verifying) { if (!index.verify(&error)) throw error; std::fputs("Index verified: checksums, dictionary, postings, documents\n",output); return 0; }
        if (!term || !*term || !utf8(term,std::strlen(term)) || (std::strcmp(space,"surface") && std::strcmp(space,"lemma"))) throw "Invalid inspect term/space";
        PostingList list; if (!index.lookup(term,std::strlen(term),!std::strcmp(space,"lemma") ? TermSpace::lemma : TermSpace::surface,list,&error)) throw error;
        std::fprintf(output,"term\t%s\nspace\t%s\ndocument_frequency\t%llu\ndocument_ids",term,space,static_cast<unsigned long long>(list.count));
        PostingCursor cursor; if (!cursor.open(index,list,&error)) throw error;
        U64 id,n=0; while (n<limit && cursor.next(id,&error)) { std::fprintf(output,"\t%llu",static_cast<unsigned long long>(id)); ++n; }
        if (error) throw error;
        std::fputc('\n',output); return 0;
    } catch (const char* error) { std::fprintf(errors,"[boolean_index] %s\n",error); return 1; }
      catch (const std::exception& error) { std::fprintf(errors,"[boolean_index] %s\n",error.what()); return 1; }
}
