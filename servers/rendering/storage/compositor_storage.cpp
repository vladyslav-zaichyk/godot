/**************************************************************************/
/*  compositor_storage.cpp                                                */
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

#include "compositor_storage.h"

// Storage

RendererCompositorStorage *RendererCompositorStorage::singleton = nullptr;

RendererCompositorStorage::RendererCompositorStorage() {
	singleton = this;
}

RendererCompositorStorage::~RendererCompositorStorage() {
	singleton = nullptr;
}

void RendererCompositorStorage::_gpu_occlusion_mark_changed() {
	if (gpu_occlusion_revision != UINT64_MAX) {
		gpu_occlusion_revision++;
	}
}

bool RendererCompositorStorage::gpu_occlusion_observer_effect_is_valid(RID p_effect) const {
	const CompositorEffect *effect = compositor_effects_owner.get_or_null(p_effect);
	return effect && effect->is_enabled && effect->callback_type == RSE::COMPOSITOR_EFFECT_CALLBACK_TYPE_POST_OPAQUE &&
			effect->callback.is_valid() && effect->flags.is_empty();
}

bool RendererCompositorStorage::gpu_occlusion_observer_is_valid(RID p_compositor, RID p_effect, bool p_using_taa) const {
	if (gpu_occlusion_revision == UINT64_MAX) {
		return false;
	}
	if (p_effect.is_null()) {
		// Keep the original no-compositor policy, including empty compositors.
		return p_compositor.is_null();
	}
	if (!p_using_taa || !gpu_occlusion_observer_effect_is_valid(p_effect)) {
		return false;
	}
	const Compositor *compositor = compositor_owner.get_or_null(p_compositor);
	if (!compositor) {
		return false;
	}
	uint32_t enabled_count = 0;
	for (RID rid : compositor->compositor_effects) {
		const CompositorEffect *effect = compositor_effects_owner.get_or_null(rid);
		if (!effect) {
			return false;
		}
		if (effect->is_enabled) {
			// Count occurrences across ALL callback stages. Duplicate RIDs fail.
			if (rid != p_effect || ++enabled_count > 1) {
				return false;
			}
		}
	}
	return enabled_count == 1;
}

// Compositor effect

RID RendererCompositorStorage::compositor_effect_allocate() {
	return compositor_effects_owner.allocate_rid();
}

void RendererCompositorStorage::compositor_effect_initialize(RID p_rid) {
	compositor_effects_owner.initialize_rid(p_rid, CompositorEffect());
	_gpu_occlusion_mark_changed();
}

void RendererCompositorStorage::compositor_effect_free(RID p_rid) {
	CompositorEffect *effect = compositor_effects_owner.get_or_null(p_rid);
	ERR_FAIL_NULL(effect);
	_gpu_occlusion_mark_changed();

	// Remove this RID from any compositor that uses it.
	for (const RID &compositor_rid : compositor_owner.get_owned_list()) {
		Compositor *compositor = compositor_owner.get_or_null(compositor_rid);
		if (compositor) {
			compositor->compositor_effects.erase(p_rid);
		}
	}

	// Update motion vector count if needed.
	if (effect->is_enabled && effect->flags.has_flag(RSE::CompositorEffectFlags::COMPOSITOR_EFFECT_FLAG_NEEDS_MOTION_VECTORS)) {
		num_compositor_effects_with_motion_vectors--;
	}

	compositor_effects_owner.free(p_rid);
}

void RendererCompositorStorage::compositor_effect_set_callback(RID p_effect, RSE::CompositorEffectCallbackType p_callback_type, const Callable &p_callback) {
	CompositorEffect *effect = compositor_effects_owner.get_or_null(p_effect);
	ERR_FAIL_NULL(effect);

	_gpu_occlusion_mark_changed();
	effect->callback_type = p_callback_type;
	effect->callback = p_callback;
}

void RendererCompositorStorage::compositor_effect_set_enabled(RID p_effect, bool p_enabled) {
	CompositorEffect *effect = compositor_effects_owner.get_or_null(p_effect);
	ERR_FAIL_NULL(effect);

	_gpu_occlusion_mark_changed();
	if (effect->is_enabled != p_enabled && effect->flags.has_flag(RSE::CompositorEffectFlags::COMPOSITOR_EFFECT_FLAG_NEEDS_MOTION_VECTORS)) {
		if (p_enabled) {
			num_compositor_effects_with_motion_vectors++;
		} else {
			num_compositor_effects_with_motion_vectors--;
		}
	}

	effect->is_enabled = p_enabled;
}

bool RendererCompositorStorage::compositor_effect_get_enabled(RID p_effect) const {
	CompositorEffect *effect = compositor_effects_owner.get_or_null(p_effect);
	ERR_FAIL_NULL_V(effect, false);

	return effect->is_enabled;
}

RSE::CompositorEffectCallbackType RendererCompositorStorage::compositor_effect_get_callback_type(RID p_effect) const {
	CompositorEffect *effect = compositor_effects_owner.get_or_null(p_effect);
	ERR_FAIL_NULL_V(effect, RSE::COMPOSITOR_EFFECT_CALLBACK_TYPE_MAX);

	return effect->callback_type;
}

Callable RendererCompositorStorage::compositor_effect_get_callback(RID p_effect) const {
	CompositorEffect *effect = compositor_effects_owner.get_or_null(p_effect);
	ERR_FAIL_NULL_V(effect, Callable());

	return effect->callback;
}

void RendererCompositorStorage::compositor_effect_set_flag(RID p_effect, RSE::CompositorEffectFlags p_flag, bool p_set) {
	CompositorEffect *effect = compositor_effects_owner.get_or_null(p_effect);
	ERR_FAIL_NULL(effect);

	_gpu_occlusion_mark_changed();
	if (effect->is_enabled && p_flag == RSE::CompositorEffectFlags::COMPOSITOR_EFFECT_FLAG_NEEDS_MOTION_VECTORS) {
		bool was_set = effect->flags.has_flag(RSE::CompositorEffectFlags::COMPOSITOR_EFFECT_FLAG_NEEDS_MOTION_VECTORS);

		if (was_set != p_set) {
			if (p_set) {
				num_compositor_effects_with_motion_vectors++;
			} else {
				num_compositor_effects_with_motion_vectors--;
			}
		}
	}

	if (p_set) {
		effect->flags.set_flag(p_flag);
	} else {
		effect->flags.clear_flag(p_flag);
	}
}

bool RendererCompositorStorage::compositor_effect_get_flag(RID p_effect, RSE::CompositorEffectFlags p_flag) const {
	CompositorEffect *effect = compositor_effects_owner.get_or_null(p_effect);
	ERR_FAIL_NULL_V(effect, false);

	return effect->flags.has_flag(p_flag);
}

// Compositor

RID RendererCompositorStorage::compositor_allocate() {
	return compositor_owner.allocate_rid();
}

void RendererCompositorStorage::compositor_initialize(RID p_rid) {
	compositor_owner.initialize_rid(p_rid, Compositor());
	_gpu_occlusion_mark_changed();
}

void RendererCompositorStorage::compositor_free(RID p_rid) {
	_gpu_occlusion_mark_changed();
	compositor_owner.free(p_rid);
}

// compositor effects

void RendererCompositorStorage::compositor_set_compositor_effects(RID p_compositor, const Vector<RID> &p_effects) {
	Compositor *compositor = compositor_owner.get_or_null(p_compositor);
	ERR_FAIL_NULL(compositor);

	_gpu_occlusion_mark_changed();
	compositor->compositor_effects.clear();
	for (const RID &effect : p_effects) {
		if (is_compositor_effect(effect)) {
			compositor->compositor_effects.push_back(effect);
		}
	}
}

Vector<RID> RendererCompositorStorage::compositor_get_compositor_effects(RID p_compositor, RSE::CompositorEffectCallbackType p_callback_type, bool p_enabled_only) const {
	Compositor *compositor = compositor_owner.get_or_null(p_compositor);
	ERR_FAIL_NULL_V(compositor, Vector<RID>());

	if (p_enabled_only || p_callback_type != RSE::COMPOSITOR_EFFECT_CALLBACK_TYPE_ANY) {
		Vector<RID> effects;

		for (RID rid : compositor->compositor_effects) {
			if ((!p_enabled_only || compositor_effect_get_enabled(rid)) && (p_callback_type == RSE::COMPOSITOR_EFFECT_CALLBACK_TYPE_ANY || compositor_effect_get_callback_type(rid) == p_callback_type)) {
				effects.push_back(rid);
			}
		}

		return effects;
	} else {
		return compositor->compositor_effects;
	}
}
