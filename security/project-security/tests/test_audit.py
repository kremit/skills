import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import audit

NPM = {
    "vulnerabilities": {
        "proxy-addr": {"severity": "critical", "via": [{"title": "IP spoofing", "severity": "critical", "range": "<2.0.8", "url": "https://x/1"}], "fixAvailable": True},
        "send": {"severity": "moderate", "via": ["proxy-addr"], "fixAvailable": {"name": "express", "version": "5.0.0", "isSemVerMajor": True}},
        "vue": {"severity": "high", "via": [{"title": "XSS", "severity": "high", "range": "<3.5"}], "fixAvailable": {"name": "vue", "version": "3.5.1", "isSemVerMajor": False}},
    },
    "metadata": {"vulnerabilities": {"info": 0, "low": 1, "moderate": 2, "high": 1, "critical": 1, "total": 5}},
}

COMPOSER = {
    "advisories": {
        "mtdowling/jmespath.php": [{"title": "Code injection", "cve": "CVE-2026-54133", "severity": "critical", "affectedVersions": "<2.9.1", "link": "https://x/2"}],
        "guzzlehttp/guzzle": [{"title": "Host bypass", "cve": None, "severity": "high", "affectedVersions": "<7.15.2"}, {"title": "Cookie", "severity": "medium"}],
    },
    "abandoned": {"old/pkg": "new/pkg", "gone/pkg": None},
}


class Parse(unittest.TestCase):
    def test_npm_counts_and_direct_advisories(self):
        counts, findings = audit.parse_npm(NPM)
        self.assertEqual(counts, {"critical": 1, "high": 1, "medium": 2, "low": 1})
        self.assertEqual([f.package for f in findings], ["proxy-addr", "vue"])
        self.assertEqual(findings[1].fix, "vue@3.5.1")
        self.assertEqual(findings[0].fix, "npm audit fix")

    def test_composer_counts_versions_and_abandoned(self):
        counts, findings, gone = audit.parse_composer(COMPOSER, {"guzzlehttp/guzzle": "7.8.1"})
        self.assertEqual(counts, {"critical": 1, "high": 1, "medium": 1, "low": 0})
        self.assertEqual(findings[1].installed, "7.8.1")
        self.assertEqual(findings[0].cve, "CVE-2026-54133")
        self.assertEqual(gone, ["old/pkg (use new/pkg)", "gone/pkg"])

    def test_composer_abandoned_as_empty_list(self):
        self.assertEqual(audit.parse_composer({"advisories": [], "abandoned": []}, {})[2], [])


class Discovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def repo(self, rel):
        path = self.root / rel
        (path / ".git").mkdir(parents=True)
        return path

    def test_finds_repos_at_depth_two_and_skips_nested(self):
        a = self.repo("a")
        b = self.repo("group/b")
        self.repo("a/nested")
        self.repo("x/y/too-deep")
        self.assertEqual(audit.find_repos(self.root), [a, b])

    def test_manifests_skip_vendor_and_node_modules(self):
        r = self.repo("app")
        (r / "composer.json").write_text("{}")
        (r / "web").mkdir()
        (r / "web/package.json").write_text("{}")
        (r / "vendor/lib").mkdir(parents=True)
        (r / "vendor/lib/composer.json").write_text("{}")
        (r / "node_modules/x").mkdir(parents=True)
        (r / "node_modules/x/package.json").write_text("{}")
        self.assertEqual(audit.find_manifests(r), [r, r / "web"])

    def test_missing_lock_and_other_tools(self):
        d = self.root / "d"
        d.mkdir()
        (d / "package.json").write_text(json.dumps({"dependencies": {"a": "1"}}))
        (d / "composer.json").write_text("{}")
        found = {a.manager: a for a in audit.audit_dir("p", "local", d, ".")}
        self.assertEqual((found["npm"].status, found["composer"].status), ("no lock", "no lock"))
        (d / "yarn.lock").write_text("")
        self.assertEqual(audit.audit_dir("p", "local", d, ".")[0].note, "yarn lockfile; only npm locks are audited")

    def test_package_json_without_dependencies_is_ignored(self):
        d = self.root / "e"
        d.mkdir()
        (d / "package.json").write_text("{}")
        self.assertEqual(audit.audit_dir("p", "local", d, "."), [])


class Registry(unittest.TestCase):
    def test_slugs(self):
        self.assertEqual(audit.github_slug("git@github.com:Acme/My-App.git"), "acme/my-app")
        self.assertEqual(audit.github_slug("https://github.com/Acme/web.git"), "acme/web")
        self.assertIsNone(audit.github_slug("git@gitlab.example.edu:group/sub/x.git"))

    def test_rows_with_repos_only(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("project name,id,git repo\nA,a,\nB,b,git@github.com:o/b.git\nC,c,(none: new project)\nD,d,https://gitlab.example.edu/x/y.git\n")
        self.addCleanup(Path(f.name).unlink)
        self.assertEqual(audit.registry_repos(Path(f.name)), [("b", "git@github.com:o/b.git"), ("d", "https://gitlab.example.edu/x/y.git")])


class Commands(unittest.TestCase):
    def test_audits_never_install_or_run_scripts(self):
        seen = []

        def fake(command, **kwargs):
            seen.append(command)
            return subprocess.CompletedProcess(command, 1, stdout="{}", stderr="")

        with mock.patch.object(audit.subprocess, "run", side_effect=fake), mock.patch.object(audit.shutil, "which", return_value="/usr/bin/x"):
            audit.audit_npm(audit.Audit("p", "local", "npm", "."), Path("."))
            audit.audit_composer(audit.Audit("p", "local", "composer", "."), Path("."))
        self.assertIn("--package-lock-only", seen[0])
        self.assertIn("--ignore-scripts", seen[0])
        self.assertTrue({"--locked", "--no-plugins", "--no-scripts"} <= set(seen[1]))

    def test_empty_composer_lock_is_not_an_error(self):
        done = subprocess.CompletedProcess([], 0, stdout="No packages - skipping audit.", stderr="No packages - skipping audit.")
        with mock.patch.object(audit.subprocess, "run", return_value=done), mock.patch.object(audit.shutil, "which", return_value="/usr/bin/composer"):
            result = audit.audit_composer(audit.Audit("p", "local", "composer", "."), Path("."))
        self.assertEqual((result.status, result.note), ("ok", "no packages in the lockfile"))


if __name__ == "__main__":
    unittest.main()
