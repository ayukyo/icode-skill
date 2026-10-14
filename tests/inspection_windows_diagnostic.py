"""Bounded platform diagnosis only; not product acceptance."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


def main():
    with tempfile.TemporaryDirectory(prefix="icode-diagnostic-") as temporary:
        raw = Path(temporary)
        canonical = raw.resolve(strict=True)
        chain = [raw, *raw.parents]
        identity = {"raw": str(raw), "canonical": str(canonical),
                    "same_object": os.path.samefile(raw, canonical),
                    "equal_path": raw == canonical,
                    "any_ancestor_reparse": any(
                        bool(getattr(path.lstat(), "st_file_attributes", 0)
                             & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)) for path in chain)}
        original = {"HOME": temporary, "PATH": os.environ.get("PATH", ""),
                    "LANG": os.environ.get("LANG", "C.UTF-8"), "PYTHONDONTWRITEBYTECODE": "1"}
        results = []
        cases = [("original_four", original)]
        if os.name == "nt" and os.environ.get("SystemRoot"):
            cases.append(("only_systemroot_added", dict(original, SystemRoot=os.environ["SystemRoot"])))
        for label, environment in cases:
            try:
                process = subprocess.run([sys.executable, "-c", "print(123)"], env=environment,
                                         capture_output=True, timeout=10)
                results.append({"case": label, "returncode": process.returncode,
                                "printed_marker": process.stdout.strip() == b"123",
                                "hash_init_failure": b"_Py_HashRandomization_Init" in process.stderr})
            except (OSError, subprocess.TimeoutExpired) as error:
                results.append({"case": label, "exception_type": type(error).__name__})
        print(json.dumps({"python": sys.version.split()[0], "platform": sys.platform,
                          "path_identity": identity, "startup": results}, ensure_ascii=True))


if __name__ == "__main__":
    main()
