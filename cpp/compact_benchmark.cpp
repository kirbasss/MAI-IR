#include "compact_index.hpp"
#include "positional_index.hpp"
#include <chrono>
#include <cstdlib>
#include <cstring>

namespace {
using U64=std::uint64_t;
using Clock=std::chrono::steady_clock;
void require(bool ok,const char* error) { if (!ok) throw (error ? error : "Benchmark operation failed"); }
struct File {
    FILE* f;
    explicit File(FILE* value):f(value) { if (!f) throw "Cannot open benchmark stream"; }
    ~File() { std::fclose(f); }
};
struct Bytes {
    unsigned char* p; U64 size;
    explicit Bytes(U64 n):p(nullptr),size(n) {
        // A diagnostic operates on one term at a time, never on the full index.
        if (n>512ULL*1024*1024) throw "Benchmark list exceeds 512 MiB memory limit";
        p=static_cast<unsigned char*>(std::malloc(n ? static_cast<std::size_t>(n) : 1));
        if (!p) throw "Cannot allocate benchmark buffer";
        std::memset(p,0,n ? static_cast<std::size_t>(n) : 1);
    }
    ~Bytes() { std::free(p); }
};
void seek(FILE* f,U64 offset) {
    if (offset>INT64_MAX) throw "Benchmark offset overflow";
#ifdef _WIN32
    if (_fseeki64(f,static_cast<__int64>(offset),SEEK_SET)) throw "Cannot seek benchmark stream";
#else
    if (fseeko(f,static_cast<off_t>(offset),SEEK_SET)) throw "Cannot seek benchmark stream";
#endif
}
template<class Action> double timed(Action action) {
    const auto start=Clock::now(); action();
    return std::chrono::duration<double,std::milli>(Clock::now()-start).count();
}
double load(FILE* f,U64 offset,Bytes& bytes) {
    return timed([&]() {
        seek(f,offset);
        if (std::fread(bytes.p,1,static_cast<std::size_t>(bytes.size),f)!=bytes.size) throw "Truncated benchmark list";
    });
}
U64 integer(const unsigned char* p,unsigned width) {
    U64 result=0; for (unsigned i=0;i<width;++i) result|=U64(p[i])<<(8*i); return result;
}
U64 digest(U64 previous,U64 value) { return (previous^value)*1099511628211ULL; }
U64 raw_decode(const Bytes& bytes,U64 count,bool coordinates) {
    U64 checksum=14695981039346656037ULL;
    if (bytes.size!=count*8) throw "Unexpected raw benchmark list size";
    for (U64 i=0;i<count;++i) {
        const auto* p=bytes.p+i*8;
        const U64 value=coordinates ? (integer(p,4)<<32)|integer(p+4,4) : integer(p,8);
        checksum=digest(checksum,value);
    }
    return checksum;
}
U64 compact_decode(const Bytes& bytes,U64 count,bool coordinates) {
    const unsigned char* p=bytes.p; U64 remaining=bytes.size,checksum=14695981039346656037ULL;
    U64 previous=0,previous_doc=0,previous_position=0; unsigned previous_field=0;
    for (U64 i=0;i<count;++i) {
        U64 delta=0,value=0;
        if (!gaming::read_compact_uint(p,remaining,delta)) throw "Invalid benchmark VB value";
        if (coordinates) {
            U64 code=0;
            if (!gaming::read_compact_uint(p,remaining,code) || delta>UINT32_MAX-previous_doc) throw "Invalid benchmark coordinate";
            const U64 doc=previous_doc+delta; const unsigned field=static_cast<unsigned>(code&1);
            const U64 base=i==0 || delta || field!=previous_field ? 0 : previous_position;
            if ((code>>1)>0x7fffffffU-base) throw "Benchmark position overflow";
            const U64 position=base+(code>>1);
            value=(doc<<32)|(U64(field)<<31)|position;
            if (i && value<=previous) throw "Unsorted benchmark coordinates";
            previous_doc=doc; previous_field=field; previous_position=position;
        } else {
            if (!delta || delta>UINT64_MAX-previous) throw "Benchmark posting overflow";
            value=previous+delta;
        }
        previous=value; checksum=digest(checksum,value);
    }
    if (remaining) throw "Trailing benchmark VB bytes";
    return checksum;
}
U64 scan(gaming::BooleanIndex& index,gaming::PositionalIndex& positions,
         const gaming::PostingList& posting,const gaming::PositionList& position,bool coordinates,U64 count) {
    const char* error=nullptr; U64 value=0,seen=0,checksum=14695981039346656037ULL;
    if (coordinates) {
        gaming::PositionCursor cursor; require(cursor.open(positions,position,&error),error);
        while (cursor.next(value,&error)) { checksum=digest(checksum,value); ++seen; }
    } else {
        gaming::PostingCursor cursor; require(cursor.open(index,posting,&error),error);
        while (cursor.next(value,&error)) { checksum=digest(checksum,value); ++seen; }
    }
    if (error) throw error;
    if (seen!=count) throw "Benchmark cursor count mismatch";
    return checksum;
}
}

void gaming::benchmark_compact_lists(const char* base,const char* positions_dir,const char* compact_dir,
                                    const char* term,TermSpace space,unsigned repeats,FILE* output) {
    const char* error=nullptr;
    BooleanIndex index; require(index.open(base,&error),error);
    PositionalIndex positions; require(positions.open(positions_dir,base,index,&error),error);
    CompactIndex compact; require(compact.open(compact_dir,base,positions_dir,index,positions,&error),error);
    PostingList posting; require(index.lookup(term,std::strlen(term),space,posting,&error),error);
    if (posting.ordinal==UINT64_MAX) throw "Benchmark term is absent";
    PositionList position; require(positions.lookup(term,std::strlen(term),space,position,&error),error);
    for (unsigned kind=0;kind<2;++kind) {
        const bool coordinates=kind!=0;
        const U64 count=coordinates ? position.count : posting.count;
        U64 vb_bytes=0;
        File raw(coordinates ? positions.stream(position,&error) : index.open_postings(posting,&error));
        File vb(compact.stream(coordinates ? CompactKind::coordinates : CompactKind::postings,posting.ordinal,vb_bytes,&error));
        // Offset is obtained from the compressed term table, outside timing.
#ifdef _WIN32
        const auto vb_start=_ftelli64(vb.f);
#else
        const auto vb_start=ftello(vb.f);
#endif
        if (vb_start<0) throw "Cannot get benchmark stream offset";
        const U64 raw_start=80+(coordinates ? position.offset*8 : posting.offset);
        Bytes raw_data(count*8),vb_data(vb_bytes);
        // Untimed read/decode warm-up; all subsequent results are cache-sensitive.
        load(raw.f,raw_start,raw_data); load(vb.f,static_cast<U64>(vb_start),vb_data);
        const U64 expected=raw_decode(raw_data,count,coordinates);
        if (compact_decode(vb_data,count,coordinates)!=expected) throw "Benchmark decoded data differs";
        for (unsigned repeat=0;repeat<repeats;++repeat) {
            double reads[2]{},decodes[2]{},scans[2]{};
            for (unsigned step=0;step<2;++step) {
                const bool compressed=(step+(repeat%2))%2!=0;
                const unsigned k=compressed ? 1 : 0;
                reads[k]=compressed ? load(vb.f,static_cast<U64>(vb_start),vb_data) : load(raw.f,raw_start,raw_data);
                U64 checksum=0;
                decodes[k]=timed([&]() { checksum=compressed ? compact_decode(vb_data,count,coordinates) : raw_decode(raw_data,count,coordinates); });
                if (checksum!=expected) throw "Benchmark memory decoding differs";
                index.use_compressed(compressed ? &compact : nullptr);
                positions.use_compressed(compressed ? &compact : nullptr);
                scans[k]=timed([&]() { checksum=scan(index,positions,posting,position,coordinates,count); });
                if (checksum!=expected) throw "Benchmark cursor decoding differs";
            }
            std::fprintf(output,"{\"kind\":\"%s\",\"repeat\":%u,\"count\":%llu,\"raw_bytes\":%llu,\"vb_bytes\":%llu,"
                         "\"raw_read_ms\":%.6f,\"vb_read_ms\":%.6f,\"raw_decode_ms\":%.6f,\"vb_decode_ms\":%.6f,"
                         "\"raw_cursor_ms\":%.6f,\"vb_cursor_ms\":%.6f,\"checksum\":\"%016llx\"}\n",
                         coordinates ? "coordinates" : "postings",repeat,
                         static_cast<unsigned long long>(count),static_cast<unsigned long long>(raw_data.size),
                         static_cast<unsigned long long>(vb_data.size),reads[0],reads[1],decodes[0],decodes[1],scans[0],scans[1],
                         static_cast<unsigned long long>(expected));
        }
        index.use_compressed(nullptr); positions.use_compressed(nullptr);
    }
}
