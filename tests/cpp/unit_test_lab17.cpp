#include "compact_index.hpp"
#include "tokenizer.hpp"
#include <gtest/gtest.h>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace {
using U64=std::uint64_t;

// Independent definition of the lecture's little-endian, high-bit-final VB.
std::string reference_vb(U64 value) {
    std::string bytes;
    do {
        unsigned char byte=static_cast<unsigned char>(value&127);
        value>>=7;
        if (!value) byte|=128;
        bytes.push_back(static_cast<char>(byte));
    } while (value);
    return bytes;
}

TEST(UnitCodec17,BoundaryValuesDecodeFromFileAndMemoryWithoutEatingNextNumber) {
    const U64 values[]={0,1,127,128,129,16383,16384,16385,
                        (U64(1)<<32)-1,U64(1)<<32,(U64(1)<<63)-1,U64(1)<<63,UINT64_MAX};
    std::string bytes;
    for (U64 value:values) bytes+=reference_vb(value);
    FILE* file=std::tmpfile(); ASSERT_NE(file,nullptr);
    ASSERT_EQ(std::fwrite(bytes.data(),1,bytes.size(),file),bytes.size());
    std::rewind(file);
    const auto* memory=reinterpret_cast<const unsigned char*>(bytes.data());
    U64 file_left=bytes.size(),memory_left=bytes.size();
    for (U64 expected:values) {
        U64 a=UINT64_MAX,b=UINT64_MAX;
        ASSERT_TRUE(gaming::read_compact_uint(file,file_left,a));
        ASSERT_TRUE(gaming::read_compact_uint(memory,memory_left,b));
        EXPECT_EQ(a,expected); EXPECT_EQ(b,expected);
        EXPECT_EQ(file_left,memory_left);
    }
    EXPECT_EQ(file_left,0U); EXPECT_EQ(memory_left,0U);
    EXPECT_EQ(memory,reinterpret_cast<const unsigned char*>(bytes.data()+bytes.size()));
    std::fclose(file);
}

TEST(UnitCodec17,EveryProperPrefixOfLargestIntegerIsRejected) {
    const std::string encoded=reference_vb(UINT64_MAX);
    ASSERT_EQ(encoded.size(),10U);
    for (std::size_t length=0;length<encoded.size();++length) {
        SCOPED_TRACE(length);
        FILE* file=std::tmpfile(); ASSERT_NE(file,nullptr);
        ASSERT_EQ(std::fwrite(encoded.data(),1,length,file),length);
        std::rewind(file);
        U64 left=length,value=0;
        EXPECT_FALSE(gaming::read_compact_uint(file,left,value));
        std::fclose(file);
        const auto* memory=reinterpret_cast<const unsigned char*>(encoded.data());
        left=length; value=0;
        EXPECT_FALSE(gaming::read_compact_uint(memory,left,value));
    }
}

struct OwnedToken {
    std::string term;
    U64 position;
    std::size_t begin,end;
};
std::vector<OwnedToken> collect(const std::string& field) {
    std::vector<OwnedToken> result;
    gaming::tokenize(field,[&](const gaming::Token& token) {
        result.push_back({std::string(token.term),token.position,token.begin_byte,token.end_byte});
    });
    return result;
}

TEST(UnitTokenizer17,JoiningIndependentFieldsPreservesTokensAndByteSpans) {
    const std::string fields[]={"S.T.A.L.K.E.R. v1.2.3",u8"Ёж и GTA5", "C++ C# 2026",u8"Half-Life 🎮 тест"};
    std::string joined;
    std::vector<OwnedToken> expected;
    for (const auto& field:fields) {
        const std::size_t base=joined.size();
        const std::size_t position_base=expected.size();
        auto tokens=collect(field);
        for (auto& token:tokens) {
            token.position+=position_base; token.begin+=base; token.end+=base;
            expected.push_back(token);
        }
        joined+=field; joined+='\n';
    }
    const auto actual=collect(joined);
    ASSERT_EQ(actual.size(),expected.size());
    for (std::size_t i=0;i<actual.size();++i) {
        EXPECT_EQ(actual[i].term,expected[i].term);
        EXPECT_EQ(actual[i].position,expected[i].position);
        EXPECT_EQ(actual[i].begin,expected[i].begin);
        EXPECT_EQ(actual[i].end,expected[i].end);
    }
}
}
