"""Bound CSV uploads before parsing multipart data or reading files into memory."""

from collections.abc import Callable

from fastapi import HTTPException, Request, Response, UploadFile
from fastapi.routing import APIRoute

from app.core.import_limits import MAX_CSV_BYTES, MAX_UPLOAD_BODY_BYTES


class BoundedUploadRoute(APIRoute):
    """Apply the same streaming budget to every multipart import endpoint.

    A route dependency would run after FastAPI has parsed the form. Wrapping the
    handler instead bounds even chunked requests before the parser consumes them.
    The parser also limits part headers (python-multipart), files and form fields.
    """

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def bounded_handler(request: Request) -> Response:
            media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type not in {"multipart/form-data", "application/x-www-form-urlencoded"}:
                return await handler(request)

            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    length = int(content_length)
                    if length < 0:
                        raise ValueError
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
                if length > MAX_UPLOAD_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="Upload request is too large")

            received = 0
            receive = request.receive

            async def bounded_receive():
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > MAX_UPLOAD_BODY_BYTES:
                        raise HTTPException(status_code=413, detail="Upload request is too large")
                return message

            bounded_request = Request(request.scope, receive=bounded_receive)
            # FastAPI reuses this parsed form; the context closes uploaded files
            # on validation failures as well as successful requests.
            async with bounded_request.form(max_files=1, max_fields=0, max_part_size=4096):
                return await handler(bounded_request)

        return bounded_handler


async def read_csv_upload(file: UploadFile) -> bytes:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a CSV file")
    content = await file.read(MAX_CSV_BYTES + 1)
    if len(content) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="CSV file exceeds the 1 MiB limit")
    return content
