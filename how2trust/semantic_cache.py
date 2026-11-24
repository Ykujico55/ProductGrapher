"""Local, content-addressed storage for successful semantic model replies."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .project import canonical, digest


class SemanticRequestCache:
    def __init__(self, root, repository, client, requirements, version):
        profile = [version, str(repository or ""), client.provider, client.model,
                   getattr(client, "url", ""), getattr(client, "protocol", ""),
                   getattr(client, "max_tokens", None), getattr(client, "json_mode", False),
                   requirements]
        self.root = Path(root) / "requests-v1" / digest(profile)

    def path(self, instructions, payload):
        key = digest([instructions, payload])
        return self.root / key[:2] / (key + ".json")

    @staticmethod
    def load(path):
        try:
            record = json.loads(path.read_bytes())
            data = record["reply"]
            if isinstance(data, dict) and record["fingerprint"] == digest([path.stem, data]):
                return data
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    @staticmethod
    def save(path, data):
        temp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {"reply": data, "fingerprint": digest([path.stem, data])}
            with NamedTemporaryFile(dir=path.parent, delete=False) as out:
                temp = Path(out.name)
                out.write(canonical(record).encode("utf-8"))
            os.replace(temp, path)
        except OSError:
            pass  # A validated reply remains usable when the optional cache is unavailable.
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
