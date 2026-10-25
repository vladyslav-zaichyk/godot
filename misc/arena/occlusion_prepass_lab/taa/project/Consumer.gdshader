shader_type spatial;
render_mode shader_driven_instances, shader_driven_motion_vectors, skip_vertex_transform,
    unshaded, cull_disabled, fog_disabled, shadows_disabled;
uniform readonly_buffer identity_bank;
uniform readonly_buffer current_pose;
uniform readonly_buffer previous_pose;
uniform readonly_buffer lifecycle;
uniform readonly_buffer state;
uniform readonly_buffer header;
varying flat uint stable_color;
uint lane(uvec4 v, uint i) { if(i==0u)return v.x;if(i==1u)return v.y;if(i==2u)return v.z;return v.w; }
void vertex() {
    uint slot=uint(INSTANCE_ID), id=0xffffffffu; stable_color=0u;
    uvec4 h=buffer_load(header,0u);
    bool valid=slot<32u && h.z==16u && h.w==0xe8000001u;
    if(valid)id=lane(buffer_load(identity_bank,slot>>2u),slot&3u);
    valid=valid && id<16u;
    if(valid) {
        uvec4 s=buffer_load(state,id);
        valid=s.x==1u && s.y==1u && s.z==id && s.w!=0u;
    }
    if(valid) {
        uint b=id*4u;
        uvec4 c=buffer_load(current_pose,b+3u),p=buffer_load(previous_pose,b+3u),life=buffer_load(lifecycle,id);
        valid=c.x!=0u && c.x==buffer_load(state,id).w && c.y!=0u && c.w==h.x && life.x!=0u && life.z!=0u;
        bool history=valid && p.x==c.x && p.y==c.y && p.w==h.y && life.x==life.y && life.z==life.w;
        vec4 r0=uintBitsToFloat(buffer_load(current_pose,b));
        vec4 r1=uintBitsToFloat(buffer_load(current_pose,b+1u));
        vec4 r2=uintBitsToFloat(buffer_load(current_pose,b+2u));
        float d=uintBitsToFloat(c.z);
        if(IS_PREVIOUS_FRAME && history) {
            r0=uintBitsToFloat(buffer_load(previous_pose,b));r1=uintBitsToFloat(buffer_load(previous_pose,b+1u));
            r2=uintBitsToFloat(buffer_load(previous_pose,b+2u));d=uintBitsToFloat(p.z);
        }
        vec3 local=VERTEX;local.x+=d*(local.y+0.5);vec4 q=vec4(local,1.0);
        vec3 owner=vec3(dot(r0,q),dot(r1,q),dot(r2,q));
        valid=valid && !any(isnan(owner)) && !any(isinf(owner));
        VERTEX=(VIEW_MATRIX*MODEL_MATRIX*vec4(owner,1.0)).xyz;
        NORMAL=normalize(mat3(VIEW_MATRIX)*vec3(0.0,0.0,1.0));stable_color=id+1u;
    }
    if(!valid)VERTEX=vec3(0.0,0.0,1000.0);
}
void fragment(){ALBEDO=vec3(float(stable_color)/32.0,0.0,0.0);}
