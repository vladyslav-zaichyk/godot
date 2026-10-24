shader_type spatial;
render_mode shader_driven_instances, skip_vertex_transform, unshaded, cull_disabled, fog_disabled, shadows_disabled;
uniform readonly_buffer identity_bank;
uniform readonly_buffer slots;
varying flat uint color_bits;
void vertex() {
    uint draw_slot = uint(INSTANCE_ID);
    uint stable_id = 0xffffffffu;
    if (draw_slot < 32u) {
        uvec4 ids = buffer_load(identity_bank, draw_slot >> 2u);
        stable_id = ids[int(draw_slot & 3u)];
    }
    // The complete immutable fallback addresses all sixteen stable slots.
    // State is checked before pose/geometry work; no dense old candidate is fallback.
    bool active = false;
    color_bits = 0u;
    if (stable_id < 16u) {
        uvec4 state = buffer_load(slots, stable_id * 2u + 1u);
        active = state.x == 1u && state.z == stable_id && state.w == 0x413133u;
        if (active) {
            vec4 root = uintBitsToFloat(buffer_load(slots, stable_id * 2u));
            vec3 local = root.xyz + vec3(VERTEX.xy * root.w, 0.0);
            vec4 world = MODEL_MATRIX * vec4(local, 1.0);
            VERTEX = (VIEW_MATRIX * world).xyz;
            NORMAL = normalize(mat3(VIEW_MATRIX) * vec3(0.0, 0.0, 1.0));
            color_bits = state.y;
        }
    }
    if (!active) { VERTEX = vec3(0.0, 0.0, 1000.0); }
}
void fragment() {
    ALBEDO = vec3((color_bits & 1u) != 0u ? 1.0 : 0.0,
        (color_bits & 2u) != 0u ? 1.0 : 0.0, (color_bits & 4u) != 0u ? 1.0 : 0.0);
}
