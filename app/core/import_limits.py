"""Shared resource budgets for CSV imports, including direct service callers."""

MAX_CSV_BYTES = 1024 * 1024
MAX_CSV_ROWS = 10_000
MAX_CSV_COLUMNS = 64
MAX_CSV_FIELD_CHARS = 4096
MAX_UPLOAD_BODY_BYTES = MAX_CSV_BYTES + 64 * 1024
