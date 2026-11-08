#include "boolean_search.hpp"
#include <gtest/gtest.h>
#include <filesystem>
#include <fstream>
#include <chrono>
#include <string>
#include <vector>
#include <map>
#include <sstream>

namespace {
namespace fs=std::filesystem;
using U64=std::uint64_t;
void num(std::ostream& out,U64 n,unsigned width=8) { for (unsigned i=0;i<width;++i) out.put(static_cast<char>(n>>(8*i))); }
int command(const std::vector<std::string>& args,unsigned kind) {
    std::vector<const char*> argv{"test"}; for (const auto& a:args) argv.push_back(a.c_str());
    FILE* out=std::tmpfile(); FILE* err=std::tmpfile();
    const int code=kind==0 ? gaming::run_boolean_index(static_cast<int>(argv.size()),argv.data(),out,err) :
                   kind==1 ? gaming::run_positional_index(static_cast<int>(argv.size()),argv.data(),out,err) :
                   kind==2 ? gaming::run_compact_index(static_cast<int>(argv.size()),argv.data(),out,err) :
                             gaming::run_skip_index(static_cast<int>(argv.size()),argv.data(),out,err);
    std::fclose(out); std::fclose(err); return code;
}
struct Skips:testing::Test {
    fs::path root;
    std::string p(const char* name) const { return (root/name).string(); }
    std::vector<std::string> base() const { return {"--index",p("packed/index"),"--positions",p("packed/positions"),"--compact",p("packed/compact")}; }
    int skip(const char* mode,const char* option,const char* dir) {
        auto a=base(); a.insert(a.begin(),mode); a.push_back(option); a.push_back(p(dir)); a.push_back("--reserve-mib"); a.push_back("0"); return command(a,3);
    }
    void SetUp() override {
        root=fs::temp_directory_path()/("mai-skips-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        ASSERT_TRUE(fs::create_directory(root));
        const std::map<std::string,std::vector<std::string>> entries={
            {u8"ru:игрой",{u8"игра"}},{u8"ru:игры",{u8"игра",u8"играть"}}};
        std::ostringstream records,strings;
        for (const auto& entry:entries) {
            const auto ko=strings.str().size(); strings<<entry.first; const auto vo=strings.str().size();
            for (const auto& v:entry.second) { strings<<v; strings.put(0); }
            num(records,ko,4); num(records,entry.first.size(),4); num(records,vo,4); num(records,strings.str().size()-vo,4);
        }
        const auto payload=records.str()+strings.str(); U64 checksum=14695981039346656037ULL;
        for (unsigned char c:payload) { checksum^=c; checksum*=1099511628211ULL; }
        { std::ofstream d(p("dict.bin"),std::ios::binary); d.write("MAIIL4\0\1",8); num(d,entries.size()); num(d,strings.str().size()); num(d,checksum); d<<payload; }
        std::ofstream corpus(p("corpus.bin"),std::ios::binary); corpus.write("MAIIR3\0\1",8);
        for (unsigned i=0;i<600;++i) {
            const U64 id=i==599 ? UINT64_MAX : 5000000000ULL+i*17;
            const std::string title=i%17==0 ? "God of War" : "Common title";
            std::string body="common ";
            for (unsigned j=0;j<25;++j) body+=(j%3==0 ? "of " : "filler ");
            if (i%17==0) body+="god of war rare ";
            if (i%11==0) body+="red red red blue ";
            if (i%7==0) body+="doom eternal ";
            if (i%5==0) body+="eternal doom ";
            if (i%17==0) body+=u8"игры of war ";
            if (i%7==0) body+=u8"игрой of war ";
            num(corpus,id); num(corpus,4,4); corpus<<"test"; num(corpus,title.size()); corpus<<title; num(corpus,body.size()); corpus<<body;
        }
        corpus.close();
        ASSERT_EQ(command({"build","--corpus",p("corpus.bin"),"--output",p("index"),"--dict",p("dict.bin")},0),0);
        ASSERT_EQ(command({"build","--corpus",p("corpus.bin"),"--index",p("index"),"--output",p("positions"),"--memory-mib","1","--dict",p("dict.bin")},1),0);
        ASSERT_EQ(command({"build","--index",p("index"),"--positions",p("positions"),"--output",p("compact"),"--reserve-mib","0"},2),0);
        ASSERT_EQ(command({"pack","--index",p("index"),"--positions",p("positions"),"--compact",p("compact"),"--output",p("packed"),"--reserve-mib","0"},2),0);
        ASSERT_EQ(skip("build","--output","skips"),0);
    }
    void TearDown() override { fs::remove_all(root); }
    std::vector<U64> query(const std::string& text,bool optimized,bool use_skips,gaming::SearchStats* stats=nullptr,bool lemma=false) {
        gaming::BooleanSearch engine; const char* error=nullptr;
        EXPECT_TRUE(engine.open(p("packed/index").c_str(),lemma ? gaming::TermSpace::lemma : gaming::TermSpace::surface,p("dict.bin").c_str(),&error,p("packed/positions").c_str(),
                                p("packed/compact").c_str(),use_skips ? p("skips").c_str() : nullptr,optimized))<<(error ? error : "");
        gaming::BooleanQuery q; EXPECT_TRUE(q.compile(engine,text.c_str(),&error))<<(error ? error : "");
        U64 id=0; std::vector<U64> ids; while (q.next(id,&error)) ids.push_back(id);
        EXPECT_EQ(error,nullptr); if (stats) *stats=engine.stats(); return ids;
    }
};
TEST_F(Skips,AllBooleanAndPhraseResultsEqualLinearIncludingNestedNegation) {
    for (const char* text:{"common","absent","NOT absent","rare AND common","common AND NOT doom","NOT doom AND common",
                          "NOT NOT common","NOT (rare OR doom)","(common OR doom) AND (rare OR eternal)",
                          "common AND NOT (doom AND NOT rare)","NOT common AND NOT doom","doom OR NOT rare",
                          "\"god of war\"","\"red red\"","\"red red red\"","\"common title\"","\"title common\"",
                          "\"god of war\" AND NOT doom","rare AND \"god of war\"","\"god of war\" OR \"eternal doom\""}) {
        const auto reference=query(text,false,false);
        EXPECT_EQ(query(text,true,false),reference)<<text;
        EXPECT_EQ(query(text,true,true),reference)<<text;
    }
    EXPECT_EQ(query("\"god of war\"",true,true).size(),36U);
    EXPECT_EQ(query("NOT absent",true,true).back(),UINT64_MAX);
    const char* words[]={"rare","common","doom","eternal","absent"};
    for (unsigned i=0;i<25;++i) {
        const std::string text="("+std::string(words[i%5])+" OR NOT "+words[(i*3)%5]+") AND ("+words[(i+1)%5]+" OR "+words[(i+2)%5]+")";
        EXPECT_EQ(query(text,true,true),query(text,false,false))<<text;
    }
}
TEST_F(Skips,CheckpointsVerifyAndReduceDecodedCoordinates) {
    EXPECT_EQ(skip("verify","--skips","skips"),0);
    gaming::SearchStats linear,fast;
    EXPECT_EQ(query("\"god of war\"",true,true,&fast),query("\"god of war\"",false,false,&linear));
    EXPECT_GT(fast.jumps,0U); EXPECT_GT(fast.skipped,0U);
    EXPECT_LT(fast.decoded_coordinates,linear.decoded_coordinates);
    EXPECT_EQ(query("common AND NOT doom",true,true,&fast),query("common AND NOT doom",false,false,&linear));
    EXPECT_LT(fast.universe_documents,linear.universe_documents);
}
TEST_F(Skips,LemmaAlternativesRepeatedWordsAndFieldsPreserveResults) {
    for (const char* text:{u8"игры AND common",u8"\"игры of war\"",u8"\"игрой of war\"",u8"NOT игры AND common",
                          "\"red red red\"","\"title common\"","\"god of war\" AND NOT doom"}) {
        EXPECT_EQ(query(text,true,true,nullptr,true),query(text,false,false,nullptr,true))<<text;
    }
    EXPECT_GT(query(u8"\"игры of war\"",true,true,nullptr,true).size(),36U);
}
TEST_F(Skips,AdvancePreservesExactCheckpointAndLargestIdAndMonotonicity) {
    gaming::BooleanSearch e; const char* error=nullptr;
    ASSERT_TRUE(e.open(p("packed/index").c_str(),gaming::TermSpace::surface,nullptr,&error,p("packed/positions").c_str(),p("packed/compact").c_str(),p("skips").c_str()));
    gaming::PostingList list; ASSERT_TRUE(e.index().lookup("common",6,gaming::TermSpace::surface,list,&error));
    gaming::PostingCursor cursor; ASSERT_TRUE(cursor.open(e.index(),list,&error));
    U64 id=0;
    ASSERT_TRUE(cursor.advance(5000000000ULL+24*17,id,&error)); EXPECT_EQ(id,5000000000ULL+24*17);
    ASSERT_TRUE(cursor.advance(id,id,&error)); EXPECT_EQ(id,5000000000ULL+24*17);
    ASSERT_TRUE(cursor.advance(UINT64_MAX,id,&error)); EXPECT_EQ(id,UINT64_MAX);
    EXPECT_FALSE(cursor.next(id,&error)); EXPECT_EQ(error,nullptr);
}
TEST_F(Skips,IncompleteTruncatedWrongBindingAndExistingOutputAreRejected) {
    EXPECT_NE(skip("build","--output","skips"),0);
    const auto binding_file=root/"skips/skip_postings.bin";
    { std::fstream changed(binding_file,std::ios::in|std::ios::out|std::ios::binary); changed.seekg(40); const int value=changed.get(); changed.seekp(40); changed.put(static_cast<char>(value^255)); }
    EXPECT_NE(skip("verify","--skips","skips"),0);
    { std::fstream changed(binding_file,std::ios::in|std::ios::out|std::ios::binary); changed.seekg(40); const int value=changed.get(); changed.seekp(40); changed.put(static_cast<char>(value^255)); }
    { std::ofstream marker(p("skips/INCOMPLETE")); marker<<"Incomplete\n"; }
    EXPECT_NE(skip("verify","--skips","skips"),0); fs::remove(root/"skips/INCOMPLETE");
    const auto file=root/"skips/skip_positions.bin"; const auto size=fs::file_size(file); fs::resize_file(file,size-1);
    EXPECT_NE(skip("verify","--skips","skips"),0);
    fs::resize_file(file,size);
    { std::fstream changed(file,std::ios::in|std::ios::out|std::ios::binary); changed.seekg(80); const int value=changed.get(); changed.seekp(80); changed.put(static_cast<char>(value^255)); }
    EXPECT_NE(skip("verify","--skips","skips"),0);
}
TEST_F(Skips,CoordinateAdvanceMatchesLinearLowerBoundAcrossDocumentAndFieldChanges) {
    gaming::BooleanSearch e; const char* error=nullptr;
    ASSERT_TRUE(e.open(p("packed/index").c_str(),gaming::TermSpace::surface,nullptr,&error,p("packed/positions").c_str(),p("packed/compact").c_str(),p("skips").c_str()));
    gaming::PositionList list; ASSERT_TRUE(e.positions().lookup("of",2,gaming::TermSpace::surface,list,&error));
    gaming::PositionCursor source; ASSERT_TRUE(source.open(e.positions(),list,&error));
    std::vector<U64> reference; U64 value=0; while (source.next(value,&error)) reference.push_back(value);
    ASSERT_EQ(error,nullptr);
    gaming::PositionCursor fast; ASSERT_TRUE(fast.open(e.positions(),list,&error));
    for (std::size_t i=0;i<reference.size();i+=137) {
        ASSERT_TRUE(fast.advance(reference[i],value,&error)); EXPECT_EQ(value,reference[i]);
        ASSERT_TRUE(fast.advance(reference[i],value,&error)); EXPECT_EQ(value,reference[i]);
        if (i+1<reference.size()) { ASSERT_TRUE(fast.next(value,&error)); EXPECT_EQ(value,reference[i+1]); }
    }
    EXPECT_FALSE(fast.advance(UINT64_MAX,value,&error)); EXPECT_EQ(error,nullptr);
}
TEST_F(Skips,EmptyIndexAndDiskReserveAreHandled) {
    auto a=base(); a.insert(a.begin(),"build"); a.insert(a.end(),{"--output",p("reserve"),"--reserve-mib","1000000000"});
    EXPECT_NE(command(a,3),0); EXPECT_TRUE(fs::exists(root/"reserve/INCOMPLETE"));
    { std::ofstream f(p("empty.bin"),std::ios::binary); f.write("MAIIR3\0\1",8); }
    ASSERT_EQ(command({"build","--corpus",p("empty.bin"),"--output",p("empty_index")},0),0);
    ASSERT_EQ(command({"build","--corpus",p("empty.bin"),"--index",p("empty_index"),"--output",p("empty_positions")},1),0);
    ASSERT_EQ(command({"build","--index",p("empty_index"),"--positions",p("empty_positions"),"--output",p("empty_compact"),"--reserve-mib","0"},2),0);
    for (const char* mode:{"build","verify"}) {
        EXPECT_EQ(command({mode,"--index",p("empty_index"),"--positions",p("empty_positions"),"--compact",p("empty_compact"),
                           mode==std::string("build") ? "--output" : "--skips",p("empty_skips"),"--reserve-mib","0"},3),0);
    }
}
TEST_F(Skips,CliSupportsStatsLinearAndSkipMode) {
    for (const char* execution:{"linear","optimized"}) {
        std::vector<std::string> a=base(); a.insert(a.end(),{"--skips",p("skips"),"--execution",execution,"--stats","--query","\"god of war\""});
        std::vector<const char*> argv{"boolean_search"}; for (const auto& s:a) argv.push_back(s.c_str());
        FILE* in=std::tmpfile(); FILE* out=std::tmpfile(); FILE* err=std::tmpfile();
        EXPECT_EQ(gaming::run_boolean_search(static_cast<int>(argv.size()),argv.data(),in,out,err),0);
        std::rewind(out); std::string response; char p[1024]; while (const auto n=std::fread(p,1,sizeof(p),out)) response.append(p,n);
        EXPECT_NE(response.find("\"stats\":"),std::string::npos); EXPECT_NE(response.find("\"total\":36"),std::string::npos);
        std::fclose(in); std::fclose(out); std::fclose(err);
    }
}
}
