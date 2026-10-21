#include "tokenizer_demo.hpp"

#include <cstdio>
#include <iostream>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

int main(int argc, char** argv) {
#ifdef _WIN32
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    return gaming::run_tokenizer_demo(argc, argv, std::cout, std::cerr);
}
