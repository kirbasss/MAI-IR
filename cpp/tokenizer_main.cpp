#include "tokenizer_cli.hpp"

#include <cstdio>
#include <iostream>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

int main(int argc, char** argv) {
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    return gaming::run_tokenizer(argc, argv, std::cin, std::cout, std::cerr);
}
