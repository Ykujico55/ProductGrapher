"""Run bundled PyCG against the supplied source tree in an isolated interpreter."""

import json
from pathlib import Path
import sys


def main():
    # The parent starts this file with -I -S. Only this subprocess exposes the
    # upstream package's absolute "pycg" imports at the top level.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor" / "pycg.zip"))
    from pycg.pycg import CallGraphGenerator

    request = json.loads(sys.stdin.buffer.read())
    root = Path(request["root"])
    generator = CallGraphGenerator([str(root / name) for name in request["files"]], str(root))
    generator.analyze()
    # ASCII is a valid UTF-8 representation of this JSON even on Windows.
    json.dump(generator.document(), sys.stdout, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


if __name__ == "__main__":
    main()
