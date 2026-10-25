shader_type spatial;
render_mode diffuse_lambert, specular_disabled, ambient_light_disabled, fog_disabled;
void fragment() { ALBEDO=vec3(1.0); EMISSION=vec3(0.0,0.0,0.25); }
// Only the directional light and its native shadow attenuation contribute to red.
void light() { DIFFUSE_LIGHT+=vec3(ATTENUATION*LIGHT_COLOR.r,0.0,0.0); }
