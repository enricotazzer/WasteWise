"""Pin the exact versions installed in the api image, split into runtime and dev sets.

Run inside the api container, whose environment is the one the test suite ran against:

    make lock        # docker compose exec -T api python scripts/freeze_requirements.py

`pyproject.toml` keeps open-ended ranges (it describes what the code supports). The two
files written here pin what was actually tested, so a production build months from now
installs those versions rather than whatever majors PyPI happens to serve that day:

  requirements.txt      runtime: the transitive closure of `[project].dependencies`
  requirements-dev.txt  `-r requirements.txt` plus what only the dev extra pulls in

The closure is computed from the installed distributions' own metadata, with environment
markers evaluated for this interpreter and the extras each requirement asks for, so a
package that only a dev tool needs (pytest's pluggy, say) never lands in the runtime pin.
"""

import re
import sys
import tomllib
from collections import deque
from datetime import date
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
SELF = "wastewise-api"


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def closure(roots: list[str]) -> dict[str, str]:
    """Canonical name -> installed version, for `roots` and everything they require."""
    found: dict[str, str] = {}
    queue = deque(Requirement(r) for r in roots)
    seen: set[tuple[str, frozenset[str]]] = set()
    while queue:
        req = queue.popleft()
        key = (canonical(req.name), frozenset(req.extras))
        if key in seen:
            continue
        seen.add(key)
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue
        try:
            dist = distribution(req.name)
        except PackageNotFoundError:
            sys.exit(f"{req.name} is required but not installed — is this the api image?")
        found[canonical(req.name)] = dist.version
        for raw in dist.requires or []:
            child = Requirement(raw)
            if child.marker is None:
                queue.append(child)
                continue
            # A conditional dependency counts when its marker holds for this interpreter
            # and for no extra, or for one of the extras the parent was asked for.
            envs = [{"extra": ""}] + [{"extra": extra} for extra in req.extras]
            if any(child.marker.evaluate(env) for env in envs):
                child.marker = None
                queue.append(child)
    found.pop(canonical(SELF), None)
    return found


def render(pins: dict[str, str], header: str, *, include: str | None = None) -> str:
    lines = [header]
    if include:
        lines.append(f"-r {include}")
    lines += [
        f"{distribution(name).metadata['Name']}=={version}"
        for name, version in sorted(pins.items())
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    runtime = closure(project["dependencies"])
    dev = closure(project["dependencies"] + project["optional-dependencies"]["dev"])
    dev_only = {name: version for name, version in dev.items() if name not in runtime}

    stamp = (
        f"# Generated {date.today().isoformat()} by `make lock` (scripts/freeze_requirements.py).\n"
        "# Dependabot may bump single pins in between; regenerate from a tested image."
    )
    (ROOT / "requirements.txt").write_text(
        render(runtime, f"{stamp}\n# Runtime pins: what the test suite ran against.")
    )
    (ROOT / "requirements-dev.txt").write_text(
        render(
            dev_only,
            f"{stamp}\n# Dev-only pins on top of the runtime set.",
            include="requirements.txt",
        )
    )
    print(
        f"requirements.txt: {len(runtime)} pins · requirements-dev.txt: +{len(dev_only)} dev-only"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
