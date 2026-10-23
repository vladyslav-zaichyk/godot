/**************************************************************************/
/*  test_shader_language.cpp                                              */
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

TEST_FORCE_LINK(test_shader_language)

#include "servers/rendering/shader_compiler.h"
#include "servers/rendering/shader_types.h"

namespace TestShaderLanguage {

static ShaderLanguage::ShaderCompileInfo spatial_compile_info() {
	ShaderLanguage::ShaderCompileInfo info;
	info.functions = ShaderTypes::get_singleton()->get_functions(RSE::SHADER_SPATIAL);
	info.render_modes = ShaderTypes::get_singleton()->get_modes(RSE::SHADER_SPATIAL);
	info.stencil_modes = ShaderTypes::get_singleton()->get_stencil_modes(RSE::SHADER_SPATIAL);
	info.shader_types = ShaderTypes::get_singleton()->get_types();
	return info;
}

static void initialize_compiler(ShaderCompiler &r_compiler, ShaderCompiler::IdentifierActions &r_actions, HashMap<StringName, ShaderLanguage::ShaderNode::Uniform> &r_uniforms, bool p_allow_buffers = true, bool p_allow_shader_driven_instances = false, bool p_allow_shader_motion_vectors = false) {
	ShaderCompiler::DefaultIdentifierActions defaults;
	defaults.base_texture_binding_index = 1;
	defaults.texture_layout_set = 3;
	defaults.base_uniform_string = "material.";
	defaults.renames["INSTANCE_ID"] = "INSTANCE_INDEX";
	defaults.renames["IS_PREVIOUS_FRAME"] = "godot_is_previous_frame";
	r_compiler.initialize(defaults);
	r_actions.uniforms = &r_uniforms;
	r_actions.allow_readonly_buffers = p_allow_buffers;
	r_actions.allow_shader_driven_instances = p_allow_shader_driven_instances;
	r_actions.allow_shader_driven_motion_vectors = p_allow_shader_motion_vectors;
	r_actions.entry_point_stages["vertex"] = ShaderCompiler::STAGE_VERTEX;
	r_actions.entry_point_stages["fragment"] = ShaderCompiler::STAGE_FRAGMENT;
}

TEST_CASE("[Rendering][ShaderLanguage][BufferRD] Readonly buffer uniform metadata") {
	ShaderLanguage language;
	const String code = "shader_type spatial;\n"
			"uniform readonly_buffer agents;\n"
			"uniform float scale = 1.0;\n"
			"void vertex() { VERTEX.x += float(buffer_load(agents, 0u).x + buffer_length(agents)); }\n";
	REQUIRE(language.compile(code, spatial_compile_info()) == OK);
	const ShaderLanguage::ShaderNode::Uniform &buffer = language.get_shader()->uniforms["agents"];
	CHECK(buffer.is_buffer());
	CHECK_FALSE(buffer.is_texture());
	CHECK_FALSE(ShaderLanguage::is_sampler_type(buffer.type));
	CHECK(buffer.order == -1);
	CHECK(language.get_shader()->uniforms["scale"].order == 0);
	const PropertyInfo property = ShaderLanguage::uniform_to_property_info(buffer);
	CHECK(property.type == Variant::OBJECT);
	CHECK(property.hint == PROPERTY_HINT_RESOURCE_TYPE);
	CHECK(property.hint_string == "BufferRD");
	CHECK(ShaderLanguage::get_datatype_size(buffer.type) == 0);
}

TEST_CASE("[Rendering][ShaderLanguage][BufferRD] Reject unsupported buffer declarations and expressions") {
	const char *invalid_bodies[] = {
		"global uniform readonly_buffer agents;",
		"instance uniform readonly_buffer agents;",
		"uniform readonly_buffer agents[2];",
		"uniform readonly_buffer[2] agents;",
		"uniform readonly_buffer agents = 0;",
		"uniform readonly_buffer agents : source_color;",
		"uniform highp readonly_buffer agents;",
		"varying readonly_buffer agents;",
		"struct Agent { readonly_buffer data; };",
		"readonly_buffer agents;",
		"const readonly_buffer agents = 0;",
		"readonly_buffer get_data() { return 0; }",
		"void read_data(readonly_buffer data) {}",
		"void read_data(out readonly_buffer data) {}",
		"void vertex() { readonly_buffer data; }",
		"uniform readonly_buffer agents; void vertex() { agents; }",
		"uniform readonly_buffer agents; void vertex() { agents = agents; }",
		"uniform readonly_buffer agents; void vertex() { bool same = agents == agents; }",
		"uniform readonly_buffer agents; void vertex() { uvec4 value = buffer_load(agents[0], 0u); }",
		"uniform readonly_buffer agents; void vertex() { uvec4 value = buffer_load(agents.data, 0u); }",
		"uniform readonly_buffer agents; void vertex() { uvec4 value = buffer_load(true ? agents : agents, 0u); }",
		"uniform readonly_buffer agents; void vertex() { uvec4 value = buffer_load(readonly_buffer(), 0u); }",
		"uniform readonly_buffer agents; void vertex() { uvec4 value = buffer_load(agents); }",
		"uniform readonly_buffer agents; void vertex() { uvec4 value = buffer_load(agents, 1.0); }",
		"uniform readonly_buffer agents; void vertex() { uvec4 value = buffer_load(agents, -1); }",
		"uniform readonly_buffer agents; void vertex() { uint count = buffer_length(); }",
		"uniform readonly_buffer agents; void vertex() { uint count = buffer_length(agents, 0u); }",
		"uniform sampler2D image; void vertex() { uint count = buffer_length(image); }",
	};
	const ShaderLanguage::ShaderCompileInfo info = spatial_compile_info();
	for (const char *body : invalid_bodies) {
		INFO(body);
		ShaderLanguage language;
		CHECK(language.compile(String("shader_type spatial;\n") + body, info) == ERR_PARSE_ERROR);
	}
	ShaderLanguage language;
	CHECK(language.compile("shader_type canvas_item; uniform readonly_buffer agents;", info) == ERR_PARSE_ERROR);
}

TEST_CASE("[Rendering][ShaderLanguage][BufferRD] SSBO bindings and bounds-checked load generation") {
	ShaderCompiler compiler;
	ShaderCompiler::IdentifierActions actions;
	HashMap<StringName, ShaderLanguage::ShaderNode::Uniform> uniforms;
	initialize_compiler(compiler, actions, uniforms);
	ShaderCompiler::GeneratedCode generated;
	const String code = "shader_type spatial;\n"
			"uniform sampler2D images[3];\n"
			"uniform readonly_buffer zebra;\n"
			"uniform float scale;\n"
			"uniform sampler2D image;\n"
			"uniform readonly_buffer agents;\n"
			"void vertex() { uint cursor = 0u; VERTEX.x += float(buffer_load(agents, cursor++).x + buffer_length(zebra)); }\n";
	REQUIRE(compiler.compile(RSE::SHADER_SPATIAL, code, &actions, "", generated) == OK);
	REQUIRE(generated.buffers.size() == 2);
	CHECK(generated.buffers[0].name == StringName("agents"));
	CHECK(generated.buffers[0].binding == 5);
	CHECK(generated.buffers[1].name == StringName("zebra"));
	CHECK(generated.buffers[1].binding == 6);
	REQUIRE(generated.texture_uniforms.size() == 2);
	CHECK(generated.texture_uniforms[0].array_size == 3);
	CHECK(generated.uniform_offsets.size() == 1);
	CHECK(generated.uniform_total_size == 16);
	CHECK_FALSE(generated.uniforms.contains("agents"));
	CHECK_FALSE(generated.uniforms.contains("zebra"));
	const String &vertex_globals = generated.stage_globals[ShaderCompiler::STAGE_VERTEX];
	CHECK(vertex_globals.contains("layout(set = 3, binding = 5, std430) readonly buffer"));
	CHECK(vertex_globals.contains("uvec4 data[]; } m_agents;"));
	CHECK(vertex_globals.contains("if (record_index >= uint(m_agents.data.length())) { return uvec4(0u); }"));
	CHECK(vertex_globals.contains("return m_agents.data[record_index];"));
	CHECK(generated.stage_globals[ShaderCompiler::STAGE_FRAGMENT].contains("readonly buffer"));
	REQUIRE(generated.code.has("vertex"));
	CHECK(generated.code["vertex"].count("m_cursor++") == 1);
	CHECK(generated.code["vertex"].contains("godot_buffer_load_m_agents("));
	CHECK(generated.code["vertex"].contains("uint(m_zebra.data.length())"));
}

TEST_CASE("[Rendering][ShaderLanguage][BufferRD] Buffer-only material and backend opt-in") {
	const String code = "shader_type spatial; uniform readonly_buffer agents; void vertex() { VERTEX.x += float(buffer_load(agents, 0u).x); }";
	ShaderCompiler compiler;
	ShaderCompiler::IdentifierActions actions;
	HashMap<StringName, ShaderLanguage::ShaderNode::Uniform> uniforms;
	initialize_compiler(compiler, actions, uniforms, false);
	ShaderCompiler::GeneratedCode rejected;
	ERR_PRINT_OFF;
	const Error unavailable = compiler.compile(RSE::SHADER_SPATIAL, code, &actions, "", rejected);
	ERR_PRINT_ON;
	CHECK(unavailable == ERR_UNAVAILABLE);
	CHECK(rejected.buffers.is_empty());
	ShaderCompiler::GeneratedCode ordinary;
	REQUIRE(compiler.compile(RSE::SHADER_SPATIAL, "shader_type spatial; uniform float scale; void vertex() { VERTEX *= scale; }", &actions, "", ordinary) == OK);
	CHECK(ordinary.buffers.is_empty());
	actions.allow_readonly_buffers = true;
	ShaderCompiler::GeneratedCode accepted;
	REQUIRE(compiler.compile(RSE::SHADER_SPATIAL, code, &actions, "", accepted) == OK);
	REQUIRE(accepted.buffers.size() == 1);
	CHECK(accepted.buffers[0].binding == 1);
	CHECK(accepted.uniform_offsets.is_empty());
	CHECK(accepted.uniform_total_size == 0);
	CHECK(accepted.uniforms.is_empty());
	CHECK(uniforms["agents"].is_buffer());
	CHECK_FALSE(uniforms["agents"].is_texture());
}

TEST_CASE("[Rendering][ShaderLanguage][ShaderDriven] Spatial capability requires explicit backend opt-in") {
	const String code = "shader_type spatial; render_mode shader_driven_instances; void vertex() { VERTEX *= 2.0; }";
	ShaderLanguage language;
	REQUIRE(language.compile(code, spatial_compile_info()) == OK);

	ShaderCompiler compiler;
	ShaderCompiler::IdentifierActions actions;
	CHECK_FALSE(actions.allow_shader_driven_instances);
	HashMap<StringName, ShaderLanguage::ShaderNode::Uniform> uniforms;
	initialize_compiler(compiler, actions, uniforms);
	bool capable = false;
	actions.render_mode_flags["shader_driven_instances"] = &capable;
	ShaderCompiler::GeneratedCode rejected;
	ERR_PRINT_OFF;
	const Error unavailable = compiler.compile(RSE::SHADER_SPATIAL, code, &actions, "", rejected);
	ERR_PRINT_ON;
	CHECK(unavailable == ERR_UNAVAILABLE);
	CHECK_FALSE(capable);
	CHECK(rejected.code.is_empty());
	CHECK(rejected.buffers.is_empty());

	actions.allow_shader_driven_instances = true;
	ShaderCompiler::GeneratedCode accepted;
	REQUIRE(compiler.compile(RSE::SHADER_SPATIAL, code, &actions, "", accepted) == OK);
	CHECK(capable);
	CHECK(accepted.code.has("vertex"));
}

TEST_CASE("[Rendering][ShaderLanguage][ShaderDriven] Packed BufferRD IDs preserve draw-instance addressing") {
	ShaderCompiler compiler;
	ShaderCompiler::IdentifierActions actions;
	HashMap<StringName, ShaderLanguage::ShaderNode::Uniform> uniforms;
	initialize_compiler(compiler, actions, uniforms, true, true);
	bool capable = false;
	actions.render_mode_flags["shader_driven_instances"] = &capable;
	const String code = "shader_type spatial;\n"
			"render_mode shader_driven_instances, skip_vertex_transform;\n"
			"uniform readonly_buffer visible_ids;\n"
			"uniform readonly_buffer poses;\n"
			"void vertex() {\n"
			"uint draw_slot = uint(INSTANCE_ID);\n"
			"uvec4 packed_ids = buffer_load(visible_ids, draw_slot >> 2u);\n"
			"uint stable_slot = packed_ids[int(draw_slot & 3u)];\n"
			"VERTEX.x = uintBitsToFloat(buffer_load(poses, stable_slot).x);\n"
			"}\n";
	ShaderCompiler::GeneratedCode generated;
	REQUIRE(compiler.compile(RSE::SHADER_SPATIAL, code, &actions, "", generated) == OK);
	CHECK(capable);
	REQUIRE(generated.buffers.size() == 2);
	CHECK(generated.buffers[0].name == StringName("poses"));
	CHECK(generated.buffers[1].name == StringName("visible_ids"));
	CHECK(uniforms["poses"].is_buffer());
	CHECK(uniforms["visible_ids"].is_buffer());
	CHECK(generated.uniform_offsets.is_empty());
	REQUIRE(generated.code.has("vertex"));
	const String &vertex = generated.code["vertex"];
	CHECK(vertex.count("INSTANCE_INDEX") == 1);
	CHECK(vertex.contains("godot_buffer_load_m_visible_ids("));
	CHECK(vertex.contains("m_draw_slot >> 2u"));
	CHECK(vertex.contains("m_draw_slot & 3u"));
	CHECK(vertex.contains("m_packed_ids["));
	CHECK(vertex.contains("godot_buffer_load_m_poses(m_stable_slot)"));
	CHECK(generated.stage_globals[ShaderCompiler::STAGE_VERTEX].contains("return uvec4(0u);"));
}

TEST_CASE("[Rendering][ShaderLanguage][ShaderDriven] Canvas and particle shaders reject the spatial mode") {
	const struct {
		RSE::ShaderMode mode;
		const char *code;
	} cases[] = {
		{ RSE::SHADER_CANVAS_ITEM, "shader_type canvas_item; render_mode shader_driven_instances;" },
		{ RSE::SHADER_PARTICLES, "shader_type particles; render_mode shader_driven_instances;" },
	};
	for (const auto &test : cases) {
		INFO(test.code);
		ShaderLanguage::ShaderCompileInfo info;
		info.functions = ShaderTypes::get_singleton()->get_functions(test.mode);
		info.render_modes = ShaderTypes::get_singleton()->get_modes(test.mode);
		info.stencil_modes = ShaderTypes::get_singleton()->get_stencil_modes(test.mode);
		info.shader_types = ShaderTypes::get_singleton()->get_types();
		ShaderLanguage language;
		CHECK(language.compile(test.code, info) == ERR_PARSE_ERROR);
	}
}

TEST_CASE("[Rendering][ShaderLanguage][ShaderDriven] Backend permission does not leak between compiler clients") {
	const String code = "shader_type spatial; render_mode shader_driven_instances; void vertex() { VERTEX *= 2.0; }";
	ShaderCompiler compiler;
	ShaderCompiler::IdentifierActions allowed;
	HashMap<StringName, ShaderLanguage::ShaderNode::Uniform> allowed_uniforms;
	initialize_compiler(compiler, allowed, allowed_uniforms, false, true);
	bool allowed_capability = false;
	allowed.render_mode_flags["shader_driven_instances"] = &allowed_capability;
	ShaderCompiler::GeneratedCode accepted;
	REQUIRE(compiler.compile(RSE::SHADER_SPATIAL, code, &allowed, "", accepted) == OK);
	CHECK(allowed_capability);

	ShaderCompiler::IdentifierActions ordinary_backend;
	HashMap<StringName, ShaderLanguage::ShaderNode::Uniform> ordinary_uniforms;
	initialize_compiler(compiler, ordinary_backend, ordinary_uniforms, false);
	bool ordinary_capability = false;
	ordinary_backend.render_mode_flags["shader_driven_instances"] = &ordinary_capability;
	ShaderCompiler::GeneratedCode rejected;
	ERR_PRINT_OFF;
	const Error unavailable = compiler.compile(RSE::SHADER_SPATIAL, code, &ordinary_backend, "", rejected);
	ERR_PRINT_ON;
	CHECK(unavailable == ERR_UNAVAILABLE);
	CHECK_FALSE(ordinary_capability);
	CHECK(rejected.code.is_empty());
}

TEST_CASE("[Rendering][ShaderLanguage][ShaderDriven] Ordinary vertex and billboard-style transforms do not opt in") {
	const char *codes[] = {
		"shader_type spatial; void vertex() { VERTEX *= 2.0; }",
		"shader_type spatial; render_mode skip_vertex_transform; void vertex() { VERTEX = (VIEW_MATRIX * MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }",
		"shader_type spatial; void vertex() { MODELVIEW_MATRIX = VIEW_MATRIX * MODEL_MATRIX; MODELVIEW_NORMAL_MATRIX = mat3(MODELVIEW_MATRIX); }",
	};
	ShaderCompiler compiler;
	for (const char *code : codes) {
		INFO(code);
		ShaderCompiler::IdentifierActions actions;
		HashMap<StringName, ShaderLanguage::ShaderNode::Uniform> uniforms;
		initialize_compiler(compiler, actions, uniforms, false);
		bool capable = false;
		actions.render_mode_flags["shader_driven_instances"] = &capable;
		ShaderCompiler::GeneratedCode generated;
		REQUIRE(compiler.compile(RSE::SHADER_SPATIAL, code, &actions, "", generated) == OK);
		CHECK_FALSE(capable);
		CHECK(generated.buffers.is_empty());
	}
}

TEST_CASE("[Rendering][ShaderLanguage][GPUHistory] Previous context is readonly and restricted to vertex evaluation") {
	const char *invalid_bodies[] = {
		"void vertex() { IS_PREVIOUS_FRAME = true; }",
		"void write_context(out bool context) { context = true; } void vertex() { write_context(IS_PREVIOUS_FRAME); }",
		"void fragment() { ALBEDO = vec3(IS_PREVIOUS_FRAME ? 1.0 : 0.0); }",
		"void light() { DIFFUSE_LIGHT = vec3(IS_PREVIOUS_FRAME ? 1.0 : 0.0); }",
		"bool previous() { return IS_PREVIOUS_FRAME; } void vertex() { VERTEX.x += previous() ? 1.0 : 0.0; } void fragment() { ALBEDO = vec3(previous() ? 1.0 : 0.0); }",
		"bool previous() { return IS_PREVIOUS_FRAME; } bool nested() { return previous(); } void fragment() { ALBEDO = vec3(nested() ? 1.0 : 0.0); }",
		"bool previous() { return IS_PREVIOUS_FRAME; } bool nested() { return previous(); } void light() { DIFFUSE_LIGHT = vec3(nested() ? 1.0 : 0.0); }",
		"void write_context(out bool context) { context = true; } bool previous() { write_context(IS_PREVIOUS_FRAME); return false; } void vertex() { VERTEX.x += previous() ? 1.0 : 0.0; }",
		"void write_context(inout bool context) { context = !context; } bool previous() { write_context(IS_PREVIOUS_FRAME); return false; } void vertex() { VERTEX.x += previous() ? 1.0 : 0.0; }",
	};
	for (const char *body : invalid_bodies) {
		INFO(body);
		ShaderLanguage language;
		CHECK(language.compile(String("shader_type spatial; render_mode shader_driven_instances, shader_driven_motion_vectors;\n") + body, spatial_compile_info()) == ERR_PARSE_ERROR);
	}
	ShaderLanguage language;
	CHECK(language.compile("shader_type spatial; render_mode shader_driven_instances, shader_driven_motion_vectors; bool previous() { return IS_PREVIOUS_FRAME; } void vertex() { VERTEX.x += previous() ? 1.0 : 0.0; }", spatial_compile_info()) == OK);
	CHECK(language.compile("shader_type spatial; render_mode shader_driven_instances, shader_driven_motion_vectors; bool previous() { return IS_PREVIOUS_FRAME; } bool nested() { return previous(); } void vertex() { VERTEX.x += nested() ? 1.0 : 0.0; }", spatial_compile_info()) == OK);
	CHECK(language.compile("shader_type spatial; render_mode shader_driven_instances, shader_driven_motion_vectors; bool previous() { return IS_PREVIOUS_FRAME; } float previous(float value) { return value; } void vertex() { VERTEX.x += previous() ? 1.0 : 0.0; } void fragment() { ALBEDO = vec3(previous(1.0)); }", spatial_compile_info()) == OK);
}

TEST_CASE("[Rendering][ShaderLanguage][GPUHistory] Shader history requires independent backend permission") {
	const String code = "shader_type spatial; render_mode shader_driven_instances, shader_driven_motion_vectors; void vertex() { VERTEX.x += IS_PREVIOUS_FRAME ? 1.0 : 0.0; }";
	ShaderCompiler compiler;
	ShaderCompiler::IdentifierActions actions;
	HashMap<StringName, ShaderLanguage::ShaderNode::Uniform> uniforms;
	initialize_compiler(compiler, actions, uniforms, true, true);
	CHECK_FALSE(actions.allow_shader_driven_motion_vectors);
	bool history_capable = false;
	actions.render_mode_flags["shader_driven_motion_vectors"] = &history_capable;
	ShaderCompiler::GeneratedCode generated;
	ERR_PRINT_OFF;
	const Error unavailable = compiler.compile(RSE::SHADER_SPATIAL, code, &actions, "", generated);
	ERR_PRINT_ON;
	CHECK(unavailable == ERR_UNAVAILABLE);
	CHECK_FALSE(history_capable);
	CHECK(generated.code.is_empty());
	actions.allow_shader_driven_motion_vectors = true;
	REQUIRE(compiler.compile(RSE::SHADER_SPATIAL, code, &actions, "", generated) == OK);
	CHECK(history_capable);
	CHECK(generated.uses_previous_frame);
	CHECK_FALSE(generated.uses_vertex_time);
	CHECK_FALSE(generated.uses_fragment_time);
}

TEST_CASE("[Rendering][ShaderLanguage][GPUHistory] Previous context requires both explicit spatial modes") {
	const char *codes[] = {
		"shader_type spatial; void vertex() { VERTEX.x += IS_PREVIOUS_FRAME ? 1.0 : 0.0; }",
		"shader_type spatial; render_mode shader_driven_instances; void vertex() { VERTEX.x += IS_PREVIOUS_FRAME ? 1.0 : 0.0; }",
		"shader_type spatial; bool previous() { return IS_PREVIOUS_FRAME; } void vertex() { VERTEX.x += previous() ? 1.0 : 0.0; }",
		"shader_type spatial; render_mode shader_driven_motion_vectors; void vertex() { VERTEX.x += IS_PREVIOUS_FRAME ? 1.0 : 0.0; }",
		"shader_type spatial; render_mode shader_driven_motion_vectors; void vertex() { VERTEX *= 2.0; }",
	};
	for (const char *code : codes) {
		INFO(code);
		ShaderCompiler compiler;
		ShaderCompiler::IdentifierActions actions;
		HashMap<StringName, ShaderLanguage::ShaderNode::Uniform> uniforms;
		initialize_compiler(compiler, actions, uniforms, true, true, true);
		ShaderCompiler::GeneratedCode generated;
		ERR_PRINT_OFF;
		const Error unavailable = compiler.compile(RSE::SHADER_SPATIAL, code, &actions, "", generated);
		ERR_PRINT_ON;
		CHECK(unavailable == ERR_UNAVAILABLE);
	}
}

TEST_CASE("[Rendering][ShaderLanguage][GPUHistory] Helpers select history without remapping the current draw identity") {
	ShaderCompiler compiler;
	ShaderCompiler::IdentifierActions actions;
	HashMap<StringName, ShaderLanguage::ShaderNode::Uniform> uniforms;
	initialize_compiler(compiler, actions, uniforms, true, true, true);
	const String code = "shader_type spatial;\n"
			"render_mode shader_driven_instances, shader_driven_motion_vectors, skip_vertex_transform;\n"
			"uniform readonly_buffer visible_ids;\n"
			"uniform readonly_buffer current_poses;\n"
			"uniform readonly_buffer previous_poses;\n"
			"vec3 pose(uint stable_id) { return uintBitsToFloat(IS_PREVIOUS_FRAME ? buffer_load(previous_poses, stable_id) : buffer_load(current_poses, stable_id)).xyz; }\n"
			"void vertex() { uint slot = uint(INSTANCE_ID); uvec4 ids = buffer_load(visible_ids, slot >> 2u); uint stable_id = ids[int(slot & 3u)]; VERTEX = (MODELVIEW_MATRIX * vec4(pose(stable_id), 1.0)).xyz; }\n";
	ShaderCompiler::GeneratedCode generated;
	REQUIRE(compiler.compile(RSE::SHADER_SPATIAL, code, &actions, "", generated) == OK);
	CHECK(generated.uses_previous_frame);
	REQUIRE(generated.buffers.size() == 3);
	CHECK(generated.uniform_offsets.is_empty());
	CHECK(generated.stage_globals[ShaderCompiler::STAGE_VERTEX].contains("godot_is_previous_frame"));
	CHECK(generated.stage_globals[ShaderCompiler::STAGE_VERTEX].contains("godot_buffer_load_m_previous_poses"));
	CHECK(generated.stage_globals[ShaderCompiler::STAGE_VERTEX].contains("godot_buffer_load_m_current_poses"));
	CHECK_FALSE(generated.stage_globals[ShaderCompiler::STAGE_FRAGMENT].contains("godot_is_previous_frame"));
	REQUIRE(generated.code.has("vertex"));
	CHECK(generated.code["vertex"].count("INSTANCE_INDEX") == 1);
	CHECK(generated.code["vertex"].count("godot_buffer_load_m_visible_ids") == 1);
	CHECK_FALSE(generated.uses_vertex_time);
}

TEST_CASE("[Rendering][ShaderLanguage][GPUHistory] Analytic capability does not require reading the context") {
	ShaderCompiler compiler;
	ShaderCompiler::IdentifierActions actions;
	HashMap<StringName, ShaderLanguage::ShaderNode::Uniform> uniforms;
	initialize_compiler(compiler, actions, uniforms, false, true, true);
	bool history_capable = false;
	actions.render_mode_flags["shader_driven_motion_vectors"] = &history_capable;
	ShaderCompiler::GeneratedCode generated;
	REQUIRE(compiler.compile(RSE::SHADER_SPATIAL, "shader_type spatial; render_mode shader_driven_instances, shader_driven_motion_vectors; void vertex() { VERTEX *= 2.0; }", &actions, "", generated) == OK);
	CHECK(history_capable);
	CHECK_FALSE(generated.uses_previous_frame);
	CHECK(generated.buffers.is_empty());
}

TEST_CASE("[Rendering][ShaderLanguage][GPUHistory] Permission and context metadata do not leak between compiler clients") {
	const String code = "shader_type spatial; render_mode shader_driven_instances, shader_driven_motion_vectors; void vertex() { VERTEX.x += IS_PREVIOUS_FRAME ? 1.0 : 0.0; }";
	ShaderCompiler compiler;
	ShaderCompiler::IdentifierActions allowed;
	HashMap<StringName, ShaderLanguage::ShaderNode::Uniform> allowed_uniforms;
	initialize_compiler(compiler, allowed, allowed_uniforms, true, true, true);
	ShaderCompiler::GeneratedCode generated;
	REQUIRE(compiler.compile(RSE::SHADER_SPATIAL, code, &allowed, "", generated) == OK);
	CHECK(generated.uses_previous_frame);
	ShaderCompiler::IdentifierActions ordinary;
	HashMap<StringName, ShaderLanguage::ShaderNode::Uniform> ordinary_uniforms;
	initialize_compiler(compiler, ordinary, ordinary_uniforms, false);
	REQUIRE(compiler.compile(RSE::SHADER_SPATIAL, "shader_type spatial; void vertex() { VERTEX *= 2.0; }", &ordinary, "", generated) == OK);
	CHECK_FALSE(generated.uses_previous_frame);
	CHECK(generated.buffers.is_empty());
	ERR_PRINT_OFF;
	const Error unavailable = compiler.compile(RSE::SHADER_SPATIAL, code, &ordinary, "", generated);
	ERR_PRINT_ON;
	CHECK(unavailable == ERR_UNAVAILABLE);
	CHECK_FALSE(generated.uses_previous_frame);
}

TEST_CASE("[Rendering][ShaderLanguage][GPUHistory] Canvas and particles cannot opt into spatial history") {
	const struct {
		RSE::ShaderMode mode;
		const char *code;
	} cases[] = {
		{ RSE::SHADER_CANVAS_ITEM, "shader_type canvas_item; render_mode shader_driven_motion_vectors;" },
		{ RSE::SHADER_CANVAS_ITEM, "shader_type canvas_item; void vertex() { VERTEX.x += IS_PREVIOUS_FRAME ? 1.0 : 0.0; }" },
		{ RSE::SHADER_PARTICLES, "shader_type particles; render_mode shader_driven_motion_vectors;" },
		{ RSE::SHADER_PARTICLES, "shader_type particles; void process() { ACTIVE = IS_PREVIOUS_FRAME; }" },
	};
	for (const auto &test : cases) {
		INFO(test.code);
		ShaderLanguage::ShaderCompileInfo info;
		info.functions = ShaderTypes::get_singleton()->get_functions(test.mode);
		info.render_modes = ShaderTypes::get_singleton()->get_modes(test.mode);
		info.stencil_modes = ShaderTypes::get_singleton()->get_stencil_modes(test.mode);
		info.shader_types = ShaderTypes::get_singleton()->get_types();
		ShaderLanguage language;
		CHECK(language.compile(test.code, info) == ERR_PARSE_ERROR);
	}
}

} // namespace TestShaderLanguage
