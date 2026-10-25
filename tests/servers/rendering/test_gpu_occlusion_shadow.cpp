/**************************************************************************/
/*  test_gpu_occlusion_shadow.cpp                                          */
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

TEST_FORCE_LINK(test_gpu_occlusion_shadow)

#include "servers/rendering/gpu_occlusion_shadow.h"
#include "servers/rendering/rendering_server_enums.h"

#include <algorithm>
#include <cstdint>
#include <limits>
#include <vector>

namespace TestGpuOcclusionShadow {

// The helper receives storage snapshots, not a renderer or an RD device. An
// independently authored fixture supplies one admitted light and raw work tuple.
// The tests below mutate one fact at a time; they do not implement admission.
constexpr uint32_t PARAMETER_COUNT = RenderingServerEnums::LIGHT_PARAM_MAX;
using Light = GpuOcclusionShadow::LightSnapshot<PARAMETER_COUNT>;
using Snapshot = GpuOcclusionShadow::Snapshot<PARAMETER_COUNT>;
using Work = GpuOcclusionShadow::RawWork;

static Light directional(uint64_t p_instance = 11) {
	Light light;
	light.instance = p_instance;
	light.base = 21;
	light.version = 31;
	light.atlas_texture = 77;
	light.atlas_size = 128;
	light.shadow_enabled = true;
	light.directional = true;
	light.orthogonal = true;
	light.transform[0] = light.transform[4] = light.transform[8] = 1;
	light.shadow_transform[0] = light.shadow_transform[4] = light.shadow_transform[8] = 1;
	light.shadow_camera[0] = light.shadow_camera[5] = light.shadow_camera[10] = light.shadow_camera[15] = 1;
	for (float &component : light.color) {
		component = 1;
	}
	return light;
}

struct View {
	std::vector<Light> lights;
	std::vector<Work> work;
	bool derived_lists_empty = true;
	mutable std::vector<uint32_t> visits;
	int64_t missing_index = -1;

	bool capture(Snapshot &r_snapshot) const {
		visits.clear();
		return GpuOcclusionShadow::capture<PARAMETER_COUNT>(uint32_t(lights.size()), [&](uint32_t p_index, Light &r_light) {
			visits.push_back(p_index);
			if (p_index == missing_index) {
				return false;
			}
			r_light = lights[p_index];
			return true;
		}, uint32_t(work.size()), work.empty() ? nullptr : work.data(), derived_lists_empty, r_snapshot);
	}

	bool matches(const Snapshot &p_snapshot) const {
		return GpuOcclusionShadow::matches<PARAMETER_COUNT>(p_snapshot, uint32_t(lights.size()), [&](uint32_t p_index, Light &r_light) {
			if (p_index == missing_index) {
				return false;
			}
			r_light = lights[p_index];
			return true;
		}, int(work.size()), work.empty() ? nullptr : work.data(), derived_lists_empty);
	}
};

static View supported() {
	View view;
	view.lights.push_back(directional());
	view.work.push_back({ 11, 0 });
	return view;
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusionShadow] Empty and unshadowed views preserve admission") {
	Snapshot snapshot;
	View view;
	CHECK(view.capture(snapshot));
	CHECK_FALSE(snapshot.has_directional);
	CHECK(view.matches(snapshot));
	for (uint32_t i = 0; i < 4097; i++) {
		Light light = directional(100 + i);
		light.shadow_enabled = false;
		light.directional = false;
		light.orthogonal = false;
		view.lights.push_back(light);
	}
	CHECK(view.capture(snapshot));
	CHECK_FALSE(snapshot.has_directional);
	CHECK(view.visits.size() == view.lights.size());
	CHECK(view.matches(snapshot));
	view.work.push_back({ 100, 0 });
	CHECK_FALSE(view.capture(snapshot));
	view.work.clear();
	view.derived_lists_empty = false;
	CHECK_FALSE(view.capture(snapshot));
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusionShadow] One orthogonal directional requires its exact scheduled pass zero") {
	Snapshot snapshot;
	View view = supported();
	REQUIRE(view.capture(snapshot));
	CHECK(snapshot.has_directional);
	CHECK(snapshot.light.instance == 11);
	CHECK(snapshot.light.base == 21);
	CHECK(snapshot.work.light == 11);
	CHECK(snapshot.work.pass == 0);
	CHECK(view.matches(snapshot));
	view.work.clear(); // A missing update cannot be called a cached directional success.
	CHECK_FALSE(view.capture(snapshot));
	view.work.push_back({ 12, 0 });
	CHECK_FALSE(view.capture(snapshot));
	view.work[0] = { 11, -1 };
	CHECK_FALSE(view.capture(snapshot));
	view.work[0].pass = 1;
	CHECK_FALSE(view.capture(snapshot));
	view.work[0].pass = 0;
	view.work.push_back(view.work[0]);
	CHECK_FALSE(view.capture(snapshot));
	view.work.pop_back();
	view.derived_lists_empty = false;
	CHECK_FALSE(view.capture(snapshot));
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusionShadow] Unsupported lighting fails instead of widening the scope") {
	Snapshot snapshot;
	View view = supported();
	for (int scenario = 0; scenario < 7; scenario++) {
		view = supported();
		switch (scenario) {
			case 0: view.lights[0].directional = false; break; // Spot, omni, cube and area are all positional.
			case 1: view.lights[0].orthogonal = false; break; // Both two- and four-split directional modes.
			case 2: view.lights[0].sky_only = true; break;
			case 3: view.lights[0].shadow_enabled = false; break;
			case 4: view.lights[0].instance = 0; break;
			case 5: view.lights[0].base = 0; break;
			case 6: view.missing_index = 0; break; // A freed borrowed light cannot be inspected safely.
		}
		CAPTURE(scenario);
		CHECK_FALSE(view.capture(snapshot));
	}
	view = supported();
	view.lights.push_back(view.lights[0]); // Duplicate instance is also a second visible shadow light.
	CHECK_FALSE(view.capture(snapshot));
	view = supported();
	view.lights.push_back(directional(12));
	CHECK_FALSE(view.capture(snapshot));
	view.lights[1].directional = false;
	CHECK_FALSE(view.capture(snapshot));
	for (auto type : { RenderingServerEnums::LIGHT_SPOT, RenderingServerEnums::LIGHT_OMNI, RenderingServerEnums::LIGHT_AREA }) {
		view = supported();
		view.lights[0].light_type = type;
		view.lights[0].directional = false;
		CAPTURE(type);
		CHECK_FALSE(view.capture(snapshot));
	}
	for (auto mode : { RenderingServerEnums::LIGHT_DIRECTIONAL_SHADOW_PARALLEL_2_SPLITS, RenderingServerEnums::LIGHT_DIRECTIONAL_SHADOW_PARALLEL_4_SPLITS }) {
		view = supported();
		view.lights[0].directional_mode = mode;
		view.lights[0].orthogonal = false;
		CAPTURE(mode);
		CHECK_FALSE(view.capture(snapshot));
	}
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusionShadow] Full visible list is checked beyond clustered light packing limits") {
	Snapshot snapshot;
	View view;
	for (uint32_t i = 0; i < 4097; i++) {
		Light light = directional(100 + i);
		light.shadow_enabled = false;
		view.lights.push_back(light);
	}
	view.lights.push_back(directional());
	view.work.push_back({ 11, 0 });
	REQUIRE(view.capture(snapshot));
	CHECK(snapshot.has_directional);
	CHECK(snapshot.light.instance == 11);
	REQUIRE(view.visits.size() == 4098);
	for (uint32_t i = 0; i < view.visits.size(); i++) {
		CHECK(view.visits[i] == i);
	}
	view.lights.push_back(directional(12));
	CHECK_FALSE(view.capture(snapshot));
	CHECK(view.visits.back() == 4098);
	view.lights.back().shadow_enabled = false;
	view.missing_index = 4098;
	CHECK_FALSE(view.capture(snapshot));
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusionShadow] Failure clears retained admission and recovery requires a fresh capture") {
	View view = supported();
	Snapshot snapshot;
	REQUIRE(view.capture(snapshot));
	const Snapshot admitted = snapshot;
	for (int scenario = 0; scenario < 5; scenario++) {
		view = supported();
		REQUIRE(view.capture(snapshot));
		switch (scenario) {
			case 0: view.work.clear(); break;
			case 1: view.work[0].pass = 1; break;
			case 2: view.lights.push_back(directional(12)); break;
			case 3: view.missing_index = 0; break;
			case 4: view.derived_lists_empty = false; break;
		}
		CAPTURE(scenario);
		CHECK_FALSE(view.matches(admitted));
		CHECK_FALSE(view.capture(snapshot));
		CHECK_FALSE(snapshot.has_directional);
		CHECK(snapshot.light.instance == 0);
		CHECK(snapshot.work.light == 0);
		CHECK(admitted.has_directional); // A failed recapture does not mutate a borrowed prior snapshot.
		view = supported();
		CHECK(view.matches(admitted));
		CHECK(view.capture(snapshot));
		CHECK(snapshot == admitted);
	}
	view.lights.clear();
	view.work.clear();
	CHECK_FALSE(view.matches(admitted));
	CHECK(view.capture(snapshot));
	CHECK_FALSE(snapshot.has_directional);
	view = supported();
	CHECK_FALSE(view.matches(snapshot)); // A newly added light cannot match an old no-shadow capture.
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusionShadow] Impossible raw work is rejected before accessing borrowed lights") {
	const Work valid_work = { 11, 0 };
	for (int raw_count : { -1, 2, std::numeric_limits<int>::max() }) {
		Snapshot snapshot;
		snapshot.has_directional = true;
		snapshot.light = directional();
		uint32_t visits = 0;
		CAPTURE(raw_count);
		CHECK_FALSE(GpuOcclusionShadow::capture<PARAMETER_COUNT>(std::numeric_limits<uint32_t>::max(), [&](uint32_t, Light &) {
			visits++;
			return false;
		}, raw_count, &valid_work, true, snapshot));
		CHECK(visits == 0);
		CHECK_FALSE(snapshot.has_directional);
		CHECK(snapshot.light.instance == 0);
	}
	Snapshot snapshot;
	uint32_t visits = 0;
	CHECK_FALSE(GpuOcclusionShadow::capture<PARAMETER_COUNT>(1, [&](uint32_t, Light &) {
		visits++;
		return false;
	}, 1, nullptr, true, snapshot));
	CHECK(visits == 0);
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusionShadow] Relevant scalar state cannot mutate behind an unchanged light version") {
	const View original = supported();
	Snapshot admitted;
	REQUIRE(original.capture(admitted));
	const uint64_t captured_version = admitted.light.version;

	uint64_t Light::*const identities[] = { &Light::instance, &Light::base, &Light::version, &Light::projector, &Light::atlas_texture };
	for (uint32_t i = 0; i < sizeof(identities) / sizeof(identities[0]); i++) {
		View changed = original;
		changed.lights[0].*identities[i] += 1;
		CAPTURE(i);
		CHECK_FALSE(changed.matches(admitted));
	}
	bool Light::*const flags[] = { &Light::shadow_enabled, &Light::directional, &Light::orthogonal, &Light::sky_only,
		&Light::reverse_cull, &Light::blend_splits, &Light::negative, &Light::distance_fade };
	for (uint32_t i = 0; i < sizeof(flags) / sizeof(flags[0]); i++) {
		View changed = original;
		auto member = flags[i];
		changed.lights[0].*member = !(changed.lights[0].*member);
		CAPTURE(i);
		CHECK(changed.lights[0].version == captured_version);
		CHECK_FALSE(changed.matches(admitted));
	}
	int32_t Light::*const modes[] = { &Light::light_type, &Light::directional_mode, &Light::sky_mode, &Light::bake_mode, &Light::atlas_size };
	for (uint32_t i = 0; i < sizeof(modes) / sizeof(modes[0]); i++) {
		View changed = original;
		changed.lights[0].*modes[i] += 1;
		CAPTURE(i);
		Snapshot fresh;
		CHECK(changed.capture(fresh)); // Still-admitted metadata must nevertheless invalidate the old capture.
		CHECK(changed.lights[0].version == captured_version);
		CHECK_FALSE(changed.matches(admitted));
	}
	uint32_t Light::*const masks[] = { &Light::cull_mask, &Light::instance_cull_mask, &Light::caster_mask, &Light::max_sdfgi_cascade };
	for (uint32_t i = 0; i < sizeof(masks) / sizeof(masks[0]); i++) {
		View changed = original;
		changed.lights[0].*masks[i] ^= 0x80u;
		CAPTURE(i);
		Snapshot fresh;
		CHECK(changed.capture(fresh));
		CHECK(changed.lights[0].version == captured_version);
		CHECK_FALSE(changed.matches(admitted));
	}
}

// A pointer to each actual numeric slot keeps this test exhaustive when a
// parameter count changes, without copying admission or equality conditions.
static std::vector<float *> numeric_slots(Light &p_light) {
	std::vector<float *> slots;
	auto append = [&](auto &p_array) {
		for (float &value : p_array) {
			slots.push_back(&value);
		}
	};
	append(p_light.transform);
	append(p_light.shadow_transform);
	append(p_light.shadow_camera);
	append(p_light.parameters);
	append(p_light.color);
	append(p_light.distance_fade_parameters);
	slots.push_back(&p_light.shadow_range);
	slots.push_back(&p_light.shadow_range_begin);
	slots.push_back(&p_light.shadow_split);
	slots.push_back(&p_light.shadow_texel_size);
	slots.push_back(&p_light.shadow_bias_scale);
	append(p_light.shadow_uv_scale);
	return slots;
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusionShadow] Every captured numeric component participates in revalidation") {
	View original = supported();
	Snapshot admitted;
	REQUIRE(original.capture(admitted));
	const uint32_t slot_count = uint32_t(numeric_slots(original.lights[0]).size());
	REQUIRE(slot_count == PARAMETER_COUNT + 54);
	for (uint32_t i = 0; i < slot_count; i++) {
		View changed = original;
		*numeric_slots(changed.lights[0])[i] += 0.125f;
		CAPTURE(i);
		Snapshot fresh;
		CHECK(changed.capture(fresh));
		CHECK(changed.lights[0].version == admitted.light.version);
		CHECK_FALSE(changed.matches(admitted));
		CHECK_FALSE(fresh.light == admitted.light);
		CHECK(original.matches(admitted));
	}
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusionShadow] Nonfinite state rejects every numeric slot and clears the output") {
	View original = supported();
	const uint32_t slot_count = uint32_t(numeric_slots(original.lights[0]).size());
	for (float invalid : { std::numeric_limits<float>::infinity(), -std::numeric_limits<float>::infinity(), std::numeric_limits<float>::quiet_NaN() }) {
		for (uint32_t i = 0; i < slot_count; i++) {
			View changed = original;
			*numeric_slots(changed.lights[0])[i] = invalid;
			CAPTURE(i);
			Snapshot snapshot;
			REQUIRE(original.capture(snapshot));
			CHECK_FALSE(changed.capture(snapshot));
			CHECK_FALSE(snapshot.has_directional);
			CHECK(snapshot.light.instance == 0);
		}
	}
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusionShadow] Light reordering preserves the same shadow identity but a changed set does not") {
	View view = supported();
	Light unshadowed = directional(99);
	unshadowed.shadow_enabled = false;
	view.lights.push_back(unshadowed);
	Snapshot admitted;
	REQUIRE(view.capture(admitted));
	std::reverse(view.lights.begin(), view.lights.end());
	CHECK(view.matches(admitted));
	view.lights.erase(view.lights.begin());
	CHECK(view.matches(admitted));
	// Unshadowed metadata is outside this shadow snapshot, not permission for a
	// callback to mutate a scene. The caller contract separately forbids that.
	unshadowed.parameters[0] = std::numeric_limits<float>::infinity();
	view.lights.insert(view.lights.begin(), unshadowed);
	CHECK(view.matches(admitted));
	view.lights[0].shadow_enabled = true;
	CHECK_FALSE(view.matches(admitted));
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusionShadow] Double precision transforms are not narrowed while snapshotting") {
	using DoubleLight = GpuOcclusionShadow::LightSnapshot<PARAMETER_COUNT, double>;
	using DoubleSnapshot = GpuOcclusionShadow::Snapshot<PARAMETER_COUNT, double>;
	DoubleLight light;
	light.instance = 11;
	light.base = 21;
	light.version = 31;
	light.shadow_enabled = light.directional = light.orthogonal = true;
	light.transform[9] = 1.0;
	light.shadow_uv_scale[0] = 1.0;
	const Work work = { 11, 0 };
	auto get = [&](uint32_t, DoubleLight &r_light) {
		r_light = light;
		return true;
	};
	DoubleSnapshot snapshot;
	REQUIRE(GpuOcclusionShadow::capture<PARAMETER_COUNT>(1, get, 1, &work, true, snapshot));
	CHECK(GpuOcclusionShadow::matches<PARAMETER_COUNT>(snapshot, 1, get, 1, &work, true));
	light.transform[9] += 1e-12; // Smaller than one float ULP at 1.
	CHECK(float(light.transform[9]) == 1.0f);
	CHECK_FALSE(GpuOcclusionShadow::matches<PARAMETER_COUNT>(snapshot, 1, get, 1, &work, true));
	light.transform[9] = 1.0;
	light.shadow_uv_scale[0] += 1e-12;
	CHECK(float(light.shadow_uv_scale[0]) == 1.0f);
	CHECK_FALSE(GpuOcclusionShadow::matches<PARAMETER_COUNT>(snapshot, 1, get, 1, &work, true));
	light.shadow_uv_scale[0] = 1.0;
	light.transform[9] = std::numeric_limits<double>::infinity();
	CHECK_FALSE(GpuOcclusionShadow::capture<PARAMETER_COUNT>(1, get, 1, &work, true, snapshot));
	CHECK_FALSE(snapshot.has_directional);
}

} // namespace TestGpuOcclusionShadow
