"""Order exports: a formatted Excel workbook and a clean UTF-8 CSV.

Both share one column definition. Headers and category values follow the UI
language (English or Arabic); numbers always stay numeric with Latin digits.
Timestamps are shown in Asia/Riyadh local time, as in the app.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Iterator

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .. import geo
from ..timeutil import utc_to_riyadh
from .queries import iter_order_rows

# key, English header, Arabic header, kind, width
COLUMNS: list[tuple[str, str, str, str, int]] = [
    ("order_id", "Order", "رقم الطلب", "int", 10),
    ("order_date", "Order date", "تاريخ الطلب", "date", 13),
    ("order_placed_at", "Placed at (Riyadh)", "وقت الطلب (الرياض)", "datetime", 18),
    ("status", "Status", "الحالة", "label", 13),
    ("channel", "Channel", "القناة", "label", 10),
    ("customer_code", "Customer code", "رمز العميل", "text", 13),
    ("customer_name", "Customer", "العميل", "text", 24),
    ("customer_region", "Region", "المنطقة", "place", 17),
    ("customer_city", "City", "المدينة", "place", 15),
    ("line_count", "Lines", "عدد الأسطر", "int", 9),
    ("units", "Units", "عدد الوحدات", "int", 9),
    ("gross_merchandise_value", "Gross merchandise value (SAR)", "إجمالي قيمة البضائع (ريال)", "money", 16),
    ("discounts", "Discounts (SAR)", "الخصومات (ريال)", "money", 13),
    ("ordered_value", "Ordered value (SAR)", "قيمة الطلب (ريال)", "money", 15),
    ("successful_collections", "Collected (SAR)", "المحصّل (ريال)", "money", 14),
    ("successful_refunds", "Refunded (SAR)", "المسترد (ريال)", "money", 14),
    ("net_collections", "Net collections (SAR)", "صافي التحصيل (ريال)", "money", 16),
    ("collection_state", "Collection status", "حالة التحصيل", "label", 16),
    ("transactions", "Transactions", "عدد العمليات", "int", 12),
]

LABELS: dict[str, dict[str, str]] = {
    "en": {"pending": "Pending", "processing": "Processing", "shipped": "Shipped", "delivered": "Delivered",
           "cancelled": "Cancelled", "app": "App", "web": "Web", "paid": "Paid", "unpaid": "Unpaid",
           "partially_refunded": "Partly refunded", "refunded": "Refunded", "partially_paid": "Partly paid"},
    "ar": {"pending": "بانتظار الدفع", "processing": "قيد التجهيز", "shipped": "تم الشحن", "delivered": "تم التسليم",
           "cancelled": "ملغى", "app": "التطبيق", "web": "الموقع", "paid": "مدفوع", "unpaid": "غير مدفوع",
           "partially_refunded": "مسترد جزئياً", "refunded": "مسترد", "partially_paid": "مدفوع جزئياً"},
}

ACCENT = "0E6A4E"


def _value(row: dict, key: str, kind: str, lang: str):
    if key == "order_placed_at":
        v = row.get("order_placed_at_utc")
        return utc_to_riyadh(v).replace(tzinfo=None) if isinstance(v, datetime) else v
    v = row.get(key)
    if kind == "label" and isinstance(v, str):
        return LABELS[lang].get(v, v)
    if kind == "place":
        return geo.label(v, lang)
    return v


def _filters_text(f: dict, lang: str) -> list[tuple[str, str]]:
    ar = lang == "ar"
    statuses = ", ".join(LABELS[lang].get(s, s) for s in (f.get("statuses") or [])) or ("الكل" if ar else "All")
    return [
        ("الفترة (تاريخ الطلب، توقيت الرياض)" if ar else "Period (order date, Asia/Riyadh)",
         f"{f['date_from']} → {f['date_to']}"),
        ("الحالة" if ar else "Status", statuses),
        ("الفئة" if ar else "Category", f.get("category") or ("الكل" if ar else "All")),
        ("المنتج" if ar else "Product", f.get("product_sku") or ("الكل" if ar else "All")),
        ("المنطقة" if ar else "Region", geo.label(f.get("region"), lang) or ("الكل" if ar else "All")),
        ("المدينة" if ar else "City", geo.label(f.get("city"), lang) or ("الكل" if ar else "All")),
        ("رقم الطلب" if ar else "Order number", str(f["order_id"]) if f.get("order_id") is not None else ("—")),
    ]


# ---------------------------------------------------------------- CSV

def iter_csv(f: dict, limit: int, lang: str) -> Iterator[str]:
    """UTF-8 with BOM (Excel then reads Arabic names correctly); plain decimals, ISO dates."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c[2] if lang == "ar" else c[1] for c in COLUMNS])
    yield "﻿" + buf.getvalue()  # header goes out even when no order matches
    buf.seek(0)
    buf.truncate(0)
    for row in iter_order_rows(f, limit):
        out = []
        for key, _en, _ar, kind, _w in COLUMNS:
            v = _value(row, key, kind, lang)
            if isinstance(v, datetime):
                v = v.strftime("%Y-%m-%d %H:%M")
            elif isinstance(v, date):
                v = v.isoformat()
            elif isinstance(v, Decimal):
                v = f"{v:.2f}"
            out.append("" if v is None else v)
        w.writerow(out)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)


# ---------------------------------------------------------------- Excel

def build_xlsx(f: dict, limit: int, lang: str, generated_at_riyadh: datetime) -> bytes:
    ar = lang == "ar"
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("الطلبات" if ar else "Orders")
    ws.sheet_view.rightToLeft = ar
    ws.freeze_panes = "A2"
    for i, col in enumerate(COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = col[4] + (4 if ar else 0)

    header_font = Font(name="Arial", bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor=ACCENT)
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin = Side(style="thin", color="DCE2DE")
    body_font = Font(name="Arial")
    total_font = Font(name="Arial", bold=True)
    formats = {"int": "0", "money": "#,##0.00", "date": "yyyy-mm-dd", "datetime": "yyyy-mm-dd hh:mm"}

    ws.row_dimensions[1].height = 32
    header = []
    for col in COLUMNS:
        c = WriteOnlyCell(ws, value=col[2] if ar else col[1])
        c.font, c.fill, c.alignment, c.border = header_font, header_fill, header_align, Border(bottom=thin)
        header.append(c)
    ws.append(header)

    n = 0
    for row in iter_order_rows(f, limit):
        n += 1
        cells = []
        for key, _en, _ar, kind, _w in COLUMNS:
            v = _value(row, key, kind, lang)
            if isinstance(v, Decimal):
                v = float(v)  # Excel stores numbers as doubles; values are 2-dp amounts well inside exact range
            c = WriteOnlyCell(ws, value=v)
            c.font = body_font
            if kind in formats:
                c.number_format = formats[kind]
            cells.append(c)
        ws.append(cells)

    if n:
        last = n + 1
        totals = []
        for i, (key, _en, _ar, kind, _w) in enumerate(COLUMNS, start=1):
            letter = get_column_letter(i)
            if i == 1:
                v = "الإجمالي" if ar else "Total"
            elif kind == "money" or key in ("line_count", "units", "transactions"):
                v = f"=SUBTOTAL(9,{letter}2:{letter}{last})"
            elif key == "order_date":
                v = f"=SUBTOTAL(3,A2:A{last})"
            else:
                v = None
            c = WriteOnlyCell(ws, value=v)
            c.font = total_font
            c.border = Border(top=Side(style="medium", color=ACCENT))
            if kind == "money":
                c.number_format = formats["money"]
            totals.append(c)
        ws.append([])
        ws.append(totals)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{max(n + 1, 2)}"

    info = wb.create_sheet("معلومات" if ar else "About")
    info.sheet_view.rightToLeft = ar
    info.column_dimensions["A"].width = 38
    info.column_dimensions["B"].width = 70
    title = WriteOnlyCell(info, value="SalesFlow — " + ("تصدير الطلبات" if ar else "Orders export"))
    title.font = Font(name="Arial", bold=True, size=14, color=ACCENT)
    info.append([title])
    info.append([])
    rows: list[tuple[str, str]] = [
        ("وقت التصدير (الرياض)" if ar else "Exported at (Asia/Riyadh)", generated_at_riyadh.strftime("%Y-%m-%d %H:%M")),
        ("عدد الطلبات" if ar else "Orders", str(n)),
        *_filters_text(f, lang),
    ]
    if n >= limit:
        rows.append(("تنبيه" if ar else "Note",
                     f"تم الوصول إلى الحد الأقصى ({limit} طلب)؛ ضيّق الفلاتر لتصدير الباقي." if ar else
                     f"The export limit ({limit} orders) was reached; narrow the filters to export the rest."))
    rows += [
        ("ملاحظة" if ar else "Notes",
         "تُختار الطلبات حسب تاريخ الطلب. أعمدة التحصيل إجمالية لكل طلب مهما كان تاريخ الدفع. "
         "التحصيلات حركة نقد وليست إيراداً محاسبياً." if ar else
         "Orders are selected by order date. Collection columns are lifetime totals per order, whatever the "
         "payment date. Collections are cash movements, not accounting revenue."),
        ("البيانات" if ar else "Data",
         "بيانات اصطناعية مولّدة، لا تمثل أشخاصاً أو معاملات حقيقية." if ar else
         "Synthetic, generated data. No real people or transactions."),
        ("العملة" if ar else "Currency", "SAR"),
    ]
    for k, v in rows:
        kc = WriteOnlyCell(info, value=k)
        kc.font = Font(name="Arial", bold=True)
        vc = WriteOnlyCell(info, value=v)
        vc.font = Font(name="Arial")
        vc.alignment = Alignment(wrap_text=True, vertical="top")
        info.append([kc, vc])

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
