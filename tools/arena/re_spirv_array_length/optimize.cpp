// CPU-only adapter around the same optimizer used by Godot's Vulkan backend.
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>

#include "thirdparty/re-spirv/re-spirv.h"

int main(int argc, char **argv) {
	if (argc != 6) {
		std::cerr << "usage: optimize input.spv output.spv inline[0|1] dce[0|1] specialization[none|0|1]\n";
		return 2;
	}
	std::ifstream source(argv[1], std::ios::binary);
	if (!source) {
		return 2;
	}
	std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(source)), {});
	if (bytes.size() < 20 || bytes.size() % 4 != 0) {
		return 2;
	}
	respv::Shader shader;
	if (!shader.parse(bytes.data(), bytes.size(), std::string(argv[3]) == "1") || shader.empty()) {
		std::cerr << "re-spirv parse failed\n";
		return 3;
	}
	respv::Options options;
	options.removeDeadCode = std::string(argv[4]) == "1";
	std::vector<respv::SpecConstant> constants;
	if (std::string(argv[5]) != "none") {
		constants.emplace_back(0, std::vector<uint32_t>{ std::string(argv[5]) == "1" ? 1u : 0u });
	}
	std::vector<uint8_t> optimized;
	if (!respv::Optimizer::run(shader, constants.data(), uint32_t(constants.size()), optimized, options) || optimized.empty()) {
		std::cerr << "re-spirv optimization failed\n";
		return 4;
	}
	std::ofstream destination(argv[2], std::ios::binary);
	destination.write(reinterpret_cast<const char *>(optimized.data()), optimized.size());
	if (!destination) {
		return 2;
	}
	std::cout << "input_bytes=" << bytes.size() << " output_bytes=" << optimized.size() << '\n';
	return 0;
}
