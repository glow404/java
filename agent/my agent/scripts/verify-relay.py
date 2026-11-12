"""真实中转站应用验收：只使用新建虚构样例，验证模型、数据库快照与确认链路。

读取忽略文件中的专用验收账号；不打印密钥、不读取其他账号或业务表。
"""
import json
import os
import time
from pathlib import Path
import httpx
from dotenv import dotenv_values

root = Path(__file__).resolve().parent.parent
credentials = dotenv_values(root / '.env.acceptance')
client = httpx.Client(base_url=os.getenv('VIBE_URL', 'http://localhost:8080'), timeout=230, follow_redirects=False)
report = {'status': 'running', 'model_mode': 'openai_responses', 'data': 'new synthetic scores only', 'samples_sent': False, 'passed': [], 'calls': []}


def checked(response):
    if response.status_code != 200:
        try:
            detail = response.json().get('detail', '')
        except ValueError:
            detail = 'Non-JSON application error'
        raise RuntimeError(f'Application request failed: HTTP {response.status_code}: {str(detail)[:300]}')
    return response.json()


def post(path, payload=None, **kwargs):
    return checked(client.post(path, headers={'X-CSRF-Token': csrf}, **({'json': payload} if payload is not None else {}), **kwargs))


def ask(question):
    started = time.monotonic()
    print('Calling real relay with synthetic metadata...', flush=True)
    result = post(f'/api/conversations/{conversation}/messages', {'content': question, 'table_ids': [table_id]})
    report['calls'].append({'elapsed_seconds': round(time.monotonic() - started, 2), 'kind': 'clarify' if result.get('clarifications') else 'plan' if result.get('plan') else 'query'})
    return result


try:
    me = checked(client.post('/api/auth/login', json={'username': credentials['USERNAME'], 'password': credentials['PASSWORD']}))
    csrf = me['csrf_token']
    name = f'真实模型虚构验收-{time.time_ns()}'
    uploaded = post('/api/files', files={'file': (name + '.csv', '学号,班级,成绩\n001,一班,90\n002,一班,50\n003,二班,80\n004,二班,\n'.encode(), 'text/csv')})
    deadline = time.monotonic() + 90
    while True:
        task = checked(client.get(f"/api/tasks/{uploaded['task_id']}"))
        if task['status'] == 'completed':
            break
        if task['status'] == 'failed' or time.monotonic() > deadline:
            raise RuntimeError('Synthetic file parsing failed or timed out')
        time.sleep(0.5)
    preview = checked(client.get(f"/api/imports/{uploaded['task_id']}"))
    candidate = preview['tables'][0]
    imported = post(f"/api/imports/{uploaded['task_id']}/confirm", {'version': preview['version'], 'tables': [{
        'candidate_id': candidate['candidate_id'], 'name': name, 'description': '虚构验收数据；不是公司业务数据',
        'description_provenance': 'user', 'fields': candidate['fields'], 'reviewed': True}]})
    table_id = imported['table_ids'][0]
    conversation = post('/api/conversations', {'title': '真实云渡接口虚构数据验收'})['id']
    report.update(table_id=table_id, conversation_id=conversation)
    average = ask('按班级统计成绩平均值，空成绩不按零计，并画柱状图。只查询当前选中的这张表。')
    assert average.get('plan_source') == 'openai_responses'
    result = average['result']
    plan = result['query_snapshot']['plan']
    measure = next(a for a in plan['aggregates'] if a['function'] == 'avg')
    assert {r['c2']: r[measure['alias']] for r in result['rows']} == {'一班': 70, '二班': 80}
    assert result['chart']['type'] == 'bar'
    assert checked(client.get(f"/api/results/{result['id']}"))['rows'] == result['rows']
    report['passed'].append('real model grouped average -> real SQL -> stored chart snapshot')
    ambiguous = ask('高分学生占比是多少？我还没有定义高分阈值和分母，请先问我，不要默认任何口径。')
    assert ambiguous.get('clarifications') and not ambiguous.get('result') and not ambiguous.get('plan')
    report['passed'].append('undefined ratio requests clarification')
    previewed = ask('把学号为001的成绩修改为93。其他记录保持不变，请先预览影响范围，等待我确认。')
    assert previewed.get('plan_source') == 'openai_responses' and previewed.get('plan'), 'Mutation intent did not produce a real-model preview'
    pending = previewed['plan']
    assert pending['status'] == 'pending' and pending['operation'] == 'update' and pending['affected_count'] == 1
    assert pending['before'][0]['values']['c3'] == 90 and pending['after'][0]['values']['c3'] == 93
    before = checked(client.get(f'/api/tables/{table_id}'))
    assert next(r['values']['c3'] for r in before['rows'] if r['values']['c1'] == '001') == 90
    committed = post(f"/api/plans/{pending['id']}/confirm")
    assert committed['plan']['status'] == 'executed'
    assert committed['result']['rows'][0]['c3'] == 93
    repeated = post(f"/api/plans/{pending['id']}/confirm")
    assert repeated['result']['id'] == committed['result']['id']
    after = checked(client.get(f'/api/tables/{table_id}'))
    assert next(r['values']['c3'] for r in after['rows'] if r['values']['c1'] == '001') == 93
    history = checked(client.get('/api/history?limit=100'))['operations']
    assert len([entry for entry in history if entry.get('plan_id') == pending['id'] and entry.get('action') == 'update']) == 1
    report['passed'].append('real model mutation -> persisted preview -> confirmation -> single commit')
    assert checked(client.get('/api/catalog'))['model_mode'] == 'openai_responses'
    report['passed'].append('running application uses real Responses adapter')
    report['status'] = 'passed'
    print(json.dumps({'passed': len(report['passed']), 'model': 'real relay', 'calls': report['calls']}, ensure_ascii=False), flush=True)
except Exception as exc:
    # 只记录应用级错误和已通过步骤；不能把中途失败的报告当作完整联调成功。
    report.update(status='failed', error=f'{type(exc).__name__}: {str(exc)[:300]}')
    raise
finally:
    (root / 'test-results').mkdir(exist_ok=True)
    (root / 'test-results/relay-application-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    client.close()
