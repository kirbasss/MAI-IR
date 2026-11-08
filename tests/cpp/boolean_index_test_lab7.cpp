#include "boolean_index.hpp"
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
#include <tuple>
#include <vector>

namespace {
namespace fs=std::filesystem;
using U64=std::uint64_t;
using Docs=std::vector<std::tuple<U64,std::string,std::string,std::string>>;
void num(std::ostream& f,U64 n,unsigned bytes=8) { for (unsigned i=0;i<bytes;++i) f.put(static_cast<char>(n>>(8*i))); }
std::string read(const fs::path& path) { std::ifstream f(path,std::ios::binary); return {std::istreambuf_iterator<char>(f),std::istreambuf_iterator<char>()}; }
void dictionary(const fs::path& path) {
    const std::map<std::string,std::vector<std::string>> pairs={
        {u8"ru:игры",{u8"игра"}},{u8"ru:игрой",{u8"игра"}},{u8"ru:стали",{u8"сталь",u8"стать"}}};
    std::ostringstream records,strings;
    for (const auto& [key,values]:pairs) {
        const auto ko=strings.str().size(); strings<<key; const auto vo=strings.str().size();
        for (const auto& v:values) { strings<<v; strings.put(0); }
        num(records,ko,4); num(records,key.size(),4); num(records,vo,4); num(records,strings.str().size()-vo,4);
    }
    const auto payload=records.str()+strings.str(); U64 hash=14695981039346656037ULL;
    for (unsigned char c:payload) { hash^=c; hash*=1099511628211ULL; }
    std::ofstream f(path,std::ios::binary); f.write("MAIIL4\0\1",8);
    num(f,pairs.size()); num(f,strings.str().size()); num(f,hash); f<<payload;
}
struct Index : testing::Test {
    fs::path root;
    void SetUp() override {
        root=fs::temp_directory_path()/ ("mai-bool-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        ASSERT_TRUE(fs::create_directory(root));
    }
    void TearDown() override { fs::remove_all(root); }
    void corpus(const Docs& docs) {
        std::ofstream f(root/"corpus.bin",std::ios::binary); f.write("MAIIR3\0\1",8);
        for (const auto& [id,source,title,text]:docs) {
            num(f,id); num(f,source.size(),4); f<<source;
            for (const auto& field:{title,text}) { num(f,field.size()); f<<field; }
        }
    }
    int run(std::vector<std::string> args) {
        args.insert(args.begin(),"boolean_index"); std::vector<const char*> argv;
        for (const auto& arg:args) argv.push_back(arg.c_str());
        FILE *out=std::tmpfile(),*err=std::tmpfile();
        const int code=gaming::run_boolean_index(static_cast<int>(argv.size()),argv.data(),out,err);
        std::fclose(out); std::fclose(err); return code;
    }
    int build(const char* name="index",std::vector<std::string> extra={}) {
        std::vector<std::string> args={"build","--corpus",(root/"corpus.bin").string(),"--output",(root/name).string()};
        args.insert(args.end(),extra.begin(),extra.end()); return run(args);
    }
    void open(gaming::BooleanIndex& index,const char* name="index") {
        const char* error=nullptr; ASSERT_TRUE(index.open((root/name).string().c_str(),&error))<<(error ? error : "");
    }
    std::vector<U64> ids(gaming::BooleanIndex& index,const std::string& term,gaming::TermSpace space=gaming::TermSpace::surface) {
        const char* error=nullptr; gaming::PostingList list;
        EXPECT_TRUE(index.lookup(term.data(),term.size(),space,list,&error))<<(error ? error : "");
        gaming::PostingCursor cursor; EXPECT_TRUE(cursor.open(index,list,&error));
        std::vector<U64> result; U64 id;
        while (cursor.next(id,&error)) result.push_back(id);
        EXPECT_EQ(error,nullptr); return result;
    }
};
TEST_F(Index,PostingListsAreUniqueSortedAndIncludeBothFields) {
    corpus({{1,"test","Doom doom","Doom game"},{2,"test","game","Minecraft"},{3,"test","update","Doom doom"}});
    ASSERT_EQ(build(),0); gaming::BooleanIndex index; open(index);
    EXPECT_EQ(ids(index,"doom"),(std::vector<U64>{1,3}));
    EXPECT_EQ(ids(index,"game"),(std::vector<U64>{1,2}));
    EXPECT_EQ(ids(index,"minecraft"),(std::vector<U64>{2})); EXPECT_TRUE(ids(index,"absent").empty());
    const char* error=nullptr; EXPECT_TRUE(index.verify(&error))<<(error ? error : "");
    EXPECT_EQ(index.documents(),3); EXPECT_EQ(index.terms(),4); EXPECT_EQ(index.postings(),6);
}
TEST_F(Index,Arbitrary64BitIdsTitleOnlyAndEmptyDocumentsRemainInUniverse) {
    corpus({{3,"test",u8"Игры",""},{UINT64_C(5000000000),"test","",""}});
    ASSERT_EQ(build(),0); gaming::BooleanIndex index; open(index);
    EXPECT_EQ(ids(index,u8"игры"),(std::vector<U64>{3}));
    const char* error=nullptr; gaming::DocumentInfo info;
    ASSERT_TRUE(index.document(5000000000ULL,info,&error)); EXPECT_EQ(info.id,5000000000ULL);
    EXPECT_TRUE(index.document(4,info,&error)); EXPECT_EQ(info.id,0);
    ASSERT_TRUE(index.document_at(0,info,&error)); EXPECT_EQ(info.id,3);
    ASSERT_TRUE(index.document_at(1,info,&error)); EXPECT_EQ(info.id,5000000000ULL);
    ASSERT_TRUE(index.document_at(2,info,&error)); EXPECT_EQ(info.id,0);
    EXPECT_TRUE(index.verify(&error))<<(error ? error : "");
}
TEST_F(Index,DocumentTitlesAndUrlsSurviveWithoutTheSourceCorpus) {
    corpus({{8,"stopgame",u8"Обзор Doom","game"}});
    { std::ofstream f(root/"urls.bin",std::ios::binary); f.write("MAIIU7\0\1",8); const std::string url="https://example.test/doom"; num(f,8); num(f,url.size(),4); f<<url; }
    ASSERT_EQ(build("index",{"--urls",(root/"urls.bin").string()}),0);
    fs::remove(root/"corpus.bin"); fs::remove(root/"urls.bin"); gaming::BooleanIndex index; open(index);
    const char* error=nullptr; gaming::DocumentInfo d; ASSERT_TRUE(index.document(8,d,&error));
    std::string title(d.title_size,' '),url(d.url_size,' '),source(d.source_size,' ');
    ASSERT_TRUE(index.text(d.title_offset,title.size(),title.data(),&error));
    ASSERT_TRUE(index.text(d.url_offset,url.size(),url.data(),&error));
    ASSERT_TRUE(index.text(d.source_offset,source.size(),source.data(),&error));
    EXPECT_EQ(title,u8"Обзор Doom"); EXPECT_EQ(url,"https://example.test/doom"); EXPECT_EQ(source,"stopgame");
}
TEST_F(Index,LemmaNamespaceExpandsAmbiguityWithoutDuplicatingDocumentIds) {
    corpus({{1,"test",u8"Игры игрой стали стали PS5",""},{2,"test",u8"Игры",""}}); dictionary(root/"dictionary.bin");
    ASSERT_EQ(build("index",{"--dict",(root/"dictionary.bin").string()}),0);
    gaming::BooleanIndex index; open(index); ASSERT_TRUE(index.has_lemmas());
    EXPECT_EQ(ids(index,u8"игры"),(std::vector<U64>{1,2}));
    EXPECT_TRUE(ids(index,u8"игра").empty());
    EXPECT_EQ(ids(index,u8"игра",gaming::TermSpace::lemma),(std::vector<U64>{1,2}));
    EXPECT_EQ(ids(index,u8"сталь",gaming::TermSpace::lemma),(std::vector<U64>{1}));
    EXPECT_EQ(ids(index,u8"стать",gaming::TermSpace::lemma),(std::vector<U64>{1}));
    EXPECT_EQ(ids(index,"ps5",gaming::TermSpace::lemma),(std::vector<U64>{1}));
    const char* error=nullptr; EXPECT_TRUE(index.verify(&error))<<(error ? error : "");
}
TEST_F(Index,IndependentCursorsDoNotChangeEachOthersPositions) {
    corpus({{1,"test","a b",""},{2,"test","a b",""},{3,"test","b",""}}); ASSERT_EQ(build(),0);
    gaming::BooleanIndex index; open(index); const char* error=nullptr; gaming::PostingList a,b;
    ASSERT_TRUE(index.lookup("a",1,gaming::TermSpace::surface,a,&error)); ASSERT_TRUE(index.lookup("b",1,gaming::TermSpace::surface,b,&error));
    gaming::PostingCursor x,y; ASSERT_TRUE(x.open(index,a,&error)); ASSERT_TRUE(y.open(index,b,&error));
    U64 id; ASSERT_TRUE(x.next(id,&error)); EXPECT_EQ(id,1); ASSERT_TRUE(y.next(id,&error)); EXPECT_EQ(id,1);
    ASSERT_TRUE(y.next(id,&error)); EXPECT_EQ(id,2); ASSERT_TRUE(x.next(id,&error)); EXPECT_EQ(id,2);
    EXPECT_FALSE(x.next(id,&error)); EXPECT_EQ(error,nullptr); ASSERT_TRUE(y.next(id,&error)); EXPECT_EQ(id,3);
}
TEST_F(Index,MultipleMergePassesAndCrossRunDuplicatesProduceIdenticalIndexFiles) {
    std::string text;
    for (unsigned pass=0;pass<2;++pass) for (unsigned i=0;i<420000;++i) text+="word"+std::to_string(i)+" ";
    corpus({{1,"test","",text},{2,"test","word1 word7",""}});
    ASSERT_EQ(build("small",{"--memory-mib","1"}),0);
    const auto stats=nlohmann::json::parse(read(root/"small/statistics.json"));
    EXPECT_GT(stats["initial_runs"].get<unsigned>(),24u); EXPECT_GE(stats["merge_passes"].get<unsigned>(),2u);
    ASSERT_EQ(build("large",{"--memory-mib","32"}),0);
    for (const char* name:{"terms.bin","postings.bin","documents.bin","strings.bin"})
        EXPECT_EQ(read(root/"small"/name),read(root/"large"/name))<<name;
    gaming::BooleanIndex index; open(index,"small"); EXPECT_EQ(index.terms(),420000);
    EXPECT_EQ(ids(index,"word1"),(std::vector<U64>{1,2}));
    const char* error=nullptr; EXPECT_TRUE(index.verify(&error))<<(error ? error : "");
}
TEST_F(Index,CorruptionTruncationAndIncompleteMarkersAreRejected) {
    corpus({{1,"test","doom",""}}); ASSERT_EQ(build(),0);
    { std::ofstream(root/"index/INCOMPLETE")<<"unfinished"; }
    gaming::BooleanIndex index; const char* error=nullptr;
    EXPECT_FALSE(index.open((root/"index").string().c_str(),&error)); fs::remove(root/"index/INCOMPLETE");
    { std::fstream f(root/"index/postings.bin",std::ios::binary|std::ios::in|std::ios::out); f.seekp(80); f.put(2); }
    open(index); EXPECT_FALSE(index.verify(&error));
    fs::resize_file(root/"index/terms.bin",81); EXPECT_FALSE(index.open((root/"index").string().c_str(),&error));
    gaming::PostingList list; EXPECT_FALSE(index.lookup("doom",4,gaming::TermSpace::surface,list,&error));
}
TEST_F(Index,MalformedCorporaAndMismatchingUrlCatalogRemainIncomplete) {
    corpus({{1,"test","valid",std::string("\xc0\x80",2)}});
    EXPECT_NE(build("utf8"),0); EXPECT_TRUE(fs::exists(root/"utf8/INCOMPLETE"));
    corpus({{1,"test","a",""},{1,"test","b",""}}); EXPECT_NE(build("ids"),0);
    corpus({{2,"test","a",""}});
    { std::ofstream f(root/"urls.bin",std::ios::binary); f.write("MAIIU7\0\1",8); num(f,1); num(f,1,4); f<<"a"; }
    EXPECT_NE(build("urls",{"--urls",(root/"urls.bin").string()}),0);
    EXPECT_TRUE(fs::exists(root/"urls/INCOMPLETE"));
}
TEST_F(Index,EmptyIndexAndUnavailableLemmaNamespaceAreHandled) {
    corpus({}); ASSERT_EQ(build(),0); gaming::BooleanIndex index; open(index);
    EXPECT_TRUE(ids(index,"doom").empty()); EXPECT_EQ(index.documents(),0);
    const char* error=nullptr; EXPECT_TRUE(index.verify(&error)); gaming::PostingList list;
    EXPECT_FALSE(index.lookup("doom",4,gaming::TermSpace::lemma,list,&error));
}
TEST_F(Index,ExistingResultsAndDiskReserveAreProtected) {
    corpus({{1,"test","doom",""}}); ASSERT_EQ(build(),0);
    const auto before=read(root/"index/terms.bin"); EXPECT_NE(build(),0); EXPECT_EQ(read(root/"index/terms.bin"),before);
    EXPECT_NE(build("reserve",{"--reserve-mib","1000000000"}),0);
    EXPECT_TRUE(fs::exists(root/"reserve/INCOMPLETE"));
    EXPECT_NE(build("invalid",{"--memory-mib","0"}),0); EXPECT_FALSE(fs::exists(root/"invalid"));
}
} // namespace
