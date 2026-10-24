shader_type spatial;
render_mode shader_driven_instances, skip_vertex_transform, unshaded, cull_disabled, fog_disabled, shadows_disabled;

uniform readonly_buffer visible_ids;
uniform uint id_base = 0u;
uniform uint columns = 128u;
uniform uint rows = 64u;
varying flat uint color_bits;

void vertex() {
    uint slot = id_base + uint(INSTANCE_ID);
    uvec4 word = buffer_load(visible_ids, slot >> 2u);
    uint identity = word[int(slot & 3u)];
    vec2 position = vec2(float(identity % columns) + 0.5 - float(columns) * 0.5,
        float(identity / columns) + 0.5 - float(rows) * 0.5);
    // A bounded data-only behavior marker; no nodes, simulation or animation ABI.
    color_bits = 1u + identity % 6u;
    VERTEX = (VIEW_MATRIX * MODEL_MATRIX * vec4(VERTEX.xy + position, 0.0, 1.0)).xyz;
    NORMAL = vec3(0.0, 0.0, 1.0);
}

void fragment() {
    ALBEDO = vec3((color_bits & 1u) != 0u ? 1.0 : 0.0,
        (color_bits & 2u) != 0u ? 1.0 : 0.0,
        (color_bits & 4u) != 0u ? 1.0 : 0.0);
}
