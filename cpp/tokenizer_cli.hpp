#pragma once

#include <iosfwd>

namespace gaming {

// The real CLI entry point, with injectable streams for native C++ tests.
// argv[0] is the executable name; errors are reported by a nonzero return code.
int run_tokenizer(int argc, const char* const* argv,
                  std::istream& input, std::ostream& output, std::ostream& errors);

}  // namespace gaming
