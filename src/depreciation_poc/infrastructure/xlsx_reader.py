"""Small dependency-free XLSX reader for the fixed POC source workbooks.

It intentionally reads values only. Formatting and formulas are outside the source
snapshot contract, which keeps the customer deployment free of Excel automation.
"""
from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile


NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def read_sheet(path: str | Path, sheet_name: str) -> list[dict[str, str]]:
    """Return a worksheet as header-keyed string rows without requiring openpyxl."""
    path = Path(path)
    with ZipFile(path) as archive:
        shared = _shared_strings(archive)
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("pkg:Relationship", NS)}
        worksheet_target = None
        for sheet in workbook.findall("main:sheets/main:sheet", NS):
            if sheet.attrib.get("name") == sheet_name:
                worksheet_target = targets[sheet.attrib[f"{{{NS['rel']}}}id"]]
                break
        if worksheet_target is None:
            raise ValueError(f"Worksheet {sheet_name!r} not found in {path.name}")
        target = worksheet_target.replace("\\", "/")
        if not target.startswith("xl/"):
            target = f"xl/{target.lstrip('/')}"
        root = ElementTree.fromstring(archive.read(target))
    raw_rows: list[dict[int, str]] = []
    for row in root.findall("main:sheetData/main:row", NS):
        values: dict[int, str] = {}
        for cell in row.findall("main:c", NS):
            index = _column_index(cell.attrib.get("r", "A1"))
            values[index] = _cell_value(cell, shared)
        if values:
            raw_rows.append(values)
    if not raw_rows:
        return []
    headers = raw_rows[0]
    header_names: dict[int, str] = {}
    header_counts: dict[str, int] = {}
    for index, raw_name in headers.items():
        name = raw_name.strip()
        if not name:
            continue
        header_counts[name] = header_counts.get(name, 0) + 1
        header_names[index] = name if header_counts[name] == 1 else f"{name}__{header_counts[name]}"
    return [
        {header_names[index]: value for index, value in row.items() if index in header_names}
        for row in raw_rows[1:]
    ]


def _shared_strings(archive: ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(node.itertext()) for node in root.findall("main:si", NS)]


def _cell_value(cell: ElementTree.Element, shared: list[str]) -> str:
    value = cell.findtext("main:v", default="", namespaces=NS)
    cell_type = cell.attrib.get("t")
    if cell_type == "s" and value:
        return shared[int(value)]
    if cell_type == "inlineStr":
        return "".join(cell.find("main:is", NS).itertext()) if cell.find("main:is", NS) is not None else ""
    return value


def _column_index(reference: str) -> int:
    letters = "".join(character for character in reference if character.isalpha())
    result = 0
    for character in letters:
        result = result * 26 + ord(character.upper()) - ord("A") + 1
    return result
