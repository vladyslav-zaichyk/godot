shader_type spatial;
render_mode shader_driven_instances, shader_driven_motion_vectors, skip_vertex_transform,
    unshaded, cull_disabled, fog_disabled, shadows_disabled;

uniform readonly_buffer current_pose;
uniform readonly_buffer previous_pose;
uniform readonly_buffer lifecycle;
uniform readonly_buffer header;
uniform readonly_buffer visible_ids;

bool previous_context() { return IS_PREVIOUS_FRAME; }

void vertex() {
    uint slot = uint(INSTANCE_ID);
    uvec4 h = buffer_load(header, 0u);
    bool valid = h.w == 0xe8000001u && h.z >= 8u && h.z <= 64u && slot >= 3u && slot < 5u &&
        buffer_length(visible_ids) == 2u && buffer_length(lifecycle) == h.z &&
        buffer_length(current_pose) == h.z * 4u && buffer_length(previous_pose) == h.z * 4u;
    uint id = 0xffffffffu;
    if (valid) { uvec4 packed_id = buffer_load(visible_ids, slot >> 2u); uint lane = slot & 3u; id = lane == 0u ? packed_id.x : lane == 1u ? packed_id.y : lane == 2u ? packed_id.z : packed_id.w; valid = id < h.z; }
    if (valid) {
        uint base = id * 4u;
        uvec4 c = buffer_load(current_pose, base + 3u);
        uvec4 p = buffer_load(previous_pose, base + 3u);
        uvec4 life = buffer_load(lifecycle, id);
        valid = c.x != 0u && c.y != 0u && c.w == h.x && life.x != 0u && life.z != 0u;
        bool history = valid && p.x == c.x && p.y == c.y && p.w == h.y &&
            life.x == life.y && life.z == life.w;
        bool use_previous = previous_context() && history;
        vec4 r0 = uintBitsToFloat(buffer_load(current_pose, base));
        vec4 r1 = uintBitsToFloat(buffer_load(current_pose, base + 1u));
        vec4 r2 = uintBitsToFloat(buffer_load(current_pose, base + 2u));
        float deformation = uintBitsToFloat(c.z);
        if (use_previous) {
            r0 = uintBitsToFloat(buffer_load(previous_pose, base));
            r1 = uintBitsToFloat(buffer_load(previous_pose, base + 1u));
            r2 = uintBitsToFloat(buffer_load(previous_pose, base + 2u));
            deformation = uintBitsToFloat(p.z);
        }
        vec3 local = VERTEX;
        local.x += deformation * local.y;
        vec4 q = vec4(local, 1.0);
        vec3 owner_local = vec3(dot(r0, q), dot(r1, q), dot(r2, q));
        if (any(isnan(owner_local)) || any(isinf(owner_local))) { valid = false; }
        VERTEX = (VIEW_MATRIX * MODEL_MATRIX * vec4(owner_local, 1.0)).xyz;
        NORMAL = normalize(mat3(VIEW_MATRIX) * vec3(0.0, 0.0, 1.0));
    }
    if (!valid) { VERTEX = vec3(0.0, 0.0, 1000.0); }
}

void fragment() { ALBEDO = vec3(0.5); }
