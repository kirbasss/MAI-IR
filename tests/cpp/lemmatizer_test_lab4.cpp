#include "lemmatizer.hpp"
#include "lemmatizer_cli.hpp"
#include "lemma_search.hpp"
#include <gtest/gtest.h>
#include <nlohmann/json.hpp>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <map>
#include <sstream>
#include <string>
#include <vector>
#include <tuple>

namespace {
using Bytes = std::vector<unsigned char>;
void number(Bytes& b, std::uint64_t n, unsigned bytes) {
    for (unsigned i=0;i<bytes;++i) b.push_back(static_cast<unsigned char>(n>>(i*8)));
}
std::uint64_t fnv(const Bytes& bytes) {
    std::uint64_t h=14695981039346656037ULL;
    for (auto c:bytes) { h^=c; h*=1099511628211ULL; }
    return h;
}
using Pairs = std::map<std::string,std::vector<std::string>>;
Bytes dictionary_bytes(const Pairs& pairs) {
    Bytes records, strings;
    for (const auto& [key,lemmas]:pairs) {
        const auto key_offset=strings.size(); strings.insert(strings.end(),key.begin(),key.end());
        const auto value_offset=strings.size();
        for (const auto& lemma:lemmas) { strings.insert(strings.end(),lemma.begin(),lemma.end()); strings.push_back(0); }
        number(records,key_offset,4); number(records,key.size(),4);
        number(records,value_offset,4); number(records,strings.size()-value_offset,4);
    }
    records.insert(records.end(),strings.begin(),strings.end());
    Bytes out={'M','A','I','I','L','4',0,1};
    number(out,pairs.size(),8); number(out,strings.size(),8); number(out,fnv(records),8);
    out.insert(out.end(),records.begin(),records.end()); return out;
}
Pairs sample() {
    return {{u8"ru:игра",{u8"игра"}}, {u8"ru:игры",{u8"игра"}},
            {u8"ru:играми",{u8"игра"}}, {u8"ru:стали",{u8"сталь",u8"стать"}},
            {u8"ru:ёлки",{u8"ёлка"}}, {"n:game",{"game"}}, {"n:child",{"child"}},
            {"n:saw",{"saw"}}, {"v:see",{"see"}}, {"v:play",{"play"}},
            {"v:make",{"make"}}, {"a:good",{"good"}}, {"a:fast",{"fast"}},
            {"x:n:children",{"child"}}, {"x:v:saw",{"see"}}, {"x:a:better",{"good"}}};
}
struct Fixture : testing::Test {
    std::filesystem::path directory;
    gaming::Lemmatizer lemma;
    void SetUp() override {
        const auto suffix=std::chrono::steady_clock::now().time_since_epoch().count();
        directory=std::filesystem::temp_directory_path()/ ("mai-lemma-"+std::to_string(suffix));
        ASSERT_TRUE(std::filesystem::create_directory(directory));
        save("dict.bin",dictionary_bytes(sample()));
        const char* error=nullptr;
        ASSERT_TRUE(lemma.load((directory/"dict.bin").string().c_str(),&error)) << (error?error:"");
    }
    void TearDown() override { std::filesystem::remove_all(directory); }
    void save(const char* name,const Bytes& bytes) {
        std::ofstream f(directory/name,std::ios::binary); f.write(reinterpret_cast<const char*>(bytes.data()),bytes.size());
    }
    std::vector<std::string> analyze(const std::string& word, bool* known=nullptr) {
        gaming::LemmaResult r;
        EXPECT_TRUE(lemma.analyze(word.data(),word.size(),r));
        if (known) *known=r.known;
        std::vector<std::string> out;
        for (std::size_t i=0;i<r.count;++i) out.emplace_back(r.values[i].data,r.values[i].size);
        return out;
    }
    bool reload(const Bytes& bytes) {
        save("bad.bin",bytes); const char* error=nullptr;
        const bool ok=lemma.load((directory/"bad.bin").string().c_str(),&error);
        if (!ok) { EXPECT_NE(error,nullptr); }
        return ok;
    }
};
TEST_F(Fixture,RussianInflectionsAndLemmaAreEquivalent) {
    for (const auto& s:{u8"игра",u8"игры",u8"играми"})
        EXPECT_EQ(analyze(s),std::vector<std::string>{u8"игра"});
}
TEST_F(Fixture,AmbiguityIsRetainedNotArbitrarilyResolved) {
    EXPECT_EQ(analyze(u8"стали"),(std::vector<std::string>{u8"сталь",u8"стать"}));
}
TEST_F(Fixture,YoIsPreservedNotConflatedWithE) {
    EXPECT_EQ(analyze(u8"ёлки"),std::vector<std::string>{u8"ёлка"});
    bool known=true; EXPECT_EQ(analyze(u8"елки",&known),std::vector<std::string>{u8"елки"}); EXPECT_FALSE(known);
}
TEST_F(Fixture,EnglishRegularFormsAreDictionaryValidated) {
    EXPECT_EQ(analyze("games"),std::vector<std::string>{"game"});
    EXPECT_EQ(analyze("playing"),std::vector<std::string>{"play"});
    EXPECT_EQ(analyze("played"),std::vector<std::string>{"play"});
    EXPECT_EQ(analyze("making"),std::vector<std::string>{"make"});
    EXPECT_EQ(analyze("fastest"),std::vector<std::string>{"fast"});
    bool known=true; EXPECT_EQ(analyze("xyzings",&known),std::vector<std::string>{"xyzings"}); EXPECT_FALSE(known);
}
TEST_F(Fixture,EnglishExceptionsAndPosAmbiguity) {
    EXPECT_EQ(analyze("children"),std::vector<std::string>{"child"});
    EXPECT_EQ(analyze("better"),std::vector<std::string>{"good"});
    EXPECT_EQ(analyze("saw"),(std::vector<std::string>{"saw","see"}));
}
TEST_F(Fixture,SpecialAndUnknownTokensSurvive) {
    for (const std::string s:{"c++","c#","3.0.1","ps5","half-life","baldur's","stalker",u8"киберпанк",u8"gameигра"}) {
        bool known=true; EXPECT_EQ(analyze(s,&known),std::vector<std::string>{s}); EXPECT_FALSE(known);
    }
}
TEST_F(Fixture,ProperNameInflectionsDoNotInventNicknameSynonyms) {
    auto pairs=sample();
    pairs[u8"ru:джозеф"]={u8"джозеф"};
    pairs[u8"ru:джозефа"]={u8"джозеф"};
    pairs[u8"ru:джозефу"]={u8"джозеф"};
    pairs[u8"ru:джо"]={u8"джо"};
    ASSERT_TRUE(reload(dictionary_bytes(pairs)));
    for (const auto& word:{u8"джозеф",u8"джозефа",u8"джозефу"}) {
        bool known=false;
        EXPECT_EQ(analyze(word,&known),std::vector<std::string>{u8"джозеф"});
        EXPECT_TRUE(known);
    }
    EXPECT_EQ(analyze(u8"джо"),std::vector<std::string>{u8"джо"});
}
TEST_F(Fixture,UnknownLongTokenHasNoTruncation) {
    const std::string s(5000,'q'); EXPECT_EQ(analyze(s),std::vector<std::string>{s});
}
TEST_F(Fixture,EmptyInputHasNoLemma) {
    EXPECT_TRUE(analyze("").empty());
}
TEST_F(Fixture,LoadedDictionaryIsReusableAndViewStable) {
    gaming::LemmaResult r; ASSERT_TRUE(lemma.analyze(u8"игры",std::strlen(u8"игры"),r));
    const auto* p=r.values[0].data;
    for (int i=0;i<10000;++i) EXPECT_EQ(analyze("playing"),std::vector<std::string>{"play"});
    EXPECT_EQ(std::string(p,r.values[0].size),u8"игра"); EXPECT_GT(lemma.memory_bytes(),0u);
}
TEST_F(Fixture,HashCollisionsStillFindEveryKey) {
    auto pairs=sample();
    std::vector<std::string> words;
    for (int i=0;i<1000;++i) {
        std::string word="key";
        word.push_back(static_cast<char>('a'+i%26));
        word.push_back(static_cast<char>('a'+(i/26)%26));
        word.push_back(static_cast<char>('a'+i/(26*26)));
        words.push_back(word); pairs["n:"+word]={"game"};
    }
    ASSERT_TRUE(reload(dictionary_bytes(pairs)));
    for (const auto& word:words) EXPECT_EQ(analyze(word),std::vector<std::string>{"game"});
    EXPECT_EQ(analyze(u8"играми"),std::vector<std::string>{u8"игра"});
    EXPECT_EQ(analyze("played"),std::vector<std::string>{"play"});
}
TEST_F(Fixture,FailureToLoadLeavesSafeUnloadedState) {
    const char* error=nullptr;
    EXPECT_FALSE(lemma.load((directory/"absent.bin").string().c_str(),&error));
    bool known=true; EXPECT_EQ(analyze("playing",&known),std::vector<std::string>{"playing"});
    EXPECT_FALSE(known); EXPECT_EQ(lemma.entries(),0u); EXPECT_EQ(lemma.memory_bytes(),0u);
}
TEST_F(Fixture,TruncatedExtendedAndBadChecksumRejected) {
    auto bytes=dictionary_bytes(sample()); bytes.pop_back(); EXPECT_FALSE(reload(bytes));
    bytes=dictionary_bytes(sample()); bytes.push_back(0); EXPECT_FALSE(reload(bytes));
    bytes=dictionary_bytes(sample()); bytes.back()^=1; EXPECT_FALSE(reload(bytes));
}
TEST_F(Fixture,WrongHeaderAndHostileSizeRejected) {
    auto bytes=dictionary_bytes(sample()); bytes[0]='X'; EXPECT_FALSE(reload(bytes));
    bytes=dictionary_bytes(sample()); for (int i=8;i<16;++i) bytes[i]=255; EXPECT_FALSE(reload(bytes));
}
TEST_F(Fixture,InvalidUtf8EmptyLemmaAndTooManyAlternativesRejected) {
    EXPECT_FALSE(reload(dictionary_bytes({{"ru:a",{""}}})));
    EXPECT_FALSE(reload(dictionary_bytes({{"ru:a",{std::string("\xc0\x80",2)}}})));
    std::vector<std::string> values; for (int i=0;i<65;++i) values.push_back("x"+std::to_string(i));
    EXPECT_FALSE(reload(dictionary_bytes({{"ru:a",values}})));
}

std::string contents(FILE* f) {
    std::fflush(f); std::rewind(f); std::string s; char buffer[4096];
    while (const auto n=std::fread(buffer,1,sizeof(buffer),f)) s.append(buffer,n);
    return s;
}
struct Streams {
    FILE *in=std::tmpfile(), *out=std::tmpfile(), *err=std::tmpfile();
    ~Streams() { if (in) std::fclose(in); if (out) std::fclose(out); if (err) std::fclose(err); }
};
int run(std::vector<std::string> args,Streams& s) {
    std::vector<const char*> argv; for (const auto& a:args) argv.push_back(a.c_str());
    return gaming::run_lemmatizer(static_cast<int>(argv.size()),argv.data(),s.in,s.out,s.err);
}
TEST_F(Fixture,CliRealTokenizerPreservesUtf8OffsetsAndAlternatives) {
    Streams s; const std::string text=u8"Игры стали C++ 3.0.1 S.T.A.L.K.E.R.";
    std::fwrite(text.data(),1,text.size(),s.in); std::rewind(s.in);
    ASSERT_EQ(run({"lemma","text","--dict",(directory/"dict.bin").string()},s),0) << contents(s.err);
    const auto out=contents(s.out);
    EXPECT_NE(out.find(u8"1\ttext\t0\t0\t8\tигры\tигра\t1\n"),std::string::npos);
    EXPECT_NE(out.find(u8"стали\tсталь|стать\t1"),std::string::npos);
    EXPECT_NE(out.find("c++\tc++\t0"),std::string::npos);
    EXPECT_NE(out.find("3.0.1\t3.0.1\t0"),std::string::npos);
    EXPECT_NE(out.find("stalker\tstalker\t0"),std::string::npos);
    auto stats=nlohmann::json::parse(contents(s.err));
    EXPECT_EQ(stats["tokens"],5); EXPECT_EQ(stats["known_tokens"],2); EXPECT_EQ(stats["ambiguous_tokens"],1);
}
TEST_F(Fixture,DemoUsesLiveDictionaryAndShowsAllAlternativesAndFallback) {
    Streams s;
    ASSERT_EQ(run({"lemma","demo","--dict",(directory/"dict.bin").string()},s),0) << contents(s.err);
    const auto out=contents(s.out);
    EXPECT_NE(out.find(u8"Исходное"),std::string::npos);
    EXPECT_NE(out.find(u8"Токен"),std::string::npos);
    EXPECT_NE(out.find(u8"сталь | стать"),std::string::npos);
    EXPECT_NE(out.find(u8"ёлка"),std::string::npos);
    EXPECT_NE(out.find("c++"),std::string::npos);
    // The tiny test dictionary does not know "игроки": no canned demo answers.
    const auto start=out.find(u8"Игроки");
    ASSERT_NE(start,std::string::npos);
    const auto row=out.substr(start,out.find('\n',start)-start);
    EXPECT_NE(row.find(u8"игроки"),std::string::npos);
    EXPECT_NE(row.find(u8"нет"),std::string::npos);
    EXPECT_EQ(out.find("{\"rules_version\""),std::string::npos);
    const auto stats=nlohmann::json::parse(contents(s.err));
    EXPECT_EQ(stats["tokens"],18);
    EXPECT_EQ(stats["ambiguous_tokens"],1);
}
TEST_F(Fixture,DemoCustomUtf8InputAndOutputUseTheSameAnalysisAsTsv) {
    const std::string text=u8"Игры стали PS5";
    save("input.txt",Bytes(text.begin(),text.end()));
    const auto dict=(directory/"dict.bin").string();
    const auto input=(directory/"input.txt").string();
    const auto output=(directory/"table.txt").string();
    Streams table,tsv;
    ASSERT_EQ(run({"lemma","demo","--dict",dict,"--input",input,"--output",output},table),0);
    ASSERT_EQ(run({"lemma","text","--dict",dict,"--input",input},tsv),0);
    EXPECT_TRUE(contents(table.out).empty());
    std::ifstream file(output,std::ios::binary);
    const std::string rendered((std::istreambuf_iterator<char>(file)),std::istreambuf_iterator<char>());
    EXPECT_NE(rendered.find(u8"сталь | стать"),std::string::npos);
    EXPECT_EQ(rendered.find(u8"Люди"),std::string::npos);
    const auto a=nlohmann::json::parse(contents(table.err)), b=nlohmann::json::parse(contents(tsv.err));
    for (const char* key:{"tokens","known_tokens","changed_tokens","ambiguous_tokens","emitted_lemmas","input_utf8_bytes"})
        EXPECT_EQ(a[key],b[key]) << key;
}
Bytes corpus() {
    Bytes out={'M','A','I','I','R','3',0,1};
    for (int id=1;id<=2;++id) {
        number(out,id,8); number(out,4,4); for (char c:std::string("test")) out.push_back(c);
        const std::string title="Games", text=id==1?u8"играми":"";
        for (const auto& value:{title,text}) { number(out,value.size(),8); out.insert(out.end(),value.begin(),value.end()); }
    }
    return out;
}
TEST_F(Fixture,CliCorpusStreamsFieldsTitleOnlyAndLimit) {
    save("corpus.bin",corpus()); Streams s;
    ASSERT_EQ(run({"lemma","corpus",(directory/"corpus.bin").string(),"--dict",(directory/"dict.bin").string(),"--limit","1"},s),0);
    const auto stats=nlohmann::json::parse(contents(s.out));
    EXPECT_EQ(stats["documents"],1); EXPECT_EQ(stats["tokens"],2);
    EXPECT_EQ(stats["changed_tokens"],2);
}
TEST_F(Fixture,CliErrorsDoNotPublishPartialOutputOrOverwrite) {
    save("corrupt.bin",{'b','a','d'}); Streams s;
    const auto output=(directory/"out.tsv").string();
    EXPECT_NE(run({"lemma","corpus",(directory/"corrupt.bin").string(),"--dict",(directory/"dict.bin").string(),"--output",output},s),0);
    EXPECT_FALSE(std::filesystem::exists(output)); EXPECT_FALSE(std::filesystem::exists(output+".part"));
    save("out.tsv",{'k','e','e','p'}); Streams s2;
    EXPECT_NE(run({"lemma","text","--dict",(directory/"dict.bin").string(),"--output",output},s2),0);
    std::ifstream f(output); std::string value; f>>value; EXPECT_EQ(value,"keep");
}
TEST_F(Fixture,CliMalformedUtf8RejectsAndRemovesPartial) {
    Streams s; const unsigned char bad[]={0xc0,0x80}; std::fwrite(bad,1,2,s.in); std::rewind(s.in);
    const auto output=(directory/"out.tsv").string();
    EXPECT_NE(run({"lemma","text","--dict",(directory/"dict.bin").string(),"--output",output},s),0);
    EXPECT_FALSE(std::filesystem::exists(output)); EXPECT_FALSE(std::filesystem::exists(output+".part"));
}
TEST_F(Fixture,CliArgumentErrors) {
    for (const auto& args:std::vector<std::vector<std::string>>{
        {"lemma"},{"lemma","unknown"},{"lemma","text"},
        {"lemma","corpus","file","--limit","0"},
        {"lemma","text","--dict","x","--no-output","--output","file"},
        {"lemma","text","--dict","x","--unknown","x"}}) {
        Streams s; EXPECT_NE(run(args,s),0);
    }
}
TEST_F(Fixture,CliNoOutputHasStatisticsButNoTsv) {
    Streams s; std::fputs("playing",s.in); std::rewind(s.in);
    const auto stats_path=(directory/"stats.json").string();
    ASSERT_EQ(run({"lemma","text","--dict",(directory/"dict.bin").string(),"--no-output","--stats",stats_path},s),0);
    EXPECT_TRUE(contents(s.out).empty());
    std::ifstream f(stats_path); nlohmann::json stats; f>>stats; EXPECT_EQ(stats["changed_tokens"],1);
}

Bytes search_corpus(const std::vector<std::tuple<unsigned,std::string,std::string>>& rows) {
    Bytes bytes={'M','A','I','I','R','3',0,1};
    for (const auto& [id,title,text]:rows) {
        number(bytes,id,8); number(bytes,4,4);
        for (char c:std::string("test")) bytes.push_back(c);
        for (const auto& value:{title,text}) { number(bytes,value.size(),8); bytes.insert(bytes.end(),value.begin(),value.end()); }
    }
    return bytes;
}
struct SearchOutput { int code; std::string out,err; };
SearchOutput search(const std::filesystem::path& dir,const std::string& query,const std::string& field="title",const std::string& k="5") {
    std::ofstream(dir/"queries.tsv",std::ios::binary)<<query;
    Streams streams;
    std::vector<std::string> args={"lemma_search","--corpus",(dir/"search.bin").string(),
        "--dict",(dir/"dict.bin").string(),"--queries",(dir/"queries.tsv").string(),"--field",field,"--top-k",k};
    std::vector<const char*> argv; for (const auto& a:args) argv.push_back(a.c_str());
    const auto code=gaming::run_lemma_search(static_cast<int>(argv.size()),argv.data(),streams.out,streams.err);
    return {code,contents(streams.out),contents(streams.err)};
}
std::vector<unsigned> ranking(const std::string& text,const std::string& query,const std::string& mode) {
    std::istringstream stream(text); std::string line; std::getline(stream,line);
    std::vector<unsigned> ids;
    while (std::getline(stream,line)) {
        std::istringstream row(line); std::string q,m,rank,id;
        std::getline(row,q,'\t'); std::getline(row,m,'\t'); std::getline(row,rank,'\t'); std::getline(row,id,'\t');
        if (q==query && m==mode) ids.push_back(static_cast<unsigned>(std::stoul(id)));
    }
    return ids;
}
TEST_F(Fixture,SearchShowsAmbiguityRegressionAndExactBonusRecovery) {
    auto pairs=sample(); pairs["n:window"]={"window"}; save("dict.bin",dictionary_bytes(pairs));
    save("search.bin",search_corpus({{1,"release window",""},{2,"Windows 10",""}}));
    const auto result=search(directory,"q\tWindows\n"); ASSERT_EQ(result.code,0)<<result.err;
    EXPECT_EQ(ranking(result.out,"q","exact"),std::vector<unsigned>{2});
    EXPECT_EQ(ranking(result.out,"q","lemma"),(std::vector<unsigned>{1,2}));
    EXPECT_EQ(ranking(result.out,"q","hybrid"),(std::vector<unsigned>{2,1}));
}
TEST_F(Fixture,SearchAndSemanticsRepeatedTermsAndFieldSelection) {
    save("search.bin",search_corpus({{1,"game","children"},{2,"games children",""},{3,"children",""}}));
    auto result=search(directory,"q\tgames games children\n","title"); ASSERT_EQ(result.code,0)<<result.err;
    EXPECT_EQ(ranking(result.out,"q","lemma"),std::vector<unsigned>{2});
    result=search(directory,"q\tgames children\n","both"); ASSERT_EQ(result.code,0)<<result.err;
    EXPECT_EQ(ranking(result.out,"q","lemma"),(std::vector<unsigned>{2,1}));
    EXPECT_EQ(ranking(result.out,"q","exact"),std::vector<unsigned>{2});
}
TEST_F(Fixture,SearchUnknownQueryViewsAndProtectedTermsRemainValid) {
    save("search.bin",search_corpus({{1,"PS5 C++ 3.0.1",""},{2,"PS5 C# 3.0.1",""}}));
    const auto result=search(directory,"q\tPS5 C++ 3.0.1\n"); ASSERT_EQ(result.code,0)<<result.err;
    for (const auto& mode:{"exact","lemma","hybrid"}) EXPECT_EQ(ranking(result.out,"q",mode),std::vector<unsigned>{1});
}
TEST_F(Fixture,SearchTopKAndDocumentIdTiesAreDeterministic) {
    save("search.bin",search_corpus({{1,"game",""},{2,"game",""},{3,"game",""}}));
    const auto result=search(directory,"q\tgame\n","title","2"); ASSERT_EQ(result.code,0)<<result.err;
    EXPECT_EQ(ranking(result.out,"q","lemma"),(std::vector<unsigned>{1,2}));
}
TEST_F(Fixture,SearchRejectsBadQueryFilesAndOptions) {
    save("search.bin",search_corpus({{1,"game",""}}));
    for (const std::string query:{"", "q\t\n", "q\tgame\nq\tchild\n", "q\tgame\textra\n", "bad id\tgame\n"}) {
        const auto result=search(directory,query); EXPECT_NE(result.code,0); EXPECT_TRUE(result.out.empty());
    }
    EXPECT_NE(search(directory,"q\tgame\n","invalid").code,0);
    EXPECT_NE(search(directory,"q\tgame\n","title","101").code,0);
}
TEST_F(Fixture,SearchMalformedCorpusNeverPublishesPartialRankings) {
    auto bytes=search_corpus({{1,"game",""},{2,"game",""}}); bytes.pop_back(); save("search.bin",bytes);
    auto result=search(directory,"q\tgame\n"); EXPECT_NE(result.code,0); EXPECT_TRUE(result.out.empty());
    save("search.bin",search_corpus({{2,"game",""},{1,"game",""}}));
    result=search(directory,"q\tgame\n"); EXPECT_NE(result.code,0); EXPECT_TRUE(result.out.empty());
}
TEST_F(Fixture,SearchNoMatchDoesNotInventResults) {
    save("search.bin",search_corpus({{1,"game",""}}));
    const auto result=search(directory,"q\tunknown\n"); ASSERT_EQ(result.code,0)<<result.err;
    EXPECT_TRUE(ranking(result.out,"q","lemma").empty());
    EXPECT_NE(result.err.find("matches=0"),std::string::npos);
}
} // namespace
