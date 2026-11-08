#include "boolean_search.hpp"
#include <gtest/gtest.h>
#include <nlohmann/json.hpp>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <functional>
#include <map>
#include <sstream>
#include <string>
#include <vector>

namespace {
namespace fs=std::filesystem;
using U64=std::uint64_t;
void number(std::ostream& f,U64 n,unsigned bytes=8) { for (unsigned i=0;i<bytes;++i) f.put(static_cast<char>(n>>(i*8))); }
void dictionary(const fs::path& path,bool changed=false) {
    const std::map<std::string,std::vector<std::string>> pairs={
        {"n:game",{changed ? "other" : "game"}},
        {u8"ru:игрой",{u8"игра"}}, {u8"ru:игры",{u8"игра"}}, {u8"ru:стали",{u8"сталь",u8"стать"}}};
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
std::string contents(FILE* f) {
    std::rewind(f); std::string result; char block[4096]; std::size_t n;
    while ((n=std::fread(block,1,sizeof(block),f))) result.append(block,n);
    return result;
}
struct Search : testing::Test {
    fs::path root;
    const std::vector<U64> ids={3,12,45,100,1000,2000,4000,5000000000ULL};
    void SetUp() override {
        root=fs::temp_directory_path()/("mai-search-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        ASSERT_TRUE(fs::create_directory(root));
        std::ofstream f(root/"corpus.bin",std::ios::binary); f.write("MAIIR3\0\1",8);
        for (unsigned mask=0;mask<8;++mask) {
            std::string text;
            if (mask&1) text+="a a ";
            if (mask&2) text+="b ";
            if (mask&4) text+="c ";
            if (mask==1) text+=u8"Игры games стали ";
            if (mask==2) text+=u8"игрой game сталь ";
            if (mask==4) text+=u8"стать ";
            const std::string title=mask==7 ? "Title \"quoted\"\n<game>" : "";
            number(f,ids[mask]); number(f,4,4); f<<"test";
            number(f,title.size()); f<<title; number(f,text.size()); f<<text;
        }
        f.close(); dictionary(root/"dictionary.bin");
        std::vector<std::string> args={"boolean_index","build","--corpus",(root/"corpus.bin").string(),"--output",(root/"index").string(),"--dict",(root/"dictionary.bin").string()};
        std::vector<const char*> argv; for (const auto& a:args) argv.push_back(a.c_str());
        FILE *out=std::tmpfile(),*err=std::tmpfile(); ASSERT_NE(out,nullptr); ASSERT_NE(err,nullptr);
        const int code=gaming::run_boolean_index(static_cast<int>(argv.size()),argv.data(),out,err);
        const auto errors=contents(err); std::fclose(out); std::fclose(err); ASSERT_EQ(code,0)<<errors;
    }
    void TearDown() override { fs::remove_all(root); }
    std::vector<U64> query(const std::string& text,bool lemma=false) {
        gaming::BooleanSearch engine; const char* error=nullptr;
        EXPECT_TRUE(engine.open((root/"index").string().c_str(),lemma ? gaming::TermSpace::lemma : gaming::TermSpace::surface,
                                (root/"dictionary.bin").string().c_str(),&error))<<(error ? error : "");
        gaming::BooleanQuery q; EXPECT_TRUE(q.compile(engine,text.c_str(),&error))<<(error ? error : "");
        std::vector<U64> result; U64 id;
        while (q.next(id,&error)) result.push_back(id);
        EXPECT_EQ(error,nullptr); return result;
    }
    std::vector<U64> expected(const std::function<bool(unsigned)>& accept) {
        std::vector<U64> result; for (unsigned i=0;i<8;++i) if (accept(i)) result.push_back(ids[i]); return result;
    }
    std::pair<int,std::string> cli(std::vector<std::string> args,const std::string& input="") {
        args.insert(args.begin(),{"boolean_search","--index",(root/"index").string()});
        std::vector<const char*> argv; for (const auto& a:args) argv.push_back(a.c_str());
        FILE *in=std::tmpfile(),*out=std::tmpfile(),*err=std::tmpfile();
        std::fwrite(input.data(),1,input.size(),in); std::rewind(in);
        const int code=gaming::run_boolean_search(static_cast<int>(argv.size()),argv.data(),in,out,err);
        const auto result=contents(out); std::fclose(in); std::fclose(out); std::fclose(err); return {code,result};
    }
};
TEST_F(Search,PrecedenceParenthesesAndSymbolAliases) {
    EXPECT_EQ(query("a OR b AND NOT c"),expected([](unsigned m){return (m&1) || ((m&2) && !(m&4));}));
    EXPECT_EQ(query("(a OR b) AND NOT c"),expected([](unsigned m){return (m&3) && !(m&4);}));
    EXPECT_EQ(query("(a | b) & !c"),query("(a OR b) AND NOT c"));
    EXPECT_EQ(query("NOT NOT a"),query("a"));
}
TEST_F(Search,ComplementIncludesEmptyDocsAndArbitrary64BitIds) {
    EXPECT_EQ(query("NOT absent"),ids);
    EXPECT_EQ(query("NOT (a OR b OR c)"),(std::vector<U64>{3}));
    EXPECT_EQ(query("a AND b AND c"),(std::vector<U64>{5000000000ULL}));
    EXPECT_TRUE(query("NOT (NOT absent)").empty());
}
TEST_F(Search,SetIdentitiesAndDuplicates) {
    EXPECT_EQ(query("a OR a OR a"),query("a"));
    EXPECT_EQ(query("a AND a"),query("a"));
    EXPECT_TRUE(query("a AND NOT a").empty());
    EXPECT_EQ(query("a OR NOT a"),ids);
    EXPECT_EQ(query("NOT (a AND b)"),query("NOT a OR NOT b"));
    EXPECT_EQ(query("NOT (a OR b)"),query("NOT a AND NOT b"));
    EXPECT_TRUE(query("absent AND a").empty());
    EXPECT_EQ(query("absent OR a"),query("a"));
}
TEST_F(Search,GeneratedExpressionsMatchIndependentTruthTable) {
    const std::vector<std::string> atoms={"a","b","c","absent"};
    for (bool lemma:{false,true}) for (unsigned a=0;a<4;++a) for (unsigned b=0;b<4;++b) for (unsigned c=0;c<4;++c) {
        const auto q="("+atoms[a]+" OR NOT "+atoms[b]+") AND "+atoms[c];
        EXPECT_EQ(query(q,lemma),expected([=](unsigned m){
            const auto has=[m](unsigned bit){return bit<3 && (m&(1U<<bit));};
            return (has(a) || !has(b)) && has(c);
        }))<<q<<" space="<<(lemma ? "lemma" : "surface");
    }
}
TEST_F(Search,NormalizationAndLemmaAlternativesMatchIndexPolicy) {
    EXPECT_EQ(query("A"),query("a"));
    EXPECT_EQ(query(u8"ИГРЫ"),(std::vector<U64>{12}));
    EXPECT_EQ(query(u8"Игры",true),(std::vector<U64>{12,45}));
    EXPECT_EQ(query("games",true),(std::vector<U64>{12,45,5000000000ULL}));
    EXPECT_EQ(query(u8"стали",true),(std::vector<U64>{12,45,1000}));
    EXPECT_EQ(query(u8"сталь",true),(std::vector<U64>{12,45}));
    EXPECT_EQ(query(u8"NOT стали",true),expected([](unsigned m){return m!=1 && m!=2 && m!=4;}));
    EXPECT_EQ(query("a",true),query("a"));
}
TEST_F(Search,MalformedExpressionsAndUtf8AreRejectedAndQueryCanBeRecompiled) {
    gaming::BooleanSearch engine; const char* error=nullptr;
    ASSERT_TRUE(engine.open((root/"index").string().c_str(),gaming::TermSpace::surface,nullptr,&error));
    gaming::BooleanQuery q;
    for (const auto& text:std::vector<std::string>{""," ","()","(a","a)","a b","AND a","a OR","a && b","NOT","a OR OR b","\"a b\"","a/b",std::string("\xc0\x80",2)}) {
        EXPECT_FALSE(q.compile(engine,text.c_str(),&error))<<text; EXPECT_NE(error,nullptr);
    }
    ASSERT_TRUE(q.compile(engine,"a",&error)); U64 id; EXPECT_TRUE(q.next(id,&error)); EXPECT_EQ(id,12);
}
TEST_F(Search,QuerySizeDepthAndOperandBoundsAreEnforced) {
    gaming::BooleanSearch engine; const char* error=nullptr;
    ASSERT_TRUE(engine.open((root/"index").string().c_str(),gaming::TermSpace::surface,nullptr,&error)); gaming::BooleanQuery q;
    EXPECT_FALSE(q.compile(engine,std::string(8193,'a').c_str(),&error));
    EXPECT_FALSE(q.compile(engine,(std::string(66,'(')+"a"+std::string(66,')')).c_str(),&error));
    std::string many="a"; for (int i=0;i<64;++i) many+=" OR a";
    EXPECT_FALSE(q.compile(engine,many.c_str(),&error));
}
TEST_F(Search,DictionaryMustBePresentAndHaveTheIndexFingerprint) {
    gaming::BooleanSearch engine; const char* error=nullptr;
    EXPECT_FALSE(engine.open((root/"index").string().c_str(),gaming::TermSpace::lemma,nullptr,&error));
    dictionary(root/"other.bin",true);
    EXPECT_FALSE(engine.open((root/"index").string().c_str(),gaming::TermSpace::lemma,(root/"other.bin").string().c_str(),&error));
    ASSERT_NE(error,nullptr); EXPECT_NE(std::string(error).find("fingerprint"),std::string::npos);
}
TEST_F(Search,CliCountsAllMatchesButLimitsDisplayedResultsAndEscapesMetadata) {
    const auto [code,text]=cli({"--limit","2","--query","NOT absent"}); ASSERT_EQ(code,0);
    const auto row=nlohmann::json::parse(text); EXPECT_EQ(row["total"],8); ASSERT_EQ(row["results"].size(),2);
    EXPECT_EQ(row["results"][0]["id"],3); EXPECT_EQ(row["results"][1]["id"],12);
    const auto all=cli({"--query","a AND b AND c"}); ASSERT_EQ(all.first,0);
    const auto doc=nlohmann::json::parse(all.second)["results"][0];
    EXPECT_EQ(doc["title"],"Title \"quoted\"\n<game>"); EXPECT_EQ(doc["source"],"test");
    EXPECT_EQ(nlohmann::json::parse(cli({"--limit","0","--query","a"}).second)["results"].size(),0);
}
TEST_F(Search,CliStdinRecoversAfterBadQueriesAndHandlesCrLfAndFinalLine) {
    const auto [code,text]=cli({},"a\r\n()\na AND b"); EXPECT_EQ(code,1);
    std::istringstream lines(text); std::string line; std::vector<nlohmann::json> rows;
    while (std::getline(lines,line)) rows.push_back(nlohmann::json::parse(line));
    ASSERT_EQ(rows.size(),3); EXPECT_EQ(rows[0]["total"],4); EXPECT_TRUE(rows[1].contains("error")); EXPECT_EQ(rows[2]["total"],2);
    const auto bad=cli({},std::string(9000,'a')+"\n"+std::string("a\0b\n",4)+"c\n");
    std::istringstream more(bad.second); rows.clear();
    while (std::getline(more,line)) rows.push_back(nlohmann::json::parse(line));
    ASSERT_EQ(rows.size(),3); EXPECT_TRUE(rows[0].contains("error")); EXPECT_TRUE(rows[1].contains("error")); EXPECT_EQ(rows[2]["total"],4);
    EXPECT_NE(cli({"--limit","1001","--query","a"}).first,0);
    EXPECT_NE(cli({"--space","unknown","--query","a"}).first,0);
}
TEST_F(Search,EmptyIndexAndSurfaceOnlyIndexWorkWithoutDictionary) {
    { std::ofstream f(root/"empty.bin",std::ios::binary); f.write("MAIIR3\0\1",8); }
    const std::string corpus=(root/"empty.bin").string(),output=(root/"empty").string();
    const char* args[]={"index","build","--corpus",corpus.c_str(),"--output",output.c_str()};
    FILE *out=std::tmpfile(),*err=std::tmpfile(); ASSERT_EQ(gaming::run_boolean_index(6,args,out,err),0); std::fclose(out); std::fclose(err);
    gaming::BooleanSearch engine; const char* error=nullptr;
    ASSERT_TRUE(engine.open(output.c_str(),gaming::TermSpace::surface,nullptr,&error));
    gaming::BooleanQuery q; ASSERT_TRUE(q.compile(engine,"NOT absent",&error)); U64 id; EXPECT_FALSE(q.next(id,&error)); EXPECT_EQ(error,nullptr);
    EXPECT_FALSE(engine.open(output.c_str(),gaming::TermSpace::lemma,(root/"dictionary.bin").string().c_str(),&error));
}
}
