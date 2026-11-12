"""恢复验收：对照备份计数，校验恢复文件哈希，并用独立数据库执行登录与查询。

运行在 API 临时容器；输入 /verification 目录，不改变正式数据库连接或上传卷。
"""
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, select, func
from sqlalchemy.engine import make_url

root = Path('/verification')
restore = json.loads((root / 'restore.json').read_text(encoding='utf-8-sig'))
database = restore['database']
if not database.startswith('vibe_restore_') or not database.removeprefix('vibe_restore_').isdigit():
    raise ValueError('Only isolated restore databases may be verified')

# 导入应用前替换当前临时进程连接，确保所有 API 调用都在恢复库内执行。
os.environ['DATABASE_URL'] = make_url(os.environ['DATABASE_URL']).set(database=database).render_as_string(hide_password=False)
os.environ['UPLOAD_DIR'] = '/restored-uploads'
from app import db
from app.main import app
from fastapi.testclient import TestClient

expected_counts = json.loads((root / 'counts.json').read_text(encoding='utf-8-sig'))
engine = create_engine(os.environ['DATABASE_URL'])
with engine.connect() as connection:
    from sqlalchemy import MetaData, Table
    for name, expected in expected_counts.items():
        table = Table(name, MetaData(), autoload_with=connection)
        actual = connection.scalar(select(func.count()).select_from(table))
        assert actual == expected, f'Count mismatch for {name}: {actual} != {expected}'
    files = list(connection.execute(select(db.files)).mappings())
    for item in files:
        content = (Path('/restored-uploads') / item['stored_name']).read_bytes()
        assert len(content) == item['size']
        assert hashlib.sha256(content).hexdigest() == item['sha256']

expected = json.loads(Path('/evidence/persistence.json').read_text())
# 已校对备份原计数和文件后，只升级这个隔离恢复库，验证旧备份仍可被新版本读取。
from alembic import command
from alembic.config import Config
from app.workflow import setup_checkpoints
command.upgrade(Config('/app/alembic.ini'), 'head')
setup_checkpoints()
storage = json.loads(Path('/session/session.json').read_text())
client = TestClient(app)
for cookie in storage['cookies']:
    client.cookies.set(cookie['name'], cookie['value'])
me = client.get('/api/auth/me')
assert me.status_code == 200, 'Backed-up server session did not restore'
assert me.json()['username'] == expected['username']
table = client.get(f"/api/tables/{expected['table_id']}").json()
assert table['total'] == expected['row_count']
assert next(r['values']['c3'] for r in table['rows'] if r['values']['c1'] == '001') == 95
result = client.get(f"/api/results/{expected['result_id']}").json()
assert [r['c3'] for r in result['rows']] == [90, 80]
download = client.get(f"/api/files/{expected['file_id']}/download")
assert download.status_code == 200
assert hashlib.sha256(download.content).hexdigest() == expected['file_sha256']
conversation = client.get(f"/api/conversations/{expected['conversation_id']}").json()
assert any(m['result_id'] == expected['result_id'] for m in conversation['messages'])
assert client.post('/api/auth/logout', headers={'X-CSRF-Token': me.json()['csrf_token']}).status_code == 200
assert client.get('/api/auth/me').status_code == 401
client.cookies.clear()
login = client.post('/api/auth/login', json={'username': os.environ['VIBE_ACCEPTANCE_USERNAME'], 'password': os.environ['VIBE_ACCEPTANCE_PASSWORD']})
assert login.status_code == 200, 'Restored password hash failed login'
csrf = login.json()['csrf_token']
# 用恢复库中的字段注册表执行新查询，验证恢复后仍能实际读写快照及审计。
query = client.post(f"/api/conversations/{expected['conversation_id']}/messages", headers={'X-CSRF-Token': csrf}, json={
    'content': '恢复验收结构化查询', 'table_ids': [expected['table_id']],
    'plan': {'operation': 'select', 'table_id': expected['table_id'], 'fields': ['c1','c3'],
             'filters': [{'field_id': 'c3', 'op': 'gte', 'value': 80}], 'sort': [{'field_id': 'c3','direction': 'desc'}]}})
assert query.status_code == 200, f'Database query after recovery failed (HTTP {query.status_code})'
assert [r['c3'] for r in query.json()['result']['rows']] == [95, 80]
report = {'database': database, 'counts': expected_counts, 'source_files_verified': len(files),
          'passed': ['backup counts', 'source file hashes', 'session', 'relogin', 'saved rows', 'query snapshot', 'conversation', 'download', 'new real query and checkpoint']}
(root / 'restore-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'database': database, 'verified_tables': len(expected_counts), 'source_files': len(files), 'passed': len(report['passed'])}))
