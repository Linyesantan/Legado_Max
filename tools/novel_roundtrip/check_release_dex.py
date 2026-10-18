"""Inspect actual DEX models: R8 mapping omits identity-mapped fields."""
import struct
import sys
import zipfile

prefix = "Lio/legado/app/model/localBook/"
models = {
    "CloudManuscript$Snapshot": "schema chapter title raw revision proof",
    "CloudManuscript$Draft": "endpoint snapshot body",
    "CloudManuscript$Update": "schema base_revision after_sha256 proof changes",
    "ManuscriptDelta$Change": "offset removed added",
}
expected = {prefix + name + ";": set(fields.split()) for name, fields in models.items()}
found = set()


def inspect(data):
    def u32(offset):
        return struct.unpack_from("<I", data, offset)[0]

    def leb(offset):
        value = shift = 0
        while True:
            byte = data[offset]
            offset += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value, offset
            shift += 7

    strings = []
    for i in range(u32(56)):
        _, start = leb(u32(u32(60) + i * 4))
        strings.append(data[start:data.index(b"\0", start)].decode("utf-8", errors="replace"))
    types = [strings[u32(u32(68) + i * 4)] for i in range(u32(64))]
    for i in range(u32(96)):
        offset = u32(100) + i * 32
        name = types[u32(offset)]
        if name not in expected:
            continue
        assert not u32(offset + 4) & 0x600, f"Abstract/interface JSON model: {name}"
        cursor = u32(offset + 24)
        assert cursor, f"Missing class data: {name}"
        counts = []
        for _ in range(4):
            count, cursor = leb(cursor)
            counts.append(count)
        fields = set()
        methods = set()
        for group, count in enumerate(counts):
            index = 0
            for _ in range(count):
                delta, cursor = leb(cursor)
                index += delta
                _, cursor = leb(cursor)  # access_flags
                if group < 2:
                    field_name = strings[u32(u32(84) + index * 8 + 4)]
                    if group == 1:
                        fields.add(field_name)
                else:
                    _, cursor = leb(cursor)  # code_off
                    methods.add(strings[u32(u32(92) + index * 8 + 4)])
        assert expected[name] <= fields, f"Missing/renamed fields in {name}: {expected[name] - fields}"
        assert "<init>" in methods, f"Missing constructor: {name}"
        found.add(name)


with zipfile.ZipFile(sys.argv[1]) as apk:
    for entry in apk.namelist():
        if entry.startswith("classes") and entry.endswith(".dex"):
            inspect(apk.read(entry))
assert found == expected.keys(), f"Missing/renamed models: {expected.keys() - found}"
print("Release APK: all 4 concrete Gson models, constructors and JSON fields preserved")
