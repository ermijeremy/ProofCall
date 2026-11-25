"""Bounded Excel contact preview and atomic import; never submits calls."""

from io import BytesIO
import math
import re
from uuid import uuid4
from zipfile import BadZipFile, ZipFile
from xml.etree.ElementTree import ParseError

from defusedxml.common import DefusedXmlException
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.recruitment import RecruitmentLead, RecruitmentCampaign
from app.services.recruitment_service import campaign

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000


def normalize_phone(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("Enter a phone number, not a formula or date")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("Phone number must contain whole digits")
        value = int(value)
    number = re.sub(r"[\s()\-]", "", str(value))
    if re.fullmatch(r"0[79][0-9]{8}", number):
        number = "+251" + number[1:]
    elif re.fullmatch(r"[79][0-9]{8}", number):
        number = "+251" + number
    elif re.fullmatch(r"251[79][0-9]{8}", number):
        number = "+" + number
    if not re.fullmatch(r"\+[1-9][0-9]{6,14}", number):
        raise ValueError("Use +country-code format, or an Ethiopian 09… / 07… number")
    return number


def contact_sets(db: Session, campaign_id: str):
    owner = campaign(db, campaign_id).company_id
    existing = set(db.scalars(select(RecruitmentLead.phone_number).where(
        RecruitmentLead.campaign_id == campaign_id)))
    blocked = set(db.scalars(select(RecruitmentLead.phone_number).join(RecruitmentCampaign).where(
        RecruitmentCampaign.company_id == owner,
        RecruitmentLead.do_not_contact.is_(True))))
    return existing, blocked


def preview_excel(db: Session, campaign_id: str, content: bytes) -> dict:
    campaign(db, campaign_id)
    if len(content) > MAX_BYTES:
        raise ValueError("Excel file must be 2 MB or smaller")
    try:
        with ZipFile(BytesIO(content)) as archive:
            if len(archive.infolist()) > 500 or sum(i.file_size for i in archive.infolist()) > 20 * 1024 * 1024:
                raise ValueError("Excel workbook expands beyond the import limit")
            types = archive.read("[Content_Types].xml")
            if b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml" not in types:
                raise ValueError("Save the workbook as .xlsx (without macros)")
        book = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
    except Exception as exc:
        raise ValueError("Upload a valid .xlsx workbook, at most 2 MB and 1,000 rows") from exc
    try:
        sheet = book.worksheets[0]
        # Ignore unreliable worksheet dimensions, but bound rows and columns.
        sheet.reset_dimensions()
        iterator = sheet.iter_rows(max_col=30)
        headers = next(iterator)
        aliases = {"name": "name", "full name": "name", "phone": "phone", "phone number": "phone", "mobile": "phone", "mobile number": "phone"}
        columns = {}
        for i, cell in enumerate(headers):
            key = aliases.get(str(cell.value or "").strip().lower().replace("_", " "))
            if key:
                if key in columns:
                    raise ValueError("Use only one Name column and one Phone number column")
                columns[key] = i
        if set(columns) != {"name", "phone"}:
            raise ValueError("First row must contain Name and Phone number headers (within the first 30 columns)")
        existing, blocked = contact_sets(db, campaign_id)
        seen, rows = set(), []
        for row_number, cells in enumerate(iterator, start=2):
            if row_number > MAX_ROWS + 1:
                raise ValueError("Use at most 1,000 spreadsheet rows after the header")
            name_cell, phone_cell = cells[columns["name"]], cells[columns["phone"]]
            if name_cell.value is None and phone_cell.value is None:
                continue
            name = str(name_cell.value or "").strip()
            row = {"row": row_number, "name": name[:255], "phone_number": "", "status": "valid", "message": "Ready to import"}
            try:
                if name_cell.data_type == "f" or phone_cell.data_type == "f":
                    raise ValueError("Replace formulas with plain values")
                if len(name) > 255:
                    raise ValueError("Name exceeds 255 characters")
                phone = normalize_phone(phone_cell.value)
                row["phone_number"] = phone
                if phone in blocked:
                    row.update(status="blocked", message="Do not contact — skipped")
                elif phone in existing or phone in seen:
                    row.update(status="duplicate", message="Already in the campaign or spreadsheet — skipped")
                seen.add(phone)
            except ValueError as exc:
                row.update(status="invalid", message=str(exc))
            rows.append(row)
        if not rows:
            raise ValueError("The workbook has no contacts")
        return {"sheet": sheet.title, "rows": rows, "valid": sum(r["status"] == "valid" for r in rows), "total": len(rows)}
    except (IndexError, StopIteration) as exc:
        raise ValueError("The workbook has no contact sheet") from exc
    except (DefusedXmlException, ParseError, BadZipFile, OSError, EOFError) as exc:
        raise ValueError("The workbook is damaged or contains unsupported XML") from exc
    finally:
        book.close()


def import_contacts(db: Session, campaign_id: str, data) -> dict:
    campaign(db, campaign_id)
    if not data.contact_permission:
        raise ValueError("Confirm permission to call the imported contacts")
    existing, blocked = contact_sets(db, campaign_id)
    imported = skipped = 0
    for contact in data.contacts:
        phone = normalize_phone(contact.phone_number)
        if phone in existing or phone in blocked:
            skipped += 1
            continue
        db.add(RecruitmentLead(lead_id=f"lead_{uuid4().hex}", campaign_id=campaign_id,
                              name=contact.name, phone_number=phone,
                              contact_source=data.contact_source, contact_permission=True))
        existing.add(phone)
        imported += 1
    db.commit()
    return {"imported": imported, "skipped": skipped}
