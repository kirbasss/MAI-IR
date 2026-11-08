#include "boolean_search.hpp"
#include <gtest/gtest.h>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

namespace {
namespace fs=std::filesystem;
using U64=std::uint64_t;
void num(std::ostream& out,U64 value,unsigned width=8) {
    for (unsigned i=0;i<width;++i) out.put(static_cast<char>(value>>(8*i)));
}
U64 value(const char* bytes,unsigned width=8) {
    U64 result=0; for (unsigned i=0;i<width;++i) result|=U64(static_cast<unsigned char>(bytes[i]))<<(8*i); return result;
}
int command(const std::vector<std::string>& args,bool positional=false,bool compact=false) {
    std::vector<const char*> argv; argv.push_back(compact ? "compact_index" : positional ? "positional_index" : "boolean_index");
    for (const auto& arg:args) argv.push_back(arg.c_str());
    FILE *out=std::tmpfile(),*err=std::tmpfile();
    const int code=compact ? gaming::run_compact_index(static_cast<int>(argv.size()),argv.data(),out,err) :
        positional ? gaming::run_positional_index(static_cast<int>(argv.size()),argv.data(),out,err) :
                     gaming::run_boolean_index(static_cast<int>(argv.size()),argv.data(),out,err);
    std::fclose(out); std::fclose(err); return code;
}
struct Compression:testing::Test {
    fs::path root;
    std::string path(const char* name) const { return (root/name).string(); }
    void SetUp() override {
        root=fs::temp_directory_path()/("mai-compress-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        ASSERT_TRUE(fs::create_directory(root));
        std::ofstream corpus(path("corpus.bin"),std::ios::binary); corpus.write("MAIIR3\0\1",8);
        const auto add=[&](U64 id,const std::string& title,const std::string& body) {
            num(corpus,id); num(corpus,4,4); corpus<<"test"; num(corpus,title.size()); corpus<<title;
            num(corpus,body.size()); corpus<<body;
        };
        add(3,"Doom",u8"игры red red"); add(8,"Doom Eternal",u8"игрой doom eternal red blue");
        add(5000000000ULL,"Eternal Doom",u8"игры doom red blue red"); corpus.close();
        std::ostringstream records,strings; const std::map<std::string,std::string> dict={{u8"ru:игрой",u8"игра"},{u8"ru:игры",u8"игра"}};
        for (const auto& [key,value]:dict) {
            const auto ko=strings.str().size(); strings<<key; const auto vo=strings.str().size(); strings<<value; strings.put(0);
            num(records,ko,4); num(records,key.size(),4); num(records,vo,4); num(records,strings.str().size()-vo,4);
        }
        const auto payload=records.str()+strings.str(); U64 checksum=14695981039346656037ULL;
        for (unsigned char byte:payload) { checksum^=byte; checksum*=1099511628211ULL; }
        std::ofstream dictionary(path("dict.bin"),std::ios::binary); dictionary.write("MAIIL4\0\1",8);
        num(dictionary,dict.size()); num(dictionary,strings.str().size()); num(dictionary,checksum); dictionary<<payload; dictionary.close();
        ASSERT_EQ(command({"build","--corpus",path("corpus.bin"),"--output",path("index"),"--dict",path("dict.bin")}),0);
        ASSERT_EQ(command({"build","--corpus",path("corpus.bin"),"--index",path("index"),"--output",path("positions"),"--dict",path("dict.bin"),"--memory-mib","1"},true),0);
        ASSERT_EQ(command({"build","--index",path("index"),"--positions",path("positions"),"--output",path("compact"),"--reserve-mib","0"},false,true),0);
    }
    void TearDown() override { fs::remove_all(root); }
    std::vector<U64> query(const char* text,bool lemma,bool compressed) {
        gaming::BooleanSearch engine; const char* error=nullptr;
        EXPECT_TRUE(engine.open(path("index").c_str(),lemma ? gaming::TermSpace::lemma : gaming::TermSpace::surface,
                                path("dict.bin").c_str(),&error,path("positions").c_str(),compressed ? path("compact").c_str() : nullptr))<<(error ? error : "");
        gaming::BooleanQuery q; EXPECT_TRUE(q.compile(engine,text,&error))<<(error ? error : "");
        std::vector<U64> ids; U64 id=0; while (q.next(id,&error)) ids.push_back(id);
        EXPECT_EQ(error,nullptr); return ids;
    }
};
TEST_F(Compression,AllPostingsAndCoordinatesEqualOriginal) {
    EXPECT_EQ(command({"verify","--index",path("index"),"--positions",path("positions"),"--compact",path("compact")},false,true),0);
    for (bool lemma:{false,true}) for (const char* query_text:{"doom","doom AND eternal","NOT doom","doom OR red",
            "\"doom eternal\"","\"red red\"","\"red blue red\"",u8"игры",u8"\"игры doom\""}) {
        EXPECT_EQ(query(query_text,lemma,true),query(query_text,lemma,false))<<query_text;
    }
    EXPECT_EQ(query(u8"игры",true,true),(std::vector<U64>{3,8,5000000000ULL}));
    EXPECT_EQ(query("\"doom eternal\"",false,true),(std::vector<U64>{8}));
    gaming::BooleanIndex source; const char* error=nullptr;
    ASSERT_TRUE(source.open(path("index").c_str(),&error));
    gaming::PostingList doom; ASSERT_TRUE(source.lookup("doom",4,gaming::TermSpace::surface,doom,&error));
    ASSERT_NE(doom.ordinal,UINT64_MAX);
    std::ifstream table(path("compact/compact_posting_terms.bin"),std::ios::binary);
    char entry[16]; table.seekg(static_cast<std::streamoff>(80+doom.ordinal*16)); ASSERT_TRUE(static_cast<bool>(table.read(entry,16)));
    std::ifstream payload(path("compact/compact_postings.bin"),std::ios::binary);
    payload.seekg(static_cast<std::streamoff>(80+value(entry)));
    char first=0; ASSERT_TRUE(static_cast<bool>(payload.get(first)));
    EXPECT_EQ(static_cast<unsigned char>(first),0x83U); // lecture VB: doc ID 3, final-byte bit set
}
TEST_F(Compression,BenchmarkChecksMemoryDecodingAndProductionCursorsInBothSpaces) {
    for (const char* space:{"surface","lemma"}) {
        EXPECT_EQ(command({"benchmark","--index",path("index"),"--positions",path("positions"),
                           "--compact",path("compact"),"--term","doom","--space",space,"--repeats","2"},false,true),0);
    }
    EXPECT_NE(command({"benchmark","--index",path("index"),"--positions",path("positions"),
                       "--compact",path("compact"),"--term","missing"},false,true),0);
}
TEST_F(Compression,FormatIsSmallerAndExistingOutputIsProtected) {
    EXPECT_LT(fs::file_size(root/"compact/compact_postings.bin"),fs::file_size(root/"index/postings.bin"));
    EXPECT_LT(fs::file_size(root/"compact/compact_positions.bin"),fs::file_size(root/"positions/positions.bin"));
    EXPECT_NE(command({"build","--index",path("index"),"--positions",path("positions"),"--output",path("compact"),"--reserve-mib","0"},false,true),0);
    EXPECT_NE(command({"build","--index",path("index"),"--positions",path("positions"),"--output",path("reserve"),"--reserve-mib","1000000000"},false,true),0);
    EXPECT_TRUE(fs::exists(root/"reserve/INCOMPLETE"));
}
TEST_F(Compression,EmptyCorpusProducesValidEmptyStreams) {
    { std::ofstream corpus(path("empty.bin"),std::ios::binary); corpus.write("MAIIR3\0\1",8); }
    ASSERT_EQ(command({"build","--corpus",path("empty.bin"),"--output",path("empty_index")}),0);
    ASSERT_EQ(command({"build","--corpus",path("empty.bin"),"--index",path("empty_index"),"--output",path("empty_positions"),"--memory-mib","1"},true),0);
    ASSERT_EQ(command({"build","--index",path("empty_index"),"--positions",path("empty_positions"),"--output",path("empty_compact"),"--reserve-mib","0"},false,true),0);
    ASSERT_EQ(command({"verify","--index",path("empty_index"),"--positions",path("empty_positions"),"--compact",path("empty_compact")},false,true),0);
    gaming::BooleanSearch engine; const char* error=nullptr;
    ASSERT_TRUE(engine.open(path("empty_index").c_str(),gaming::TermSpace::surface,nullptr,&error,path("empty_positions").c_str(),path("empty_compact").c_str()))<<(error ? error : "");
    gaming::BooleanQuery q; ASSERT_TRUE(q.compile(engine,"NOT doom",&error)); U64 id=0;
    EXPECT_FALSE(q.next(id,&error)); EXPECT_EQ(error,nullptr);
    ASSERT_EQ(command({"pack","--index",path("empty_index"),"--positions",path("empty_positions"),
                       "--compact",path("empty_compact"),"--output",path("empty_packed"),"--reserve-mib","0"},false,true),0);
    EXPECT_EQ(command({"verify-packed","--index",path("empty_index"),"--positions",path("empty_positions"),"--packed",path("empty_packed")},false,true),0);
}
TEST_F(Compression,PackedIndexSearchesWithoutRawPostingOrCoordinatePayloads) {
    ASSERT_EQ(command({"pack","--index",path("index"),"--positions",path("positions"),
                       "--compact",path("compact"),"--output",path("packed"),"--reserve-mib","0"},false,true),0);
    ASSERT_EQ(command({"verify-packed","--index",path("index"),"--positions",path("positions"),"--packed",path("packed")},false,true),0);
    EXPECT_EQ(fs::file_size(root/"packed/index/postings.bin"),80);
    EXPECT_EQ(fs::file_size(root/"packed/positions/positions.bin"),80);
    EXPECT_EQ(fs::file_size(root/"packed/index/terms.bin"),80);
    EXPECT_LT(fs::file_size(root/"packed/index/strings.bin"),fs::file_size(root/"index/strings.bin"));
    EXPECT_GT(fs::file_size(root/"packed/compact/compact_vocabulary.bin"),80);
    EXPECT_GT(fs::file_size(root/"packed/compact/compact_vocabulary_blocks.bin"),80);
    EXPECT_LT(fs::file_size(root/"packed/index/postings.bin")+fs::file_size(root/"packed/compact/compact_postings.bin"),
              fs::file_size(root/"index/postings.bin"));
    gaming::BooleanSearch engine; const char* error=nullptr;
    ASSERT_TRUE(engine.open(path("packed/index").c_str(),gaming::TermSpace::lemma,path("dict.bin").c_str(),&error,
                            path("packed/positions").c_str(),path("packed/compact").c_str()))<<(error ? error : "");
    for (const char* query_text:{u8"игры",u8"\"игры doom\"","doom AND eternal","\"missing doom\""}) {
        gaming::BooleanQuery q; ASSERT_TRUE(q.compile(engine,query_text,&error))<<(error ? error : "");
        std::vector<U64> ids; U64 id=0; while (q.next(id,&error)) ids.push_back(id);
        EXPECT_EQ(error,nullptr); EXPECT_EQ(ids,query(query_text,true,false))<<query_text;
    }
    gaming::BooleanIndex original; ASSERT_TRUE(original.open(path("index").c_str(),&error));
    std::ifstream terms(path("index/terms.bin"),std::ios::binary),strings(path("index/strings.bin"),std::ios::binary);
    terms.seekg(80);
    for (U64 ordinal=0;ordinal<original.terms();++ordinal) {
        char record[40]; ASSERT_TRUE(static_cast<bool>(terms.read(record,40)));
        const auto space=value(record,4) ? gaming::TermSpace::lemma : gaming::TermSpace::surface;
        const auto size=value(record+4,4); std::string word(static_cast<std::size_t>(size),'\0');
        strings.seekg(static_cast<std::streamoff>(80+value(record+8)));
        ASSERT_TRUE(static_cast<bool>(strings.read(word.data(),static_cast<std::streamsize>(word.size()))));
        gaming::PostingList raw,packed;
        ASSERT_TRUE(original.lookup(word.data(),word.size(),space,raw,&error));
        ASSERT_TRUE(engine.index().lookup(word.data(),word.size(),space,packed,&error))<<(error ? error : "");
        EXPECT_EQ(raw.offset,packed.offset); EXPECT_EQ(raw.count,packed.count); EXPECT_EQ(packed.ordinal,ordinal);
    }
    const auto vocabulary=root/"packed/compact/compact_vocabulary.bin";
    const auto before=fs::file_size(vocabulary); fs::resize_file(vocabulary,before-1);
    gaming::BooleanSearch damaged;
    EXPECT_FALSE(damaged.open(path("packed/index").c_str(),gaming::TermSpace::surface,nullptr,&error,
                              path("packed/positions").c_str(),path("packed/compact").c_str()));
    gaming::BooleanIndex raw_only; EXPECT_FALSE(raw_only.open(path("packed/index").c_str(),&error));
}
TEST_F(Compression,PackedRootIncompleteMarkerBlocksSearchAndVerification) {
    ASSERT_EQ(command({"pack","--index",path("index"),"--positions",path("positions"),
                       "--compact",path("compact"),"--output",path("packed"),"--reserve-mib","0"},false,true),0);
    { std::ofstream marker(path("packed/INCOMPLETE")); marker<<"Incomplete\n"; }
    for (bool lemma:{false,true}) {
        gaming::BooleanSearch engine; const char* error=nullptr;
        EXPECT_FALSE(engine.open(path("packed/index").c_str(),lemma ? gaming::TermSpace::lemma : gaming::TermSpace::surface,
                                 path("dict.bin").c_str(),&error,path("packed/positions").c_str(),path("packed/compact/").c_str()));
        ASSERT_NE(error,nullptr);
        EXPECT_STREQ(error,"Packed index is incomplete");
    }
    EXPECT_NE(command({"verify-packed","--index",path("index"),"--positions",path("positions"),"--packed",path("packed")},false,true),0);
    ASSERT_TRUE(fs::remove(root/"packed/INCOMPLETE"));
    EXPECT_EQ(command({"verify-packed","--index",path("index"),"--positions",path("positions"),"--packed",path("packed")},false,true),0);
    gaming::BooleanSearch completed; const char* error=nullptr;
    EXPECT_TRUE(completed.open(path("packed/index").c_str(),gaming::TermSpace::surface,nullptr,&error,
                               path("packed/positions").c_str(),path("packed/compact").c_str()))<<(error ? error : "");
}
TEST_F(Compression,TruncationCorruptionAndWrongBindingAreRejected) {
    const auto file=root/"compact/compact_positions.bin"; const auto old=fs::file_size(file);
    fs::resize_file(file,old-1);
    gaming::BooleanSearch engine; const char* error=nullptr;
    EXPECT_FALSE(engine.open(path("index").c_str(),gaming::TermSpace::surface,nullptr,&error,path("positions").c_str(),path("compact").c_str()));
    fs::resize_file(file,old);
    EXPECT_NE(command({"verify","--index",path("index"),"--positions",path("positions"),"--compact",path("compact")},false,true),0);
    EXPECT_FALSE(engine.open(path("index").c_str(),gaming::TermSpace::surface,nullptr,&error,nullptr,path("compact").c_str()));
    {
        std::ofstream corpus(path("other.bin"),std::ios::binary); corpus.write("MAIIR3\0\1",8);
        num(corpus,19); num(corpus,4,4); corpus<<"test"; num(corpus,4); corpus<<"Doom"; num(corpus,4); corpus<<"game";
    }
    ASSERT_EQ(command({"build","--corpus",path("other.bin"),"--output",path("other_index")}),0);
    ASSERT_EQ(command({"build","--corpus",path("other.bin"),"--index",path("other_index"),"--output",path("other_positions"),"--memory-mib","1"},true),0);
    EXPECT_FALSE(engine.open(path("other_index").c_str(),gaming::TermSpace::surface,nullptr,&error,path("other_positions").c_str(),path("compact").c_str()));
}
TEST(CompactCodec,RejectsNoncanonicalOverflowAndTruncation) {
    const std::vector<std::pair<std::string,U64>> good={{std::string("\x80",1),0},{std::string("\xff",1),127},
        {std::string("\x00\x81",2),128},{std::string("\x7f\x7f\x7f\x7f\x7f\x7f\x7f\x7f\x7f\x81",10),UINT64_MAX}};
    for (const auto& [bytes,value]:good) {
        FILE* file=std::tmpfile(); ASSERT_NE(file,nullptr); std::fwrite(bytes.data(),1,bytes.size(),file); std::rewind(file);
        U64 left=bytes.size(),actual=0; EXPECT_TRUE(gaming::read_compact_uint(file,left,actual)); EXPECT_EQ(actual,value); EXPECT_EQ(left,0); std::fclose(file);
        const auto* memory=reinterpret_cast<const unsigned char*>(bytes.data()); left=bytes.size(); actual=0;
        EXPECT_TRUE(gaming::read_compact_uint(memory,left,actual)); EXPECT_EQ(actual,value); EXPECT_EQ(left,0);
        EXPECT_EQ(memory,reinterpret_cast<const unsigned char*>(bytes.data())+bytes.size());
    }
    for (const auto& bytes:{std::string("\x00\x80",2),std::string("\0",1),
                            std::string("\x7f\x7f\x7f\x7f\x7f\x7f\x7f\x7f\x7f\x82",10)}) {
        FILE* file=std::tmpfile(); ASSERT_NE(file,nullptr); std::fwrite(bytes.data(),1,bytes.size(),file); std::rewind(file);
        U64 left=bytes.size(),actual=0; EXPECT_FALSE(gaming::read_compact_uint(file,left,actual)); std::fclose(file);
        const auto* memory=reinterpret_cast<const unsigned char*>(bytes.data()); left=bytes.size(); actual=0;
        EXPECT_FALSE(gaming::read_compact_uint(memory,left,actual));
    }
}
}
