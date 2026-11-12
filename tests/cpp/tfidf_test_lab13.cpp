#include "tfidf.hpp"
#include <gtest/gtest.h>
#include <nlohmann/json.hpp>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <vector>
#include <map>
#include <cmath>
#include <chrono>
#include <algorithm>

namespace {
namespace fs=std::filesystem;
using U64=std::uint64_t;
void num(std::ostream& out,U64 n,unsigned width=8) { for (unsigned i=0;i<width;++i) out.put(static_cast<char>(n>>(8*i))); }
int command(const std::vector<std::string>& args,unsigned kind,std::string* text=nullptr,const std::string& input="") {
    std::vector<const char*> argv{"test"}; for (const auto& a:args) argv.push_back(a.c_str());
    FILE *out=std::tmpfile(),*err=std::tmpfile(),*in=std::tmpfile();
    std::fwrite(input.data(),1,input.size(),in); std::rewind(in);
    const int code=kind==0 ? gaming::run_boolean_index(static_cast<int>(argv.size()),argv.data(),out,err) :
        kind==1 ? gaming::run_positional_index(static_cast<int>(argv.size()),argv.data(),out,err) :
        kind==2 ? gaming::run_tfidf_index(static_cast<int>(argv.size()),argv.data(),out,err) :
        kind==3 ? gaming::run_boolean_search(static_cast<int>(argv.size()),argv.data(),in,out,err) :
                  gaming::run_compact_index(static_cast<int>(argv.size()),argv.data(),out,err);
    if (text) { std::rewind(out); text->clear(); int c; while ((c=std::fgetc(out))!=EOF) text->push_back(static_cast<char>(c)); }
    if (code && kind!=3) { std::rewind(err); int c; while ((c=std::fgetc(err))!=EOF) std::fputc(c,stderr); }
    std::fclose(out); std::fclose(err); std::fclose(in); return code;
}
struct TfidfTests:testing::Test {
    fs::path root;
    struct Doc { U64 id; std::string title,body; };
    // title/body frequencies combine; full document norms include nonquery words.
    std::vector<Doc> docs{{3,"car","insurance auto insurance common"},
        {19,"car","car common"},{5000000000ULL,"insurance","best rare common"},
        {UINT64_MAX-1,"car","car common"},{UINT64_MAX,"",""}};
    bool lemmas=false;
    std::string p(const char* name) const { return (root/name).string(); }
    void SetUp() override {
        root=fs::temp_directory_path()/("mai_tfidf_"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        fs::create_directory(root); build();
    }
    void TearDown() override { fs::remove_all(root); }
    void build() {
        if (lemmas) {
            const std::map<std::string,std::vector<std::string>> entries{{"n:cars",{"car"}},{"n:vehicles",{"auto","car"}}};
            std::ostringstream records,strings;
            for (const auto& entry:entries) {
                const auto key=strings.str().size(); strings<<entry.first; const auto values=strings.str().size();
                for (const auto& v:entry.second) { strings<<v; strings.put(0); }
                num(records,key,4); num(records,entry.first.size(),4); num(records,values,4); num(records,strings.str().size()-values,4);
            }
            const auto payload=records.str()+strings.str(); U64 checksum=14695981039346656037ULL;
            for (unsigned char c:payload) { checksum^=c; checksum*=1099511628211ULL; }
            std::ofstream dict(p("dict.bin"),std::ios::binary); dict.write("MAIIL4\0\1",8); num(dict,entries.size()); num(dict,strings.str().size()); num(dict,checksum); dict<<payload;
        }
        std::ofstream corpus(p("corpus.bin"),std::ios::binary); corpus.write("MAIIR3\0\1",8);
        for (const auto& doc:docs) {
            num(corpus,doc.id); num(corpus,4,4); corpus<<"test";
            num(corpus,doc.title.size()); corpus<<doc.title; num(corpus,doc.body.size()); corpus<<doc.body;
        }
        corpus.close();
        std::vector<std::string> index_args{"build","--corpus",p("corpus.bin"),"--output",p("index"),"--reserve-mib","1"};
        std::vector<std::string> pos_args{"build","--corpus",p("corpus.bin"),"--index",p("index"),"--output",p("positions"),"--reserve-mib","1","--memory-mib","1"};
        if (lemmas) { index_args.insert(index_args.end(),{"--dict",p("dict.bin")}); pos_args.insert(pos_args.end(),{"--dict",p("dict.bin")}); }
        ASSERT_EQ(command(index_args,0),0);
        ASSERT_EQ(command(pos_args,1),0);
        ASSERT_EQ(command({"build","--index",p("index"),"--positions",p("positions"),"--output",p("norms"),"--reserve-mib","0"},2),0);
    }
    std::vector<std::string> cli(bool packed=false) const {
        return {"--index",p(packed ? "packed/index" : "index"),"--positions",p(packed ? "packed/positions" : "positions"),
            "--ranking","tfidf","--norms",p("norms")};
    }
    gaming::RankedResult search(const std::string& query,unsigned limit=1000) {
        gaming::BooleanSearch engine; gaming::Tfidf rank; const char* error=nullptr;
        EXPECT_TRUE(engine.open(p("index").c_str(),gaming::TermSpace::surface,nullptr,&error,p("positions").c_str()));
        EXPECT_TRUE(rank.open(p("norms").c_str(),p("index").c_str(),p("positions").c_str(),engine,&error));
        gaming::RankedResult result;
        EXPECT_TRUE(rank.search(engine,query.c_str(),limit,result,&error))<<(error ? error : ""); return result;
    }
    std::vector<gaming::RankedHit> reference(const std::string& query) {
        std::vector<std::map<std::string,unsigned>> counts;
        std::map<std::string,unsigned> df,q;
        for (const auto& doc:docs) {
            std::istringstream input(doc.title+" "+doc.body); std::string term; counts.emplace_back();
            while (input>>term) ++counts.back()[term];
            for (const auto& entry:counts.back()) ++df[entry.first];
        }
        std::istringstream input(query); std::string term; while (input>>term) if (df[term]) ++q[term];
        std::map<std::string,double> weights; double qnorm=0;
        for (const auto& entry:q) { const double w=(1+std::log10(entry.second))*std::log10(double(docs.size())/df[entry.first]); weights[entry.first]=w; qnorm+=w*w; }
        std::vector<gaming::RankedHit> hits;
        for (unsigned i=0;i<docs.size();++i) {
            double norm=0,dot=0;
            for (const auto& entry:counts[i]) { const double w=1+std::log10(entry.second); norm+=w*w; dot+=w*weights[entry.first]; }
            if (dot>0) hits.push_back({docs[i].id,dot/std::sqrt(norm*qnorm)});
        }
        std::sort(hits.begin(),hits.end(),[](auto a,auto b) { return a.score>b.score || (a.score==b.score && a.id<b.id); }); return hits;
    }
};
TEST_F(TfidfTests,IndependentCosineReferenceIncludingRepeatedQueriesAndOov) {
    for (const auto& query:{"car insurance","car car insurance","rare common","insurance","auto best","car unknown","common"}) {
        const auto expected=reference(query); const auto actual=search(query);
        ASSERT_EQ(actual.total,expected.size())<<query; ASSERT_EQ(actual.count,expected.size());
        for (unsigned i=0;i<expected.size();++i) { EXPECT_EQ(actual.hits[i].id,expected[i].id)<<query; EXPECT_NEAR(actual.hits[i].score,expected[i].score,1e-12)<<query; }
    }
}
TEST_F(TfidfTests,TopKIsExactPrefixAndTiesUseOriginalUint64Ids) {
    const auto all=search("car"); ASSERT_EQ(all.count,3U);
    EXPECT_EQ(all.hits[0].id,19U); EXPECT_EQ(all.hits[1].id,UINT64_MAX-1);
    for (unsigned k=0;k<=5;++k) {
        const auto result=search("car",k); EXPECT_EQ(result.total,3U); ASSERT_EQ(result.count,std::min(k,3U));
        for (unsigned i=0;i<result.count;++i) EXPECT_EQ(result.hits[i].id,all.hits[i].id);
    }
}
TEST_F(TfidfTests,FullVocabularyNormPenalizesAdditionalNonqueryTerms) {
    const auto result=search("car"); ASSERT_EQ(result.count,3U);
    EXPECT_GT(result.hits[0].score,result.hits[2].score);
    // First doc: car TF=1; insurance TF=2; auto=1; common=1.
    EXPECT_NEAR(result.hits[2].score,1/std::sqrt(3+std::pow(1+std::log10(2),2)),1e-12);
}
TEST_F(TfidfTests,NoMatchesAndZeroIdfDoNotProduceNanOrZeroScoreResults) {
    EXPECT_EQ(search("unknown").count,0U);
    // A nonempty universal corpus: every indexed term has df=N and IDF=0.
    fs::remove_all(root); fs::create_directory(root); docs={{1,"same","same"},{2,"same","same"}}; build();
    EXPECT_EQ(search("same same").total,0U);
}
TEST_F(TfidfTests,EmptyCorpusHasValidNormsAndEmptyResults) {
    fs::remove_all(root); fs::create_directory(root); docs.clear(); build();
    EXPECT_EQ(search("car").total,0U);
}
TEST_F(TfidfTests,PackedAndRawHaveIdenticalScoresAndNormBinding) {
    ASSERT_EQ(command({"build","--index",p("index"),"--positions",p("positions"),"--output",p("compact"),"--reserve-mib","0"},4),0);
    ASSERT_EQ(command({"pack","--index",p("index"),"--positions",p("positions"),"--compact",p("compact"),"--output",p("packed"),"--reserve-mib","0"},4),0);
    auto args=cli(true); args.insert(args.end(),{"--compact",p("packed/compact"),"--query","car insurance"});
    std::string output; ASSERT_EQ(command(args,3,&output),0);
    const auto row=nlohmann::json::parse(output); const auto expected=reference("car insurance");
    ASSERT_EQ(row["results"].size(),expected.size());
    for (unsigned i=0;i<expected.size();++i) { EXPECT_EQ(row["results"][i]["id"],expected[i].id); EXPECT_NEAR(row["results"][i]["score"].get<double>(),expected[i].score,1e-11); }
    auto quoted=cli(true); quoted.insert(quoted.end(),{"--compact",p("packed/compact"),"--query","\"car common\""});
    ASSERT_EQ(command(quoted,3,&output),0);
    const auto phrase=nlohmann::json::parse(output);
    EXPECT_EQ(phrase["total"],2);
    EXPECT_EQ(phrase["results"][0]["id"],19);
}
TEST_F(TfidfTests,StdinJsonlRecoversAfterErrorsAndValidatesUtf8AndLimits) {
    auto args=cli(); std::string output;
    ASSERT_EQ(command(args,3,&output,"car insurance\n!!!\ncar\n"),1);
    std::istringstream lines(output); std::string line; std::getline(lines,line); EXPECT_EQ(nlohmann::json::parse(line)["ranking"],"tfidf");
    std::getline(lines,line); EXPECT_TRUE(nlohmann::json::parse(line).contains("error"));
    std::getline(lines,line); EXPECT_EQ(nlohmann::json::parse(line)["total"],3);
    for (const std::string text:{std::string(8193,'a')+"\n",std::string("a\0b\n",4),std::string("\xff\n",2)}) {
        EXPECT_EQ(command(args,3,&output,text),1); EXPECT_TRUE(nlohmann::json::parse(output).contains("error"));
    }
    args.insert(args.end(),{"--limit","1001"}); EXPECT_EQ(command(args,3),1);
}
TEST_F(TfidfTests,QueryBagOfWordsIgnoresOrderAndCountsRepeatedTerms) {
    const auto a=search("insurance car"),b=search("car insurance"); ASSERT_EQ(a.count,b.count);
    for (unsigned i=0;i<a.count;++i) { EXPECT_EQ(a.hits[i].id,b.hits[i].id); EXPECT_DOUBLE_EQ(a.hits[i].score,b.hits[i].score); }
    const auto repeated=search("car car insurance"); EXPECT_NE(repeated.hits[0].score,b.hits[0].score);
    auto args=cli(); std::string many; for (unsigned i=0;i<65;++i) many+="car "; args.insert(args.end(),{"--query",many}); EXPECT_EQ(command(args,3),1);
}
TEST_F(TfidfTests,QuotedPhrasesRequireWholeOrderedMatchWithinOneField) {
    const auto words=search("car common");
    ASSERT_EQ(words.total,4U); // Unquoted terms still use TF-IDF bag-of-words.
    const auto phrase=search("\"car common\"");
    ASSERT_EQ(phrase.total,2U);
    ASSERT_EQ(phrase.count,2U);
    EXPECT_EQ(phrase.hits[0].id,19U);
    EXPECT_EQ(phrase.hits[1].id,UINT64_MAX-1);
    EXPECT_EQ(search("\"common car\"").total,0U);
    EXPECT_EQ(search("\"car insurance\"").total,0U); // Title/body boundary.
    const auto mixed=search("\"insurance auto\" car");
    ASSERT_EQ(mixed.total,1U);
    EXPECT_EQ(mixed.hits[0].id,3U);
    EXPECT_EQ(search("\"insurance auto\" \"rare common\"").total,0U);
    EXPECT_EQ(search("\"car common\"",1).count,1U);
    EXPECT_EQ(search("\"car common\"",0).total,2U);
    auto args=cli(); args.insert(args.end(),{"--query","\"car common"});
    EXPECT_NE(command(args,3),0); // Missing closing quote is an error.
}
TEST_F(TfidfTests,RejectsIncompleteCorruptTruncatedAndForeignNorms) {
    auto args=cli(); args.insert(args.end(),{"--query","car"});
    { std::ofstream marker(p("norms/INCOMPLETE")); marker<<"no"; }
    EXPECT_EQ(command(args,3),1); fs::remove(p("norms/INCOMPLETE"));
    fs::copy_file(p("norms/norms.bin"),p("backup"));
    { std::fstream file(p("norms/norms.bin"),std::ios::in|std::ios::out|std::ios::binary); file.seekp(81); file.put('x'); }
    EXPECT_EQ(command(args,3),1);
    fs::copy_file(p("backup"),p("norms/norms.bin"),fs::copy_options::overwrite_existing); fs::resize_file(p("norms/norms.bin"),81);
    EXPECT_EQ(command(args,3),1);
    fs::copy_file(p("backup"),p("norms/norms.bin"),fs::copy_options::overwrite_existing);
    { std::fstream file(p("norms/norms.bin"),std::ios::in|std::ios::out|std::ios::binary); file.seekp(24); file.put('x'); }
    EXPECT_EQ(command(args,3),1);
    EXPECT_NE(command({"build","--index",p("index"),"--positions",p("positions"),"--output",p("norms"),"--reserve-mib","0"},2),0);
}
TEST_F(TfidfTests,LemmaAlternativesUseSeparateFullNormsAndDeduplicateQueryTerms) {
    fs::remove_all(root); fs::create_directory(root); lemmas=true;
    docs={{3,"cars","car"},{19,"vehicles",""},{5000000000ULL,"insurance",""},{UINT64_MAX,"",""}}; build();
    gaming::BooleanSearch engine; gaming::Tfidf rank; const char* error=nullptr;
    ASSERT_TRUE(engine.open(p("index").c_str(),gaming::TermSpace::lemma,p("dict.bin").c_str(),&error,p("positions").c_str()));
    ASSERT_TRUE(rank.open(p("norms").c_str(),p("index").c_str(),p("positions").c_str(),engine,&error));
    gaming::RankedResult result;
    ASSERT_TRUE(rank.search(engine,"cars car",10,result,&error));
    ASSERT_EQ(result.count,2U); EXPECT_EQ(result.terms,1U); EXPECT_EQ(result.hits[0].id,3U); EXPECT_NEAR(result.hits[0].score,1,1e-12);
    EXPECT_NEAR(result.hits[1].score,1/std::sqrt(2.0),1e-12);
    ASSERT_TRUE(rank.search(engine,"vehicles",10,result,&error));
    EXPECT_EQ(result.terms,2U); ASSERT_EQ(result.count,2U); EXPECT_EQ(result.hits[0].id,19U);
    // car df=2, auto df=1: both alternatives contribute once at the shared position.
    const double a=std::log10(4.0),c=std::log10(2.0);
    EXPECT_NEAR(result.hits[0].score,(a+c)/std::sqrt(2*(a*a+c*c)),1e-12);
    ASSERT_TRUE(rank.search(engine,"\"cars\"",10,result,&error));
    EXPECT_EQ(result.total,2U); // Phrase matching uses the same lemma alternatives.
}
TEST_F(TfidfTests,HeapMatchesFullSortAcrossManyDocuments) {
    fs::remove_all(root); fs::create_directory(root); docs.clear();
    for (unsigned i=0;i<1200;++i) {
        std::string body;
        for (unsigned j=0;j<i%13;++j) body+="car ";
        for (unsigned j=0;j<i%7;++j) body+="insurance ";
        for (unsigned j=0;j<i%5;++j) body+="filler ";
        docs.push_back({U64(i)*17+1,"common",body});
    }
    build(); const auto expected=reference("car insurance");
    for (unsigned k:{1U,7U,20U,1000U}) {
        const auto actual=search("car insurance",k); ASSERT_EQ(actual.total,expected.size()); ASSERT_EQ(actual.count,k);
        for (unsigned i=0;i<k;++i) { EXPECT_EQ(actual.hits[i].id,expected[i].id); EXPECT_NEAR(actual.hits[i].score,expected[i].score,1e-12); }
    }
}
}
