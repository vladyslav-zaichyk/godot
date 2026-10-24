#version 450
layout(push_constant, std430) uniform Quad { vec4 rectangle; } pc;
void main() {
    const vec2 corner[6] = vec2[6](vec2(0,0),vec2(1,0),vec2(1,1),vec2(0,0),vec2(1,1),vec2(0,1));
    vec2 pixel = mix(pc.rectangle.xy, pc.rectangle.zw, corner[gl_VertexIndex]);
    gl_Position = vec4(pixel / vec2(64.0,32.0) * 2.0 - 1.0, 0.6, 1.0);
}
