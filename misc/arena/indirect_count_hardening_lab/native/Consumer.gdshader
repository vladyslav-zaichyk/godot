shader_type spatial;
render_mode shader_driven_instances, skip_vertex_transform, unshaded, cull_disabled, fog_disabled, shadows_disabled;

uniform readonly_buffer visible_ids;
uniform readonly_buffer probe_alias;
uniform vec2 visual_offset = vec2(0.0);
varying flat uint color_bits;

void vertex() {
    uint slot = uint(INSTANCE_ID);
    uvec4 packed_ids = buffer_load(visible_ids, slot >> 2u);
    uint identity = packed_ids[int(slot & 3u)];
    // Keep the probe descriptor observable in both native depth and color paths.
    bool marker_valid = buffer_load(probe_alias, 0u).x == 0xcdcdcdcdu;
    vec2 position = vec2(float(identity % 4u) - 1.5, float(identity / 4u) - 1.0) + visual_offset;
    VERTEX = (VIEW_MATRIX * MODEL_MATRIX * vec4(VERTEX.xy + position, 0.0, 1.0)).xyz;
    NORMAL = vec3(0.0,0.0,1.0);
    color_bits = 1u + identity % 6u;
    if (!marker_valid || identity >= 12u) VERTEX = vec3(0.0,0.0,1000.0);
}

void fragment() {
    ALBEDO = vec3((color_bits & 1u) != 0u ? 1.0 : 0.0,
        (color_bits & 2u) != 0u ? 1.0 : 0.0,
        (color_bits & 4u) != 0u ? 1.0 : 0.0);
}
