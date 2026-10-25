/**************************************************************************/
/*  test_gpu_occlusion_repeat.cpp                                          */
/**************************************************************************/
/*                         This file is part of:                          */
/*                             GODOT ENGINE                               */
/*                        https://godotengine.org                         */
/**************************************************************************/
/* Copyright (c) 2014-present Godot Engine contributors (see AUTHORS.md).   */
/* Copyright (c) 2007-2014 Juan Linietsky, Ariel Manzur.                     */
/*                                                                        */
/* Permission is hereby granted, free of charge, to any person obtaining    */
/* a copy of this software and associated documentation files (the          */
/* "Software"), to deal in the Software without restriction, including      */
/* without limitation the rights to use, copy, modify, merge, publish,      */
/* distribute, sublicense, and/or sell copies of the Software, and to       */
/* permit persons to whom the Software is furnished to do so, subject to    */
/* the following conditions:                                              */
/*                                                                        */
/* The above copyright notice and this permission notice shall be          */
/* included in all copies or substantial portions of the Software.         */
/*                                                                        */
/* THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,          */
/* EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF      */
/* MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. */
/* IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY    */
/* CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,    */
/* TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE        */
/* SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.                 */
/**************************************************************************/

#include "tests/test_macros.h"

TEST_FORCE_LINK(test_gpu_occlusion_repeat)

#include "servers/rendering/gpu_occlusion_repeat.h"

#include <algorithm>
#include <cstdint>
#include <limits>
#include <vector>

namespace TestGpuOcclusionRepeat {

// Match the fields consumed by the helper without requiring renderer/device
// construction. Group IDs below are the independent compatibility oracle.
struct ElementInfo {
	uint32_t value = 0;
	uint32_t repeat = 0;
};

struct Draw {
	uint32_t base_index = 0;
	uint32_t instance_count = 0;

	bool operator==(const Draw &p_other) const {
		return base_index == p_other.base_index && instance_count == p_other.instance_count;
	}
};

static std::vector<ElementInfo> make_info(const std::vector<uint32_t> &p_groups) {
	std::vector<ElementInfo> info(p_groups.size());
	for (uint32_t begin = 0; begin < p_groups.size();) {
		uint32_t end = begin + 1;
		while (end < p_groups.size() && p_groups[end] == p_groups[begin]) {
			end++;
		}
		for (uint32_t i = begin; i < end; i++) {
			info[i] = { 0xa0000000u ^ (i * 13u), end - i };
		}
		begin = end;
	}
	return info;
}

// Build expected draws by visiting each selected element. This never consumes
// repeat metadata: a phase transition, compatibility boundary or chunk start
// begins a new draw, and all later adjacent compatible elements extend it.
static std::vector<Draw> reference_draws(const std::vector<uint32_t> &p_groups, const std::vector<uint8_t> &p_early, uint32_t p_from, uint32_t p_to, bool p_draw_early, uint32_t p_offset) {
	std::vector<Draw> draws;
	bool previous_selected = false;
	for (uint32_t i = p_from; i < p_to; i++) {
		const bool selected = bool(p_early[i]) == p_draw_early;
		if (selected) {
			if (!previous_selected || p_groups[i] != p_groups[i - 1]) {
				draws.push_back({ i + p_offset, 1 });
			} else {
				draws.back().instance_count++;
			}
		}
		previous_selected = selected;
	}
	return draws;
}

static bool check_draw_coverage(const std::vector<uint32_t> &p_groups, const std::vector<uint8_t> &p_early, const uint32_t *p_repeat, uint32_t p_from, uint32_t p_to, bool p_draw_early, uint32_t p_offset) {
	std::vector<Draw> draws;
	std::vector<uint32_t> visits(p_groups.size(), 0);
	for (uint32_t i = p_from; i < p_to;) {
		if (bool(p_early[i]) != p_draw_early) {
			i++;
			continue;
		}
		const uint32_t repeat = GpuOcclusionRepeat::bound_repeat(p_repeat[i], i, p_to);
		if (repeat == 0 || repeat > p_to - i) {
			return false;
		}
		draws.push_back({ i + p_offset, repeat });
		for (uint32_t j = i; j < i + repeat; j++) {
			if (bool(p_early[j]) != p_draw_early || p_groups[j] != p_groups[i]) {
				return false;
			}
			visits[j]++;
		}
		i += repeat;
	}
	for (uint32_t i = 0; i < p_groups.size(); i++) {
		const uint32_t expected = i >= p_from && i < p_to && bool(p_early[i]) == p_draw_early ? 1 : 0;
		if (visits[i] != expected) {
			return false;
		}
	}
	return draws == reference_draws(p_groups, p_early, p_from, p_to, p_draw_early, p_offset);
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusionRepeat] Mixed phases keep compatible opaque color groups intact") {
	const std::vector<uint32_t> groups = { 0, 0, 0, 0, 0, 1, 1, 2, 2 };
	const std::vector<uint8_t> early = { 1, 1, 0, 0, 1, 1, 0, 0, 0 };
	const std::vector<ElementInfo> info = make_info(groups);
	const std::vector<uint32_t> expected = { 2, 1, 2, 1, 1, 1, 1, 2, 1 };
	std::vector<uint32_t> repeats(groups.size());
	const auto stats = GpuOcclusionRepeat::build_phase_repeats(info.data(), early.data(), uint32_t(groups.size()), repeats.data());
	CHECK(repeats == expected);
	CHECK(stats.original_draw_count == 3);
	CHECK(stats.early_depth_draw_count == 3);
	CHECK(stats.remaining_depth_draw_count == 3);
	// The color path can still draw the original 5-, 2- and 2-instance groups.
	CHECK(info[0].repeat == 5);
	CHECK(info[5].repeat == 2);
	CHECK(info[7].repeat == 2);
	CHECK(check_draw_coverage(groups, early, repeats.data(), 0, 9, true, 51));
	CHECK(check_draw_coverage(groups, early, repeats.data(), 0, 9, false, 51));
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusionRepeat] Every small group layout phase mask and chunk covers its exact original elements") {
	constexpr uint32_t guard = 0xd15ea5ed;
	for (uint32_t count = 0; count <= 7; count++) {
		const uint32_t layouts = count > 0 ? 1u << (count - 1) : 1;
		for (uint32_t layout = 0; layout < layouts; layout++) {
			std::vector<uint32_t> groups(count);
			for (uint32_t i = 1; i < count; i++) {
				groups[i] = groups[i - 1] + ((layout >> (i - 1)) & 1u);
			}
			const auto original = make_info(groups);
			for (uint32_t mask = 0; mask < (1u << count); mask++) {
				auto info = original;
				std::vector<uint8_t> early(count);
				for (uint32_t i = 0; i < count; i++) {
					early[i] = (mask >> i) & 1u;
				}
				std::vector<uint32_t> storage(count + 2, guard);
				uint32_t *repeats = storage.data() + 1;
				const auto stats = GpuOcclusionRepeat::build_phase_repeats(info.data(), early.data(), count, repeats);
				CAPTURE(count);
				CAPTURE(layout);
				CAPTURE(mask);
				CHECK(storage.front() == guard);
				CHECK(storage.back() == guard);
				CHECK(stats.original_draw_count == (count > 0 ? groups.back() + 1 : 0));
				CHECK(stats.early_depth_draw_count == reference_draws(groups, early, 0, count, true, 0).size());
				CHECK(stats.remaining_depth_draw_count == reference_draws(groups, early, 0, count, false, 0).size());
				for (uint32_t i = 0; i < count; i++) {
					CHECK(info[i].repeat == original[i].repeat);
					CHECK(info[i].value == original[i].value);
				}
				for (uint32_t from = 0; from <= count; from++) {
					for (uint32_t to = from; to <= count; to++) {
						CAPTURE(from);
						CAPTURE(to);
						const uint32_t offsets[] = { 0, 113, std::numeric_limits<uint32_t>::max() - 8 };
						const uint32_t offset = offsets[(mask + from + to) % 3];
						CHECK(check_draw_coverage(groups, early, repeats, from, to, true, offset));
						CHECK(check_draw_coverage(groups, early, repeats, from, to, false, offset));
					}
				}
			}
		}
	}
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusionRepeat] Reused phase storage overwrites former batching without changing metadata") {
	const std::vector<uint32_t> groups(257, 0);
	auto info = make_info(groups);
	std::vector<uint8_t> early(groups.size());
	std::vector<uint32_t> repeats(groups.size(), 0xffffffff);
	for (uint32_t epoch = 0; epoch < 5; epoch++) {
		for (uint32_t i = 0; i < early.size(); i++) {
			early[i] = epoch == 0 ? 0 : epoch == 1 ? 1 : epoch == 2 ? i % 2 : epoch == 3 ? (i / 17) % 2 : 0;
		}
		const auto stats = GpuOcclusionRepeat::build_phase_repeats(info.data(), early.data(), uint32_t(groups.size()), repeats.data());
		CAPTURE(epoch);
		CHECK(stats.original_draw_count == 1);
		CHECK(stats.early_depth_draw_count == reference_draws(groups, early, 0, uint32_t(groups.size()), true, 0).size());
		CHECK(stats.remaining_depth_draw_count == reference_draws(groups, early, 0, uint32_t(groups.size()), false, 0).size());
		CHECK(check_draw_coverage(groups, early, repeats.data(), 0, 257, true, 41));
		CHECK(check_draw_coverage(groups, early, repeats.data(), 0, 257, false, 41));
		CHECK(check_draw_coverage(groups, early, repeats.data(), 13, 201, true, 41));
		CHECK(check_draw_coverage(groups, early, repeats.data(), 13, 201, false, 41));
		for (uint32_t i = 0; i < info.size(); i++) {
			CHECK(info[i].repeat == 257 - i);
			CHECK(info[i].value == (0xa0000000u ^ (i * 13u)));
		}
	}
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusionRepeat] Stock maximum repeat boundary never merges compatible groups") {
	// Stock RenderElementInfo caps a compatible batch at (1 << 20) - 1.
	// Matching geometry and phase on both sides must still honor that boundary.
	constexpr uint32_t max_repeats = (1u << 20) - 1;
	std::vector<uint32_t> groups(max_repeats + 2, 0);
	groups[max_repeats] = 1;
	groups[max_repeats + 1] = 1;
	const auto info = make_info(groups);
	std::vector<uint8_t> early(groups.size(), 1);
	std::vector<uint32_t> repeats(groups.size());
	const auto stats = GpuOcclusionRepeat::build_phase_repeats(info.data(), early.data(), uint32_t(groups.size()), repeats.data());
	CHECK(stats.original_draw_count == 2);
	CHECK(stats.early_depth_draw_count == 2);
	CHECK(stats.remaining_depth_draw_count == 0);
	CHECK(repeats[0] == max_repeats);
	CHECK(repeats[max_repeats - 1] == 1);
	CHECK(repeats[max_repeats] == 2);
	CHECK(repeats[max_repeats + 1] == 1);
	CHECK(check_draw_coverage(groups, early, repeats.data(), max_repeats - 3, max_repeats + 2, true, 31));
	CHECK(check_draw_coverage(groups, early, repeats.data(), 19, max_repeats - 1, true, 31));
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusionRepeat] Empty phases and chunk bounds avoid underflow and overdraw") {
	const auto stats = GpuOcclusionRepeat::build_phase_repeats<ElementInfo>(nullptr, nullptr, 0, nullptr);
	CHECK(stats.original_draw_count == 0);
	CHECK(stats.early_depth_draw_count == 0);
	CHECK(stats.remaining_depth_draw_count == 0);
	CHECK(GpuOcclusionRepeat::bound_repeat(17, 11, 11) == 0);
	CHECK(GpuOcclusionRepeat::bound_repeat(17, 12, 11) == 0);
	CHECK(GpuOcclusionRepeat::bound_repeat(0, 2, 8) == 0);
	CHECK(GpuOcclusionRepeat::bound_repeat(3, 2, 8) == 3);
	CHECK(GpuOcclusionRepeat::bound_repeat(17, 2, 8) == 6);
	const uint32_t last = std::numeric_limits<uint32_t>::max();
	CHECK(GpuOcclusionRepeat::bound_repeat(last, last - 2, last) == 2);
	CHECK(GpuOcclusionRepeat::bound_repeat(1, last - 1, last) == 1);
	CHECK(GpuOcclusionRepeat::bound_repeat(last, last, 0) == 0);
}

} // namespace TestGpuOcclusionRepeat
