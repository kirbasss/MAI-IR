#pragma once

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <iosfwd>
#include <optional>
#include <string>

namespace gaming {

struct DemoOptions {
    std::optional<std::uint64_t> document_id;
    std::optional<std::string> find;
    std::string field = "text";
    std::size_t context = 5;
};

std::filesystem::path latest_snapshot(const std::filesystem::path& root);
std::string demonstrate(const std::filesystem::path& snapshot, const DemoOptions& options = {});
int run_tokenizer_demo(int argc, const char* const* argv, std::ostream& output, std::ostream& errors);

}  // namespace gaming
