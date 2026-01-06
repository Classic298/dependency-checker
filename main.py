import re
import sys
import requests
from packaging.version import Version, InvalidVersion

PYPI_URL = "https://pypi.org/pypi/{name}/json"


def parse_requirements(path):
    """
    Very simple requirements.txt parser.
    Supports lines like:
        package==1.2.3
        package>=1.0
        package
    Ignores comments and empty lines.
    """
    deps = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            # Strip comments and whitespace
            line = line.split("#", 1)[0].strip()
            if not line:
                continue

            # Match "name and optional specifiers"
            m = re.match(r"^([A-Za-z0-9_.-]+)(\[[^\]]+\])?\s*([<>=!~].*)?$", line)
            if not m:
                # Skip lines we don't understand
                continue

            name, extras, spec = m.groups()
            deps[name] = (spec or "").strip()
    return deps


def extract_license(info):
    """
    Extract license string from PyPI JSON info.
    Prefer classifiers; fall back to 'license' field.
    """
    classifiers = [c for c in info.get("classifiers", []) if c.startswith("License ::")]
    if classifiers:
        return "; ".join(classifiers)
    return info.get("license") or "Unknown"


def extract_python_requires(info):
    """
    Extract supported Python versions for the latest release.

    Prefer the 'requires_python' field (e.g. '>=3.8'), and if that's missing,
    fall back to Python-related classifiers like
    'Programming Language :: Python :: 3.11'.
    """
    rp = info.get("requires_python")
    if rp:
        return rp

    classifiers = info.get("classifiers", []) or []
    py_classifiers = [
        c for c in classifiers
        if c.startswith("Programming Language :: Python ::")
    ]
    versions = []
    for c in py_classifiers:
        # e.g. "Programming Language :: Python :: 3.11"
        parts = [p.strip() for p in c.split("::")]
        if parts:
            last = parts[-1]
            # Filter out generic "Python" / "3" entries if possible
            if last and any(ch.isdigit() for ch in last):
                versions.append(last)

    if versions:
        # Deduplicate and sort for consistent output
        versions = sorted(set(versions))
        return ", ".join(versions)

    return "Unknown"


def main(req_file):
    deps = parse_requirements(req_file)
    deps = parse_requirements(req_file)
    outdated = []
    warnings = []

    for name, spec in deps.items():
        # Only consider pinned "==version" specs as candidates for updates.
        pinned_match = re.match(r"^==\s*([0-9A-Za-z_.+-]+)$", spec)
        if not pinned_match:
            # If it isn't strictly pinned (==), we skip it for this simple checker.
            continue

        current_str = pinned_match.group(1)

        # Query PyPI for latest version + license + python requirements
        try:
            r = requests.get(PYPI_URL.format(name=name), timeout=5)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            # On error, just report and move on
            print(f"ERROR: could not fetch {name} from PyPI: {e}", file=sys.stderr)
            continue

        info = data["info"]
        latest_str = info["version"]
        license_ = extract_license(info)
        py_req = extract_python_requires(info)

        try:
            current_v = Version(current_str)
            latest_v = Version(latest_str)
        except InvalidVersion:
            # If versions aren't parseable, skip comparison
            continue

        if latest_v > current_v:
            # This package really needs an update
            outdated.append((name, spec, latest_str, license_, py_req))
        elif current_v > latest_v:
            warnings.append(f"WARNING: {name} specified version {current_str} is NEWER than PyPI latest {latest_str}!")

    if warnings:
        print("\n" + "="*80)
        print("VERSION WARNINGS (Specified > PyPI Latest)")
        print("="*80)
        for w in warnings:
            print(w)
        print("="*80 + "\n")

    if not outdated:
        print("All pinned (==) dependencies are up to date.")
        return

    # Print only the ones that need an update
    print(f"{'Package':20} {'Specified':15} {'Latest':15} {'License':45} {'Python'}")
    print("-" * 120)
    for name, spec, latest, license_, py_req in sorted(outdated, key=lambda x: x[0].lower()):
        print(f"{name:20} {spec:15} {latest:15} {license_[:43]:45} {py_req}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python check_updates.py requirements.txt")
        sys.exit(1)
    main(sys.argv[1])
