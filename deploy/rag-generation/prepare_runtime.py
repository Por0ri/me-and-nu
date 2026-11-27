"""Prepare a fresh SHA-pinned RAG runtime without opening provider credentials.

Default output is disabled. This local file tool never invokes Docker, systemd,
HTTP, a model, a worker, or a service database. It never edits a prior runtime.
"""
import argparse
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
from urllib.parse import urlsplit

KEY_HOST = '/home/ubuntu/.secrets/anime-openai-key.txt'
KEY_CONTAINER = '/run/secrets/rag-llm-key'
EMBEDDING_MODEL = 'text-embedding-3-small'
RUNTIME_FIELDS = {'isolated_root','database_path','image_root','api_key_file','vector_dsn_file',
    'expected_vector_database','catalog_work_ids','enabled','confirm_isolated_copy','model','pricing','offline'}


class PreparationError(RuntimeError):
    """Fixed nonsecret validation reasons only."""


def require(value, reason):
    if not value:
        raise PreparationError(reason)


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def runtime_bytes(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()


def raw_sha(value):
    return hashlib.sha256(value).hexdigest()


def checked_path(path, *, exists=True):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path
            and not any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (path,*path.parents)),
            'unsafe_configuration_path')
    require(not exists or path.exists(), 'configuration_path_missing')
    return path


def read_json(path):
    path = checked_path(path)
    require(path.is_file() and path.stat().st_size <= 128*1024, 'invalid_configuration_file')
    raw = path.read_bytes()
    def pairs(items):
        value = dict(items)
        require(len(value) == len(items),'duplicate_configuration_key')
        return value
    try:
        return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError())),raw
    except (ValueError,UnicodeError):
        raise PreparationError('invalid_configuration_json') from None


def validate_model_settings(settings):
    require(isinstance(settings,dict) and set(settings) == {'schema_version','purpose','model','pricing','pricing_sources'}
            and type(settings['schema_version']) is int and settings['schema_version'] == 1
            and settings['purpose'] == 'rag_model_settings', 'invalid_model_settings')
    model = settings['model']
    require(isinstance(model,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',model)
            and model != EMBEDDING_MODEL, 'invalid_generation_model')
    prices = settings['pricing']
    require(isinstance(prices,dict) and set(prices) == {model,EMBEDDING_MODEL}, 'explicit_model_prices_required')
    for name,rates in prices.items():
        require(isinstance(rates,dict) and set(rates) == {'input','output','cached_input'}, 'explicit_token_prices_required')
        require(all(type(value) in (str,int,float) for value in rates.values()), 'invalid_token_price')
        try:
            values = {key:Decimal(str(value)) for key,value in rates.items()}
        except (InvalidOperation,ValueError,TypeError):
            raise PreparationError('invalid_token_price') from None
        require(all(value.is_finite() and value >= 0 for value in values.values()) and values['input'] > 0
                and (name != model or values['output'] > 0), 'invalid_token_price')
    sources = settings['pricing_sources']
    require(isinstance(sources,list) and len(sources) == 2, 'price_sources_required')
    require({row.get('model') for row in sources if isinstance(row,dict)} == set(prices), 'price_sources_required')
    for row in sources:
        require(set(row) == {'model','url','checked_on','basis'} and isinstance(row['url'],str)
                and isinstance(row['basis'],str) and 1 <= len(row['basis']) <= 500, 'invalid_price_source')
        url = urlsplit(row['url'])
        require(url.scheme == 'https' and url.hostname in {'developers.openai.com','platform.openai.com','openai.com'}
                and not url.username and not url.password and not url.query and not url.fragment, 'official_price_source_required')
        try:
            require(date.fromisoformat(row['checked_on']).isoformat() == row['checked_on'], 'invalid_price_source_date')
        except (ValueError,TypeError):
            raise PreparationError('invalid_price_source_date') from None
    return settings


def build_runtime(template,settings, *, enabled=False):
    validate_model_settings(settings)
    require(type(enabled) is bool and isinstance(template,dict) and set(template) == RUNTIME_FIELDS,
            'invalid_runtime_template')
    # These container paths are the reviewed Compose contract. Host data paths
    # are explicit mounts; no original dictionary/production DB path is inferred.
    require(template['isolated_root'] == '/var/lib/menu-rag'
            and template['database_path'] == '/var/lib/menu-rag/ani_rag.sqlite3'
            and template['image_root'] == '/opt/rag-images'
            and template['vector_dsn_file'] == '/run/secrets/rag-vector-dsn'
            and isinstance(template['expected_vector_database'],str)
            and re.fullmatch(r'menu_rag_vector(?:_[a-z0-9]+)*',template['expected_vector_database'])
            and template['confirm_isolated_copy'] is True and template['offline'] is False
            and type(template['enabled']) is bool, 'isolated_runtime_contract_required')
    ids = template['catalog_work_ids']
    require(isinstance(ids,list) and ids and all(type(value) is int and 1 <= value <= 2**63-1 for value in ids)
            and len(ids) == len(set(ids)), 'invalid_runtime_work_ids')
    return {**template,'enabled':enabled,'api_key_file':KEY_CONTAINER,'model':settings['model'],'pricing':settings['pricing']}


def validate_activation(policy,reviewed_policy_sha256,runtime,output):
    # The host scheduler separately validates every route, seed, image binding,
    # prior history and journal. This file writer checks only its pinned binding.
    require(isinstance(reviewed_policy_sha256,str) and re.fullmatch(r'[0-9a-f]{64}',reviewed_policy_sha256)
            and isinstance(policy,dict) and digest(policy) == reviewed_policy_sha256,
            'reviewed_schedule_policy_required')
    require(type(policy.get('schema_version')) is int and policy['schema_version'] == 2
            and policy.get('purpose') == 'daily_count_schedule'
            and policy.get('max_attempts') == 40 and type(policy.get('max_attempts')) is int
            and policy.get('success_target') == 20 and type(policy.get('success_target')) is int,
            'mixed_count_policy_required')
    rag = policy.get('rag')
    require(isinstance(rag,dict) and set(rag) == {'enabled','scope_file','scope_sha256','runtime_config','runtime_sha256','feed_valid_days'}
            and rag['enabled'] is True and (rag['feed_valid_days'] is None
                or (type(rag['feed_valid_days']) is int and 1 <= rag['feed_valid_days'] <= 365)),
            'explicit_rag_activation_policy_required')
    require(rag['runtime_config'] == str(output) and rag['runtime_sha256'] == raw_sha(runtime_bytes(runtime)),
            'reviewed_runtime_binding_required')
    require(re.fullmatch(r'[0-9a-f]{64}',rag['scope_sha256'] or '') and isinstance(rag['scope_file'],str),
            'reviewed_rag_scope_required')
    scope = PurePosixPath(rag['scope_file'])
    require(not scope.is_absolute() and '..' not in scope.parts and '\\' not in rag['scope_file'] and scope.suffix == '.json',
            'reviewed_rag_scope_required')


def validate_secret_permissions(info,modes):
    require(info.st_uid == 10001 and info.st_mode & 0o777 in modes, 'existing_secret_permissions_changed')


def check_host_files(*,state_dir,image_dir,vector_dsn_file,key_file=KEY_HOST,service_dsn_file=None):
    require(str(key_file) == KEY_HOST, 'existing_approved_key_reference_required')
    state_dir,image_dir = checked_path(state_dir),checked_path(image_dir)
    vector,key = checked_path(vector_dsn_file),checked_path(key_file)
    require(state_dir.is_dir() and image_dir.is_dir() and vector.is_file() and key.is_file(), 'prepared_runtime_files_required')
    # stat only: provider key and DSN contents are never opened or copied.
    require(key.stat().st_size > 0 and key.stat().st_size <= 16384 and vector.stat().st_size > 0,
            'nonempty_existing_secret_files_required')
    if os.name != 'nt':
        validate_secret_permissions(key.stat(),{0o600})
        validate_secret_permissions(vector.stat(),{0o400,0o600})
    if service_dsn_file is not None:
        service = checked_path(service_dsn_file)
        require(service.is_file() and 0 < service.stat().st_size <= 16384, 'service_dsn_file_required')
        if os.name != 'nt':
            validate_secret_permissions(service.stat(),{0o400,0o600})
    database = checked_path(state_dir/'ani_rag.sqlite3')
    require(database.is_file(), 'isolated_runtime_database_required')
    # Refuse a WAL database before opening it: a read-only controller mount must
    # not depend on creating/removing shared-memory sidecars after worker exit.
    with database.open('rb') as stream:
        header = stream.read(20)
    require(header[:16] == b'SQLite format 3\x00' and header[18:20] == b'\x01\x01',
            'delete_journal_runtime_required')
    require(not any(Path(str(database)+suffix).exists() and Path(str(database)+suffix).stat().st_size
                    for suffix in ('-wal','-journal')), 'quiescent_runtime_check_required')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON')
        require(conn.execute('PRAGMA journal_mode').fetchone() == ('delete',), 'delete_journal_runtime_required')
    return {'host_files_verified':True,'sqlite_journal_mode':'delete','key_contents_read':False,
            'dsn_contents_read':False,'database_writes':0}


def write_fresh(path,raw):
    path = checked_path(path,exists=False)
    require(path.parent.is_dir() and not path.exists(), 'fresh_output_file_required')
    with path.open('xb') as stream:
        os.chmod(path,0o440)
        if os.name != 'nt' and os.geteuid() == 0:
            os.chown(path,10001,10001)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def prepare(*,template_path,model_settings_path,reviewed_model_settings_sha256,output=None,
            enable=False,policy_path=None,reviewed_policy_sha256=None,host_check=None):
    template,template_raw = read_json(template_path)
    settings,settings_raw = read_json(model_settings_path)
    require(isinstance(reviewed_model_settings_sha256,str) and re.fullmatch(r'[0-9a-f]{64}',reviewed_model_settings_sha256)
            and raw_sha(settings_raw) == reviewed_model_settings_sha256,'reviewed_model_settings_required')
    runtime = build_runtime(template,settings,enabled=enable)
    require(enable or policy_path is None and reviewed_policy_sha256 is None, 'activation_arguments_require_enable')
    if enable:
        require(output is not None and policy_path is not None, 'explicit_enabled_output_policy_required')
        validate_activation(read_json(policy_path)[0],reviewed_policy_sha256,runtime,checked_path(output,exists=False))
    report = {'schema_version':1,'purpose':'rag_runtime_preparation','runtime_sha256':raw_sha(runtime_bytes(runtime)),
        'enabled':enable,'model':settings['model'],'model_settings_sha256':reviewed_model_settings_sha256,
        'template_sha256':raw_sha(template_raw),'key_host_reference':KEY_HOST,'key_container_reference':KEY_CONTAINER,
        'key_contents_read':False,'key_copied':False,'llm_calls':0,'service_db_access':False,'worker_started':False,
        'host_files_verified':False,'provider_generation_permission_verified':False,
        'enabled_candidate_sha256':raw_sha(runtime_bytes(build_runtime(template,settings,enabled=True)))}
    if host_check is not None:
        report.update(check_host_files(**host_check))
    if enable:
        report['reviewed_policy_sha256'] = reviewed_policy_sha256
    if output is not None:
        output = checked_path(output,exists=False)
        receipt = checked_path(Path(str(output)+'.preparation.json'),exists=False)
        require(not output.exists() and not receipt.exists(), 'fresh_output_file_required')
        write_fresh(output,runtime_bytes(runtime))
        write_fresh(receipt,runtime_bytes(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template',type=Path,required=True)
    parser.add_argument('--model-settings',type=Path,required=True)
    parser.add_argument('--reviewed-model-settings-sha256',required=True)
    parser.add_argument('--output',type=Path,help='Fresh file only; omit for a hash-only preview')
    parser.add_argument('--enable',action='store_true',help='File preparation only; requires the reviewed enabled policy')
    parser.add_argument('--policy',type=Path)
    parser.add_argument('--reviewed-policy-sha256')
    parser.add_argument('--check-host-files',action='store_true')
    parser.add_argument('--state-host-dir',type=Path)
    parser.add_argument('--image-host-dir',type=Path)
    parser.add_argument('--vector-dsn-host-file',type=Path)
    parser.add_argument('--service-dsn-host-file',type=Path)
    args = parser.parse_args(argv)
    try:
        host_check = None
        if args.check_host_files:
            require(all((args.state_host_dir,args.image_host_dir,args.vector_dsn_host_file)), 'explicit_host_paths_required')
            host_check = {'state_dir':args.state_host_dir,'image_dir':args.image_host_dir,
                'vector_dsn_file':args.vector_dsn_host_file,'service_dsn_file':args.service_dsn_host_file}
        elif any((args.state_host_dir,args.image_host_dir,args.vector_dsn_host_file,args.service_dsn_host_file)):
            require(False,'host_paths_require_explicit_check')
        report = prepare(template_path=args.template,model_settings_path=args.model_settings,
            reviewed_model_settings_sha256=args.reviewed_model_settings_sha256,output=args.output,
            enable=args.enable,policy_path=args.policy,reviewed_policy_sha256=args.reviewed_policy_sha256,host_check=host_check)
        print(json.dumps(report,sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({'status':'stopped','reason':str(error) if isinstance(error,PreparationError)
            else 'runtime_preparation_failed','llm_calls':0,'worker_started':False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
