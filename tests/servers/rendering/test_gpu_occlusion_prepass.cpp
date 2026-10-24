/**************************************************************************/
/*  test_gpu_occlusion_prepass.cpp                                        */
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

TEST_FORCE_LINK(test_gpu_occlusion_prepass)

#include "core/object/class_db.h"
#include "servers/rendering/gpu_occlusion_prepass.h"

namespace TestGpuOcclusionPrepass {

static GpuOcclusionConsumer consumer(uint64_t p_instance, int64_t p_base, int64_t p_command_offset) {
	GpuOcclusionConsumer result;
	result.instance = RID::from_uint64(p_instance);
	result.identity_bank = RID::from_uint64(100);
	result.candidate_identity_base = p_base;
	result.logical_slot_capacity = 16;
	result.draw_slot_capacity = 64;
	result.candidate_commands = RID::from_uint64(200);
	result.candidate_command_byte_offset = p_command_offset;
	result.fallback_commands = RID::from_uint64(300);
	result.fallback_command_byte_offset = 0;
	result.shader_buffers.push_back(result.identity_bank);
	return result;
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusion] Signed metadata must fit before any narrowing") {
	using GpuOcclusionMetadata::validate_consumer_numbers;
	CHECK(validate_consumer_numbers(16, 16, 32, 0) == OK);
	CHECK(validate_consumer_numbers(INT32_MAX - 1, 1, INT32_MAX, INT32_MAX - 3) == OK);
	CHECK(validate_consumer_numbers(16, 0, 32, 0) == ERR_INVALID_PARAMETER);
	CHECK(validate_consumer_numbers(15, 16, 32, 0) == ERR_INVALID_PARAMETER);
	CHECK(validate_consumer_numbers(16, 16, 31, 0) == ERR_INVALID_PARAMETER);
	CHECK(validate_consumer_numbers(INT64_MAX, 16, 32, 0) == ERR_INVALID_PARAMETER);
	CHECK(validate_consumer_numbers(16, INT64_MAX, 32, 0) == ERR_INVALID_PARAMETER);
	CHECK(validate_consumer_numbers(16, 16, INT64_MAX, 0) == ERR_INVALID_PARAMETER);
	CHECK(validate_consumer_numbers(16, 16, 32, -4) == ERR_INVALID_PARAMETER);
	CHECK(validate_consumer_numbers(16, 16, 32, 2) == ERR_INVALID_PARAMETER);
	CHECK(validate_consumer_numbers(16, 16, 32, INT64_MAX) == ERR_INVALID_PARAMETER);
	CHECK(validate_consumer_numbers(INT32_MAX, 1, INT32_MAX, 0) == ERR_INVALID_PARAMETER);
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusion] Half open intervals do not wrap or collide at adjacent endpoints") {
	using GpuOcclusionMetadata::intervals_overlap;
	CHECK_FALSE(intervals_overlap(0, 20, 20, 20));
	CHECK(intervals_overlap(0, 20, 19, 20));
	CHECK(intervals_overlap(19, 20, 0, 20));
	CHECK_FALSE(intervals_overlap(0, 0, 0, 20));
	CHECK(intervals_overlap(UINT64_MAX - 3, 4, UINT64_MAX, 1));
	CHECK_FALSE(intervals_overlap(UINT64_MAX - 3, 4, 0, 4));
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusion] Candidate regions are exclusive while immutable fallbacks may share") {
	using GpuOcclusionMetadata::consumers_conflict;
	GpuOcclusionConsumer a = consumer(1, 16, 0);
	GpuOcclusionConsumer b = consumer(2, 32, 20);
	CHECK_FALSE(consumers_conflict(a, b));
	b.candidate_identity_base = 31;
	CHECK(consumers_conflict(a, b));
	b.candidate_identity_base = 32;
	b.logical_slot_capacity = 32; // B's fallback [0,32) overlaps A's candidate.
	CHECK(consumers_conflict(a, b));
	b = consumer(2, 32, 16);
	CHECK(consumers_conflict(a, b));
	b = consumer(2, 32, 20);
	b.fallback_commands = a.candidate_commands;
	b.fallback_command_byte_offset = 16;
	CHECK(consumers_conflict(a, b));
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusion] Every command bank excludes all registered shader banks") {
	using GpuOcclusionMetadata::consumers_conflict;
	GpuOcclusionConsumer a = consumer(1, 16, 0);
	GpuOcclusionConsumer b = consumer(2, 32, 20);
	b.shader_buffers.push_back(a.candidate_commands);
	CHECK(consumers_conflict(a, b));
	CHECK(consumers_conflict(b, a));
	b = consumer(2, 32, 20);
	b.shader_buffers.push_back(a.fallback_commands);
	CHECK(consumers_conflict(a, b));
	b = consumer(2, 32, 20);
	b.identity_bank = a.candidate_commands;
	CHECK(consumers_conflict(a, b));
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusion] Acknowledgments require exact current signed integer identity") {
	Dictionary ack;
	ack["epoch"] = int64_t(2);
	ack["configuration_generation"] = int64_t(3);
	ack["error"] = int64_t(OK);
	CHECK(GpuOcclusionPrepassState::validate_ack(ack, 2, 3));
	CHECK_FALSE(GpuOcclusionPrepassState::validate_ack(ack, 1, 3));
	CHECK_FALSE(GpuOcclusionPrepassState::validate_ack(ack, 2, 4));
	CHECK_FALSE(GpuOcclusionPrepassState::validate_ack(ack, 0, 0));
	CHECK_FALSE(GpuOcclusionPrepassState::validate_ack(ack, UINT64_MAX, 3));
	for (const char *field : { "epoch", "configuration_generation", "error" }) {
		Dictionary changed = ack.duplicate();
		changed[field] = true;
		CHECK_FALSE(GpuOcclusionPrepassState::validate_ack(changed, 2, 3));
		changed[field] = 0.0;
		CHECK_FALSE(GpuOcclusionPrepassState::validate_ack(changed, 2, 3));
		changed.erase(field);
		CHECK_FALSE(GpuOcclusionPrepassState::validate_ack(changed, 2, 3));
	}
	ack["error"] = int64_t(FAILED);
	CHECK_FALSE(GpuOcclusionPrepassState::validate_ack(ack, 2, 3));
	CHECK_FALSE(GpuOcclusionPrepassState::validate_ack(Variant(), 2, 3));
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusion] Every recording starts fallback and reentry cannot alter the active identity") {
	Ref<GpuOcclusionPrepassState> state;
	state.instantiate();
	state->configuration_generation = 5;
	CHECK(state->begin_recording(RID::from_uint64(1)));
	CHECK(state->epoch == 1);
	CHECK(state->recording);
	state->set_outcome("ACKNOWLEDGED", true);
	CHECK_FALSE(state->begin_recording(RID::from_uint64(2)));
	CHECK(state->viewport == RID::from_uint64(1));
	CHECK(state->epoch == 1);
	CHECK(state->selected);
	state->recording = false;
	CHECK(state->begin_recording(RID::from_uint64(1)));
	CHECK(state->epoch == 2);
	CHECK_FALSE(state->selected);
	CHECK(String(state->outcome["mode"]) == "FALLBACK_SELECTED");
	state->recording = false;
	state->epoch = INT64_MAX;
	CHECK_FALSE(state->begin_recording(RID::from_uint64(1)));
	CHECK(state->epoch == INT64_MAX);
	CHECK_FALSE(state->selected);
	CHECK_FALSE(state->recording);
}

TEST_CASE("[Rendering][IndirectDraw][GPUOcclusion] Context invalidation revokes borrowed render data") {
	Ref<RenderGpuOcclusionContext> context;
	context.instantiate();
	CHECK_FALSE(context->is_active());
	context->prepare(RID::from_uint64(1), 2, 3, 0, Size2i(64, 32), Projection(), Transform3D(), RID::from_uint64(4), 5);
	CHECK(context->is_active());
	CHECK(context->get_epoch() == 2);
	CHECK(context->get_configuration_generation() == 3);
	CHECK(context->get_view_index() == 0);
	CHECK(context->get_internal_size() == Size2i(64, 32));
	CHECK(context->get_depth() == RID::from_uint64(4));
	CHECK(context->get_compute_list() == 5);
	context->invalidate();
	CHECK_FALSE(context->is_active());
	CHECK(context->get_viewport().is_null());
	CHECK(context->get_depth().is_null());
	CHECK(context->get_epoch() == 0);
	CHECK(context->get_configuration_generation() == 0);
	CHECK(context->get_compute_list() == -1);
	CHECK(context->get_view_index() == -1);
	CHECK(context->get_internal_size() == Size2i());
}

TEST_CASE("[SceneTree][Rendering][IndirectDraw][GPUOcclusion] Public API metadata preserves signed ranges and context getter types") {
	const MethodBind *registration = ClassDB::get_method("RenderingServer", "viewport_register_gpu_occlusion_consumer");
	REQUIRE(registration != nullptr);
	CHECK(registration->get_argument_count() == 7);
	CHECK(registration->get_argument_type(3) == Variant::INT);
	CHECK(registration->get_argument_type(5) == Variant::INT);
	CHECK(registration->get_argument_type(6) == Variant::INT);
	CHECK(registration->get_return_info().type == Variant::INT);
	const MethodBind *depth = ClassDB::get_method("RenderGpuOcclusionContext", "get_depth");
	const MethodBind *epoch = ClassDB::get_method("RenderGpuOcclusionContext", "get_epoch");
	REQUIRE(depth != nullptr);
	REQUIRE(epoch != nullptr);
	CHECK(depth->get_return_info().type == Variant::RID);
	CHECK(epoch->get_return_info().type == Variant::INT);
}

} // namespace TestGpuOcclusionPrepass
