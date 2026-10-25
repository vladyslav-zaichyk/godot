shader_type spatial;
render_mode unshaded,fog_disabled,shadows_disabled;
instance uniform float identity;
void fragment(){ALBEDO=vec3(identity,0.0,0.0);}
