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

static void initialize_compiler(ShaderCompiler &r_compiler, ShaderCompiler::IdentifierActions &r_actions, HashMap<StringName, ShaderLanguage::ShaderNode::Uniform> &r_uniforms, bool p_allow_buffers = true) {
	ShaderCompiler::DefaultIdentifierActions defaults;
	defaults.base_texture_binding_index = 1;
	defaults.texture_layout_set = 3;
	defaults.base_uniform_string = "material.";
	r_compiler.initialize(defaults);
	r_actions.uniforms = &r_uniforms;
	r_actions.allow_readonly_buffers = p_allow_buffers;
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

} // namespace TestShaderLanguage
