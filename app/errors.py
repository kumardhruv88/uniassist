"""Structured errors: one shape for every failure, a stable code, a hint, the trace id, and never a stack trace."""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger("uniassist.errors")

HINTS = {
    "INVALID_REQUEST": "Check the request body against the API contract (see /docs).",
    "INVALID_METADATA": "Send metadata as a JSON object with the Annex B fields (doc_id, title, issuer, authority_level, doc_type, effective_from, ...).",
    "UNSUPPORTED_FILE": "Upload a PDF, DOCX, TXT, MD, HTML, PNG or JPG file.",
    "FILE_TOO_LARGE": "Split the document or raise MAX_UPLOAD_MB.",
    "NOT_FOUND": "Check the identifier.",
    "UNAUTHORIZED": "Send the X-Admin-Token header.",
    "RATE_LIMITED": "Wait a moment and retry.",
    "CLIENT_BLOCKED": "Too many blocked requests from this client. Try again later.",
    "LLM_UNAVAILABLE": "Start Ollama (ollama serve) and check OLLAMA_BASE_URL. Rule, refusal and eligibility answers still work.",
    "INTERNAL_ERROR": "Retry. If it persists, share the trace_id with the team.",
}
STATUS_CODES = {400: "INVALID_REQUEST", 401: "UNAUTHORIZED", 404: "NOT_FOUND", 413: "FILE_TOO_LARGE",
                415: "UNSUPPORTED_FILE", 422: "INVALID_REQUEST", 429: "RATE_LIMITED", 503: "LLM_UNAVAILABLE"}


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: object | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.details, self.retry_after = code, message, status, details, retry_after


def error_body(code: str, message: str, trace_id: str, details: object | None = None, detail: object | None = None) -> dict:
    """{"error": {code, message, hint, trace_id, details?}, "detail": ...}. `detail` keeps FastAPI's own shape
    (a string, a list of field errors, or {"message", "errors"}) so existing clients keep working."""
    err = {"code": code, "message": message, "hint": HINTS.get(code, ""), "trace_id": trace_id}
    if details is not None:
        err["details"] = details
    return {"error": err, "detail": message if detail is None else detail}


def install(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app(request: Request, e: AppError):
        tid = uuid.uuid4().hex[:8]
        headers = {"Retry-After": str(int(e.retry_after) + 1)} if e.retry_after else None
        return JSONResponse(error_body(e.code, e.message, tid, e.details), status_code=e.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, e: RequestValidationError):
        raw = jsonable_encoder(e.errors())
        details = [{"field": ".".join(str(x) for x in err["loc"][1:]), "message": err["msg"]} for err in raw]
        return JSONResponse(error_body("INVALID_REQUEST", "The request did not match the API contract.", uuid.uuid4().hex[:8],
                                       details, detail=raw), status_code=422)

    @app.exception_handler(HTTPException)
    async def _http(request: Request, e: HTTPException):
        code = STATUS_CODES.get(e.status_code, "INVALID_REQUEST")
        if isinstance(e.detail, dict):
            msg, details = e.detail.get("message", "Request failed."), e.detail.get("errors")
            code = e.detail.get("code", code)
        else:
            msg, details = str(e.detail), None
        return JSONResponse(error_body(code, msg, uuid.uuid4().hex[:8], details, detail=e.detail),
                            status_code=e.status_code, headers=getattr(e, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, e: Exception):
        tid = uuid.uuid4().hex[:8]
        log.exception("unhandled error trace_id=%s path=%s", tid, request.url.path)
        return JSONResponse(error_body("INTERNAL_ERROR", "Something went wrong while handling this request.", tid), status_code=500)
