/**************************************************************************/
/*  test_multicommand.cpp                                                 */
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

TEST_FORCE_LINK(test_multicommand)

#include "core/object/class_db.h"
#include "core/object/method_bind.h"
#include "servers/rendering/rendering_server.h"
#include "servers/rendering/shared_draw_command_span.h"

namespace TestMultiCommand {

using RendererSharedDraw::CommandSpan;
using RendererSharedDraw::CountedCommandSpan;
using RendererSharedDraw::validate_command_span;
using RendererSharedDraw::validate_counted_command_span;
static constexpr auto LOOP = RSE::MULTIMESH_INDIRECT_SUBMISSION_LOOP;
static constexpr auto MULTI_DRAW = RSE::MULTIMESH_INDIRECT_SUBMISSION_MULTI_DRAW;
static constexpr auto MULTI_DRAW_COUNT = RSE::MULTIMESH_INDIRECT_SUBMISSION_MULTI_DRAW_COUNT;

TEST_CASE("[Rendering][MultiCommand] Packed and padded command spans retain exact final bytes") {
	CommandSpan span;
	REQUIRE(validate_command_span(1000000, 16, 1024, 32, MULTI_DRAW, 2048, span) == OK);
	CHECK(span.capacity == 1000000);
	CHECK(span.offset == 16);
	CHECK(span.count == 1024);
	CHECK(span.stride == 32);
	CHECK(span.end == 32772);
	REQUIRE(validate_command_span(8, 4, 64, 20, LOOP, 1, span) == OK);
	CHECK(span.end == 1284);
}

TEST_CASE("[Rendering][MultiCommand] Signed inputs reject negative values before narrowing") {
	for (int64_t value : { int64_t(-1), std::numeric_limits<int64_t>::min() }) {
		CommandSpan span{ 17, 4, 7, 32, 216 };
		CHECK(validate_command_span(value, 0, 1, 20, LOOP, 1, span) == ERR_INVALID_PARAMETER);
		CHECK(validate_command_span(1, value, 1, 20, LOOP, 1, span) == ERR_INVALID_PARAMETER);
		CHECK(validate_command_span(1, 0, value, 20, LOOP, 1, span) == ERR_INVALID_PARAMETER);
		CHECK(validate_command_span(1, 0, 1, value, LOOP, 1, span) == ERR_INVALID_PARAMETER);
		CHECK(span.capacity == 17);
		CHECK(span.offset == 4);
		CHECK(span.count == 7);
		CHECK(span.stride == 32);
		CHECK(span.end == 216);
	}
}

TEST_CASE("[Rendering][MultiCommand] Oversized script values cannot wrap to valid native fields") {
	for (int64_t value : { int64_t(INT32_MAX) + 1, int64_t(1) << 32, std::numeric_limits<int64_t>::max() }) {
		CommandSpan span;
		CHECK(validate_command_span(value, 0, 1, 20, LOOP, 1, span) == ERR_INVALID_PARAMETER);
		CHECK(validate_command_span(1, value, 1, 20, LOOP, 1, span) == ERR_INVALID_PARAMETER);
		CHECK(validate_command_span(1, 0, value, 20, LOOP, 1, span) == ERR_INVALID_PARAMETER);
		CHECK(validate_command_span(1, 0, 1, value, LOOP, 1, span) == ERR_INVALID_PARAMETER);
	}
}

TEST_CASE("[Rendering][MultiCommand] Layout rejects zero capacity count and unaligned records") {
	CommandSpan span;
	CHECK(validate_command_span(0, 0, 1, 20, LOOP, 1, span) == ERR_INVALID_PARAMETER);
	CHECK(validate_command_span(1, 0, 0, 20, LOOP, 1, span) == ERR_INVALID_PARAMETER);
	CHECK(validate_command_span(1, 0, 1025, 20, LOOP, 1, span) == ERR_INVALID_PARAMETER);
	CHECK(validate_command_span(1, 2, 1, 20, LOOP, 1, span) == ERR_INVALID_PARAMETER);
	for (int64_t stride : { 0, 16, 19, 21, 22 }) {
		CHECK(validate_command_span(1, 0, 1, stride, LOOP, 1, span) == ERR_INVALID_PARAMETER);
	}
	CHECK(validate_command_span(1, 0, 1, 20, RSE::MultimeshIndirectSubmissionMode(2), 1, span) == ERR_INVALID_PARAMETER);
}

TEST_CASE("[Rendering][MultiCommand] LOOP and one-command MULTI_DRAW do not require multidraw support") {
	CommandSpan span;
	CHECK(validate_command_span(1, 0, 1024, 20, LOOP, 1, span) == OK);
	CHECK(validate_command_span(1, 0, 1, 20, MULTI_DRAW, 1, span) == OK);
	CHECK(validate_command_span(1, 0, 1, 20, MULTI_DRAW, 0, span) == OK);
	CHECK(validate_command_span(1, 0, 2, 20, MULTI_DRAW, 1, span) == ERR_UNAVAILABLE);
	CHECK(validate_command_span(1, 0, 2, 20, MULTI_DRAW, 0, span) == ERR_UNAVAILABLE);
	CHECK(validate_command_span(1, 0, 1024, 20, MULTI_DRAW, 1023, span) == ERR_UNAVAILABLE);
	CHECK(validate_command_span(1, 0, 1024, 20, MULTI_DRAW, 1024, span) == OK);
}

TEST_CASE("[Rendering][MultiCommand] Full span rejects wrapped products without changing output") {
	CommandSpan span{ 17, 4, 7, 32, 216 };
	CHECK(validate_command_span(1, 0, 1024, INT32_MAX - 3, LOOP, 1, span) == ERR_INVALID_PARAMETER);
	CHECK(span.count == 7);
	CHECK(span.end == 216);
	CHECK(validate_command_span(1, INT32_MAX - 3, 2, INT32_MAX - 3, LOOP, 1, span) == ERR_INVALID_PARAMETER);
	CHECK(span.offset == 4);
}

TEST_CASE("[Rendering][MultiCommand] Highest representable final record keeps wide offset arithmetic") {
	CommandSpan span;
	REQUIRE(validate_command_span(INT32_MAX, INT32_MAX - 3, 2, INT32_MAX - 19, LOOP, 1, span) == OK);
	CHECK(span.end == uint64_t(UINT32_MAX) - 3);
	CHECK(uint64_t(span.offset) + span.stride + 20 == span.end);
	CHECK(uint64_t(span.offset) + span.stride <= UINT32_MAX);
}

TEST_CASE("[SceneTree][Rendering][MultiCommand] Bindings retain signed64 layout and independent history default") {
	const MethodBind *attach = ClassDB::get_method("RenderingServer", "multimesh_attach_shared_draw_command_span");
	REQUIRE(attach != nullptr);
	CHECK(attach->get_argument_count() == 8);
	CHECK(attach->get_default_argument_count() == 1);
	CHECK_FALSE(bool(attach->get_default_argument(7)));
	for (int argument : { 1, 3, 4, 5 }) {
		CHECK(attach->get_argument_type(argument) == Variant::INT);
#ifdef DEBUG_ENABLED
		CHECK(attach->get_argument_meta(argument) == GodotTypeInfo::METADATA_INT_IS_INT64);
#endif
	}
	for (const char *name : { "multimesh_get_command_byte_offset", "multimesh_get_shared_draw_command_count", "multimesh_get_shared_draw_command_stride", "multimesh_get_shared_draw_submission_mode" }) {
		const MethodBind *query = ClassDB::get_method("RenderingServer", name);
		REQUIRE(query != nullptr);
		CHECK(query->get_argument_count() == 1);
		CHECK(query->get_return_info().type == Variant::INT);
		CHECK(query->is_const());
	}
	RenderingServer *server = RenderingServer::get_singleton();
	REQUIRE(server != nullptr);
	CHECK(server->multimesh_get_shared_draw_command_count(RID()) == 0);
	CHECK(server->multimesh_get_shared_draw_command_stride(RID()) == 0);
	CHECK(server->multimesh_get_shared_draw_submission_mode(RID()) == LOOP);
}

TEST_CASE("[Rendering][MultiCommand][IndirectCount] Counted spans preserve exact prefix suffix and final bytes") {
	CountedCommandSpan span;
	REQUIRE(validate_counted_command_span(1000000, 32, 1024, 32, 4, true, 1024, span) == OK);
	CHECK(span.commands.capacity == 1000000);
	CHECK(span.commands.offset == 32);
	CHECK(span.commands.count == 1024);
	CHECK(span.commands.stride == 32);
	CHECK(span.commands.end == 32788);
	CHECK(span.count_offset == 4);
	CHECK(span.count_end == 8);
	REQUIRE(validate_counted_command_span(8, 4, 4, 20, 84, true, 4, span) == OK);
	CHECK(span.commands.end == 84);
	CHECK(span.count_offset == 84);
	CHECK(span.count_end == 88);
	// Identical byte ranges in distinct banks are legal.
	CHECK(validate_counted_command_span(8, 4, 4, 20, 4, false, 4, span) == OK);
}

TEST_CASE("[Rendering][MultiCommand][IndirectCount] Counted signed64 fields reject each invalid value transactionally") {
	for (int64_t value : { int64_t(-1), std::numeric_limits<int64_t>::min(), int64_t(INT32_MAX) + 1, int64_t(1) << 32, std::numeric_limits<int64_t>::max() }) {
		CountedCommandSpan span{ { 17, 4, 7, 32, 216 }, 224, 228 };
		CHECK(validate_counted_command_span(value, 0, 1, 20, 24, false, 1024, span) == ERR_INVALID_PARAMETER);
		CHECK(validate_counted_command_span(1, value, 1, 20, 24, false, 1024, span) == ERR_INVALID_PARAMETER);
		CHECK(validate_counted_command_span(1, 0, value, 20, 24, false, 1024, span) == ERR_INVALID_PARAMETER);
		CHECK(validate_counted_command_span(1, 0, 1, value, 24, false, 1024, span) == ERR_INVALID_PARAMETER);
		CHECK(validate_counted_command_span(1, 0, 1, 20, value, false, 1024, span) == ERR_INVALID_PARAMETER);
		CHECK(span.commands.capacity == 17);
		CHECK(span.commands.offset == 4);
		CHECK(span.commands.count == 7);
		CHECK(span.commands.stride == 32);
		CHECK(span.commands.end == 216);
		CHECK(span.count_offset == 224);
		CHECK(span.count_end == 228);
	}
}

TEST_CASE("[Rendering][MultiCommand][IndirectCount] Counted zero bounds and maximum one retain mandatory stride") {
	CountedCommandSpan span;
	CHECK(validate_counted_command_span(0, 0, 1, 20, 0, false, 1024, span) == ERR_INVALID_PARAMETER);
	CHECK(validate_counted_command_span(1, 0, 0, 20, 0, false, 1024, span) == ERR_INVALID_PARAMETER);
	CHECK(validate_counted_command_span(1, 0, 1025, 20, 0, false, 2048, span) == ERR_INVALID_PARAMETER);
	CHECK(validate_counted_command_span(1, 2, 1, 20, 0, false, 1024, span) == ERR_INVALID_PARAMETER);
	CHECK(validate_counted_command_span(1, 0, 1, 20, 2, false, 1024, span) == ERR_INVALID_PARAMETER);
	for (int64_t stride : { 0, 16, 19, 21, 22 }) {
		CHECK(validate_counted_command_span(1, 0, 1, stride, 0, false, 1024, span) == ERR_INVALID_PARAMETER);
	}
	for (int64_t stride : { 20, 32 }) {
		REQUIRE(validate_counted_command_span(1, 32, 1, stride, 0, true, 1, span) == OK);
		CHECK(span.commands.end == 52);
		CHECK(span.commands.stride == stride);
	}
}

TEST_CASE("[Rendering][MultiCommand][IndirectCount] Counted bank overlap includes padding and exact boundaries") {
	CountedCommandSpan span{ { 17, 4, 7, 32, 216 }, 224, 228 };
	// Commands occupy [32, 84); even [52, 64) stride padding is reserved.
	for (int64_t count_offset : { 32, 48, 52, 60, 64, 80 }) {
		CHECK(validate_counted_command_span(8, 32, 2, 32, count_offset, true, 2, span) == ERR_INVALID_PARAMETER);
		CHECK(span.commands.end == 216);
		CHECK(span.count_offset == 224);
	}
	CHECK(validate_counted_command_span(8, 32, 2, 32, 28, true, 2, span) == OK);
	CHECK(span.count_end == span.commands.offset);
	CHECK(validate_counted_command_span(8, 32, 2, 32, 84, true, 2, span) == OK);
	CHECK(span.count_offset == span.commands.end);
}

TEST_CASE("[Rendering][MultiCommand][IndirectCount] Counted capability is required at maximum one independently of ordinary multidraw") {
	CountedCommandSpan span{ { 17, 4, 7, 32, 216 }, 224, 228 };
	CHECK(validate_counted_command_span(1, 0, 1, 20, 0, false, 0, span) == ERR_UNAVAILABLE);
	CHECK(validate_counted_command_span(1, 0, 2, 20, 0, false, 1, span) == ERR_UNAVAILABLE);
	CHECK(span.commands.count == 7);
	CHECK(span.count_end == 228);
	CHECK(validate_counted_command_span(1, 0, 1024, 20, 0, false, 1023, span) == ERR_UNAVAILABLE);
	CHECK(validate_counted_command_span(1, 0, 1024, 20, 0, false, 1024, span) == OK);
	CommandSpan old;
	CHECK(validate_command_span(1, 0, 1, 20, MULTI_DRAW, 0, old) == OK);
	CHECK(validate_command_span(1, 0, 1, 20, MULTI_DRAW_COUNT, 1024, old) == ERR_INVALID_PARAMETER);
	CHECK(int(LOOP) == 0);
	CHECK(int(MULTI_DRAW) == 1);
	CHECK(int(MULTI_DRAW_COUNT) == 2);
}

TEST_CASE("[Rendering][MultiCommand][IndirectCount] Counted full span preserves uint32 end and signed32 counter fields") {
	CountedCommandSpan span;
	REQUIRE(validate_counted_command_span(INT32_MAX, INT32_MAX - 3, 2, INT32_MAX - 19, INT32_MAX - 3, false, 2, span) == OK);
	CHECK(span.commands.end == uint64_t(UINT32_MAX) - 3);
	CHECK(span.count_end == uint64_t(INT32_MAX) + 1);
	const CountedCommandSpan previous = span;
	CHECK(validate_counted_command_span(1, 0, 1024, INT32_MAX - 3, 0, false, 1024, span) == ERR_INVALID_PARAMETER);
	CHECK(validate_counted_command_span(1, INT32_MAX - 3, 2, INT32_MAX - 3, 0, false, 2, span) == ERR_INVALID_PARAMETER);
	CHECK(span.commands.end == previous.commands.end);
	CHECK(span.count_end == previous.count_end);
}

TEST_CASE("[SceneTree][Rendering][MultiCommand][IndirectCount] Counted bindings preserve nine arguments signed64 and const counter queries") {
	const MethodBind *attach = ClassDB::get_method("RenderingServer", "multimesh_attach_shared_draw_command_span_count");
	REQUIRE(attach != nullptr);
	CHECK(attach->get_argument_count() == 9);
	CHECK(attach->get_default_argument_count() == 1);
	CHECK_FALSE(bool(attach->get_default_argument(8)));
	CHECK(attach->get_argument_type(8) == Variant::BOOL);
	CHECK(attach->get_return_info().type == Variant::INT);
	CHECK(ClassDB::get_integer_constant("RenderingServer", "MULTIMESH_INDIRECT_SUBMISSION_LOOP") == 0);
	CHECK(ClassDB::get_integer_constant("RenderingServer", "MULTIMESH_INDIRECT_SUBMISSION_MULTI_DRAW") == 1);
	CHECK(ClassDB::get_integer_constant("RenderingServer", "MULTIMESH_INDIRECT_SUBMISSION_MULTI_DRAW_COUNT") == 2);
	for (int argument : { 1, 3, 5, 6, 7 }) {
		CHECK(attach->get_argument_type(argument) == Variant::INT);
#ifdef DEBUG_ENABLED
		CHECK(attach->get_argument_meta(argument) == GodotTypeInfo::METADATA_INT_IS_INT64);
#endif
	}
	for (int argument : { 0, 2, 4 }) {
		CHECK(attach->get_argument_type(argument) == Variant::RID);
	}
	const MethodBind *bank = ClassDB::get_method("RenderingServer", "multimesh_get_shared_draw_count_buffer_rd_rid");
	const MethodBind *offset = ClassDB::get_method("RenderingServer", "multimesh_get_shared_draw_count_byte_offset");
	REQUIRE(bank != nullptr);
	REQUIRE(offset != nullptr);
	CHECK(bank->get_argument_count() == 1);
	CHECK(bank->get_return_info().type == Variant::RID);
	CHECK(bank->is_const());
	CHECK(offset->get_argument_count() == 1);
	CHECK(offset->get_return_info().type == Variant::INT);
	CHECK(offset->is_const());
	RenderingServer *server = RenderingServer::get_singleton();
	REQUIRE(server != nullptr);
	CHECK(server->multimesh_get_shared_draw_count_buffer_rd_rid(RID()).is_null());
	CHECK(server->multimesh_get_shared_draw_count_byte_offset(RID()) == 0);
}

} // namespace TestMultiCommand
