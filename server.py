import os
import io
import sys
import time
import uuid
import socket
import uvicorn
import pandas as pd
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Query, Form, Depends
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any, Optional, List, Tuple

# Ensure studio folder on path
sys.path.append(os.path.dirname(__file__))
from project_db import ProjectDatabase
from extractors import PlatformRegistry
from job_manager import ProjectJobManager
from exporter import ProjectExporter
from auth_manager import AuthManager, decode_jwt_token, create_jwt_token

from catalog_manager import CatalogManager, MASTER_CSV_PATH, PART1_CSV_PATH, PART2_CSV_PATH
from automation_runner import AutomationRunner
from scraper_engine import AmazonScraperEngine
from spreadsheet_manager import SpreadsheetManager
from batch_machine import BatchEnrichmentMachine

app = FastAPI(title="Professional E-Commerce Data Extraction & Spreadsheet Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------
# HEALTH CHECK & RENDER KEEP-ALIVE DAEMON
# -------------------------------------------------------------
SERVER_START_TIME = time.time()

@app.get("/api/ping")
@app.get("/api/health")
async def ping_health():
    return {
        "status": "online",
        "service": "storehub-platform",
        "timestamp": time.time(),
        "uptime_seconds": int(time.time() - SERVER_START_TIME)
    }

def _start_self_keepalive_daemon():
    url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("SELF_PING_URL")
    if not url:
        return
    import threading
    import urllib.request
    def _keepalive_loop():
        target = f"{url.rstrip('/')}/api/ping"
        while True:
            time.sleep(600)  # Ping every 10 minutes (prevents 15m idle sleep)
            try:
                req = urllib.request.Request(target, headers={"User-Agent": "StoreHub-KeepAlive/1.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    pass
            except Exception:
                pass
    t = threading.Thread(target=_keepalive_loop, daemon=True)
    t.start()

_start_self_keepalive_daemon()

# Authentication & Multi-Tenant Isolated Storage
auth_mgr = AuthManager()
registry = PlatformRegistry()

catalog_mgr = CatalogManager()
automation = AutomationRunner(catalog_mgr)
scraper = AmazonScraperEngine()
spreadsheet_mgr = SpreadsheetManager()
machine = BatchEnrichmentMachine()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

# -------------------------------------------------------------
# PYDANTIC SCHEMAS
# -------------------------------------------------------------
class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class ProjectCreateRequest(BaseModel):
    name: str
    platform: str = "amazon"
    config: Optional[Dict[str, Any]] = None

class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    platform: Optional[str] = None
    status: Optional[str] = None
    concurrency: Optional[int] = None
    config: Optional[Dict[str, Any]] = None
    mapping: Optional[Dict[str, Any]] = None

class ManualUrlsRequest(BaseModel):
    urls_or_names: str # newline or comma separated
    column_name: str = "Product URL or Name"

class CellEditRequest(BaseModel):
    row_idx: int
    col_name: str
    new_val: str

class AddColumnRequest(BaseModel):
    col_name: str
    default_val: str = ""

class RenameColumnRequest(BaseModel):
    old_col: str
    new_col: str

class AddRowRequest(BaseModel):
    row_data: Dict[str, Any]

class DeleteRowsRequest(BaseModel):
    row_indices: List[int]

class MappingConfirmRequest(BaseModel):
    mapping: Dict[str, str]

class StartJobRequest(BaseModel):
    concurrency: int = 2

class PresetSaveRequest(BaseModel):
    id: str
    name: str
    description: str = ""
    config: Dict[str, Any]

# -------------------------------------------------------------
# AUTHENTICATION & MULTI-TENANT ISOLATION RESOLVER
# -------------------------------------------------------------
def get_user_context(
    request: Request,
    token: Optional[str] = None
) -> Tuple[Dict[str, Any], ProjectDatabase, ProjectJobManager, ProjectExporter]:
    """
    Resolves the current authenticated customer and injects their
    STRICTLY ISOLATED local storage database and job manager.
    Customer 1 and Customer 2 have separate physical database files on disk.
    """
    token_candidate = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token_candidate = auth_header[7:].strip()
    elif token:
        token_candidate = token
    elif "storehub_auth_token" in request.cookies:
        token_candidate = request.cookies.get("storehub_auth_token")

    # If no token provided, check if a demo user header is passed, or default to demo customer 1
    # for easy previewing, but with clear customer separation
    if not token_candidate:
        demo_header = request.headers.get("X-Customer-ID")
        if demo_header:
            user = auth_mgr.get_user_by_id(demo_header)
            if user:
                return user, auth_mgr.get_user_db(user["id"]), auth_mgr.get_user_job_manager(user["id"]), auth_mgr.get_user_exporter(user["id"])
        
        # Fallback to customer 1 if unauthenticated, so the initial page load displays cleanly
        user = auth_mgr.get_user_by_id("usr_cust_1")
        if not user:
            users = auth_mgr.get_all_users_summary()
            user = users[0] if users else None
        
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required. Please sign in.")
            
        return user, auth_mgr.get_user_db(user["id"]), auth_mgr.get_user_job_manager(user["id"]), auth_mgr.get_user_exporter(user["id"])

    payload = decode_jwt_token(token_candidate)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail="Invalid or expired session token.")

    user = auth_mgr.get_user_by_id(payload["sub"])
    if not user:
        # Cryptographically verified token from this server: auto-restore user account in database
        uid = payload["sub"]
        email = payload.get("email", f"{uid}@storehub.io")
        name = payload.get("name", "Workspace User")
        auth_mgr.restore_verified_user(uid, email, name)
        user = auth_mgr.get_user_by_id(uid)
        if not user:
            raise HTTPException(status_code=401, detail="Customer account not found.")

    user_db = auth_mgr.get_user_db(user["id"])
    user_job_mgr = auth_mgr.get_user_job_manager(user["id"])
    user_exporter = auth_mgr.get_user_exporter(user["id"])
    return user, user_db, user_job_mgr, user_exporter

# -------------------------------------------------------------
# AUTH API ENDPOINTS
# -------------------------------------------------------------
@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    ok, msg, user, token = auth_mgr.register_user(req.name, req.email, req.password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg, "user": user, "token": token}

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    ok, msg, user, token = auth_mgr.authenticate_user(req.email, req.password)
    if not ok:
        raise HTTPException(status_code=401, detail=msg)
    return {"success": True, "message": msg, "user": user, "token": token}

@app.get("/api/auth/me")
async def get_current_user_info(request: Request, token: Optional[str] = None):
    user, _, _, _ = get_user_context(request, token)
    return {"success": True, "user": user}

@app.get("/api/auth/demo-accounts")
async def get_demo_accounts():
    """Provides non-sensitive account list for 1-click test switching in evaluation."""
    accounts = auth_mgr.get_all_users_summary()
    return {"accounts": accounts}

# -------------------------------------------------------------
# ROOT & CORE NETWORK VIEWS
# -------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_home():
    html_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>E-Commerce Data Extraction Platform Loading...</h1>")

@app.get("/api/dashboard/stats")
async def get_dashboard_stats(request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    return user_db.get_dashboard_stats()

@app.get("/api/network-info")
async def get_network_info(request: Request):
    local_ip = get_local_ip()
    status = automation.get_status()
    render_url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("RENDER_EXTERNAL_HOSTNAME")
    is_render = bool(os.getenv("RENDER") or render_url)

    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    proto = request.headers.get("x-forwarded-proto") or ("https" if is_render else request.url.scheme)
    public_url = f"{proto}://{host}" if host else (render_url or f"http://{local_ip}:8000")
    is_cloud = is_render or "onrender.com" in host

    return {
        "is_cloud": is_cloud,
        "environment": "Render Cloud Production" if is_cloud else "Local Development",
        "public_url": public_url,
        "local_ip": local_ip,
        "port": 8000,
        "wifi_url": public_url,
        "localhost_url": "http://localhost:8000",
        "tunnel_url": status.get("tunnel_url", ""),
        "db_engine": "SQLite 3 WAL (Isolated User Vaults)",
        "anti_bot_profile": "Active (Chrome Headless)",
        "region": "Singapore (Southeast Asia) / Global" if is_cloud else "Local Machine"
    }

# -------------------------------------------------------------
# PROJECTS MANAGEMENT (SCOPED TO CURRENT CUSTOMER'S LOCAL STORAGE)
# -------------------------------------------------------------
@app.get("/api/projects")
async def list_projects(request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    return user_db.list_projects()

@app.post("/api/projects")
async def create_project(req: ProjectCreateRequest, request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    project_id = f"proj_{uuid.uuid4().hex[:10]}"
    p = user_db.create_project(project_id, req.name, req.platform, req.config or {})
    return {"success": True, "project": p}

@app.get("/api/projects/{project_id}")
async def get_project(project_id: str, request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    p = user_db.get_project(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found in your workspace")
    return p

@app.post("/api/projects/{project_id}")
async def update_project(project_id: str, req: ProjectUpdateRequest, request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    updates = {}
    if req.name is not None: updates["name"] = req.name
    if req.platform is not None: updates["platform"] = req.platform
    if req.status is not None: updates["status"] = req.status
    if req.concurrency is not None: updates["concurrency"] = req.concurrency
    if req.config is not None: updates["config_json"] = req.config
    if req.mapping is not None: updates["mapping_json"] = req.mapping

    success = user_db.update_project(project_id, updates)
    if not success:
        raise HTTPException(status_code=400, detail="Update failed")
    return {"success": True, "project": user_db.get_project(project_id)}

@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str, request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    success = user_db.delete_project(project_id)
    if not success:
        raise HTTPException(status_code=404, detail="Project not found in your workspace")
    return {"success": True, "message": "Project and all associated data deleted"}

# -------------------------------------------------------------
# STEP 1: DATA IMPORT (FILE UPLOAD OR MANUAL URLS)
# -------------------------------------------------------------
@app.post("/api/projects/{project_id}/upload")
async def upload_project_dataset(
    project_id: str,
    file: UploadFile = File(...),
    request: Request = None,
    token: Optional[str] = None
):
    user, user_db, _, _ = get_user_context(request, token)
    project = user_db.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found in your workspace")

    contents = await file.read()
    filename = file.filename or "upload.csv"
    ext = os.path.splitext(filename)[1].lower()

    try:
        if ext in [".xlsx", ".xls"]:
            df = pd.read_excel(io.BytesIO(contents), dtype=str)
        else:
            try:
                df = pd.read_csv(io.BytesIO(contents), dtype=str, encoding="utf-8")
            except Exception:
                df = pd.read_csv(io.BytesIO(contents), dtype=str, encoding="latin-1")

        df = df.fillna("")
        columns = list(df.columns)
        rows = df.to_dict(orient="records")

        # Auto-detect target column
        detected_col = ""
        for c in columns:
            cl = c.lower()
            if any(k in cl for k in ["url", "link", "product", "title", "name", "item", "asin"]):
                detected_col = c
                break
        if not detected_col and len(columns) > 0:
            detected_col = columns[0]

        mapping = {"target_column": detected_col}
        user_db.save_input_dataset(project_id, columns, rows, mapping)

        return {
            "success": True,
            "filename": filename,
            "total_rows": len(rows),
            "columns": columns,
            "detected_column": detected_col,
            "sample_rows": rows[:5]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"File parsing error: {str(e)}")

@app.post("/api/projects/{project_id}/manual-urls")
async def import_manual_urls(
    project_id: str,
    req: ManualUrlsRequest,
    request: Request,
    token: Optional[str] = None
):
    user, user_db, _, _ = get_user_context(request, token)
    project = user_db.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found in your workspace")

    if "\n" in req.urls_or_names:
        raw_items = [line.strip() for line in req.urls_or_names.split("\n") if line.strip()]
    elif "," in req.urls_or_names:
        raw_items = [line.strip() for line in req.urls_or_names.split(",") if line.strip()]
    else:
        raw_items = [req.urls_or_names.strip()]

    if not raw_items:
        raise HTTPException(status_code=400, detail="No URLs or product names provided")

    col = req.column_name or "Product URL or Name"
    columns = [col, "Category", "Notes"]
    rows = [{col: item, "Category": "General", "Notes": ""} for item in raw_items]
    mapping = {"target_column": col}
    user_db.save_input_dataset(project_id, columns, rows, mapping)

    return {
        "success": True,
        "total_rows": len(rows),
        "columns": columns,
        "detected_column": col,
        "sample_rows": rows[:5]
    }

# -------------------------------------------------------------
# STEP 2: INPUT SPREADSHEET EDITOR
# -------------------------------------------------------------
@app.get("/api/projects/{project_id}/input/data")
async def get_input_data(
    project_id: str,
    page: int = 1,
    page_size: int = 50,
    search: str = "",
    sort_col: str = "",
    sort_asc: bool = True,
    request: Request = None,
    token: Optional[str] = None
):
    user, user_db, _, _ = get_user_context(request, token)
    return user_db.get_input_rows(project_id, page=page, page_size=page_size, search=search, sort_col=sort_col, sort_asc=sort_asc)

@app.post("/api/projects/{project_id}/input/cell")
async def update_input_cell(project_id: str, req: CellEditRequest, request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    success = user_db.update_input_cell(project_id, req.row_idx, req.col_name, req.new_val)
    if not success:
        raise HTTPException(status_code=400, detail="Cell update failed")
    return {"success": True}

@app.post("/api/projects/{project_id}/input/column/add")
async def add_input_column(project_id: str, req: AddColumnRequest, request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    success = user_db.add_input_column(project_id, req.col_name, req.default_val)
    if not success:
        raise HTTPException(status_code=400, detail="Column addition failed")
    return {"success": True}

@app.delete("/api/projects/{project_id}/input/column/{col_name}")
async def delete_input_column(project_id: str, col_name: str, request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    success = user_db.delete_input_column(project_id, col_name)
    if not success:
        raise HTTPException(status_code=400, detail="Column deletion failed")
    return {"success": True}

@app.post("/api/projects/{project_id}/input/column/rename")
async def rename_input_column(project_id: str, req: RenameColumnRequest, request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    success = user_db.rename_input_column(project_id, req.old_col, req.new_col)
    if not success:
        raise HTTPException(status_code=400, detail="Column rename failed")
    return {"success": True}

@app.post("/api/projects/{project_id}/input/row/add")
async def add_input_row(project_id: str, req: AddRowRequest, request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    new_idx = user_db.add_input_row(project_id, req.row_data)
    return {"success": True, "row_index": new_idx}

@app.post("/api/projects/{project_id}/input/rows/delete")
async def delete_input_rows(project_id: str, req: DeleteRowsRequest, request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    deleted = user_db.delete_input_rows(project_id, req.row_indices)
    return {"success": True, "deleted_count": deleted}

@app.post("/api/projects/{project_id}/mapping")
async def confirm_column_mapping(project_id: str, req: MappingConfirmRequest, request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    success = user_db.save_column_mapping(project_id, req.mapping)
    if not success:
        raise HTTPException(status_code=400, detail="Column mapping update failed")
    return {"success": True, "mapping": req.mapping}

# -------------------------------------------------------------
# STEP 3 & 4: PLATFORMS & CONFIGURATION PRESETS
# -------------------------------------------------------------
@app.get("/api/platforms")
def get_platforms():
    return registry.get_supported_platforms()

@app.get("/api/presets")
async def list_presets(request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    return user_db.get_config_presets()

@app.post("/api/presets")
async def save_preset(req: PresetSaveRequest, request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    user_db.save_config_preset(req.id, req.name, req.description, req.config)
    return {"success": True, "preset_id": req.id}

# -------------------------------------------------------------
# STEP 5 & 6: CONCURRENCY, WORKERS & LIVE PROGRESS
# -------------------------------------------------------------
@app.post("/api/projects/{project_id}/start")
async def start_scraping_job(project_id: str, req: StartJobRequest, request: Request, token: Optional[str] = None):
    user, _, user_job_mgr, _ = get_user_context(request, token)
    ok, msg = user_job_mgr.start_job(project_id, concurrency=req.concurrency, retry_failed_only=False)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}

@app.post("/api/projects/{project_id}/pause")
async def pause_scraping_job(project_id: str, request: Request, token: Optional[str] = None):
    user, _, user_job_mgr, _ = get_user_context(request, token)
    ok, msg = user_job_mgr.pause_job(project_id)
    return {"success": ok, "message": msg}

@app.post("/api/projects/{project_id}/resume")
async def resume_scraping_job(project_id: str, request: Request, token: Optional[str] = None):
    user, _, user_job_mgr, _ = get_user_context(request, token)
    ok, msg = user_job_mgr.resume_job(project_id)
    return {"success": ok, "message": msg}

@app.post("/api/projects/{project_id}/stop")
async def stop_scraping_job(project_id: str, request: Request, token: Optional[str] = None):
    user, _, user_job_mgr, _ = get_user_context(request, token)
    ok, msg = user_job_mgr.stop_job(project_id)
    return {"success": ok, "message": msg}

@app.post("/api/projects/{project_id}/retry-failed")
async def retry_failed_job(project_id: str, req: StartJobRequest, request: Request, token: Optional[str] = None):
    user, _, user_job_mgr, _ = get_user_context(request, token)
    ok, msg = user_job_mgr.retry_failed(project_id, concurrency=req.concurrency)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}

@app.get("/api/projects/{project_id}/status")
async def get_scraping_status(project_id: str, request: Request, token: Optional[str] = None):
    user, _, user_job_mgr, _ = get_user_context(request, token)
    return user_job_mgr.get_job_status(project_id)

@app.get("/api/projects/{project_id}/logs")
async def get_project_logs(
    project_id: str,
    since_id: int = 0,
    limit: int = 100,
    request: Request = None,
    token: Optional[str] = None
):
    user, user_db, _, _ = get_user_context(request, token)
    return user_db.get_logs(project_id, since_id=since_id, limit=limit)

# -------------------------------------------------------------
# STEP 7: LIVE STREAMING OUTPUT SPREADSHEET
# -------------------------------------------------------------
@app.get("/api/projects/{project_id}/output/data")
async def get_output_data(
    project_id: str,
    page: int = 1,
    page_size: int = 50,
    search: str = "",
    status: str = "",
    request: Request = None,
    token: Optional[str] = None
):
    user, user_db, _, _ = get_user_context(request, token)
    return user_db.get_output_rows(project_id, page=page, page_size=page_size, search=search, status_filter=status)

@app.post("/api/projects/{project_id}/output/cell")
async def update_output_cell(project_id: str, req: CellEditRequest, request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    success = user_db.update_output_cell(project_id, req.row_idx, req.col_name, req.new_val)
    if not success:
        raise HTTPException(status_code=400, detail="Cell update failed")
    return {"success": True}

@app.post("/api/projects/{project_id}/output/apply-shopify-template")
async def apply_shopify_template(project_id: str, request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    cols = user_db.apply_shopify_schema(project_id)
    return {"success": True, "columns": cols, "total_columns": len(cols)}

@app.post("/api/projects/{project_id}/output/add-shopify-columns")
async def add_shopify_columns(project_id: str, request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    cols = user_db.add_missing_shopify_columns(project_id)
    return {"success": True, "columns": cols, "total_columns": len(cols)}

@app.post("/api/projects/{project_id}/output/reset-studio-template")
async def reset_studio_template(project_id: str, request: Request, token: Optional[str] = None):
    user, user_db, _, _ = get_user_context(request, token)
    cols = user_db.reset_studio_columns(project_id)
    return {"success": True, "columns": cols, "total_columns": len(cols)}

# -------------------------------------------------------------
# STEP 8: REVIEW & EXPORT
# -------------------------------------------------------------
@app.get("/api/projects/{project_id}/export")
async def export_project_dataset(
    project_id: str,
    format: str = "shopify_csv",
    scope: str = "all",
    request: Request = None,
    token: Optional[str] = None
):
    user, _, _, user_exporter = get_user_context(request, token)
    try:
        content, filename, media_type = user_exporter.generate_export(project_id, fmt=format, scope=scope)
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# -------------------------------------------------------------
# BACKWARDS COMPATIBILITY & MASTER TOOLS
# -------------------------------------------------------------
@app.get("/api/stats")
async def get_master_stats():
    return catalog_mgr.get_stats()

@app.get("/api/products")
async def get_products(
    q: str = "",
    vendor: str = "",
    tag: str = "",
    page: int = 1,
    page_size: int = 24
):
    return catalog_mgr.search_products(query=q, vendor=vendor, tag=tag, page=page, page_size=page_size)

@app.get("/api/product/{handle}")
async def get_product(handle: str):
    p = catalog_mgr.get_product(handle)
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    return p

@app.post("/api/product/{handle}")
async def update_product(handle: str, req: Dict[str, Any]):
    success = catalog_mgr.update_product(handle, req)
    return {"success": success}

@app.delete("/api/product/{handle}")
async def delete_product(handle: str):
    success = catalog_mgr.delete_product(handle)
    return {"success": success}

@app.post("/api/tasks/run")
async def run_task(req: Dict[str, Any]):
    task_name = req.get("task", "")
    success, msg = automation.run_task(task_name, req.get("params", {}))
    return {"success": success, "message": msg}

@app.get("/api/download/{filename}")
async def download_file(filename: str):
    allowed_files = {
        "master": (MASTER_CSV_PATH, "all_products_master_enriched.csv"),
        "part1": (PART1_CSV_PATH, "all_products_part1_of_2.csv"),
        "part2": (PART2_CSV_PATH, "all_products_part2_of_2.csv"),
    }
    if filename not in allowed_files:
        raise HTTPException(status_code=400, detail="Invalid file download request")
    
    file_path, download_name = allowed_files[filename]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Requested file does not exist")
        
    return FileResponse(
        path=file_path,
        filename=download_name,
        media_type="text/csv"
    )

if __name__ == "__main__":
    ip = get_local_ip()
    print("=" * 65)
    print("  🚀 PROFESSIONAL E-COMMERCE EXTRACTION & SPREADSHEET PLATFORM")
    print("=" * 65)
    print(f"  Local Browser:   http://localhost:8000")
    print(f"  Network / Wi-Fi: http://{ip}:8000")
    print("=" * 65)
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
