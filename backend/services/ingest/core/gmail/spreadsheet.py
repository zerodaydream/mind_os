"""XLSX and legacy XLS spreadsheet attachment text."""

from __future__ import annotations

from io import BytesIO


def extract_xlsx(raw: bytes) -> str:
    from openpyxl import load_workbook

    book = load_workbook(BytesIO(raw), read_only=True, data_only=True)
    lines: list[str] = []
    try:
        for sheet in book.worksheets:
            lines.append(f"## Sheet: {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    lines.append("\t".join(cells))
    finally:
        book.close()
    return "\n".join(lines)


def extract_xls(raw: bytes) -> str:
    import xlrd

    book = xlrd.open_workbook(file_contents=raw)
    lines: list[str] = []
    for sheet in book.sheets():
        lines.append(f"## Sheet: {sheet.name}")
        for rx in range(sheet.nrows):
            cells = [str(sheet.cell_value(rx, cx)).strip() for cx in range(sheet.ncols)]
            cells = [c for c in cells if c]
            if cells:
                lines.append("\t".join(cells))
    return "\n".join(lines)