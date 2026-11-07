#include "boolean_search.hpp"
#include <gtest/gtest.h>
#include <nlohmann/json.hpp>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <tuple>
#include <vector>

namespace {
namespace fs=std::filesystem;
using U64=std::uint64_t;
void number(std::ostream& f,U64 n,unsigned width=8) { for (unsigned i=0;i<width;++i) f.put(static_cast<char>(n>>(i*8))); }
std::string read(const fs::path& p) { std::ifstream f(p,std::ios::binary); return {std::istreambuf_iterator<char>(f),std::istreambuf_iterator<char>()}; }
void dictionary(const fs::path& path) {
    const std::map<std::string,std::vector<std::string>> pairs={
        {u8"ru:игры",{u8"игра"}},{u8"ru:игрой",{u8"игра"}},{u8"ru:стали",{u8"сталь",u8"стать"}}};
    std::ostringstream records,strings;
    for (const auto& [key,values]:pairs) {
        const auto ko=strings.str().size(); strings<<key; const auto vo=strings.str().size();
        for (const auto& v:values) { strings<<v; strings.put(0); }
        number(records,ko,4); number(records,key.size(),4); number(records,vo,4); number(records,strings.str().size()-vo,4);
    }
    const auto payload=records.str()+strings.str(); U64 hash=14695981039346656037ULL;
    for (unsigned char c:payload) { hash^=c; hash*=1099511628211ULL; }
    std::ofstream f(path,std::ios::binary); f.write("MAIIL4\0\1",8);
    number(f,pairs.size()); number(f,strings.str().size()); number(f,hash); f<<payload;
}
using Docs=std::vector<std::tuple<U64,std::string,std::string>>;
struct Phrases:testing::Test {
    fs::path root;
    void corpus(const Docs& docs) {
        std::ofstream f(root/"corpus.bin",std::ios::binary); f.write("MAIIR3\0\1",8);
        for (const auto& [id,title,body]:docs) { number(f,id); number(f,4,4); f<<"test"; number(f,title.size()); f<<title; number(f,body.size()); f<<body; }
    }
    std::pair<int,std::string> run(std::vector<std::string> args,bool positional) {
        args.insert(args.begin(),positional ? "positions" : "boolean_index"); std::vector<const char*> argv;
        for (const auto& a:args) argv.push_back(a.c_str());
        FILE *out=std::tmpfile(),*err=std::tmpfile();
        const int result=positional ? gaming::run_positional_index(static_cast<int>(argv.size()),argv.data(),out,err) :
                                    gaming::run_boolean_index(static_cast<int>(argv.size()),argv.data(),out,err);
        std::rewind(out); std::string text; char block[4096]; std::size_t n;
        while ((n=std::fread(block,1,sizeof(block),out))) text.append(block,n);
        std::fclose(out); std::fclose(err); return {result,text};
    }
    int boolean(const char* name="index",bool lemma=true) {
        std::vector<std::string> args={"build","--corpus",(root/"corpus.bin").string(),"--output",(root/name).string()};
        if (lemma) args.insert(args.end(),{"--dict",(root/"dict.bin").string()});
        return run(args,false).first;
    }
    std::pair<int,std::string> positions(const char* name="positions",unsigned memory=1,bool lemma=true) {
        std::vector<std::string> args={"build","--corpus",(root/"corpus.bin").string(),"--index",(root/"index").string(),"--output",(root/name).string(),"--memory-mib",std::to_string(memory)};
        if (lemma) args.insert(args.end(),{"--dict",(root/"dict.bin").string()});
        return run(args,true);
    }
    void SetUp() override {
        root=fs::temp_directory_path()/("mai-phrases-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        ASSERT_TRUE(fs::create_directory(root)); dictionary(root/"dict.bin");
        corpus({{3,"Doom","Eternal game"},{8,"Doom Eternal","red blue red red blue"},
                {42,"",u8"doom amazing eternal blue red игры стали"},
                {100,"doom eternal",u8"игрой сталь"},
                {5000000000ULL,"",u8"doom eternal doom eternal game Игры стать"}});
        ASSERT_EQ(boolean(),0); ASSERT_EQ(positions().first,0);
    }
    void TearDown() override { fs::remove_all(root); }
    std::vector<U64> query(const std::string& text,bool lemma=false) {
        gaming::BooleanSearch engine; const char* error=nullptr;
        EXPECT_TRUE(engine.open((root/"index").string().c_str(),lemma ? gaming::TermSpace::lemma : gaming::TermSpace::surface,
                                (root/"dict.bin").string().c_str(),&error,(root/"positions").string().c_str()))<<(error ? error : "");
        gaming::BooleanQuery q; EXPECT_TRUE(q.compile(engine,text.c_str(),&error))<<(error ? error : "");
        std::vector<U64> result; U64 id; while (q.next(id,&error)) result.push_back(id); EXPECT_EQ(error,nullptr); return result;
    }
};
TEST_F(Phrases,AdjacentOrderedTokensAndNoCrossFieldMatch) {
    EXPECT_EQ(query("doom AND eternal"),(std::vector<U64>{3,8,42,100,5000000000ULL}));
    EXPECT_EQ(query("\"doom eternal\""),(std::vector<U64>{8,100,5000000000ULL}));
    EXPECT_EQ(query("\"eternal doom\""),(std::vector<U64>{5000000000ULL}));
    EXPECT_EQ(query("\"DOOM, eternal!\""),query("\"doom eternal\""));
    EXPECT_EQ(query("\"doom\""),query("doom"));
}
TEST_F(Phrases,RepeatedWordsOverlappingOccurrencesAndUnknownTokens) {
    EXPECT_EQ(query("\"red red\""),(std::vector<U64>{8}));
    EXPECT_EQ(query("\"red blue red\""),(std::vector<U64>{8}));
    EXPECT_EQ(query("\"blue red\""),(std::vector<U64>{8,42}));
    EXPECT_TRUE(query("\"red red red\"").empty());
    EXPECT_TRUE(query("\"doom absent\"").empty());
}
TEST_F(Phrases,PhrasesCombineWithBooleanOperatorsInBothSpaces) {
    for (bool lemma:{false,true}) {
        EXPECT_EQ(query("\"doom eternal\" AND game",lemma),(std::vector<U64>{5000000000ULL}));
        EXPECT_EQ(query("NOT \"doom eternal\"",lemma),(std::vector<U64>{3,42}));
        EXPECT_EQ(query("\"doom eternal\" OR \"blue red\"",lemma),(std::vector<U64>{8,42,100,5000000000ULL}));
        EXPECT_TRUE(query("\"doom eternal\" AND NOT \"doom eternal\"",lemma).empty());
    }
}
TEST_F(Phrases,LemmaAlternativesStayAtOriginalPositionAndDoNotInventNeighbors) {
    EXPECT_EQ(query(u8"\"игры стали\""),(std::vector<U64>{42}));
    EXPECT_EQ(query(u8"\"игры стали\"",true),(std::vector<U64>{42,100,5000000000ULL}));
    EXPECT_TRUE(query(u8"\"сталь стать\"",true).empty());
    EXPECT_EQ(query(u8"\"игрой сталь\"",true),(std::vector<U64>{42,100}));
}
TEST_F(Phrases,CoordinatesExposeFieldAndTokenPositionAndVerify) {
    gaming::BooleanIndex b; gaming::PositionalIndex p; const char* error=nullptr;
    ASSERT_TRUE(b.open((root/"index").string().c_str(),&error));
    ASSERT_TRUE(p.open((root/"positions").string().c_str(),(root/"index").string().c_str(),b,&error));
    ASSERT_TRUE(p.verify(&error))<<(error ? error : "");
    gaming::PositionList list; ASSERT_TRUE(p.lookup("doom",4,gaming::TermSpace::surface,list,&error)); EXPECT_EQ(list.count,6);
    gaming::PositionCursor c; ASSERT_TRUE(c.open(p,list,&error)); U64 key;
    ASSERT_TRUE(c.next(key,&error)); EXPECT_EQ(key,0); // document ordinal 0, title position 0
    ASSERT_TRUE(c.next(key,&error)); EXPECT_EQ(key,UINT64_C(1)<<32); // doc ordinal 1, title
    ASSERT_TRUE(c.next(key,&error)); EXPECT_EQ(key,(UINT64_C(2)<<32)|0x80000000ULL); // body
}
TEST_F(Phrases,MalformedQuotesEmptyPhrasesAndMissingPositionsAreErrors) {
    gaming::BooleanSearch engine; const char* error=nullptr;
    ASSERT_TRUE(engine.open((root/"index").string().c_str(),gaming::TermSpace::surface,nullptr,&error,(root/"positions").string().c_str()));
    gaming::BooleanQuery q;
    for (const auto& text:{"\"\"","\"...\"","\"doom","\"doom\" eternal","doom \"eternal\""}) EXPECT_FALSE(q.compile(engine,text,&error))<<text;
    std::string long_phrase="\"a"; for (unsigned i=0;i<64;++i) long_phrase+=" a"; long_phrase+='"';
    EXPECT_FALSE(q.compile(engine,long_phrase.c_str(),&error));
    gaming::BooleanSearch plain;
    ASSERT_TRUE(plain.open((root/"index").string().c_str(),gaming::TermSpace::surface,nullptr,&error));
    EXPECT_FALSE(q.compile(plain,"\"doom eternal\"",&error)); ASSERT_NE(error,nullptr); EXPECT_NE(std::string(error).find("--positions"),std::string::npos);
}
TEST_F(Phrases,CorruptedTruncatedIncompleteAndWrongBindingAreRejected) {
    const auto bytes=read(root/"positions/positions.bin");
    { std::fstream f(root/"positions/positions.bin",std::ios::in|std::ios::out|std::ios::binary); f.seekp(80); f.put('X'); }
    gaming::BooleanIndex b; const char* error=nullptr; ASSERT_TRUE(b.open((root/"index").string().c_str(),&error));
    { gaming::PositionalIndex p; ASSERT_TRUE(p.open((root/"positions").string().c_str(),(root/"index").string().c_str(),b,&error)); EXPECT_FALSE(p.verify(&error)); }
    fs::resize_file(root/"positions/positions.bin",bytes.size()-1);
    { gaming::PositionalIndex p; EXPECT_FALSE(p.open((root/"positions").string().c_str(),(root/"index").string().c_str(),b,&error)); }
    { std::ofstream f(root/"positions/positions.bin",std::ios::binary); f<<bytes; }
    { std::ofstream f(root/"positions/INCOMPLETE"); f<<"incomplete"; }
    { gaming::PositionalIndex p; EXPECT_FALSE(p.open((root/"positions").string().c_str(),(root/"index").string().c_str(),b,&error)); }
    fs::remove(root/"positions/INCOMPLETE"); corpus({{1,"other","corpus"}}); ASSERT_EQ(boolean("other"),0);
    gaming::BooleanIndex other; ASSERT_TRUE(other.open((root/"other").string().c_str(),&error));
    gaming::PositionalIndex p; EXPECT_FALSE(p.open((root/"positions").string().c_str(),(root/"other").string().c_str(),other,&error));
}
TEST_F(Phrases,WrongCorpusDictionaryAndExistingOutputAreProtected) {
    EXPECT_NE(positions().first,0);
    EXPECT_NE(positions("missingdict",1,false).first,0); EXPECT_FALSE(fs::exists(root/"missingdict"));
    corpus({{3,"changed",""},{8,"",""},{42,"",""},{100,"",""},{5000000000ULL,"",""}});
    EXPECT_NE(positions("wrongcorpus").first,0); EXPECT_TRUE(fs::exists(root/"wrongcorpus/INCOMPLETE"));
    const auto failed=run({"build","--corpus",(root/"corpus.bin").string(),"--index",(root/"index").string(),"--dict",(root/"dict.bin").string(),"--output",(root/"reserve").string(),"--reserve-mib","1000000000"},true);
    EXPECT_NE(failed.first,0); EXPECT_TRUE(fs::exists(root/"reserve/INCOMPLETE"));
}
TEST_F(Phrases,ExternalMergeAcrossMoreThan24RunsMatchesLargeBufferByteForByte) {
    std::string body; body.reserve(2700000); for (unsigned i=0;i<300000;++i) body+="red blue ";
    corpus({{10,"",body}}); fs::rename(root/"index",root/"old-index"); ASSERT_EQ(boolean(),0);
    const auto small=positions("small",1); ASSERT_EQ(small.first,0);
    const auto begin=small.second.rfind('{'); ASSERT_NE(begin,std::string::npos);
    const auto stats=nlohmann::json::parse(small.second.substr(begin)); EXPECT_GT(stats["initial_runs"],24); EXPECT_EQ(stats["merge_passes"],2);
    ASSERT_EQ(positions("large",32).first,0);
    EXPECT_EQ(read(root/"small/position_terms.bin"),read(root/"large/position_terms.bin"));
    EXPECT_EQ(read(root/"small/positions.bin"),read(root/"large/positions.bin"));
    EXPECT_FALSE(fs::exists(root/"small/INCOMPLETE"));
    for (const auto& entry:fs::directory_iterator(root/"small")) EXPECT_NE(entry.path().filename().string().find("run_"),0);
}
TEST_F(Phrases,EmptyCorpusAndSurfaceOnlyCoordinateIndex) {
    corpus({}); fs::rename(root/"index",root/"old-index"); ASSERT_EQ(boolean("index",false),0);
    ASSERT_EQ(positions("empty",1,false).first,0);
    gaming::BooleanSearch engine; const char* error=nullptr;
    ASSERT_TRUE(engine.open((root/"index").string().c_str(),gaming::TermSpace::surface,nullptr,&error,(root/"empty").string().c_str()));
    gaming::BooleanQuery q; ASSERT_TRUE(q.compile(engine,"NOT \"doom eternal\"",&error)); U64 id; EXPECT_FALSE(q.next(id,&error)); EXPECT_EQ(error,nullptr);
}
TEST_F(Phrases,GeneratedPhrasesMatchIndependentSlidingWindowReference) {
    Docs docs; std::vector<std::string> bodies;
    for (unsigned mask=0;mask<8;++mask) {
        std::string body;
        for (unsigned j=0;j<3;++j) { if (j) body+=' '; body+=(mask&(1U<<j)) ? 'a' : 'b'; }
        bodies.push_back(body); docs.emplace_back(10+mask,"",body);
    }
    corpus(docs); fs::rename(root/"index",root/"old-index"); fs::rename(root/"positions",root/"old-positions");
    ASSERT_EQ(boolean(),0); ASSERT_EQ(positions().first,0);
    for (unsigned length=1;length<=3;++length) for (unsigned bits=0;bits<(1U<<length);++bits) {
        std::string phrase;
        for (unsigned j=0;j<length;++j) { if (j) phrase+=' '; phrase+=(bits&(1U<<j)) ? 'a' : 'b'; }
        std::vector<U64> expected;
        for (unsigned d=0;d<8;++d) {
            bool found=false;
            for (unsigned start=0;start+length<=3;++start) {
                bool match=true;
                for (unsigned j=0;j<length;++j) if (bodies[d][(start+j)*2]!=phrase[j*2]) match=false;
                found=found || match;
            }
            if (found) expected.push_back(10+d);
        }
        for (bool lemma:{false,true}) EXPECT_EQ(query('"'+phrase+'"',lemma),expected)<<phrase;
    }
}
TEST_F(Phrases,CliQuotedQueriesAndMalformedLineRecovery) {
    const std::string base=(root/"index").string(),pos=(root/"positions").string();
    const char* argv[]={"search","--index",base.c_str(),"--positions",pos.c_str(),"--limit","1"};
    FILE *input=std::tmpfile(),*output=std::tmpfile(),*errors=std::tmpfile();
    std::fputs("\"doom eternal\"\n\"unclosed\n\"red red\"",input); std::rewind(input);
    EXPECT_EQ(gaming::run_boolean_search(7,argv,input,output,errors),1);
    std::rewind(output); char line[8192]; std::vector<nlohmann::json> rows;
    while (std::fgets(line,sizeof(line),output)) rows.push_back(nlohmann::json::parse(line));
    std::fclose(input); std::fclose(output); std::fclose(errors);
    ASSERT_EQ(rows.size(),3); EXPECT_EQ(rows[0]["total"],3); EXPECT_EQ(rows[0]["results"].size(),1);
    EXPECT_TRUE(rows[1].contains("error")); EXPECT_EQ(rows[2]["total"],1);
}
}
