#include "positional_index.hpp"
#include "tokenizer.hpp"
#include <exception>
#include <cstring>

namespace gaming {
struct PhraseQuery::State {
    struct Stream { PositionCursor cursor; std::uint64_t head=0; bool ready=false,ended=false; };
    struct Group { unsigned begin=0,count=0; std::uint64_t head=0; bool ready=false,ended=false; };
    PositionalIndex& index; TermSpace space; const Lemmatizer& dictionary;
    Stream streams[512]; Group groups[64]; unsigned stream_count=0,group_count=0;
    std::uint64_t last_document=UINT64_MAX;
    bool optimized;
    State(PositionalIndex& p,TermSpace s,const Lemmatizer& d,bool opt):index(p),space(s),dictionary(d),optimized(opt) {}
    void add(const Token& token) {
        if (group_count==64) throw "Phrase exceeds 64 tokens";
        Group& g=groups[group_count++]; g.begin=stream_count;
        LemmaResult r;
        if (space==TermSpace::lemma) {
            if (!dictionary.analyze(token.term.data(),token.term.size(),r)) throw "Too many phrase lemma alternatives";
        } else { r.count=1; r.values[0]={token.term.data(),token.term.size()}; }
        for (std::size_t i=0;i<r.count;++i) {
            if (stream_count==512) throw "Phrase exceeds 512 coordinate streams";
            PositionList list; const char* error=nullptr;
            if (!index.lookup(r.values[i].data,r.values[i].size,space,list,&error)) throw error;
            if (!list.count) continue;
            if (!streams[stream_count].cursor.open(index,list,&error)) throw error;
            ++stream_count; ++g.count;
        }
    }
    bool fill_stream(unsigned i) {
        Stream& s=streams[i]; if (s.ready) return true; if (s.ended) return false;
        const char* error=nullptr; s.ready=s.cursor.next(s.head,&error); if (error) throw error;
        s.ended=!s.ready; return s.ready;
    }
    bool fill_group(unsigned i) {
        Group& g=groups[i]; if (g.ready) return true; if (g.ended) return false;
        // Union alternative lemmas at original coordinates, then shift by phrase offset.
        for (;;) {
            std::uint64_t smallest=UINT64_MAX;
            for (unsigned j=g.begin;j<g.begin+g.count;++j) if (fill_stream(j) && streams[j].head<smallest) smallest=streams[j].head;
            if (smallest==UINT64_MAX) { g.ended=true; return false; }
            for (unsigned j=g.begin;j<g.begin+g.count;++j) if (streams[j].ready && streams[j].head==smallest) streams[j].ready=false;
            const auto position=static_cast<std::uint32_t>(smallest)&0x7fffffffU;
            if (position<i) continue;
            g.head=smallest-i; g.ready=true; return true;
        }
    }
    bool next(std::uint64_t& id) {
        for (;;) {
            std::uint64_t largest=0;
            for (unsigned i=0;i<group_count;++i) { if (!fill_group(i)) return false; if (groups[i].head>largest) largest=groups[i].head; }
            bool match=true;
            for (unsigned i=0;i<group_count;++i) {
                if (groups[i].head<largest) {
                    groups[i].ready=false; match=false;
                    if (optimized) {
                        // Shifted positions must not carry across title/body.
                        const auto position=largest&0x7fffffffU;
                        const std::uint64_t target=position+i<=0x7fffffffU ? largest+i : (largest&~std::uint64_t(0x7fffffffU))+0x80000000ULL;
                        Group& g=groups[i];
                        for (unsigned j=g.begin;j<g.begin+g.count;++j) {
                            Stream& s=streams[j];
                            if (s.ended || (s.ready && s.head>=target)) continue;
                            const char* error=nullptr; s.ready=s.cursor.advance(target,s.head,&error);
                            if (error) throw error;
                            s.ended=!s.ready;
                        }
                    }
                }
            }
            if (!match) continue;
            for (unsigned i=0;i<group_count;++i) groups[i].ready=false;
            const auto ordinal=largest>>32;
            if (ordinal==last_document) continue;
            last_document=ordinal; DocumentInfo d; const char* error=nullptr;
            if (!index.documents().document_at(ordinal,d,&error)) throw error;
            if (!d.id) throw "Phrase coordinate references missing document";
            id=d.id; return true;
        }
    }
};
PhraseQuery::~PhraseQuery() { delete state_; }
bool PhraseQuery::compile(PositionalIndex& index,TermSpace space,const Lemmatizer& dictionary,const char* phrase,std::size_t size,const char** error,bool optimized) {
    *error=nullptr; delete state_; state_=nullptr;
    try {
        if (!index.available()) throw "Phrase search requires --positions DIR";
        if (!phrase || size>8192) throw "Invalid phrase length";
        state_=new State(index,space,dictionary,optimized);
        tokenize(std::string_view(phrase,size),[&](const Token& token){state_->add(token);});
        if (!state_->group_count) throw "Phrase contains no searchable tokens";
        return true;
    } catch (const char* e) { *error=e; }
      catch (const std::exception&) { *error="Cannot tokenize phrase or allocate memory"; }
    delete state_; state_=nullptr; return false;
}
bool PhraseQuery::next(std::uint64_t& id,const char** error) {
    *error=nullptr; id=0;
    try { if (!state_) throw "Phrase not compiled"; return state_->next(id); }
    catch (const char* e) { *error=e; return false; }
}
bool PhraseQuery::advance(std::uint64_t target,std::uint64_t& id,const char** error) {
    while (next(id,error)) if (id>=target) return true;
    return false;
}
}
