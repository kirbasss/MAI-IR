#include "boolean_index.hpp"
#include "boolean_search.hpp"
#include <gtest/gtest.h>
#include <nlohmann/json.hpp>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

namespace {
namespace fs=std::filesystem;
using U64=std::uint64_t;
void number(std::ostream& stream,U64 value,unsigned width=8) {
    for (unsigned i=0;i<width;++i) stream.put(static_cast<char>(value>>(8*i)));
}
void record(std::ostream& stream,U64 id,const std::string& title,const std::string& body) {
    number(stream,id); number(stream,4,4); stream.write("test",4);
    number(stream,title.size()); stream.write(title.data(),static_cast<std::streamsize>(title.size()));
    number(stream,body.size()); stream.write(body.data(),static_cast<std::streamsize>(body.size()));
}
std::string contents(FILE* file) {
    std::rewind(file); std::string value; char buffer[1024]; std::size_t n;
    while ((n=std::fread(buffer,1,sizeof(buffer),file))) value.append(buffer,n);
    return value;
}
struct Snippets : testing::Test {
    fs::path root;
    void SetUp() override {
        root=fs::temp_directory_path()/("mai-snippets-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        ASSERT_TRUE(fs::create_directory(root));
        std::ofstream corpus(root/"corpus.bin",std::ios::binary); corpus.write("MAIIR3\0\1",8);
        record(corpus,1,"Doom новости",u8"Doom снова здесь. В Doom Eternal вышло обновление. Конец.\n\nДругой блок.");
        record(corpus,2,"Пустой текст Doom","");
        record(corpus,3,"Doom <script>","Doom <script>alert(1)</script>! Другая заметка.");
        record(corpus,4,"Длинный текст",std::string(800,'x')+u8" Ёж и Doom");
        record(corpus,5,"Контекст",std::string(700,'l')+u8" контекст "+std::string(700,'r'));
        corpus.close();
        std::vector<std::string> args={"boolean_index","build","--corpus",(root/"corpus.bin").string(),
                                        "--output",(root/"index").string()};
        std::vector<const char*> argv; for (const auto& arg:args) argv.push_back(arg.c_str());
        FILE *out=std::tmpfile(),*err=std::tmpfile(); ASSERT_NE(out,nullptr); ASSERT_NE(err,nullptr);
        const int code=gaming::run_boolean_index(static_cast<int>(argv.size()),argv.data(),out,err);
        const std::string errors=contents(err); std::fclose(out); std::fclose(err);
        ASSERT_EQ(code,0)<<errors;
    }
    void TearDown() override { fs::remove_all(root); }
    std::pair<int,std::string> cli(std::vector<std::string> args) {
        args.insert(args.begin(),{"boolean_search","--index",(root/"index").string(),
                                    "--corpus",(root/"corpus.bin").string()});
        std::vector<const char*> argv; for (const auto& arg:args) argv.push_back(arg.c_str());
        FILE *in=std::tmpfile(),*out=std::tmpfile(),*err=std::tmpfile();
        const int code=gaming::run_boolean_search(static_cast<int>(argv.size()),argv.data(),in,out,err);
        const std::string response=contents(out)+contents(err);
        std::fclose(in); std::fclose(out); std::fclose(err); return {code,response};
    }
};
TEST_F(Snippets,ChoosesQueryDependentSentenceAndReturnsUtf8ByteSpans) {
    const auto [code,text]=cli({"--query","doom AND eternal"}); ASSERT_EQ(code,0)<<text;
    const auto row=nlohmann::json::parse(text)["results"][0];
    ASSERT_EQ(row["id"],1);
    const std::string snippet=row["snippet"];
    EXPECT_NE(snippet.find("Doom Eternal"),std::string::npos);
    EXPECT_NE(snippet.find("снова здесь"),std::string::npos);
    EXPECT_NE(snippet.find("Конец"),std::string::npos);
    EXPECT_EQ(snippet.find("Другой блок"),std::string::npos);
    ASSERT_EQ(row["snippet_matches"].size(),3U);
    for (const auto& match:row["snippet_matches"]) {
        const std::size_t begin=match[0],end=match[1];
        ASSERT_LE(end,snippet.size()); EXPECT_LT(begin,end);
        const std::string word=snippet.substr(begin,end-begin);
        EXPECT_TRUE(word=="Doom" || word=="Eternal");
    }
}
TEST_F(Snippets,OffsetsIndexMatchesDirectScanAndRejectsCorruption) {
    const auto [built,notice]=cli({"--build-snippet-index",(root/"offsets.bin").string()});
    ASSERT_EQ(built,0)<<notice;
    const auto [direct_code,direct]=cli({"--query","doom","--limit","3"}); ASSERT_EQ(direct_code,0)<<direct;
    const auto [indexed_code,indexed]=cli({"--query","doom","--limit","3",
                                           "--snippet-index",(root/"offsets.bin").string()});
    ASSERT_EQ(indexed_code,0)<<indexed;
    EXPECT_EQ(nlohmann::json::parse(direct)["results"],nlohmann::json::parse(indexed)["results"]);
    auto file=std::fstream(root/"offsets.bin",std::ios::in|std::ios::out|std::ios::binary);
    file.seekp(40); file.put('x'); file.close();
    const auto [bad_code,bad]=cli({"--query","doom","--snippet-index",(root/"offsets.bin").string()});
    EXPECT_NE(bad_code,0); EXPECT_NE(bad.find("checksum"),std::string::npos);
}
TEST_F(Snippets,TitleOnlyDocumentHasEmptySnippet) {
    const auto [code,text]=cli({"--query","Пустой","--limit","1"}); ASSERT_EQ(code,0)<<text;
    const auto row=nlohmann::json::parse(text)["results"][0];
    EXPECT_EQ(row["id"],2); EXPECT_EQ(row["snippet"],"");
    EXPECT_TRUE(row["snippet_matches"].empty());
}
TEST_F(Snippets,LongSentenceIsCutOnUtf8BoundaryNearMatch) {
    const auto [code,text]=cli({"--query",u8"ёж","--limit","1"}); ASSERT_EQ(code,0)<<text;
    const auto row=nlohmann::json::parse(text)["results"][0];
    EXPECT_EQ(row["id"],4);
    const std::string snippet=row["snippet"];
    EXPECT_LE(snippet.size(),600U);
    EXPECT_TRUE(row["snippet_left"]); EXPECT_FALSE(row["snippet_right"]);
    EXPECT_NE(snippet.find(u8"Ёж"),std::string::npos);
    ASSERT_EQ(row["snippet_matches"].size(),1U);
    const std::size_t begin=row["snippet_matches"][0][0],end=row["snippet_matches"][0][1];
    EXPECT_EQ(snippet.substr(begin,end-begin),u8"Ёж");
}
TEST_F(Snippets,ChangedCorpusTitleIsRejectedBeforeShowingSnippet) {
    auto corpus=std::fstream(root/"corpus.bin",std::ios::in|std::ios::out|std::ios::binary);
    corpus.seekp(32); corpus.put('X'); corpus.close();
    const auto [code,text]=cli({"--query","doom","--limit","1"});
    EXPECT_NE(code,0); EXPECT_NE(text.find("Snippet corpus title differs"),std::string::npos);
}
TEST_F(Snippets,LongSentenceKeepsContextOnBothSidesOfMatch) {
    const auto [code,text]=cli({"--query",u8"контекст","--limit","1"}); ASSERT_EQ(code,0)<<text;
    const auto row=nlohmann::json::parse(text)["results"][0];
    ASSERT_EQ(row["id"],5);
    const std::string snippet=row["snippet"];
    EXPECT_LE(snippet.size(),600U);
    EXPECT_TRUE(row["snippet_left"]); EXPECT_TRUE(row["snippet_right"]);
    EXPECT_NE(snippet.find(u8"контекст"),std::string::npos);
    EXPECT_EQ(row["snippet_matches"].size(),1U);
    EXPECT_EQ(snippet.front(),'l'); EXPECT_EQ(snippet.back(),'r');
}
TEST_F(Snippets,FiltersBeforeLimitingAndCountsMatchingDocuments) {
    std::ofstream filters(root/"filters.bin",std::ios::binary);
    filters.write("MAIFLT1\0",8); number(filters,5);
    // year, publication type (news=1, review=3), site (gamemag=1, pcgamer=6)
    const unsigned char rows[5][4]={{231,7,1,1},{232,7,3,6},{232,7,3,6},{231,7,1,1},{0,0,1,6}};
    for (const auto& row : rows)
        filters.write(reinterpret_cast<const char*>(row),4);
    filters.close();
    const auto [code,text]=cli({"--query","doom","--limit","1","--filters",(root/"filters.bin").string(),
                                "--filter-source","pcgamer","--filter-type","review","--filter-year","2024"});
    ASSERT_EQ(code,0)<<text;
    const auto result=nlohmann::json::parse(text);
    EXPECT_EQ(result["total"],2);
    ASSERT_EQ(result["results"].size(),1U);
    EXPECT_EQ(result["results"][0]["id"],2);
    const auto [bad,error]=cli({"--query","doom","--filter-year","2024"});
    EXPECT_NE(bad,0); EXPECT_NE(error.find("Filters require"),std::string::npos);
}
}
