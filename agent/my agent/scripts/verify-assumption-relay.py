"""真实云渡口径建议验收：只创建虚构 FRR/FAR 样例，先建议再跟进确认。

输出脱敏报告，不打印凭据或供应商原文；每次运行保留文件、表与对话。
"""

import json
import math
import os
import time
from pathlib import Path
import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "test-results" / "assumption-relay-report.json"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    report = {"status": "running", "data": "new synthetic FRR/FAR only", "questions": 0, "passed": [], "calls": []}
    with httpx.Client(base_url=os.getenv("VIBE_URL", "http://localhost:8080"), timeout=20, follow_redirects=False) as client:
        def checked(response):
            require(response.status_code == 200, f"Application HTTP {response.status_code}; raw response withheld")
            return response.json()
        try:
            ready = checked(client.get("/api/ready"))
            require(ready.get("model_mode") == "openai_responses", "Real Responses service is required; no request sent")
            config = dotenv_values(ROOT / ".env")
            require(str(config.get("MODEL_SEND_SAMPLES", "false")).lower() == "false", "Disable raw sample sending before verification")
            credentials = dotenv_values(ROOT / ".env.acceptance")
            me = checked(client.post("/api/auth/login", json={"username": credentials["USERNAME"], "password": credentials["PASSWORD"]}))
            csrf = {"X-CSRF-Token": me["csrf_token"]}
            def post(path, body=None, **kwargs):
                return checked(client.post(path, headers=csrf, **({"json": body} if body is not None else {}), **kwargs))
            def get(path):
                return checked(client.get(path))
            stamp = str(time.time_ns())
            title = "虚构FRR口径验收-" + stamp
            csv = "threshold,unlock_threshold,match_score_threshold,frr,far\n0.1,0.8,0.6,0.01,0.002\n0.2,0.85,0.7,0.02,0.002\n0.3,0.9,0.8,0.04,0.0001\n0.4,0.95,0.9,0.03,0.00005\n0.5,0.99,1,0.01,\n"
            upload = post("/api/files", files={"file": (title + ".csv", csv.encode(), "text/csv")})
            report.update(file_id=upload["file_id"], task_id=upload["task_id"])
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                task = get(f"/api/tasks/{upload['task_id']}")
                if task["status"] == "completed":
                    break
                require(task["status"] != "failed", "Synthetic parsing failed")
                time.sleep(0.5)
            else:
                raise RuntimeError("Synthetic parsing timeout")
            preview = get(f"/api/imports/{upload['task_id']}")
            # 通用解析器会保留无表头备用候选；按人工标注核对字段后只选正确候选，不更改解析规则。
            expected_names = {"threshold", "unlock_threshold", "match_score_threshold", "frr", "far"}
            candidates = [item for item in preview["tables"] if {field["name"] for field in item["fields"]} == expected_names]
            require(len(candidates) == 1, "Synthetic CSV annotated header candidate mismatch")
            source = candidates[0]
            imported = post(f"/api/imports/{upload['task_id']}/confirm", {"version": preview["version"], "tables": [{
                "candidate_id": source["candidate_id"], "name": title, "description": "人工标注虚构率值，不是公司业务数据", "fields": source["fields"], "reviewed": True}]})
            table_id = imported["table_ids"][0]
            table = get(f"/api/tables/{table_id}")
            fields = {field["name"]: field["id"] for field in table["fields"]}
            require(set(fields) == {"threshold", "unlock_threshold", "match_score_threshold", "frr", "far"}, "Synthetic field registry mismatch")
            require(all(not field["unit"] for field in table["fields"]), "Fixture must reproduce missing registered units")
            conversation = post("/api/conversations", {"title": title})["id"]
            report.update(table_id=table_id, conversation_id=conversation, file_id=upload["file_id"], model="openai_responses", raw_samples_sent=False)
            def ask(question):
                report["questions"] += 1
                print(f"Real assumption question {report['questions']}/2: waiting for preview...", flush=True)
                started = time.monotonic()
                run = post(f"/api/conversations/{conversation}/runs", {"content": question, "table_ids": [table_id]})
                while time.monotonic() - started < 420:
                    run = get(f"/api/runs/{run['id']}")
                    if run["status"] == "completed":
                        break
                    require(run["status"] not in {"failed", "cancelled"}, "Real planning did not complete; raw model details withheld")
                    time.sleep(0.75)
                else:
                    raise RuntimeError("Real planning exceeded 420-second polling limit")
                response = run.get("response") or {}
                require(response.get("plan_source") == "openai_responses", "Actual model adapter not reported")
                require(response.get("plan") and not response.get("result"), "Expected preview-only proposal instead of repeated interrogation or execution")
                plan = response["plan"]
                structured = plan["plan"]
                require(structured["operation"] == "select" and structured.get("extremum") == {"field_id": fields["far"], "direction": "min"}, "Optimal FAR must preserve corresponding minimum rows")
                filters = [item for item in structured["filters"] if item["field_id"] == fields["frr"]]
                require(len(filters) == 1 and filters[0]["op"] == "lt" and math.isclose(filters[0]["value"], 0.03), "3% must become strict frr < 0.03 in ratio storage")
                require(structured.get("assumptions") and "待确认" in plan["intent"], "Missing readable persisted assumptions")
                report["calls"].append({"seconds": round(time.monotonic() - started, 2), "run_id": run["id"], "plan_id": plan["id"]})
                return plan
            first = ask("frr<3%的情况下，far的最优值和对应的阈值是多少？")
            require({fields[name] for name in ("threshold", "unlock_threshold", "match_score_threshold", "far")}.issubset(set(first["plan"]["fields"])), "Ambiguous threshold must propose showing all registered candidates")
            require(get(f"/api/conversations/{conversation}")["results"] == [], "A suggested interpretation executed before confirmation")
            report["passed"].append("ambiguous original question yields evidence-backed ratio/minimum/all-threshold preview, no query result yet")
            second = ask("对应的阈值指的是unlock_threshold（c2），far的最优值是指最小值，frr字段的存储单位：小数比例。")
            require(fields["unlock_threshold"] in second["plan"]["fields"] and fields["far"] in second["plan"]["fields"], "Followup lost its requested threshold or FAR")
            require(fields["threshold"] not in second["plan"]["fields"] and fields["match_score_threshold"] not in second["plan"]["fields"], "Followup did not narrow the threshold selection")
            require(get(f"/api/plans/{first['id']}")["status"] == "cancelled", "Followup did not invalidate old unconfirmed preview")
            require(client.post(f"/api/plans/{first['id']}/confirm", headers=csrf).status_code == 409, "Old preview was executable")
            result = post(f"/api/plans/{second['id']}/confirm")["result"]
            require([row[fields["unlock_threshold"]] for row in result["rows"]] == [0.8, 0.85], "Actual SQL did not retain both annotated optimum thresholds")
            require(all(math.isclose(row[fields["far"]], 0.002) for row in result["rows"]), "Actual FAR minimum mismatch")
            repeated = post(f"/api/plans/{second['id']}/confirm")["result"]
            require(repeated["id"] == result["id"] and repeated["query_snapshot"] == result["query_snapshot"], "Repeated confirmation changed the snapshot")
            after = get(f"/api/tables/{table_id}")
            require(after["fields"] == table["fields"] and after["version"] == table["version"] and after["rows"] == table["rows"], "Interpretation mutated source data or metadata")
            report["passed"].extend(["explicit followup changes preview without unit error, old plan cancelled", "actual scoped SQL returns FAR 0.002 and both unlock thresholds 0.8/0.85", "repeat confirmation shares snapshot, source records and metadata unchanged"])
            report.update(status="passed", first_plan_id=first["id"], plan_id=second["id"], result_id=result["id"])
        except Exception as error:
            report["status"] = "failed"
            report["error"] = str(error) if isinstance(error, RuntimeError) else f"{type(error).__name__}; raw error text withheld"
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "questions", "passed", "calls", "error") if key in report}, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
