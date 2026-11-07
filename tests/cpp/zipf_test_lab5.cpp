#include "zipf.hpp"
#include <gtest/gtest.h>
#include <nlohmann/json.hpp>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <sstream>
#include <string>
#include <tuple>
#include <vector>

namespace {
namespace fs=std::filesystem;
using Json=nlohmann::json;
using Docs=std::vector<std::tuple<unsigned,std::string,std::string,std::string>>;
void integer(std::ostream& f,std::uint64_t value,unsigned bytes) {
    for (unsigned i=0;i<bytes;++i) f.put(static_cast<char>(value>>(8*i)));
}
std::string read(const fs::path& path) {
    std::ifstream file(path,std::ios::binary);
    return {std::istreambuf_iterator<char>(file),std::istreambuf_iterator<char>()};
}
void lemma_dictionary(const fs::path& path) {
    const std::vector<std::pair<std::string,std::vector<std::string>>> entries={
        {u8"ru:игры",{u8"игра"}}, {u8"ru:игра",{u8"игра"}}, {u8"ru:стали",{u8"сталь",u8"стать"}}};
    std::ostringstream records,strings;
    for (const auto& [key,values]:entries) {
        const auto key_offset=strings.str().size(); strings<<key;
        const auto value_offset=strings.str().size();
        for (const auto& value:values) { strings<<value; strings.put(0); }
        integer(records,key_offset,4); integer(records,key.size(),4);
        integer(records,value_offset,4); integer(records,strings.str().size()-value_offset,4);
    }
    const auto payload=records.str()+strings.str();
    std::uint64_t hash=14695981039346656037ULL;
    for (unsigned char c:payload) { hash^=c; hash*=1099511628211ULL; }
    std::ofstream out(path,std::ios::binary); out.write("MAIIL4\0\1",8);
    integer(out,entries.size(),8); integer(out,strings.str().size(),8); integer(out,hash,8); out<<payload;
}
struct Zipf : testing::Test {
    fs::path root;
    void SetUp() override {
        root=fs::temp_directory_path()/ ("mai-zipf-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        ASSERT_TRUE(fs::create_directory(root));
    }
    void TearDown() override { fs::remove_all(root); }
    void corpus(const Docs& docs) {
        std::ofstream file(root/"corpus.bin",std::ios::binary); file.write("MAIIR3\0\1",8);
        for (const auto& [id,source,title,text]:docs) {
            integer(file,id,8); integer(file,source.size(),4); file<<source;
            for (const auto& field:{title,text}) { integer(file,field.size(),8); file<<field; }
        }
    }
    int run(const char* dir="out",std::vector<std::string> extra={}) {
        std::vector<std::string> args={"zipf","--corpus",(root/"corpus.bin").string(),"--output",(root/dir).string()};
        args.insert(args.end(),extra.begin(),extra.end());
        std::vector<const char*> argv; for (const auto& arg:args) argv.push_back(arg.c_str());
        FILE *out=std::tmpfile(),*err=std::tmpfile();
        const int code=gaming::run_zipf(static_cast<int>(argv.size()),argv.data(),out,err);
        if (!code) { EXPECT_FALSE(fs::exists(root/dir/"INCOMPLETE")); }
        std::fclose(out); std::fclose(err); return code;
    }
    Json stats(const char* dir="out") { return Json::parse(read(root/dir/"statistics.json")); }
    Json group(const Json& s,const char* name) {
        for (const auto& g:s.at("groups")) if (g.at("name")==name) return g;
        ADD_FAILURE()<<"Missing group "<<name; return {};
    }
};
TEST_F(Zipf,CountsActualTokensAndAllGroupDimensions) {
    corpus({{1,"stopgame","Games games",u8"Игра игра игры"},{2,"pcgamer","Games","Game game C++ C#"}});
    std::ofstream(root/"types.tsv")<<"1\tnews\n2\treview\n";
    ASSERT_EQ(run("out",{"--groups",(root/"types.tsv").string()}),0);
    const auto s=stats(),all=group(s,"all");
    EXPECT_EQ(all["tokens"],10); EXPECT_EQ(all["vocabulary"],6); EXPECT_EQ(all["hapax"],3);
    EXPECT_EQ(group(s,"source:stopgame")["tokens"],5);
    EXPECT_EQ(group(s,"language:ru")["tokens"],5); // source language, not token script
    EXPECT_EQ(group(s,"type:news")["documents"],1);
    EXPECT_EQ(group(s,"type:review")["tokens"],5);
    const auto tsv=read(root/"out/frequencies.tsv");
    EXPECT_NE(tsv.find("all\t1\tgames\t3\t0.3\n"),std::string::npos);
    EXPECT_NE(tsv.find("all\t2\tgame\t2\t0.2\n"),std::string::npos);
    EXPECT_NE(tsv.find(u8"all\t3\tигра\t2\t0.2\n"),std::string::npos);
    EXPECT_NE(tsv.find("all\t4\tc#\t1\t0.1\n"),std::string::npos);
}
TEST_F(Zipf,FieldSelectionAndUnknownSourceAreExplicit) {
    corpus({{1,"custom","Title title","Body"}});
    ASSERT_EQ(run("title",{"--field","title"}),0);
    ASSERT_EQ(run("text",{"--field","text"}),0);
    EXPECT_EQ(group(stats("title"),"all")["tokens"],2);
    EXPECT_EQ(group(stats("text"),"language:unknown")["tokens"],1);
    EXPECT_FALSE(stats("text")["has_types"]);
}
TEST_F(Zipf,LemmasMergeUnambiguousFormsAndPreserveAmbiguityAndTokenMass) {
    corpus({{1,"stopgame",u8"Игры игра стали PS5",""}});
    lemma_dictionary(root/"dictionary.bin");
    ASSERT_EQ(run("surface"),0);
    ASSERT_EQ(run("lemma",{"--dict",(root/"dictionary.bin").string()}),0);
    const auto s=stats("lemma"),g=group(s,"all");
    EXPECT_EQ(s["representation"],"lemma_unambiguous");
    EXPECT_EQ(g["tokens"],group(stats("surface"),"all")["tokens"]);
    EXPECT_EQ(g["tokens"],4); EXPECT_EQ(g["vocabulary"],3);
    EXPECT_EQ(s["known_tokens"],3); EXPECT_EQ(s["changed_tokens"],1); EXPECT_EQ(s["ambiguous_tokens"],1);
    const auto table=read(root/"lemma/frequencies.tsv");
    EXPECT_NE(table.find(u8"all\t1\tигра\t2\t0.5\n"),std::string::npos);
    EXPECT_NE(table.find(u8"\tстали\t1\t0.25\n"),std::string::npos);
    EXPECT_EQ(table.find(u8"\tсталь\t"),std::string::npos);
    EXPECT_EQ(table.find(u8"\tстать\t"),std::string::npos);
    EXPECT_NE(table.find("\tps5\t1\t0.25\n"),std::string::npos);
}
TEST_F(Zipf,MissingDictionaryIsRejectedBeforeCreatingOutput) {
    corpus({{1,"test","hello",""}});
    EXPECT_NE(run("missing",{"--dict",(root/"absent.bin").string()}),0);
    EXPECT_FALSE(fs::exists(root/"missing"));
}
TEST_F(Zipf,NormalizedZipfConservesTheTotalAndSmallFitIsNull) {
    corpus({{1,"test","a a a b b c",""}});
    ASSERT_EQ(run(),0); const auto g=group(stats(),"all");
    EXPECT_NEAR(g["harmonic"].get<double>(),1+0.5+1.0/3,1e-10);
    EXPECT_NEAR(g["zipf_c"].get<double>()*g["harmonic"].get<double>(),6,1e-9);
    EXPECT_TRUE(g["alpha"].is_null()); EXPECT_TRUE(g["power_c"].is_null());
}
TEST_F(Zipf,PowerFitRecoversAKnownZipfDistribution) {
    std::string text;
    for (const auto& [word,n]:std::vector<std::pair<std::string,int>>{{"a",60},{"b",30},{"c",20},{"d",15},{"e",12}})
        for (int i=0;i<n;++i) text+=word+" ";
    corpus({{1,"test","",text}});
    ASSERT_EQ(run("out",{"--fit-min","1","--fit-max","5"}),0);
    const auto g=group(stats(),"all");
    EXPECT_NEAR(g["alpha"].get<double>(),1,1e-10);
    EXPECT_NEAR(g["power_c"].get<double>(),60,1e-9);
    EXPECT_NEAR(g["r_squared_log"].get<double>(),1,1e-10);
}
TEST_F(Zipf,EmptyAndPunctuationOnlyInputsHaveNoInventedTerms) {
    corpus({}); ASSERT_EQ(run("empty"),0);
    EXPECT_EQ(group(stats("empty"),"all")["tokens"],0);
    corpus({{1,"test","...","!?"}}); ASSERT_EQ(run("punct"),0);
    EXPECT_EQ(group(stats("punct"),"all")["vocabulary"],0);
}
TEST_F(Zipf,SpillingAndMultipleMergePassesPreserveEveryFrequency) {
    std::string text;
    for (unsigned i=0;i<150000;++i) text+="word"+std::to_string(i)+" ";
    text+="word42 word42 "; corpus({{1,"test","",text}});
    ASSERT_EQ(run("small",{"--memory-mib","1"}),0);
    const auto s=stats("small");
    EXPECT_GT(s["counting_runs"].get<unsigned>(),24u);
    const auto g=group(s,"all"); EXPECT_EQ(g["tokens"],150002); EXPECT_EQ(g["vocabulary"],150000);
    EXPECT_EQ(g["hapax"],149999);
    // Memory budget changes the number of runs, never ranking, ties or counts.
    ASSERT_EQ(run("large",{"--memory-mib","32"}),0);
    EXPECT_EQ(read(root/"small/frequencies.tsv"),read(root/"large/frequencies.tsv"));
    for (const auto& entry:fs::directory_iterator(root/"small")) EXPECT_NE(entry.path().extension(),".bin");
}
TEST_F(Zipf,MalformedCorpusAndUtf8AreMarkedIncomplete) {
    corpus({{1,"test","ok",std::string("\xc0\x80",2)}});
    EXPECT_NE(run("utf8"),0); EXPECT_TRUE(fs::exists(root/"utf8/INCOMPLETE"));
    corpus({{1,"test","good","text"}}); fs::resize_file(root/"corpus.bin",15);
    EXPECT_NE(run("truncated"),0); EXPECT_TRUE(fs::exists(root/"truncated/INCOMPLETE"));
    corpus({{1,"test","a",""},{1,"test","b",""}}); EXPECT_NE(run("ids"),0);
}
TEST_F(Zipf,GroupIdsMustMatchExactlyAndExtraRowsAreRejected) {
    corpus({{2,"test","hello",""}});
    for (const auto& value:{"1\tnews\n","2\tnews\n3\tnews\n","2\tbad/type\n",""}) {
        std::ofstream(root/"types.tsv")<<value;
        const auto name="bad"+std::to_string(std::strlen(value));
        EXPECT_NE(run(name.c_str(),{"--groups",(root/"types.tsv").string()}),0);
    }
}
TEST_F(Zipf,ExistingOutputAndInvalidOptionsCannotDestroyResults) {
    corpus({{1,"test","hello",""}}); ASSERT_EQ(run(),0);
    const auto before=read(root/"out/frequencies.tsv"); EXPECT_NE(run(),0);
    EXPECT_EQ(read(root/"out/frequencies.tsv"),before);
    for (const auto& args:std::vector<std::vector<std::string>>{
        {"--memory-mib","0"},{"--memory-mib","2049"},{"--field","bad"},
        {"--fit-min","20","--fit-max","10"},{"--unknown","x"},{"--field","text","--field","title"}})
        EXPECT_NE(run("invalid",args),0);
    EXPECT_FALSE(fs::exists(root/"invalid"));
}
} // namespace
