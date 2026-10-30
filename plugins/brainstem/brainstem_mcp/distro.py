"""A distro: the unchanged kernel, pinned, plus a list of agent.py files, each pinned by sha256.

    {
      "schema": "brainstem-distro/1",
      "name": "rappmcp",
      "kernel": { ...a kernel pin, the same shape as brainstem_mcp/kernel.json... },
      "agents": [ {"path": "agents/wingman_wizard_agent.py", "sha256": "<64 hex>"} ]
    }

Paths are relative to the manifest's folder and stay inside it. brainstem-mcp serves only the
listed files, and only while their bytes match their pins. It never edits the kernel.

    python -m brainstem_mcp.distro check <manifest>    say which files match their pins
    python -m brainstem_mcp.distro pin <manifest>      write each listed file's current sha256
"""
import hashlib
import json
import pathlib
import sys

from .kernel import check_pin

SCHEMA = "brainstem-distro/1"


class DistroError(ValueError):
    pass


def _load(path):
    path = pathlib.Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DistroError(f"{path}: no such distro manifest")
    except ValueError as error:
        raise DistroError(f"{path}: not JSON ({error})")
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise DistroError(f"{path}: a distro manifest needs \"schema\": \"{SCHEMA}\"")
    problem = check_pin(data.get("kernel"))
    if problem:
        raise DistroError(f"{path}: {problem}")
    if not isinstance(data.get("agents"), list):
        raise DistroError(f"{path}: \"agents\" must be a list of {{path, sha256}}")
    return data


def _resolve(root, item, manifest):
    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
        raise DistroError(f"{manifest}: every agent needs a \"path\"")
    relative = pathlib.PurePosixPath(item["path"])
    if relative.is_absolute() or ".." in relative.parts or not relative.name.endswith(".py"):
        raise DistroError(f"{manifest}: {item['path']} must be a .py file inside the manifest's folder")
    return root.joinpath(*relative.parts)


def read(path):
    """Return {name, kernel, agents: [(path, sha256)], missing: [path]}. Raises DistroError."""
    path = pathlib.Path(path).resolve()
    data = _load(path)
    root = path.parent
    agents, missing = [], []
    for item in data["agents"]:
        file = _resolve(root, item, path)
        sha = item.get("sha256")
        if not (isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)):
            raise DistroError(f"{path}: {item['path']} needs a sha256 (64 lowercase hex characters)")
        if file.is_file():
            agents.append((str(file), sha))
        else:
            missing.append(str(file))
    return {"name": data.get("name"), "kernel": data["kernel"], "agents": agents, "missing": missing}


def digest(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def check(path):
    """[(relative path, ok, reason)] for every listed file."""
    path = pathlib.Path(path).resolve()
    data = _load(path)
    results = []
    for item in data["agents"]:
        file = _resolve(path.parent, item, path)
        if not file.is_file():
            results.append((item["path"], False, "missing"))
        elif digest(file) != item.get("sha256"):
            results.append((item["path"], False, f"sha256 is {digest(file)}, the manifest pins {item.get('sha256')}"))
        else:
            results.append((item["path"], True, "matches"))
    return results


def pin(path):
    """Write each listed file's current sha256 into the manifest. Returns the paths it changed."""
    path = pathlib.Path(path).resolve()
    data = _load(path)
    changed = []
    for item in data["agents"]:
        sha = digest(_resolve(path.parent, item, path))
        if item.get("sha256") != sha:
            item["sha256"] = sha
            changed.append(item["path"])
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return changed


def main(argv):
    if len(argv) != 2 or argv[0] not in ("check", "pin"):
        print(__doc__.strip().splitlines()[-2].strip(), file=sys.stderr)
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    try:
        if argv[0] == "pin":
            for item in pin(argv[1]) or ["(nothing changed)"]:
                print(f"pinned {item}")
            return 0
        results = check(argv[1])
    except DistroError as error:
        print(error, file=sys.stderr)
        return 1
    for item, ok, reason in results:
        print(f"{'ok  ' if ok else 'FAIL'} {item}: {reason}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
