"""生成可复现脱敏解析样例，人工期望值单独保存在 expected.json。"""
from pathlib import Path
from openpyxl import Workbook
from docx import Document
from reportlab.pdfgen import canvas


def generate(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    book = Workbook()
    sheet = book.active
    sheet.title = "成绩明细"
    sheet.append(["2026 秋季成绩（分）"])
    sheet.merge_cells("A1:E1")
    sheet.append(["学号", "姓名", "学科", "成绩", "公式成绩"])
    sheet.append(["00017", "用户1", "学科1", 80, "=D3+5"])
    sheet.append([None, None, "学科2", None, None])
    sheet.merge_cells("A3:A4")
    sheet.merge_cells("B3:B4")
    sheet.append([None, None, "学科3", 90, None])
    sheet.append([18, "用户2", "学科1", 75, None])
    sheet["A6"].number_format = "00000"
    multi = book.create_sheet("多级表头")
    multi.append(["学号", "成绩", None])
    multi.append([None, "语文（分）", "数学（分）"])
    multi.merge_cells("A1:A2")
    multi.merge_cells("B1:C1")
    multi.append(["00017", 90, 95])
    book.save(root / "merged_headers.xlsx")
    grains = Workbook()
    data = grains.active
    data.title = "综合评价"
    data.append(["学号", "姓名", "学生总分", "一级指标", "一级指标总分", "加分项目", "加分分值"])
    data.append(["001", "同名", 92, "体育", 30, "比赛A", 5])
    data.append(["001", "同名", 92, "体育", 30, "比赛B", 3])
    data.append(["001", "同名", 92, "学习", 62, "活动C", 2])
    data.append(["002", "同名", 85, "体育", 25, "比赛D", 4])
    data.append([None, "未核对", None, "体育", None, "孤立明细", 1])
    grains.save(root / "grains.xlsx")
    (root / "leading_zero.csv").write_text("学号,成绩,订单号\n00017,,000001\n00018,75,000002\n", encoding="utf-8-sig")
    doc = Document()
    doc.add_paragraph("成绩来源：教务系统。成绩单位：分。")
    table = doc.add_table(rows=3, cols=3)
    for cells, row in zip(table.rows, [["学号", "学科", "成绩"], ["00017", "语文", "90"], ["00018", "数学", ""]]):
        for cell, value in zip(cells.cells, row):
            cell.text = value
    doc.add_paragraph("以下是另一份订单表。")
    second = doc.add_table(rows=2, cols=2)
    for cells, row in zip(second.rows, [["订单号", "金额"], ["000009", "19.5"]]):
        for cell, value in zip(cells.cells, row):
            cell.text = value
    doc.save(root / "tables.docx")
    pdf = canvas.Canvas(str(root / "text_table.pdf"))
    for index, row in enumerate([["student_id", "score"], ["00017", "90"], ["00018", "75"]]):
        for col, value in enumerate(row):
            pdf.drawString(72 + col * 140, 750 - index * 24, value)
    pdf.save()
    scan = canvas.Canvas(str(root / "no_text.pdf"))
    scan.rect(72, 72, 400, 500, fill=1)
    scan.save()


if __name__ == "__main__":
    generate(Path(__file__).parent)
