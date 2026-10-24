/**************************************************************************/
/*  rendering_device_indirect.h                                           */
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

#include <cstdint>

namespace RenderingDeviceIndirect {

// Internal driver policy. Unknown limits must not disable existing backend paths.
struct Capabilities {
	enum StrideMode {
		STRIDE_REQUESTED, // Metal advances by the supplied stride, including zero.
		STRIDE_PACKED, // D3D12 command signatures use the native command size.
		STRIDE_VULKAN, // Vulkan requires an aligned, full command stride for count > 1.
	};

	uint32_t max_draw_count = 0; // Zero means unknown, not unsupported.
	StrideMode stride_mode = STRIDE_REQUESTED;
};

// Validate CPU-visible metadata only. No command contents are read from the GPU.
inline Error validate_draw_range(uint64_t p_buffer_size, bool p_use_indices, uint32_t p_offset, uint32_t p_draw_count, uint32_t p_stride, const Capabilities &p_capabilities) {
	if ((p_offset % 4) != 0) {
		return ERR_INVALID_PARAMETER;
	}
	if (p_draw_count == 0) {
		return OK;
	}
	if (p_capabilities.max_draw_count != 0 && p_draw_count > p_capabilities.max_draw_count) {
		return ERR_UNAVAILABLE;
	}

	const uint32_t command_size = p_use_indices ? 20 : 16;
	uint32_t effective_stride = 0;
	if (p_draw_count > 1) {
		if (p_capabilities.stride_mode == Capabilities::STRIDE_VULKAN && ((p_stride % 4) != 0 || p_stride < command_size)) {
			return ERR_INVALID_PARAMETER;
		}
		effective_stride = p_capabilities.stride_mode == Capabilities::STRIDE_PACKED ? command_size : p_stride;
	}

	// All source values are uint32_t. Promoting before the product and addition
	// keeps even their maximum possible command end representable in uint64_t.
	const uint64_t end = uint64_t(p_offset) + uint64_t(p_draw_count - 1) * uint64_t(effective_stride) + uint64_t(command_size);
	return end <= p_buffer_size ? OK : ERR_INVALID_PARAMETER;
}

} // namespace RenderingDeviceIndirect
