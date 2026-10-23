shader_type spatial;
render_mode shader_driven_instances, skip_vertex_transform, unshaded, cull_disabled, fog_disabled, shadows_disabled;

uniform readonly_buffer visible_ids;
uniform readonly_buffer poses;
uniform uint id_base = 0u;
uniform uint expected_phase = 0u;
uniform vec2 view_offset = vec2(0.0);
varying flat uint color_bits;

void vertex() {
    uint slot = uint(INSTANCE_ID);
    uint packed_slot = id_base + slot;
    uvec4 record = buffer_load(visible_ids, packed_slot >> 2u);
    uint stable_id = record[int(packed_slot & 3u)];
    bool valid = slot == 3u && stable_id < 2u &&
        buffer_length(visible_ids) == 16u && buffer_length(poses) == 8u;
    color_bits = 5u;
    if (valid) {
        uint base = stable_id * 4u;
        vec4 r0 = uintBitsToFloat(buffer_load(poses, base));
        vec4 r1 = uintBitsToFloat(buffer_load(poses, base + 1u));
        vec4 r2 = uintBitsToFloat(buffer_load(poses, base + 2u));
        uvec4 metadata = buffer_load(poses, base + 3u);
        valid = metadata.y == stable_id && metadata.z == expected_phase;
        vec4 p = vec4(VERTEX, 1.0);
        vec3 world = vec3(dot(r0, p), dot(r1, p), dot(r2, p));
        world.xy += view_offset;
        // Keep native MODEL_MATRIX observable: controls use their identities,
        // while the shader-driven mode must preserve the owner without a pool read.
        VERTEX = (VIEW_MATRIX * MODEL_MATRIX * vec4(world, 1.0)).xyz;
        NORMAL = normalize(mat3(VIEW_MATRIX) * vec3(0.0, 0.0, 1.0));
        color_bits = metadata.x;
    }
    if (!valid) {
        VERTEX = vec3(0.0, 0.0, 1000.0);
    }
}

void fragment() {
    ALBEDO = vec3((color_bits & 1u) != 0u ? 1.0 : 0.0,
        (color_bits & 2u) != 0u ? 1.0 : 0.0,
        (color_bits & 4u) != 0u ? 1.0 : 0.0);
}
