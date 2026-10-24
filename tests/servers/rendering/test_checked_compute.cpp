/**************************************************************************/
/*  test_checked_compute.cpp                                             */
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

TEST_FORCE_LINK(test_checked_compute)

#include "core/object/class_db.h"
#include "servers/rendering/rendering_device.h"
#include "servers/rendering/rendering_device_graph.h"

#include <type_traits>

namespace TestCheckedCompute {

static_assert(std::is_same_v<decltype(&RenderingDevice::compute_list_dispatch_indirect_checked), Error (RenderingDevice::*)(RenderingDevice::ComputeListID, RID, int64_t)>);
static_assert(std::is_same_v<decltype(&RenderingDevice::compute_list_dispatch_checked), Error (RenderingDevice::*)(RenderingDevice::ComputeListID, uint32_t, uint32_t, uint32_t)>);
static_assert(std::is_same_v<decltype(&RenderingDevice::compute_list_bind_uniform_set_checked), Error (RenderingDevice::*)(RenderingDevice::ComputeListID, RID, uint32_t)>);
static_assert(std::is_same_v<decltype(&RenderingDevice::compute_list_begin_callback_scope), RenderingDevice::ComputeListID (RenderingDevice::*)()>);
static_assert(std::is_same_v<decltype(&RenderingDevice::compute_list_end_callback_scope), bool (RenderingDevice::*)()>);

TEST_CASE("[SceneTree][Rendering][IndirectDraw][GPUOcclusion] Script callbacks cannot acquire or release native callback scopes") {
	CHECK(ClassDB::get_method("RenderingDevice", "compute_list_begin_callback_scope") == nullptr);
	CHECK(ClassDB::get_method("RenderingDevice", "compute_list_end_callback_scope") == nullptr);
	CHECK(ClassDB::get_method("RenderingDevice", "compute_callback_reject_operation") == nullptr);
	CHECK(ClassDB::get_method("RenderingDevice", "storage_buffer_is_nonpersistent") == nullptr);
}

TEST_CASE("[Rendering][IndirectDraw][CheckedCompute] Compute alias preflight is const and leaves new or recorded trackers unchanged") {
	RenderingDeviceGraph graph;
	RenderingDeviceGraph::ResourceTracker tracker;
	graph.add_compute_list_begin();
	const RenderingDeviceGraph &query = graph;
	CHECK(query.is_compute_list_usage_compatible(nullptr, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ));
	CHECK(query.is_compute_list_usage_compatible(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ));
	CHECK(tracker.command_frame == -1);
	CHECK(tracker.compute_list_index == -1);
	graph.add_compute_list_usage(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_STORAGE_BUFFER_READ);
	const auto frame = tracker.command_frame;
	const auto segment = tracker.compute_list_index;
	CHECK_FALSE(query.is_compute_list_usage_compatible(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ));
	CHECK(tracker.command_frame == frame);
	CHECK(tracker.compute_list_index == segment);
	CHECK(tracker.compute_list_usage == RenderingDeviceGraph::RESOURCE_USAGE_STORAGE_BUFFER_READ);
}

TEST_CASE("[Rendering][IndirectDraw][CheckedCompute] Shader-first and arguments-first usages conflict in both directions") {
	for (auto shader_usage : { RenderingDeviceGraph::RESOURCE_USAGE_STORAGE_BUFFER_READ, RenderingDeviceGraph::RESOURCE_USAGE_STORAGE_BUFFER_READ_WRITE, RenderingDeviceGraph::RESOURCE_USAGE_UNIFORM_BUFFER_READ }) {
		RenderingDeviceGraph graph;
		RenderingDeviceGraph::ResourceTracker shader_first;
		RenderingDeviceGraph::ResourceTracker arguments_first;
		graph.add_compute_list_begin();
		graph.add_compute_list_usage(&shader_first, shader_usage);
		graph.add_compute_list_usage(&arguments_first, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ);
		CHECK_FALSE(graph.is_compute_list_usage_compatible(&shader_first, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ));
		CHECK_FALSE(graph.is_compute_list_usage_compatible(&arguments_first, shader_usage));
		CHECK(graph.is_compute_list_usage_compatible(&arguments_first, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ));
	}
}

TEST_CASE("[Rendering][IndirectDraw][CheckedCompute] A new graph segment permits an argument bank to change roles") {
	RenderingDeviceGraph graph;
	RenderingDeviceGraph::ResourceTracker tracker;
	graph.add_compute_list_begin();
	graph.add_compute_list_usage(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_STORAGE_BUFFER_READ_WRITE);
	CHECK_FALSE(graph.is_compute_list_usage_compatible(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ));
	const auto old_segment = tracker.compute_list_index;
	graph.add_compute_list_begin();
	CHECK(graph.is_compute_list_usage_compatible(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ));
	CHECK(tracker.compute_list_index == old_segment);
	graph.add_compute_list_usage(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ);
	CHECK(tracker.compute_list_index != old_segment);
	CHECK_FALSE(graph.is_compute_list_usage_compatible(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_STORAGE_BUFFER_READ_WRITE));
}

TEST_CASE("[Rendering][IndirectDraw][CheckedCompute] A stale frame is compatible without resetting its tracker") {
	RenderingDeviceGraph graph;
	RenderingDeviceGraph::ResourceTracker tracker;
	graph.add_compute_list_begin();
	graph.add_compute_list_usage(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_STORAGE_BUFFER_READ);
	tracker.command_frame -= 1;
	const auto frame = tracker.command_frame;
	const auto segment = tracker.compute_list_index;
	CHECK(graph.is_compute_list_usage_compatible(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ));
	CHECK(tracker.command_frame == frame);
	CHECK(tracker.compute_list_index == segment);
	CHECK(tracker.compute_list_usage == RenderingDeviceGraph::RESOURCE_USAGE_STORAGE_BUFFER_READ);
}

TEST_CASE("[SceneTree][Rendering][IndirectDraw][CheckedCompute] Checked compute bindings expose Error returns and signed64 indirect offsets") {
	const MethodBind *indirect = ClassDB::get_method("RenderingDevice", "compute_list_dispatch_indirect_checked");
	const MethodBind *direct = ClassDB::get_method("RenderingDevice", "compute_list_dispatch_checked");
	const MethodBind *bind = ClassDB::get_method("RenderingDevice", "compute_list_bind_uniform_set_checked");
	REQUIRE(indirect != nullptr);
	REQUIRE(direct != nullptr);
	REQUIRE(bind != nullptr);
	CHECK(indirect->get_argument_count() == 3);
	CHECK(direct->get_argument_count() == 4);
	CHECK(bind->get_argument_count() == 3);
	for (const MethodBind *method : { indirect, direct, bind }) {
		CHECK(method->get_return_info().type == Variant::INT);
	}
	CHECK(indirect->get_argument_type(1) == Variant::RID);
	CHECK(indirect->get_argument_type(2) == Variant::INT);
#ifdef DEBUG_ENABLED
	CHECK(indirect->get_argument_meta(2) == GodotTypeInfo::METADATA_INT_IS_INT64);
#endif
}

} // namespace TestCheckedCompute
