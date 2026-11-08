#include "lemmatizer.hpp"

#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace {
std::uint32_t u32(const unsigned char* p) {
    return std::uint32_t(p[0]) | (std::uint32_t(p[1]) << 8) |
           (std::uint32_t(p[2]) << 16) | (std::uint32_t(p[3]) << 24);
}
std::uint64_t u64(const unsigned char* p) { return u32(p) | (std::uint64_t(u32(p+4)) << 32); }
std::uint64_t hash(const void* data, std::size_t size) {
    auto* p = static_cast<const unsigned char*>(data);
    std::uint64_t h = 14695981039346656037ULL;
    for (std::size_t i=0; i<size; ++i) { h ^= p[i]; h *= 1099511628211ULL; }
    return h;
}
bool utf8(const unsigned char* p, std::size_t n) {
    for (std::size_t i=0; i<n;) {
        unsigned c = p[i++], need = 0, cp = 0, minimum = 0;
        if (c < 128) { if (!c || c < 32 || c == 127) return false; continue; }
        if (c >= 0xc2 && c <= 0xdf) { need=1; cp=c&31; minimum=128; }
        else if (c >= 0xe0 && c <= 0xef) { need=2; cp=c&15; minimum=2048; }
        else if (c >= 0xf0 && c <= 0xf4) { need=3; cp=c&7; minimum=65536; }
        else return false;
        if (need > n-i) return false;
        while (need--) { c=p[i++]; if ((c&192)!=128) return false; cp=(cp<<6)|(c&63); }
        if (cp<minimum || cp>0x10ffff || (cp>=0xd800 && cp<=0xdfff)) return false;
    }
    return true;
}
bool add(gaming::LemmaResult& out, const char* p, std::size_t n) {
    for (std::size_t i=0; i<out.count; ++i)
        if (out.values[i].size==n && !std::memcmp(out.values[i].data,p,n)) return true;
    if (out.count==64) return false;
    out.values[out.count++]={p,n}; out.known=true; return true;
}
struct Rule { char pos; const char* suffix; const char* ending; };
constexpr Rule rules[] = {
    {'n',"s",""},{'n',"ses","s"},{'n',"xes","x"},{'n',"zes","z"},
    {'n',"ches","ch"},{'n',"shes","sh"},{'n',"men","man"},{'n',"ies","y"},
    {'v',"s",""},{'v',"ies","y"},{'v',"es","e"},{'v',"es",""},
    {'v',"ed","e"},{'v',"ed",""},{'v',"ing","e"},{'v',"ing",""},
    {'a',"er",""},{'a',"est",""},{'a',"er","e"},{'a',"est","e"}
};
}

gaming::Lemmatizer::~Lemmatizer() { clear(); }
void gaming::Lemmatizer::clear() {
    std::free(payload_); std::free(slots_);
    payload_=nullptr; slots_=nullptr; strings_=nullptr;
    count_=0; payload_size_=0; capacity_=0;
}

bool gaming::Lemmatizer::load(const char* path, const char** error) {
    *error=nullptr;
    // A failed reload must not leave a partially usable dictionary.
    clear();
    FILE* f=std::fopen(path,"rb");
    if (!f) { *error="Cannot open dictionary"; return false; }
    unsigned char header[32];
    if (std::fread(header,1,32,f)!=32 || std::memcmp(header,"MAIIL4\0\1",8)) {
        std::fclose(f); *error="Invalid dictionary header"; return false;
    }
    const auto n=u64(header+8), blob=u64(header+16), expected=u64(header+24);
    if (!n || n>10000000 || !blob || blob>512ULL*1024*1024 || n*16+blob>768ULL*1024*1024) {
        std::fclose(f); *error="Dictionary size limit exceeded"; return false;
    }
    count_=static_cast<std::size_t>(n); payload_size_=static_cast<std::size_t>(n*16+blob);
    payload_=static_cast<unsigned char*>(std::malloc(payload_size_));
    if (!payload_) { std::fclose(f); clear(); *error="Dictionary allocation failed"; return false; }
    const bool read_ok=std::fread(payload_,1,payload_size_,f)==payload_size_ &&
                       std::fgetc(f)==EOF && !std::ferror(f);
    std::fclose(f);
    if (!read_ok || hash(payload_,payload_size_)!=expected) {
        clear(); *error="Truncated, extended or corrupted dictionary"; return false;
    }
    strings_=reinterpret_cast<const char*>(payload_+count_*16);
    capacity_=2; while (capacity_<count_*2) capacity_*=2;
    slots_=static_cast<std::uint32_t*>(std::calloc(capacity_,sizeof(std::uint32_t)));
    if (!slots_) { clear(); *error="Hash table allocation failed"; return false; }
    for (std::size_t i=0; i<count_; ++i) {
        const auto* r=payload_+i*16;
        const std::uint64_t ko=u32(r), kn=u32(r+4), vo=u32(r+8), vn=u32(r+12);
        if (!kn || !vn || ko+kn>blob || vo+vn>blob || kn>4096 || strings_[vo+vn-1]!=0 ||
            !utf8(reinterpret_cast<const unsigned char*>(strings_+ko),static_cast<std::size_t>(kn))) {
            clear(); *error="Invalid dictionary record"; return false;
        }
        std::size_t start=0, values=0;
        for (std::size_t j=0; j<vn; ++j) if (strings_[vo+j]==0) {
            if (j==start || ++values>64 ||
                !utf8(reinterpret_cast<const unsigned char*>(strings_+vo+start),j-start)) {
                clear(); *error="Invalid dictionary lemmas"; return false;
            }
            start=j+1;
        }
        auto slot=static_cast<std::size_t>(hash(strings_+ko,static_cast<std::size_t>(kn)))&(capacity_-1);
        while (slots_[slot]) {
            const auto* other=payload_+(slots_[slot]-1)*16;
            if (u32(other+4)==kn && !std::memcmp(strings_+u32(other),strings_+ko,static_cast<std::size_t>(kn))) {
                clear(); *error="Duplicate dictionary key"; return false;
            }
            slot=(slot+1)&(capacity_-1);
        }
        slots_[slot]=static_cast<std::uint32_t>(i+1);
    }
    return true;
}

bool gaming::Lemmatizer::lookup(const char* key, std::size_t size, LemmaResult& out) const {
    auto slot=static_cast<std::size_t>(hash(key,size))&(capacity_-1);
    while (slots_[slot]) {
        const auto* r=payload_+(slots_[slot]-1)*16;
        if (u32(r+4)==size && !std::memcmp(strings_+u32(r),key,size)) {
            const auto* p=strings_+u32(r+8); const auto bytes=u32(r+12);
            std::size_t start=0;
            for (std::size_t j=0; j<bytes; ++j) if (!p[j]) {
                if (!add(out,p+start,j-start)) return false;
                start=j+1;
            }
            return true;
        }
        slot=(slot+1)&(capacity_-1);
    }
    return true;
}

bool gaming::Lemmatizer::analyze(const char* word, std::size_t size, LemmaResult& out) const {
    out.count=0; out.known=false;
    if (!size) return true;
    bool english=size>0, russian=size>0;
    for (std::size_t i=0; i<size; ++i) {
        const auto c=static_cast<unsigned char>(word[i]);
        if (c<'a' || c>'z') english=false;
        if (i+1<size && c==0xd0 && static_cast<unsigned char>(word[i+1])>=0xb0 &&
            static_cast<unsigned char>(word[i+1])<=0xbf) ++i;
        else if (i+1<size && c==0xd1 &&
                 (static_cast<unsigned char>(word[i+1])>=0x80 && static_cast<unsigned char>(word[i+1])<=0x91) &&
                 static_cast<unsigned char>(word[i+1])!=0x90) ++i;
        else russian=false;
    }
    char key[4104];
    if (slots_ && size<=4096 && russian) {
        std::memcpy(key,"ru:",3); std::memcpy(key+3,word,size);
        if (!lookup(key,size+3,out)) return false;
    } else if (slots_ && size<=4096 && english) {
        // Keep base forms as well: "saw" can be both a noun and "see".
        for (const char pos : "nvar") {
            if (!pos) break;
            key[0]=pos; key[1]=':'; std::memcpy(key+2,word,size);
            if (!lookup(key,size+2,out)) return false;
            key[0]='x'; key[1]=':'; key[2]=pos; key[3]=':'; std::memcpy(key+4,word,size);
            LemmaResult exceptions;
            if (!lookup(key,size+4,exceptions)) return false;
            if (exceptions.known) {
                for (std::size_t i=0;i<exceptions.count;++i)
                    if (!add(out,exceptions.values[i].data,exceptions.values[i].size)) return false;
                continue;
            }
            for (const auto& rule : rules) if (rule.pos==pos) {
                const auto suffix=std::strlen(rule.suffix), ending=std::strlen(rule.ending);
                if (size<=suffix || std::memcmp(word+size-suffix,rule.suffix,suffix)) continue;
                key[0]=pos; key[1]=':';
                std::memcpy(key+2,word,size-suffix);
                std::memcpy(key+2+size-suffix,rule.ending,ending);
                if (!lookup(key,2+size-suffix+ending,out)) return false;
            }
        }
    }
    if (!out.count) { out.values[0]={word,size}; out.count=1; }
    return true;
}
