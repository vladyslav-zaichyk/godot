"""Bounded scalar SPIR-V interpreter for the authored CPU contract programs.

This is an independent semantic oracle, not a general SPIR-V implementation.
Unsupported executed instructions and out-of-bounds loads fail explicitly.
"""
import struct


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


class Program:
    def __init__(self, binary, opcodes):
        words = struct.unpack('<' + 'I' * (len(binary) // 4), binary)
        if words[0] != 0x07230203 or len(binary) % 4:
            raise ValueError('Invalid SPIR-V header.')
        self.instructions = []
        offset = 5
        while offset < len(words):
            count, opcode = words[offset] >> 16, words[offset] & 0xffff
            if not count or offset + count > len(words):
                raise ValueError('Invalid instruction size.')
            self.instructions.append((opcodes[opcode], words[offset + 1:offset + count]))
            offset += count
        self.constants, self.variables, self.bindings, self.types, self.functions = {}, {}, {}, {}, {}
        current = None
        for name, args in self.instructions:
            if name == 'EntryPoint':
                self.entry = args[1]
            elif name == 'Decorate' and args[1] == 33:  # Binding
                self.bindings[args[0]] = args[2]
            elif name.startswith('Type'):
                self.types[args[0]] = (name, args[1:])
            elif name in ('Constant', 'SpecConstant'):
                self.constants[args[1]] = args[2]
            elif name in ('ConstantTrue', 'ConstantFalse', 'SpecConstantTrue', 'SpecConstantFalse'):
                self.constants[args[1]] = 'spec' if name.startswith('Spec') else name == 'ConstantTrue'
            elif name == 'ConstantNull':
                self.constants[args[1]] = 0
            elif name == 'Function':
                current = {'parameters': [], 'code': []}
                self.functions[args[1]] = current
            elif name == 'FunctionEnd':
                current = None
            elif current is not None:
                if name == 'FunctionParameter':
                    current['parameters'].append(args[1])
                else:
                    current['code'].append((name, args))
            elif name == 'Variable':
                self.variables[args[1]] = (args[0], args[2])

    def count(self, name):
        return sum(op == name for op, _ in self.instructions)

    def check_lengths(self):
        """Check the complete pointer/type/literal chain after every remap."""
        members = []
        for name, args in self.instructions:
            if name != 'ArrayLength':
                continue
            _, _, structure, member = args
            pointer_type, _ = self.variables[structure]
            pointer_name, pointer = self.types[pointer_type]
            struct_name, fields = self.types[pointer[1]]
            require(pointer_name == 'TypePointer' and struct_name == 'TypeStruct', 'Length query lost its pointer/struct chain.')
            require(member == len(fields) - 1 and self.types[fields[member]][0] == 'TypeRuntimeArray', 'Length query member is not the final runtime array.')
            require(member == (1 if self.bindings[structure] == 0 else 0), 'Length query member literal was remapped.')
            members.append(member)
        return members

    def execute(self, index, length_a, length_b, enabled):
        values = {key: enabled if value == 'spec' else value for key, value in self.constants.items()}
        values.update({key: (key,) for key in self.variables})
        memory = {}
        buffers = {0: [100 + i * 7 for i in range(length_a)], 1: [900 + i for i in range(length_b)]}
        output = []

        def load(pointer):
            base, *path = pointer
            if base in self.variables:
                _, storage = self.variables[base]
                if storage == 9:  # PushConstant
                    require(path == [0], 'Unexpected push-constant addressing.')
                    return index
                binding = self.bindings[base]
                if binding in buffers:
                    member = 1 if binding == 0 else 0
                    require(len(path) == 2 and path[0] == member, 'Unexpected storage-buffer addressing.')
                    if path[1] >= len(buffers[binding]):
                        raise ValueError('Executed an out-of-bounds buffer load.')
                    return buffers[binding][path[1]]
            if pointer not in memory:
                raise ValueError('Read an uninitialized local variable.')
            return memory[pointer]

        def call(function, arguments, depth=0):
            if depth > 4:
                raise ValueError('Unexpected recursion.')
            body = self.functions[function]
            local = dict(values)
            local.update(zip(body['parameters'], arguments))
            labels = {args[0]: position for position, (name, args) in enumerate(body['code']) if name == 'Label'}
            pc, label, predecessor = 0, None, None
            for _ in range(1000):
                name, args = body['code'][pc]
                pc += 1
                if name == 'Label':
                    label = args[0]
                elif name == 'Variable':
                    local[args[1]] = (args[1],)
                    if len(args) == 4:
                        memory[(args[1],)] = local[args[3]]
                elif name == 'AccessChain':
                    local[args[1]] = (*local[args[2]], *(local[item] for item in args[3:]))
                elif name == 'Load':
                    local[args[1]] = load(local[args[2]])
                elif name == 'Store':
                    pointer, value = local[args[0]], local[args[1]]
                    if self.bindings.get(pointer[0]) == 2:
                        require(pointer[1:] == (0,), 'Unexpected output member.')
                        output.append(value)
                    else:
                        memory[pointer] = value
                elif name == 'ArrayLength':
                    pointer = local[args[2]]
                    require(len(pointer) == 1, 'Length query lost its global struct pointer.')
                    local[args[1]] = len(buffers[self.bindings[pointer[0]]])
                elif name == 'IAdd':
                    local[args[1]] = (local[args[2]] + local[args[3]]) & 0xffffffff
                elif name == 'UGreaterThanEqual':
                    local[args[1]] = local[args[2]] >= local[args[3]]
                elif name == 'FunctionCall':
                    local[args[1]] = call(args[2], [local[item] for item in args[3:]], depth + 1)
                elif name == 'CopyObject':
                    local[args[1]] = local[args[2]]
                elif name == 'Phi':
                    choices = dict(zip(args[3::2], args[2::2]))
                    local[args[1]] = local[choices[predecessor]]
                elif name in ('Branch', 'BranchConditional'):
                    predecessor = label
                    target = args[0] if name == 'Branch' else args[1] if local[args[0]] else args[2]
                    pc = labels[target]
                elif name in ('SelectionMerge', 'LoopMerge', 'Nop'):
                    pass
                elif name == 'ReturnValue':
                    return local[args[0]]
                elif name == 'Return':
                    return None
                else:
                    raise ValueError('Unsupported executed instruction: ' + name)
            raise ValueError('The bounded program failed to terminate.')

        call(self.entry, [])
        if len(output) != 1:
            raise ValueError('Expected exactly one output store.')
        return output[0]
