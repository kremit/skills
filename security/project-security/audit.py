#!/usr/bin/env python3
"""Dependency audit of local project clones and registry repos.

Finds git repos with package.json or composer.json, then runs `npm audit` or
`composer audit` against their lockfiles. Remote registry repos on GitHub are
audited from lockfiles fetched with `gh api`, without cloning.

Read only: nothing is installed, no package scripts or Composer plugins run.
The audits send dependency lists to the npm registry and Packagist.
"""

import argparse
import base64
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_ROOTS = ["~/dev/company/repo", "~/dev/projects"]
DEFAULT_REGISTRY = "~/dev/projects/meta/projects_report.csv"
SKIP_DIRS = {".git", "node_modules", "vendor", "bower_components", "dist", "build", ".cache"}
SEVERITIES = ["critical", "high", "medium", "low"]
NPM_LOCKS = ("package-lock.json", "npm-shrinkwrap.json")
OTHER_LOCKS = {"yarn.lock": "yarn", "pnpm-lock.yaml": "pnpm", "bun.lockb": "bun"}
GITHUB = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")
EXIT_CLEAN, EXIT_FINDINGS, EXIT_ERROR = 0, 3, 5


@dataclass
class Finding:
    package: str
    severity: str
    title: str
    cve: str = ""
    installed: str = ""
    affected: str = ""
    fix: str = ""
    link: str = ""


@dataclass
class Audit:
    project: str
    source: str
    manager: str
    path: str
    status: str = "ok"
    counts: dict = field(default_factory=lambda: {s: 0 for s in SEVERITIES})
    findings: list = field(default_factory=list)
    abandoned: list = field(default_factory=list)
    note: str = ""


# ----- discovery


def find_repos(root: Path, depth: int = 2) -> list[Path]:
    """Directories holding .git at depth 1 or 2 under root; repos nested in a repo are skipped."""
    found: list[Path] = []
    if not root.is_dir():
        return found

    def walk(path: Path, level: int) -> None:
        for child in sorted(path.iterdir()):
            if not child.is_dir() or child.is_symlink() or child.name in SKIP_DIRS:
                continue
            if (child / ".git").exists():
                found.append(child)
            elif level < depth:
                walk(child, level + 1)

    walk(root, 1)
    return found


def find_manifests(repo: Path, depth: int = 3) -> list[Path]:
    """Directories within a repo holding package.json or composer.json."""
    out: list[Path] = []

    def walk(path: Path, level: int) -> None:
        if (path / "package.json").is_file() or (path / "composer.json").is_file():
            out.append(path)
        if level >= depth:
            return
        for child in sorted(path.iterdir()):
            if child.is_dir() and not child.is_symlink() and child.name not in SKIP_DIRS and not child.name.startswith("."):
                walk(child, level + 1)

    walk(repo, 0)
    return out


def remote_url(repo: Path) -> str:
    done = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"], capture_output=True, text=True)
    return done.stdout.strip()


def github_slug(url: str) -> str | None:
    match = GITHUB.search(url or "")
    return f"{match[1]}/{match[2]}".lower() if match else None


def tracked(repo: Path, path: Path) -> bool:
    rel = str(path.relative_to(repo))
    done = subprocess.run(["git", "-C", str(repo), "ls-files", "--error-unmatch", rel], capture_output=True)
    return done.returncode == 0


def registry_repos(csv_path: Path) -> list[tuple[str, str]]:
    """(project id, git url) for every registry row with a repo."""
    rows = []
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            url = (row.get("git repo") or "").strip()
            if url and ("://" in url or url.startswith("git@")):
                rows.append((row.get("id") or row.get("project name") or url, url))
    return rows


# ----- audits


def parse_npm(data: dict) -> tuple[dict, list[Finding]]:
    counts = {s: 0 for s in SEVERITIES}
    meta = data.get("metadata", {}).get("vulnerabilities", {})
    for key, value in meta.items():
        sev = "medium" if key == "moderate" else key
        if sev in counts:
            counts[sev] += int(value)
    findings = []
    for name, vuln in data.get("vulnerabilities", {}).items():
        advisories = [v for v in vuln.get("via", []) if isinstance(v, dict)]
        if not advisories:
            continue  # only pulled in by another vulnerable package
        fix = vuln.get("fixAvailable")
        fix_text = "" if fix is False else f"{fix['name']}@{fix['version']}{' (major)' if fix.get('isSemVerMajor') else ''}" if isinstance(fix, dict) else "npm audit fix"
        for adv in advisories:
            sev = adv.get("severity", vuln.get("severity", ""))
            findings.append(Finding(
                package=name, severity="medium" if sev == "moderate" else sev, title=adv.get("title", ""),
                affected=adv.get("range", ""), fix=fix_text, link=adv.get("url", ""),
            ))
    return counts, findings


def composer_versions(lock: Path) -> dict:
    try:
        data = json.loads(lock.read_text())
    except (OSError, ValueError):
        return {}
    return {p["name"]: p.get("version", "") for key in ("packages", "packages-dev") for p in data.get(key, [])}


def parse_composer(data: dict, versions: dict) -> tuple[dict, list[Finding], list[str]]:
    counts = {s: 0 for s in SEVERITIES}
    findings = []
    for name, advisories in (data.get("advisories") or {}).items():
        for adv in advisories:
            sev = (adv.get("severity") or "medium").lower()
            counts[sev if sev in counts else "medium"] += 1
            findings.append(Finding(
                package=name, severity=sev, title=adv.get("title", ""), cve=adv.get("cve") or "",
                installed=versions.get(name, ""), affected=adv.get("affectedVersions", ""), link=adv.get("link", ""),
            ))
    abandoned = data.get("abandoned") or {}
    gone = [f"{k} (use {v})" if v else k for k, v in abandoned.items()] if isinstance(abandoned, dict) else []
    return counts, findings, gone


def run_json(command: list[str], cwd: Path) -> tuple[dict | None, str]:
    """Runs an audit; both tools exit nonzero when they find something, so only bad JSON is an error."""
    try:
        done = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return None, "timed out"
    start = done.stdout.find("{")
    try:
        return json.loads(done.stdout[start:]) if start >= 0 else None, done.stderr.strip()[-300:]
    except ValueError:
        return None, (done.stderr or done.stdout).strip()[-300:]


def audit_npm(audit: Audit, path: Path) -> Audit:
    if not shutil.which("npm"):
        audit.status, audit.note = "skipped", "npm not installed"
        return audit
    data, err = run_json(["npm", "audit", "--json", "--package-lock-only", "--ignore-scripts"], path)
    if data is None or "error" in data:
        audit.status = "error"
        audit.note = (data or {}).get("error", {}).get("summary", "") if data else err
        return audit
    audit.counts, audit.findings = parse_npm(data)
    return audit


def audit_composer(audit: Audit, path: Path) -> Audit:
    if not shutil.which("composer"):
        audit.status, audit.note = "skipped", "composer not installed"
        return audit
    command = ["composer", "audit", "--locked", "--format=json", "--no-interaction", "--no-plugins", "--no-scripts", "--abandoned=report"]
    data, err = run_json(command, path)
    if data is None and "No packages" in err:
        audit.note = "no packages in the lockfile"
        return audit
    if data is None:
        audit.status, audit.note = "error", err
        return audit
    audit.counts, audit.findings, audit.abandoned = parse_composer(data, composer_versions(path / "composer.lock"))
    return audit


def audit_dir(project: str, source: str, path: Path, label: str, repo: Path | None = None) -> list[Audit]:
    """One audit per package manager found in a directory."""
    results = []
    if (path / "package.json").is_file():
        audit = Audit(project, source, "npm", label)
        lock = next((path / n for n in NPM_LOCKS if (path / n).is_file()), None)
        other = next((tool for name, tool in OTHER_LOCKS.items() if (path / name).is_file()), None)
        if lock:
            results.append(audit_npm(audit, path))
            if repo and not tracked(repo, lock):
                audit.note = (audit.note + "; " if audit.note else "") + "lockfile not committed"
        elif other:
            audit.status, audit.note = "skipped", f"{other} lockfile; only npm locks are audited"
            results.append(audit)
        elif _has_dependencies(path / "package.json"):
            audit.status, audit.note = "no lock", "no package-lock.json to audit"
            results.append(audit)
    if (path / "composer.json").is_file():
        audit = Audit(project, source, "composer", label)
        lock = path / "composer.lock"
        if lock.is_file():
            results.append(audit_composer(audit, path))
            if repo and not tracked(repo, lock):
                audit.note = (audit.note + "; " if audit.note else "") + "lockfile not committed"
        else:
            audit.status, audit.note = "no lock", "no composer.lock to audit"
            results.append(audit)
    return results


def _has_dependencies(package_json: Path) -> bool:
    try:
        data = json.loads(package_json.read_text())
    except (OSError, ValueError):
        return False
    return bool(data.get("dependencies") or data.get("devDependencies"))


def audit_local(repo: Path, depth: int) -> list[Audit]:
    results = []
    for path in find_manifests(repo, depth):
        label = str(path.relative_to(repo)) if path != repo else "."
        results += audit_dir(repo.name, f"local {repo}", path, label, repo)
    return results


def gh_raw(slug: str, path: str) -> bytes | None:
    done = subprocess.run(["gh", "api", f"repos/{slug}/contents/{path}"], capture_output=True, text=True)
    if done.returncode != 0:
        return None
    try:
        return base64.b64decode(json.loads(done.stdout)["content"])
    except (ValueError, KeyError):
        return None


def audit_remote(project: str, slug: str, depth: int) -> list[Audit]:
    """Audits a GitHub repo from its committed lockfiles, without cloning it."""
    done = subprocess.run(["gh", "api", f"repos/{slug}/git/trees/HEAD?recursive=1", "--jq", ".tree[] | select(.type == \"blob\") | .path"], capture_output=True, text=True)
    if done.returncode != 0:
        return [Audit(project, f"github {slug}", "-", ".", "error", note=done.stderr.strip()[-200:])]
    wanted = {"package.json", "composer.json", "composer.lock", *NPM_LOCKS, *OTHER_LOCKS}
    files = [p for p in done.stdout.split() if Path(p).name in wanted and len(Path(p).parts) <= depth + 1
             and not SKIP_DIRS.intersection(Path(p).parts[:-1])]
    results = []
    with tempfile.TemporaryDirectory(prefix="project-security-") as tmp:
        dirs = sorted({str(Path(p).parent) for p in files})
        for rel in dirs:
            target = Path(tmp) / rel
            target.mkdir(parents=True, exist_ok=True)
            for p in files:
                if str(Path(p).parent) == rel:
                    data = gh_raw(slug, p)
                    if data is not None:
                        (target / Path(p).name).write_bytes(data)
            results += audit_dir(project, f"github {slug}", target, rel)
    return results


# ----- report


def report(audits: list[Audit], show: int) -> str:
    lines = []
    width = max([len(a.project) for a in audits] + [7])
    lines.append(f"{'PROJECT':<{width}}  {'MGR':<8} {'PATH':<18} CRIT HIGH  MED  LOW  STATUS")
    order = sorted(audits, key=lambda a: (-a.counts["critical"], -a.counts["high"], -a.counts["medium"], a.project))
    for a in order:
        c = a.counts
        status = a.status + (f" ({a.note})" if a.note else "") + (f", {len(a.abandoned)} abandoned" if a.abandoned else "")
        lines.append(f"{a.project:<{width}}  {a.manager:<8} {a.path[:18]:<18} {c['critical']:>4} {c['high']:>4} {c['medium']:>4} {c['low']:>4}  {status}")
    serious = sorted(((a, f) for a in order for f in a.findings if f.severity in ("critical", "high")), key=lambda af: SEVERITIES.index(af[1].severity))
    if serious:
        lines += ["", "CRITICAL AND HIGH"]
        for a, f in serious[:show]:
            ver = f" {f.installed}" if f.installed else ""
            fix = f"  fix: {f.fix}" if f.fix else f"  affected: {f.affected}" if f.affected else ""
            lines.append(f"- {a.project} ({a.manager}) {f.package}{ver}: {f.severity.upper()} {f.cve or ''} {f.title}{fix}".replace("  ", " ").rstrip())
        if len(serious) > show:
            lines.append(f"... {len(serious) - show} more in the JSON report")
    kinds = [a.source.split()[0] for a in audits]
    tally = ", ".join(f"{kinds.count(k)} {k}" for k in ("local", "github", "other") if k in kinds)
    lines += ["", f"{len(audits)} audits: {tally or 'nothing found'}."]
    return "\n".join(lines)


def output_dir() -> Path:
    here = Path(__file__).resolve().parent
    repo = subprocess.run(["git", "-C", str(here), "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    cwd = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    out = Path("tmp/project-security") if repo and repo == cwd else Path("project-security")
    out.mkdir(parents=True, exist_ok=True)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="npm and composer audits for local and registry projects")
    parser.add_argument("--root", action="append", help="scan root for local clones; repeatable")
    parser.add_argument("--registry", default=os.environ.get("PROJECT_REGISTRY", DEFAULT_REGISTRY), help="registry CSV for remote repos")
    parser.add_argument("--local-only", action="store_true", help="skip registry repos")
    parser.add_argument("--remote-only", action="store_true", help="skip local clones")
    parser.add_argument("--project", action="append", help="limit to these repo or project names; repeatable")
    parser.add_argument("--depth", type=int, default=3, help="manifest search depth inside a repo")
    parser.add_argument("--show", type=int, default=30, help="critical and high findings to list")
    parser.add_argument("--json", help="report path; default tmp/project-security/latest.json")
    args = parser.parse_args(argv)

    env_roots = os.environ.get("PROJECT_AUDIT_ROOTS")
    roots = args.root or (env_roots.split(":") if env_roots else DEFAULT_ROOTS)
    wanted = {p.lower() for p in args.project or []}
    pick = (lambda name: not wanted or name.lower() in wanted)
    jobs = []
    seen_slugs = set()
    if not args.remote_only:
        for root in roots:
            for repo in find_repos(Path(root).expanduser()):
                slug = github_slug(remote_url(repo))
                if slug:
                    seen_slugs.add(slug)
                if pick(repo.name):
                    jobs.append(("local", repo))
    registry = Path(args.registry).expanduser()
    if not args.local_only and registry.is_file():
        for project, url in registry_repos(registry):
            slug = github_slug(url)
            if not pick(project) and not (slug and pick(slug.split("/")[1])):
                continue
            if not slug:
                jobs.append(("note", Audit(project, f"other {url}", "-", ".", "skipped", note="not on GitHub")))
            elif slug not in seen_slugs:
                seen_slugs.add(slug)
                jobs.append(("remote", (project, slug)))

    def run(job):
        kind, item = job
        if kind == "local":
            return audit_local(item, args.depth)
        if kind == "remote":
            return audit_remote(*item, args.depth)
        return [item]

    with ThreadPoolExecutor(max_workers=4) as pool:
        audits = [a for batch in pool.map(run, jobs) for a in batch]
    print(report(audits, args.show))
    path = Path(args.json) if args.json else output_dir() / "latest.json"
    path.write_text(json.dumps([asdict(a) for a in audits], indent=2))
    print(f"JSON: {path}")
    if any(a.status == "error" for a in audits) and not any(a.findings for a in audits):
        return EXIT_ERROR
    return EXIT_FINDINGS if any(a.counts["critical"] or a.counts["high"] for a in audits) else EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
