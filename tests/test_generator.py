import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
import zipfile
from acbrlibgenerator.catalog import Catalog
from acbrlibgenerator.build import plan, describe, execute
from acbrlibgenerator.common import Runner, sha256
from acbrlibgenerator.installer import prepare
from acbrlibgenerator.bootstrap import read_toolchain

LPI = '''<CONFIG><ProjectOptions><VersionInfo><StringTable ProductVersion="1.2.3"/></VersionInfo><BuildModes>
<Item1 Name="Win32"/><Item2 Name="Linux"><CompilerOptions><Target><Filename Value="bin/Linux/ST/libtest"/></Target><CodeGeneration><TargetCPU Value="x86_64"/><TargetOS Value="linux"/></CodeGeneration></CompilerOptions></Item2>
<Item3 Name="Linux-MT"><CompilerOptions><Target><Filename Value="bin/Linux/MT/libtest"/></Target><CodeGeneration><TargetCPU Value="x86_64"/><TargetOS Value="linux"/></CodeGeneration></CompilerOptions></Item3><Item4 Name="Debug"/>
</BuildModes></ProjectOptions><CompilerOptions><Target><Filename Value="bin/Windows/Test32"/></Target><CodeGeneration><TargetCPU Value="i386"/><TargetOS Value="win32"/></CodeGeneration></CompilerOptions></CONFIG>'''
ISS = '''#define ACBrDIR ".."
;#define MyAppTarget "x64"
#IfNDef MyAppTarget
#define MyAppTarget "x86"
#endif
#define MyAppVersion GetFileVersion(MyAppExeName)
[Setup]
LicenseFile=LICENCA.TXT
; Instalação
[Files]
Source: {#MyAppExeName}; DestDir: {app};
Source: {#ACBrDIR}\\DLLs\\test.dll; DestDir: {app};
'''

def profile(**kwargs):
    return dict(libraries='all', library_modes=['Linux', 'Linux-MT'], monitor=False, monitor_modes=[], installer=False, **kwargs)

class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='acbr-generator-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        folder = self.root/'sources/Projetos/ACBrLib/Fontes/CEP'
        folder.mkdir(parents=True)
        self.path = folder/'ACBrLibCEP.lpi'
        self.path.write_text(LPI, encoding='utf-8')
        self.catalog = Catalog.load(self.root/'sources')

    def test_inherits_default_mode_and_excludes_debug(self):
        modes = self.catalog.libraries[0].modes
        self.assertEqual(len(modes), 3)
        self.assertEqual(modes[0].filename, 'Test32.dll')
        self.assertEqual(modes[1].filename, 'libtest.so')

    def test_excludes_demo_and_console_projects(self):
        self.path.with_name('ACBrLibCEPDemo.lpi').write_text(LPI)
        self.path.with_name('ACBrLibCEPConsoleMT.lpi').write_text(LPI)
        self.assertEqual(len(Catalog.load(self.catalog.sources).libraries), 1)

    def test_legacy_encoding(self):
        self.path.write_bytes(LPI.replace('ProductVersion=', 'FileDescription="Endereços" ProductVersion=').encode('cp1252'))
        self.assertEqual(Catalog.load(self.catalog.sources).libraries[0].version,'1.2.3')

    def test_rejects_unknown_projects_and_modes(self):
        for key, value in [('libraries',['typo']),('library_modes',['typo'])]:
            cfg=profile();cfg[key]=value
            with self.assertRaises(ValueError):plan(self.catalog,cfg)

    def test_no_selection_is_error(self):
        cfg=profile();cfg['libraries']=[]
        with self.assertRaises(ValueError):plan(self.catalog,cfg)

    def test_all_selects_finalbuilder_libraries_and_reports_other_projects(self):
        for name in ['GAV', 'LCB', 'ExtratoAPI']:
            path=self.catalog.sources/f'Projetos/ACBrLib/Fontes/{name}/ACBrLib{name}.lpi'
            path.parent.mkdir(parents=True)
            path.write_text(LPI)
        catalog=Catalog.load(self.catalog.sources)
        jobs,skipped=plan(catalog,profile())
        self.assertEqual({job.project.name for job in jobs},{'ACBrLibCEP'})
        self.assertEqual(len(skipped),3)
        for name in ['GAV','LCB','ExtratoAPI']:
            cfg=profile();cfg['libraries']=[f'ACBrLib{name}']
            with self.assertRaisesRegex(ValueError,'FinalBuilder'):plan(catalog,cfg)

    def test_installer_requires_windows_monitor(self):
        cfg=profile();cfg['installer']=True
        with self.assertRaises(ValueError):plan(self.catalog,cfg)

    def test_plan_does_not_write_and_separates_st_mt(self):
        jobs,_=plan(self.catalog,profile())
        output=self.root/'preview'
        result=describe(jobs,output,['lazbuild with spaces'],self.root/'config')
        self.assertFalse(output.exists())
        self.assertNotEqual(result[0]['binary'],result[1]['binary'])
        self.assertEqual(result[0]['command'][-1],str(self.path.resolve()))

    def runner(self, behavior):
        class Fake(Runner):
            def say(self,text):
                with self.log.open('a',encoding='utf-8') as f:f.write(text+'\n')
            def run(self,args,cwd=None):
                if behavior=='fail':raise subprocess.CalledProcessError(23,args)
                if behavior=='missing':return
                options=next(a for a in args if a.startswith('--opt='))[6:]
                path=options.split(' -FU')[0][2:]
                Path(path).write_bytes(b'new artifact')
        return Fake

    def test_build_packages_checksums_and_preserves_sources(self):
        digest=sha256(self.path)
        result=execute(self.catalog,profile(),self.root,self.root/'out',runner_factory=self.runner('success'))
        report=json.loads((result/'manifest.json').read_text())
        self.assertEqual(report['status'],'success')
        for job in report['jobs']:
            archive=next(f for f in job['files'] if f['path'].endswith('.zip'))
            self.assertEqual(sha256(result/archive['path']),archive['sha256'])
            with zipfile.ZipFile(result/archive['path']) as z:
                self.assertEqual(len(z.namelist()),1)
                self.assertIn('bin/Linux/',z.namelist()[0])
        self.assertEqual(digest,sha256(self.path))

    def test_failure_stops_following_jobs_and_leaves_manifest(self):
        with self.assertRaises(subprocess.CalledProcessError):
            execute(self.catalog,profile(),self.root,self.root/'out',runner_factory=self.runner('fail'))
        report=json.loads(next((self.root/'out').glob('*/manifest.json')).read_text())
        self.assertEqual([j['status'] for j in report['jobs']],['failed','pending'])
        self.assertIn('23',report['error'])

    def test_old_binary_does_not_satisfy_new_build(self):
        self.path.with_name('libtest.so').write_bytes(b'old')
        with self.assertRaisesRegex(RuntimeError,'sem gerar'):
            execute(self.catalog,profile(),self.root,self.root/'out',runner_factory=self.runner('missing'))

    def test_revision_mismatch_stops_before_writing(self):
        with self.assertRaisesRegex(ValueError,'Revisão'):
            execute(self.catalog,profile(),self.root,self.root/'out',expected_revision='123')
        self.assertFalse((self.root/'out').exists())

    def test_output_spaces_are_rejected_before_compilation(self):
        jobs,_=plan(self.catalog,profile())
        with self.assertRaisesRegex(ValueError,'Caminho'):
            describe(jobs,self.root/'out space',['lazbuild'],self.root/'config')

class InstallerTests(unittest.TestCase):
    def test_architecture_staging_preserves_payload_and_legacy_text(self):
        original=ISS.encode('cp1252')
        for x64 in [True,False]:
            text=prepare(original,Path('C:/ACBr'),Path('C:/Monitor'),Path('C:/out/Monitor.exe'),x64).decode('utf-8-sig')
            self.assertIn('Instalação',text)
            self.assertIn('SourceDir=C:\\Monitor',text)
            self.assertIn('GetFileVersion(BuilderBinary)',text)
            self.assertIn('{#ACBrDIR}\\DLLs\\test.dll',text)
            self.assertEqual(text.count('#define MyAppTarget '),2) # includes commented upstream example
        self.assertEqual(original,ISS.encode('cp1252'))
    def test_changed_script_fails_explicitly(self):
        with self.assertRaises(ValueError):prepare(b'[Setup]',Path('a'),Path('b'),Path('c'),True)

class ProcessTests(unittest.TestCase):
    def test_stdout_stderr_and_exitcode(self):
        with tempfile.TemporaryDirectory() as root,redirect_stdout(io.StringIO()):
            log=Path(root)/'build.log'
            with self.assertRaises(subprocess.CalledProcessError) as error:
                Runner(log).run([sys.executable,'-c',"import sys;print('out');print('err',file=sys.stderr);sys.exit(17)"])
            self.assertEqual(error.exception.returncode,17)
            self.assertIn('out',log.read_text());self.assertIn('err',log.read_text())
    def test_toolchain_uses_numeric_revision_and_commit(self):
        cfg=read_toolchain(Path(__file__).resolve().parents[1]/'config/toolchain.json')
        self.assertTrue(cfg['acbr_revision'].isdigit())
        self.assertEqual(len(cfg['installers_ref']),40)

if __name__=='__main__':unittest.main()
