#!/usr/bin/env python3
"""Extract NVIDIA 621.14 DL-DSR kernels and weights for RTX GPUs.

Usage: python3 tools/extract-dldsr.py /path/nvlddmkm.sys /path/output-directory
The destination must be new, with an existing parent directory.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import tempfile

DRIVER_VERSION = "621.14"
DRIVER_BYTES = 102238952
DRIVER_SHA256 = "447c10f368d19db5f9dae9cceb690918d761f0d361d98017b7a68a54d99c6ba5"
PARAM_BYTES = 40
ARCHES = (75, 86, 120, 121)
FIXTURE_PATH = Path(__file__).with_name("dldsr-621.14.json")

class ExtractionError(Exception):
    """An input, metadata or destination validation failure."""


def require(condition, message):
    if not condition:
        raise ExtractionError(message)


def check_range(total, offset, size, label):
    require(0 <= offset <= total and 0 <= size <= total - offset,
            "{} range exceeds its containing data".format(label))


def unpack(fmt, data, offset, label):
    check_range(len(data), offset, struct.calcsize(fmt), label)
    return struct.unpack_from(fmt, data, offset)


def u32(data, offset, label):
    return unpack("<I", data, offset, label)[0]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def verify_hash(data, expected, label):
    actual = sha256(data)
    require(actual == expected,
            "{} SHA256 is {}; expected {}".format(label, actual, expected))
    return actual


def read_driver(path):
    require(stat.S_ISREG(path.stat().st_mode), "driver must be a regular file")
    with path.open("rb") as stream:
        metadata = os.fstat(stream.fileno())
        require(stat.S_ISREG(metadata.st_mode), "driver must be a regular file")
        require(metadata.st_size == DRIVER_BYTES,
                "NVIDIA {} requires a {}-byte driver; supplied size is {}".format(
                    DRIVER_VERSION, DRIVER_BYTES, metadata.st_size))
        # Bound memory use even if the file changes during the read. All further
        # reads and hashes use this single immutable snapshot.
        data = stream.read(DRIVER_BYTES + 1)
    require(len(data) == DRIVER_BYTES, "driver size changed during the read")
    return data


class PEImage:
    """Bounds-checked PE32+ section mapping using the file's own image base."""

    def __init__(self, data):
        self.data = data
        check_range(len(data), 0, 0x40, "DOS header")
        require(data[:2] == b"MZ", "driver requires an MZ DOS header")
        pe = u32(data, 0x3C, "PE header pointer")
        require(pe >= 0x40, "PE header overlaps the DOS header")
        check_range(len(data), pe, 24, "PE header")
        require(data[pe:pe + 4] == b"PE\0\0", "driver requires a PE signature")
        machine, count, _, _, _, optional_size, _ = unpack(
            "<HHIIIHH", data, pe + 4, "COFF header")
        require(machine == 0x8664, "driver requires the AMD64 PE machine type")
        require(1 <= count <= 96, "PE section count must be between 1 and 96")
        optional = pe + 24
        require(optional_size >= 112, "PE32+ optional header is truncated")
        check_range(len(data), optional, optional_size, "PE optional header")
        require(unpack("<H", data, optional, "PE magic")[0] == 0x20B,
                "driver requires the PE32+ optional-header format")
        self.image_base = unpack("<Q", data, optional + 24, "PE image base")[0]
        self.image_size = u32(data, optional + 56, "PE image size")
        header_size = u32(data, optional + 60, "PE header size")
        check_range(len(data), 0, header_size, "PE headers")
        require(self.image_base + self.image_size <= 1 << 64,
                "PE virtual address range overflows 64 bits")
        table = optional + optional_size
        check_range(header_size, table, 40 * count, "PE section table")
        self.sections = []
        for index in range(count):
            name, virtual_size, rva, raw_size, raw_offset = unpack(
                "<8sIIII", data, table + index * 40, "PE section header")
            span = max(virtual_size, raw_size)
            check_range(self.image_size, rva, span, "PE virtual section")
            if raw_size:
                require(raw_offset >= header_size,
                        "PE raw section overlaps its headers")
                check_range(len(data), raw_offset, raw_size, "PE raw section")
            self.sections.append((name.rstrip(b"\0").decode("ascii", "replace"),
                                  rva, span, raw_size, raw_offset))

    def read_va(self, va, size):
        require(size > 0, "PE VA read requires a positive length")
        rva = va - self.image_base
        check_range(self.image_size, rva, size, "PE VA")
        # Reject both cross-section reads and ambiguous overlapping mappings.
        matches = [section for section in self.sections
                   if rva < section[1] + section[2] and section[1] < rva + size]
        require(len(matches) == 1,
                "VA {} requires exactly one PE section mapping".format(hex(va)))
        name, section_rva, span, raw_size, raw_offset = matches[0]
        delta = rva - section_rva
        check_range(span, delta, size, "PE virtual section read")
        check_range(raw_size, delta, size, "PE file-backed section read")
        offset = raw_offset + delta
        check_range(len(self.data), offset, size, "PE file read")
        data = self.data[offset:offset + size]
        return data, {"va": hex(va), "file_offset": offset, "section": name,
                      "bytes": size, "sha256": sha256(data)}



FormatError = ExtractionError
check_hash = verify_hash

def take(data, offset, size, label):
    check_range(len(data), offset, size, label)
    return data[offset:offset + size]

class PE(PEImage):
    def read(self, va, size):
        return self.read_va(va, size)[0]

def load_fixtures():
    fixtures = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))
    require(fixtures['version'] == 1, 'unsupported fixture schema')
    require(fixtures['driver_sha256'] == DRIVER_SHA256, 'unexpected fixture source')
    return fixtures


def parse_program(container, sm, fixture, program):
    label = f'sm{sm}/prog{program:02}'
    record = take(container, fixture['record_offset'], fixture['record_bytes'], label)
    check_hash(record, fixture['record_sha256'], label + ' NVuc record')
    header = unpack('<8I', record, 0, label + ' header')
    require(header == (0x6375564e, 0x10001, sm << 16 | 5, sm << 16 | sm,
                       sm << 16 | 5, 1, len(record), 0), label + ': NVuc header mismatch')
    expected_sections = ((1, fixture['code_bytes'], 0xc4), (3, 4, 0xc0),
                         (0x15, 0, 0), (0x17, 0, 0))
    for index, section in enumerate(expected_sections):
        got = unpack('<8I', record, 0x20 + index * 0x20, label + ' descriptor')
        require(got == (*section, 0, 0, 0, 0, 0), label + ': section descriptor mismatch')
    inline = unpack('<8I', record, 0xa0, label + ' inline metadata')
    require(inline == (0x1403, 0, 0, 0, fixture['nvuc_shared_bytes'], 1, 0x10001, 1),
            label + ': NVuc resource metadata mismatch')
    registers, = unpack('<I', record, 0xc0, label + ' register count')
    require(registers == fixture['registers'], label + ': register count mismatch')
    code = take(record, 0xc4, fixture['code_bytes'], label + ' instructions')
    require(len(record) == len(code) + 0xc4 and len(code) % 16 == 0,
            label + ': code alignment or record extent mismatch')
    check_hash(code, fixture['code_sha256'], label + ' instructions')
    # EXIT is opcode 0x94d on these four architectures; predication is bits 12..15.
    actual_exits = [pc for pc in range(0, len(code), 16)
                    if (struct.unpack_from('<I', code, pc)[0] & 0xfff) == 0x94d]
    require(actual_exits == fixture['exit_pcs'], label + ': EXIT offset mismatch')
    return code


def adapt_code(code, sm, fixture):
    """Preserve packed-handle SM75/86; load the independent sampler on SM12x."""
    check_hash(code, fixture['code_sha256'], f'sm{sm} patch input')
    result = bytearray(code)
    patches = fixture['sampler_patch_pcs']
    require((sm in (75, 86) and len(patches) == 0) or
            (sm in (120, 121) and len(patches) == 6), 'unexpected sampler patch count')
    for pc in patches:
        # LDCU UR5,c0[0x3a0], stall 15 and write barrier 0. The following
        # instruction waits on barrier 0, retaining its existing control bits.
        require(pc % 16 == 0 and pc + 32 <= len(code), 'patch range exceeds code')
        old = unpack('<4I', code, pc, 'UMOV patch input')
        require(old[:3] == (0x00057c82, 0xff, 0x08000000), 'expected UMOV UR5,URZ')
        control = 0x000e2f00
        struct.pack_into('<4I', result, pc, 0xff0577ac, 0x3a0 << 5, 0x08000800, control)
        following, = unpack('<I', code, pc + 28, 'following dependency control')
        struct.pack_into('<I', result, pc + 28, following | 0x00100000)
    patched = bytes(result)
    # The fixture pins adapter bytes and the complete byte-for-byte unchanged body.
    check_hash(patched, fixture['vulkan_code_sha256'], f'sm{sm} adapted code')
    allowed = {pc for patch in patches for pc in (patch, patch + 16)}
    changes = [pc for pc in range(0, len(code), 16)
               if code[pc:pc + 16] != patched[pc:pc + 16]]
    require(changes == fixture['changed_instruction_pcs'], 'instruction change set differs')
    require(set(changes) <= allowed, 'adapter changed an unexpected instruction')
    for pc in range(0, len(code), 16):
        if pc not in allowed:
            require(code[pc:pc + 16] == patched[pc:pc + 16], 'non-adapter instruction differs')
    return patched


def attr(tag, *values):
    data = struct.pack('<' + 'I' * len(values), *values)
    return struct.pack('<BBH', 4, tag, len(data)) + data


def half_attr(tag, value):
    return struct.pack('<BBH', 3, tag, value)


def byte_attr(tag, value):
    return struct.pack('<BBH', 2, tag, value)


def build_cubin(code, sm, registers, shared, exit_pcs, param_bytes=PARAM_BYTES):
    """Traditional CUDA ELF64 ABI7, matching the proven SM120 emitter exactly."""
    require(sm in ARCHES and 0 < registers <= 255, 'invalid CUDA target/resources')
    require(len(code) % 16 == 0 and 0 < param_bytes <= 0xffff, 'invalid code/parameters')
    param_base = 0x160 if sm < 120 else 0x380
    names = ['', '.shstrtab', '.strtab', '.symtab', '.nv.info', '.nv.info.dldsr',
             '.text.dldsr', '.nv.constant0.dldsr', '.nv.shared.dldsr']
    name_data = b'\0'
    name_offsets = []
    for name in names:
        name_offsets.append(len(name_data) if name else 0)
        if name:
            name_data += name.encode('ascii') + b'\0'
    strings = b'\0dldsr\0'

    def symbol(name, info, other, section, value, size):
        return struct.pack('<IBBHQQ', name, info, other, section, value, size)

    symbols = (bytes(24) + symbol(0, 3, 0, 6, 0, 0) + symbol(0, 3, 0, 7, 0, 0)
               + symbol(0, 3, 0, 8, 0, 0) + symbol(1, 0x12, 0x10, 6, 0, len(code)))
    module_info = attr(0x2f, 4, registers) + attr(0x11, 4, 0) + attr(0x12, 4, 0)
    kparam = struct.pack('<BBHIHHI', 4, 0x17, 12, 0, 0, 0, (param_bytes << 18) | 0x1f000)
    entry_info = (attr(0x37, 130) + kparam + half_attr(0x1b, 255) + byte_attr(0x4c, 1)
                  + attr(0x1c, *exit_pcs) + half_attr(0x19, param_bytes)
                  + attr(0x0a, 2, (param_bytes << 16) | param_base))
    constants = bytes(param_base + param_bytes)
    # type, flags, link, info, alignment, entrysize, file payload, logical size
    sections = [
        (0, 0, 0, 0, 0, 0, b'', 0),
        (3, 0, 0, 0, 1, 0, name_data, len(name_data)),
        (3, 0, 0, 0, 1, 0, strings, len(strings)),
        (2, 0, 2, 4, 8, 24, symbols, len(symbols)),
        (0x70000000, 0, 3, 0, 4, 0, module_info, len(module_info)),
        (0x70000000, 0x40, 3, 6, 4, 0, entry_info, len(entry_info)),
        (1, 6, 3, registers << 24 | 4, 128, 0, code, len(code)),
        (1, 0x42, 0, 6, 4, 0, constants, len(constants)),
        (8, 3, 0, 6, 16, 0, b'', shared),
    ]
    data = bytearray(64)
    headers = []
    for i, (kind, flags, link, info, alignment, entsize, payload, size) in enumerate(sections):
        if alignment:
            data.extend(bytes((-len(data)) % alignment))
        offset = len(data) if i else 0
        data.extend(payload)
        headers.append(struct.pack('<IIQQQQIIQQ', name_offsets[i], kind, flags, 0,
                                   offset, size, link, info, alignment, entsize))
    data.extend(bytes((-len(data)) % 8))
    shoff = len(data)
    data.extend(b''.join(headers))
    ident = b'\x7fELF' + bytes([2, 1, 1, 0x33, 7]) + bytes(7)
    # SM in low byte, 64-bit-address + unified-texture flags, virtual SM in bits 16..23.
    flags = sm << 16 | 0x500 | sm
    data[:64] = struct.pack('<16sHHIQQQIHHHHHH', ident, 2, 190, 1, 0, 0, shoff,
                           flags, 64, 56, 0, 64, len(sections), 1)
    return bytes(data)


def make_assets(driver, arches=ARCHES, fixtures=None):
    """Return deterministic {relative path: bytes} after all source checks pass."""
    fixtures = load_fixtures() if fixtures is None else fixtures
    require(len(driver) == DRIVER_BYTES, 'unexpected driver file size')
    check_hash(driver, DRIVER_SHA256, '621.14 driver')
    require(len(set(arches)) == len(arches) and set(arches) <= set(ARCHES) and arches,
            'select one or more distinct pinned architectures')
    pe = PE(driver)
    # Pin factor mapping and architecture dispatch independently of each container.
    for evidence in fixtures['selection_ranges']:
        check_hash(pe.read(evidence['va'], evidence['bytes']), evidence['sha256'], evidence['label'])
    for row in range(2):
        slot, *columns = struct.unpack('<I6H', pe.read(0x1411e0870 + 16 * row, 16))
        require(slot == row and columns == [0xff, 0xff, row, row, row, row],
                'factor-to-program selection mismatch')
    assets = {}
    manifest = {'schema_version': 1, 'source_driver_sha256': DRIVER_SHA256,
                'kernel_name': 'dldsr', 'parameter_bytes': PARAM_BYTES,
                'parameters': fixtures['parameters'], 'architectures': {}, 'weights': []}
    for model, weight in enumerate(fixtures['weights']):
        data = pe.read(weight['va'], weight['bytes'])
        check_hash(data, weight['sha256'], f'weights{model:02}')
        name = f'weights{model:02}.bin'
        assets[name] = data
        manifest['weights'].append({'path': name, 'bytes': len(data), 'sha256': sha256(data)})
    for sm in sorted(arches):
        arch = fixtures['architectures'][str(sm)]
        container = pe.read(arch['va'], arch['bytes'])
        check_hash(container, arch['sha256'], f'sm{sm} container')
        size_field, = struct.unpack('<I', pe.read(arch['size_va'], 4))
        require(size_field == len(container), f'sm{sm}: driver container size mismatch')
        entry = {k: arch[k] for k in ('source_handle_mode',
            'use64bit_texturing', 'parameter_base', 'elf_flags', 'compatible_sm')}
        entry['directory'] = f'sm{sm}'
        entry['programs'] = []
        for model, program in enumerate(arch['programs']):
            original = parse_program(container, sm, program, model)
            adapted = adapt_code(original, sm, program)
            cubin = build_cubin(adapted, sm, program['registers'], program['elf_shared_bytes'],
                                program['exit_pcs'])
            check_hash(cubin, program['vulkan_cubin_sha256'], f'sm{sm}/prog{model:02} cubin')
            name = f'sm{sm}/prog{model:02}-vk.cubin'
            assets[name] = cubin
            entry['programs'].append({
                'path': name, 'sha256': sha256(cubin), 'factor': ['1.78', '2.25'][model],
                'source_tile': [[32, 48], [48, 48]][model],
                'output_tile': [[24, 36], [32, 32]][model],
                'block': [[384, 1, 1], [320, 1, 1]][model],
                'grid': ['ceil(source_width/32),ceil(source_height/48),1',
                         'ceil(source_width/48),ceil(source_height/48),1'][model],
                'dynamic_shared_bytes': 0, 'registers': program['registers'],
                'elf_shared_bytes': program['elf_shared_bytes'],
                'code_sha256': sha256(adapted),
                'changed_instruction_pcs': program['changed_instruction_pcs']})
        manifest['architectures'][str(sm)] = entry
    assets['manifest.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode('utf-8')
    return assets


def check_destination(destination):
    require(not os.path.lexists(destination),
            "output path already exists: {}; choose a new directory".format(destination))
    require(destination.parent.is_dir(), "output parent must be an existing directory")


def publish_assets(files, destination):
    check_destination(destination)
    # All validation finishes before staging; every file is complete before the
    # final directory is published. Scratch stays under the requested parent.
    with tempfile.TemporaryDirectory(prefix=".extract-dldsr-", dir=destination.parent) as scratch:
        stage = Path(scratch) / "assets"
        stage.mkdir(mode=0o700)
        for name, data in files.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        # Claim an absent destination exclusively. A concurrent extraction or
        # an existing empty directory/symlink/file is protected by mkdir, too.
        destination.mkdir(mode=0o700)
        try:
            # Atomically replace only our own empty reservation with all assets.
            stage.rename(destination)
        except BaseException:
            try:
                destination.rmdir()
            except OSError:
                pass
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("driver", type=Path, help="user-supplied NVIDIA 621.14 nvlddmkm.sys")
    parser.add_argument("output_directory", type=Path,
                        help="new output directory, with an existing parent")
    args = parser.parse_args(argv)
    # abspath preserves the final symlink for the overwrite guard.
    destination = Path(os.path.abspath(args.output_directory))
    try:
        check_destination(destination)
        assets = make_assets(read_driver(args.driver))
        publish_assets(assets, destination)
    except (ExtractionError, OSError, KeyError, json.JSONDecodeError, struct.error) as error:
        parser.exit(1, "{}: {}\n".format(parser.prog, error))
    except KeyboardInterrupt:
        parser.exit(130, "{}: extraction interrupted\n".format(parser.prog))
    print("Generated DL-DSR Vulkan assets in {}".format(destination))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
