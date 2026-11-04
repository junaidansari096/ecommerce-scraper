import os
import re
import sys
import time
import threading
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from project_db import ProjectDatabase
from extractors import PlatformRegistry

class ProjectJobManager:
    def __init__(self, db: ProjectDatabase):
        self.db = db
        self.registry = PlatformRegistry()
        self.lock = threading.RLock()
        
        # In-memory execution state per project_id
        # { project_id: { is_running, is_paused, stop_requested, start_time, processed, total, speed, active_workers, ... } }
        self.jobs: Dict[str, Dict[str, Any]] = {}

    def _get_or_create_job_state(self, project_id: str) -> Dict[str, Any]:
        with self.lock:
            if project_id not in self.jobs:
                self.jobs[project_id] = {
                    "is_running": False,
                    "is_paused": False,
                    "stop_requested": False,
                    "start_time": 0.0,
                    "pause_time": 0.0,
                    "processed_count": 0,
                    "failed_count": 0,
                    "total_to_process": 0,
                    "speed_per_min": 0.0,
                    "active_workers": 0,
                    "current_worker_tasks": {}, # { worker_id: current_product_title }
                    "recent_logs": []
                }
            return self.jobs[project_id]

    def log_event(self, project_id: str, worker_id: str, message: str, level: str = "info"):
        self.db.add_log(project_id, worker_id, message, level)
        with self.lock:
            state = self._get_or_create_job_state(project_id)
            state["recent_logs"].append({
                "time": time.strftime("%H:%M:%S"),
                "worker": worker_id,
                "message": message,
                "level": level
            })
            if len(state["recent_logs"]) > 100:
                state["recent_logs"].pop(0)

    def start_job(self, project_id: str, concurrency: int = 2, retry_failed_only: bool = False) -> Tuple[bool, str]:
        with self.lock:
            state = self._get_or_create_job_state(project_id)
            if state["is_running"]:
                return False, "Project scraping job is already running!"

            project = self.db.get_project(project_id)
            if not project:
                return False, f"Project '{project_id}' not found."

            # Fetch rows to process
            inputs_resp = self.db.get_input_rows(project_id, page=1, page_size=100000)
            input_rows = inputs_resp.get("rows", [])
            if not input_rows:
                return False, "No input data found for this project. Please upload or add rows first."

            mapping = project.get("mapping", {})
            target_col = mapping.get("target_column") or (inputs_resp["columns"][0] if inputs_resp["columns"] else "")

            # If retrying failed only
            existing_outputs = {r["__row_idx__"]: r for r in self.db.get_all_output_data(project_id)}
            rows_to_run = []
            for r in input_rows:
                idx = r["__row_idx__"]
                if retry_failed_only:
                    if idx in existing_outputs and existing_outputs[idx]["__status__"] == "failed":
                        rows_to_run.append(r)
                else:
                    rows_to_run.append(r)

            if not rows_to_run:
                return False, "All products already successfully processed."

            state["is_running"] = True
            state["is_paused"] = False
            state["stop_requested"] = False
            state["start_time"] = time.time()
            state["total_to_process"] = len(rows_to_run)
            state["processed_count"] = 0
            state["failed_count"] = 0
            state["active_workers"] = 0
            state["current_worker_tasks"] = {}

            cfg = project.get("config", {}) or {}
            max_img = int(cfg.get("max_images", 5))
            self.db.set_output_columns(project_id, self.db.get_default_output_columns(max_img))

            self.db.update_project(project_id, {
                "status": "running",
                "concurrency": concurrency
            })

            # Launch background manager thread
            worker_thread = threading.Thread(
                target=self._run_job_orchestrator,
                args=(project_id, rows_to_run, target_col, project.get("platform", "amazon"), cfg, concurrency),
                daemon=True
            )
            worker_thread.start()
            self.log_event(project_id, "system", f"Job initialized: {len(rows_to_run)} items queued across {concurrency} workers.", "info")
            return True, "Scraping job started successfully."

    def pause_job(self, project_id: str) -> Tuple[bool, str]:
        with self.lock:
            state = self._get_or_create_job_state(project_id)
            if not state["is_running"]:
                return False, "Job is not running."
            state["is_paused"] = True
            state["pause_time"] = time.time()
            self.db.update_project(project_id, {"status": "paused"})
            self.log_event(project_id, "system", "Job paused by user.", "warn")
            return True, "Scraping job paused."

    def resume_job(self, project_id: str) -> Tuple[bool, str]:
        with self.lock:
            state = self._get_or_create_job_state(project_id)
            if not state["is_running"] or not state["is_paused"]:
                return False, "Job is not in a paused state."
            state["is_paused"] = False
            self.db.update_project(project_id, {"status": "running"})
            self.log_event(project_id, "system", "Job resumed.", "info")
            return True, "Scraping job resumed."

    def stop_job(self, project_id: str) -> Tuple[bool, str]:
        with self.lock:
            state = self._get_or_create_job_state(project_id)
            if not state["is_running"]:
                return False, "Job is not running."
            state["stop_requested"] = True
            self.log_event(project_id, "system", "Cancellation requested. Waiting for active workers to complete...", "warn")
            return True, "Cancellation signal sent."

    def get_job_status(self, project_id: str) -> Dict[str, Any]:
        with self.lock:
            state = self._get_or_create_job_state(project_id)
            project = self.db.get_project(project_id) or {}
            
            elapsed = 0.0
            if state["is_running"] and state["start_time"] > 0:
                elapsed = time.time() - state["start_time"]

            # Speed calculation
            speed = 0.0
            if elapsed > 2 and state["processed_count"] > 0:
                speed = round((state["processed_count"] / elapsed) * 60, 1)

            state["speed_per_min"] = speed

            # Calculate ETA
            eta_sec = 0
            remaining = max(0, state["total_to_process"] - state["processed_count"])
            if speed > 0 and remaining > 0:
                eta_sec = int((remaining / speed) * 60)

            progress_pct = int((state["processed_count"] / max(state["total_to_process"], 1)) * 100) if state["total_to_process"] > 0 else 0

            return {
                "project_id": project_id,
                "project_name": project.get("name", "Untitled Project"),
                "status": project.get("status", "draft"),
                "is_running": state["is_running"],
                "is_paused": state["is_paused"],
                "total_to_process": state["total_to_process"],
                "processed_count": state["processed_count"],
                "failed_count": state["failed_count"],
                "pending_count": remaining,
                "progress_percent": progress_pct,
                "active_workers": state["active_workers"],
                "speed_per_min": speed,
                "elapsed_seconds": int(elapsed),
                "eta_seconds": eta_sec,
                "worker_tasks": dict(state["current_worker_tasks"]),
                "recent_logs": list(state["recent_logs"][-20:])
            }

    def _run_job_orchestrator(self, project_id: str, rows_to_run: List[Dict[str, Any]], target_col: str, platform_id: str, config: Dict[str, Any], concurrency: int):
        extractor = self.registry.get_extractor(platform_id)
        state = self._get_or_create_job_state(project_id)

        def worker_task(worker_num: int, row_data: Dict[str, Any]):
            worker_id = f"worker-{worker_num}"
            while state["is_paused"]:
                time.sleep(0.5)
                if state["stop_requested"]:
                    return None

            if state["stop_requested"]:
                return None

            row_idx = row_data["__row_idx__"]
            query = str(row_data.get(target_col, "")).strip()
            if not query:
                # Fallback to any non-empty column
                for k, v in row_data.items():
                    if not k.startswith("__") and str(v).strip():
                        query = str(v).strip()
                        break

            with self.lock:
                state["active_workers"] += 1
                state["current_worker_tasks"][worker_id] = query[:30]

            self.log_event(project_id, worker_id, f"Processing row #{row_idx + 1}: '{query[:35]}...'", "info")

            try:
                # Platform-specific extraction
                result = extractor.extract(query, config)
                
                # Merge original row data with enriched fields
                merged = dict(row_data)
                for k, v in result.items():
                    if k not in ["Images"]: # keep primitive types
                        merged[f"Enriched_{k}"] = v
                        merged[k] = v

                # Also populate standard Shopify columns so they are immediately available
                title_val = merged.get("Title") or merged.get("Product Name") or query
                handle_val = re.sub(r'[^a-zA-Z0-9]+', '-', str(title_val).lower()).strip('-')[:80]
                merged["URL handle"] = handle_val
                if "Product image URL" not in merged or not merged["Product image URL"]:
                    merged["Product image URL"] = result.get("Primary_Image", "")
                if "Image position" not in merged or not merged["Image position"]:
                    merged["Image position"] = "1"
                if "Compare-at price" not in merged or not merged["Compare-at price"]:
                    merged["Compare-at price"] = result.get("Compare_at_Price", "")
                if "Published on online store" not in merged:
                    merged["Published on online store"] = "TRUE"
                if "Requires shipping" not in merged:
                    merged["Requires shipping"] = "TRUE"
                if "Charge tax" not in merged:
                    merged["Charge tax"] = "TRUE"
                if "Fulfillment service" not in merged:
                    merged["Fulfillment service"] = "manual"
                if "Inventory tracker" not in merged:
                    merged["Inventory tracker"] = "shopify"
                if "Inventory quantity" not in merged:
                    merged["Inventory quantity"] = "50"
                if "Continue selling when out of stock" not in merged:
                    merged["Continue selling when out of stock"] = "DENY"
                if "Gift card" not in merged:
                    merged["Gift card"] = "FALSE"
                if "Option1 name" not in merged:
                    merged["Option1 name"] = "Title"
                if "Option1 value" not in merged:
                    merged["Option1 value"] = "Default Title"
                if "SEO title" not in merged:
                    merged["SEO title"] = title_val
                if "SEO description" not in merged:
                    desc_clean = re.sub(r'<[^>]+>', '', str(result.get("Description", ""))).strip()
                    merged["SEO description"] = desc_clean[:320]

                # Save immediately to database
                self.db.save_output_row(project_id, row_idx, merged, status="completed", error_msg="")
                self.log_event(project_id, worker_id, f"✅ Completed #{row_idx + 1}: Vendor '{result.get('Vendor')}', Price: ₹{result.get('Price')}", "success")
                
                with self.lock:
                    state["processed_count"] += 1
                return True

            except Exception as e:
                err_msg = str(e)
                failed_entry = dict(row_data)
                failed_entry["Status"] = "Failed"
                failed_entry["Error"] = err_msg
                self.db.save_output_row(project_id, row_idx, failed_entry, status="failed", error_msg=err_msg)
                self.log_event(project_id, worker_id, f"❌ Error row #{row_idx + 1}: {err_msg}", "error")
                with self.lock:
                    state["processed_count"] += 1
                    state["failed_count"] += 1
                return False

            finally:
                with self.lock:
                    state["active_workers"] = max(0, state["active_workers"] - 1)
                    state["current_worker_tasks"].pop(worker_id, None)

        # Execute in thread pool with concurrency
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = []
            for i, r in enumerate(rows_to_run):
                worker_idx = (i % concurrency) + 1
                futures.append(executor.submit(worker_task, worker_idx, r))

            for f in as_completed(futures):
                if state["stop_requested"]:
                    break
                try:
                    f.result()
                except Exception as e:
                    self.log_event(project_id, "system", f"Worker unhandled: {e}", "error")

        with self.lock:
            state["is_running"] = False
            state["is_paused"] = False
            final_status = "canceled" if state["stop_requested"] else "completed"
            self.db.update_project(project_id, {"status": final_status})
            self.log_event(project_id, "system", f"Scraping job finished with status: {final_status.upper()}.", "success" if final_status == "completed" else "warn")
