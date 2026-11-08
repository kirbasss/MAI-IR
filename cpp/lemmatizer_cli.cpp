#include "lemmatizer_cli.hpp"
#include "lemmatizer.hpp"
#include "tokenizer.hpp"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#ifndef _WIN32
#include <unistd.h>
#endif

namespace {
using Clock = std::chrono::steady_clock;
constexpr std::size_t max_field = 64 * 1024 * 1024;
struct Buffer {
    char* data = nullptr; std::size_t size = 0, capacity = 0;
    ~Buffer() { std::free(data); }
    void reserve(std::size_t n) {
        if (n > max_field) throw "Field size limit exceeded";
        if (n > capacity) {
            void* p=std::realloc(data,n ? n : 1);
            if (!p) throw "Input allocation failed";
            data=static_cast<char*>(p); capacity=n;
        }
        size=n;
    }
};
struct File {
    FILE* stream=nullptr;
    ~File() { if (stream) std::fclose(stream); }
};
struct Output {
    FILE* stream=nullptr;
    const char* target=nullptr;
    char partial[4096]{};
    bool owned=false;
    ~Output() { if (owned) { if (stream) std::fclose(stream); std::remove(partial); } }
    void open(const char* path) {
        if (std::strlen(path)+6>=sizeof(partial)) throw "Output path too long";
        FILE* existing=std::fopen(path,"rb");
        if (existing) { std::fclose(existing); throw "Output already exists"; }
        std::snprintf(partial,sizeof(partial),"%s.part",path);
        stream=std::fopen(partial,"wbx");
        if (!stream) throw "Cannot exclusively create partial output";
        owned=true; target=path;
    }
    void commit() {
        if (!owned) return;
        const int closed=std::fclose(stream); stream=nullptr;
        if (closed) throw "Cannot close output";
#ifdef _WIN32
        // Windows C rename does not replace an existing destination.
        if (std::rename(partial,target)) throw "Cannot publish output without overwrite";
#else
        // link() fails if the destination exists; rename() could overwrite it.
        if (::link(partial,target)) throw "Cannot publish output without overwrite";
        std::remove(partial);
#endif
        owned=false;
    }
};
struct Counts {
    std::uint64_t documents=0, bytes=0, tokens=0, known=0, changed=0, ambiguous=0, lemmas=0;
    double processing_ms=0;
};
constexpr const char* demo_text = u8"Игроки играли в игры. Люди, ёлки, стали. Games were better. "
    u8"C++ C# PS5 3.0.1. Джозеф Джозефа Джозефу Джо.";
void cell(FILE* output, const char* value, std::size_t size, std::size_t width) {
    std::fwrite(value,1,size,output);
    std::size_t characters=0;
    for (std::size_t i=0;i<size;++i)
        characters+=(static_cast<unsigned char>(value[i])&0xc0)!=0x80;
    for (std::size_t i=characters;i<width;++i) std::fputc(' ',output);
    std::fputs("  ",output);
}
void table_header(FILE* output) {
    cell(output,u8"№",std::strlen(u8"№"),4);
    cell(output,u8"Исходное",std::strlen(u8"Исходное"),20);
    cell(output,u8"Токен",std::strlen(u8"Токен"),20);
    cell(output,u8"Словарь",std::strlen(u8"Словарь"),7);
    std::fputs(u8"Лемма или леммы\n",output);
}
void exact(FILE* f, char* p, std::size_t n) {
    if (n && std::fread(p,1,n,f)!=n) throw "Truncated corpus";
}
std::uint64_t integer(FILE* f, unsigned bytes) {
    unsigned char p[8]{}; exact(f,reinterpret_cast<char*>(p),bytes);
    std::uint64_t n=0; for (unsigned i=0;i<bytes;++i) n|=std::uint64_t(p[i])<<(i*8);
    return n;
}
void field(FILE* f, Buffer& b) {
    const auto n=integer(f,8); if (n>max_field) throw "Field size limit exceeded";
    b.reserve(static_cast<std::size_t>(n)); exact(f,b.data,b.size);
}
void process(gaming::Lemmatizer& lemma, const Buffer& text, std::uint64_t id,
             const char* field_name, Counts& stats, FILE* output, bool table=false) {
    const auto start=Clock::now(); stats.bytes+=text.size;
    gaming::tokenize(std::string_view(text.data ? text.data : "",text.size),[&](const gaming::Token& token) {
        gaming::LemmaResult result;
        if (!lemma.analyze(token.term.data(),token.term.size(),result)) throw "Too many lemma alternatives";
        ++stats.tokens; stats.known+=result.known; stats.ambiguous+=result.count>1;
        stats.lemmas+=result.count;
        bool changed=false;
        for (std::size_t i=0;i<result.count;++i)
            if (result.values[i].size!=token.term.size() ||
                std::memcmp(result.values[i].data,token.term.data(),token.term.size())) changed=true;
        stats.changed+=changed;
        if (output) {
            if (table) {
                char position[32];
                std::snprintf(position,sizeof(position),"%llu",static_cast<unsigned long long>(token.position));
                cell(output,position,std::strlen(position),4);
                cell(output,text.data+token.begin_byte,token.end_byte-token.begin_byte,20);
                cell(output,token.term.data(),token.term.size(),20);
                const char* known=result.known ? u8"да" : u8"нет";
                cell(output,known,std::strlen(known),7);
                for (std::size_t i=0;i<result.count;++i) {
                    if (i) std::fputs(" | ",output);
                    std::fwrite(result.values[i].data,1,result.values[i].size,output);
                }
                std::fputc('\n',output);
                return;
            }
            std::fprintf(output,"%llu\t%s\t%llu\t%zu\t%zu\t",static_cast<unsigned long long>(id),field_name,
                         static_cast<unsigned long long>(token.position),token.begin_byte,token.end_byte);
            std::fwrite(token.term.data(),1,token.term.size(),output); std::fputc('\t',output);
            for (std::size_t i=0;i<result.count;++i) {
                if (i) std::fputc('|',output);
                std::fwrite(result.values[i].data,1,result.values[i].size,output);
            }
            std::fprintf(output,"\t%d\n",int(result.known));
        }
    });
    stats.processing_ms+=std::chrono::duration<double,std::milli>(Clock::now()-start).count();
}
std::uint64_t positive(const char* p) {
    if (!*p) throw "Expected positive --limit";
    std::uint64_t n=0;
    for (;*p;++p) {
        if (*p<'0' || *p>'9' || n>(UINT64_MAX-unsigned(*p-'0'))/10) throw "Invalid --limit";
        n=n*10+unsigned(*p-'0');
    }
    if (!n) throw "Expected positive --limit";
    return n;
}
void json(FILE* f, const Counts& s, const gaming::Lemmatizer& dictionary, double load, double elapsed) {
    std::fprintf(f,"{\"rules_version\":\"lemma-v1\",\"tokenizer_version\":\"gaming-v3\","
        "\"documents\":%llu,\"input_utf8_bytes\":%llu,\"tokens\":%llu,\"known_tokens\":%llu,"
        "\"changed_tokens\":%llu,\"ambiguous_tokens\":%llu,\"emitted_lemmas\":%llu,"
        "\"dictionary_entries\":%zu,\"dictionary_memory_bytes\":%zu,\"load_ms\":%.6f,"
        "\"processing_ms\":%.6f,\"elapsed_ms\":%.6f}\n",
        static_cast<unsigned long long>(s.documents),static_cast<unsigned long long>(s.bytes),
        static_cast<unsigned long long>(s.tokens),static_cast<unsigned long long>(s.known),
        static_cast<unsigned long long>(s.changed),static_cast<unsigned long long>(s.ambiguous),
        static_cast<unsigned long long>(s.lemmas),dictionary.entries(),dictionary.memory_bytes(),
        load,s.processing_ms,elapsed);
}
}

int gaming::run_lemmatizer(int argc, const char* const* argv, FILE* input, FILE* output, FILE* errors) {
    try {
        if (argc==2 && !std::strcmp(argv[1],"--help")) {
            std::fputs("lemmatizer text --dict FILE [--input UTF8_FILE] [--output TSV] [--stats JSON]\n"
                       "lemmatizer demo --dict FILE [--input UTF8_FILE] [--output TABLE] [--stats JSON]\n"
                       "lemmatizer corpus SNAPSHOT.bin --dict FILE [--limit N] [--output TSV] [--stats JSON]\n"
                       "demo: live token-to-lemmas table; built-in examples unless --input is given.\n"
                       "--no-output disables TSV. Unknown tokens survive; alternatives are joined by |.\n"
                       "UTF-8; original token positions and byte offsets are preserved.\n",output); return 0;
        }
        if (argc<2) throw "Missing mode; use --help";
        const bool corpus=!std::strcmp(argv[1],"corpus"), text=!std::strcmp(argv[1],"text"),
                   demo=!std::strcmp(argv[1],"demo");
        if (!corpus && !text && !demo) throw "Unknown mode";
        const char* dict_path=nullptr; const char* input_path=nullptr;
        const char* output_path=nullptr; const char* stats_path=nullptr;
        std::uint64_t limit=0; bool no_output=false;
        int index=2;
        if (corpus) { if (index==argc) throw "Missing corpus path"; input_path=argv[index++]; }
        while (index<argc) {
            const char* option=argv[index++];
            if (!std::strcmp(option,"--no-output") && !no_output) { no_output=true; continue; }
            if (index==argc) throw "Missing option value";
            const char* value=argv[index++];
            if (!std::strcmp(option,"--dict") && !dict_path) dict_path=value;
            else if (!std::strcmp(option,"--input") && (text || demo) && !input_path) input_path=value;
            else if (!std::strcmp(option,"--output") && !output_path) output_path=value;
            else if (!std::strcmp(option,"--stats") && !stats_path) stats_path=value;
            else if (!std::strcmp(option,"--limit") && corpus && !limit) limit=positive(value);
            else throw "Unknown, duplicate or inapplicable option";
        }
        if (!dict_path) throw "Missing --dict";
        if (no_output && output_path) throw "--no-output conflicts with --output";
        const auto load_start=Clock::now();
        Lemmatizer dictionary; const char* error=nullptr;
        if (!dictionary.load(dict_path,&error)) throw error;
        const double load_ms=std::chrono::duration<double,std::milli>(Clock::now()-load_start).count();
        File input_file;
        if (input_path) {
            input_file.stream=std::fopen(input_path,"rb");
            if (!input_file.stream) throw "Cannot open input";
            input=input_file.stream;
        }
        Output tokens, statistics;
        if (output_path) tokens.open(output_path);
        if (stats_path) statistics.open(stats_path);
        FILE* tsv=output_path ? tokens.stream : (((text || demo) && !no_output) ? output : nullptr);
        if (tsv) {
            if (demo) {
                std::fputs(u8"ЖИВОЙ РАЗБОР: токенизатор gaming-v3 + загруженный словарь.\n\n",tsv);
                table_header(tsv);
            } else std::fputs("document_id\tfield\tposition\tbegin_byte\tend_byte\tterm\tlemmas\tknown\n",tsv);
        }
        const auto start=Clock::now(); Counts stats; Buffer buffer;
        if (corpus) {
            char magic[8]; exact(input,magic,8);
            if (std::memcmp(magic,"MAIIR3\0\1",8)) throw "Unsupported corpus format";
            std::uint64_t previous=0;
            while (!limit || stats.documents<limit) {
                const int c=std::fgetc(input);
                if (c==EOF) { if (std::ferror(input)) throw "Cannot read corpus"; break; }
                if (std::ungetc(c,input)==EOF) throw "Cannot read corpus prefix";
                const auto id=integer(input,8);
                if (id<=previous) throw "Document IDs must increase";
                previous=id;
                const auto source_size=integer(input,4);
                if (!source_size || source_size>64) throw "Invalid source size";
                char source[64]; exact(input,source,static_cast<std::size_t>(source_size));
                for (std::size_t i=0;i<source_size;++i)
                    if (!((source[i]>='a' && source[i]<='z') ||
                        (i && ((source[i]>='0' && source[i]<='9') || source[i]=='_')))) throw "Invalid source";
                field(input,buffer); process(dictionary,buffer,id,"title",stats,tsv);
                field(input,buffer); process(dictionary,buffer,id,"text",stats,tsv);
                ++stats.documents;
            }
        } else if (demo && !input_path) {
            buffer.reserve(std::strlen(demo_text));
            std::memcpy(buffer.data,demo_text,buffer.size);
            process(dictionary,buffer,1,"text",stats,tsv,true); stats.documents=1;
        } else {
            // Geometric growth, bounded by one field; no list of all documents.
            std::size_t used=0;
            for (;;) {
                if (used==buffer.capacity) {
                    if (used==max_field) {
                        if (std::fgetc(input)!=EOF || std::ferror(input)) throw "Field size limit exceeded";
                        break;
                    }
                    const auto next=buffer.capacity ? buffer.capacity*2 : 65536;
                    buffer.reserve(next>max_field ? max_field : next);
                }
                const auto n=std::fread(buffer.data+used,1,buffer.capacity-used,input); used+=n;
                if (!n) { if (std::ferror(input)) throw "Cannot read text"; break; }
            }
            buffer.size=used; process(dictionary,buffer,1,"text",stats,tsv,demo); stats.documents=1;
        }
        if (demo && tsv) std::fputs(u8"\nСловарь: да — найден словарный разбор; нет — сохранён исходный токен.\n"
            u8"Варианты через | относятся к одной позиции; контекстный выбор не выполняется.\n"
            u8"Джозефа → джозеф — словоформы; Джозеф → Джо — сокращённое имя, не лемматизация.\n",tsv);
        if (tsv && (std::fflush(tsv) || std::ferror(tsv))) throw "Cannot write TSV";
        const double elapsed=std::chrono::duration<double,std::milli>(Clock::now()-start).count();
        FILE* summary=(text || demo) ? errors : output;
        json(summary,stats,dictionary,load_ms,elapsed);
        if (std::fflush(summary) || std::ferror(summary)) throw "Cannot write statistics";
        if (statistics.stream) {
            json(statistics.stream,stats,dictionary,load_ms,elapsed);
            if (std::ferror(statistics.stream)) throw "Cannot write statistics file";
        }
        tokens.commit(); statistics.commit();
        return 0;
    } catch (const char* error) {
        std::fprintf(errors,"[lemmatizer] %s\n",error); return 1;
    } catch (const std::exception& error) {
        std::fprintf(errors,"[lemmatizer] %s\n",error.what()); return 1;
    }
}
