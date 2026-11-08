#include "zipf.hpp"
#include "tokenizer.hpp"
#include "lemmatizer.hpp"
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <cerrno>
#include <fcntl.h>
#include <sys/stat.h>
#ifdef _WIN32
#include <direct.h>
#include <io.h>
#else
#include <unistd.h>
#endif

namespace {
constexpr std::size_t max_field=64*1024*1024, max_groups=128, fan_in=24;
using U64=std::uint64_t;
void checked_add(U64& target,U64 value) {
    if (UINT64_MAX-target<value) throw "Counter overflow";
    target+=value;
}
struct File {
    FILE* f=nullptr;
    explicit File(const char* path,const char* mode="rb") {
        if (!std::strcmp(mode,"wbx")) {
            // MinGW's older Windows CRT does not implement fopen("wbx").
#ifdef _WIN32
            const int fd=_open(path,_O_WRONLY|_O_CREAT|_O_EXCL|_O_BINARY,_S_IREAD|_S_IWRITE);
            if (fd>=0) { f=_fdopen(fd,"wb"); if (!f) _close(fd); }
#else
            const int fd=::open(path,O_WRONLY|O_CREAT|O_EXCL,0644);
            if (fd>=0) { f=::fdopen(fd,"wb"); if (!f) ::close(fd); }
#endif
        } else f=std::fopen(path,mode);
        if (!f) throw "Cannot open file (input must exist; output must be new)";
    }
    ~File() { if (f) std::fclose(f); }
    void finish() { const int code=std::fclose(f); f=nullptr; if (code) throw "Cannot close output"; }
};
void write_bytes(FILE* f,const void* p,std::size_t n) {
    if (n && std::fwrite(p,1,n,f)!=n) throw "Cannot write output (check free space)";
}
void read_bytes(FILE* f,void* p,std::size_t n) {
    if (n && std::fread(p,1,n,f)!=n) throw "Truncated input";
}
U64 number(FILE* f,unsigned bytes) {
    unsigned char p[8]; read_bytes(f,p,bytes); U64 result=0;
    for (unsigned i=0;i<bytes;++i) result|=U64(p[i])<<(8*i);
    return result;
}
void number(FILE* f,U64 value,unsigned bytes) {
    unsigned char p[8]; for (unsigned i=0;i<bytes;++i) p[i]=static_cast<unsigned char>(value>>(8*i));
    write_bytes(f,p,bytes);
}
struct Buffer {
    char* data=nullptr; std::size_t size=0,capacity=0;
    ~Buffer() { std::free(data); }
    void resize(std::size_t n) {
        if (n>max_field) throw "Field size limit exceeded";
        if (n>capacity) {
            void* p=std::realloc(data,n); if (!p) throw "Allocation failed";
            data=static_cast<char*>(p); capacity=n;
        }
        size=n;
    }
};
struct Row { std::uint32_t group=0,size=0; U64 count=0; const char* word=nullptr; };
int compare(const Row& a,const Row& b,bool frequency) {
    if (a.group!=b.group) return a.group<b.group ? -1 : 1;
    if (frequency && a.count!=b.count) return a.count>b.count ? -1 : 1;
    const auto n=a.size<b.size ? a.size : b.size;
    const int c=std::memcmp(a.word,b.word,n);
    if (c) return c;
    return a.size==b.size ? 0 : (a.size<b.size ? -1 : 1);
}
void exchange(Row& a,Row& b) { const Row t=a; a=b; b=t; }
void sift(Row* rows,std::size_t n,std::size_t root,bool frequency) {
    while (root<n/2) {
        auto child=root*2+1;
        if (child+1<n && compare(rows[child],rows[child+1],frequency)<0) ++child;
        if (compare(rows[root],rows[child],frequency)>=0) return;
        exchange(rows[root],rows[child]); root=child;
    }
}
void sort(Row* rows,std::size_t n,bool frequency) {
    for (auto i=n/2;i>0;--i) sift(rows,n,i-1,frequency);
    for (auto i=n;i>1;--i) { exchange(rows[0],rows[i-1]); sift(rows,i-1,0,frequency); }
}
void write_row(FILE* f,const Row& row) {
    number(f,row.group,4); number(f,row.size,4); number(f,row.count,8); write_bytes(f,row.word,row.size);
}
struct Reader {
    FILE* f=nullptr; Buffer buffer; Row row;
    ~Reader() { if (f) std::fclose(f); }
    bool next() {
        const int c=std::fgetc(f);
        if (c==EOF) { if (std::ferror(f)) throw "Cannot read run"; return false; }
        if (std::ungetc(c,f)==EOF) throw "Cannot read record prefix";
        row.group=static_cast<std::uint32_t>(number(f,4));
        row.size=static_cast<std::uint32_t>(number(f,4)); row.count=number(f,8);
        if (row.group>=max_groups || !row.size || !row.count) throw "Invalid run record";
        buffer.resize(row.size); read_bytes(f,buffer.data,buffer.size); row.word=buffer.data; return true;
    }
};
struct Paths {
    char** data=nullptr; std::size_t size=0,capacity=0;
    ~Paths() { for (std::size_t i=0;i<size;++i) std::free(data[i]); std::free(data); }
    void add(const char* path) {
        if (size==capacity) {
            const auto n=capacity ? capacity*2 : 16;
            void* p=std::realloc(data,n*sizeof(char*)); if (!p) throw "Path allocation failed";
            data=static_cast<char**>(p); capacity=n;
        }
        auto* copy=static_cast<char*>(std::malloc(std::strlen(path)+1));
        if (!copy) throw "Path allocation failed";
        std::strcpy(copy,path); data[size++]=copy;
    }
    void take(Paths& other) {
        for (std::size_t i=0;i<size;++i) std::free(data[i]);
        std::free(data);
        data=other.data; size=other.size; capacity=other.capacity;
        other.data=nullptr; other.size=other.capacity=0;
    }
};
struct Workspace {
    const char* directory; U64 serial=0;
    explicit Workspace(const char* path) : directory(path) {
        if (!*path || std::strlen(path)>3500) throw "Invalid output directory";
#ifdef _WIN32
        const int code=_mkdir(path);
#else
        const int code=mkdir(path,0755);
#endif
        if (code) throw "Output directory must be NEW, with an existing parent directory";
        char marker[4096]; named(marker,"INCOMPLETE"); File f(marker,"wbx");
        const char note[]="Incomplete Zipf run; do not use these results.\n";
        write_bytes(f.f,note,sizeof(note)-1); f.finish();
    }
    void named(char* out,const char* name) const { std::snprintf(out,4096,"%s/%s",directory,name); }
    void next(char* out) { std::snprintf(out,4096,"%s/run_%llu.bin",directory,static_cast<unsigned long long>(++serial)); }
};
// At most fan_in files are open. Own binary min-heap of reader indices.
void merge(const Paths& paths,std::size_t begin,std::size_t end,const char* target,bool frequency) {
    Reader readers[fan_in]; unsigned heap[fan_in]; std::size_t used=0;
    for (std::size_t i=begin;i<end;++i) {
        auto& r=readers[i-begin]; r.f=std::fopen(paths.data[i],"rb");
        if (!r.f) throw "Cannot open temporary run";
        if (!r.next()) continue;
        std::size_t child=used++; heap[child]=static_cast<unsigned>(i-begin);
        while (child) {
            const auto parent=(child-1)/2;
            if (compare(readers[heap[parent]].row,readers[heap[child]].row,frequency)<=0) break;
            const auto t=heap[parent]; heap[parent]=heap[child]; heap[child]=t; child=parent;
        }
    }
    File output(target,"wbx"); Row pending; Buffer word; bool have=false;
    while (used) {
        auto& reader=readers[heap[0]];
        if (have && !frequency && compare(pending,reader.row,false)==0) checked_add(pending.count,reader.row.count);
        else {
            if (have) write_row(output.f,pending);
            word.resize(reader.row.size); std::memcpy(word.data,reader.row.word,word.size);
            pending=reader.row; pending.word=word.data; have=true;
        }
        if (!reader.next()) heap[0]=heap[--used];
        std::size_t parent=0;
        while (parent<used/2) {
            auto child=parent*2+1;
            if (child+1<used && compare(readers[heap[child+1]].row,readers[heap[child]].row,frequency)<0) ++child;
            if (compare(readers[heap[parent]].row,readers[heap[child]].row,frequency)<=0) break;
            const auto t=heap[parent]; heap[parent]=heap[child]; heap[child]=t; parent=child;
        }
    }
    if (have) write_row(output.f,pending);
    output.finish();
}
void reduce(Paths& paths,Workspace& work,bool frequency) {
    while (paths.size>1) {
        Paths next;
        for (std::size_t begin=0;begin<paths.size;begin+=fan_in) {
            const auto end=paths.size-begin>fan_in ? begin+fan_in : paths.size;
            char path[4096]; work.next(path); merge(paths,begin,end,path,frequency); next.add(path);
            for (auto i=begin;i<end;++i) if (std::remove(paths.data[i])) throw "Cannot remove owned temporary run";
        }
        paths.take(next);
    }
}
class Counter {
    std::uint32_t* slots_=nullptr; Row* rows_=nullptr; char* pool_=nullptr;
    std::size_t capacity_=0,size_=0,pool_size_=0,pool_used_=0;
    Paths& runs_; Workspace& work_; bool frequency_;
public:
    Counter(std::size_t bytes,Paths& runs,Workspace& work,bool frequency=false) : runs_(runs),work_(work),frequency_(frequency) {
        capacity_=2;
        while (capacity_*2<=bytes/32) capacity_*=2;
        pool_size_=bytes-capacity_*sizeof(std::uint32_t)-(capacity_/2)*sizeof(Row);
        slots_=static_cast<std::uint32_t*>(std::calloc(capacity_,sizeof(std::uint32_t)));
        rows_=static_cast<Row*>(std::malloc(capacity_/2*sizeof(Row)));
        pool_=static_cast<char*>(std::malloc(pool_size_));
        if (!slots_ || !rows_ || !pool_) { std::free(slots_); std::free(rows_); std::free(pool_); throw "Counter allocation failed"; }
    }
    ~Counter() { std::free(slots_); std::free(rows_); std::free(pool_); }
    void add(unsigned group,const char* word,std::size_t size,U64 count=1) {
        if (!size || size>pool_size_ || size>UINT32_MAX) throw "A token exceeds the configured counting buffer; increase --memory-mib";
        U64 hash=14695981039346656037ULL^group;
        for (std::size_t i=0;i<size;++i) { hash^=static_cast<unsigned char>(word[i]); hash*=1099511628211ULL; }
        auto slot=static_cast<std::size_t>(hash)&(capacity_-1);
        if (!frequency_) while (slots_[slot]) {
            Row& row=rows_[slots_[slot]-1];
            if (row.group==group && row.size==size && !std::memcmp(row.word,word,size)) { checked_add(row.count,count); return; }
            slot=(slot+1)&(capacity_-1);
        }
        if (size_==capacity_/2 || pool_used_+size>pool_size_) { flush(); slot=static_cast<std::size_t>(hash)&(capacity_-1); }
        std::memcpy(pool_+pool_used_,word,size);
        rows_[size_]={group,static_cast<std::uint32_t>(size),count,pool_+pool_used_};
        slots_[slot]=static_cast<std::uint32_t>(++size_); pool_used_+=size;
    }
    void flush() {
        if (!size_) return;
        sort(rows_,size_,frequency_); char path[4096]; work_.next(path); File file(path,"wbx");
        for (std::size_t i=0;i<size_;++i) write_row(file.f,rows_[i]);
        file.finish(); runs_.add(path); size_=pool_used_=0; std::memset(slots_,0,capacity_*sizeof(std::uint32_t));
    }
};
struct Group {
    char name[96]{}; U64 documents=0,tokens=0,vocabulary=0,hapax=0,top=0,fit_n=0;
    double harmonic=0,sx=0,sy=0,sxx=0,sxy=0,syy=0;
};
struct Groups {
    Group rows[max_groups]; unsigned size=0;
    unsigned get(const char* name) {
        for (unsigned i=0;i<size;++i) if (!std::strcmp(rows[i].name,name)) return i;
        if (size==max_groups || std::strlen(name)>=sizeof(rows[0].name)) throw "Too many groups or group name too long";
        std::strcpy(rows[size].name,name); return size++;
    }
};
const char* language(const char* source) {
    const char* russian[]={"stopgame","playground","ixbt_games","igromania","gamemag"};
    const char* english[]={"pcgamer","eurogamer","gamingonlinux"};
    for (const char* name:russian)
        if (!std::strcmp(name,source)) return "ru";
    for (const char* name:english)
        if (!std::strcmp(name,source)) return "en";
    return "unknown";
}
bool identifier(const char* p,std::size_t n) {
    if (!n || n>64) return false;
    for (std::size_t i=0;i<n;++i)
        if (!((p[i]>='a' && p[i]<='z') || (i && ((p[i]>='0' && p[i]<='9') || p[i]=='_')))) return false;
    return true;
}
U64 positive(const char* p) {
    U64 value=0; if (!*p) throw "Expected positive integer";
    for (;*p;++p) {
        if (*p<'0' || *p>'9' || value>(UINT64_MAX-unsigned(*p-'0'))/10) throw "Invalid positive integer";
        value=value*10+unsigned(*p-'0');
    }
    if (!value) throw "Expected positive integer";
    return value;
}
void finite_json(FILE* f,double value) {
    if (std::isfinite(value)) std::fprintf(f,"%.12g",value); else std::fputs("null",f);
}
}

int gaming::run_zipf(int argc,const char* const* argv,FILE* output,FILE* errors) {
    try {
        if (argc==2 && !std::strcmp(argv[1],"--help")) {
            std::fputs("zipf --corpus MAIIR3.bin --output NEW_DIR [--groups TYPES.tsv] [--memory-mib 64]\n"
                "     [--field title|text|both] [--fit-min 10] [--fit-max 10000] [--dict FILE]\n"
                "C++ external counting: all, source, source-language, publication type. UTF-8.\n"
                "TYPES.tsv: strictly matching document_id TAB lowercase_type, without a header.\n"
                "--dict: merge unambiguous lemmas; ambiguous/unknown tokens remain unchanged.\n"
                "Each token counts once. No stop words removed. New output directory required.\n",output); return 0;
        }
        const char *corpus=nullptr,*directory=nullptr,*types=nullptr,*field="both",*dictionary_path=nullptr;
        U64 memory=64,fit_min=10,fit_max=10000;
        unsigned seen=0;
        for (int i=1;i<argc;i+=2) {
            if (i+1==argc) throw "Missing option value";
            const char *key=argv[i],*value=argv[i+1]; unsigned bit=0;
            if (!std::strcmp(key,"--corpus")) { bit=1; corpus=value; }
            else if (!std::strcmp(key,"--output")) { bit=2; directory=value; }
            else if (!std::strcmp(key,"--groups")) { bit=4; types=value; }
            else if (!std::strcmp(key,"--memory-mib")) { bit=8; memory=positive(value); }
            else if (!std::strcmp(key,"--field")) { bit=16; field=value; }
            else if (!std::strcmp(key,"--fit-min")) { bit=32; fit_min=positive(value); }
            else if (!std::strcmp(key,"--fit-max")) { bit=64; fit_max=positive(value); }
            else if (!std::strcmp(key,"--dict")) { bit=128; dictionary_path=value; }
            else throw "Unknown option";
            if (seen&bit) throw "Duplicate option";
            seen|=bit;
        }
        if (!corpus || !directory) throw "--corpus and --output are required";
        if (memory>2048 || fit_min>fit_max) throw "Invalid memory or fit range";
        if (std::strcmp(field,"both") && std::strcmp(field,"title") && std::strcmp(field,"text")) throw "Invalid --field";
        Lemmatizer dictionary;
        double dictionary_load_ms=0;
        if (dictionary_path) {
            const auto load_start=std::chrono::steady_clock::now(); const char* error=nullptr;
            if (!dictionary.load(dictionary_path,&error)) throw error;
            dictionary_load_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-load_start).count();
        }
        File input(corpus); FILE* type_input=nullptr;
        // Own optional stream, closed on every exit including exceptions.
        struct OptionalFile { FILE* f=nullptr; ~OptionalFile() { if (f) std::fclose(f); } } sidecar;
        if (types) { sidecar.f=std::fopen(types,"rb"); if (!sidecar.f) throw "Cannot open groups TSV"; type_input=sidecar.f; }
        char magic[8]; read_bytes(input.f,magic,8);
        if (std::memcmp(magic,"MAIIR3\0\1",8)) throw "Unsupported corpus format";
        Workspace work(directory); Groups groups; groups.get("all"); Paths runs;
        const auto start=std::chrono::steady_clock::now();
        U64 documents=0,bytes=0,previous=0,counting_runs=0;
        U64 known_tokens=0,changed_tokens=0,ambiguous_tokens=0;
        {
            Counter counter(static_cast<std::size_t>(memory)*1024*1024,runs,work); Buffer text;
            for (;;) {
                const int c=std::fgetc(input.f);
                if (c==EOF) { if (std::ferror(input.f)) throw "Cannot read corpus"; break; }
                if (std::ungetc(c,input.f)==EOF) throw "Cannot read corpus prefix";
                const auto id=number(input.f,8); if (id<=previous) throw "Document IDs must increase"; previous=id;
                const auto n=number(input.f,4); if (!n || n>64) throw "Invalid source size";
                char source[65]{}; read_bytes(input.f,source,static_cast<std::size_t>(n));
                if (!identifier(source,static_cast<std::size_t>(n))) throw "Invalid source identifier";
                unsigned selected[4]={0,0,0,0},used=3; char name[96];
                std::snprintf(name,sizeof(name),"source:%s",source); selected[1]=groups.get(name);
                std::snprintf(name,sizeof(name),"language:%s",language(source)); selected[2]=groups.get(name);
                if (type_input) {
                    char line[160]; if (!std::fgets(line,sizeof(line),type_input)) throw "Groups TSV has too few records";
                    auto len=std::strlen(line); if (!len || (line[len-1]!='\n' && !std::feof(type_input))) throw "Groups TSV line too long";
                    while (len && (line[len-1]=='\r' || line[len-1]=='\n')) line[--len]=0;
                    char* tab=std::strchr(line,'\t'); if (!tab || std::strchr(tab+1,'\t')) throw "Expected document_id TAB type";
                    *tab=0; if (positive(line)!=id || !identifier(tab+1,std::strlen(tab+1))) throw "Groups TSV ID/type mismatch";
                    std::snprintf(name,sizeof(name),"type:%s",tab+1); selected[3]=groups.get(name); used=4;
                }
                for (unsigned i=0;i<used;++i) checked_add(groups.rows[selected[i]].documents,1);
                for (unsigned f=0;f<2;++f) {
                    const auto length=number(input.f,8); if (length>max_field) throw "Field size limit exceeded";
                    text.resize(static_cast<std::size_t>(length)); read_bytes(input.f,text.data,text.size);
                    if ((f==0 && !std::strcmp(field,"text")) || (f==1 && !std::strcmp(field,"title"))) continue;
                    checked_add(bytes,length);
                    tokenize(std::string_view(text.data ? text.data : "",text.size),[&](const Token& token) {
                        const char* term=token.term.data(); std::size_t size=token.term.size();
                        if (dictionary_path) {
                            LemmaResult result;
                            if (!dictionary.analyze(term,size,result)) throw "Too many lemma alternatives";
                            known_tokens+=result.known;
                            if (result.count>1) ++ambiguous_tokens;
                            else if (result.count==1) {
                                const auto value=result.values[0];
                                changed_tokens+=(size!=value.size || std::memcmp(term,value.data,size));
                                term=value.data; size=value.size;
                            }
                        }
                        for (unsigned i=0;i<used;++i) {
                            counter.add(selected[i],term,size);
                            checked_add(groups.rows[selected[i]].tokens,1);
                        }
                    });
                }
                checked_add(documents,1);
            }
            counter.flush(); counting_runs=runs.size;
        }
        if (type_input && (std::fgetc(type_input)!=EOF || std::ferror(type_input))) throw "Groups TSV has extra records or read error";
        reduce(runs,work,false);
        Paths ranks;
        if (runs.size) {
            {
                Reader reader; reader.f=std::fopen(runs.data[0],"rb"); if (!reader.f) throw "Cannot open counts run";
                Counter sorter(static_cast<std::size_t>(memory)*1024*1024,ranks,work,true);
                while (reader.next()) sorter.add(reader.row.group,reader.row.word,reader.row.size,reader.row.count);
                sorter.flush();
            }
            if (std::remove(runs.data[0])) throw "Cannot remove owned counts run";
        }
        const auto ranking_runs=ranks.size; reduce(ranks,work,true);
        char target[4096]; work.named(target,"frequencies.tsv");
        {
            File table(target,"wbx"); std::fputs("group\trank\tterm\tfrequency\trelative_frequency\n",table.f);
            if (ranks.size) {
                Reader reader; reader.f=std::fopen(ranks.data[0],"rb"); if (!reader.f) throw "Cannot open ranked run";
                while (reader.next()) {
                    const auto& row=reader.row; if (row.group>=groups.size) throw "Invalid ranked group";
                    auto& g=groups.rows[row.group]; checked_add(g.vocabulary,1); const auto rank=g.vocabulary;
                    g.hapax+=row.count==1; if (rank==1) g.top=row.count; g.harmonic+=1.0/static_cast<double>(rank);
                    std::fprintf(table.f,"%s\t%llu\t",g.name,static_cast<unsigned long long>(rank));
                    write_bytes(table.f,row.word,row.size);
                    std::fprintf(table.f,"\t%llu\t%.12g\n",static_cast<unsigned long long>(row.count),double(row.count)/double(g.tokens));
                    if (rank>=fit_min && rank<=fit_max) {
                        const double x=std::log(double(rank)),y=std::log(double(row.count));
                        ++g.fit_n; g.sx+=x; g.sy+=y; g.sxx+=x*x; g.sxy+=x*y; g.syy+=y*y;
                    }
                }
            }
            if (std::ferror(table.f)) throw "Cannot write frequency table";
            table.finish();
        }
        if (ranks.size && std::remove(ranks.data[0])) throw "Cannot remove owned ranked run";
        work.named(target,"statistics.json");
        {
            File stats(target,"wbx");
            const auto elapsed=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
            std::fprintf(stats.f,"{\n\"format\":\"MAIIR-ZIPF-v1\",\"tokenizer\":\"%s\",\"field\":\"%s\","
                "\"representation\":\"%s\",\"known_tokens\":%llu,\"changed_tokens\":%llu,\"ambiguous_tokens\":%llu,"
                "\"dictionary_memory_bytes\":%llu,\"dictionary_load_ms\":%.6f,"
                "\"documents\":%llu,\"input_utf8_bytes\":%llu,\"counting_buffer_bytes\":%llu,"
                "\"counting_runs\":%llu,\"ranking_runs\":%llu,\"temporary_files_created\":%llu,"
                "\"elapsed_ms\":%.6f,\"fit_min\":%llu,\"fit_max\":%llu,\"has_types\":%s,\"groups\":[\n",
                rules_version,field,dictionary_path ? "lemma_unambiguous" : "surface",
                static_cast<unsigned long long>(known_tokens),static_cast<unsigned long long>(changed_tokens),
                static_cast<unsigned long long>(ambiguous_tokens),static_cast<unsigned long long>(dictionary.memory_bytes()),dictionary_load_ms,
                static_cast<unsigned long long>(documents),static_cast<unsigned long long>(bytes),
                static_cast<unsigned long long>(memory*1024*1024),static_cast<unsigned long long>(counting_runs),
                static_cast<unsigned long long>(ranking_runs),static_cast<unsigned long long>(work.serial),elapsed,
                static_cast<unsigned long long>(fit_min),static_cast<unsigned long long>(fit_max),types ? "true" : "false");
            for (unsigned i=0;i<groups.size;++i) {
                const auto& g=groups.rows[i];
                std::fprintf(stats.f,"%s{\"name\":\"%s\",\"documents\":%llu,\"tokens\":%llu,\"vocabulary\":%llu,"
                    "\"hapax\":%llu,\"top_frequency\":%llu,\"harmonic\":%.12g,\"zipf_c\":%.12g,\"fit_n\":%llu,\"alpha\":",
                    i ? ",\n" : "",g.name,static_cast<unsigned long long>(g.documents),static_cast<unsigned long long>(g.tokens),
                    static_cast<unsigned long long>(g.vocabulary),static_cast<unsigned long long>(g.hapax),
                    static_cast<unsigned long long>(g.top),g.harmonic,g.harmonic ? double(g.tokens)/g.harmonic : 0,
                    static_cast<unsigned long long>(g.fit_n));
                const double n=double(g.fit_n),den=n*g.sxx-g.sx*g.sx;
                const double alpha=(g.fit_n>=2 && den>0) ? -(n*g.sxy-g.sx*g.sy)/den : NAN;
                finite_json(stats.f,alpha); std::fputs(",\"power_c\":",stats.f);
                finite_json(stats.f,std::isfinite(alpha) ? std::exp((g.sy+alpha*g.sx)/n) : NAN);
                std::fputs(",\"r_squared_log\":",stats.f);
                const auto var=n*g.syy-g.sy*g.sy;
                finite_json(stats.f,(den>0 && var>0) ? (n*g.sxy-g.sx*g.sy)*(n*g.sxy-g.sx*g.sy)/(den*var) : NAN);
                std::fputc('}',stats.f);
            }
            std::fputs("\n]}\n",stats.f); if (std::ferror(stats.f)) throw "Cannot write statistics"; stats.finish();
        }
        work.named(target,"INCOMPLETE"); if (std::remove(target)) throw "Cannot finalize output directory";
        std::fprintf(output,"Zipf complete: %llu documents; %u groups; %s\n",static_cast<unsigned long long>(documents),groups.size,directory);
        return 0;
    } catch (const char* error) { std::fprintf(errors,"[zipf] %s\n",error); return 1; }
      catch (const std::exception& error) { std::fprintf(errors,"[zipf] %s\n",error.what()); return 1; }
}
