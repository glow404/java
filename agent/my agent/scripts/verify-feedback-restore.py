"""独立恢复库的反馈验收：校对备份、恢复会话及新资源，并执行参数化查询。"""
import hashlib
import json
import os
from pathlib import Path
from sqlalchemy import create_engine, MetaData, Table, select, func
from sqlalchemy.engine import make_url

root = Path('/verification')
restore = json.loads((root / 'restore.json').read_text(encoding='utf-8-sig'))
database = restore['database']
if not database.startswith('vibe_restore_') or not database.removeprefix('vibe_restore_').isdigit():
    raise ValueError('Only isolated restore databases may be verified')
os.environ['DATABASE_URL'] = make_url(os.environ['DATABASE_URL']).set(database=database).render_as_string(hide_password=False)
os.environ['UPLOAD_DIR'] = '/restored-uploads'

from app import db
from app.main import app
from fastapi.testclient import TestClient

expected_counts = json.loads((root / 'counts.json').read_text(encoding='utf-8-sig'))
engine = create_engine(os.environ['DATABASE_URL'])
with engine.connect() as connection:
    # 先验证恢复原样，再进行隔离行为验证，避免新审计记录干扰备份对照。
    for name, expected in expected_counts.items():
        table = Table(name, MetaData(), autoload_with=connection)
        assert connection.scalar(select(func.count()).select_from(table)) == expected, f'Count mismatch: {name}'
    source_files = list(connection.execute(select(db.files)).mappings())
    for item in source_files:
        content = (Path('/restored-uploads') / item['stored_name']).read_bytes()
        assert len(content) == item['size']
        assert hashlib.sha256(content).hexdigest() == item['sha256']

baseline = json.loads(Path('/evidence/feedback-browser-report.json').read_text())
assert baseline['status'] == 'passed'
expected = baseline['resources']
storage = json.loads(Path('/session/session.json').read_text())
client = TestClient(app)
for cookie in storage['cookies']:
    client.cookies.set(cookie['name'], cookie['value'])

def get(endpoint):
    response = client.get('/api' + endpoint)
    assert response.status_code == 200, f'GET {endpoint}: HTTP {response.status_code}'
    return response.json()

me = get('/auth/me')
assert me['username'] == os.environ['VIBE_ACCEPTANCE_USERNAME']
catalog = get('/catalog')
table = get('/tables/' + expected['primary_table_id'])
assert [row['values']['c1'] for row in table['rows']] == ['003', '001', '002', '004']
assert table['rows'][3]['values']['c3'] is None
assert table['description'] == expected['primary_metadata']['description']
assert next(field for field in table['fields'] if field['id'] == 'c3')['aliases'] == ['分数', '得分']
assert next(field for field in table['fields'] if field['id'] == 'c3')['description'] == expected['primary_metadata']['score_description']
conversation = get('/conversations/' + expected['conversation_id'])
assert conversation['title'] == expected['conversation_title']
assert expected['hidden_result_id'] in catalog['hidden_result_ids']
assert expected['hidden_result_id'] in conversation['hidden_result_ids']
for identifier in [expected['original_order_result_id'], expected['adjusted_result_id'], expected['joined_result_id'], expected['ui_joined_result_id'], expected['narrow_result_id']]:
    assert get('/results/' + identifier)['id'] == identifier
    assert any(result['id'] == identifier for result in conversation['results'])
for identifier in [expected['cancelled_run_id'], expected['cancelled_ui_run_id']]:
    assert get('/runs/' + identifier)['status'] == 'cancelled'
relation = next(item for item in get('/relations')['relations'] if item['id'] == expected['ui_relation_id'])
assert relation['status'] == 'active' and not relation['stale']
manual = get('/tables/' + expected['manual_table_id'])
assert not manual['archived'] and manual['row_count'] == 1
assert manual['rows'][0]['values'][manual['fields'][0]['id']] == '00009'
download = client.get('/api/files/' + expected['source_file_id'] + '/download')
assert download.status_code == 200
assert hashlib.sha256(download.content).hexdigest() == expected['source_file_sha256']

assert client.post('/api/auth/logout', headers={'X-CSRF-Token': me['csrf_token']}).status_code == 200
assert client.get('/api/auth/me').status_code == 401
client.cookies.clear()
login = client.post('/api/auth/login', json={'username': os.environ['VIBE_ACCEPTANCE_USERNAME'], 'password': os.environ['VIBE_ACCEPTANCE_PASSWORD']})
assert login.status_code == 200
# 此后仅在隔离恢复库新建查询快照和审计，不调用模型或生产RQ。
response = client.post('/api/conversations/' + expected['conversation_id'] + '/messages', headers={'X-CSRF-Token': login.json()['csrf_token']}, json={
    'content': '恢复验收：已核对字段的结构化查询', 'table_ids': [table['id']],
    'plan': {'operation': 'select', 'table_id': table['id'], 'fields': ['c1', '分数'],
        'filters': [{'field_id': '得分', 'op': 'gte', 'value': 80}], 'sort': [{'field_id': '分数', 'direction': 'desc'}]}})
assert response.status_code == 200, f'Query after restore: HTTP {response.status_code}'
assert [row['c3'] for row in response.json()['result']['rows']] == [90, 80]
report = {'database': database, 'verified_tables': len(expected_counts), 'source_files_verified': len(source_files),
    'passed': ['backup counts', 'all source file hashes', 'server session', 'row order and leading zero', 'metadata and aliases',
        'renamed conversation', 'immutable result snapshots', 'cancelled runs', 'confirmed relations', 'restored table', 'hidden preferences', 'relogin and actual SQL query']}
(root / 'feedback-restore-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print({'database': database, 'verified_tables': report['verified_tables'], 'source_files': len(source_files), 'passed': len(report['passed'])})
