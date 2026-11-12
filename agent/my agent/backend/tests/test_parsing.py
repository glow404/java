"""文件结构与来源回归测试：比对独立人工标注，不依赖硬编码文件行数。"""
import json
from pathlib import Path
import pytest
from openpyxl import Workbook
from app.parsing import parse_file, ParseError, csv_safe

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


def test_explicit_merges_and_outside_blanks():
    preview = parse_file(FIXTURES / "merged_headers.xlsx", "source-file", "parse-task")
    tables = {table["name"]: table for table in preview["tables"]}
    table = tables["成绩明细"]
    assert [row["values"] for row in table["rows"]] == EXPECTED["merged_headers"]["成绩明细"]
    assert table["row_count"] == len(EXPECTED["merged_headers"]["成绩明细"])
    assert table["rows"][1]["source"]["cells"]["c1"]["merge_range"] == "A3:A4"
    assert table["rows"][1]["source"]["cells"]["c1"]["basis"] == "explicit_vertical_merge"
    assert table["rows"][2]["source"]["needs_identity_review"] is True
    assert table["rows"][0]["source"]["worksheet"] == "成绩明细"
    assert table["rows"][0]["source"]["original_row"] == 3
    assert table["rows"][0]["source"]["file_id"] == "source-file"
    assert table["rows"][0]["source"]["task_id"] == "parse-task"
    formula = table["rows"][0]["source"]["cells"]["c5"]
    assert formula["formula"] == "=D3+5" and formula["cached_value"] is None
    assert formula["cache_available"] is False
    assert any("公式缓存缺失" in warning for warning in table["warnings"])
    assert "2026 秋季成绩（分）" in table["original"]["titles"]
    assert [field["name"] for field in tables["多级表头"]["fields"]] == EXPECTED["merged_headers"]["多级表头_names"]
    assert tables["多级表头"]["fields"][1]["unit"] == "分"


def test_grain_splitting_does_not_duplicate_totals_or_merge_names():
    preview = parse_file(FIXTURES / "grains.xlsx")
    tables = {table["grain"]: table for table in preview["tables"]}
    for grain, expected_rows in EXPECTED["grains"].items():
        assert [row["values"] for row in tables[grain]["rows"]] == expected_rows
        assert tables[grain]["row_count"] == len(expected_rows)
        assert tables[grain]["requires_review"] is True
    assert sum(row["values"]["c3"] for row in tables["学生汇总"]["rows"]) == 177
    assert tables["加分明细"]["rows"][-1]["source"]["needs_identity_review"]
    assert all("c3" not in row["values"] and "c5" not in row["values"] for row in tables["加分明细"]["rows"])


def test_csv_identifier_empty_score_and_encoding(tmp_path):
    table = parse_file(FIXTURES / "leading_zero.csv")["tables"][0]
    assert [row["values"] for row in table["rows"]] == EXPECTED["csv"]
    assert table["fields"][0]["type"] == "string"
    assert table["fields"][1]["type"] == "integer"
    chinese = tmp_path / "gb.csv"
    chinese.write_bytes("学号,成绩\n0001,89\n".encode("gb18030"))
    assert parse_file(chinese)["tables"][0]["rows"][0]["values"]["c1"] == "0001"


def test_csv_scientific_numbers_preserve_identifiers_and_original_values(tmp_path):
    path = tmp_path / "scientific.csv"
    raw_values = ["0.0037", "8.937e-05", "1.787E-05", ".5e+2", "-2E+1", "+3e0"]
    path.write_text("学号,编码,far\n" + "\n".join(
        f"{index:05d},{index:04d},{value}" for index, value in enumerate(raw_values, 1)), encoding="utf-8")
    table = parse_file(path)["tables"][0]
    assert [field["type"] for field in table["fields"]] == ["string", "string", "number"]
    assert [row["values"]["c1"] for row in table["rows"]] == [f"{index:05d}" for index in range(1, 7)]
    assert [row["values"]["c2"] for row in table["rows"]] == [f"{index:04d}" for index in range(1, 7)]
    assert [row["values"]["c3"] for row in table["rows"]] == [0.0037, 0.00008937, 0.00001787, 50, -20, 3]
    assert [row["values"][2] for row in table["original"]["rows"][1:]] == raw_values
    assert table["fields"][2]["type_provenance"] == "inference"


@pytest.mark.parametrize("value", ["1e309", "-1E309", "9" * 400, "NaN", "Infinity", "1e", "1e+", "e3"])
def test_csv_nonfinite_or_invalid_numbers_remain_text(tmp_path, value):
    path = tmp_path / "invalid-number.csv"
    path.write_text(f"指标名称,数值\n样本1,0.0037\n样本2,{value}\n", encoding="utf-8")
    table = parse_file(path)["tables"][0]
    assert table["fields"][1]["type"] == "string"
    assert [row["values"]["c2"] for row in table["rows"]] == ["0.0037", value]


def test_docx_multiple_tables_and_nearby_description():
    preview = parse_file(FIXTURES / "tables.docx")
    assert len(preview["tables"]) == 2
    first, second = preview["tables"]
    assert "教务系统" in first["description"]
    assert first["rows"][1]["values"]["c3"] is None
    assert first["rows"][0]["source"]["document_table"] == 1
    assert second["rows"][0]["values"]["c1"] == "000009"
    assert second["rows"][0]["source"]["document_table"] == 2


def test_docling_real_text_pdf_and_scanned_rejection():
    table = parse_file(FIXTURES / "text_table.pdf")["tables"][0]
    assert [row["values"] for row in table["rows"]] == EXPECTED["pdf"]
    assert table["rows"][0]["source"]["page"] == 1
    assert table["rows"][0]["source"]["parser"] == "docling_native"
    assert table["structure_uncertain"]
    with pytest.raises(ParseError, match="扫描"):
        parse_file(FIXTURES / "no_text.pdf")


def test_ambiguous_identity_and_conflicting_totals_pause(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(["姓名", "学生总分", "一级指标", "一级指标总分", "明细"])
    sheet.append(["同名", 90, "体育", 30, "活动"])
    path = tmp_path / "unknown.xlsx"
    book.save(path)
    table = parse_file(path)["tables"][0]
    assert table["structure_uncertain"]
    assert any("没有可靠身份" in warning for warning in table["warnings"])
    sheet.insert_cols(1)
    sheet["A1"] = "学号"
    sheet["A2"] = "001"
    sheet.append(["001", "同名", 92, "体育", 30, "另活动"])
    book.save(path)
    table = parse_file(path)["tables"][0]
    assert table["structure_uncertain"]
    assert any("冲突总分" in warning for warning in table["warnings"])


def test_no_automatic_fill_without_merge(tmp_path):
    file = tmp_path / "no-merge.csv"
    file.write_text("用户,学科,成绩\n用户1,学科1,80\n,学科2,90\n", encoding="utf-8")
    rows = parse_file(file)["tables"][0]["rows"]
    assert rows[1]["values"]["c1"] is None


def test_headerless_merged_first_record_is_never_lost(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(["用户1", "学科1", "成绩A"])
    sheet.append([None, "学科2", "成绩B"])
    sheet.append([None, "学科3", "成绩C"])
    sheet.merge_cells("A1:A2")
    path = tmp_path / "headerless.xlsx"
    book.save(path)
    table = parse_file(path)["tables"][0]
    assert [row["values"] for row in table["rows"]] == [
        {"c1": "用户1", "c2": "学科1", "c3": "成绩A"},
        {"c1": "用户1", "c2": "学科2", "c3": "成绩B"},
        {"c1": None, "c2": "学科3", "c3": "成绩C"},
    ]
    assert table["original"]["header_rows"] == []
    assert table["structure_uncertain"]
    assert all(row["source"]["needs_structure_review"] for row in table["rows"])
    assert all(field["type_provenance"] == "inference" for field in table["fields"])


def test_pure_text_ambiguous_header_has_full_first_row_alternative(tmp_path):
    path = tmp_path / "text.csv"
    path.write_text("Alice,Math,Excellent\nBob,History,Good\n", encoding="utf-8")
    candidates = parse_file(path)["tables"]
    assert len(candidates) == 2
    assert candidates[0]["mutually_exclusive_group"] == candidates[1]["mutually_exclusive_group"]
    assert candidates[1]["rows"][0]["values"] == {"c1": "Alice", "c2": "Math", "c3": "Excellent"}
    assert candidates[1]["row_count"] == 2


def test_numeric_merged_total_not_repeated_and_formula_only_row_kept(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(["学号", "总分", "公式成绩"])
    sheet.append(["001", 90, None])
    sheet.append(["002", None, None])
    sheet.merge_cells("B2:B3")
    sheet.append([None, None, "=1+1"])
    path = tmp_path / "totals.xlsx"
    book.save(path)
    table = parse_file(path)["tables"][0]
    assert table["rows"][1]["values"]["c2"] is None
    assert table["rows"][2]["source"]["cells"]["c3"]["formula"] == "=1+1"


def test_xls_and_invalid_archives_fail_explicitly(tmp_path):
    with pytest.raises(ParseError, match="另存为 xlsx"):
        parse_file(tmp_path / "old.xls")
    fake = tmp_path / "fake.xlsx"
    fake.write_text("not a spreadsheet")
    with pytest.raises(ParseError, match="有效"):
        parse_file(fake)


@pytest.mark.parametrize("value", ["=1+1", "+cmd", "-cmd", "@SUM(A1)", "  =1", "\t=1"])
def test_export_formula_injection_escaped(value):
    assert csv_safe(value).startswith("'")


def test_export_regular_values():
    assert csv_safe(None) == ""
    assert csv_safe("00017") == "00017"
    assert csv_safe(90) == "90"
