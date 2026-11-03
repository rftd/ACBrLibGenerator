"""Regression for runners with a Git PPA newer than Ubuntu's git-svn."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'posix', 'script APT executado somente no Linux')
class GitSvnSetupTests(unittest.TestCase):
    def execute(self, candidate, install_exit=0):
        with tempfile.TemporaryDirectory(prefix='git-svn-setup-') as temporary:
            folder = Path(temporary)
            log = folder / 'calls.jsonl'
            for name, body in {
                'sudo': 'exec "$@"',
                'apt-cache': 'printf "git-svn:\\n  Installed: (none)\\n  Candidate: %s\\n" "$CANDIDATE"',
                'apt-get': '''python3 -c 'import json,os,sys; open(os.environ["CALLS"],"a").write(json.dumps(sys.argv[1:])+"\\n")' "$@"
if [ "$1" = install ]; then exit "$INSTALL_EXIT"; fi''',
                'git': 'printf "git %s\\n" "$*"',
                'svn': 'printf "1.14.1\\n"',
            }.items():
                path = folder / name
                path.write_text('#!/bin/sh\n' + body + '\n')
                path.chmod(0o755)
            environment = {**os.environ, 'PATH': f'{folder}{os.pathsep}{os.environ["PATH"]}',
                           'CANDIDATE': candidate, 'CALLS': str(log), 'INSTALL_EXIT': str(install_exit)}
            script = Path(__file__).resolve().parents[1] / 'scripts/install_git_svn.sh'
            result = subprocess.run(['bash', str(script)], env=environment, text=True, capture_output=True)
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, calls

    def test_uses_same_candidate_and_allows_downgrade_from_ppa_git(self):
        version = '1:2.34.1-1ubuntu1.15'
        result, calls = self.execute(version)
        self.assertEqual(result.returncode, 0, result.stderr)
        install = next(call for call in calls if call[0] == 'install')
        self.assertIn('--allow-downgrades', install)
        for package in ['git', 'git-man', 'git-svn']:
            self.assertIn(f'{package}={version}', install)
        self.assertIn('git svn --version', result.stdout)

    def test_missing_candidate_fails_without_install(self):
        result, calls = self.execute('(none)')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Nenhuma versão', result.stderr)
        self.assertFalse(any(call[0] == 'install' for call in calls))

    def test_install_failure_is_not_reported_as_success(self):
        result, _ = self.execute('1:2.34.1-1ubuntu1.15', install_exit=100)
        self.assertEqual(result.returncode, 100)
        self.assertNotIn('git svn --version', result.stdout)
