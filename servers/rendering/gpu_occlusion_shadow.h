/**************************************************************************/
/*  gpu_occlusion_shadow.h                                                */
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

#include <cmath>
#include <cstdint>

namespace GpuOcclusionShadow {

// Plain metadata only. Capture retains one light and never bounds the visible
// CPU light list by a GPU cluster packing limit. The renderer supplies the
// enum classification and actual parameter count from LightStorage.
template <uint32_t ParameterCount, typename Real = float>
struct LightSnapshot {
	uint64_t instance = 0;
	uint64_t base = 0;
	uint64_t version = 0;
	uint64_t projector = 0;
	uint64_t atlas_texture = 0;
	int32_t atlas_size = 0;
	bool shadow_enabled = false;
	bool directional = false;
	bool orthogonal = false;
	bool sky_only = false;
	bool reverse_cull = false;
	bool blend_splits = false;
	bool negative = false;
	bool distance_fade = false;
	int32_t light_type = 0;
	int32_t directional_mode = 0;
	int32_t sky_mode = 0;
	int32_t bake_mode = 0;
	uint32_t cull_mask = 0;
	uint32_t instance_cull_mask = 0;
	uint32_t caster_mask = 0;
	uint32_t max_sdfgi_cascade = 0;
	Real transform[12] = {};
	Real shadow_transform[12] = {};
	Real shadow_camera[16] = {};
	float parameters[ParameterCount] = {};
	float color[4] = {};
	float distance_fade_parameters[3] = {};
	float shadow_range = 0;
	float shadow_range_begin = 0;
	float shadow_split = 0;
	float shadow_texel_size = 0;
	float shadow_bias_scale = 0;
	Real shadow_uv_scale[2] = {};

	template <typename T, uint32_t N>
	static bool finite_array(const T (&p_values)[N]) {
		for (uint32_t i = 0; i < N; i++) {
			if (!std::isfinite(p_values[i])) {
				return false;
			}
		}
		return true;
	}

	template <typename T, uint32_t N>
	static bool equal_array(const T (&p_a)[N], const T (&p_b)[N]) {
		for (uint32_t i = 0; i < N; i++) {
			if (p_a[i] != p_b[i]) {
				return false;
			}
		}
		return true;
	}

	bool is_finite() const {
		return finite_array(transform) && finite_array(shadow_transform) && finite_array(shadow_camera) &&
				finite_array(parameters) && finite_array(color) && finite_array(distance_fade_parameters) &&
				std::isfinite(shadow_range) && std::isfinite(shadow_range_begin) && std::isfinite(shadow_split) &&
				std::isfinite(shadow_texel_size) && std::isfinite(shadow_bias_scale) && finite_array(shadow_uv_scale);
	}

	bool operator==(const LightSnapshot &p_other) const {
		return instance == p_other.instance && base == p_other.base && version == p_other.version && projector == p_other.projector &&
				atlas_texture == p_other.atlas_texture && atlas_size == p_other.atlas_size &&
				shadow_enabled == p_other.shadow_enabled && directional == p_other.directional && orthogonal == p_other.orthogonal && sky_only == p_other.sky_only &&
				reverse_cull == p_other.reverse_cull && blend_splits == p_other.blend_splits && negative == p_other.negative && distance_fade == p_other.distance_fade &&
				light_type == p_other.light_type && directional_mode == p_other.directional_mode && sky_mode == p_other.sky_mode && bake_mode == p_other.bake_mode &&
				cull_mask == p_other.cull_mask && instance_cull_mask == p_other.instance_cull_mask && caster_mask == p_other.caster_mask && max_sdfgi_cascade == p_other.max_sdfgi_cascade &&
				equal_array(transform, p_other.transform) && equal_array(shadow_transform, p_other.shadow_transform) && equal_array(shadow_camera, p_other.shadow_camera) &&
				equal_array(parameters, p_other.parameters) && equal_array(color, p_other.color) && equal_array(distance_fade_parameters, p_other.distance_fade_parameters) &&
				shadow_range == p_other.shadow_range && shadow_range_begin == p_other.shadow_range_begin && shadow_split == p_other.shadow_split &&
				shadow_texel_size == p_other.shadow_texel_size && shadow_bias_scale == p_other.shadow_bias_scale && equal_array(shadow_uv_scale, p_other.shadow_uv_scale);
	}
};

struct RawWork {
	uint64_t light = 0;
	int pass = 0;

	bool operator==(const RawWork &p_other) const {
		return light == p_other.light && pass == p_other.pass;
	}
};

template <uint32_t ParameterCount, typename Real = float>
struct Snapshot {
	bool has_directional = false;
	LightSnapshot<ParameterCount, Real> light;
	RawWork work;

	bool operator==(const Snapshot &p_other) const {
		return has_directional == p_other.has_directional &&
				(!has_directional || (light == p_other.light && work == p_other.work));
	}
};

// The accessor must validate instance/base ownership before calling storage
// getters. Only shadow-enabled entries need full finite metadata. Rejection
// always clears the output so a previous capture cannot survive as a candidate.
template <uint32_t ParameterCount, typename Real, typename Accessor>
bool capture(uint32_t p_visible_count, const Accessor &p_get_light, int p_raw_count, const RawWork *p_raw_work, bool p_derived_lists_empty, Snapshot<ParameterCount, Real> &r_snapshot) {
	r_snapshot = {};
	if (!p_derived_lists_empty || p_raw_count < 0 || p_raw_count > 1 || (p_raw_count > 0 && !p_raw_work)) {
		return false;
	}
	Snapshot<ParameterCount, Real> snapshot;
	for (uint32_t i = 0; i < p_visible_count; i++) {
		LightSnapshot<ParameterCount, Real> light;
		if (!p_get_light(i, light)) {
			return false;
		}
		if (!light.shadow_enabled) {
			continue;
		}
		if (snapshot.has_directional || light.instance == 0 || light.base == 0 ||
				!light.directional || !light.orthogonal || light.sky_only || !light.is_finite()) {
			return false;
		}
		snapshot.has_directional = true;
		snapshot.light = light;
	}
	if (snapshot.has_directional) {
		if (p_raw_count != 1 || p_raw_work->light != snapshot.light.instance || p_raw_work->pass != 0) {
			return false;
		}
		snapshot.work = *p_raw_work;
	} else if (p_raw_count != 0) {
		return false;
	}
	r_snapshot = snapshot;
	return true;
}

template <uint32_t ParameterCount, typename Real, typename Accessor>
bool matches(const Snapshot<ParameterCount, Real> &p_snapshot, uint32_t p_visible_count, const Accessor &p_get_light, int p_raw_count, const RawWork *p_raw_work, bool p_derived_lists_empty) {
	Snapshot<ParameterCount, Real> current;
	return capture(p_visible_count, p_get_light, p_raw_count, p_raw_work, p_derived_lists_empty, current) && current == p_snapshot;
}

} // namespace GpuOcclusionShadow
