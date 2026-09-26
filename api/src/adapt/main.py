"""ADAPT API and static web server, one process.

There is deliberately no publish, schedule, post or share endpoint. The only way
copy leaves ADAPT is a person copying or downloading approved text.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import APP_NAME
from .env import load_env

load_env()

from .config import ROOT, ConfigError, load_config
from .db import init_db
from .routes import recover_interrupted, router
from .seeds import load_fixtures
from .sessions import COOKIE, cookie_secure, ensure_session

log = logging.getLogger("adapt")
WEB_BUILD = Path(os.environ.get("ADAPT_WEB_BUILD") or ROOT / "web" / "build")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        cfg = load_config()
    except ConfigError as e:
        # Print the full list and stop: the app must not boot on bad config.
        print(str(e), file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    if not cfg.markets:
        log.warning("No market snapshots in %s/markets. Runs will be refused until they exist.", cfg.source_dir)
    app.state.config = cfg
    app.state.tables = init_db()
    app.state.seeds = load_fixtures()
    interrupted = recover_interrupted()
    if interrupted:
        log.warning("Marked %d interrupted market(s) as stopped; they can be retried.", interrupted)
    yield


app = FastAPI(title=APP_NAME, lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def session_cookie(request: Request, call_next):
    """Every API request gets an anonymous session; the cookie is the only identity."""
    if not request.url.path.startswith("/api/") or request.url.path == "/api/health":
        return await call_next(request)
    sid, created = ensure_session(request.cookies.get(COOKIE))
    request.state.session_id = sid
    response = await call_next(request)
    if created:
        response.set_cookie(COOKIE, sid, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax",
                            secure=cookie_secure())
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    return JSONResponse(detail, status_code=exc.status_code)


app.include_router(router)


@app.get("/api/health")
def health() -> dict:
    cfg = app.state.config
    return {
        "ok": True,
        "app": APP_NAME,
        "brands": sorted(cfg.brands),
        "markets": sorted(cfg.markets),
        "tables": app.state.tables,
        "model_configured": bool(os.environ.get("ANTHROPIC_MODEL")),
    }


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def api_not_found(rest: str) -> JSONResponse:
    return JSONResponse({"error": "not_found", "message": f"No API route /api/{rest}."}, status_code=404)


# ---------- static SvelteKit build (SPA fallback) ----------

if (WEB_BUILD / "_app").exists():
    app.mount("/_app", StaticFiles(directory=WEB_BUILD / "_app"), name="app-assets")


@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def spa(path: str):
    target = (WEB_BUILD / path).resolve()
    if path and target.is_file() and WEB_BUILD.resolve() in target.parents:
        return FileResponse(target)
    index = WEB_BUILD / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse(
        {"app": APP_NAME, "message": "Web build not found. Run `npm run build` in web/."}, status_code=503
    )
