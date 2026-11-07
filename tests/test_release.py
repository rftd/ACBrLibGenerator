import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from acbrlibgenerator.common import sha256
from acbrlibgenerator.release import collect
from acbrlibgenerator.versioning import file_version,project_file_version
from acbrlibgenerator.catalog import Catalog
from acbrlibgenerator.build import plan, describe, Job


def pe(wide=False):
    d=bytearray(0x600);d[:2]=b'MZ';struct.pack_into('<I',d,0x3c,0x80);d[0x80:0x84]=b'PE\0\0'
    struct.pack_into('<H',d,0x86,1);size=0xf0 if wide else 0xe0;struct.pack_into('<H',d,0x94,size)
    struct.pack_into('<H',d,0x98,0x20b if wide else 0x10b)
    struct.pack_into('<II',d,0x98+(112 if wide else 96)+16,0x1000,0x400)
    struct.pack_into('<IIII',d,0x98+size+8,0x400,0x1000,0x400,0x200)
    for offset,identifier,target in [(0,16,0x80000020),(0x20,1,0x80000040),(0x40,0x409,0x60)]:
        struct.pack_into('<HH',d,0x200+offset+12,0,1);struct.pack_into('<II',d,0x200+offset+16,identifier,target)
    struct.pack_into('<II',d,0x260,0x1100,92);struct.pack_into('<HHH',d,0x300,92,52,0)
    key='VS_VERSION_INFO\0'.encode('utf-16le');d[0x306:0x306+len(key)]=key
    struct.pack_into('<IIIIII',d,0x328,0xfeef04bd,0x10000,(1<<16)|2,(3<<16)|4,99<<16,88<<16)
    return bytes(d)


class VersionTests(unittest.TestCase):
    def test_reads_file_version_not_product_version_for_pe32_and_pe64(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'lib.dll'
            for wide in [False,True]:
                path.write_bytes(pe(wide));self.assertEqual(file_version(path),'1.2.3.4')
            path.write_bytes(b'MZ')
            with self.assertRaises(ValueError):file_version(path)

    def test_legacy_windows_modes_get_explicit_target(self):
        with tempfile.TemporaryDirectory() as root:
            source=Path(root);folder=source/'Projetos/ACBrLib/Fontes/GAV';folder.mkdir(parents=True)
            (folder/'ACBrLibGAV.lpi').write_text('<CONFIG><ProjectOptions><BuildModes><Item1 Name="x86 - CDECL"/></BuildModes></ProjectOptions><CompilerOptions><Target><Filename Value="bin/Cdecl/ACBrGAV32"/></Target><CodeGeneration><TargetCPU Value="i386"/></CodeGeneration></CompilerOptions></CONFIG>')
            catalog=Catalog.load(source)
            cfg={'libraries':'all','library_modes':['x86 - CDECL'],'monitor':False,'monitor_modes':[],'installer':False}
            project=catalog.libraries[0]
            jobs=[Job(project, project.modes[0], False)]
            args=describe(jobs,source/'out',['lazbuild'],source/'config')[0]['command']
            self.assertIn('--os=win32',args)
            self.assertIn('--cpu=i386',args)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.sources=self.root/'sources';self.artifacts=self.root/'artifacts';self.templates=self.root/'templates';self.output=self.root/'publish'
        self.lock={'commit':'a'*40,'revision':'123','svn_url':'https://svn.example/trunk'}
        lib=self.sources/'Projetos/ACBrLib/Fontes/CEP';lib.mkdir(parents=True)
        (lib/'ACBrLibCEP.lpi').write_text('''<CONFIG><ProjectOptions><VersionInfo><MajorVersionNr Value="1"/><MinorVersionNr Value="2"/><RevisionNr Value="3"/><BuildNr Value="4"/><StringTable ProductVersion="99.88.77"/></VersionInfo><BuildModes><Item1 Name="Windows"/></BuildModes></ProjectOptions><CompilerOptions><Target><Filename Value="bin/Windows/ST/Cdecl/ACBrCEP32"/></Target><CodeGeneration><TargetCPU Value="i386"/><TargetOS Value="win32"/></CodeGeneration></CompilerOptions></CONFIG>''')
        for rel,text in [('Projetos/ACBrLib/Fontes/CEP/ACBrLibCEP-change-log.txt','lib log'),('Projetos/ACBrLib/Fontes/Comum/ACBrLibComum-change-log.txt','common log'),('Fontes/service.ini','new ini'),('schemas/current.xsd','schema')]:
            p=self.sources/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
        for rel,text in [('LEIA-ME.pdf','pdf'),('dep/OpenSSL/vendor.dll','vendor'),('dep/service.ini','old ini'),('dep/Schemas/old.xsd','old schema'),('bin/old.dll','old binary'),('log/old.txt','old log'),('Build.bat','script')]:
            p=self.templates/'ACBrLibCEP'/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
        self.recipes={'ACBrLibCEP':{'files':{'dep/service.ini':'Fontes/service.ini'},'directories':{'dep/Schemas':'schemas'}}};self.reports=[]
        for platform,modes in [('windows',[('ST','bin/Windows/ST/Cdecl/ACBrCEP32.dll'),('MT','bin/Windows/MT/Cdecl/ACBrCEP32.dll')]),('linux',[('Linux','bin/Linux/ST/libacbrcep64.so')])]:
            directory=self.artifacts/platform;directory.mkdir(parents=True);jobs=[]
            for mode,member in modes:
                z=directory/f'{mode}.zip'
                with zipfile.ZipFile(z,'w') as stream:stream.writestr(member,pe() if platform=='windows' else b'ELF')
                jobs.append({'project':'ACBrLibCEP','mode':mode,'target':'i386-win32' if platform=='windows' else 'x86_64-linux','status':'success','library':True,'installer':False,'file_version':'1.2.3.4','version_source':'binary' if platform=='windows' else 'project','archive_path':member,'files':[{'path':z.name,'sha256':sha256(z)}]})
            if platform=='windows':
                for arch,target in [('x86','i386-win32'),('x64','x86_64-win64')]:
                    p=directory/f'ACBrMonitorPLUS-1.4.0.9-{arch}-Instalador.exe';p.write_bytes(b'installer')
                    jobs.append({'project':'ACBrMonitor','mode':arch,'target':target,'status':'success','library':False,'installer':True,'files':[{'path':p.name,'sha256':sha256(p)}]})
            else:jobs.append({'project':'ACBrMonitor','mode':'Linux','target':'x86_64-linux','status':'success','library':False,'installer':False,'files':[]})
            report={'status':'success','source':{'commit':'a'*40,'revision':'123'},'jobs':jobs};p=directory/'manifest.json';p.write_text(json.dumps(report));self.reports.append((p,report))

    def collect(self):
        with patch('acbrlibgenerator.release.verify'), patch('acbrlibgenerator.release.release_libraries',return_value=('ACBrLibCEP',)):
            return collect(self.artifacts,self.sources,self.lock,self.output,self.templates,self.recipes)

    def test_finalbuilder_bundle_name_layout_and_fresh_standard_files(self):
        self.collect();package=self.output/'ACBrLibCEP 1.2.3.4.zip';self.assertTrue(package.is_file())
        with zipfile.ZipFile(package) as z:
            names=z.namelist()
            for name in ['bin/Windows/ST/Cdecl/ACBrCEP32.dll','bin/Windows/MT/Cdecl/ACBrCEP32.dll','bin/Linux/ST/libacbrcep64.so','LEIA-ME.pdf','log/ACBrLibComum-change-log.txt','dep/Schemas/current.xsd']:
                self.assertIn(name,names)
            self.assertEqual(z.read('dep/service.ini'),b'new ini')
            for name in ['dep/Schemas/old.xsd','Build.bat','bin/old.dll','log/old.txt']:self.assertNotIn(name,names)
        self.assertEqual(len(list(self.output.glob('*-Instalador.exe'))),2)
        self.assertFalse(any('Linux' in p.name for p in self.output.iterdir()))
        lib=Catalog.load(self.sources).libraries[0];self.assertEqual(project_file_version(lib),'1.2.3.4');self.assertEqual(lib.version,'99.88.77')

    def test_revisions_cannot_be_mixed(self):
        p,r=self.reports[1];r['source']['commit']='b'*40;p.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError,'revisões diferentes'):self.collect()

    def test_release_rejects_libraries_outside_finalbuilder(self):
        path,report=self.reports[0]
        report['jobs'].append({**report['jobs'][0],'project':'ACBrLibGAV'})
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError,'inesperadas'):self.collect()

    def test_release_requires_the_entire_finalbuilder_selection(self):
        with patch('acbrlibgenerator.release.verify'):
            with self.assertRaisesRegex(ValueError,'Bibliotecas ausentes'):
                collect(self.artifacts,self.sources,self.lock,self.output,self.templates,self.recipes)

    def test_both_monitor_installers_are_required(self):
        p,r=self.reports[0];r['jobs'].pop();p.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError,'instaladores x86 e x64'):self.collect()

    def test_corrupt_archives_are_rejected(self):
        (self.artifacts/'windows/ST.zip').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'Checksum'):self.collect()

    def test_conflicting_windows_versions_are_rejected(self):
        p,r=self.reports[0];r['jobs'][1]['file_version']='9.9.9.9';p.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError,'inconsistente'):self.collect()


class WorkflowPolicyTests(unittest.TestCase):
    def test_only_manual_and_release_triggers(self):
        root=Path(__file__).resolve().parents[1]
        for path in (root/'.github/workflows').glob('*.yml'):
            triggers=path.read_text().split('permissions:',1)[0];self.assertIn('  workflow_dispatch:',triggers)
            for event in ['push','pull_request','schedule']:self.assertNotIn(f'\n  {event}:',triggers,path.name)
