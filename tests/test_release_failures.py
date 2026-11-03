import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from acbrlibgenerator.bootstrap import install_pascal
from acbrlibgenerator.container_build import execute, restore_ownership
from acbrlibgenerator.source_patches import apply_compatibility


class CrossCompilerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.environment = Path(temporary.name)
        bootstrap = self.environment / '_fpcup' / ('fpclazup.exe' if os.name == 'nt' else 'fpclazup')
        bootstrap.parent.mkdir()
        bootstrap.touch()
        self.cfg = {'fpc': 'release_3_2_2', 'lazarus': 'lazarus_4_6'}

    def test_cross_build_reuses_sources_without_rejected_download_options(self):
        runner = Mock()
        install_pascal(self.environment, Path('pascalinstall'), self.cfg, ['win32'], ['--build-only'], runner)
        native, cross = [call.args[0] for call in runner.run.call_args_list]
        self.assertEqual(native[native.index('--cross') + 1], '')
        self.assertIn('--fpc', native)
        self.assertIn('--build-only', native)
        self.assertIn('--cputarget=i386', cross)
        self.assertIn('--ostarget=win32', cross)
        self.assertIn('--only=FPCBuildOnly,LazarusBuildOnly', cross)
        self.assertFalse(any('URL=' in str(option) or 'Version=' in str(option) for option in cross))

    def test_cross_failure_stops_before_continuing(self):
        runner = Mock()
        runner.run.side_effect = [None, subprocess.CalledProcessError(13, ['fpclazup'])]
        with self.assertRaises(subprocess.CalledProcessError):
            install_pascal(self.environment, Path('pascalinstall'), self.cfg, ['win32', 'win64'], [], runner)
        self.assertEqual(runner.run.call_count, 2)

    def test_unknown_target_is_rejected_before_installing(self):
        runner = Mock()
        with self.assertRaises(ValueError):
            install_pascal(self.environment, Path('pascalinstall'), self.cfg, ['unknown'], [], runner)
        runner.run.assert_not_called()


class SourceCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        libs = self.root / 'Projetos/ACBrLib/Fontes'
        self.dis = libs / 'DIS/ACBrLibDISBase.pas'
        self.common = libs / 'Comum/ACBrLibComum.pas'
        self.dis.parent.mkdir(parents=True)
        self.common.parent.mkdir(parents=True)
        self.original = b'\xef\xbb\xbf// acentua\xe7\xe3o\r\nResult := ConverterUTF8ParaAnsi(E.Message);\r\nTexto := ConverterAnsiParaUTF8(eTexto);\r\n'
        self.dis.write_bytes(self.original)
        self.common.write_bytes(b'function TACBrLib.ConverterStringEntrada(AData: AnsiString): AnsiString;\r\nfunction TACBrLib.ConverterStringSaida(AData: AnsiString): AnsiString;\r\n')

    def test_preserves_encoding_and_records_patch_without_reapplying_it(self):
        common = self.common.read_bytes()
        result = apply_compatibility(self.root, Mock())
        updated = self.dis.read_bytes()
        self.assertEqual(updated, self.original.replace(b'ConverterUTF8ParaAnsi', b'ConverterStringSaida')
                         .replace(b'ConverterAnsiParaUTF8', b'ConverterStringEntrada'))
        self.assertEqual(len(result), 1)
        self.assertNotEqual(result[0]['original_sha256'], result[0]['patched_sha256'])
        self.assertEqual(self.common.read_bytes(), common)
        self.assertEqual(apply_compatibility(self.root, Mock()), [])

    def test_older_api_is_left_unchanged(self):
        self.common.write_bytes(b'function ConverterUTF8ParaAnsi(X: string): string;\r\nfunction ConverterAnsiParaUTF8(X: string): string;\r\n')
        self.assertEqual(apply_compatibility(self.root, Mock()), [])
        self.assertEqual(self.dis.read_bytes(), self.original)

    def test_unknown_api_fails_without_changing_sources(self):
        self.common.write_bytes(b'unit ACBrLibComum;')
        with self.assertRaises(ValueError):
            apply_compatibility(self.root, Mock())
        self.assertEqual(self.dis.read_bytes(), self.original)


class ContainerArtifactTests(unittest.TestCase):
    def test_failure_still_exports_bootstrap_log_and_restores_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output, state = root / 'artifacts', root / 'state'
            output.mkdir()
            state.mkdir()
            (state / 'bootstrap.log').write_text('diagnostic log')
            failure = subprocess.CalledProcessError(2, ['lazbuild'])
            with patch('acbrlibgenerator.container_build.subprocess.run', side_effect=failure), \
                 patch('acbrlibgenerator.container_build.restore_ownership') as ownership:
                with self.assertRaises(subprocess.CalledProcessError) as raised:
                    execute([['lazbuild'], ['never-run']], output, 1001, 1002, state)
            self.assertIs(raised.exception, failure)
            self.assertEqual((output / 'bootstrap/bootstrap.log').read_text(), 'diagnostic log')
            ownership.assert_called_once_with(output, 1001, 1002)

    def test_success_restores_private_manifests_to_runner_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            nested = output / 'build'
            nested.mkdir()
            manifest = nested / 'manifest.json'
            manifest.write_text(json.dumps({'status': 'success'}))
            manifest.chmod(0o600)
            with patch('acbrlibgenerator.container_build.subprocess.run'), \
                 patch('acbrlibgenerator.container_build.os.chown', create=True) as chown:
                execute([['lazbuild']], output, 1001, 1002)
            self.assertIn(((manifest, 1001, 1002), {'follow_symlinks': False}),
                          [(call.args, call.kwargs) for call in chown.call_args_list])

    @unittest.skipUnless(os.name == 'posix', 'symlink test requires POSIX')
    def test_ownership_does_not_follow_links_outside_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output, external = root / 'artifacts', root / 'external'
            output.mkdir()
            external.mkdir()
            secret = external / 'keep-owner'
            secret.touch()
            (output / 'link').symlink_to(external, target_is_directory=True)
            with patch('acbrlibgenerator.container_build.os.chown') as chown:
                restore_ownership(output, 1001, 1002)
            paths = [Path(call.args[0]) for call in chown.call_args_list]
            self.assertNotIn(secret, paths)
            self.assertTrue(all(call.kwargs['follow_symlinks'] is False for call in chown.call_args_list))
