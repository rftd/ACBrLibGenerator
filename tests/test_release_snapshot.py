import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from acbrlibgenerator.release_snapshot import freeze, main, restore


class ReleaseSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / 'dist/source-lock.json'
        self.config = {'branch': 'svn/trunk', 'svn_url': 'https://svn.example/trunk/', 'start_revision': 123}
        self.lock = {'branch': 'svn/trunk', 'commit': 'a' * 40, 'revision': '124',
                     'svn_url': 'https://svn.example/trunk'}
        self.state = {**self.lock, 'url': self.lock['svn_url']}

    def responses(self, lock=None):
        return [{'tag_name': 'v1'}, [[{'name': 'other.zip', 'id': 1}],
                                   [{'name': 'source-lock.json', 'id': 2}]],
                self.lock if lock is None else lock]

    def test_new_release_has_no_snapshot_and_does_not_write_a_lock(self):
        with patch('acbrlibgenerator.release_snapshot.gh_json',
                   side_effect=[{'tag_name': 'v1'}, [[]]]):
            self.assertFalse(restore('owner/repo', 10, 'v1', self.config, self.output))
        self.assertFalse(self.output.exists())

    def test_retry_restores_the_original_commit_instead_of_reading_the_mirror_branch(self):
        with patch('acbrlibgenerator.release_snapshot.gh_json', side_effect=self.responses()) as api:
            self.assertTrue(restore('owner/repo', 10, 'v1', self.config, self.output))
        self.assertEqual(json.loads(self.output.read_text()), self.lock)
        self.assertEqual(api.call_args.args[1], 'repos/owner/repo/releases/assets/2')

    def test_snapshot_is_uploaded_before_builds_and_never_overwritten(self):
        with patch('acbrlibgenerator.release_snapshot.gh_json', return_value={'tag_name': 'v1'}), \
             patch('acbrlibgenerator.release_snapshot.subprocess.run') as upload:
            freeze('owner/repo', 10, 'v1', self.config, self.state, self.output)
        self.assertEqual(json.loads(self.output.read_text()), self.lock)
        self.assertNotIn('--clobber', upload.call_args.args[0])
        self.assertTrue(upload.call_args.kwargs['check'])

    def test_invalid_snapshot_fails_instead_of_selecting_new_sources(self):
        for invalid in [[], {**self.lock, 'commit': None}, {**self.lock, 'revision': '122'},
                        {**self.lock, 'branch': 'main'}, {**self.lock, 'svn_url': 'https://other.example'}]:
            with self.subTest(lock=invalid), \
                 patch('acbrlibgenerator.release_snapshot.gh_json', side_effect=self.responses(invalid)):
                with self.assertRaises(ValueError):
                    restore('owner/repo', 10, 'v1', self.config, self.output)
        self.assertFalse(self.output.exists())

    def test_api_failure_does_not_count_as_a_new_release(self):
        with patch('acbrlibgenerator.release_snapshot.gh_json',
                   side_effect=subprocess.CalledProcessError(1, ['gh', 'api'])):
            with self.assertRaises(subprocess.CalledProcessError):
                restore('owner/repo', 10, 'v1', self.config, self.output)

    def test_tag_mismatch_and_failed_upload_stop_the_pipeline(self):
        with patch('acbrlibgenerator.release_snapshot.gh_json', return_value={'tag_name': 'other'}):
            with self.assertRaises(ValueError):
                freeze('owner/repo', 10, 'v1', self.config, self.state, self.output)
        with patch('acbrlibgenerator.release_snapshot.gh_json', return_value={'tag_name': 'v1'}), \
             patch('acbrlibgenerator.release_snapshot.subprocess.run',
                   side_effect=subprocess.CalledProcessError(1, ['gh', 'release', 'upload'])):
            with self.assertRaises(subprocess.CalledProcessError):
                freeze('owner/repo', 10, 'v1', self.config, self.state, self.output)

    def test_restore_cli_reports_whether_svn_sync_is_needed(self):
        config = self.root / 'mirror.json'
        config.write_text(json.dumps(self.config))
        outputs = self.root / 'github-output'
        for restored in [False, True]:
            with patch('acbrlibgenerator.release_snapshot.restore', return_value=restored), \
                 patch.dict('os.environ', {'GITHUB_OUTPUT': str(outputs)}):
                main(['restore', '--repository', 'owner/repo', '--release-id', '10', '--tag', 'v1',
                      '--config', str(config), '--output', str(self.output)])
            self.assertEqual(outputs.read_text().splitlines()[-1], f'restored={str(restored).lower()}')
