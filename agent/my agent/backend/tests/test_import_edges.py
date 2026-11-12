"""导入修正边界：分页、互斥结构、汇总唯一性与来源标注。"""
import os
from pathlib import Path
import pytest
from sqlalchemy import select, update
from app import db, jobs
from test_api import Account


@pytest.fixture(autouse=True)
def isolated_database():
    if os.getenv("VIBE_TEST_DATABASE") != "true" or db.engine.url.database != "vibe_test":
        pytest.skip("requires isolated database")


def parsed(owner, name, content):
    uploaded = owner.post("/api/files", files={"file": (name, content, "application/octet-stream")})
    assert uploaded.status_code == 200, uploaded.text
    saved = uploaded.json()
    jobs.run_task(saved["task_id"], owner.id)
    response = owner.client.get(f"/api/imports/{saved['task_id']}")
    assert response.status_code == 200, response.text
    return saved, response.json()


def selection(candidate, **changes):
    return {"candidate_id": candidate["candidate_id"], "name": candidate["name"], "description": candidate["description"],
        "fields": candidate["fields"], "reviewed": True, **changes}


def test_preview_paging_corrects_beyond_first_window():
    owner = Account()
    data = "学号,成绩\n" + "\n".join(f"{i:05d},{i}" for i in range(450))
    saved, preview = parsed(owner, "large.csv", data.encode())
    candidate = preview["tables"][0]
    assert candidate["row_count"] == 450 and len(candidate["rows"]) == 200
    assert len(candidate["original"]["rows"]) == 200
    paged = owner.client.get(f"/api/imports/{saved['task_id']}?candidate_id={candidate['candidate_id']}&offset=200&limit=200").json()["tables"][0]
    assert paged["rows"][0]["values"]["c1"] == "00200" and paged["row_offset"] == 200
    imported = owner.post(f"/api/imports/{saved['task_id']}/confirm", json={"version": preview["version"],
        "tables": [selection(candidate, corrections=[{"row_index": 220, "field_id": "c2", "value": 999}])]})
    assert imported.status_code == 200, imported.text
    table_id = imported.json()["table_ids"][0]
    with db.engine.connect() as conn:
        value = conn.scalar(select(db.data_rows.c["values"]).where(db.data_rows.c.table_id == table_id, db.data_rows.c["values"]["c1"].astext == "00220"))
    assert value["c2"] == 999


def test_ambiguous_structure_cannot_import_both_alternatives():
    owner = Account()
    saved, preview = parsed(owner, "ambiguous.csv", b"Alpha,Beta\nFoo,Bar\n")
    assert len(preview["tables"]) == 2
    invalid = owner.post(f"/api/imports/{saved['task_id']}/confirm", json={"version": preview["version"], "tables": [selection(c) for c in preview["tables"]]})
    assert invalid.status_code == 422
    assert owner.client.get("/api/catalog").json()["tables"] == []
    body = {"version": preview["version"], "tables": [selection(preview["tables"][1])]}
    response = owner.post(f"/api/imports/{saved['task_id']}/confirm", json=body)
    assert response.status_code == 200
    assert owner.client.get(f"/api/tables/{response.json()['table_ids'][0]}").json()["total"] == 2


def test_grain_relations_unique_summary_and_mutation_rollback():
    owner = Account()
    sample = Path(__file__).parent / "fixtures" / "grains.xlsx"
    saved, preview = parsed(owner, "综合评价.xlsx", sample.read_bytes())
    confirmed = owner.post(f"/api/imports/{saved['task_id']}/confirm", json={"version": preview["version"], "tables": [selection(c) for c in preview["tables"]]})
    assert confirmed.status_code == 200, confirmed.text
    tables = owner.client.get("/api/catalog").json()["tables"]
    student = next(t for t in tables if t["structure"]["grain"] == "学生汇总")
    assert len(student["structure"]["relations"]) == 2
    rows = owner.client.get(f"/api/tables/{student['id']}").json()["rows"]
    original = next(row for row in rows if row["values"]["c1"] is not None)
    conversation = owner.post("/api/conversations", json={}).json()["id"]
    target = {"id": student["id"], "conversation_id": conversation}
    previewed = owner.ask(target, {"operation": "insert", "values": original["values"]})
    assert previewed.status_code == 200
    plan_id = previewed.json()["plan"]["id"]
    rejected = owner.post(f"/api/plans/{plan_id}/confirm")
    assert rejected.status_code == 422
    assert owner.client.get(f"/api/tables/{student['id']}").json()["total"] == len(rows)
    assert owner.client.get(f"/api/plans/{plan_id}").json()["status"] == "pending"


def test_changed_description_cannot_claim_file_origin():
    owner = Account()
    table = owner.table()
    edited = owner.patch(f"/api/tables/{table['id']}", json={"version": 1, "name": "成绩", "description": "用户新增描述",
        "description_provenance": "file", "fields": table["fields"]})
    assert edited.status_code == 200
    assert edited.json()["description_provenance"] == "user"
    repeated = owner.patch(f"/api/tables/{table['id']}", json={"version": 2, "name": "成绩", "description": "用户新增描述",
        "description_provenance": "file", "fields": edited.json()["fields"]})
    assert repeated.status_code == 200
    assert repeated.json()["description_provenance"] == "user"


def test_inferred_types_cannot_be_relabelled_as_file():
    from app import jobs
    owner = Account()
    uploaded = owner.post('/api/files', files={'file': ('scores.csv', '学号,成绩\n001,90\n'.encode(), 'text/csv')}).json()
    jobs.run_task(uploaded['task_id'], owner.id)
    preview = owner.client.get(f"/api/imports/{uploaded['task_id']}").json()
    candidate = preview['tables'][0]
    score = next(field for field in candidate['fields'] if field['name'] == '成绩')
    assert score['type_provenance'] == 'inference'
    score['type_provenance'] = 'file'
    confirmed = owner.post(f"/api/imports/{uploaded['task_id']}/confirm", json={'version': preview['version'], 'tables': [{
        'candidate_id': candidate['candidate_id'], 'name': candidate['name'], 'fields': candidate['fields'], 'reviewed': True} ]})
    assert confirmed.status_code == 200
    table_id = confirmed.json()['table_ids'][0]
    table = owner.client.get(f'/api/tables/{table_id}').json()
    imported_score = next(field for field in table['fields'] if field['name'] == '成绩')
    assert imported_score['type_provenance'] == 'confirmed_inference'
    imported_score['type_provenance'] = 'file'
    edited = owner.patch(f'/api/tables/{table_id}', json={'version': table['version'], 'name': table['name'], 'fields': table['fields']})
    assert edited.status_code == 200
    assert next(field for field in edited.json()['fields'] if field['name'] == '成绩')['type_provenance'] == 'confirmed_inference'


def test_same_named_scientific_csv_uses_selected_table_and_returns_all_extremum_ties():
    owner = Account()
    header = "threshold,unlock_threshold,match_score_threshold,far,frr\n"

    def import_rates(content):
        saved, preview = parsed(owner, "far_frr_thresholds_0.50_0.85.csv", (header + content).encode())
        candidate = next(table for table in preview["tables"]
            if [field["name"] for field in table["fields"]] == header.strip().split(","))
        assert candidate["fields"][3]["type"] == "number"
        confirmed = owner.post(f"/api/imports/{saved['task_id']}/confirm", json={"version": preview["version"],
            "tables": [selection(candidate)]})
        assert confirmed.status_code == 200, confirmed.text
        table_id = confirmed.json()["table_ids"][0]
        conversation = owner.post("/api/conversations", json={}).json()["id"]
        return {"id": table_id, "conversation_id": conversation}

    wrong_table = import_rates("0.5,0.5,0.5,0.0006854,0.5773\n")
    correct_table = import_rates(
        "0.5,0.5,0.5,0.0037,0.006434\n"
        "0.77,0.77,0.77,3.575e-05,0.02574\n"
        "0.78,0.78,0.78,1.787e-05,0.02681\n"
        "0.79,0.79,0.79,1.787E-05,0.02681\n"
        "0.8,0.8,0.8,0.00001787,0.02842\n"
        "0.81,0.81,0.81,1.787e-05,0.05523\n"
        "0.82,0.82,0.82,0,0.03\n"
        "0.85,0.85,0.85,0,0.1662\n")
    assert wrong_table["id"] != correct_table["id"]

    def query_rates(table):
        response = owner.ask(table, {"operation": "select", "fields": ["c2", "c4", "c5"],
            "filters": [{"field_id": "c5", "op": "lt", "value": 3, "unit": "%", "storage_unit": "ratio"}],
            "extremum": {"field_id": "c4", "direction": "min"}})
        assert response.status_code == 200, response.text
        assert "result" not in response.json()
        confirmed = owner.post(f"/api/plans/{response.json()['plan']['id']}/confirm")
        assert confirmed.status_code == 200, confirmed.text
        return confirmed.json()["result"]

    # 同名不代表同一数据源；仅按明确的表 ID 查询，不能混入其他表或过滤范围外的 FAR=0。
    empty = query_rates(wrong_table)
    assert empty["rows"] == []
    assert empty["query_snapshot"]["extremum_diagnostic"]["reason"] == "no_matching_rows"
    result = query_rates(correct_table)
    assert result["table_id"] == correct_table["id"]
    assert [row["c2"] for row in result["rows"]] == [0.78, 0.79, 0.8]
    assert [row["c4"] for row in result["rows"]] == [0.00001787] * 3
    assert [row["c5"] for row in result["rows"]] == [0.02681, 0.02681, 0.02842]
    assert result["query_snapshot"]["plan"]["filters"][0]["value"] == 0.03
