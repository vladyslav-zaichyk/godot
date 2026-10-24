/**************************************************************************/
/*  gpu_occlusion_prepass.cpp                                             */
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

#include "gpu_occlusion_prepass.h"

#include "core/object/class_db.h"

Error GpuOcclusionMetadata::validate_camera(const Transform3D &p_camera, const Projection &p_projection, const Projection &p_corrected_projection, bool p_orthogonal) {
	const real_t camera_determinant = p_camera.basis.determinant();
	if (!p_camera.is_finite() || !Math::is_finite(camera_determinant) || camera_determinant == 0 || !p_camera.affine_inverse().is_finite()) {
		return ERR_INVALID_PARAMETER;
	}
	for (const Projection *projection : { &p_projection, &p_corrected_projection }) {
		for (uint32_t column = 0; column < 4; column++) {
			if (!projection->columns[column].is_finite()) {
				return ERR_INVALID_PARAMETER;
			}
		}
		const real_t determinant = projection->determinant();
		if (!Math::is_finite(determinant) || determinant == 0) {
			return ERR_INVALID_PARAMETER;
		}
	}
	// Admit native axis-aligned orthographic and centered perspective matrices.
	// Off-center/custom frusta have their own future qualification boundary.
	if (p_projection.columns[0].x <= 0 || p_projection.columns[1].y <= 0 ||
			p_projection.columns[0].y != 0 || p_projection.columns[0].z != 0 || p_projection.columns[0].w != 0 ||
			p_projection.columns[1].x != 0 || p_projection.columns[1].z != 0 || p_projection.columns[1].w != 0 ||
			p_projection.columns[2].x != 0 || p_projection.columns[2].y != 0) {
		return ERR_UNAVAILABLE;
	}
	if (p_orthogonal) {
		if (p_projection.columns[2].w != 0 || p_projection.columns[3].w != 1) {
			return ERR_UNAVAILABLE;
		}
	} else if (p_projection.columns[2].w != -1 || p_projection.columns[3].w != 0 || p_projection.columns[3].x != 0 || p_projection.columns[3].y != 0) {
		return ERR_UNAVAILABLE;
	}
	// These getters assume the original OpenGL projection, NOT corrected reverse Z.
	const real_t z_near = p_projection.get_z_near();
	const real_t z_far = p_projection.get_z_far();
	if (!Math::is_finite(z_near) || !Math::is_finite(z_far) || z_far <= z_near || (!p_orthogonal && z_near <= 0)) {
		return ERR_INVALID_PARAMETER;
	}
	return OK;
}

Error GpuOcclusionMetadata::validate_consumer_numbers(int64_t p_candidate_base, int64_t p_capacity, int64_t p_draw_capacity, int64_t p_command_offset) {
	if (p_capacity <= 0 || p_capacity > INT32_MAX || p_draw_capacity <= 0 || p_draw_capacity > INT32_MAX ||
			p_candidate_base < p_capacity || p_candidate_base > INT32_MAX || p_command_offset < 0 || p_command_offset > INT32_MAX || (p_command_offset % 4) != 0) {
		return ERR_INVALID_PARAMETER;
	}
	// The signed source values fit int32 before widening. These sums/products
	// remain representable without narrowing or reading GPU command contents.
	const uint64_t end = uint64_t(p_candidate_base) + uint64_t(p_capacity);
	return end <= uint64_t(p_draw_capacity) ? OK : ERR_INVALID_PARAMETER;
}

bool GpuOcclusionMetadata::intervals_overlap(uint64_t p_begin_a, uint64_t p_size_a, uint64_t p_begin_b, uint64_t p_size_b) {
	// Compare distances rather than adding arbitrary caller-supplied uint64s.
	if (p_size_a == 0 || p_size_b == 0) {
		return false;
	}
	return p_begin_a <= p_begin_b ? p_begin_b - p_begin_a < p_size_a : p_begin_a - p_begin_b < p_size_b;
}

bool GpuOcclusionMetadata::consumers_conflict(const GpuOcclusionConsumer &p_a, const GpuOcclusionConsumer &p_b) {
	if (p_a.identity_bank == p_b.identity_bank &&
			(intervals_overlap(p_a.candidate_identity_base, p_a.logical_slot_capacity, p_b.candidate_identity_base, p_b.logical_slot_capacity) ||
					intervals_overlap(p_a.candidate_identity_base, p_a.logical_slot_capacity, 0, p_b.logical_slot_capacity) ||
					intervals_overlap(p_b.candidate_identity_base, p_b.logical_slot_capacity, 0, p_a.logical_slot_capacity))) {
		return true;
	}
	if ((p_a.candidate_commands == p_b.candidate_commands && intervals_overlap(p_a.candidate_command_byte_offset, 20, p_b.candidate_command_byte_offset, 20)) ||
			(p_a.candidate_commands == p_b.fallback_commands && intervals_overlap(p_a.candidate_command_byte_offset, 20, p_b.fallback_command_byte_offset, 20)) ||
			(p_b.candidate_commands == p_a.fallback_commands && intervals_overlap(p_b.candidate_command_byte_offset, 20, p_a.fallback_command_byte_offset, 20))) {
		return true;
	}
	for (RID bank : { p_a.candidate_commands, p_a.fallback_commands }) {
		if (bank == p_b.identity_bank || p_b.shader_buffers.has(bank)) {
			return true;
		}
	}
	for (RID bank : { p_b.candidate_commands, p_b.fallback_commands }) {
		if (bank == p_a.identity_bank || p_a.shader_buffers.has(bank)) {
			return true;
		}
	}
	return false;
}

bool GpuOcclusionMetadata::validate_acknowledgment(const Variant &p_acknowledgment, uint64_t p_epoch, uint64_t p_configuration_generation) {
	if (p_acknowledgment.get_type() != Variant::DICTIONARY || p_epoch == 0 || p_configuration_generation == 0 || p_epoch > INT64_MAX || p_configuration_generation > INT64_MAX) {
		return false;
	}
	const Dictionary ack = p_acknowledgment;
	const Variant epoch = ack.get("epoch", Variant());
	const Variant generation = ack.get("configuration_generation", Variant());
	const Variant error = ack.get("error", Variant());
	return epoch.get_type() == Variant::INT && generation.get_type() == Variant::INT && error.get_type() == Variant::INT &&
			int64_t(epoch) == int64_t(p_epoch) && int64_t(generation) == int64_t(p_configuration_generation) && int64_t(error) == OK;
}

bool GpuOcclusionPrepassState::begin_recording(RID p_viewport) {
	if (recording) {
		return false;
	}
	viewport = p_viewport;
	selected = false;
	if (epoch >= INT64_MAX) {
		producer = Callable();
		set_outcome("EPOCH_EXHAUSTED");
		return false;
	}
	epoch++;
	recording = true;
	set_outcome("NOT_SELECTED");
	return true;
}

void GpuOcclusionPrepassState::set_outcome(const String &p_reason, bool p_selected) {
	selected = p_selected;
	outcome.clear();
	outcome["epoch"] = int64_t(epoch);
	outcome["configuration_generation"] = int64_t(configuration_generation);
	outcome["selected"] = selected;
	outcome["mode"] = selected ? "CANDIDATE_SELECTED" : "FALLBACK_SELECTED";
	outcome["reason"] = p_reason;
}

void RenderGpuOcclusionContext::prepare(RID p_viewport, uint64_t p_epoch, uint64_t p_generation, int32_t p_view_index, const Size2i &p_size, const Projection &p_projection, const Transform3D &p_camera_transform, RID p_depth, int64_t p_compute_list) {
	viewport = p_viewport;
	epoch = p_epoch;
	configuration_generation = p_generation;
	view_index = p_view_index;
	internal_size = p_size;
	projection = p_projection;
	camera_transform = p_camera_transform;
	depth = p_depth;
	compute_list = p_compute_list;
	active = p_viewport.is_valid() && p_epoch > 0 && p_generation > 0 && p_epoch <= INT64_MAX && p_generation <= INT64_MAX && p_view_index == 0 && p_size.x > 0 && p_size.y > 0 && p_depth.is_valid() && p_compute_list >= 0;
}

void RenderGpuOcclusionContext::invalidate() {
	active = false;
	depth = RID();
	compute_list = -1;
}

void RenderGpuOcclusionContext::_bind_methods() {
	ClassDB::bind_method(D_METHOD("is_active"), &RenderGpuOcclusionContext::is_active);
	ClassDB::bind_method(D_METHOD("get_viewport"), &RenderGpuOcclusionContext::get_viewport);
	ClassDB::bind_method(D_METHOD("get_epoch"), &RenderGpuOcclusionContext::get_epoch);
	ClassDB::bind_method(D_METHOD("get_configuration_generation"), &RenderGpuOcclusionContext::get_configuration_generation);
	ClassDB::bind_method(D_METHOD("get_view_index"), &RenderGpuOcclusionContext::get_view_index);
	ClassDB::bind_method(D_METHOD("get_internal_size"), &RenderGpuOcclusionContext::get_internal_size);
	ClassDB::bind_method(D_METHOD("get_projection"), &RenderGpuOcclusionContext::get_projection);
	ClassDB::bind_method(D_METHOD("get_camera_transform"), &RenderGpuOcclusionContext::get_camera_transform);
	ClassDB::bind_method(D_METHOD("get_depth"), &RenderGpuOcclusionContext::get_depth);
	ClassDB::bind_method(D_METHOD("get_compute_list"), &RenderGpuOcclusionContext::get_compute_list);
}
