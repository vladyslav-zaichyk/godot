/**************************************************************************/
/*  test_indirect_count_graph.cpp                                         */
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

TEST_FORCE_LINK(test_indirect_count_graph)

#include "servers/rendering/rendering_device_graph.h"

namespace TestIndirectCountGraph {

TEST_CASE("[Rendering][IndirectDraw][IndirectCount] Alias preflight does not mutate a new or recorded tracker") {
	RenderingDeviceGraph graph;
	RenderingDeviceGraph::ResourceTracker tracker;
	CHECK(graph.is_draw_list_usage_compatible(nullptr, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ));
	CHECK(graph.is_draw_list_usage_compatible(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ));
	CHECK(tracker.command_frame == -1);
	CHECK(tracker.draw_list_index == -1);
	graph.add_draw_list_usage(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_STORAGE_BUFFER_READ);
	const auto frame = tracker.command_frame;
	const auto list = tracker.draw_list_index;
	CHECK_FALSE(graph.is_draw_list_usage_compatible(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ));
	CHECK(tracker.command_frame == frame);
	CHECK(tracker.draw_list_index == list);
	CHECK(tracker.draw_list_usage == RenderingDeviceGraph::RESOURCE_USAGE_STORAGE_BUFFER_READ);
}

TEST_CASE("[Rendering][IndirectDraw][IndirectCount] Repeated command and counter reads are compatible") {
	RenderingDeviceGraph graph;
	RenderingDeviceGraph::ResourceTracker tracker;
	graph.add_draw_list_usage(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ);
	graph.add_draw_list_usage(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ);
	CHECK(graph.is_draw_list_usage_compatible(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ));
	for (auto usage : { RenderingDeviceGraph::RESOURCE_USAGE_STORAGE_BUFFER_READ, RenderingDeviceGraph::RESOURCE_USAGE_STORAGE_BUFFER_READ_WRITE, RenderingDeviceGraph::RESOURCE_USAGE_VERTEX_BUFFER_READ, RenderingDeviceGraph::RESOURCE_USAGE_INDEX_BUFFER_READ }) {
		CHECK_FALSE(graph.is_draw_list_usage_compatible(&tracker, usage));
	}
}

TEST_CASE("[Rendering][IndirectDraw][IndirectCount] Alias preflight ignores retired frames and other draw lists") {
	RenderingDeviceGraph graph;
	RenderingDeviceGraph::ResourceTracker tracker;
	graph.add_draw_list_usage(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_STORAGE_BUFFER_READ);
	tracker.command_frame -= 1;
	CHECK(graph.is_draw_list_usage_compatible(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ));
	tracker.command_frame += 1;
	tracker.draw_list_index += 1;
	CHECK(graph.is_draw_list_usage_compatible(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ));
}

TEST_CASE("[Rendering][IndirectDraw][IndirectCount] Indirect aliases remain incompatible across subpasses") {
	RenderingDeviceGraph graph;
	RenderingDeviceGraph::ResourceTracker tracker;
	graph.add_draw_list_usage(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ);
	graph.add_draw_list_next_subpass(RenderingDeviceDriver::COMMAND_BUFFER_TYPE_PRIMARY);
	CHECK_FALSE(graph.is_draw_list_usage_compatible(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_STORAGE_BUFFER_READ));
	CHECK_FALSE(graph.is_draw_list_usage_compatible(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_VERTEX_BUFFER_READ));
	CHECK(graph.is_draw_list_usage_compatible(&tracker, RenderingDeviceGraph::RESOURCE_USAGE_INDIRECT_BUFFER_READ));
}

} // namespace TestIndirectCountGraph
