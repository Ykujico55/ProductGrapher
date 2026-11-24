"""Identify How2Trust exports that can be regenerated inside a repository."""

import json


def export_data(text):
    source = text.lstrip()
    if source.startswith("{"):
        if not any(marker in source for marker in ('"snapshot"', '"how2trust.review.v1"', '"how2trust.claim-drift.v1"')):
            return None
        payload = source
    elif source.startswith("<!doctype html>"):
        if '<meta name="generator" content="How2Trust Review">' in source[:1024]:
            marker, ending = '<script type="application/json" id="report-data">', '</script>'
        elif '<title>How2Trust · 仓库架构工作台</title>' in source[:1024]:
            marker, ending = '<script>window.H2T_GRAPH=', ';</script>'
        else:
            return None
        start = source.find(marker)
        if start < 0:
            return None
        start += len(marker)
        end = source.find(ending, start)
        if end < 0:
            return None
        payload = source[start:end]
    else:
        return None
    try:
        return json.loads(payload)
    except (ValueError, RecursionError):
        return None


def export_kind(text):
    data = export_data(text)
    if isinstance(data, dict):
        if data.get("schema") == "how2trust.semantics.v1" and {"snapshot", "overview", "items", "fingerprint"} <= data.keys():
            return "semantics"
        if data.get("schema") == "how2trust.review.v1" and "changes" in data:
            return "review"
        if data.get("schema") == "how2trust.claim-drift.v1" and {"previousSnapshot", "currentSnapshot", "claims"} <= data.keys():
            return "claim-drift"
        if data.get("schema") == 1 and {"snapshot", "tree", "nodes", "edges", "files"} <= data.keys():
            return "graph"
    return None


def existing_export(path):
    try:
        return export_kind(path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError:
        return None
