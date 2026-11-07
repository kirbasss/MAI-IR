#pragma once
#include <cstdio>

namespace gaming {
// Own bounded hash table, heapsort and multi-pass merge; no STL data structures.
int run_zipf(int argc, const char* const* argv, FILE* output, FILE* errors);
}
