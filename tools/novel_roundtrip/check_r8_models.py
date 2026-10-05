"""Check the actual release mapping, not just unminified JVM unit tests."""
import re
import sys
from pathlib import Path

prefix = "io.legado.app.model.localBook."
models = {
    "CloudManuscript$Snapshot": "schema chapter title raw revision proof",
    "CloudManuscript$Draft": "endpoint snapshot body",
    "CloudManuscript$Update": "schema base_revision after_sha256 proof changes",
    "ManuscriptDelta$Change": "offset removed added",
}
mapping = Path(sys.argv[1]).read_text()
for model, fields in models.items():
    name = prefix + model
    match = re.search(r"^" + re.escape(name) + r" -> " + re.escape(name) + r":\n((?:[ #].*\n)*)", mapping, re.M)
    assert match, f"R8 renamed/removed JSON model: {name}"
    for field in fields.split():
        assert re.search(r"^    \S+ " + field + r" -> " + field + r"$", match[1], re.M), f"R8 renamed/removed {name}.{field}\n{match[0]}"
print("Release R8 mapping: all 4 Gson models and wire/draft fields preserved")
