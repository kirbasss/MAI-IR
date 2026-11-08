#include "skip_index.hpp"
#include "compact_index.hpp"
#include "positional_index.hpp"
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <exception>
#include <sys/stat.h>
#ifdef _WIN32
#include <windows.h>
#include <direct.h>
#else
#include <sys/statvfs.h>
#endif

namespace {
using U64=std::uint64_t;
constexpr U64 basis=14695981039346656037ULL;
const char* names[]={"skip_posting_terms.bin","skip_postings.bin","skip_position_terms.bin","skip_positions.bin"};
const char* compact_names[]={"compact_posting_terms.bin","compact_postings.bin","compact_position_terms.bin","compact_positions.bin",
                            "compact_vocabulary_blocks.bin","compact_vocabulary.bin"};
void require(bool ok,const char* const& e) { if (!ok) throw (e ? e : "Skip operation failed"); }
void path(char* p,const char* dir,const char* name) {
    if (std::strlen(dir)+std::strlen(name)+2>4096) throw "Skip path too long";
    std::strcpy(p,dir); std::strcat(p,"/"); std::strcat(p,name);
}
U64 hash(U64 h,const void* p,std::size_t n) { const auto* b=static_cast<const unsigned char*>(p); for (std::size_t i=0;i<n;++i) { h^=b[i]; h*=1099511628211ULL; } return h; }
U64 decode(const unsigned char* p) { U64 v=0; for (unsigned i=0;i<8;++i) v|=U64(p[i])<<(8*i); return v; }
void encode(unsigned char* p,U64 v) { for (unsigned i=0;i<8;++i) p[i]=static_cast<unsigned char>(v>>(8*i)); }
void read(FILE* f,void* p,std::size_t n) { if (n && std::fread(p,1,n,f)!=n) throw "Truncated skip input"; }
void write(FILE* f,const void* p,std::size_t n) { if (n && std::fwrite(p,1,n,f)!=n) throw "Cannot write skip index"; }
void seek(FILE* f,U64 offset) {
    if (offset>INT64_MAX) throw "Skip offset overflow";
#ifdef _WIN32
    if (_fseeki64(f,static_cast<__int64>(offset),SEEK_SET)) throw "Cannot seek skip stream";
#else
    if (fseeko(f,static_cast<off_t>(offset),SEEK_SET)) throw "Cannot seek skip stream";
#endif
}
U64 tell(FILE* f) {
#ifdef _WIN32
    const auto n=_ftelli64(f);
#else
    const auto n=ftello(f);
#endif
    if (n<0) throw "Cannot tell skip stream"; return static_cast<U64>(n);
}
struct File {
    FILE* f=nullptr;
    explicit File(const char* p,const char* mode="rb"):f(std::fopen(p,mode)) { if (!f) throw "Cannot open skip file"; }
    ~File() { if (f) std::fclose(f); }
    void close() { FILE* old=f; f=nullptr; if (old && std::fclose(old)) throw "Cannot close skip output"; }
};
U64 binding(const char* compact) {
    U64 h=basis; unsigned char bytes[80]; char p[4096];
    for (const char* name:compact_names) {
        path(p,compact,name); FILE* f=std::fopen(p,"rb");
        if (!f) { if (name==compact_names[4] || name==compact_names[5]) continue; throw "Missing compact input"; }
        const auto n=std::fread(bytes,1,80,f); std::fclose(f);
        if (n!=80) throw "Truncated compact header";
        h=hash(h,bytes,80);
    }
    return h;
}
struct Header { U64 count=0,bytes=0,checksum=basis,binding=0,terms=0,width=0; };
Header header(FILE* f,unsigned kind) {
    unsigned char p[80]; seek(f,0); read(f,p,80);
    if (std::memcmp(p,"MAISK12\1",8) || decode(p+8)!=kind || decode(p+64)!=256 || decode(p+72)!=1) throw "Invalid skip header/version";
    Header h{decode(p+16),decode(p+24),decode(p+32),decode(p+40),decode(p+48),decode(p+56)};
    if (h.width!=(kind%2 ? 24U : 16U) || h.count>UINT64_MAX/h.width || h.bytes!=h.count*h.width || h.bytes>INT64_MAX-80) throw "Invalid skip size";
    seek(f,80+h.bytes); if (std::fgetc(f)!=EOF) throw "Trailing skip data";
    seek(f,0);
#ifdef _WIN32
    if (_fseeki64(f,0,SEEK_END)) throw "Cannot size skip input";
#else
    if (fseeko(f,0,SEEK_END)) throw "Cannot size skip input";
#endif
    if (tell(f)!=80+h.bytes) throw "Truncated skip data";
    return h;
}
U64 stride(U64 count) {
    if (count<256) return 0;
    U64 step=static_cast<U64>(std::sqrt(static_cast<double>(count)));
    while (step && step>count/step) --step;
    while (step<count/step || step*step<count) ++step;
    return step;
}
void reserve(const char* dir,U64 bytes) {
    U64 free=0;
#ifdef _WIN32
    ULARGE_INTEGER n; if (!GetDiskFreeSpaceExA(dir,&n,nullptr,nullptr)) throw "Cannot check skip disk space"; free=n.QuadPart;
#else
    struct statvfs n{}; if (statvfs(dir,&n)) throw "Cannot check skip disk space"; free=U64(n.f_bavail)*n.f_frsize;
#endif
    if (free<bytes) throw "Skip disk reserve reached";
}
struct Writer {
    File file; Header h; unsigned kind;
    Writer(const char* dir,unsigned k):file(filename(dir,k),"w+b"),kind(k) { unsigned char p[80]{}; write(file.f,p,80); h.width=k%2 ? 24 : 16; }
    static const char* filename(const char* dir,unsigned k) { static char p[4096]; path(p,dir,names[k]); return p; }
    void record(U64 a,U64 b,U64 c=0) {
        unsigned char p[24]; encode(p,a); encode(p+8,b); encode(p+16,c);
        write(file.f,p,static_cast<std::size_t>(h.width)); h.checksum=hash(h.checksum,p,static_cast<std::size_t>(h.width)); ++h.count; h.bytes+=h.width;
    }
    void finish(U64 bind,U64 terms) {
        unsigned char p[80]{}; std::memcpy(p,"MAISK12\1",8);
        const U64 fields[]={kind,h.count,h.bytes,h.checksum,bind,terms,h.width,256,1};
        for (unsigned i=0;i<9;++i) encode(p+8+i*8,fields[i]);
        seek(file.f,0); write(file.f,p,80); file.close();
    }
};
void build(const char* base,const char* positions,const char* compact_dir,const char* output,U64 reserve_bytes,FILE* out) {
    const char* error=nullptr; gaming::BooleanIndex index; require(index.open(base,&error,true),error);
    gaming::PositionalIndex pos; require(pos.open(positions,base,index,&error,true),error);
    gaming::CompactIndex compact; require(compact.open(compact_dir,base,positions,index,pos,&error),error); require(compact.verify(&error),error);
#ifdef _WIN32
    if (_mkdir(output)) throw "Skip output must be a NEW directory";
#else
    if (mkdir(output,0755)) throw "Skip output must be a NEW directory";
#endif
    char p[4096]; path(p,output,"INCOMPLETE"); { File marker(p,"wb"); write(marker.f,"Incomplete\n",11); }
    reserve(output,reserve_bytes); Writer pt(output,0),pd(output,1),ct(output,2),cd(output,3);
    const U64 bind=binding(compact_dir); U64 decoded[2]{};
    for (unsigned kind=0;kind<2;++kind) {
        Writer& table=kind ? ct : pt; Writer& data=kind ? cd : pd;
        path(p,compact_dir,compact_names[kind*2]); File source_table(p); seek(source_table.f,80);
        path(p,compact_dir,compact_names[kind*2+1]); File source_data(p); FILE* f=source_data.f;
        path(p,positions,"position_terms.bin"); File position_counts(p); seek(position_counts.f,80);
        for (U64 ordinal=0;ordinal<index.terms();++ordinal) {
            unsigned char metadata[16]; read(source_table.f,metadata,16);
            const U64 bytes=decode(metadata+8),start=80+decode(metadata); seek(f,start);
            // Counts are present in the retained positional table; posting count
            // is recovered while decoding, avoiding dependence on raw terms.bin.
            U64 count=0;
            if (kind) {
                unsigned char row[16]; read(position_counts.f,row,16); count=decode(row+8);
            } else {
                // A first byte-only pass counts canonical VB codewords.
                U64 left=bytes,value=0; while (left) { require(gaming::read_compact_uint(f,left,value),"Invalid skip source VB"); ++count; }
                seek(f,start);
            }
            const U64 step=stride(count),begin=data.h.count; U64 remaining=bytes,previous=0,doc=0,position=0; unsigned field=0;
            for (U64 i=0;i<count;++i) {
                U64 delta=0,value=0; require(gaming::read_compact_uint(f,remaining,delta),"Invalid skip source VB");
                if (kind) {
                    U64 code=0; require(gaming::read_compact_uint(f,remaining,code),"Invalid skip source coordinate");
                    if (delta>UINT32_MAX-doc) throw "Skip source document overflow";
                    const unsigned next_field=static_cast<unsigned>(code&1);
                    if (!i || delta || next_field!=field) position=0;
                    if ((code>>1)>0x7fffffffU-position) throw "Skip source position overflow";
                    doc+=delta; position+=code>>1; field=next_field; value=(doc<<32)|(U64(field)<<31)|position;
                    if (doc>=index.documents() || (i && value<=previous)) throw "Unsorted skip source coordinates";
                } else { if (!delta || delta>UINT64_MAX-previous) throw "Invalid skip source ID"; value=previous+delta; }
                previous=value;
                if (step && (i+1)%step==0) data.record(value,bytes-remaining,i+1);
            }
            if (remaining) throw "Skip source count mismatch";
            decoded[kind]+=count; table.record(begin,data.h.count-begin);
            if (ordinal%100000==0) {
                reserve(output,reserve_bytes);
                std::fprintf(out,"skip kind %u: %llu / %llu terms\n",kind,static_cast<unsigned long long>(ordinal),static_cast<unsigned long long>(index.terms()));
                std::fflush(out);
            }
        }
    }
    pt.finish(bind,index.terms()); pd.finish(bind,index.terms()); ct.finish(bind,index.terms()); cd.finish(bind,index.terms());
    path(p,output,"INCOMPLETE"); if (std::remove(p)) throw "Cannot publish skip index";
    std::fprintf(out,"{\"terms\":%llu,\"posting_points\":%llu,\"coordinate_points\":%llu,\"decoded_postings\":%llu,\"decoded_coordinates\":%llu}\n",
                 static_cast<unsigned long long>(index.terms()),static_cast<unsigned long long>(pd.h.count),static_cast<unsigned long long>(cd.h.count),
                 static_cast<unsigned long long>(decoded[0]),static_cast<unsigned long long>(decoded[1]));
}
}

gaming::SkipIndex::~SkipIndex() { for (FILE* f:files_) if (f) std::fclose(f); }
bool gaming::SkipIndex::open(const char* dir,const char* compact,const BooleanIndex& index,const char** error) {
    *error=nullptr; for (FILE*& f:files_) { if (f) std::fclose(f); f=nullptr; }
    try {
        char p[4096]; path(p,dir,"INCOMPLETE"); if (FILE* f=std::fopen(p,"rb")) { std::fclose(f); throw "Skip index is incomplete"; }
        const U64 bind=binding(compact); terms_=index.terms();
        for (unsigned k=0;k<4;++k) {
            path(p,dir,names[k]); files_[k]=std::fopen(p,"rb"); if (!files_[k]) throw "Cannot open skip index";
            const auto h=header(files_[k],k);
            if (h.binding!=bind || h.terms!=terms_ || (!(k%2) && h.count!=terms_)) throw "Skip index belongs to a different compact index";
            if (k%2) counts_[k/2]=h.count;
        }
        return true;
    } catch (const char* e) { *error=e; for (FILE*& f:files_) { if (f) std::fclose(f); f=nullptr; } return false; }
}
bool gaming::SkipIndex::load(unsigned kind,U64 ordinal,U64 count,U64 bytes,SkipPoint*& points,U64& size,const char** error) const {
    *error=nullptr; points=nullptr; size=0;
    try {
        if (kind>1 || ordinal>=terms_ || !files_[kind*2]) throw "Invalid skip list handle";
        unsigned char row[24]; seek(files_[kind*2],80+ordinal*16); read(files_[kind*2],row,16);
        const U64 offset=decode(row),n=decode(row+8),step=stride(count);
        if (offset>counts_[kind] || n>counts_[kind]-offset || n!=(step ? count/step : 0) || n>SIZE_MAX/sizeof(SkipPoint)) throw "Invalid skip list bounds";
        if (!n) return true;
        points=static_cast<SkipPoint*>(std::malloc(static_cast<std::size_t>(n)*sizeof(SkipPoint))); if (!points) throw "Cannot allocate skip points";
        seek(files_[kind*2+1],80+offset*24);
        for (U64 i=0;i<n;++i) {
            read(files_[kind*2+1],row,24); auto& point=points[i]; point={decode(row),decode(row+8),decode(row+16)};
            if (point.bytes>bytes || point.consumed!=(i+1)*step || !point.bytes || ((point.consumed==count)!=(point.bytes==bytes)) ||
                (i && (point.value<=points[i-1].value || point.bytes<=points[i-1].bytes))) throw "Invalid skip checkpoint";
        }
        size=n; return true;
    } catch (const char* e) { std::free(points); points=nullptr; *error=e; return false; }
}
bool gaming::SkipIndex::verify(const char** error) const {
    *error=nullptr;
    try {
        for (unsigned k=0;k<4;++k) {
            if (!files_[k]) throw "Skip index is not open";
            const auto h=header(files_[k],k); seek(files_[k],80); unsigned char p[65536]; U64 left=h.bytes,checksum=basis;
            while (left) { const auto n=static_cast<std::size_t>(left>sizeof(p) ? sizeof(p) : left); read(files_[k],p,n); checksum=hash(checksum,p,n); left-=n; }
            if (checksum!=h.checksum) throw "Skip checksum mismatch";
        }
        return true;
    } catch (const char* e) { *error=e; return false; }
}
int gaming::run_skip_index(int argc,const char* const* argv,FILE* out,FILE* err) {
    try {
        if (argc<2) throw "Expected build or verify";
        if (!std::strcmp(argv[1],"--help")) { std::fputs("UTF-8 skip index: skip_index build|verify --index DIR --positions DIR --compact DIR [--output NEW_DIR | --skips DIR] [--reserve-mib 4608]\n",out); return 0; }
        const char *base=nullptr,*positions=nullptr,*compact=nullptr,*output=nullptr,*skips=nullptr; U64 reserve_mib=4608;
        for (int i=2;i<argc;++i) {
            const char* key=argv[i]; if (++i==argc) throw "Missing skip option value"; const char* value=argv[i];
            if (!std::strcmp(key,"--index")) base=value; else if (!std::strcmp(key,"--positions")) positions=value;
            else if (!std::strcmp(key,"--compact")) compact=value; else if (!std::strcmp(key,"--output")) output=value;
            else if (!std::strcmp(key,"--skips")) skips=value;
            else if (!std::strcmp(key,"--reserve-mib")) {
                reserve_mib=0; if (!*value) throw "Invalid skip reserve";
                for (const char* p=value;*p;++p) { if (*p<'0' || *p>'9' || reserve_mib>(UINT64_MAX-unsigned(*p-'0'))/10) throw "Invalid skip reserve"; reserve_mib=reserve_mib*10+unsigned(*p-'0'); }
            } else throw "Unknown skip option";
        }
        if (!base || !positions || !compact || reserve_mib>UINT64_MAX/(1024*1024)) throw "Invalid skip options";
        if (!std::strcmp(argv[1],"build")) { if (!output) throw "--output required"; build(base,positions,compact,output,reserve_mib*1024*1024,out); }
        else if (!std::strcmp(argv[1],"verify")) {
            if (!skips) throw "--skips required";
            const char* error=nullptr; BooleanIndex index; require(index.open(base,&error,true),error);
            PositionalIndex pos; require(pos.open(positions,base,index,&error,true),error);
            CompactIndex cmp; require(cmp.open(compact,base,positions,index,pos,&error),error);
            SkipIndex result; require(result.open(skips,compact,index,&error),error); require(result.verify(&error),error);
            // Each checkpoint is compared with the actual VB stream, not just its checksum.
            require(cmp.verify(&error),error);
            for (unsigned kind=0;kind<2;++kind) {
                char name[4096];
                path(name,compact,compact_names[kind*2]); File source_table(name); seek(source_table.f,80);
                path(name,compact,compact_names[kind*2+1]); File source_data(name); FILE* f=source_data.f;
                path(name,skips,names[kind*2]); File table(name); seek(table.f,80);
                path(name,skips,names[kind*2+1]); File data(name); seek(data.f,80);
                U64 expected_offset=0;
                for (U64 ordinal=0;ordinal<index.terms();++ordinal) {
                unsigned char row[16]; read(source_table.f,row,16); const U64 start=80+decode(row),bytes=decode(row+8); seek(f,start);
                read(table.f,row,16); const U64 offset=decode(row),n=decode(row+8);
                if (offset!=expected_offset) throw "Invalid skip table continuity";
                expected_offset+=n;
                U64 remaining=bytes,previous=0,doc=0,position=0,consumed=0; unsigned field=0;
                for (U64 j=0;j<n;++j) {
                    unsigned char p[24]; read(data.f,p,24); const SkipPoint point{decode(p),decode(p+8),decode(p+16)};
                    if (point.consumed<=consumed) throw "Invalid skip checkpoint ordering";
                    while (consumed<point.consumed) {
                        U64 delta=0,value=0; require(read_compact_uint(f,remaining,delta),"Truncated skip source");
                        if (kind) {
                            U64 code=0; require(read_compact_uint(f,remaining,code),"Truncated skip source"); const unsigned next_field=static_cast<unsigned>(code&1);
                            if (!consumed || delta || field!=next_field) position=0;
                            if (delta>UINT32_MAX-doc || (code>>1)>0x7fffffffU-position) throw "Skip source overflow";
                            doc+=delta; position+=code>>1; field=next_field; value=(doc<<32)|(U64(field)<<31)|position;
                        } else { if (!delta || delta>UINT64_MAX-previous) throw "Skip source overflow"; value=previous+delta; }
                        previous=value; ++consumed;
                    }
                    if (point.value!=previous || point.bytes!=tell(f)-start) throw "Skip checkpoint differs from source";
                }
                // Decode any tail to determine the full list size, including lists
                // without checkpoints; validate sqrt spacing against that size.
                const U64 checkpoint_consumed=consumed;
                while (remaining) {
                    U64 value=0; require(read_compact_uint(f,remaining,value),"Truncated skip source");
                    if (kind) require(read_compact_uint(f,remaining,value),"Truncated skip source");
                    ++consumed;
                }
                const U64 step=stride(consumed);
                if (n!=(step ? consumed/step : 0) || (n && checkpoint_consumed!=n*step)) throw "Invalid skip checkpoint spacing";
                }
                const auto h=header(data.f,kind*2+1);
                if (h.count!=expected_offset) throw "Unreferenced skip checkpoints";
            }
            std::fputs("Skip index verified: checksums and every checkpoint match VB source\n",out);
        } else throw "Unknown skip mode";
        return 0;
    } catch (const char* e) { std::fprintf(err,"[skip_index] %s\n",e); return 1; }
      catch (const std::exception&) { std::fputs("[skip_index] Allocation failure\n",err); return 1; }
}
