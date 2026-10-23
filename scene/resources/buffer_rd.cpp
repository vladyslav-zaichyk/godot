/**************************************************************************/
/*  buffer_rd.cpp                                                         */
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

#include "buffer_rd.h"

#include "core/object/class_db.h"
#include "core/os/os.h"
#include "servers/rendering/rendering_device.h"
#include "servers/rendering/rendering_server.h"

void BufferRD::_bind_methods() {
	ClassDB::bind_method(D_METHOD("attach_storage_buffer", "buffer_rd_rid"), &BufferRD::attach_storage_buffer);
	ClassDB::bind_method(D_METHOD("get_buffer_rd_rid"), &BufferRD::get_buffer_rd_rid);
	ADD_PROPERTY(PropertyInfo(Variant::RID, "buffer_rd_rid", PROPERTY_HINT_NONE, "", PROPERTY_USAGE_NONE), "", "get_buffer_rd_rid");
}

Error BufferRD::attach_storage_buffer(RID p_buffer_rd_rid) {
	RenderingServer *rs = RenderingServer::get_singleton();
	if (!rs || !rs->is_on_render_thread() || !RD::get_singleton() ||
			OS::get_singleton()->get_current_rendering_method() != "forward_plus" ||
			OS::get_singleton()->get_current_rendering_driver_name() != "vulkan") {
		return ERR_UNAVAILABLE;
	}
	// Owner lookup rejects foreign-device, freed and non-storage RIDs without
	// accessing GPU bytes. A record is exactly one std430 uvec4 (16 bytes).
	if (!RD::get_singleton()->storage_buffer_is_valid(p_buffer_rd_rid, 16, false, 16)) {
		return ERR_INVALID_PARAMETER;
	}
	if (buffer_rd_rid.is_valid() && buffer_rd_rid != p_buffer_rd_rid) {
		return ERR_ALREADY_IN_USE;
	}
	if (buffer_rd_rid != p_buffer_rd_rid) {
		buffer_rd_rid = p_buffer_rd_rid;
		emit_changed();
	}
	return OK;
}

RID BufferRD::get_buffer_rd_rid() const {
	return buffer_rd_rid;
}

RID BufferRD::get_rid() const {
	return buffer_rd_rid;
}
