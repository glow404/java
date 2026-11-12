"""真实 Responses 新功能验收：仅新建虚构文件/表/对话，两个自然语言规划请求。

只读忽略文件 .env.acceptance 的验收账号；不读取、输出模型密钥或原始供应商响应。
"""

import json
import os
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "test-results" / "feedback-relay-report.json"
POLL_SECONDS = 230


class SafeFailure(RuntimeError):
    """只含脚本自身固定信息的失败，可安全写入脱敏验收报告。"""


def require(condition, message):
    if not condition:
        raise SafeFailure(message)


def checked(response):
    # 不打印服务器detail、HTTP原响应或底层推理，避免失败时泄露商业内容与凭据。
    require(response.status_code == 200, f"Application request failed: HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise SafeFailure("Application returned non-JSON content") from exc


def row_snapshot(table):
    return [{"id": row["id"], "values": row["values"]} for row in table["rows"]]


def main():
    report = {"status": "running", "data": "new synthetic scores only", "samples_sent": False,
        "samples_basis": "deployment send_samples=false is independently verified by the operator; alias-only questions also select no raw sample fields",
        "model_mode": None, "questions_requested": 0, "passed": [], "calls": []}
    client = httpx.Client(base_url=os.getenv("VIBE_URL", "http://localhost:8080"),
        timeout=20, follow_redirects=False)
    try:
        ready = checked(client.get("/api/ready"))
        mode = ready.get("model_mode")
        report["model_mode"] = mode if mode in {"disabled", "mock", "openai_responses", "openai_chat", "generic_json"} else "unknown"
        if ready.get("status") != "ready" or mode != "openai_responses":
            report["status"] = "aborted"
            raise SafeFailure("Aborted before any model request: running application must use openai_responses")
        credentials = dotenv_values(ROOT / ".env.acceptance")
        require(bool(credentials.get("USERNAME") and credentials.get("PASSWORD")), "Dedicated acceptance credentials are missing")
        me = checked(client.post("/api/auth/login", json={"username": credentials["USERNAME"], "password": credentials["PASSWORD"]}))
        csrf = me["csrf_token"]

        def post(path, payload=None, **kwargs):
            return checked(client.post(path, headers={"X-CSRF-Token": csrf},
                **({"json": payload} if payload is not None else {}), **kwargs))

        def get_table(table_id):
            return checked(client.get(f"/api/tables/{table_id}?offset=0&limit=100"))

        stamp = str(time.time_ns())
        table_name = "真实反馈虚构验收-" + stamp
        uploaded = post("/api/files", files={"file": (table_name + ".csv",
            "学号,班级,成绩\n001,一班,90\n002,一班,50\n003,二班,80\n004,二班,\n".encode("utf-8"), "text/csv")})
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            task = checked(client.get(f"/api/tasks/{uploaded['task_id']}"))
            if task["status"] == "completed":
                break
            require(task["status"] != "failed", "New synthetic file parsing failed")
            time.sleep(0.5)
        else:
            raise SafeFailure("New synthetic file parsing timed out")
        import_preview = checked(client.get(f"/api/imports/{uploaded['task_id']}"))
        require(len(import_preview["tables"]) == 1, "Synthetic CSV has an unexpected candidate structure")
        candidate = import_preview["tables"][0]
        imported = post(f"/api/imports/{uploaded['task_id']}/confirm", {"version": import_preview["version"], "tables": [{
            "candidate_id": candidate["candidate_id"], "name": table_name,
            "description": "新建虚构验收数据；不是公司业务数据", "description_provenance": "user",
            "fields": candidate["fields"], "reviewed": True}]})
        require(len(imported["table_ids"]) == 1, "Synthetic import did not create one independent table")
        table_id = imported["table_ids"][0]
        table = get_table(table_id)
        fields = [dict(field) for field in table["fields"]]
        registered = {field["name"]: field["id"] for field in fields}
        require({"学号", "班级", "成绩"}.issubset(registered), "Synthetic CSV fields did not match the manual annotation")
        score_id, identity_id = registered["成绩"], registered["学号"]
        score_field = next(field for field in fields if field["id"] == score_id)
        score_field["aliases"] = list(dict.fromkeys([*score_field.get("aliases", []), "分数", "得分"]))
        score_field["unit"] = "分"
        patched = checked(client.patch(f"/api/tables/{table_id}", headers={"X-CSRF-Token": csrf}, json={
            "version": table["version"], "name": table["name"], "description": table["description"],
            "description_provenance": table["description_provenance"], "fields": fields}))
        saved_score_field = next(field for field in patched["fields"] if field["id"] == score_id)
        require(set(saved_score_field["aliases"]) >= {"分数", "得分"},
            "Server did not retain the registered score aliases")
        baseline = get_table(table_id)
        require(baseline["total"] == 4, "Synthetic table row count differs from the manual annotation")
        require([row["values"][identity_id] for row in baseline["rows"]] == ["001", "002", "003", "004"], "Synthetic source order or leading zeros were lost")
        require([row["values"][score_id] for row in baseline["rows"]] == [90, 50, 80, None], "Synthetic numeric values or empty score were changed")
        original_rows = row_snapshot(baseline)
        conversation_id = post("/api/conversations", {"title": "真实反馈模型独立虚构验收-" + stamp})["id"]
        report.update(table_id=table_id, conversation_id=conversation_id, task_id=uploaded["task_id"])
        report["passed"].append("new synthetic CSV imported with source order, leading zeros, null score and registered aliases")

        def conversation_results():
            return checked(client.get(f"/api/conversations/{conversation_id}"))["results"]

        def ask(question, expected_operation):
            # 当前后台仅按问题命中的原字段名选样本；两次提问使用别名，样本字段集合保持为空。
            require(not any(field["name"] in question for field in baseline["fields"]),
                "Aborted before model request: question unexpectedly matches a raw sample field name")
            started = time.monotonic()
            report["questions_requested"] += 1
            print(f"Real relay feedback question {report['questions_requested']}/2: waiting for persisted preview...", flush=True)
            run = post(f"/api/conversations/{conversation_id}/runs", {"content": question, "table_ids": [table_id]})
            deadline = started + POLL_SECONDS
            while time.monotonic() < deadline:
                pending = checked(client.get(f"/api/runs/{run['id']}", timeout=min(10, max(0.1, deadline - time.monotonic()))))
                if pending["status"] == "completed":
                    break
                require(pending["status"] not in {"failed", "cancelled"}, "Real relay workflow failed or was cancelled; no raw model output was recorded")
                time.sleep(0.75)
            else:
                raise SafeFailure("Real relay workflow did not finish within the 230-second polling limit")
            response = pending.get("response") or {}
            require(response.get("plan_source") == "openai_responses", "Workflow did not report the real Responses adapter")
            require("result" not in response and response.get("plan"), "Natural-language request did not produce a preview-only response")
            plan = response["plan"]
            require(plan["operation"] == expected_operation and plan["status"] == "pending", "Model returned an unexpected operation or non-pending plan")
            report["calls"].append({"operation": expected_operation, "elapsed_seconds": round(time.monotonic() - started, 2), "run_id": run["id"], "plan_id": plan["id"]})
            return plan

        query_plan = ask("请只查看当前虚构表中得分至少80的学生编号和得分，按得分从高到低排列。先反馈筛选和排序的下一步计划，等待我确认后再执行。", "select")
        require(not conversation_results(), "Query result was saved before explicit confirmation")
        require(row_snapshot(get_table(table_id)) == original_rows, "Query planning changed synthetic records")
        confirmed = post(f"/api/plans/{query_plan['id']}/confirm")
        result = confirmed["result"]
        require([row.get(score_id) for row in result["rows"]] == [90, 80], "Real database query did not return expected descending 90/80 scores")
        require([row.get(identity_id) for row in result["rows"]] == ["001", "003"], "Real database query selected unexpected synthetic identifiers")
        repeated = post(f"/api/plans/{query_plan['id']}/confirm")
        require(repeated["result"]["id"] == result["id"] and repeated["result"]["query_snapshot"] == result["query_snapshot"], "Repeated query confirmation did not reuse the stored snapshot")
        fetched = checked(client.get(f"/api/results/{result['id']}"))
        require(fetched["rows"] == result["rows"] and fetched["query_snapshot"] == result["query_snapshot"], "Assistant/workbench result retrieval diverged from the stored snapshot")
        require(len(conversation_results()) == 1, "Query confirmation created duplicate conversation results")
        report["passed"].append("real alias-based Chinese question -> persisted query preview -> explicit confirmation -> actual descending 90/80 SQL result")
        report["passed"].append("repeat confirmation and result retrieval share one result ID and query snapshot")
        report["query_result_id"] = result["id"]

        description = "虚构反馈验收说明-" + stamp + "：仅供功能测试，不能作为公司业务依据。"
        metadata_plan = ask(f"请将当前虚构数据表的说明精确修改为引号内文字：“{description}”。只修改表说明，字段和数据行保持原状；先展示修改前后预览，等待我确认。", "metadata")
        before_metadata = get_table(table_id)
        require(before_metadata["description"] == baseline["description"] and before_metadata["version"] == baseline["version"], "Metadata changed before explicit confirmation")
        require(row_snapshot(before_metadata) == original_rows and len(conversation_results()) == 1, "Metadata planning changed rows or saved an unconfirmed result")
        require(metadata_plan["plan"]["metadata"].get("description") == description, "Real model did not preserve the user's literal table description")
        metadata_confirmed = post(f"/api/plans/{metadata_plan['id']}/confirm")
        after_metadata = get_table(table_id)
        require(after_metadata["description"] == description and after_metadata["description_provenance"] == "user", "Confirmed literal description did not retain user provenance")
        require(after_metadata["version"] == before_metadata["version"] + 1, "Metadata confirmation did not increment the table version once")
        require(row_snapshot(after_metadata) == original_rows and after_metadata["fields"] == before_metadata["fields"], "Table description update unexpectedly modified fields or synthetic rows")
        repeated_metadata = post(f"/api/plans/{metadata_plan['id']}/confirm")
        require(repeated_metadata["result"]["id"] == metadata_confirmed["result"]["id"], "Repeated metadata confirmation created another result")
        require(get_table(table_id)["version"] == after_metadata["version"], "Repeated metadata confirmation incremented the version again")
        history = checked(client.get("/api/history?limit=100"))["operations"]
        require(sum(entry.get("plan_id") == query_plan["id"] and entry["action"] == "query" for entry in history) == 1,
            "Query audit was missing or duplicated")
        require(sum(entry.get("plan_id") == metadata_plan["id"] and entry["action"] == "metadata_update" for entry in history) == 1,
            "Metadata audit was missing or duplicated")
        report["passed"].append("real Chinese literal metadata request -> preview -> confirmation -> user provenance and one version increment")
        report["passed"].append("synthetic rows and fields retained; query and metadata audit committed once")
        report.update(status="passed", metadata_result_id=metadata_confirmed["result"]["id"],
            questions_completed=len(report["calls"]), adapter_retries="according to server configuration")
    except Exception as exc:
        if report["status"] != "aborted":
            report["status"] = "failed"
        report["error"] = str(exc) if isinstance(exc, SafeFailure) else f"{type(exc).__name__}: verification failed; raw exception text withheld"
    finally:
        REPORT_PATH.parent.mkdir(exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        client.close()
    print(json.dumps({"status": report["status"], "passed": len(report["passed"]), "calls": report["calls"],
        **({"error": report["error"]} if report.get("error") else {})}, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
