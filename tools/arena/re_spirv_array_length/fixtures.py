"""Small authored SPIR-V programs; no engine, GLSL compiler or GPU needed."""


def assembly(kind):
    common = '''
OpCapability Shader
OpMemoryModel Logical GLSL450
OpEntryPoint GLCompute %main "main"
OpExecutionMode %main LocalSize 1 1 1
OpDecorate %records ArrayStride 4
OpMemberDecorate %BlockA 0 Offset 0
OpMemberDecorate %BlockA 1 Offset 16
OpDecorate %BlockA Block
OpMemberDecorate %BlockB 0 Offset 0
OpDecorate %BlockB Block
OpMemberDecorate %Output 0 Offset 0
OpDecorate %Output Block
OpMemberDecorate %Push 0 Offset 0
OpDecorate %Push Block
OpDecorate %a DescriptorSet 0
OpDecorate %a Binding 0
OpDecorate %b DescriptorSet 0
OpDecorate %b Binding 1
OpDecorate %output DescriptorSet 0
OpDecorate %output Binding 2
OpDecorate %enabled SpecId 0
%void = OpTypeVoid
%bool = OpTypeBool
%uint = OpTypeInt 32 0
%records = OpTypeRuntimeArray %uint
%BlockA = OpTypeStruct %uint %records
%BlockB = OpTypeStruct %records
%Output = OpTypeStruct %uint
%Push = OpTypeStruct %uint
%ptr_a = OpTypePointer StorageBuffer %BlockA
%ptr_b = OpTypePointer StorageBuffer %BlockB
%ptr_output = OpTypePointer StorageBuffer %Output
%ptr_uint = OpTypePointer StorageBuffer %uint
%ptr_push = OpTypePointer PushConstant %Push
%ptr_push_uint = OpTypePointer PushConstant %uint
%main_type = OpTypeFunction %void
%helper_type = OpTypeFunction %uint %uint
%uint_0 = OpConstant %uint 0
%uint_1 = OpConstant %uint 1
%uint_9 = OpConstant %uint 9
%enabled = OpSpecConstantTrue %bool
%a = OpVariable %ptr_a StorageBuffer
%b = OpVariable %ptr_b StorageBuffer
%output = OpVariable %ptr_output StorageBuffer
%push = OpVariable %ptr_push PushConstant
'''
    if kind == 'length_only':
        # The shipped BufferRD layout uses 16-byte uvec4 records. Length is a
        # record count, independent of either record stride or the header size.
        common = common.replace('OpDecorate %records ArrayStride 4', 'OpDecorate %records ArrayStride 16')
        common = common.replace('%records = OpTypeRuntimeArray %uint',
                                '%v4uint = OpTypeVector %uint 4\n%records = OpTypeRuntimeArray %v4uint')
    start = '''
%main = OpFunction %void None %main_type
%entry = OpLabel
%out_ptr = OpAccessChain %ptr_uint %output %uint_0
'''
    end = '''
OpReturn
OpFunctionEnd
'''
    if kind == 'ordinary':
        return common + start + 'OpStore %out_ptr %uint_9\n' + end
    if kind == 'length_only':
        return common + start + '''
%length_a = OpArrayLength %uint %a 1
%length_b = OpArrayLength %uint %b 0
%sum = OpIAdd %uint %length_a %length_b
OpStore %out_ptr %sum
''' + end
    if kind == 'dead_query':
        return common + start + '''
%dead_length = OpArrayLength %uint %a 1
OpStore %out_ptr %uint_9
''' + end
    if kind != 'guarded_load':
        raise ValueError(kind)
    # Numeric ID1 is a local result. A broken operand table would remap the
    # ArrayMember literal1 during function inlining, corrupting a valid query.
    helper = '''
%helper = OpFunction %uint None %helper_type
%index = OpFunctionParameter %uint
%helper_entry = OpLabel
%1 = OpIAdd %uint %uint_0 %uint_1
%length_a = OpArrayLength %uint %a 1
%length_b = OpArrayLength %uint %b 0
%oob = OpUGreaterThanEqual %bool %index %length_a
OpSelectionMerge %in_range None
OpBranchConditional %oob %outside %in_range
%outside = OpLabel
OpReturnValue %uint_0
%in_range = OpLabel
%record_ptr = OpAccessChain %ptr_uint %a %uint_1 %index
%record = OpLoad %uint %record_ptr
%value = OpIAdd %uint %record %length_b
OpReturnValue %value
OpFunctionEnd
'''
    main = start + '''
%index_ptr = OpAccessChain %ptr_push_uint %push %uint_0
%dynamic_index = OpLoad %uint %index_ptr
OpSelectionMerge %merge None
OpBranchConditional %enabled %read %disabled
%read = OpLabel
%result = OpFunctionCall %uint %helper %dynamic_index
OpStore %out_ptr %result
OpBranch %merge
%disabled = OpLabel
OpStore %out_ptr %uint_9
OpBranch %merge
%merge = OpLabel
''' + end
    return common + helper + main
