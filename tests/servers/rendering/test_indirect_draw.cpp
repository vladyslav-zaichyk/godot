/**************************************************************************/
/*  test_indirect_draw.cpp                                                */
/**************************************************************************/
/*                         This file is part of:                          */
/*                             GODOT ENGINE                               */
/*                        https://godotengine.org                         */
/**************************************************************************/
/* Copyright (c) 2014-present Godot Engine contributors (see AUTHORS.md). */
/* Copyright (c) 2007-2014 Juan Linietsky, Ariel Manzur.                  */
/*                                                                        */
/* Permission is hereby granted, free of charge, to any person obtaining  */
/* a copy of this software and associated documentation files (the        */
/* "Software"), to deal in the Software without restriction, including    */
/* without limitation the rights to use, copy, modify, merge, publish,    */
/* distribute, sublicense, and/or sell copies of the Software, and to     */
/* permit persons to whom the Software is furnished to do so, subject to  */
/* the following conditions:                                              */
/*                                                                        */
/* The above copyright notice and this permission notice shall be         */
/* included in all copies or substantial portions of the Software.        */
/*                                                                        */
/* THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,        */
/* EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF     */
/* MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. */
/* IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY   */
/* CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,   */
/* TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE      */
/* SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.                 */
/**************************************************************************/

#include "tests/test_macros.h"

TEST_FORCE_LINK(test_indirect_draw)

#include "servers/rendering/rendering_device_indirect.h"

namespace TestIndirectDraw {

using RenderingDeviceIndirect::Capabilities;
using RenderingDeviceIndirect::validate_draw_range;
using RenderingDeviceIndirect::validate_count_draw_range;
using RenderingDeviceIndirect::validate_dispatch_range;

static constexpr Capabilities VULKAN = { 1024, Capabilities::STRIDE_VULKAN };
static constexpr Capabilities VULKAN_SINGLE = { 1, Capabilities::STRIDE_VULKAN };
static constexpr Capabilities PACKED = { 0, Capabilities::STRIDE_PACKED };
static constexpr Capabilities REQUESTED = {};

TEST_CASE("[Rendering][IndirectDraw][IndirectDispatch] A complete twelve byte command and its prefix must fit") {
	CHECK(validate_dispatch_range(12, 0) == OK);
	CHECK(validate_dispatch_range(16, 4) == OK);
	CHECK(validate_dispatch_range(24, 12) == OK);
	CHECK(validate_dispatch_range(0, 0) == ERR_INVALID_PARAMETER);
	CHECK(validate_dispatch_range(11, 0) == ERR_INVALID_PARAMETER);
	CHECK(validate_dispatch_range(15, 4) == ERR_INVALID_PARAMETER);
	CHECK(validate_dispatch_range(16, 8) == ERR_INVALID_PARAMETER);
}

TEST_CASE("[Rendering][IndirectDraw][IndirectDispatch] Every command offset requires four byte alignment") {
	for (uint32_t prefix : { 0u, 4u, 32u, 1024u }) {
		CHECK(validate_dispatch_range(2048, prefix) == OK);
		for (uint32_t delta : { 1u, 2u, 3u }) {
			CHECK(validate_dispatch_range(2048, prefix + delta) == ERR_INVALID_PARAMETER);
		}
	}
}

TEST_CASE("[Rendering][IndirectDraw][IndirectDispatch] Offset addition cannot wrap at the uint32 boundary") {
	// The old uint32_t addition wrapped this aligned command end to eight.
	CHECK(validate_dispatch_range(8, UINT32_MAX - 3) == ERR_INVALID_PARAMETER);
	CHECK(validate_dispatch_range(UINT32_MAX, UINT32_MAX - 3) == ERR_INVALID_PARAMETER);
	CHECK(validate_dispatch_range(UINT32_MAX, UINT32_MAX - 11) == ERR_INVALID_PARAMETER);
	CHECK(validate_dispatch_range(UINT32_MAX, UINT32_MAX - 15) == OK);
	const uint64_t exact_end = uint64_t(UINT32_MAX - 3) + 12;
	CHECK(validate_dispatch_range(exact_end, UINT32_MAX - 3) == OK);
	CHECK(validate_dispatch_range(exact_end - 1, UINT32_MAX - 3) == ERR_INVALID_PARAMETER);
	CHECK(validate_dispatch_range(UINT64_MAX, UINT32_MAX) == ERR_INVALID_PARAMETER);
}

TEST_CASE("[Rendering][IndirectDraw][IndirectDispatch] Small spans agree with an independent subtraction oracle") {
	for (uint32_t size = 0; size <= 96; size++) {
		for (uint32_t offset = 0; offset <= 128; offset++) {
			const bool expected = offset % 4 == 0 && offset <= size && size - offset >= 12;
			CHECK((validate_dispatch_range(size, offset) == OK) == expected);
		}
	}
}

TEST_CASE("[Rendering][IndirectDraw] Every call requires aligned command offset") {
	for (bool indexed : { false, true }) {
		for (uint32_t count : { 0u, 1u, 2u }) {
			CHECK(validate_draw_range(128, indexed, 1, count, 32, VULKAN) == ERR_INVALID_PARAMETER);
			CHECK(validate_draw_range(128, indexed, 2, count, 32, PACKED) == ERR_INVALID_PARAMETER);
			CHECK(validate_draw_range(128, indexed, 3, count, 32, REQUESTED) == ERR_INVALID_PARAMETER);
		}
	}
}

TEST_CASE("[Rendering][IndirectDraw] Empty spans do not require one command or inspect stride") {
	for (bool indexed : { false, true }) {
		CHECK(validate_draw_range(0, indexed, 0, 0, 0, VULKAN_SINGLE) == OK);
		CHECK(validate_draw_range(4, indexed, 128, 0, 1, VULKAN_SINGLE) == OK);
		CHECK(validate_draw_range(0, indexed, UINT32_MAX - 3, 0, UINT32_MAX, VULKAN_SINGLE) == OK);
	}
}

TEST_CASE("[Rendering][IndirectDraw] One command ignores every supplied stride") {
	for (uint32_t stride : { 0u, 1u, 16u, 20u, UINT32_MAX }) {
		CHECK(validate_draw_range(20, true, 0, 1, stride, VULKAN_SINGLE) == OK);
		CHECK(validate_draw_range(16, false, 0, 1, stride, VULKAN_SINGLE) == OK);
	}
}

TEST_CASE("[Rendering][IndirectDraw] Single command checks native command size and prefix") {
	CHECK(validate_draw_range(32, true, 12, 1, 0, VULKAN) == OK);
	CHECK(validate_draw_range(31, true, 12, 1, 0, VULKAN) == ERR_INVALID_PARAMETER);
	CHECK(validate_draw_range(28, false, 12, 1, 0, VULKAN) == OK);
	CHECK(validate_draw_range(27, false, 12, 1, 0, VULKAN) == ERR_INVALID_PARAMETER);
	CHECK(validate_draw_range(16, true, 0, 1, 0, VULKAN) == ERR_INVALID_PARAMETER);
}

TEST_CASE("[Rendering][IndirectDraw] Padded indexed span validates its final command") {
	CHECK(validate_draw_range(116, true, 32, 3, 32, VULKAN) == OK);
	CHECK(validate_draw_range(115, true, 32, 3, 32, VULKAN) == ERR_INVALID_PARAMETER);
	CHECK(validate_draw_range(52, true, 32, 3, 32, VULKAN) == ERR_INVALID_PARAMETER);
}

TEST_CASE("[Rendering][IndirectDraw] Padded nonindexed span uses a sixteen byte final command") {
	CHECK(validate_draw_range(112, false, 32, 3, 32, VULKAN) == OK);
	CHECK(validate_draw_range(111, false, 32, 3, 32, VULKAN) == ERR_INVALID_PARAMETER);
	CHECK(validate_draw_range(48, false, 32, 3, 32, VULKAN) == ERR_INVALID_PARAMETER);
}

TEST_CASE("[Rendering][IndirectDraw] Packed Vulkan spans fit without trailing padding") {
	CHECK(validate_draw_range(80, true, 0, 4, 20, VULKAN) == OK);
	CHECK(validate_draw_range(79, true, 0, 4, 20, VULKAN) == ERR_INVALID_PARAMETER);
	CHECK(validate_draw_range(64, false, 0, 4, 16, VULKAN) == OK);
	CHECK(validate_draw_range(63, false, 0, 4, 16, VULKAN) == ERR_INVALID_PARAMETER);
}

TEST_CASE("[Rendering][IndirectDraw] Vulkan rejects undersized and unaligned repeated strides") {
	for (uint32_t stride : { 0u, 4u, 16u, 19u, 21u }) {
		CHECK(validate_draw_range(256, true, 0, 2, stride, VULKAN) == ERR_INVALID_PARAMETER);
	}
	for (uint32_t stride : { 0u, 4u, 12u, 15u, 17u }) {
		CHECK(validate_draw_range(256, false, 0, 2, stride, VULKAN) == ERR_INVALID_PARAMETER);
	}
}

TEST_CASE("[Rendering][IndirectDraw] Missing Vulkan multidraw permits an ordinary single draw") {
	CHECK(validate_draw_range(20, true, 0, 1, 0, VULKAN_SINGLE) == OK);
	CHECK(validate_draw_range(40, true, 0, 2, 20, VULKAN_SINGLE) == ERR_UNAVAILABLE);
	CHECK(validate_draw_range(32, false, 0, 2, 16, VULKAN_SINGLE) == ERR_UNAVAILABLE);
}

TEST_CASE("[Rendering][IndirectDraw] Known device maximum is inclusive") {
	CHECK(validate_draw_range(20480, true, 0, 1024, 20, VULKAN) == OK);
	CHECK(validate_draw_range(20500, true, 0, 1025, 20, VULKAN) == ERR_UNAVAILABLE);
	CHECK(validate_draw_range(16400, false, 0, 1025, 16, VULKAN) == ERR_UNAVAILABLE);
}

TEST_CASE("[Rendering][IndirectDraw] Unknown maximum preserves existing backend multidraw") {
	CHECK(validate_draw_range(20500, true, 0, 1025, 20, REQUESTED) == OK);
	CHECK(validate_draw_range(20500, true, 0, 1025, 0, PACKED) == OK);
}

TEST_CASE("[Rendering][IndirectDraw] Offset addition does not wrap at the uint32 boundary") {
	CHECK(validate_draw_range(UINT32_MAX, true, UINT32_MAX - 3, 1, 0, VULKAN) == ERR_INVALID_PARAMETER);
	CHECK(validate_draw_range(UINT32_MAX, false, UINT32_MAX - 3, 1, 0, VULKAN) == ERR_INVALID_PARAMETER);
	CHECK(validate_draw_range(UINT32_MAX, true, UINT32_MAX - 23, 1, 0, VULKAN) == OK);
}

TEST_CASE("[Rendering][IndirectDraw] Repeated stride product is evaluated in sixty four bits") {
	CHECK(validate_draw_range(UINT32_MAX, true, 0, 3, 0x80000000u, REQUESTED) == ERR_INVALID_PARAMETER);
	CHECK(validate_draw_range(UINT32_MAX, false, 0, 3, 0x80000000u, REQUESTED) == ERR_INVALID_PARAMETER);
	const uint64_t exact_end = uint64_t(UINT32_MAX - 3) + uint64_t(UINT32_MAX - 1) * uint64_t(UINT32_MAX) + 20;
	CHECK(validate_draw_range(exact_end, true, UINT32_MAX - 3, UINT32_MAX, UINT32_MAX, REQUESTED) == OK);
	CHECK(validate_draw_range(exact_end - 1, true, UINT32_MAX - 3, UINT32_MAX, UINT32_MAX, REQUESTED) == ERR_INVALID_PARAMETER);
}

TEST_CASE("[Rendering][IndirectDraw] D3D12 fixed packed signatures ignore requested stride") {
	for (uint32_t stride : { 0u, 1u, 20u, 32u, UINT32_MAX }) {
		CHECK(validate_draw_range(80, true, 0, 4, stride, PACKED) == OK);
		CHECK(validate_draw_range(79, true, 0, 4, stride, PACKED) == ERR_INVALID_PARAMETER);
		CHECK(validate_draw_range(64, false, 0, 4, stride, PACKED) == OK);
		CHECK(validate_draw_range(63, false, 0, 4, stride, PACKED) == ERR_INVALID_PARAMETER);
	}
}

TEST_CASE("[Rendering][IndirectDraw] Metal zero stride repeatedly reads the same command") {
	CHECK(validate_draw_range(20, true, 0, 1025, 0, REQUESTED) == OK);
	CHECK(validate_draw_range(19, true, 0, 1025, 0, REQUESTED) == ERR_INVALID_PARAMETER);
	CHECK(validate_draw_range(16, false, 0, 1025, 0, REQUESTED) == OK);
	CHECK(validate_draw_range(15, false, 0, 1025, 0, REQUESTED) == ERR_INVALID_PARAMETER);
}

TEST_CASE("[Rendering][IndirectDraw] Requested backend strides retain overlap and padding semantics") {
	CHECK(validate_draw_range(28, true, 0, 3, 4, REQUESTED) == OK);
	CHECK(validate_draw_range(27, true, 0, 3, 4, REQUESTED) == ERR_INVALID_PARAMETER);
	CHECK(validate_draw_range(116, true, 32, 3, 32, REQUESTED) == OK);
	CHECK(validate_draw_range(115, true, 32, 3, 32, REQUESTED) == ERR_INVALID_PARAMETER);
	CHECK(validate_draw_range(92, true, 32, 3, 32, PACKED) == OK);
}

TEST_CASE("[Rendering][IndirectDraw][IndirectCount] Signed source integers are checked before narrowing") {
	for (int64_t invalid : { int64_t(-1), int64_t(UINT32_MAX) + 1, INT64_MAX }) {
		CHECK(validate_count_draw_range(UINT64_MAX, UINT64_MAX, true, invalid, 0, 1, 20, false, UINT32_MAX) == ERR_INVALID_PARAMETER);
		CHECK(validate_count_draw_range(UINT64_MAX, UINT64_MAX, true, 0, invalid, 1, 20, false, UINT32_MAX) == ERR_INVALID_PARAMETER);
		CHECK(validate_count_draw_range(UINT64_MAX, UINT64_MAX, true, 0, 0, invalid, 20, false, UINT32_MAX) == ERR_INVALID_PARAMETER);
		CHECK(validate_count_draw_range(UINT64_MAX, UINT64_MAX, true, 0, 0, 1, invalid, false, UINT32_MAX) == ERR_INVALID_PARAMETER);
	}
	CHECK(validate_count_draw_range(20, 4, true, 0, 0, 0, 20, false, 1) == ERR_INVALID_PARAMETER);
}

TEST_CASE("[Rendering][IndirectDraw][IndirectCount] Both buffer offsets and every stride require alignment") {
	for (bool indexed : { false, true }) {
		for (int64_t offset : { 1, 2, 3 }) {
			CHECK(validate_count_draw_range(128, 128, indexed, offset, 0, 1, 32, false, 8) == ERR_INVALID_PARAMETER);
			CHECK(validate_count_draw_range(128, 128, indexed, 0, offset, 1, 32, false, 8) == ERR_INVALID_PARAMETER);
		}
		for (int64_t stride : { 0, 4, 12, 19, 21, 31 }) {
			CHECK(validate_count_draw_range(128, 4, indexed, 0, 0, 1, stride, false, 8) == ERR_INVALID_PARAMETER);
		}
	}
}

TEST_CASE("[Rendering][IndirectDraw][IndirectCount] Maximum one still describes a valid stride") {
	CHECK(validate_count_draw_range(20, 4, true, 0, 0, 1, 20, false, 1) == OK);
	CHECK(validate_count_draw_range(20, 4, true, 0, 0, 1, 32, false, 1) == OK);
	CHECK(validate_count_draw_range(20, 4, true, 0, 0, 1, 16, false, 1) == ERR_INVALID_PARAMETER);
	CHECK(validate_count_draw_range(16, 4, false, 0, 0, 1, 16, false, 1) == OK);
	CHECK(validate_count_draw_range(16, 4, false, 0, 0, 1, 20, false, 1) == OK);
}

TEST_CASE("[Rendering][IndirectDraw][IndirectCount] Complete indexed and nonindexed command spans must fit") {
	CHECK(validate_count_draw_range(116, 4, true, 32, 0, 3, 32, false, 3) == OK);
	CHECK(validate_count_draw_range(115, 4, true, 32, 0, 3, 32, false, 3) == ERR_INVALID_PARAMETER);
	CHECK(validate_count_draw_range(112, 4, false, 32, 0, 3, 32, false, 3) == OK);
	CHECK(validate_count_draw_range(111, 4, false, 32, 0, 3, 32, false, 3) == ERR_INVALID_PARAMETER);
	CHECK(validate_count_draw_range(52, 4, true, 32, 0, 3, 32, false, 3) == ERR_INVALID_PARAMETER);
}

TEST_CASE("[Rendering][IndirectDraw][IndirectCount] Counter is exactly a four byte word including its prefix") {
	CHECK(validate_count_draw_range(20, 16, true, 0, 12, 1, 20, false, 1) == OK);
	CHECK(validate_count_draw_range(20, 15, true, 0, 12, 1, 20, false, 1) == ERR_INVALID_PARAMETER);
	CHECK(validate_count_draw_range(20, 0, true, 0, 0, 1, 20, false, 1) == ERR_INVALID_PARAMETER);
	CHECK(validate_count_draw_range(20, UINT32_MAX, true, 0, UINT32_MAX - 3, 1, 20, false, 1) == ERR_INVALID_PARAMETER);
}

TEST_CASE("[Rendering][IndirectDraw][IndirectCount] Shared buffers allow adjacent disjoint prefix or suffix counters") {
	CHECK(validate_count_draw_range(64, 64, true, 4, 0, 3, 20, true, 3) == OK);
	CHECK(validate_count_draw_range(64, 64, true, 0, 60, 3, 20, true, 3) == OK);
	CHECK(validate_count_draw_range(64, 64, false, 4, 0, 3, 20, true, 3) == OK);
}

TEST_CASE("[Rendering][IndirectDraw][IndirectCount] Shared counters cannot overlap commands or inter-command padding") {
	for (int64_t offset : { 16, 32, 36, 48, 64, 80 }) {
		CHECK(validate_count_draw_range(100, 100, true, 16, offset, 3, 32, true, 3) == ERR_INVALID_PARAMETER);
	}
	CHECK(validate_count_draw_range(100, 100, true, 16, 12, 3, 32, true, 3) == OK);
	CHECK(validate_count_draw_range(104, 104, true, 16, 100, 3, 32, true, 3) == OK);
}

TEST_CASE("[Rendering][IndirectDraw][IndirectCount] Equal byte offsets in separate buffers do not alias") {
	CHECK(validate_count_draw_range(80, 80, true, 16, 16, 3, 20, false, 3) == OK);
	CHECK(validate_count_draw_range(80, 80, true, 16, 16, 3, 20, true, 3) == ERR_INVALID_PARAMETER);
}

TEST_CASE("[Rendering][IndirectDraw][IndirectCount] Unknown or insufficient enabled device limits fail closed") {
	CHECK(validate_count_draw_range(80, 4, true, 0, 0, 4, 20, false, 0) == ERR_UNAVAILABLE);
	CHECK(validate_count_draw_range(80, 4, true, 0, 0, 4, 20, false, 3) == ERR_UNAVAILABLE);
	CHECK(validate_count_draw_range(80, 4, true, 0, 0, 4, 20, false, 4) == OK);
	CHECK(validate_count_draw_range(20, 4, true, 0, 0, 1, 20, false, 0) == ERR_UNAVAILABLE);
}

TEST_CASE("[Rendering][IndirectDraw][IndirectCount] Offset and stride arithmetic cannot wrap at uint32 boundaries") {
	CHECK(validate_count_draw_range(UINT32_MAX, 4, true, UINT32_MAX - 3, 0, 1, 20, false, 1) == ERR_INVALID_PARAMETER);
	const uint64_t end = uint64_t(UINT32_MAX - 3) + uint64_t(UINT32_MAX - 1) * uint64_t(UINT32_MAX - 3) + 20;
	CHECK(validate_count_draw_range(end, 4, true, UINT32_MAX - 3, 0, UINT32_MAX, UINT32_MAX - 3, false, UINT32_MAX) == OK);
	CHECK(validate_count_draw_range(end - 1, 4, true, UINT32_MAX - 3, 0, UINT32_MAX, UINT32_MAX - 3, false, UINT32_MAX) == ERR_INVALID_PARAMETER);
}

} // namespace TestIndirectDraw
