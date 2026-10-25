shader_type spatial;
render_mode shader_driven_instances, skip_vertex_transform, unshaded, cull_disabled, fog_disabled, shadows_disabled;
void vertex(){VERTEX=vec3(0.0,0.0,1000.0);}
void fragment(){ALBEDO=vec3(0.0);}
