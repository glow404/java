"""受控文件解析：将原始结构转换为待用户核对的候选表，保留逐行来源。

不执行公式、宏或模型代码；不以姓名合并身份，不自动填充无合并依据的空格。
"""
from __future__ import annotations

import csv
import io
import math
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

MAX_ROWS = 100_000
MAX_COLUMNS = 200
MAX_CELLS = 500_000
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
IDENTITY = re.compile(r"学号|学生编号|人员编号|订单号|编号|身份证|手机号|(^|[/ _])id($|[/ _])", re.I)
OWNERSHIP = re.compile(r"^(?:用户|姓名|学生姓名|学生|人员)$")
SUMMARY = re.compile(r"总分|汇总|总计|合计")
# FAR 等小比例常用科学计数法；识别指数可避免整列被误判为文本。
NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
HEADER = re.compile(r"^(?:学号|姓名|学生姓名|用户|学科|成绩|公式成绩|订单号|金额|数量|班级|日期|时间|学生总分|综合总分|总分|一级指标|指标名称|一级指标总分|指标总分|加分项目|加分分值|明细|student[_ ]?id|score|name|class|date|amount|order[_ ]?id)$", re.I)


class ParseError(ValueError):
    """可安全展示给用户的解析错误。"""


def _safe_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_ARCHIVE_BYTES:
                raise ParseError("解压后文件过大，请拆分后上传（上限 100 MB）。")
            if any(item.flag_bits & 1 for item in archive.infolist()):
                raise ParseError("不支持加密文档，请解密后上传。")
    except zipfile.BadZipFile as exc:
        raise ParseError("文档不是有效的 xlsx/docx 压缩结构。") from exc


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, str):
        return value.strip() or None
    return value


def _cell_value(cell: Any) -> Any:
    value = _json_value(cell.value)
    # Excel 以数值保存但显示为 00000 的编号必须保留显示前导零。
    if isinstance(value, (int, float)) and float(value).is_integer() and re.fullmatch(r"0{2,}", cell.number_format):
        return str(int(value)).zfill(len(cell.number_format))
    return value


def _type(name: str, values: list[Any]) -> str:
    present = [value for value in values if value is not None]
    if not present or IDENTITY.search(name):
        return "string"
    if all(isinstance(v, bool) for v in present):
        return "boolean"
    if all(isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in present):
        return "date"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in present):
        return "integer" if all(float(v).is_integer() for v in present) else "number"
    # JSON 不支持无限值；1e309 等溢出数值保持原文，留待预览核对。
    if all(isinstance(v, str) and NUMBER.fullmatch(v) and math.isfinite(float(v)) for v in present):
        # 名称不明显的编码也不应被数值推断吞掉前导零。
        if any(re.fullmatch(r"[+-]?0\d+", v) for v in present):
            return "string"
        return "integer" if all(re.fullmatch(r"[+-]?\d+", v) for v in present) else "number"
    return "string"


def _coerce(value: Any, kind: str) -> Any:
    if value is None:
        return None
    if kind == "string":
        return str(value)
    if kind == "integer":
        return int(value)
    if kind == "number":
        return float(value)
    return value


def _candidate(
    matrix: list[list[Any]], numbers: list[int], *, name: str, source: dict[str, Any],
    candidate_id: str, merges: list[dict[str, Any]] | None = None,
    cells: dict[tuple[int, int], dict[str, Any]] | None = None,
    description: str = "", warnings: list[str] | None = None,
    header_mode: str = "auto",
) -> dict[str, Any]:
    if not matrix or not any(any(v is not None for v in row) for row in matrix):
        raise ParseError("表格没有可用内容。")
    width = max(len(row) for row in matrix)
    if width > MAX_COLUMNS or len(matrix) > MAX_ROWS or width * len(matrix) > MAX_CELLS:
        raise ParseError("表格超出第一版解析上限：100000 行、200 列或 500000 单元格，请拆分。")
    matrix = [row + [None] * (width - len(row)) for row in matrix]
    merges = merges or []
    cells = cells or {}
    notices = list(warnings or [])
    titles: list[str] = []
    header_start = 0
    # 顶部单值行是标题/邻近描述；保留在 original，而不计入明细记录。
    while header_start < len(matrix) - 1 and sum(v is not None for v in matrix[header_start]) == 1 and width > 1 and (any(m["min_row"] == numbers[header_start] and m["max_col"] - m["min_col"] + 1 >= width for m in merges) or any(HEADER.fullmatch(re.sub(r"[（(].*?[)）]", "", str(v))) for v in matrix[header_start + 1] if v is not None)):
        titles.append(str(next(v for v in matrix[header_start] if v is not None)))
        header_start += 1
    first_values = [v for v in matrix[header_start] if v is not None]
    recognized_header = any(HEADER.fullmatch(re.sub(r"[（(].*?[)）]", "", str(v))) for v in first_values)
    all_labels = all(isinstance(v, str) and not any(char.isdigit() for char in v) for v in first_values)
    has_header = header_mode != "none" and (recognized_header or all_labels)
    ambiguous_header = has_header and not recognized_header and header_mode == "auto"
    header_end = header_start if has_header else header_start - 1
    if not has_header:
        notices.append("未识别可靠表头，首行作为数据保留；请对照原文为未命名字段输入名称、类型和归属。")
    elif ambiguous_header:
        notices.append("首行可能是表头或文本数据；已提供保留首行的无表头备用候选，只选择一种结构。")
    # 多级表头只依据明确的横向合并/跨表头纵向合并；无依据则候选单层，要求核对。
    header_row_number = numbers[header_start]
    header_merges = [m for m in merges if m["min_row"] == header_row_number and (m["max_col"] > m["min_col"] or m["max_row"] > m["min_row"])]
    if has_header and header_merges and header_start + 1 < len(matrix):
        next_row = matrix[header_start + 1]
        if any(isinstance(v, str) and not NUMBER.fullmatch(v) for v in next_row if v is not None):
            last_header = max([header_row_number + 1] + [m["max_row"] for m in header_merges])
            header_end = max((i for i, n in enumerate(numbers) if n <= last_header), default=header_start)
    header_matrix = [list(row) for row in matrix[header_start:header_end + 1]]
    for merge in merges:
        if not has_header or merge["min_row"] < header_row_number or merge["max_row"] > numbers[header_end]:
            continue
        anchor = matrix[numbers.index(merge["min_row"])][merge["min_col"] - 1]
        for r in range(header_start, header_end + 1):
            for c in range(merge["min_col"] - 1, merge["max_col"]):
                if merge["min_row"] <= numbers[r] <= merge["max_row"]:
                    header_matrix[r - header_start][c] = anchor
    fields = []
    for col in range(width):
        parts = list(dict.fromkeys(str(row[col]) for row in header_matrix if row[col] is not None))
        label = " / ".join(parts) if parts else f"未命名字段{col + 1}"
        if not parts:
            notices.append(f"第 {col + 1} 列无字段名，请核对。")
        unit_match = re.search(r"[（(]([^()（）]+)[)）]$", label)
        fields.append({"id": f"c{col + 1}", "name": label, "type": "string", "unit": unit_match.group(1) if unit_match else "", "description": "", "provenance": "file" if parts else "user", "type_provenance": "inference"})
    rows = []
    for index in range(header_end + 1, len(matrix)):
        row = list(matrix[index])
        if all(v is None for v in row) and not any(r == numbers[index] for r, _ in cells):
            continue
        row_source = {**source, "original_row": numbers[index], "cells": {}}
        for col, field in enumerate(fields):
            metadata = dict(cells.get((numbers[index], col + 1), {}))
            for merge in merges:
                if merge["min_col"] == merge["max_col"] == col + 1 and merge["min_row"] <= numbers[index] <= merge["max_row"] and merge["min_row"] > (numbers[header_end] if has_header else 0):
                    metadata["merge_range"] = merge["range"]
                    if row[col] is None and not SUMMARY.search(field["name"]):
                        anchor = matrix[numbers.index(merge["min_row"])][col]
                        # 只展开明示合并的归属/类别；数字汇总不复制到明细后重复求和。
                        if isinstance(anchor, str) or IDENTITY.search(field["name"]):
                            row[col] = str(anchor) if anchor is not None else None
                            metadata["expanded_from"] = f"{merge['min_row']}:{col + 1}"
                            metadata["basis"] = "explicit_vertical_merge"
                    elif row[col] is None and SUMMARY.search(field["name"]):
                        notices.append(f"汇总字段 {field['name']} 的合并值未复制，需核对统计粒度。")
            if metadata:
                row_source["cells"][field["id"]] = metadata
        rows.append({"values": {field["id"]: row[i] for i, field in enumerate(fields)}, "source": row_source})
    for field in fields:
        kind = _type(field["name"], [r["values"][field["id"]] for r in rows])
        field["type"] = kind
        for row in rows:
            row["values"][field["id"]] = _coerce(row["values"][field["id"]], kind)
    for field in fields:
        if (IDENTITY.search(field["name"]) or OWNERSHIP.fullmatch(field["name"])) and any(r["values"][field["id"]] is None for r in rows):
            notices.append(f"{field['name']} 存在缺失身份，保持为空；不得仅凭姓名归并。")
            for row in rows:
                if row["values"][field["id"]] is None:
                    row["source"]["needs_identity_review"] = True
    original = {"rows": [{"row_number": n, "values": row} for n, row in zip(numbers, matrix)], "merges": merges, "titles": titles, "header_rows": numbers[header_start:header_end + 1], "source_row_count": len(matrix), "candidate_row_count": len(rows), "row_count_change": len(rows) - len(matrix)}
    candidate = {"candidate_id": candidate_id, "name": name, "description": description or "\n".join(titles), "fields": fields, "rows": rows, "original": original, "warnings": list(dict.fromkeys(notices)), "requires_review": True, "row_count": len(rows), "ambiguous_header": ambiguous_header}
    if not has_header or ambiguous_header:
        candidate["structure_uncertain"] = True
        for row in rows:
            row["source"]["needs_structure_review"] = True
    return candidate


def _header_alternatives(table: dict[str, Any], *, source: dict[str, Any], cells: dict | None = None) -> list[dict[str, Any]]:
    if not table.get("ambiguous_header"):
        return _split_grains(table)
    original = table["original"]
    alternative = _candidate([row["values"] for row in original["rows"]], [row["row_number"] for row in original["rows"]], name=table["name"] + "（首行保留为数据）", source=source, candidate_id=table["candidate_id"] + "_noheader", merges=original["merges"], cells=cells, description=table["description"], warnings=["这是同源表的无表头备用候选，不应与表头候选同时导入。"], header_mode="none")
    table["mutually_exclusive_group"] = table["candidate_id"]
    alternative["mutually_exclusive_group"] = table["candidate_id"]
    return [table, alternative]


def _split_grains(table: dict[str, Any]) -> list[dict[str, Any]]:
    fields = table["fields"]
    identity = next((f for f in fields if IDENTITY.search(f["name"])), None)
    student_total = next((f for f in fields if f["name"] in ("总分", "学生总分", "综合总分")), None)
    indicator = next((f for f in fields if f["name"] in ("一级指标", "指标名称")), None)
    indicator_total = next((f for f in fields if "一级指标总分" in f["name"] or f["name"] == "指标总分"), None)
    if not student_total or not indicator or not indicator_total:
        return [table]
    if identity is None:
        table["warnings"].append("存在学生/一级指标/明细不同粒度，但没有可靠身份字段。暂停归并；请补充编号并人工核对。")
        table["structure_uncertain"] = True
        return [table]
    idf = identity["id"]
    name_fields = [f for f in fields if f["name"] in ("姓名", "学生姓名")]
    student_fields = [identity, *name_fields, student_total]
    indicator_fields = [identity, indicator, indicator_total]
    detail_fields = [f for f in fields if f["id"] not in (student_total["id"], indicator_total["id"])]
    pieces = []
    for grain, selected in (("学生汇总", student_fields), ("一级指标汇总", indicator_fields), ("加分明细", detail_fields)):
        selected_ids = [f["id"] for f in selected]
        items: list[dict[str, Any]] = []
        seen: dict[tuple[Any, ...], dict[str, Any]] = {}
        conflicts = []
        for row in table["rows"]:
            values = row["values"]
            if grain != "加分明细":
                total = student_total if grain == "学生汇总" else indicator_total
                if values[total["id"]] is None:
                    continue
                key = (values[idf],) if grain == "学生汇总" else (values[idf], values[indicator["id"]])
                if values[idf] is None or (grain == "一级指标汇总" and values[indicator["id"]] is None):
                    # 身份不明汇总单独保留，不用姓名或上一行猜测。
                    key = (*key, row["source"]["original_row"])
                if key in seen:
                    if seen[key]["values"][total["id"]] != values[total["id"]]:
                        conflicts.append(f"{grain} 同编号存在冲突总分，需人工核对。")
                        return [{**table, "structure_uncertain": True, "warnings": table["warnings"] + conflicts}]
                    seen[key]["source"]["supporting_rows"].append(row["source"]["original_row"])
                    continue
            else:
                key = (row["source"]["original_row"],)
            item = {"values": {key: values[key] for key in selected_ids}, "source": {**row["source"], "grain": grain, "supporting_rows": [row["source"]["original_row"]]}}
            seen[key] = item
            items.append(item)
        pieces.append({**table, "candidate_id": f"{table['candidate_id']}:{grain}", "name": f"{table['name']} · {grain}", "fields": selected, "rows": items, "row_count": len(items), "warnings": table["warnings"] + ["不同粒度候选拆分需用户确认；每名学生/每项指标总分仅保存一次。"], "grain": grain,
            "source_group": table["candidate_id"], "identity_fields": [idf] + ([indicator["id"]] if grain == "一级指标汇总" else []),
            "original": {**table["original"], "candidate_row_count": len(items), "row_count_change": len(items) - table["original"]["source_row_count"]}})
    return pieces


def _xlsx(path: Path, file_id: str, task_id: str) -> list[dict[str, Any]]:
    from openpyxl import load_workbook
    _safe_archive(path)
    workbook = load_workbook(path, data_only=False, read_only=False, keep_links=False)
    cached = load_workbook(path, data_only=True, read_only=False, keep_links=False)
    tables = []
    try:
        for index, sheet in enumerate(workbook.worksheets):
            if sheet.max_row * sheet.max_column > MAX_CELLS or sheet.max_row > MAX_ROWS or sheet.max_column > MAX_COLUMNS:
                raise ParseError(f"工作表 {sheet.title} 超出解析上限，请拆分。")
            matrix, numbers, metadata, warnings = [], [], {}, []
            for row in sheet.iter_rows():
                values = []
                for cell in row:
                    if cell.data_type == "f":
                        value = _cell_value(cached[sheet.title][cell.coordinate])
                        metadata[(cell.row, cell.column)] = {"formula": cell.value, "cached_value": value, "cache_available": value is not None, "coordinate": cell.coordinate}
                        if value is None:
                            warnings.append(f"{cell.coordinate} 公式缓存缺失，未计算；请用 Excel 重算保存或人工修正。")
                    else:
                        value = _cell_value(cell)
                    values.append(value)
                matrix.append(values)
                numbers.append(row[0].row)
            if not any(any(v is not None for v in row) for row in matrix):
                continue
            merges = [{"range": str(m), "min_row": m.min_row, "max_row": m.max_row, "min_col": m.min_col, "max_col": m.max_col} for m in sheet.merged_cells.ranges]
            candidate = _candidate(matrix, numbers, name=sheet.title, source={"file_id": file_id, "task_id": task_id, "worksheet": sheet.title}, candidate_id=f"sheet{index + 1}", merges=merges, cells=metadata, warnings=warnings)
            tables.extend(_header_alternatives(candidate, source={"file_id": file_id, "task_id": task_id, "worksheet": sheet.title}, cells=metadata))
    finally:
        workbook.close()
        cached.close()
    return tables


def _csv(path: Path, file_id: str, task_id: str) -> list[dict[str, Any]]:
    data = path.read_bytes()
    text = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            pass
    if text is None or "\x00" in text:
        raise ParseError("CSV 编码无法识别，请另存为 UTF-8 CSV。")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    matrix = []
    numbers = []
    reader = csv.reader(io.StringIO(text), dialect)
    for row in reader:
        matrix.append([_json_value(v) for v in row])
        numbers.append(reader.line_num)
        if len(matrix) > MAX_ROWS:
            raise ParseError("CSV 超出 100000 行，请拆分。")
    candidate = _candidate(matrix, numbers, name=path.stem, source={"file_id": file_id, "task_id": task_id, "format": "csv"}, candidate_id="csv1")
    return _header_alternatives(candidate, source={"file_id": file_id, "task_id": task_id, "format": "csv"})


def _docx(path: Path, file_id: str, task_id: str) -> list[dict[str, Any]]:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    _safe_archive(path)
    document = Document(path)
    tables, descriptions = [], []
    table_number = 0
    for block in document.element.body.iterchildren():
        if block.tag.endswith("}p"):
            paragraph = Paragraph(block, document)
            if paragraph.text.strip():
                descriptions.append(paragraph.text.strip())
        elif block.tag.endswith("}tbl"):
            table = Table(block, document)
            matrix = []
            seen_all = set()
            warnings = []
            for row in table.rows:
                values = []
                seen = set()
                for cell in row.cells:
                    # python-docx 会把合并格重复暴露为同一 XML；不伪造重复的汇总/身份值。
                    if cell._tc in seen or cell._tc in seen_all:
                        values.append(None)
                        warnings.append("Word 合并单元格未自动展开，请核对归属与粒度。")
                    else:
                        values.append(_json_value(cell.text))
                        seen.add(cell._tc)
                seen_all.update(seen)
                matrix.append(values)
            table_number += 1
            index = table_number
            candidate = _candidate(matrix, list(range(1, len(matrix) + 1)), name=f"表格 {index}", source={"file_id": file_id, "task_id": task_id, "document_table": index}, candidate_id=f"docx{index}", description="\n".join(descriptions[-3:]), warnings=warnings + ["docx 不含可靠渲染页码；来源使用表格序号与原始行号。"])
            tables.extend(_header_alternatives(candidate, source={"file_id": file_id, "task_id": task_id, "document_table": index}))
    if not tables:
        raise ParseError("docx 未找到表格；第一版需明确表格结构，请整理为表格或 CSV 后导入。")
    return tables


def _pdf(path: Path, file_id: str, task_id: str) -> list[dict[str, Any]]:
    from docling.datamodel.base_models import InputFormat, ConversionStatus
    from docling.datamodel.pipeline_options import NativePdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.pipeline.native_pdf_pipeline import NativePdfPipeline
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    # Docling 原生文本管线不启用 OCR/模型下载/远程服务。位置聚类只是候选，必须人工核对。
    converter = DocumentConverter(allowed_formats=[InputFormat.PDF], format_options={InputFormat.PDF: PdfFormatOption(pipeline_cls=NativePdfPipeline, backend=PyPdfiumDocumentBackend, pipeline_options=NativePdfPipelineOptions(document_timeout=120, enable_remote_services=False, generate_page_images=False, generate_picture_images=False))})
    result = converter.convert(path, max_num_pages=200, raises_on_error=False)
    if result.status != ConversionStatus.SUCCESS:
        raise ParseError("PDF 解析不完整或超时；请按页拆分并人工核对，不能作为可靠结构导入。")
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in result.document.texts:
        for provenance in item.prov:
            box = provenance.bbox
            grouped[provenance.page_no].append({"text": item.text.strip(), "left": box.l, "y": (box.t + box.b) / 2})
    if not grouped:
        raise ParseError("PDF 没有文本层，可能是扫描件；第一版不支持可靠 OCR，请转为可编辑文档。")
    tables = []
    for page, items in sorted(grouped.items()):
        lines: list[list[dict[str, Any]]] = []
        # 同页按 y 坐标聚成行，然后按 x 排列。绝不自动关联跨页记录。
        for item in sorted(items, key=lambda value: (-value["y"], value["left"])):
            if not item["text"]:
                continue
            if lines and abs(lines[-1][0]["y"] - item["y"]) <= 4:
                lines[-1].append(item)
            else:
                lines.append([item])
        matrix = []
        for line in lines:
            sorted_line = sorted(line, key=lambda v: v["left"])
            values = [part["text"] for part in sorted_line]
            if len(values) == 1:
                values = [part.strip() for part in re.split(r"\s{2,}|\t|\s*\|\s*", values[0])]
            matrix.append(values)
        if not matrix:
            continue
        table = _candidate(matrix, list(range(1, len(matrix) + 1)), name=f"第 {page} 页候选", source={"file_id": file_id, "task_id": task_id, "page": page, "parser": "docling_native"}, candidate_id=f"pdf{page}", warnings=["PDF 文本位置生成候选结构，可能存在列错位；未跨页合并，必须对照原文核对。"])
        table["structure_uncertain"] = True
        tables.extend(_header_alternatives(table, source={"file_id": file_id, "task_id": task_id, "page": page, "parser": "docling_native"}))
    missing_pages = sorted(set(result.document.pages) - set(grouped))
    if missing_pages:
        raise ParseError(f"PDF 第 {missing_pages} 页无文本层，含扫描/空白页；请拆分或人工整理后重试，未忽略这些页。")
    return tables


def parse_file(path: str | Path, file_id: str = "", task_id: str = "") -> dict[str, Any]:
    """解析完整预览；调用方必须事先校验路径、上传大小和所属账号。"""
    path = Path(path)
    parsers = {".xlsx": _xlsx, ".csv": _csv, ".docx": _docx, ".pdf": _pdf}
    if path.suffix.lower() == ".xls":
        raise ParseError("第一版不解析旧版 xls。请用 Excel/LibreOffice 另存为 xlsx，保留合并范围与编号格式后上传。")
    parser = parsers.get(path.suffix.lower())
    if parser is None:
        raise ParseError("支持 xlsx、csv、docx 和文本型 pdf；请转换文件格式。")
    tables = parser(path, file_id, task_id)
    if not tables:
        raise ParseError("文件没有可导入的候选表。")
    return {"task_id": task_id, "version": 1, "tables": tables, "warnings": ["候选结构及推断类型必须由用户核对；模型未参与原文提取。"]}


def csv_safe(value: Any) -> str:
    """防止下载后电子表格把不可信单元格当公式执行。"""
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text
