"""Read Git trees or two folders without checking out or importing target code."""

from __future__ import annotations

import difflib
import hashlib
import io
import json
import os
import stat
import subprocess
import tokenize
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


GIT = ["git", "--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false"]


def _git(root, *args, data=None, optional=False):
    try:
        proc = subprocess.run([*GIT, *args], cwd=root, input=data, capture_output=True)
    except FileNotFoundError as exc:
        raise ValueError("Git 不可用，请安装 Git 或选择两个目录进行比较。") from exc
    if proc.returncode and not (optional and proc.returncode == 1):
        raise ValueError(proc.stderr.decode("utf-8", "replace").strip() or "无法读取 Git 仓库")
    return proc.stdout


def _directory(path):
    path = Path(path).resolve()
    if not path.is_dir():
        raise ValueError(f"目录不存在：{path}")
    return path


def _commit(root, ref):
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("Git 基线或目标引用为空")
    return _git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()


def _name(raw):
    # Git paths are relative to the requested root. Validate before materializing
    # Git objects which may never have been checked out on this filesystem.
    name = os.fsdecode(raw)
    parts = name.split("/")
    if (any(part in {"", ".", ".."} or part.casefold() == ".git" for part in parts)
            or Path(name).drive or Path(name).is_absolute()):
        raise ValueError(f"无法还原仓库路径：{name!r}")
    if os.name == "nt" and any(
            "\\" in p or ":" in p or p.endswith((".", " ")) or Path(p).is_reserved() for p in parts):
        raise ValueError(f"当前文件系统不支持仓库路径：{name!r}")
    return name


@dataclass(frozen=True)
class File:
    mode: str
    disk: Path | None = None
    oid: str | None = None
    link_placeholder: bool = False

    @property
    def regular(self):
        return self.mode in {"100644", "100755"}


def _tree(root, commit):
    files = {}
    for row in _git(root, "ls-tree", "-rz", commit, "--", ".").split(b"\0"):
        if row:
            header, name = row.split(b"\t", 1)
            mode, _, oid = header.decode("ascii").split()
            files[_name(name)] = File(mode, oid=oid)
    return files


def _is_link(st):
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _disk_file(path, st, mode=None):
    if _is_link(st):
        return File("120000", disk=path)
    if stat.S_ISREG(st.st_mode):
        return File(mode or ("100755" if st.st_mode & stat.S_IXUSR else "100644"), disk=path)
    return None


def _folder(root):
    files = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            if path.name.casefold() == ".git":
                continue
            st = path.lstat()
            item = _disk_file(path, st)
            if item:
                files[path.relative_to(root).as_posix()] = item
            elif stat.S_ISDIR(st.st_mode):
                pending.append(path)
            else:
                raise ValueError(f"不支持设备、套接字等特殊文件：{path.relative_to(root)}")
    return files


def _worktree(root):
    staged = {}
    for row in _git(root, "ls-files", "--stage", "-z", "--", ".").split(b"\0"):
        if row:
            header, raw = row.split(b"\t", 1)
            mode, oid, stage = header.decode("ascii").split()
            name = _name(raw)
            if stage != "0":
                raise ValueError(f"文件仍有 Git 合并冲突：{name}")
            staged[name] = (mode, oid)
    untracked = _git(root, "ls-files", "-oz", "--exclude-standard", "--", ".").split(b"\0")
    names = sorted(set(staged) | {_name(row.rstrip(b"/")) for row in untracked if row})
    trust_mode = _git(root, "config", "--bool", "--get", "core.filemode", optional=True).strip() != b"false"
    link_placeholders = _git(root, "config", "--bool", "--get", "core.symlinks", optional=True).strip() == b"false"
    files = {}
    for name in names:
        path = root / name
        # A tracked directory can have been replaced by a symlink. Do not
        # follow its old children's path outside the working tree.
        parent = path.parent
        while parent != root:
            try:
                if _is_link(parent.lstat()):
                    break
            except (FileNotFoundError, NotADirectoryError):
                break
            parent = parent.parent
        if parent != root:
            continue
        try:
            st = path.lstat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        mode, oid = staged.get(name, (None, None))
        if mode == "120000" and stat.S_ISREG(st.st_mode) and link_placeholders:
            files[name] = File(mode, disk=path, link_placeholder=True)
            continue
        if not _is_link(st) and stat.S_ISDIR(st.st_mode):
            if mode == "160000" or (path / ".git").exists():
                # Gitlinks reference a commit. Their internal dirty files are
                # outside the parent repository's file inventory.
                if (path / ".git").exists():
                    oid = _commit(path, "HEAD")
                files[name] = File("160000", oid=oid)
            continue
        file_mode = mode if mode in {"100644", "100755"} and not trust_mode else None
        item = _disk_file(path, st, file_mode)
        if item is None:
            raise ValueError(f"不支持设备、套接字等特殊文件：{name}")
        files[name] = item
    return files


@contextmanager
def _reader(root, needs_git):
    # One process serves all commit blobs, including names with spaces/newlines.
    proc = subprocess.Popen([*GIT, "cat-file", "--batch"], cwd=root, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE) if needs_git else None
    def read(file):
        if file.mode == "160000":
            return file.oid.encode("ascii")
        if file.disk is not None:
            if file.mode == "120000" and not file.link_placeholder:
                return os.fsencode(os.readlink(file.disk))
            return file.disk.read_bytes()
        proc.stdin.write(file.oid.encode("ascii") + b"\n")
        proc.stdin.flush()
        header = proc.stdout.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            raise ValueError(f"无法读取 Git 对象 {file.oid}")
        data = proc.stdout.read(int(header[2]))
        if proc.stdout.read(1) != b"\n" or len(data) != int(header[2]):
            raise ValueError("Git 对象读取不完整")
        return data
    try:
        yield read
    finally:
        if proc is not None:
            proc.stdin.close()
            proc.stdout.close()
            proc.wait()


def _text(name, raw):
    if raw is None or b"\0" in raw:
        return None
    try:
        encoding = tokenize.detect_encoding(io.BytesIO(raw).readline)[0] if name.endswith(".py") else "utf-8-sig"
        return raw.decode(encoding)
    except (UnicodeError, SyntaxError, LookupError):
        return None


def _diff(old, new):
    hunks, added, removed = [], [], []
    before, after = (v.splitlines(keepends=True) for v in (old, new))
    for group in difflib.SequenceMatcher(None, before, after).get_grouped_opcodes(n=3):
        first, last = group[0], group[-1]
        old_count, new_count = last[2] - first[1], last[4] - first[3]
        lines = []
        for tag, i, stop_i, j, stop_j in group:
            if tag == "equal":
                lines.extend(" " + s.rstrip("\r\n") for s in before[i:stop_i])
            if tag in {"replace", "delete"}:
                lines.extend("-" + s.rstrip("\r\n") for s in before[i:stop_i])
                removed.extend(range(i + 1, stop_i + 1))
            if tag in {"replace", "insert"}:
                lines.extend("+" + s.rstrip("\r\n") for s in after[j:stop_j])
                added.extend(range(j + 1, stop_j + 1))
        hunks.append({"oldStart": first[1] + bool(old_count), "oldCount": old_count,
                      "newStart": first[3] + bool(new_count), "newCount": new_count, "lines": lines})
    return hunks, added, removed


def _changes(name, old_file, new_file, old, new, normalize):
    old_mode = old_file.mode if old_file else None
    new_mode = new_file.mode if new_file else None
    old_text, new_text = _text(name, old), _text(name, new)
    binary = (old is not None and old_text is None) or (new is not None and new_text is None)
    if normalize and not binary and old_file and new_file and old_file.regular and new_file.regular:
        # Emulate Git's LF checkout normalization without running clean filters.
        new_text = new_text.replace("\r\n", "\n")
        if old_mode == new_mode and old == new.replace(b"\r\n", b"\n"):
            return None
    if old is None:
        status = "added"
    elif new is None:
        status = "deleted"
    elif old_mode[:2] != new_mode[:2]:
        status = "typechanged"
    else:
        status = "modified"
    hunks, added, removed = ([], [], []) if binary else _diff(old_text or "", new_text or "")
    return {"file": name, "status": status, "before": old_text, "after": new_text, "binary": binary,
            "beforeMode": old_mode, "afterMode": new_mode,
            "beforeHash": hashlib.sha256(old).hexdigest() if old is not None else None,
            "afterHash": hashlib.sha256(new).hexdigest() if new is not None else None,
            "hunks": hunks, "addedLines": added, "removedLines": removed}


def _lf_checkout(root, names):
    """Find paths on which Git would clean ordinary CRLF to LF."""
    if not names:
        return set()
    auto = _git(root, "config", "--get", "core.autocrlf", optional=True).strip() in (b"true", b"input")
    raw = _git(root, "check-attr", "-z", "--stdin", "text", "eol",
               data=b"\0".join(map(os.fsencode, names)) + b"\0")
    values = iter(raw.split(b"\0")[:-1])
    attrs = {}
    for name, attr, value in zip(values, values, values):
        attrs.setdefault(os.fsdecode(name), {})[attr] = value
    return {path for path, info in attrs.items() if info.get(b"text") != b"unset" and (
        auto or info.get(b"text") in {b"set", b"auto"} or info.get(b"eol") in {b"crlf", b"lf"})}


def compare(root, *, base=None, head=None, base_dir=None, exclude=()):
    """Compare all changes relative to a Git commit or an explicit before directory."""
    root = _directory(root)
    if base_dir is not None and (base is not None or head is not None):
        raise ValueError("目录基线不能与 Git 引用同时使用")
    if base_dir is not None:
        before, after = _folder(_directory(base_dir)), _folder(root)
        base_commit = head_commit = None
        base_label, target_label = "BASE_DIR", "TARGET_DIR"
    else:
        base_commit = _commit(root, "HEAD" if base is None else base)
        head_commit = _commit(root, head) if head is not None else None
        before = _tree(root, base_commit)
        after = _tree(root, head_commit) if head_commit else _worktree(root)
        base_label, target_label = base_commit, head_commit or "WORKTREE"
    for path in exclude:
        before.pop(path, None)
        after.pop(path, None)
    lf = _lf_checkout(root, sorted(before.keys() & after.keys())) if base_commit and not head_commit else set()
    changes = []
    with _reader(root, base_commit is not None) as read:
        for name in sorted(before.keys() | after.keys()):
            a, b = before.get(name), after.get(name)
            if a and b and a.oid and a.oid == b.oid and a.mode == b.mode:
                continue
            old = read(a) if a else None
            new = read(b) if b else None
            if old == new and a and b and a.mode == b.mode:
                continue
            change = _changes(name, a, b, old, new, name in lf)
            if change:
                changes.append(change)
    identity = [base_label, target_label, [[c[k] for k in ("file", "beforeMode", "beforeHash", "afterMode", "afterHash")]
                                          for c in changes]]
    fingerprint = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    return {"base": base_label, "target": target_label, "baseCommit": base_commit, "targetCommit": head_commit,
            "fingerprint": fingerprint, "changes": changes}


def materialize(root, destination, *, revision=None, directory=False, inventory=None, exclude=()):
    """Copy the selected inventory into an empty directory; return omitted links."""
    root = _directory(root)
    target = Path(destination)
    if target.exists() and (_is_link(target.lstat()) or not target.is_dir() or any(target.iterdir())):
        raise ValueError("验收副本的目标目录需要为空")
    if directory and revision is not None:
        raise ValueError("目录快照不接受 Git 引用")
    if directory:
        files = _folder(root)
    elif revision is not None:
        files = _tree(root, _commit(root, revision))
    else:
        files = _worktree(root)
    target.mkdir(parents=True, exist_ok=True)
    skipped = []
    with _reader(root, revision is not None) as read:
        for name, item in sorted(files.items()):
            if name in exclude:
                continue
            data = read(item) if item.regular or inventory is not None else None
            if inventory is not None:
                inventory.append((name, item.mode, hashlib.sha256(data).hexdigest()))
            if not item.regular:
                skipped.append(name)
                continue
            copy = target / name
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(data)
            if item.mode == "100755":
                copy.chmod(copy.stat().st_mode | 0o111)
    return skipped
