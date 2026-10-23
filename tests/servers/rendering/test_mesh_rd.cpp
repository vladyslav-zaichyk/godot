/**************************************************************************/
/*  test_mesh_rd.cpp                                                      */
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

TEST_FORCE_LINK(test_mesh_rd)

#include "scene/resources/mesh.h"
#include "servers/rendering/rendering_server.h"

namespace TestMeshRD {

static Array triangle_arrays(int p_bone_count = 4) {
	Array arrays;
	arrays.resize(RSE::ARRAY_MAX);
	arrays[RSE::ARRAY_VERTEX] = PackedVector3Array{ Vector3(-1, -1, 0), Vector3(1, -1, 0), Vector3(0, 1, 0) };
	arrays[RSE::ARRAY_NORMAL] = PackedVector3Array{ Vector3(0, 0, 1), Vector3(0, 0, 1), Vector3(0, 0, 1) };
	arrays[RSE::ARRAY_COLOR] = PackedColorArray{ Color(1, 0, 0, 1), Color(0, 1, 0, 1), Color(0, 0, 1, 1) };
	arrays[RSE::ARRAY_TEX_UV] = PackedVector2Array{ Vector2(0, 0), Vector2(1, 0), Vector2(0.5, 1) };
	PackedInt32Array bones;
	PackedFloat32Array weights;
	bones.resize(3 * p_bone_count);
	weights.resize(3 * p_bone_count);
	for (int vertex = 0; vertex < 3; vertex++) {
		for (int influence = 0; influence < p_bone_count; influence++) {
			bones.set(vertex * p_bone_count + influence, influence);
			weights.set(vertex * p_bone_count + influence, influence == 0 ? 1.0f : 0.0f);
		}
	}
	arrays[RSE::ARRAY_BONES] = bones;
	arrays[RSE::ARRAY_WEIGHTS] = weights;
	arrays[RSE::ARRAY_INDEX] = PackedInt32Array{ 0, 1, 2 };
	return arrays;
}

TEST_CASE("[SceneTree][Rendering][MeshRD] Storage opt-in preserves packed data and queried layout") {
	RenderingServer *server = RenderingServer::get_singleton();
	REQUIRE(server != nullptr);
	CHECK(uint64_t(Mesh::ARRAY_FLAG_USE_STORAGE_BUFFER) == uint64_t(RSE::ARRAY_FLAG_USE_STORAGE_BUFFER));
	const uint64_t storage = RSE::ARRAY_FLAG_USE_STORAGE_BUFFER;
	CHECK((storage & ((1ULL << RSE::ARRAY_MAX) - 1)) == 0);
	CHECK((storage & (RSE::ARRAY_FLAG_FORMAT_VERSION_MASK << RSE::ARRAY_FLAG_FORMAT_VERSION_SHIFT)) == 0);

	for (int variant = 0; variant < 3; variant++) {
		const uint64_t flags = variant == 1 ? uint64_t(RSE::ARRAY_FLAG_COMPRESS_ATTRIBUTES) : 0;
		const Array arrays = triangle_arrays(variant == 2 ? 8 : 4);
		RenderingServerTypes::SurfaceData ordinary;
		RenderingServerTypes::SurfaceData storage_enabled;
		REQUIRE(server->mesh_create_surface_data_from_arrays(&ordinary, RSE::PRIMITIVE_TRIANGLES, arrays, Array(), Dictionary(), flags) == OK);
		REQUIRE(server->mesh_create_surface_data_from_arrays(&storage_enabled, RSE::PRIMITIVE_TRIANGLES, arrays, Array(), Dictionary(), flags | storage) == OK);
		CHECK((storage_enabled.format ^ ordinary.format) == storage);
		CHECK(storage_enabled.vertex_data == ordinary.vertex_data);
		CHECK(storage_enabled.attribute_data == ordinary.attribute_data);
		CHECK(storage_enabled.skin_data == ordinary.skin_data);
		CHECK(storage_enabled.index_data == ordinary.index_data);
		CHECK(storage_enabled.aabb == ordinary.aabb);
		CHECK(storage_enabled.vertex_count == 3);
		CHECK(storage_enabled.index_count == 3);
		uint32_t base_offsets[RSE::ARRAY_MAX];
		uint32_t storage_offsets[RSE::ARRAY_MAX];
		uint32_t bv, bn, ba, bs, sv, sn, sa, ss;
		server->mesh_surface_make_offsets_from_format(ordinary.format, 3, 3, base_offsets, bv, bn, ba, bs);
		server->mesh_surface_make_offsets_from_format(storage_enabled.format, 3, 3, storage_offsets, sv, sn, sa, ss);
		for (int i = 0; i < RSE::ARRAY_MAX; i++) {
			CHECK(storage_offsets[i] == base_offsets[i]);
		}
		CHECK(sv == bv);
		CHECK(sn == bn);
		CHECK(sa == ba);
		CHECK(ss == bs);
		CHECK(storage_enabled.vertex_data.size() == int((sv + sn) * 3));
		CHECK(storage_enabled.attribute_data.size() == int(sa * 3));
		CHECK(storage_enabled.skin_data.size() == int(ss * 3));
		const Array restored = server->mesh_create_arrays_from_surface_data(storage_enabled);
		CHECK(PackedColorArray(restored[RSE::ARRAY_COLOR]) == PackedColorArray(arrays[RSE::ARRAY_COLOR]));
		CHECK(PackedInt32Array(restored[RSE::ARRAY_BONES]) == PackedInt32Array(arrays[RSE::ARRAY_BONES]));
		CHECK(PackedFloat32Array(restored[RSE::ARRAY_WEIGHTS]) == PackedFloat32Array(arrays[RSE::ARRAY_WEIGHTS]));
		CHECK(PackedInt32Array(restored[RSE::ARRAY_INDEX]) == PackedInt32Array(arrays[RSE::ARRAY_INDEX]));
		const PackedVector3Array positions = restored[RSE::ARRAY_VERTEX];
		const PackedVector3Array expected_positions = arrays[RSE::ARRAY_VERTEX];
		for (int i = 0; i < positions.size(); i++) {
			CHECK(positions[i].distance_to(expected_positions[i]) < 0.001);
		}
	}
}

TEST_CASE("[SceneTree][Rendering][MeshRD] Float3 vertices and packed color remain separate native streams") {
	RenderingServer *server = RenderingServer::get_singleton();
	REQUIRE(server != nullptr);
	Array arrays;
	arrays.resize(RSE::ARRAY_MAX);
	arrays[RSE::ARRAY_VERTEX] = PackedVector3Array{ Vector3(-1, -1, 0), Vector3(1, -1, 0), Vector3(0, 1, 0) };
	arrays[RSE::ARRAY_COLOR] = PackedColorArray{ Color(1, 0, 0, 1), Color(0, 1, 0, 1), Color(0, 0, 1, 1) };
	RenderingServerTypes::SurfaceData surface;
	REQUIRE(server->mesh_create_surface_data_from_arrays(&surface, RSE::PRIMITIVE_TRIANGLES, arrays, Array(), Dictionary(), RSE::ARRAY_FLAG_USE_STORAGE_BUFFER) == OK);
	uint32_t offsets[RSE::ARRAY_MAX];
	uint32_t vertex_stride, normal_stride, attribute_stride, skin_stride;
	server->mesh_surface_make_offsets_from_format(surface.format, 3, 0, offsets, vertex_stride, normal_stride, attribute_stride, skin_stride);
	CHECK(vertex_stride == 3 * sizeof(float));
	CHECK(normal_stride == 0);
	CHECK(attribute_stride == 4);
	CHECK(skin_stride == 0);
	CHECK(offsets[RSE::ARRAY_VERTEX] == 0);
	CHECK(offsets[RSE::ARRAY_COLOR] == 0);
	CHECK(surface.vertex_data.size() == 36);
	CHECK(surface.attribute_data.size() == 12);
	CHECK(surface.skin_data.is_empty());
	CHECK(surface.index_data.is_empty());
	const Array restored = server->mesh_create_arrays_from_surface_data(surface);
	CHECK(PackedVector3Array(restored[RSE::ARRAY_VERTEX]) == PackedVector3Array(arrays[RSE::ARRAY_VERTEX]));
	CHECK(PackedColorArray(restored[RSE::ARRAY_COLOR]) == PackedColorArray(arrays[RSE::ARRAY_COLOR]));
}

TEST_CASE("[SceneTree][Rendering][MeshRD] Storage opt-in preserves native 16 and 32 bit index boundary") {
	RenderingServer *server = RenderingServer::get_singleton();
	REQUIRE(server != nullptr);
	for (int vertex_count : { 65536, 65537 }) {
		Array arrays;
		arrays.resize(RSE::ARRAY_MAX);
		PackedVector3Array vertices;
		vertices.resize(vertex_count);
		vertices.set(0, Vector3(-1, -1, 0));
		vertices.set(1, Vector3(1, -1, 0));
		vertices.set(vertex_count - 1, Vector3(0, 1, 0));
		const PackedInt32Array indices{ 0, 1, vertex_count - 1 };
		arrays[RSE::ARRAY_VERTEX] = vertices;
		arrays[RSE::ARRAY_INDEX] = indices;
		RenderingServerTypes::SurfaceData surface;
		REQUIRE(server->mesh_create_surface_data_from_arrays(&surface, RSE::PRIMITIVE_TRIANGLES, arrays, Array(), Dictionary(), RSE::ARRAY_FLAG_USE_STORAGE_BUFFER) == OK);
		uint32_t offsets[RSE::ARRAY_MAX];
		uint32_t vertex_stride, normal_stride, attribute_stride, skin_stride;
		server->mesh_surface_make_offsets_from_format(surface.format, vertex_count, 3, offsets, vertex_stride, normal_stride, attribute_stride, skin_stride);
		const uint32_t expected_index_stride = vertex_count == 65536 ? 2 : 4;
		CHECK(offsets[RSE::ARRAY_INDEX] == expected_index_stride);
		CHECK(surface.index_data.size() == int(3 * expected_index_stride));
		const Array restored = server->mesh_create_arrays_from_surface_data(surface);
		CHECK(PackedInt32Array(restored[RSE::ARRAY_INDEX]) == indices);
	}
}

} // namespace TestMeshRD
