#include "compact_index.hpp"
#include "boolean_index.hpp"
#include "positional_index.hpp"
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
constexpr U64 basis=14695981039346656037ULL;
const char* files[]={"compact_posting_terms.bin","compact_postings.bin","compact_position_terms.bin","compact_positions.bin",
                     "compact_vocabulary_blocks.bin","compact_vocabulary.bin"};
const char* magics[]={"MAICTM\0\2","MAICPO\0\2","MAICCM\0\2","MAICCO\0\2","MAICVB\0\2","MAICVD\0\2"};
const char* base_names[]={"terms.bin","postings.bin","documents.bin","strings.bin","position_terms.bin","positions.bin"};
void require(bool ok,const char* const& error) { if (!ok) throw (error ? error : "Index operation failed"); }
U64 hash(U64 h,const void* data,std::size_t size) {
    const auto* p=static_cast<const unsigned char*>(data);
    for (std::size_t i=0;i<size;++i) { h^=p[i]; h*=1099511628211ULL; } return h;
}
void path(char* out,const char* d,const char* n) {
    const auto a=std::strlen(d),b=std::strlen(n);
    if (a>4094 || b>4094-a) throw "Compact path too long";
    std::memcpy(out,d,a); out[a]='/'; std::memcpy(out+a+1,n,b+1);
}
void read(FILE* f,void* p,std::size_t n) { if (n && std::fread(p,1,n,f)!=n) throw "Truncated compact input"; }
void write(FILE* f,const void* p,std::size_t n) { if (n && std::fwrite(p,1,n,f)!=n) throw "Cannot write compact index"; }
void encode(unsigned char* p,U64 v,unsigned n=8) { for (unsigned i=0;i<n;++i) p[i]=static_cast<unsigned char>(v>>(i*8)); }
U64 decode(const unsigned char* p,unsigned n=8) { U64 v=0; for (unsigned i=0;i<n;++i) v|=U64(p[i])<<(8*i); return v; }
void seek(FILE* f,U64 n) {
    if (n>INT64_MAX) throw "Compact offset overflow";
#ifdef _WIN32
    if (_fseeki64(f,static_cast<__int64>(n),SEEK_SET)) throw "Cannot seek compact file";
#else
    if (fseeko(f,static_cast<off_t>(n),SEEK_SET)) throw "Cannot seek compact file";
#endif
}
U64 length(FILE* f) {
#ifdef _WIN32
    if (_fseeki64(f,0,SEEK_END)) throw "Cannot size compact file";
    const auto n=_ftelli64(f);
#else
    if (fseeko(f,0,SEEK_END)) throw "Cannot size compact file";
    const auto n=ftello(f);
#endif
    if (n<0) throw "Cannot size compact file";
    return static_cast<U64>(n);
}
struct File {
    FILE* f=nullptr;
    explicit File(const char* p,const char* mode="rb") {
        if (!std::strcmp(mode,"new")) {
#ifdef _WIN32
            const int fd=_open(p,_O_CREAT|_O_EXCL|_O_RDWR|_O_BINARY,_S_IREAD|_S_IWRITE);
            if (fd>=0) { f=_fdopen(fd,"w+b"); if (!f) _close(fd); }
#else
            const int fd=::open(p,O_CREAT|O_EXCL|O_RDWR,0644);
            if (fd>=0) { f=fdopen(fd,"w+b"); if (!f) ::close(fd); }
#endif
        } else f=std::fopen(p,mode);
        if (!f) throw "Cannot open compact input or exclusively create output";
    }
    ~File() { if (f) std::fclose(f); }
    void close() { FILE* old=f; f=nullptr; if (old && std::fclose(old)) throw "Cannot close compact output"; }
};
struct Binding { U64 value=basis,terms=0,documents=0,postings=0,positions=0; };
Binding binding(const char* boolean_dir,const char* positional_dir) {
    Binding b; unsigned char p[80]; char name[4096];
    for (unsigned i=0;i<6;++i) {
        path(name,i<4 ? boolean_dir : positional_dir,base_names[i]); File file(name);
        read(file.f,p,80); b.value=hash(b.value,p,80);
        if (i==0) b.terms=decode(p+8);
        else if (i==1) b.postings=decode(p+8);
        else if (i==2) b.documents=decode(p+8);
        else if (i==5) b.positions=decode(p+8);
    }
    return b;
}
struct Header { U64 count=0,bytes=0,checksum=basis,binding=0,terms=0,postings=0,coordinates=0,width=0,documents=0; };
Header header(FILE* f,unsigned kind) {
    seek(f,0); unsigned char p[80]; read(f,p,80);
    if (std::memcmp(p,magics[kind],8)) throw "Invalid compact format/version";
    Header h{decode(p+8),decode(p+16),decode(p+24),decode(p+32),decode(p+40),decode(p+48),decode(p+56),decode(p+64),decode(p+72)};
    const unsigned width=kind==4 ? 8 : kind==5 || kind%2 ? 1 : 16;
    if (h.width!=width || h.count>UINT64_MAX/h.width || h.bytes!=h.count*h.width ||
        h.bytes>INT64_MAX-80 || length(f)!=80+h.bytes) throw "Compact header/size mismatch";
    return h;
}
struct Writer {
    File file; Header h; U64 bytes=0; unsigned kind;
    Writer(const char* d,unsigned k):file(name(d,k),"new"),kind(k) {
        std::setvbuf(file.f,nullptr,_IOFBF,65536);
        unsigned char zero[80]{}; write(file.f,zero,80); h.width=k==4 ? 8 : k==5 || k%2 ? 1 : 16;
    }
    static const char* name(const char* d,unsigned k) { static char p[4096]; path(p,d,files[k]); return p; }
    void put(const void* p,std::size_t n) {
        write(file.f,p,n); h.checksum=hash(h.checksum,p,n);
        if (n>UINT64_MAX-h.bytes) throw "Compact byte count overflow"; h.bytes+=n;
    }
    void num(U64 n) { unsigned char p[16]; encode(p,n); put(p,8); }
    void entry(U64 offset,U64 size) { num(offset); num(size); }
    void var(U64 value) {
        unsigned char p[10]; unsigned n=0;
        do { auto byte=static_cast<unsigned char>(value&127); value>>=7; p[n++]=static_cast<unsigned char>(byte|(value ? 0 : 128)); } while (value);
        put(p,n);
    }
    void finish(const Binding& b) {
        h.count=h.bytes/h.width; h.binding=b.value; h.terms=b.terms; h.postings=b.postings; h.coordinates=b.positions; h.documents=b.documents;
        unsigned char p[80]{}; std::memcpy(p,magics[kind],8);
        const U64 values[]={h.count,h.bytes,h.checksum,h.binding,h.terms,h.postings,h.coordinates,h.width,h.documents};
        for (unsigned i=0;i<9;++i) encode(p+8+i*8,values[i]);
        seek(file.f,0); write(file.f,p,80); file.close();
    }
};
struct Work {
    const char* directory; U64 reserve;
    Work(const char* d,U64 bytes):directory(d),reserve(bytes) {
#ifdef _WIN32
        if (_mkdir(d)) throw "Compact output must be a NEW directory";
#else
        if (mkdir(d,0755)) throw "Compact output must be a NEW directory";
#endif
        char p[4096]; path(p,d,"INCOMPLETE"); File marker(p,"new"); write(marker.f,"Incomplete\n",11); marker.close(); check();
    }
    void check() const {
        U64 free=0;
#ifdef _WIN32
        ULARGE_INTEGER n; if (!GetDiskFreeSpaceExA(directory,&n,nullptr,nullptr)) throw "Cannot check compact disk space"; free=n.QuadPart;
#else
        struct statvfs n{}; if (statvfs(directory,&n)) throw "Cannot check compact disk space"; free=U64(n.f_bavail)*n.f_frsize;
#endif
        if (free<reserve) throw "Compact disk reserve reached";
    }
    void complete() const { char p[4096]; path(p,directory,"INCOMPLETE"); if (std::remove(p)) throw "Cannot publish compact index"; }
};
void build(const char* base,const char* positions,const char* output,U64 reserve,FILE* log) {
    const auto start=std::chrono::steady_clock::now();
    gaming::BooleanIndex bool_index; const char* error=nullptr; require(bool_index.open(base,&error),error);
    gaming::PositionalIndex pos_index; require(pos_index.open(positions,base,bool_index,&error),error);
    require(bool_index.verify(&error),error); require(pos_index.verify(&error),error);
    Binding b=binding(base,positions); Work work(output,reserve);
    char p[4096]; path(p,base,"postings.bin"); File raw_postings(p); seek(raw_postings.f,80);
    path(p,base,"terms.bin"); File raw_terms(p); seek(raw_terms.f,80);
    path(p,positions,"positions.bin"); File raw_positions(p); seek(raw_positions.f,80);
    path(p,positions,"position_terms.bin"); File raw_pos_terms(p); seek(raw_pos_terms.f,80);
    Writer posting_terms(output,0),postings(output,1),position_terms(output,2),coordinates(output,3);
    U64 expected_posting=0,expected_coordinate=0,surface_posting_bytes=0,lemma_posting_bytes=0,surface_coordinate_bytes=0,lemma_coordinate_bytes=0;
    for (U64 term=0;term<b.terms;++term) {
        unsigned char record[40]; read(raw_terms.f,record,40); const auto space=decode(record,4);
        if (space>1) throw "Invalid compact source namespace";
        const U64 poffset=decode(record+16),pcount=decode(record+24);
        if (poffset!=expected_posting*8 || pcount>b.documents || pcount>b.postings-expected_posting) throw "Invalid raw posting boundaries";
        const U64 post_start=postings.h.bytes;
        U64 previous=0;
        for (U64 i=0;i<pcount;++i) {
            unsigned char item[8]; read(raw_postings.f,item,8); const U64 id=decode(item);
            if (id<=previous) throw "Raw posting IDs not increasing";
            postings.var(id-previous); previous=id;
        }
        posting_terms.entry(post_start,postings.h.bytes-post_start);
        (space ? lemma_posting_bytes : surface_posting_bytes)+=postings.h.bytes-post_start;
        expected_posting+=pcount;
        unsigned char pos_record[16]; read(raw_pos_terms.f,pos_record,16); const auto coffset=decode(pos_record),ccount=decode(pos_record+8);
        if (coffset!=expected_coordinate || ccount>b.positions-expected_coordinate) throw "Invalid raw coordinate boundaries";
        const auto coord_start=coordinates.h.bytes;
        U64 previous_doc=0,previous_key=0; unsigned previous_field=0; U64 previous_position=0;
        bool first=true;
        for (U64 i=0;i<ccount;++i) {
            unsigned char item[8]; read(raw_positions.f,item,8);
            const U64 doc=decode(item,4); const U64 raw=decode(item+4,4);
            const unsigned field=static_cast<unsigned>(raw>>31); const U64 position=raw&0x7fffffffU;
            const U64 key=(doc<<32)|raw;
            if (doc>=b.documents || (!first && key<=previous_key)) throw "Raw coordinates not increasing";
            const U64 doc_delta=doc-previous_doc;
            const U64 position_delta=first || doc_delta || field!=previous_field ? position : position-previous_position;
            coordinates.var(doc_delta); coordinates.var((position_delta<<1)|field);
            first=false; previous_doc=doc; previous_key=key; previous_field=field; previous_position=position;
        }
        position_terms.entry(coord_start,coordinates.h.bytes-coord_start);
        (space ? lemma_coordinate_bytes : surface_coordinate_bytes)+=coordinates.h.bytes-coord_start;
        expected_coordinate+=ccount;
        if (term && term%100000==0) { work.check(); std::fprintf(log,"compressed terms: %llu\n",static_cast<unsigned long long>(term)); std::fflush(log); }
    }
    if (expected_posting!=b.postings || expected_coordinate!=b.positions) throw "Unreferenced raw postings or coordinates";
    postings.finish(b); posting_terms.finish(b); coordinates.finish(b); position_terms.finish(b);
    work.complete();
    const auto ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
    std::fprintf(log,"{\"format\":\"MAIIR-COMPACT-v2\",\"terms\":%llu,\"documents\":%llu,\"postings\":%llu,\"coordinates\":%llu,\"raw_posting_bytes\":%llu,\"raw_coordinate_bytes\":%llu,\"compact_posting_bytes\":%llu,\"compact_coordinate_bytes\":%llu,\"surface_posting_bytes\":%llu,\"lemma_posting_bytes\":%llu,\"surface_coordinate_bytes\":%llu,\"lemma_coordinate_bytes\":%llu,\"elapsed_ms\":%.3f}\n",
        static_cast<unsigned long long>(b.terms),static_cast<unsigned long long>(b.documents),
        static_cast<unsigned long long>(b.postings),static_cast<unsigned long long>(b.positions),
        static_cast<unsigned long long>(b.postings*8),static_cast<unsigned long long>(b.positions*8),
        static_cast<unsigned long long>(postings.h.bytes),static_cast<unsigned long long>(coordinates.h.bytes),
        static_cast<unsigned long long>(surface_posting_bytes),static_cast<unsigned long long>(lemma_posting_bytes),
        static_cast<unsigned long long>(surface_coordinate_bytes),static_cast<unsigned long long>(lemma_coordinate_bytes),ms);
}
void compare_raw(const char* base,const char* positions,const char* compact,const Binding& b) {
    char p[4096];
    path(p,base,"terms.bin"); File raw_terms(p); seek(raw_terms.f,80);
    path(p,base,"postings.bin"); File raw_postings(p); seek(raw_postings.f,80);
    path(p,positions,"position_terms.bin"); File raw_pos_terms(p); seek(raw_pos_terms.f,80);
    path(p,positions,"positions.bin"); File raw_positions(p); seek(raw_positions.f,80);
    path(p,compact,files[0]); File compact_post_terms(p); seek(compact_post_terms.f,80);
    path(p,compact,files[1]); File compact_postings(p); seek(compact_postings.f,80);
    path(p,compact,files[2]); File compact_pos_terms(p); seek(compact_pos_terms.f,80);
    path(p,compact,files[3]); File compact_positions(p); seek(compact_positions.f,80);
    U64 posting_total=0,coordinate_total=0;
    for (U64 term=0;term<b.terms;++term) {
        unsigned char r[40],c[16],item[8]; read(raw_terms.f,r,40); read(compact_post_terms.f,c,16);
        const U64 count=decode(r+24); U64 remaining=decode(c+8),previous=0;
        if (decode(r+16)!=posting_total*8 || count>b.postings-posting_total) throw "Raw posting list boundaries changed";
        for (U64 i=0;i<count;++i) {
            U64 delta=0; read(raw_postings.f,item,8);
            if (!gaming::read_compact_uint(compact_postings.f,remaining,delta) || !delta || delta>UINT64_MAX-previous)
                throw "Invalid compressed posting encoding";
            previous+=delta;
            if (previous!=decode(item)) throw "Compressed posting differs from original";
        }
        if (remaining) throw "Compressed posting count differs from original";
        posting_total+=count;
        read(raw_pos_terms.f,c,16); const U64 offset=decode(c),occurrences=decode(c+8);
        if (offset!=coordinate_total || occurrences>b.positions-coordinate_total) throw "Raw coordinate list boundaries changed";
        read(compact_pos_terms.f,c,16); remaining=decode(c+8);
        U64 previous_doc=0,previous_position=0; unsigned previous_field=0;
        bool first=true;
        for (U64 i=0;i<occurrences;++i) {
            U64 doc_delta=0,code=0; read(raw_positions.f,item,8);
            if (!gaming::read_compact_uint(compact_positions.f,remaining,doc_delta) ||
                !gaming::read_compact_uint(compact_positions.f,remaining,code) || doc_delta>UINT32_MAX-previous_doc)
                throw "Invalid compressed coordinate encoding";
            const U64 doc=previous_doc+doc_delta; const unsigned field=static_cast<unsigned>(code&1);
            const U64 base=first || doc_delta || field!=previous_field ? 0 : previous_position;
            const U64 delta=code>>1;
            if (delta>0x7fffffffU-base) throw "Compressed position overflow";
            const U64 position=base+delta,packed=(U64(field)<<31)|position;
            if (doc!=decode(item,4) || packed!=decode(item+4,4)) throw "Compressed coordinate differs from original";
            previous_doc=doc; previous_field=field; previous_position=position; first=false;
        }
        if (remaining) throw "Compressed coordinate count differs from original";
        coordinate_total+=occurrences;
    }
    if (posting_total!=b.postings || coordinate_total!=b.positions) throw "Compressed stream totals differ from original";
}
void directory(const char* name) {
#ifdef _WIN32
    if (_mkdir(name)) throw "Cannot create packed index directory";
#else
    if (mkdir(name,0755)) throw "Cannot create packed index directory";
#endif
}
void copy_file(const char* source,const char* destination,bool header_only,const Work& work) {
    File input(source),output(destination,"new");
    unsigned char bytes[65536]; U64 copied=0; std::size_t n;
    if (header_only) { read(input.f,bytes,80); write(output.f,bytes,80); }
    else {
        while ((n=std::fread(bytes,1,sizeof(bytes),input.f))) {
            write(output.f,bytes,n); copied+=n;
            if (copied%(64ULL*1024*1024)<n) work.check();
        }
        if (std::ferror(input.f)) throw "Cannot read packed source";
    }
    output.close();
}
void patch_u64(const char* filename,U64 offset,U64 value) {
    File file(filename,"r+b"); unsigned char p[8]; encode(p,value); seek(file.f,offset); write(file.f,p,8); file.close();
}
U64 document_string_end(const char* base,U64 documents) {
    char name[4096]; path(name,base,"documents.bin"); File file(name); seek(file.f,80);
    U64 end=0;
    for (U64 i=0;i<documents;++i) {
        unsigned char row[64]; read(file.f,row,64);
        const unsigned fields[]={8,24,40};
        for (unsigned j:fields) {
            const U64 offset=decode(row+j),size=decode(row+j+8);
            if (size>UINT64_MAX-offset) throw "Document string offset overflow";
            if (offset+size>end) end=offset+size;
        }
    }
    return end;
}
void copy_document_strings(const char* source,const char* destination,U64 bytes,const Work& work) {
    File input(source),output(destination,"new"); unsigned char header_bytes[80],buffer[65536];
    read(input.f,header_bytes,80);
    if (bytes>decode(header_bytes+16)) throw "Document strings exceed original string file";
    write(output.f,header_bytes,80); U64 remaining=bytes,checksum=basis;
    while (remaining) {
        const std::size_t n=remaining>sizeof(buffer) ? sizeof(buffer) : static_cast<std::size_t>(remaining);
        read(input.f,buffer,n); write(output.f,buffer,n); checksum=hash(checksum,buffer,n); remaining-=n;
        if (remaining%(64ULL*1024*1024)<n) work.check();
    }
    encode(header_bytes+8,bytes); encode(header_bytes+16,bytes); encode(header_bytes+24,checksum);
    seek(output.f,0); write(output.f,header_bytes,80); output.close();
}
U64 boolean_fingerprint(const char* directory) {
    U64 fingerprint=basis; unsigned char p[80]; char name[4096];
    for (unsigned i=0;i<4;++i) { path(name,directory,base_names[i]); File file(name); read(file.f,p,80); fingerprint=hash(fingerprint,p,80); }
    return fingerprint;
}
struct Chars {
    char* p=nullptr; std::size_t size=0,capacity=0;
    ~Chars() { std::free(p); }
    void resize(std::size_t n) {
        if (n>capacity) { void* q=std::realloc(p,n); if (!q) throw "Vocabulary allocation failed"; p=static_cast<char*>(q); capacity=n; }
        size=n;
    }
};
void build_vocabulary(const char* base,const char* destination,const Binding& b,const Work& work) {
    char p[4096]; path(p,base,"terms.bin"); File terms(p); seek(terms.f,80);
    path(p,base,"strings.bin"); File strings(p);
    Writer blocks(destination,4),words(destination,5);
    Chars current,previous; U64 previous_offset=0,previous_count=0; unsigned previous_space=0;
    for (U64 ordinal=0;ordinal<b.terms;++ordinal) {
        unsigned char row[40]; read(terms.f,row,40);
        const unsigned space=static_cast<unsigned>(decode(row,4));
        const U64 size=decode(row+4,4),string_offset=decode(row+8),posting_offset=decode(row+16),count=decode(row+24);
        if (space>1 || !size || size>1024*1024 || !count || count>b.documents || posting_offset%8) throw "Invalid source vocabulary record";
        current.resize(static_cast<std::size_t>(size)); seek(strings.f,80+string_offset); read(strings.f,current.p,current.size);
        if (ordinal%16==0) { blocks.num(words.h.bytes); words.var(posting_offset); previous.size=0; }
        else if (posting_offset!=previous_offset+previous_count*8) throw "Vocabulary posting offsets are not contiguous";
        std::size_t prefix=0;
        if (ordinal%16 && space==previous_space)
            while (prefix<current.size && prefix<previous.size && current.p[prefix]==previous.p[prefix]) ++prefix;
        words.var(space); words.var(prefix); words.var(current.size-prefix);
        words.put(current.p+prefix,current.size-prefix); words.var(count);
        previous.resize(current.size); std::memcpy(previous.p,current.p,current.size);
        previous_offset=posting_offset; previous_count=count; previous_space=space;
        if (ordinal && ordinal%100000==0) work.check();
    }
    blocks.finish(b); words.finish(b);
}
void compare_vocabulary(const char* base,const char* compact,U64 terms_count) {
    char p[4096]; path(p,base,"terms.bin"); File raw_terms(p); seek(raw_terms.f,80);
    path(p,base,"strings.bin"); File raw_strings(p);
    path(p,compact,files[4]); File block_file(p); const Header blocks=header(block_file.f,4); seek(block_file.f,80);
    path(p,compact,files[5]); File data_file(p); const Header data=header(data_file.f,5); seek(data_file.f,80);
    if (blocks.count!=(terms_count+15)/16) throw "Compressed vocabulary block count differs";
    U64 remaining=data.bytes,previous_end=0;
    Chars word,reference;
    for (U64 ordinal=0;ordinal<terms_count;++ordinal) {
        if (ordinal%16==0) {
            unsigned char pointer[8]; read(block_file.f,pointer,8);
            if (decode(pointer)!=data.bytes-remaining) throw "Compressed vocabulary block offset differs";
            word.size=0;
            if (!gaming::read_compact_uint(data_file.f,remaining,previous_end)) throw "Invalid vocabulary block posting offset";
        }
        U64 space=0,prefix=0,suffix=0,count=0;
        if (!gaming::read_compact_uint(data_file.f,remaining,space) || space>1 ||
            !gaming::read_compact_uint(data_file.f,remaining,prefix) || prefix>word.size ||
            !gaming::read_compact_uint(data_file.f,remaining,suffix) || suffix>1024*1024-prefix || suffix>remaining)
            throw "Invalid compressed vocabulary term";
        word.resize(static_cast<std::size_t>(prefix+suffix));
        read(data_file.f,word.p+prefix,static_cast<std::size_t>(suffix)); remaining-=suffix;
        if (!gaming::read_compact_uint(data_file.f,remaining,count) || !count) throw "Invalid compressed vocabulary count";
        unsigned char row[40]; read(raw_terms.f,row,40);
        const U64 raw_size=decode(row+4,4),raw_string=decode(row+8);
        if (space!=decode(row,4) || raw_size!=word.size || previous_end!=decode(row+16) || count!=decode(row+24))
            throw "Compressed vocabulary metadata differs from original";
        reference.resize(static_cast<std::size_t>(raw_size));
        seek(raw_strings.f,80+raw_string); read(raw_strings.f,reference.p,reference.size);
        if (std::memcmp(word.p,reference.p,word.size)) throw "Compressed term string differs from original";
        if (count>(UINT64_MAX-previous_end)/8) throw "Vocabulary posting offset overflow";
        previous_end+=count*8;
    }
    if (remaining) throw "Trailing compressed vocabulary bytes";
}
void compare_file_payload(const char* original,const char* packed,U64 bytes) {
    File a(original),b(packed); seek(a.f,80); seek(b.f,80); unsigned char x[65536],y[65536];
    U64 remaining=bytes;
    while (remaining) {
        const std::size_t n=remaining>sizeof(x) ? sizeof(x) : static_cast<std::size_t>(remaining);
        read(a.f,x,n); read(b.f,y,n); if (std::memcmp(x,y,n)) throw "Packed metadata differs from original";
        remaining-=n;
    }
    if (length(b.f)!=80+bytes) throw "Packed metadata length mismatch";
}
void verify_packed(const char* base,const char* positions,const char* packed,FILE* output) {
    gaming::BooleanIndex original; const char* error=nullptr; require(original.open(base,&error),error);
    gaming::PositionalIndex raw_positions; require(raw_positions.open(positions,base,original,&error),error);
    require(original.verify(&error),error); require(raw_positions.verify(&error),error);
    char packed_base[4096],packed_positions[4096],packed_compact[4096];
    path(packed_base,packed,"index"); path(packed_positions,packed,"positions"); path(packed_compact,packed,"compact");
    gaming::BooleanIndex index; require(index.open(packed_base,&error,true),error);
    gaming::PositionalIndex pos; require(pos.open(packed_positions,packed_base,index,&error,true),error);
    gaming::CompactIndex compact; require(compact.open(packed_compact,packed_base,packed_positions,index,pos,&error),error);
    if (!compact.has_vocabulary()) throw "Packed index lacks compressed vocabulary";
    require(compact.verify(&error),error);
    compare_raw(base,positions,packed_compact,binding(base,positions));
    compare_vocabulary(base,packed_compact,original.terms());
    char a[4096],b[4096];
    path(a,base,"documents.bin"); path(b,packed_base,"documents.bin"); compare_file_payload(a,b,original.documents()*64);
    path(a,base,"strings.bin"); path(b,packed_base,"strings.bin"); File packed_strings(b);
    unsigned char h[80]; read(packed_strings.f,h,80); compare_file_payload(a,b,decode(h+16));
    path(a,positions,"position_terms.bin"); path(b,packed_positions,"position_terms.bin");
    compare_file_payload(a,b,original.terms()*16);
    std::fputs("Packed index verified: postings, coordinates, vocabulary and metadata match original\n",output);
}
void pack(const char* base,const char* positions,const char* compact,const char* output,U64 reserve,FILE* log) {
    gaming::BooleanIndex index; const char* error=nullptr; require(index.open(base,&error),error);
    gaming::PositionalIndex positional; require(positional.open(positions,base,index,&error),error);
    gaming::CompactIndex compressed; require(compressed.open(compact,base,positions,index,positional,&error),error);
    require(index.verify(&error),error); require(positional.verify(&error),error);
    require(compressed.verify(&error),error);
    compare_raw(base,positions,compact,binding(base,positions));
    Work work(output,reserve); char target[4096],source[4096],dest[4096];
    const char* dirs[]={"index","positions","compact"};
    for (const char* d:dirs) { path(target,output,d); directory(target); }
    const U64 document_end=document_string_end(base,index.documents());
    for (unsigned i=0;i<4;++i) {
        path(source,base,base_names[i]); path(target,output,"index"); path(dest,target,base_names[i]);
        if (i==3) copy_document_strings(source,dest,document_end,work);
        else copy_file(source,dest,i==0 || i==1,work);
    }
    path(target,output,"index"); const U64 new_boolean_binding=boolean_fingerprint(target);
    for (unsigned i=4;i<6;++i) {
        path(source,positions,base_names[i]); path(target,output,"positions"); path(dest,target,base_names[i]);
        copy_file(source,dest,i==5,work);
        patch_u64(dest,32,new_boolean_binding);
    }
    char packed_base[4096],packed_positions[4096]; path(packed_base,output,"index"); path(packed_positions,output,"positions");
    const Binding new_binding=binding(packed_base,packed_positions);
    for (unsigned i=0;i<4;++i) {
        const char* name=files[i];
        path(source,compact,name); path(target,output,"compact"); path(dest,target,name);
        copy_file(source,dest,false,work);
        patch_u64(dest,32,new_binding.value);
    }
    path(target,output,"compact"); build_vocabulary(base,target,new_binding,work);
    compare_vocabulary(base,target,index.terms());
    work.complete();
    std::fprintf(log,"Packed compressed index: %s (index, positions, compact)\n",output);
}
}

namespace {
template<class Next> bool decode_compact_uint(Next next,U64& result) {
    result=0; unsigned shift=0;
    for (unsigned i=0;i<10;++i) {
        const int c=next(); if (c==EOF) return false;
        const auto byte=static_cast<unsigned>(c);
        if (i==9 && (byte&127)>1) return false;
        result|=U64(byte&127)<<shift;
        if (byte&128) return i==0 || (byte&127)!=0; // reject non-minimal encodings
        shift+=7;
    }
    return false;
}
}
bool gaming::read_compact_uint(FILE* file,U64& remaining,U64& result) {
    return decode_compact_uint([&]() {
        if (!remaining) return EOF;
        const int c=std::fgetc(file); if (c!=EOF) --remaining;
        return c;
    },result);
}
bool gaming::read_compact_uint(const unsigned char*& data,U64& remaining,U64& result) {
    return decode_compact_uint([&]() {
        if (!remaining) return EOF;
        --remaining; return static_cast<int>(*data++);
    },result);
}
gaming::CompactIndex::~CompactIndex() {
    for (FILE* f:terms_) if (f) std::fclose(f);
    for (FILE* f:vocabulary_) if (f) std::fclose(f);
}
bool gaming::CompactIndex::open(const char* d,const char* base,const char* positions,const BooleanIndex& boolean,const PositionalIndex& positional,const char** error) {
    *error=nullptr;
    for (FILE*& f:terms_) { if (f) std::fclose(f); f=nullptr; }
    for (FILE*& f:vocabulary_) { if (f) std::fclose(f); f=nullptr; }
    try {
        if (!positional.available()) throw "Compressed search requires positional index";
        char p[4096]; path(p,d,"INCOMPLETE"); if (FILE* mark=std::fopen(p,"rb")) { std::fclose(mark); throw "Compact index is incomplete"; }
        const Binding b=binding(base,positions); Header h[4];
        for (unsigned i=0;i<4;++i) {
            path(p,d,files[i]); File file(p); h[i]=header(file.f,i);
            if (h[i].binding!=b.value || h[i].terms!=b.terms || h[i].documents!=b.documents || h[i].postings!=b.postings || h[i].coordinates!=b.positions)
                throw "Compressed files belong to a different index";
        }
        if (h[0].count!=b.terms || h[2].count!=b.terms) throw "Compressed vocabulary length mismatch";
        for (unsigned kind=0;kind<2;++kind) {
            path(p,d,files[kind*2]); terms_[kind]=std::fopen(p,"rb");
            if (!terms_[kind]) throw "Cannot open compressed vocabulary";
            data_bytes_[kind]=h[kind*2+1].bytes;
        }
        path(p,d,files[4]);
        if (FILE* candidate=std::fopen(p,"rb")) {
            std::fclose(candidate);
            // pack publishes the three subdirectories together by removing the
            // marker in their shared parent, after vocabulary verification.
            path(p,d,"../INCOMPLETE");
            if (FILE* mark=std::fopen(p,"rb")) { std::fclose(mark); throw "Packed index is incomplete"; }
            Header v[2];
            for (unsigned kind=4;kind<6;++kind) {
                path(p,d,files[kind]); File file(p); v[kind-4]=header(file.f,kind);
                if (v[kind-4].binding!=b.value || v[kind-4].terms!=b.terms || v[kind-4].documents!=b.documents ||
                    v[kind-4].postings!=b.postings || v[kind-4].coordinates!=b.positions)
                    throw "Compressed vocabulary belongs to a different index";
            }
            if (v[0].count!=(b.terms+15)/16) throw "Compressed vocabulary block count mismatch";
            for (unsigned i=0;i<2;++i) { path(p,d,files[i+4]); vocabulary_[i]=std::fopen(p,"rb"); if (!vocabulary_[i]) throw "Cannot open compressed vocabulary"; }
            vocabulary_bytes_=v[1].bytes;
        } else {
            path(p,base,"terms.bin"); File original(p);
            if (b.terms && length(original.f)==80) throw "Compressed term strings are missing";
        }
        std::strcpy(directory_,d); count_=b.terms; binding_=b.value; postings_count_=b.postings; return true;
    } catch (const char* e) {
        *error=e;
        for (FILE*& f:terms_) { if (f) std::fclose(f); f=nullptr; }
        for (FILE*& f:vocabulary_) { if (f) std::fclose(f); f=nullptr; }
        return false;
    }
}
FILE* gaming::CompactIndex::stream(CompactKind kind,U64 ordinal,U64& bytes,const char** error) const {
    *error=nullptr; bytes=0;
    try {
        const unsigned k=static_cast<unsigned>(kind);
        if (k>1 || !terms_[k] || ordinal>=count_) throw "Invalid compressed term handle";
        seek(terms_[k],80+ordinal*16); unsigned char p[16]; read(terms_[k],p,16);
        const U64 offset=decode(p),size=decode(p+8);
        if (offset>data_bytes_[k] || size>data_bytes_[k]-offset) throw "Compressed list exceeds file";
        char filename[4096]; path(filename,directory_,files[k*2+1]); File file(filename);
        const auto h=header(file.f,k*2+1);
        if (h.binding!=binding_ || h.bytes!=data_bytes_[k]) throw "Compressed payload changed";
        seek(file.f,80+offset); bytes=size; FILE* result=file.f; file.f=nullptr; return result;
    } catch (const char* e) { *error=e; return nullptr; }
}
bool gaming::CompactIndex::lookup(const char* term,std::size_t size,TermSpace space,PostingList& result,const char** error) const {
    *error=nullptr; result={};
    try {
        if (!has_vocabulary()) throw "Compressed vocabulary is unavailable";
        const U64 blocks=(count_+15)/16;
        auto bounds=[&](U64 block,U64& start,U64& remaining) {
            unsigned char p[8]; seek(vocabulary_[0],80+block*8); read(vocabulary_[0],p,8); start=decode(p);
            U64 end=vocabulary_bytes_;
            if (block+1<blocks) { read(vocabulary_[0],p,8); end=decode(p); }
            if (start>=end || end>vocabulary_bytes_) throw "Invalid vocabulary block boundaries";
            remaining=end-start;
        };
        auto compare=[&](unsigned candidate_space,const Chars& candidate) {
            const unsigned target_space=static_cast<unsigned>(space);
            if (target_space!=candidate_space) return target_space<candidate_space ? -1 : 1;
            const auto n=size<candidate.size ? size : candidate.size;
            const int c=std::memcmp(term,candidate.p,n);
            return c ? c : size==candidate.size ? 0 : size<candidate.size ? -1 : 1;
        };
        auto next=[&](U64& remaining,Chars& word,unsigned& candidate_space,U64& count) {
            U64 value=0,prefix=0,suffix=0;
            if (!read_compact_uint(vocabulary_[1],remaining,value) || value>1) throw "Invalid vocabulary namespace";
            candidate_space=static_cast<unsigned>(value);
            if (!read_compact_uint(vocabulary_[1],remaining,prefix) || prefix>word.size ||
                !read_compact_uint(vocabulary_[1],remaining,suffix) || suffix>1024*1024-prefix || suffix>remaining)
                throw "Invalid vocabulary prefix or suffix";
            word.resize(static_cast<std::size_t>(prefix+suffix));
            read(vocabulary_[1],word.p+prefix,static_cast<std::size_t>(suffix)); remaining-=suffix;
            if (!word.size || !read_compact_uint(vocabulary_[1],remaining,count) || !count || count>postings_count_)
                throw "Invalid vocabulary posting count";
        };
        U64 left=0,right=blocks;
        while (left<right) {
            const U64 mid=left+(right-left)/2; U64 start=0,remaining=0,offset=0;
            bounds(mid,start,remaining); seek(vocabulary_[1],80+start);
            if (!read_compact_uint(vocabulary_[1],remaining,offset)) throw "Invalid vocabulary posting offset";
            Chars word; unsigned candidate_space=0; U64 count=0;
            next(remaining,word,candidate_space,count);
            if (compare(candidate_space,word)>=0) left=mid+1; else right=mid;
        }
        if (!left) return true;
        const U64 block=left-1; U64 start=0,remaining=0,offset=0;
        bounds(block,start,remaining); seek(vocabulary_[1],80+start);
        if (!read_compact_uint(vocabulary_[1],remaining,offset)) throw "Invalid vocabulary posting offset";
        Chars word; const U64 limit=count_-block*16;
        for (U64 i=0;i<(limit<16 ? limit : 16);++i) {
            unsigned candidate_space=0; U64 count=0; next(remaining,word,candidate_space,count);
            if (offset>postings_count_*8 || count>(postings_count_*8-offset)/8) throw "Vocabulary posting offset exceeds file";
            const int c=compare(candidate_space,word);
            if (!c) { result={offset,count,block*16+i}; return true; }
            if (c<0) return true;
            offset+=count*8;
        }
        if (remaining) throw "Trailing vocabulary block bytes";
        return true;
    } catch (const char* e) { *error=e; return false; }
}
bool gaming::CompactIndex::verify(const char** error) {
    *error=nullptr;
    try {
        if (!terms_[0] || !terms_[1]) throw "Compact index is not open";
        char p[4096]; unsigned char block[65536];
        for (unsigned kind=0;kind<(has_vocabulary() ? 6U : 4U);++kind) {
            path(p,directory_,files[kind]); File f(p); const auto h=header(f.f,kind); seek(f.f,80); U64 checksum=basis; std::size_t n;
            while ((n=std::fread(block,1,sizeof(block),f.f))) checksum=hash(checksum,block,n);
            if (std::ferror(f.f) || checksum!=h.checksum) throw "Compact checksum mismatch";
        }
        // Validate all list boundaries. Full decompression equality is checked by the
        // C++ verification command against the original sorted streams.
        for (unsigned k=0;k<2;++k) {
            seek(terms_[k],80); U64 offset=0;
            for (U64 i=0;i<count_;++i) {
                unsigned char item[16]; read(terms_[k],item,16);
                if (offset>data_bytes_[k] || decode(item)!=offset || decode(item+8)>data_bytes_[k]-offset) throw "Invalid compact list boundaries";
                offset+=decode(item+8);
            }
            if (offset!=data_bytes_[k]) throw "Unreferenced compact bytes";
        }
        if (has_vocabulary()) {
            seek(vocabulary_[0],80); U64 previous=0;
            for (U64 block=0;block<(count_+15)/16;++block) {
                unsigned char item[8]; read(vocabulary_[0],item,8); const U64 offset=decode(item);
                if ((block==0 && offset!=0) || (block && offset<=previous) || offset>=vocabulary_bytes_)
                    throw "Invalid compressed vocabulary block boundaries";
                previous=offset;
            }
            if (!count_ && vocabulary_bytes_) throw "Unexpected empty vocabulary payload";
        }
        return true;
    } catch (const char* e) { *error=e; return false; }
}
int gaming::run_compact_index(int argc,const char* const* argv,FILE* output,FILE* errors) {
    try {
        if (argc<2) throw "Expected build, verify, verify-packed, pack or benchmark";
        if (!std::strcmp(argv[1],"--help")) {
            std::fputs("UTF-8 compact index: compact_index build --index BOOLEAN_DIR --positions POSITION_DIR --output NEW_DIR [--reserve-mib 4608]\n"
                       "compact_index verify --index BOOLEAN_DIR --positions POSITION_DIR --compact DIR\n"
                       "compact_index verify-packed --index ORIGINAL_BOOLEAN_DIR --positions ORIGINAL_POSITION_DIR --packed PACKED_ROOT\n"
                       "compact_index pack --index BOOLEAN_DIR --positions POSITION_DIR --compact DIR --output NEW_DIR [--reserve-mib 4608]\n"
                       "compact_index benchmark --index BOOLEAN_DIR --positions POSITION_DIR --compact DIR --term TOKEN [--space surface|lemma] [--repeats 7]\n",output); return 0;
        }
        const char *base=nullptr,*positions=nullptr,*output_dir=nullptr,*compact=nullptr,*packed=nullptr,*term=nullptr;
        U64 reserve=4608; unsigned repeats=7; TermSpace space=TermSpace::surface;
        for (int i=2;i<argc;++i) {
            const char* key=argv[i]; if (++i==argc) throw "Missing compact option value";
            const char* value=argv[i];
            if (!std::strcmp(key,"--index")) base=value; else if (!std::strcmp(key,"--positions")) positions=value;
            else if (!std::strcmp(key,"--output")) output_dir=value; else if (!std::strcmp(key,"--compact")) compact=value;
            else if (!std::strcmp(key,"--packed")) packed=value;
            else if (!std::strcmp(key,"--term")) term=value;
            else if (!std::strcmp(key,"--space")) {
                if (!std::strcmp(value,"surface")) space=TermSpace::surface;
                else if (!std::strcmp(value,"lemma")) space=TermSpace::lemma;
                else throw "Invalid benchmark namespace";
            }
            else if (!std::strcmp(key,"--repeats")) {
                repeats=0;
                if (!*value) throw "Invalid benchmark repetitions";
                for (const char* p=value;*p;++p) {
                    if (*p<'0' || *p>'9' || repeats>31) throw "Invalid benchmark repetitions";
                    repeats=repeats*10+unsigned(*p-'0');
                }
                if (!repeats || repeats>31) throw "Benchmark repetitions must be 1..31";
            }
            else if (!std::strcmp(key,"--reserve-mib")) { reserve=0; if (!*value) throw "Invalid disk reserve"; for (const char* p=value;*p;++p) { if (*p<'0' || *p>'9' || reserve>(UINT64_MAX-unsigned(*p-'0'))/10) throw "Invalid disk reserve"; reserve=reserve*10+unsigned(*p-'0'); } }
            else throw "Unknown compact option";
        }
        if (!base || !positions || reserve>UINT64_MAX/(1024*1024)) throw "Invalid compact options";
        if (!std::strcmp(argv[1],"build")) { if (!output_dir) throw "--output is required"; build(base,positions,output_dir,reserve*1024*1024,output); }
        else if (!std::strcmp(argv[1],"verify")) {
            if (!compact) throw "--compact is required";
            BooleanIndex index; const char* error=nullptr; require(index.open(base,&error),error);
            PositionalIndex pos; require(pos.open(positions,base,index,&error),error);
            CompactIndex result; require(result.open(compact,base,positions,index,pos,&error),error);
            require(result.verify(&error),error);
            require(index.verify(&error),error); require(pos.verify(&error),error);
            compare_raw(base,positions,compact,binding(base,positions));
            std::fputs("Compact index verified: checksums, binding, every posting and coordinate against original\n",output);
        } else if (!std::strcmp(argv[1],"pack")) {
            if (!compact || !output_dir) throw "--compact and --output are required";
            pack(base,positions,compact,output_dir,reserve*1024*1024,output);
        } else if (!std::strcmp(argv[1],"verify-packed")) {
            if (!packed) throw "--packed is required";
            verify_packed(base,positions,packed,output);
        } else if (!std::strcmp(argv[1],"benchmark")) {
            if (!compact || !term) throw "--compact and --term are required";
            benchmark_compact_lists(base,positions,compact,term,space,repeats,output);
        } else throw "Unknown compact mode";
        return 0;
    } catch (const char* e) { std::fprintf(errors,"[compact_index] %s\n",e); return 1; }
      catch (const std::exception&) { std::fputs("[compact_index] Allocation failure\n",errors); return 1; }
}
