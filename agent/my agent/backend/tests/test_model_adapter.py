"""中转站协议/故障自动化替身测试；MockTransport 不代表真实接口联调。"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import HTTPException

from app.config import Settings
from app import model_adapter
from app.model_adapter import ModelAdapter

TABLES = [{"id": "table-authorized", "name": "成绩", "description": "用户已授权的字段元信息", "fields": [
    {"id": "c1", "name": "学号", "type": "string"},
    {"id": "c2", "name": "成绩", "type": "integer", "unit": "分"},
]}]
VALID = {"operation": "select", "table_id": "table-authorized", "fields": ["c1", "c2"], "limit": 20}


def configuration(**changes):
    return Settings(**{"model_provider": "openai_chat", "model_base_url": "https://relay.test/v1", "model_name": "test-model", "model_api_key": "fake-key-test", "model_protocol_verified": True, "model_allowed_data": True, "model_retries": 0, "model_timeout": 0.3, **changes})


def openai_response(plan=VALID):
    return {"choices": [{"message": {"content": json.dumps(plan, ensure_ascii=False)}}]}


def adapter_with(handler, **config):
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return ModelAdapter(configuration(**config), client)


def assert_error(adapter, status, match):
    with pytest.raises(HTTPException) as failure:
        adapter.plan("查看成绩前20条", TABLES)
    assert failure.value.status_code == status
    assert match in str(failure.value.detail)


def test_explicit_openai_protocol_payload_and_backend_credentials(caplog):
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=openai_response())

    plan = adapter_with(handler).plan("查看成绩前20条", TABLES)
    assert plan.table_id == "table-authorized"
    request = captured[0]
    assert str(request.url) == "https://relay.test/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer fake-key-test"
    body = json.loads(request.content)
    assert body["model"] == "test-model" and body["stream"] is False
    assert not {"response_format", "tools", "tool_choice", "functions"}.intersection(body)
    prompt = json.loads(body["messages"][1]["content"])
    assert prompt["allowed_tables_untrusted_metadata"] == TABLES
    assert "SQL" in body["messages"][0]["content"]
    assert "fake-key-test" not in body["messages"][0]["content"]
    assert all("fake-key-test" not in record.getMessage() for record in caplog.records)


def test_generic_documented_template_header_and_nested_response_path():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"reply": {"items": [{"plan": VALID}]}})

    adapter = adapter_with(handler, model_provider="generic_json", model_base_url="https://relay.test/under/documented/endpoint", model_auth_header="X-Relay-Key", model_auth_prefix="", model_request_template='{"deployment":"$model","request":{"text":"$prompt"},"options":{"json":false}}', model_response_path="reply.items.0.plan")
    assert adapter.plan("查看成绩", TABLES).operation == "select"
    request = captured[0]
    assert str(request.url) == "https://relay.test/under/documented/endpoint"
    assert request.headers["X-Relay-Key"] == "fake-key-test"
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["deployment"] == "test-model"
    assert body["options"] == {"json": False}
    assert json.loads(body["request"]["text"])["user_question_untrusted"] == "查看成绩"


@pytest.mark.parametrize("changes", [{"model_protocol_verified": False}, {"model_allowed_data": False}, {"model_base_url": ""}, {"model_api_key": ""}, {"model_name": ""}, {"model_provider": "not-a-provider"}])
def test_unverified_permission_or_missing_configuration_never_calls(changes):
    calls = []
    adapter = adapter_with(lambda request: calls.append(request), **changes)
    with pytest.raises(HTTPException) as failure:
        adapter.plan("查看成绩", TABLES)
    assert failure.value.status_code == 503
    assert calls == []


@pytest.mark.parametrize("url", ["http://external.test/v1", "https://user:password@relay.test/v1", "https://relay.test/v1?api_key=secret", "file:///etc/passwd"])
def test_unsafe_model_url_rejected_before_network(url):
    calls = []
    adapter = adapter_with(lambda request: calls.append(request), model_base_url=url)
    assert_error(adapter, 503, "地址" if not url.startswith("http://external") else "HTTPS")
    assert calls == []


@pytest.mark.parametrize("invalid", [
    {"operation": "select", "table_id": "table-authorized", "sql": "DROP TABLE data_rows"},
    {"operation": "python", "code": "print('untrusted')"},
    {"operation": "select", "table_id": "table-authorized", "limit": 999999},
    {"operation": "delete", "table_id": "table-authorized", "filters": []},
    {"operation": "update", "table_id": "table-authorized", "values": {}},
    {"operation": "clarify", "clarifications": []},
    {"operation": "select", "table_id": "table-authorized", "ratio": {"field_id": None, "threshold": None, "denominator": None}},
])
def test_invalid_structured_plan_never_passes_schema(invalid):
    adapter = adapter_with(lambda request: httpx.Response(200, json=openai_response(invalid)))
    assert_error(adapter, 502, "结构化计划校验")


@pytest.mark.parametrize("reply", [
    {"choices": [{"message": {"content": "```json\n{}\n```"}}]},
    {"choices": [{"message": {"content": None, "refusal": "refused"}}]},
    {"choices": []},
    {"unexpected": "response"},
])
def test_malformed_content_and_refusal_are_safe_errors(reply):
    assert_error(adapter_with(lambda request: httpx.Response(200, json=reply)), 502, "结构化计划校验")


def test_non_json_and_large_body_rejected():
    assert_error(adapter_with(lambda request: httpx.Response(200, content=b"not json")), 502, "结构化计划校验")
    assert_error(adapter_with(lambda request: httpx.Response(200, content=b"X" * 1000), model_max_response_bytes=100), 502, "大小上限")


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_http_error_retries_bounded_then_succeeds(monkeypatch, status):
    calls = []
    monkeypatch.setattr(model_adapter.time, "sleep", lambda seconds: None)

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "automatic test substitute"}) if len(calls) <= 2 else httpx.Response(200, json=openai_response())

    assert adapter_with(handler, model_retries=2).plan("查看成绩", TABLES).operation == "select"
    assert len(calls) == 3


def test_rate_limit_exhaustion_is_retryable_and_not_raw_provider_message(monkeypatch):
    monkeypatch.setattr(model_adapter.time, "sleep", lambda seconds: None)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"error": "fake-key-test secret provider detail"})

    adapter = adapter_with(handler, model_retries=1)
    assert_error(adapter, 503, "限流")
    assert len(calls) == 2


@pytest.mark.parametrize("exception", [httpx.ReadTimeout, httpx.ConnectError])
def test_timeout_and_network_error_retry_bounded(monkeypatch, exception):
    monkeypatch.setattr(model_adapter.time, "sleep", lambda seconds: None)
    calls = []

    def handler(request):
        calls.append(request)
        raise exception("automatic network-failure substitute", request=request)

    assert_error(adapter_with(handler, model_retries=2), 503, "连接失败或超时")
    assert len(calls) == 3


def test_redirect_not_followed_and_auth_not_forwarded():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://untrusted.test/collect"})

    assert_error(adapter_with(handler), 502, "HTTP 302")
    assert len(calls) == 1
    assert calls[0].url.host == "relay.test"


def test_invalid_generic_request_template_fails_before_network():
    calls = []
    adapter = adapter_with(lambda request: calls.append(request), model_provider="generic_json", model_request_template="not-json")
    assert_error(adapter, 503, "请求模板")
    assert not calls


def test_disabled_and_mock_modes_are_explicit_substitutes():
    calls = []
    assert_error(adapter_with(lambda request: calls.append(request), model_provider="disabled"), 503, "模型未配置")
    mock = adapter_with(lambda request: calls.append(request), model_provider="mock", model_protocol_verified=False, model_allowed_data=False)
    clarify = mock.plan("高分学生占比", TABLES)
    assert clarify.operation == "clarify"
    assert "自动化测试替身" in clarify.clarifications[0]
    assert mock.plan("查看成绩前20条", TABLES).table_id == "table-authorized"
    assert not calls


def test_concurrency_limit_and_exhaustion(monkeypatch):
    limit = 2
    semaphore = threading.BoundedSemaphore(limit)
    monkeypatch.setattr(model_adapter, "_semaphore", semaphore)
    lock = threading.Lock()
    active, peak = 0, 0

    def handler(request):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
        threading.Event().wait(0.03)
        with lock:
            active -= 1
        return httpx.Response(200, json=openai_response())

    adapter = adapter_with(handler, model_concurrency=limit, model_timeout=1)
    with ThreadPoolExecutor(max_workers=6) as pool:
        answers = list(pool.map(lambda index: adapter.plan("查看成绩", TABLES), range(6)))
    assert peak == limit and all(answer.operation == "select" for answer in answers)
    saturated = threading.BoundedSemaphore(1)
    saturated.acquire()
    monkeypatch.setattr(model_adapter, "_semaphore", saturated)
    no_network = []
    try:
        assert_error(adapter_with(lambda request: no_network.append(request), model_timeout=0.01), 429, "并发额度")
    finally:
        saturated.release()
    assert not no_network


def responses_reply(text=None, **changes):
    reply = {"status": "completed", "error": None, "incomplete_details": None, "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "role": "assistant", "status": "completed", "content": [
            {"type": "output_text", "text": text or json.dumps(VALID), "annotations": []}]}]}
    return {**reply, **changes}


def test_responses_protocol_reasoning_headers_and_storage_disabled(caplog):
    captured = []
    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=responses_reply())
    plan = adapter_with(handler, model_provider="openai_responses", model_reasoning_effort="xhigh",
        model_extra_headers={"x-openai-actor-authorization": "local-image-extension"}).plan("查看成绩", TABLES)
    assert plan.table_id == VALID["table_id"]
    request = captured[0]
    assert str(request.url) == "https://relay.test/v1/responses"
    assert request.headers["Authorization"] == "Bearer fake-key-test"
    assert request.headers["x-openai-actor-authorization"] == "local-image-extension"
    payload = json.loads(request.content)
    assert payload["reasoning"] == {"effort": "xhigh"}
    assert payload["store"] is False and payload["stream"] is False
    assert not {"tools", "tool_choice", "response_format", "text"}.intersection(payload)
    assert json.loads(payload["input"][0]["content"])["allowed_tables_untrusted_metadata"] == TABLES
    assert all("fake-key-test" not in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("changes", [
    {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
    {"status": "failed", "error": {"message": "private provider error"}},
    {"status": "in_progress"},
    {"status": None},
    {"output": [{"type": "function_call", "name": "execute_sql", "arguments": "untrusted"}]},
    {"output": [{"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "refused"}]}]},
    {"output": [{"type": "reasoning", "summary": []}]},
    {"output": [{"type": "message", "role": "user", "content": [{"type": "output_text", "text": "{}"}]}]},
])
def test_responses_partial_refusal_and_tool_output_cannot_be_a_plan(changes):
    adapter = adapter_with(lambda request: httpx.Response(200, json=responses_reply(**changes)), model_provider="openai_responses")
    assert_error(adapter, 502, "结构化计划校验")


def test_responses_multiple_text_parts_and_optional_reasoning():
    encoded = json.dumps(VALID)
    reply = responses_reply()
    reply["output"][1]["content"] = [{"type": "output_text", "text": encoded[:20]}, {"type": "output_text", "text": encoded[20:]}]
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=reply)
    assert adapter_with(handler, model_provider="openai_responses", model_reasoning_effort=None).plan("查看成绩", TABLES).operation == "select"
    assert "reasoning" not in json.loads(requests[0].content)


def test_responses_all_null_ratio_is_rejected_without_repair():
    # 真实联调曾收到全空占比对象；继续拒绝它，避免把结构错误误解释为可执行查询。
    invalid = {**VALID, "ratio": {"field_id": None, "threshold": None, "denominator": None}}
    adapter = adapter_with(lambda request: httpx.Response(200, json=responses_reply(json.dumps(invalid))), model_provider="openai_responses")
    assert_error(adapter, 502, "结构化计划校验")


def test_extra_headers_cannot_override_authentication():
    requests = []
    adapter = adapter_with(lambda request: requests.append(request), model_extra_headers={"authorization": "other credential"})
    assert_error(adapter, 503, "主鉴权头")
    assert not requests


@pytest.mark.parametrize("question,change", [
    ("将成绩字段说明改为桌面反馈成绩说明", {"field_changes": [{"field_id": "c2", "description": "桌面反馈成绩说明"}]}),
    ("把表说明改为虚构验收数据", {"description": "虚构验收数据"}),
])
def test_mock_new_metadata_intent_is_not_appended_to_previous_query(question, change):
    adapter = adapter_with(lambda request: pytest.fail("mock must not contact relay"), model_provider="mock")
    plan = adapter.plan(question, TABLES, history=[{"role": "user", "content": "查看得分，按得分降序"}])
    assert plan.operation == "metadata"
    actual = plan.metadata.model_dump(exclude_none=True)
    assert all(actual[key] == value for key, value in change.items())


def test_model_contract_proposes_evidenced_assumptions_and_distinguishes_percent_storage():
    prompt = json.loads(ModelAdapter.prompt("frr<3%时far最优值和对应阈值？", TABLES))
    contract = prompt["contract"]
    assert contract["assumptions"] == [] and contract["extremum"] is None
    assert "storage_unit" in contract["filters"][0]
    assert "待确认" in prompt["interpretation_rules"] and "不能猜单位" in prompt["interpretation_rules"]
    assert "0.03" in prompt["unit_rules"] and "二次换算" in prompt["unit_rules"]
    assert "并列" in prompt["extremum_rules"] and "缺阈值或分母仍须clarify" in prompt["rules"]
    assert "简短业务中文" in prompt["assumption_writing_rules"] and "不把" in prompt["assumption_writing_rules"]
