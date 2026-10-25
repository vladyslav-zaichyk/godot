/**************************************************************************/
/*  gpu_occlusion_repeat.h                                                */
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

#pragma once

#include "core/typedefs.h"

namespace GpuOcclusionRepeat {

struct Statistics {
	uint32_t original_draw_count = 0;
	uint32_t early_depth_draw_count = 0;
	uint32_t remaining_depth_draw_count = 0;
};

// Forward+ stores a suffix at every element of an original repeat group:
// [N, N - 1, ..., 1]. Split only at original boundaries or a change of phase.
// The output is another suffix table; the original metadata and InstanceData
// ordering are immutable. The caller owns p_count output entries and may reuse
// their allocation. Empty input does not dereference any pointer.
template <typename ElementInfo>
Statistics build_phase_repeats(const ElementInfo *p_info, const uint8_t *p_early, uint32_t p_count, uint32_t *r_repeats) {
	Statistics statistics;
	for (uint32_t i = p_count; i-- > 0;) {
		const uint32_t original_repeat = p_info[i].repeat;
		if (original_repeat == 1) {
			statistics.original_draw_count++;
		}
		uint32_t repeat = 1;
		if (i + 1 < p_count && original_repeat > 1 && bool(p_early[i]) == bool(p_early[i + 1])) {
			repeat = MIN(original_repeat, r_repeats[i + 1] + 1);
		}
		r_repeats[i] = repeat;
		if (repeat == 1) {
			if (p_early[i]) {
				statistics.early_depth_draw_count++;
			} else {
				statistics.remaining_depth_draw_count++;
			}
		}
	}
	return statistics;
}

// A renderer subrange can start in the middle of a repeat suffix. It must not
// draw or skip entries owned by a later range.
_FORCE_INLINE_ uint32_t bound_repeat(uint32_t p_repeat, uint32_t p_from, uint32_t p_to) {
	return p_from < p_to ? MIN(p_repeat, p_to - p_from) : 0;
}

} // namespace GpuOcclusionRepeat
