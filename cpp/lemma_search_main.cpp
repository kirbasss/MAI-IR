#include "lemma_search.hpp"
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif
int main(int argc, char** argv) {
#ifdef _WIN32
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    return gaming::run_lemma_search(argc,argv,stdout,stderr);
}
