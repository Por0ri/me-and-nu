"""Opt-in, credential-free Linux mount regression for the isolated RAG copy.

Set MENU_RAG_RUNTIME_DOCKER_IMAGE to an already built sha256 image ID. Tests use
only their own temporary volume, network=none, and synthetic dictionary rows.
No existing container, host directory, provider, or database is accessed.
"""
import json
import os
import subprocess
import unittest
from uuid import uuid4


IMAGE = os.environ.get("MENU_RAG_RUNTIME_DOCKER_IMAGE", "")
DOCKER = ["docker", "--context", "default"]
FIXTURE = """
import os, sqlite3
from pathlib import Path
root=Path('/isolated-rag'); path=root/'facts.sqlite3'
with sqlite3.connect(path) as db:
    db.execute('PRAGMA journal_mode=DELETE')
    db.executescript('''
      CREATE TABLE rag_source(source_id TEXT PRIMARY KEY);
      CREATE TABLE rag_document(document_id TEXT PRIMARY KEY,source_id TEXT,canonical_url TEXT,
        content_hash TEXT,title TEXT,content_type TEXT,parse_status TEXT,evidence_type TEXT,raw_text TEXT);
      CREATE TABLE rag_work_link(document_id TEXT,menu_work_id INTEGER,status TEXT);
      CREATE TABLE rag_document_content_type(document_id TEXT,content_type TEXT);
      CREATE TABLE rag_draft_evidence(document_id TEXT);
      INSERT INTO rag_source VALUES ('test_reference');
    ''')
    for n in range(3):
        key=f'doc:{n}'
        db.execute('INSERT INTO rag_document VALUES (?,?,?,?,?,?,?,?,?)',
          (key,'test_reference',f'https://example.invalid/{n}','a'*64,
           f'Synthetic character {n}','character','parsed','reference_fact','Synthetic fact'))
        db.execute('INSERT INTO rag_work_link VALUES (?,?,?)',(key,8,'matched'))
os.chmod(path,0o640); os.chown(path,10001,10001)
os.chmod(root,0o750); os.chown(root,10001,10001)
"""
READER = """
import hashlib,json,os,sqlite3
from pathlib import Path
from menu_worker.rag_candidates import DictionaryCandidates
root=Path('/isolated-rag'); path=root/'facts.sqlite3'
assert os.getuid()==10001
before=hashlib.sha256(path.read_bytes()).hexdigest()
with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
    assert db.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
try:
    (root/'forbidden-write').write_text('must fail')
except OSError:
    pass
else:
    raise AssertionError('controller mount is writable')
rows=DictionaryCandidates(path,catalog_work_ids={8})(
    {'namespace':'dictionary','menu_work_id':8,'content_type':'character',
     'allowed_source_ids':['test_reference']})
assert hashlib.sha256(path.read_bytes()).hexdigest()==before
assert not (root/'forbidden-write').exists()
print(json.dumps({'documents':[seed['document_id'] for row in rows for seed in row['seed_evidence']],
                  'uid':os.getuid(),'journal_mode':'delete','reader_writes':0}))
"""


@unittest.skipUnless(IMAGE, "set MENU_RAG_RUNTIME_DOCKER_IMAGE for isolated Docker regression")
class RagReadonlyRuntimeTests(unittest.TestCase):
    def docker(self, *args, data=None):
        result = subprocess.run([*DOCKER, *args], input=data, capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        return result.stdout

    def setUp(self):
        self.assertRegex(IMAGE, r"^sha256:[0-9a-f]{64}$")
        row, = json.loads(self.docker("image", "inspect", IMAGE))
        self.assertEqual(row["Id"], IMAGE)
        self.assertEqual(row["Os"], "linux")
        self.name = "menu-rag-readonly-test-" + uuid4().hex
        self.label = "menu.rag-readonly-regression=" + self.name
        self.docker("volume", "create", "--label", self.label, self.name)
        self.addCleanup(self.cleanup_volume)
        self.run_python(FIXTURE, user="0:0", readonly=False)

    def cleanup_volume(self):
        row, = json.loads(self.docker("volume", "inspect", self.name))
        self.assertEqual(row["Name"], self.name)
        self.assertEqual(row["Labels"].get("menu.rag-readonly-regression"), self.name)
        self.docker("volume", "rm", self.name)

    def run_python(self, script, *, user="10001:10001", readonly=True):
        name = self.name + "-" + uuid4().hex[:8]
        mount = "type=volume,source=" + self.name + ",target=/isolated-rag"
        if readonly:
            mount += ",readonly"
        try:
            return self.docker("run", "--rm", "-i", "--pull", "never", "--name", name,
                "--label", self.label, "--network", "none", "--read-only", "--user", user,
                "--cap-drop", "ALL", *(["--cap-add", "CHOWN"] if user == "0:0" else []),
                "--security-opt", "no-new-privileges:true",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m", "--memory", "256m", "--cpus", "0.5",
                "--pids-limit", "64", "--mount", mount,
                "-e", "RAG_GENERATION_ENABLED=false", "-e", "OPENAI_API_KEY=", "-e", "LLM_KEY=",
                "--entrypoint", "python", IMAGE, "-B", "-", data=script)
        finally:
            # A timed out client can leave only this invocation's isolated helper.
            result = subprocess.run([*DOCKER, "inspect", name], capture_output=True, text=True, timeout=20)
            if result.returncode == 0:
                row, = json.loads(result.stdout)
                self.assertEqual(row["Image"], IMAGE)
                self.assertEqual(row["HostConfig"]["NetworkMode"], "none")
                self.assertEqual(row["Config"]["Labels"].get("menu.rag-readonly-regression"), self.name)
                self.docker("rm", "-f", row["Id"])

    def test_delete_mode_uid10001_readonly_mount_survives_writer_close_and_restart(self):
        self.assertEqual(json.loads(self.run_python(READER))["documents"], ["doc:0", "doc:1", "doc:2"])
        # Each invocation is a distinct writer process, as with a Worker restart.
        for index in range(2):
            self.run_python("import sqlite3,os\nassert os.getuid()==10001\n"
                "with sqlite3.connect('/isolated-rag/facts.sqlite3') as db:\n"
                " assert db.execute('PRAGMA journal_mode').fetchone()[0]=='delete'\n"
                f" db.execute('INSERT INTO rag_draft_evidence VALUES (?)',('doc:{index}',))\n",
                readonly=False)
            report = json.loads(self.run_python(READER))
            self.assertEqual(report["documents"], [f"doc:{n}" for n in range(index + 1, 3)])
            self.assertEqual(report["reader_writes"], 0)

    def test_wal_without_sidecars_fails_closed_on_readonly_mount(self):
        self.run_python("import sqlite3\n"
            "db=sqlite3.connect('/isolated-rag/facts.sqlite3')\n"
            "assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'\n"
            "db.close()\n", readonly=False)
        output = self.run_python("""
import hashlib,json,sqlite3
from pathlib import Path
from menu_worker.rag_candidates import DictionaryCandidates
root=Path('/isolated-rag'); path=root/'facts.sqlite3'
assert not list(root.glob('*-wal')) and not list(root.glob('*-shm'))
before=hashlib.sha256(path.read_bytes()).hexdigest()
try:
    DictionaryCandidates(path,catalog_work_ids={8})(
      {'namespace':'dictionary','menu_work_id':8,'content_type':'character',
       'allowed_source_ids':['test_reference']})
except sqlite3.OperationalError:
    pass
else:
    raise AssertionError('WAL missing-sidecar guard unexpectedly succeeded')
assert hashlib.sha256(path.read_bytes()).hexdigest()==before
assert not list(root.glob('*-wal')) and not list(root.glob('*-shm'))
print(json.dumps({'wal_read_blocked':True,'reader_writes':0}))
""")
        self.assertTrue(json.loads(output)["wal_read_blocked"])

    def test_controller_composition_does_not_import_native_or_provider_modules(self):
        # Also run this test with the existing article image: it intentionally
        # has no ani_rag package, while the controller needs only menu_worker.
        output = self.run_python("""
import hashlib,json,os,sys
from pathlib import Path
from types import SimpleNamespace
os.environ['CONTENT_JOB_DATABASE_URL']='postgresql://unused@127.0.0.1:1/menu_content_jobs'
from menu_worker.rag_daily_runtime import compose_runtime
config={'isolated_root':'/isolated-rag','database_path':'/isolated-rag/facts.sqlite3',
 'image_root':'/isolated-rag','api_key_file':'/not-mounted/key','vector_dsn_file':'/not-mounted/vector',
 'expected_vector_database':'menu_rag_vector_test','catalog_work_ids':[8],
 'enabled':False,'confirm_isolated_copy':True,'offline':False,'model':'gpt-6-luna',
 'pricing':{name:{'input':'1','output':'1','cached_input':'1'}
            for name in ('gpt-6-luna','text-embedding-3-small')}}
path=Path('/tmp/runtime.json'); path.write_text(json.dumps(config))
sha=hashlib.sha256(path.read_bytes()).hexdigest()
scope={'schema_version':1,'purpose':'rag_daily_generation','routes':[
 {'namespace':'dictionary','menu_work_id':8,'content_type':'character','allowed_source_ids':['test_reference']}]}
plan={'rag_scope':scope,'rag_runtime_sha256':sha,'rag_delivery_root':'/isolated-rag',
      'config':{'artifact_root':'/isolated-rag'}}
runtime=compose_runtime(plan,app=SimpleNamespace(),runtime_path=path,runtime_sha256=sha,enable=False)
assert len(runtime.discover(scope['routes'][0]))==3
assert not any(name=='ani_rag' or name.startswith('ani_rag.') or name=='openai'
               or name.startswith('openai.') for name in sys.modules)
print(json.dumps({'native_imported':False,'provider_imported':False,'llm_calls':0}))
""")
        self.assertEqual(json.loads(output), {"native_imported": False, "provider_imported": False, "llm_calls": 0})


if __name__ == "__main__":
    unittest.main()
