"""Render the acceptance report as a self-contained, offline HTML document."""

import json
import re
from importlib.resources import files


def render_report(report: dict) -> bytes:
    """Embed the report, including both sides of its displayed diff hunks."""
    assets = files("how2trust").joinpath("assets")
    data = dict(report)
    change_fields = ("file", "status", "binary", "hunks", "beforeHash", "afterHash", "beforeMode", "afterMode")
    data["changes"] = [{key: change[key] for key in change_fields if key in change}
                       for change in report.get("changes", [])]
    payload = json.dumps(data, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    inserts = {
        "CSS": assets.joinpath("review.css").read_text(encoding="utf-8"),
        "JS": assets.joinpath("review.js").read_text(encoding="utf-8"),
        "DATA": payload,
    }
    template = assets.joinpath("review.html").read_text(encoding="utf-8")
    return re.sub(r"@@(CSS|JS|DATA)@@", lambda match: inserts[match[1]], template).encode("utf-8")
