import re
import sys
import requests
import json
import os
from packaging.version import Version, InvalidVersion

PYPI_URL = "https://pypi.org/pypi/{name}/json"
NPM_REGISTRY_URL = "https://registry.npmjs.org/{name}"


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


def parse_package_json(path):
    """
    Parses package.json for dependencies and devDependencies.
    """
    deps = {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
        all_deps = data.get("dependencies", {})
        all_deps.update(data.get("devDependencies", {}))
        for name, spec in all_deps.items():
            deps[name] = spec
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
        parts = [p.strip() for p in c.split("::")]
        if parts:
            last = parts[-1]
            if last and any(ch.isdigit() for ch in last):
                versions.append(last)

    if versions:
        versions = sorted(set(versions))
        return ", ".join(versions)

    return "Unknown"


def check_pypi_updates(name, spec):
    # Only consider pinned "==version" specs as candidates for updates or handle them.
    # The original script skipped non-pinned ones. I'll stick to that or improve it.
    pinned_match = re.match(r"^==\s*([0-9A-Za-z_.+-]+)$", spec)
    if not pinned_match:
        # For requirements.txt, if it's not pinned, we might still want to know.
        # But per original script:
        return None, None, None, None, None

    current_str = pinned_match.group(1)
    try:
        r = requests.get(PYPI_URL.format(name=name), timeout=5)
        r.raise_for_status()
        data = r.json()
        info = data["info"]
        latest_str = info["version"]
        license_ = extract_license(info)
        py_req = extract_python_requires(info)
        return current_str, latest_str, license_, py_req, None
    except Exception as e:
        return None, None, None, None, f"ERROR: {e}"


def check_npm_updates(name, spec):
    # NPM specs are often ^1.2.3 or ~1.2.3
    # Extract the version part. Simple regex to get the digits if it starts with ^ or ~
    current_str = spec
    v_match = re.search(r"(\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?)$", spec)
    if v_match:
        current_str = v_match.group(1)
    else:
        # If we can't find a version, return
        return None, None, None, None, None

    try:
        r = requests.get(NPM_REGISTRY_URL.format(name=name), timeout=5)
        r.raise_for_status()
        data = r.json()
        latest_str = data.get("dist-tags", {}).get("latest")
        if not latest_str:
            return None, None, None, None, "ERROR: no latest tag"
        
        # Get license from the latest version info
        latest_info = data.get("versions", {}).get(latest_str, {})
        license_ = latest_info.get("license") or data.get("license") or "Unknown"
        if isinstance(license_, dict):
            license_ = license_.get("type", "Unknown")
        
        engines = latest_info.get("engines", {})
        node_req = engines.get("node", "Unknown")
        
        return current_str, latest_str, license_, node_req, None
    except Exception as e:
        return None, None, None, None, f"ERROR: {e}"


def main(file_path):
    is_npm = os.path.basename(file_path) == "package.json"
    
    if is_npm:
        deps = parse_package_json(file_path)
    else:
        deps = parse_requirements(file_path)

    outdated = []
    warnings = []

    for name, spec in deps.items():
        if is_npm:
            current_str, latest_str, license_, env_req, err = check_npm_updates(name, spec)
        else:
            current_str, latest_str, license_, env_req, err = check_pypi_updates(name, spec)

        if err:
            print(f"ERROR: could not fetch {name}: {err}", file=sys.stderr)
            continue
        
        if not current_str or not latest_str:
            continue

        try:
            # Clean version for comparison (remove leading v if any)
            c_v_p = current_str.lstrip('v')
            l_v_p = latest_str.lstrip('v')
            current_v = Version(c_v_p)
            latest_v = Version(l_v_p)
        except InvalidVersion:
            continue

        if latest_v > current_v:
            outdated.append((name, spec, latest_str, license_, env_req))
        elif current_v > latest_v:
            warnings.append(f"WARNING: {name} specified version {current_str} is NEWER than latest {latest_str}!")

    if warnings:
        print("\n" + "="*80)
        print(f"VERSION WARNINGS (Specified > Latest)")
        print("="*80)
        for w in warnings:
            print(w)
        print("="*80 + "\n")

    if not outdated:
        print(f"All dependencies in {file_path} are up to date.")
        return

    # Print only the ones that need an update
    env_label = "Node" if is_npm else "Python"
    print(f"{'Package':20} {'Specified':15} {'Latest':15} {'License':45} {env_label}")
    print("-" * 120)
    for name, spec, latest, license_, env_req in sorted(outdated, key=lambda x: x[0].lower()):
        print(f"{name:20} {spec:15} {latest:15} {license_[:43]:45} {env_req}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python script.py <requirements.txt | package.json>")
        sys.exit(1)
    main(sys.argv[1])
