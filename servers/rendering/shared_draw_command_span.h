/**************************************************************************/
/*  shared_draw_command_span.h                                            */
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

#include "core/error/error_list.h"
#include "servers/rendering/rendering_server_enums.h"

#include <cstdint>
#include <limits>

namespace RendererSharedDraw {

struct CommandSpan {
	int capacity = 0;
	uint32_t offset = 0;
	uint32_t count = 0;
	uint32_t stride = 0;
	uint64_t end = 0;
};

// Keep the original signed script/managed values until every narrowing is checked.
// Output is transactional too: an invalid layout never changes the old description.
inline Error validate_command_span(int64_t p_capacity, int64_t p_offset, int64_t p_count, int64_t p_stride, RSE::MultimeshIndirectSubmissionMode p_mode, uint32_t p_max_draw_count, CommandSpan &r_span) {
	constexpr int64_t MAX_FIELD = std::numeric_limits<int32_t>::max();
	if (p_capacity <= 0 || p_capacity > MAX_FIELD || p_offset < 0 || p_offset > MAX_FIELD || (p_offset % 4) != 0 || p_count < 1 || p_count > 1024 || p_stride < 20 || p_stride > MAX_FIELD || (p_stride % 4) != 0) {
		return ERR_INVALID_PARAMETER;
	}
	if (p_mode != RSE::MULTIMESH_INDIRECT_SUBMISSION_LOOP && p_mode != RSE::MULTIMESH_INDIRECT_SUBMISSION_MULTI_DRAW) {
		return ERR_INVALID_PARAMETER;
	}
	if (p_mode == RSE::MULTIMESH_INDIRECT_SUBMISSION_MULTI_DRAW && p_count > 1 && (p_max_draw_count == 0 || uint64_t(p_count) > p_max_draw_count)) {
		return ERR_UNAVAILABLE;
	}
	const uint64_t end = uint64_t(p_offset) + uint64_t(p_count - 1) * uint64_t(p_stride) + 20;
	// Global RD buffers and draw offsets are uint32; no possible buffer can hold more.
	if (end > std::numeric_limits<uint32_t>::max()) {
		return ERR_INVALID_PARAMETER;
	}
	r_span.capacity = int(p_capacity);
	r_span.offset = uint32_t(p_offset);
	r_span.count = uint32_t(p_count);
	r_span.stride = uint32_t(p_stride);
	r_span.end = end;
	return OK;
}

struct CountedCommandSpan {
	CommandSpan commands;
	uint32_t count_offset = 0;
	uint64_t count_end = 0;
};

// The counted attachment has its own capability even for maximum one. Reuse
// LOOP only for E9's numeric layout rules, never as an execution fallback.
inline Error validate_counted_command_span(int64_t p_capacity, int64_t p_offset, int64_t p_maximum, int64_t p_stride, int64_t p_count_offset, bool p_same_buffer, uint32_t p_count_limit, CountedCommandSpan &r_span) {
	CommandSpan commands;
	const Error layout_error = validate_command_span(p_capacity, p_offset, p_maximum, p_stride, RSE::MULTIMESH_INDIRECT_SUBMISSION_LOOP, 0, commands);
	if (layout_error != OK) {
		return layout_error;
	}
	if (p_count_offset < 0 || p_count_offset > std::numeric_limits<int32_t>::max() || (p_count_offset % 4) != 0) {
		return ERR_INVALID_PARAMETER;
	}
	const uint64_t count_end = uint64_t(p_count_offset) + 4;
	if (p_same_buffer && uint64_t(p_count_offset) < commands.end && uint64_t(commands.offset) < count_end) {
		return ERR_INVALID_PARAMETER;
	}
	if (p_count_limit == 0 || commands.count > p_count_limit) {
		return ERR_UNAVAILABLE;
	}
	// Publish only after every check, preserving the caller's old layout on error.
	r_span.commands = commands;
	r_span.count_offset = uint32_t(p_count_offset);
	r_span.count_end = count_end;
	return OK;
}

} // namespace RendererSharedDraw
