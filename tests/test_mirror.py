from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import json
from unittest.mock import patch
from acbrlibgenerator.mirror import sync, check_blobs
from acbrlibgenerator.sources import resolve, verify
from acbrlibgenerator.build import source_info
from acbrlibgenerator.release_snapshot import freeze, restore


def run(*args):
    return subprocess.check_output(list(map(str,args)), text=True, stderr=subprocess.STDOUT).strip()


def available():
    return bool(shutil.which('svnadmin') and shutil.which('git') and (Path(run('git','--exec-path'))/'git-svn').exists())


@unittest.skipUnless(available(), 'requer SVN e git-svn')
class MirrorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mirror-test-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.svn=self.root/'svn'
        run('svnadmin','create',self.svn)
        self.url=self.svn.as_uri()+'/trunk'
        run('svn','mkdir',self.url,'-m','create trunk')
        self.wc=self.root/'wc'
        run('svn','checkout',self.url,self.wc)
        (self.wc/'hello.txt').write_text('first\n')
        run('svn','add',self.wc/'hello.txt')
        run('svn','commit',self.wc,'-m','first source')
        self.remote=self.root/'origin.git'
        run('git','init','--bare',self.remote)
        self.config={'branch':'svn/trunk','svn_url':self.url,'start_revision':2}

    def test_incremental_restores_without_cache_and_pins_sources(self):
        one=sync(self.config,str(self.remote),self.root/'cache',2,True)
        self.assertEqual(one['revision'],'2')
        (self.wc/'hello.txt').write_text('second\n')
        run('svn','commit',self.wc,'-m','second source')
        two=sync(self.config,str(self.remote),self.root/'new-cache',3,True)
        self.assertEqual(two['revision'],'3')
        self.assertNotEqual(one['commit'],two['commit'])
        self.assertEqual(run('git','--git-dir',self.remote,'show',two['commit']+':hello.txt'),'second')
        lock=resolve(str(self.remote),'svn/trunk',self.config)
        self.assertEqual(lock['commit'],two['commit'])
        checkout=self.root/'checkout'
        run('git','clone','--branch','svn/trunk',self.remote,checkout)
        verify(checkout,lock)
        provenance=source_info(checkout)
        self.assertEqual(provenance['commit'],two['commit'])
        self.assertEqual(provenance['revision'],'3')
        (checkout/'hello.txt').write_text('local adjustment')
        self.assertEqual(source_info(checkout)['modified_files'],['hello.txt'])
        lock['commit']=one['commit']
        with self.assertRaises(ValueError):verify(checkout,lock)
        self.assertEqual(run('git','--git-dir',self.remote,'for-each-ref','--format=%(refname)','refs/heads'),'refs/heads/svn/trunk')

    def test_idempotence_and_rewind_rejection(self):
        one=sync(self.config,str(self.remote),self.root/'cache',2,True)
        two=sync(self.config,str(self.remote),self.root/'cache',2,True)
        self.assertFalse(two['changed'])
        self.assertEqual(one['commit'],two['commit'])
        (self.wc/'hello.txt').write_text('next')
        run('svn','commit',self.wc,'-m','next')
        sync(self.config,str(self.remote),self.root/'cache',3,True)
        with self.assertRaises(ValueError):sync(self.config,str(self.remote),self.root/'cache',2,True)

    def test_rejects_automation_branch_and_large_blobs(self):
        with self.assertRaises(ValueError):
            sync({**self.config,'branch':'main'},str(self.remote),self.root/'cache',2)
        state=sync(self.config,str(self.remote),self.root/'cache',2)
        with self.assertRaisesRegex(ValueError,'limite'):
            check_blobs(self.root/'cache',state['commit'],limit=2)

    def test_wrong_origin_and_invalid_ref(self):
        sync(self.config,str(self.remote),self.root/'cache',2,True)
        with self.assertRaises(ValueError):
            resolve(str(self.remote),'svn/trunk',{**self.config,'svn_url':'file:///other'})
        with self.assertRaises(ValueError):resolve(str(self.remote),'--upload-pack=bad',self.config)

    def test_release_retry_keeps_snapshot_after_new_svn_commits(self):
        first=sync(self.config,str(self.remote),self.root/'cache',2,True)
        lock_file=self.root/'source-lock.json'
        with patch('acbrlibgenerator.release_snapshot.gh_json',return_value={'tag_name':'v1'}), \
             patch('acbrlibgenerator.release_snapshot.subprocess.run'):
            freeze('owner/repo',1,'v1',self.config,first,lock_file)
        original=lock_file.read_bytes()
        lock=json.loads(original)
        (self.wc/'hello.txt').write_text('new sources after first release attempt')
        run('svn','commit',self.wc,'-m','next version')
        second=sync(self.config,str(self.remote),self.root/'cache',3,True)
        self.assertNotEqual(first['commit'],second['commit'])
        lock_file.unlink()
        with patch('acbrlibgenerator.release_snapshot.gh_json',
                   side_effect=[{'tag_name':'v1'},[[{'id':2,'name':'source-lock.json'}]],lock]):
            self.assertTrue(restore('owner/repo',1,'v1',self.config,lock_file))
        restored=json.loads(lock_file.read_bytes())
        self.assertEqual(lock_file.read_bytes(),original)
        self.assertEqual(resolve(str(self.remote),restored['commit'],self.config),lock)
        self.assertEqual(restored['revision'],'2')
