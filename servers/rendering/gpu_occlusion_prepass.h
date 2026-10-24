/**************************************************************************/
/*  gpu_occlusion_prepass.h                                               */
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
#include "core/math/projection.h"
#include "core/math/transform_3d.h"
#include "core/math/vector2i.h"
#include "core/object/ref_counted.h"
#include "core/templates/rid.h"
#include "core/templates/vector.h"
#include "core/variant/callable.h"
#include "core/variant/dictionary.h"

struct GpuOcclusionConsumer {
	RID instance;
	RID identity_bank;
	int64_t candidate_identity_base = 0;
	RID candidate_commands;
	int64_t candidate_command_byte_offset = 0;
	int64_t logical_slot_capacity = 0;

	// Captured on registration; a render revalidates these borrowed resources.
	RID multimesh;
	RID fallback_commands;
	int64_t fallback_command_byte_offset = 0;
	int64_t draw_slot_capacity = 0;
	uint64_t renderer_signature = 0;
	Vector<RID> shader_buffers;
};

namespace GpuOcclusionMetadata {

// Internal admission only; caller storage and animated bounds are not inspected.
Error validate_camera(const Transform3D &p_camera, const Projection &p_projection, const Projection &p_corrected_projection, bool p_orthogonal);
Error validate_consumer_numbers(int64_t p_candidate_base, int64_t p_capacity, int64_t p_draw_capacity, int64_t p_command_offset);
bool intervals_overlap(uint64_t p_begin_a, uint64_t p_size_a, uint64_t p_begin_b, uint64_t p_size_b);
bool consumers_conflict(const GpuOcclusionConsumer &p_a, const GpuOcclusionConsumer &p_b);
bool validate_acknowledgment(const Variant &p_acknowledgment, uint64_t p_epoch, uint64_t p_configuration_generation);

} // namespace GpuOcclusionMetadata

// Internal, unreflected shared state. It survives clear/reconfigure so epochs
// and generations cannot accidentally match an acknowledgment from an old run.
class GpuOcclusionPrepassState : public RefCounted {
public:
	RID viewport;
	uint32_t layer_mask = 0;
	Callable producer;
	uint64_t epoch = 0;
	uint64_t configuration_generation = 0;
	Vector<GpuOcclusionConsumer> consumers;
	bool recording = false;
	bool selected = false;
	Dictionary outcome;

	bool begin_recording(RID p_viewport);
	void set_outcome(const String &p_reason, bool p_selected = false);
	static bool validate_ack(const Variant &p_acknowledgment, uint64_t p_epoch, uint64_t p_configuration_generation) {
		return GpuOcclusionMetadata::validate_acknowledgment(p_acknowledgment, p_epoch, p_configuration_generation);
	}
};

class RenderGpuOcclusionContext : public RefCounted {
	GDCLASS(RenderGpuOcclusionContext, RefCounted);

	bool active = false;
	RID viewport;
	uint64_t epoch = 0;
	uint64_t configuration_generation = 0;
	int32_t view_index = -1;
	Size2i internal_size;
	Projection projection;
	Transform3D camera_transform;
	RID depth;
	int64_t compute_list = -1;

protected:
	static void _bind_methods();

public:
	void prepare(RID p_viewport, uint64_t p_epoch, uint64_t p_generation, int32_t p_view_index, const Size2i &p_size, const Projection &p_projection, const Transform3D &p_camera_transform, RID p_depth, int64_t p_compute_list);
	void invalidate();
	bool is_active() const { return active; }
	RID get_viewport() const { return active ? viewport : RID(); }
	int64_t get_epoch() const { return active ? int64_t(epoch) : 0; }
	int64_t get_configuration_generation() const { return active ? int64_t(configuration_generation) : 0; }
	int32_t get_view_index() const { return active ? view_index : -1; }
	Size2i get_internal_size() const { return active ? internal_size : Size2i(); }
	Projection get_projection() const { return active ? projection : Projection(); }
	Transform3D get_camera_transform() const { return active ? camera_transform : Transform3D(); }
	RID get_depth() const { return active ? depth : RID(); }
	int64_t get_compute_list() const { return active ? compute_list : -1; }
};
