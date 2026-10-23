shader_type spatial;
render_mode unshaded, cull_disabled, fog_disabled, shadows_disabled;

uniform readonly_buffer data;
uniform int seed = 0;
uniform int record_index = 0;
uniform int expected_length = 4;
uniform bool expect_oob = false;
varying flat uint vertex_token;

void vertex() {
    vertex_token = buffer_load(data, 0u).x;
}

void fragment() {
    uint i = uint(record_index);
    uvec4 actual = buffer_load(data, i);
    uvec4 expected = uvec4(0u);
    float expected_float = 0.0;
    if (!expect_oob) {
        expected_float = float(uint(seed) + i) * 0.125;
        expected = uvec4(uint(seed) + i * 17u, uint(seed) ^ (i * 11u + 3u),
            floatBitsToUint(expected_float), 0x12340000u + i);
    }
    // Exact word checks and float reinterpretation become 0/1 channels. The
    // endpoints survive the ordinary 3D linear-to-sRGB output conversion.
    bool words_ok = all(equal(actual, expected)) && vertex_token == uint(seed);
    bool float_ok = uintBitsToFloat(actual.z) == expected_float;
    ALBEDO = vec3(words_ok ? 1.0 : 0.0, float_ok ? 1.0 : 0.0,
        buffer_length(data) == uint(expected_length) ? 1.0 : 0.0);
}
