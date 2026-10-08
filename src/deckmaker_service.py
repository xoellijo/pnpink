# -*- coding: utf-8 -*-
"""Shared DeckMaker application state and commands, independent of its UI."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
import os
import subprocess
import sys
import threading
import time
import traceback
import webbrowser

import deckmaker_jobs as JOBS
import deckmaker_paths as DMPATHS
import deckmaker_play as PLAY
import deckmaker_preview as PREVIEW
import deckmaker_runner as RUNNER
import deckmaker_spritesheet as SPRITESHEET
import dataset as DATASET
from deckmaker_types import APP_VERSION, OTHER_EXPORT_FORMATS, AppRequest, ExportOptions
import const as CONST
import gui as PROGRESS
import icc_profiles as ICC
import image_preflight as PREFLIGHT
import inkscape_cli as INKSCAPE
import log as LOG
import prefs
import temp_paths as TEMPPATHS
import template_headers as TEMPLATE_HEADERS
import text_measure as TM


SOURCE_MODES = ("auto", "local_csv", "oauth", "public")
PREVIEW_PREFETCH_IDLE_DELAY_S = 0.4


class DeckMakerService:
    def __init__(self, initial: AppRequest | None = None):
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._events: deque[dict] = deque(maxlen=1200)
        self._logs: deque[str] = deque(maxlen=500)
        self._sequence = 0
        self._busy = False
        self._closed = False
        self._job = ""
        self._status = "Ready"
        self._activity = ""
        self._progress = {"label": "", "current": 0, "total": 0}
        self._request = AppRequest(template="")
        self._generated_output_ready = False
        self._dataset_source_invalid = False
        self._pending_play_publish = False
        self._sprite_selection = None
        self._sprite_message = "Select exactly one image, group or object in Inkscape."
        self._sprite_revision = 0
        self._template_revision = 0
        self._preview_path = ""
        self._preview_runtime_revision = f"{os.getpid()}-{time.time_ns()}"
        self._preview_revision = 0
        self._preview_label = ""
        self._preview_quality = ""
        self._preview_signature = ""
        self._preview_datasets = None
        self._preview_entries = []
        self._preview_asset_key = ""
        self._preview_base_path = ""
        self._preview_base_signature = ""
        self._preview_direction = 1
        self._preview_prefetch_generation = 0
        self._preview_prefetch_service = None
        self._preview_asset_locks = {}
        self._preview_asset_signatures = {}
        self._preview_dataset_signature = ""
        self._preview_monitor_stop = threading.Event()
        self._preview_monitor_wake = threading.Event()
        self._preview_monitor_error = ""
        self._sheet_editor_active_until = 0.0
        self._sheet_editor_final_poll = False
        self._templates = []
        self._warm_sheet_key = ()
        self._sheet_embed_gid = ""
        self._sheet_embed_status = ""
        self._google_authorization_url = ""
        self._text_query_service = TM.TextQueryService(
            timeout_s=60,
            profile_stem="deckmaker-preview-main",
        )
        self._text_query_generation = 0
        self._play_server = PLAY.PlayServer()
        self._static_options = self._build_static_options()
        LOG.add_listener(self._on_log_line)
        try:
            import gsheets_client_pkce as GS
            GS.set_authorization_listener(self._on_google_authorization)
        except Exception:
            pass
        self._refresh_templates()
        if initial:
            self.open_request(initial)
        self._preview_monitor_thread = threading.Thread(
            target=self._preview_monitor,
            name="deckmaker-preview-monitor",
            daemon=True,
        )
        self._preview_monitor_thread.start()

    def _emit(self, kind: str, **payload) -> None:
        with self._condition:
            self._sequence += 1
            self._events.append({"seq": self._sequence, "kind": kind, **payload})
            self._condition.notify_all()

    def _ensure_text_query_service(self):
        with self._lock:
            service = self._text_query_service
        try:
            service.wait_ready(timeout_s=60)
            if not service.healthy:
                raise RuntimeError("Inkscape shell is not running")
            return service
        except Exception:
            service.close()
            with self._lock:
                if service is self._text_query_service:
                    self._text_query_generation += 1
                    self._text_query_service = TM.TextQueryService(
                        timeout_s=60,
                        profile_stem=f"deckmaker-preview-main-{self._text_query_generation}",
                    )
                replacement = self._text_query_service
            replacement.wait_ready(timeout_s=60)
            return replacement

    def _ensure_preview_prefetch_service(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("DeckMaker service is closing")
            service = self._preview_prefetch_service
        if service is not None:
            try:
                service.wait_ready(timeout_s=60)
                if service.healthy:
                    return service
            except Exception:
                pass
            service.close()
        with self._lock:
            if self._closed:
                raise RuntimeError("DeckMaker service is closing")
            if service is self._preview_prefetch_service:
                self._preview_prefetch_service = TM.TextQueryService(
                    timeout_s=60,
                    profile_stem=f"deckmaker-preview-prefetch-{self._preview_prefetch_generation}",
                )
            replacement = self._preview_prefetch_service
        replacement.wait_ready(timeout_s=60)
        return replacement

    def _cancel_preview_prefetch(self) -> None:
        with self._lock:
            self._preview_prefetch_generation += 1
            service = self._preview_prefetch_service
            self._preview_prefetch_service = None
        if service is not None:
            service.close()

    def _on_log_line(self, line: str) -> None:
        text = str(line or "").rstrip()
        if not text:
            return
        with self._lock:
            self._logs.append(text)
        self._emit("log", text=text)

    def log(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        with self._lock:
            self._logs.append(text)
        self._emit("log", text=text)

    def activity(self, message: str) -> None:
        with self._lock:
            self._activity = str(message or "").strip()
        self._emit("activity", text=self._activity)

    def _on_google_authorization(self, url: str) -> None:
        with self._lock:
            self._google_authorization_url = str(url or "").strip()
            if self._google_authorization_url and self._busy:
                self._activity = "Authorize Google Sheets in your browser to continue"
        self._emit("state")

    def progress(self, label: str, current: int, total: int) -> None:
        value = {"label": str(label or "").strip(), "current": int(current or 0), "total": int(total or 0)}
        with self._lock:
            self._progress = value
            self._activity = value["label"]
        self._emit("progress", **value)

    def _set_status(self, status: str) -> None:
        with self._lock:
            self._status = str(status or "Ready").strip() or "Ready"
        self._emit("state")

    @staticmethod
    def _file_mtime_ns(path: str) -> int:
        try:
            return int(os.stat(DMPATHS.normalize(path)).st_mtime_ns)
        except Exception:
            return 0

    def open_request(self, request: AppRequest) -> None:
        template = DMPATHS.normalize(request.template)
        if template:
            try:
                if prefs.get("deckmaker_web_last_template", "") != template:
                    prefs.set("deckmaker_web_last_template", template, save=True)
            except Exception:
                pass
        sheet_id = str(request.sheet_id or "").strip()
        sheet_range = str(request.sheet_range or "").strip()
        source_mode = str(request.dataset_source_mode or "").strip().lower()
        persisted_sheet_gid = ""
        record = {}
        try:
            import dataset_state as DSTATE
        except Exception:
            DSTATE = None
        if DSTATE is not None:
            try:
                DSTATE.remember_svg(template)
            except Exception as error:
                LOG.w("[deckmaker.web] Could not persist the template: %s", error)
            try:
                self._templates = DSTATE.list_svg_templates()
            except Exception:
                pass
            try:
                record = DSTATE.get_gsheet_for_svg(template) or {}
            except Exception:
                pass
        if not sheet_id:
            sheet_id = str(record.get("sheet_id") or "").strip()
            sheet_range = str(record.get("sheet_range") or "").strip()
            source_mode = source_mode or str(record.get("access_mode") or "").strip().lower()
        if (
            str(record.get("sheet_id") or "").strip() == sheet_id
            and str(record.get("sheet_range") or "").strip() == sheet_range
        ):
            persisted_sheet_gid = str(record.get("sheet_gid") or "").strip()
        if source_mode == "oauth" and sheet_range.isdigit():
            source_mode = "auto"
        source_mode = self._detect_source_mode(template, sheet_id, source_mode)
        snapshot = DMPATHS.normalize(request.snapshot_path) if request.snapshot_path else ""
        sprite_selection, sprite_message = SPRITESHEET.inspect_selection(
            template, snapshot, tuple(request.selected_ids or ()),
        )
        with self._lock:
            self._request = AppRequest(
                template=template,
                snapshot_path=snapshot,
                sheet_id=sheet_id,
                sheet_range=sheet_range,
                dataset_source_mode=source_mode,
                log_level=request.log_level,
                autorun=bool(request.autorun),
                selected_ids=tuple(request.selected_ids or ()),
            )
            self._warm_sheet_key = ()
            self._sheet_embed_gid = self._initial_sheet_gid(self._request) or (
                persisted_sheet_gid if persisted_sheet_gid.isdigit() else ""
            )
            self._sheet_embed_status = (
                "ready" if self._sheet_embed_gid else ("resolving" if sheet_id else "")
            )
            self._sprite_selection = sprite_selection
            self._sprite_message = sprite_message
            self._sprite_revision += 1
            self._template_revision += 1
            self._preview_path = ""
            self._preview_label = ""
            self._preview_signature = ""
            self._preview_datasets = None
            self._preview_entries = []
            self._preview_asset_key = ""
            self._preview_base_path = ""
            self._preview_base_signature = ""
            self._preview_asset_signatures = {}
            self._preview_dataset_signature = ""
            self._preview_prefetch_generation += 1
            self._preview_revision += 1
            self._sheet_editor_active_until = 0.0
            self._sheet_editor_final_poll = False
            self._dataset_source_invalid = False
            self._generated_output_ready = self._has_dataset_source(template, sheet_id) and os.path.isfile(DMPATHS.output_svg(template))
            self._status = "Template received" if self._has_dataset_source(template, sheet_id) else "Choose CSV or Google Sheet source"
        self.log(f"Template: {os.path.basename(template)}")
        if snapshot and os.path.isfile(snapshot):
            self.log("Using current unsaved Inkscape document snapshot")
        self._emit("state")
        if request.autorun:
            self.generate()
        else:
            self._warm_google_session()

    def _refresh_templates(self) -> None:
        try:
            import dataset_state as DSTATE
            self._templates = DSTATE.list_svg_templates()
        except Exception:
            self._templates = []

    def select_template(self, template: str) -> dict:
        with self._lock:
            if self._busy:
                raise RuntimeError("Wait for the current DeckMaker operation to finish")
            current = self._request
            known = {
                os.path.normcase(DMPATHS.normalize(record.get("path") or "")): record.get("path")
                for record in self._templates
            }
        target = known.get(os.path.normcase(DMPATHS.normalize(template)))
        if not target or not os.path.isfile(target):
            raise ValueError("Unknown SVG template; launch DeckMaker Web from that document in Inkscape first")
        if os.path.normcase(DMPATHS.normalize(target)) == os.path.normcase(DMPATHS.normalize(current.template)):
            self.refresh_previews()
            return self.state()
        self.open_request(AppRequest(template=target, log_level=current.log_level))
        return self.state()

    def template_preview(self) -> str:
        with self._lock:
            template = self._request.template
        if not template or not os.path.isfile(template):
            raise RuntimeError("No active SVG template")
        return template

    def _warm_google_session(self, retry: int = 0) -> None:
        with self._lock:
            request = self._request
            sheet_id = str(request.sheet_id or "").strip()
            source_mode = str(request.dataset_source_mode or "").strip().lower()
            warm_key = (sheet_id, request.sheet_range, request.template, source_mode)
            if self._busy or not sheet_id or source_mode == "local_csv" or warm_key == self._warm_sheet_key:
                return
            self._warm_sheet_key = warm_key
            if not self._sheet_embed_gid:
                self._sheet_embed_status = "resolving"

        def worker():
            resolved = False
            try:
                import gsheets_client_pkce as GS

                if GS.warm_session(spreadsheet_id=sheet_id, interactive=False):
                    sheets = GS.list_sheet_properties(sheet_id, interactive=False)
                    selected_gid = self._select_sheet_gid(request, sheets)
                    with self._lock:
                        if warm_key == self._warm_sheet_key and selected_gid:
                            self._sheet_embed_gid = selected_gid
                            self._sheet_embed_status = "ready"
                            resolved = True
                    if resolved:
                        try:
                            import dataset_state as DSTATE
                            DSTATE.set_gsheet_gid_for_svg(
                                request.template, request.sheet_id, request.sheet_range, selected_gid,
                            )
                        except Exception:
                            pass
                        self._set_status("Google Sheets session ready")
            except Exception as error:
                LOG.w("[deckmaker.web] Google Sheets tab resolution failed: %s", error)
            finally:
                with self._lock:
                    if not resolved and warm_key == self._warm_sheet_key:
                        self._warm_sheet_key = ()
                self._emit("state")
                if not resolved:
                    with self._lock:
                        has_fallback = bool(self._sheet_embed_gid)
                        if not has_fallback:
                            self._sheet_embed_status = "error"
                    if not has_fallback:
                        self._set_status("Could not resolve the Google Sheets tab")

        threading.Thread(target=worker, name="pnpink-gsheets-auth-warmup", daemon=True).start()

    @staticmethod
    def _initial_sheet_gid(request: AppRequest) -> str:
        source_mode = str(request.dataset_source_mode or "").strip().lower()
        selector = str(request.sheet_range or "").strip()
        if selector.isdigit() and source_mode != "local_csv":
            return selector
        if source_mode == "public":
            return "0" if not selector else ""
        return ""

    @staticmethod
    def _select_sheet_gid(request: AppRequest, sheets: list[dict]) -> str:
        selector = str(request.sheet_range or "").strip()
        if selector.isdigit():
            return selector
        sheet_name = (
            selector.split("!", 1)[0].strip().strip("'")
            if selector and not selector.isdigit()
            else os.path.splitext(os.path.basename(request.template))[0]
        )
        selected = next(
            (item for item in sheets if str(item.get("title") or "").strip().lower() == sheet_name.lower()),
            sheets[0] if sheets else None,
        )
        return str(selected.get("sheetId")) if selected is not None else ""

    def _detect_source_mode(self, template: str, sheet_id: str, explicit: str = "") -> str:
        mode = str(explicit or "").strip().lower()
        if mode == "local_csv":
            return mode
        if sheet_id:
            if mode in {"auto", "oauth", "public"}:
                return mode
            try:
                import dataset_state as DSTATE

                record = DSTATE.get_gsheet_for_svg(template) or {}
                if str(record.get("sheet_id") or "").strip() == sheet_id:
                    access_mode = str(record.get("access_mode") or "").strip().lower()
                    if access_mode in {"oauth", "public"}:
                        return access_mode
            except Exception:
                pass
            return ""
        if os.path.isfile(os.path.splitext(template)[0] + ".csv"):
            return "local_csv"
        return mode if mode in SOURCE_MODES else ""

    def _set_google_mode(self, request: AppRequest, mode: str) -> bool:
        with self._lock:
            current = self._request
            if current.sheet_id != request.sheet_id or current.sheet_range != request.sheet_range:
                return False
            current_gid = self._sheet_embed_gid
            self._request = replace(current, dataset_source_mode=mode)
        try:
            import dataset_state as DSTATE
            DSTATE.set_gsheet_for_svg(request.template, request.sheet_id, request.sheet_range, mode)
        except Exception:
            pass
        with self._lock:
            self._warm_sheet_key = ()
            self._sheet_embed_gid = self._initial_sheet_gid(self._request) or current_gid
            self._sheet_embed_status = "ready" if self._sheet_embed_gid else "resolving"
        self._emit("state")
        self._warm_google_session()
        return True

    @staticmethod
    def _has_dataset_source(template: str, sheet_id: str) -> bool:
        return bool(str(sheet_id or "").strip() or os.path.isfile(os.path.splitext(template)[0] + ".csv"))

    def _can_generate(self) -> bool:
        request = self._request
        return bool(
            request.template and os.path.isfile(request.template)
            and not self._dataset_source_invalid
            and self._has_dataset_source(request.template, request.sheet_id)
        )

    def _can_open_output(self) -> bool:
        return self._can_generate() and self._generated_output_ready and os.path.isfile(DMPATHS.output_svg(self._request.template))

    def _can_export(self) -> bool:
        return bool(
            self._request.template
            and os.path.isfile(self._request.template)
            and self._selected_export_outputs()
        )

    def _preferences(self) -> dict:
        profiles = set(prefs.get_pdf_profiles())
        return {
            "autoOpen": prefs.get_auto_open(),
            "autoExport": prefs.get_auto_export(),
            "exportPdf": prefs.get_export_pdf(),
            "exportPdfx": prefs.get_export_pdfx(),
            "exportOther": prefs.get_export_png(),
            "pdfDefault": "default" in profiles,
            "pdfScreen": "screen" in profiles,
            "pdfEbook": "ebook" in profiles,
            "pdfPrinter": "printer" in profiles,
            "pdfPrepress": "prepress" in profiles,
            "pdfRasterMode": prefs.get_pdf_raster_mode(),
            "pdfCmykIcc": ICC.display_value(prefs.get_pdf_cmyk_icc()),
            "pdfPureBlack": prefs.get_pdf_cmyk_pure_black_text(),
            "pdfxVersion": prefs.get_pdfx_version(),
            "otherFormat": prefs.get_export_other_format(),
            "otherUnit": prefs.get_export_other_unit(),
            "otherSelection": prefs.get_export_other_pages(),
            "exportDpi": prefs.get_export_dpi(),
            "jpegQuality": prefs.get_export_jpeg_quality(),
            "cutTemplate": prefs.get_export_cut_template(),
            "cutFormat": prefs.get_export_cut_template_format(),
            "splitSvg": prefs.get_split_svg_output(),
            "splitMode": prefs.get_split_svg_mode(),
            "splitParts": prefs.get_split_svg_parts() or 0,
            "splitPages": prefs.get_split_svg_limit_pages() or 0,
            "splitRecords": prefs.get_split_svg_limit_records() or 0,
            "splitMb": prefs.get_split_svg_chunk_mb_optional() or 0,
            "inkscapeWorkers": prefs.get_inkscape_shell_workers(),
            "templateEngine": prefs.get_template_engine(),
            "consoleLog": prefs.get_console_level(),
            "fileLog": prefs.get_file_level(),
            "imagePreflight": prefs.get_image_preflight(),
        }

    @staticmethod
    def _build_static_options() -> dict:
        return {
            "sourceModes": list(SOURCE_MODES),
            "formats": list(JOBS.available_other_export_formats(OTHER_EXPORT_FORMATS)),
            "units": ["pages", "items", "ids"],
            "rasterModes": ["png", "jpeg", "png_alpha", "inkscape", "none"],
            "iccProfiles": ICC.display_choices(),
            "pdfxVersions": ["1a", "3", "4"],
            "cutFormats": ["svg", "dxf", "png"],
            "templateEngines": ["legacy", "composed", "composed-instance"],
            "logLevels": ["trace", "debug", "info", "warn", "error", "none", "global"],
            "cardPresets": {
                name: {"width": size[0], "height": size[1]}
                for name, size in CONST.CARD_SIZES_MM.items()
            },
        }

    def state(self, *, include_logs: bool = True) -> dict:
        with self._lock:
            request = self._request
            has_dataset = self._has_dataset_source(request.template, request.sheet_id)
            if self._busy and self._job == "preview":
                preview_message = "Rendering preview..."
            elif not request.template:
                preview_message = "Missing template..."
            elif not has_dataset:
                preview_message = "Missing dataset..."
            elif not self._preview_entries:
                preview_message = "No assets available to preview."
            else:
                preview_message = "Choose an asset to render its preview."
            dataset_path = os.path.splitext(request.template)[0] + ".csv" if request.template else ""
            dataset_name = os.path.basename(dataset_path) if dataset_path else "No dataset"
            dataset_display = dataset_name
            if request.sheet_id and request.dataset_source_mode != "local_csv":
                dataset_path = f"https://docs.google.com/spreadsheets/d/{request.sheet_id}/edit"
                if request.sheet_range.isdigit():
                    dataset_path += f"#gid={request.sheet_range}"
                template_stem = os.path.splitext(os.path.basename(request.template))[0] if request.template else "dataset"
                dataset_display = f"gsheet://{template_stem}"
            state = {
                "version": APP_VERSION,
                "seq": self._sequence,
                "template": request.template,
                "templateName": os.path.basename(request.template) if request.template else "No template",
                "templateRevision": self._template_revision,
                "templates": list(self._templates),
                "datasetName": dataset_name,
                "datasetDisplay": dataset_display,
                "datasetPath": dataset_path,
                "snapshot": request.snapshot_path,
                "sheetId": request.sheet_id,
                "sheetRange": request.sheet_range,
                "sheetGid": self._sheet_embed_gid,
                "sheetStatus": self._sheet_embed_status,
                "googleAuthorizationUrl": self._google_authorization_url,
                "sourceMode": request.dataset_source_mode,
                "status": self._status,
                "activity": self._activity,
                "progress": dict(self._progress),
                "busy": self._busy,
                "job": self._job,
                "canGenerate": self._can_generate(),
                "canOpenOutput": self._can_open_output(),
                "canExport": self._can_export(),
                "preview": {
                    "available": bool(self._preview_path and os.path.isfile(self._preview_path)),
                    "message": preview_message,
                    "revision": self._preview_revision,
                    "label": self._preview_label,
                    "quality": self._preview_quality,
                    "url": f"/api/preview/image?rev={self._preview_runtime_revision}-{self._preview_revision}",
                    "asset": self._preview_asset_key,
                    "assets": [
                        {"value": entry["key"], "label": entry["label"]}
                        for entry in self._preview_entries
                    ],
                    "index": next(
                        (index for index, entry in enumerate(self._preview_entries) if entry["key"] == self._preview_asset_key),
                        -1,
                    ),
                },
                "spritesheet": (
                    self._sprite_selection.public(self._sprite_revision)
                    if self._sprite_selection
                    else SPRITESHEET.unavailable(self._sprite_message)
                ),
                "play": {
                    "serverRunning": self._play_server.running,
                    "serverPort": self._play_server.port if self._play_server.running else 0,
                    "stores": [
                        {"id": "local", "label": "Local", "enabled": True, "available": True},
                        {"id": "s3", "label": "S3", "enabled": False, "available": False},
                        {"id": "r2", "label": "R2", "enabled": False, "available": False},
                    ],
                },
                "preferences": self._preferences(),
                "options": self._static_options,
            }
            if include_logs:
                state["logs"] = list(self._logs)
            return state

    def spritesheet_preview(self) -> str:
        with self._lock:
            selection = self._sprite_selection
        if not selection:
            raise RuntimeError(self._sprite_message or "No spritesheet selection")
        return SPRITESHEET.render_preview(selection)

    def preview_image(self) -> str:
        with self._lock:
            path = self._preview_path
        if not path or not os.path.isfile(path):
            raise RuntimeError("No preview has been rendered")
        return path

    def wait_events(self, after: int, timeout: float = 20.0) -> dict:
        deadline = time.monotonic() + max(0.1, min(float(timeout or 20), 30.0))
        with self._condition:
            while self._sequence <= after:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            if self._sequence > after:
                batch_deadline = min(deadline, time.monotonic() + 0.05)
                while True:
                    remaining = batch_deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._condition.wait(remaining)
            events = [event for event in self._events if int(event["seq"]) > after]
            return {
                "seq": self._sequence,
                "events": events,
                "state": self.state(include_logs=False) if events else None,
            }

    def update(self, values: dict) -> dict:
        if "templatePath" in values:
            selected = self.select_template(str(values.get("templatePath") or ""))
            values = {key: value for key, value in values.items() if key != "templatePath"}
            if not values:
                return selected
        dataset_source_changed = any(key in values for key in ("sheetId", "sheetRange", "sourceMode"))
        with self._lock:
            request = self._request
            current_gid = self._sheet_embed_gid
            source_mode = str(values.get("sourceMode", request.dataset_source_mode) or "").strip().lower()
            sheet_id = str(values.get("sheetId", request.sheet_id) or "").strip()
            sheet_range = str(values.get("sheetRange", request.sheet_range) or "").strip()
            if source_mode == "oauth" and sheet_range.isdigit():
                source_mode = "auto"
            self._request = replace(
                request,
                sheet_id=sheet_id,
                sheet_range=sheet_range,
                dataset_source_mode=self._detect_source_mode(request.template, sheet_id, source_mode),
            )
            if dataset_source_changed:
                self._warm_sheet_key = ()
                same_sheet = sheet_id == request.sheet_id and sheet_range == request.sheet_range
                self._sheet_embed_gid = self._initial_sheet_gid(self._request) or (current_gid if same_sheet else "")
                self._sheet_embed_status = (
                    "ready" if self._sheet_embed_gid else ("resolving" if sheet_id else "")
                )
            self._dataset_source_invalid = False
            if dataset_source_changed:
                self._generated_output_ready = False
                self._preview_path = ""
                self._preview_label = ""
                self._preview_signature = ""
                self._preview_datasets = None
                self._preview_entries = []
                self._preview_base_path = ""
                self._preview_base_signature = ""
                self._preview_asset_signatures = {}
                self._preview_dataset_signature = ""
                self._preview_prefetch_generation += 1
                self._preview_revision += 1
                self._status = "Ready" if self._has_dataset_source(self._request.template, sheet_id) else "Choose CSV or Google Sheet source"
                self._activity = ""
        setters = {
            "autoOpen": prefs.set_auto_open,
            "autoExport": prefs.set_auto_export,
            "exportPdf": prefs.set_export_pdf,
            "exportPdfx": prefs.set_export_pdfx,
            "exportOther": prefs.set_export_png,
            "pdfRasterMode": prefs.set_pdf_raster_mode,
            "pdfPureBlack": prefs.set_pdf_cmyk_pure_black_text,
            "pdfxVersion": prefs.set_pdfx_version,
            "otherFormat": prefs.set_export_other_format,
            "otherUnit": prefs.set_export_other_unit,
            "otherSelection": prefs.set_export_other_pages,
            "exportDpi": prefs.set_export_dpi,
            "jpegQuality": prefs.set_export_jpeg_quality,
            "cutTemplate": prefs.set_export_cut_template,
            "cutFormat": prefs.set_export_cut_template_format,
            "splitSvg": prefs.set_split_svg_output,
            "splitMode": prefs.set_split_svg_mode,
            "splitParts": prefs.set_split_svg_parts,
            "splitPages": prefs.set_split_svg_limit_pages,
            "splitRecords": prefs.set_split_svg_limit_records,
            "splitMb": prefs.set_split_svg_chunk_mb,
            "inkscapeWorkers": prefs.set_inkscape_shell_workers,
            "consoleLog": prefs.set_console_level,
            "fileLog": prefs.set_file_level,
            "imagePreflight": prefs.set_image_preflight,
        }
        for key, setter in setters.items():
            if key in values:
                setter(values[key])
        profile_keys = {
            "pdfDefault": "default", "pdfScreen": "screen", "pdfEbook": "ebook",
            "pdfPrinter": "printer", "pdfPrepress": "prepress",
        }
        if any(key in values for key in profile_keys):
            current = self._preferences()
            prefs.set_pdf_profiles([name for key, name in profile_keys.items() if bool(values.get(key, current[key]))])
        if "pdfCmykIcc" in values:
            prefs.set_pdf_cmyk_icc(ICC.preference_value(values["pdfCmykIcc"]))
        if "templateEngine" in values:
            engine = str(values["templateEngine"] or "composed").strip().lower()
            prefs.set("template_engine", engine if engine in {"legacy", "composed", "composed-instance"} else "composed", save=True)
        preview_asset = str(values.get("previewAsset") or "").strip() if "previewAsset" in values else ""
        try:
            if self._request.sheet_id:
                import dataset_state as DSTATE
                DSTATE.set_gsheet_for_svg(
                    self._request.template, self._request.sheet_id, self._request.sheet_range,
                    self._request.dataset_source_mode,
                )
        except Exception:
            pass
        self._emit("state")
        if dataset_source_changed:
            self._warm_google_session()
        if preview_asset:
            with self._lock:
                has_cached_dataset = bool(self._preview_datasets)
            self.preview(
                force=False,
                refresh=not has_cached_dataset,
                asset_key=preview_asset,
            )
        return self.state()

    def set_sheet_editor_activity(self, active: bool) -> dict:
        now = time.monotonic()
        with self._lock:
            was_active = now < self._sheet_editor_active_until
            if active:
                self._sheet_editor_active_until = now + 4.0
            else:
                self._sheet_editor_active_until = 0.0
                self._sheet_editor_final_poll = was_active
        self._preview_monitor_wake.set()
        return {"active": bool(active)}

    def _begin(self, job: str, status: str, target) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._job = job
            self._status = status
            self._progress = {"label": "", "current": 0, "total": 0}
        self._emit("state")
        threading.Thread(target=self._run_job, args=(target,), name=f"pnpink-{job}", daemon=True).start()
        return True

    def _run_job(self, target) -> None:
        try:
            target()
        except Exception as error:
            LOG.w("[deckmaker.web] job failed: %s\n%s", str(error), traceback.format_exc())
            self._set_status(f"Error: {error}")
        finally:
            PROGRESS.clear_listener()
            with self._lock:
                self._busy = False
                self._job = ""
                self._activity = ""
            self._emit("state")

    def generate(self, *, play_after: bool = False) -> bool:
        if not self._can_generate():
            self._set_status("Choose CSV or Google Sheet source")
            return False
        if play_after:
            self._pending_play_publish = True

        def worker():
            request = self._request
            sheet_id = "" if request.dataset_source_mode == "local_csv" else request.sheet_id
            started = time.perf_counter()
            TEMPPATHS.cleanup_old_runs()

            def on_progress(kind: str, payload: dict):
                label = str(payload.get("label") or kind or "Working").strip()
                self.progress(label, int(payload.get("current") or 0), int(payload.get("total") or 0))

            PROGRESS.set_listener(on_progress)
            self.activity("Loading template and dataset...")
            text_query_service = self._ensure_text_query_service()
            effect = RUNNER.EngineEffect(
                request.template, sheet_id, request.sheet_range, request.log_level,
                request.dataset_source_mode, snapshot_path=request.snapshot_path,
            )
            import engine as ENGINE
            ENGINE.run(effect, APP_VERSION, text_query_service=text_query_service)
            try:
                import dataset_state as DSTATE
                if sheet_id:
                    access_mode = str(getattr(effect.options, "_dataset_access_mode", "") or "").strip().lower()
                    if access_mode in {"public", "oauth"}:
                        self._set_google_mode(request, access_mode)
                    DSTATE.set_gsheet_for_svg(request.template, sheet_id, request.sheet_range, access_mode)
            except Exception:
                pass
            if prefs.get_image_preflight(False):
                PREFLIGHT.write_text_report(DMPATHS.output_svg(request.template))
            with self._lock:
                self._generated_output_ready = os.path.isfile(DMPATHS.output_svg(request.template))
                self._dataset_source_invalid = not self._generated_output_ready
            if not self._generated_output_ready:
                raise RuntimeError("Generated output SVG not found")
            self._set_status(f"Done ({time.perf_counter() - started:.2f}s)")
            if self._pending_play_publish:
                self._pending_play_publish = False
                self._publish_play_assets(effect)

        accepted = self._begin("generate", "Generating...", worker)
        if not accepted and play_after:
            self._pending_play_publish = False
        return accepted

    @staticmethod
    def _preview_effect(request: AppRequest, symbol_base_path: str = ""):
        sheet_id = "" if request.dataset_source_mode == "local_csv" else request.sheet_id
        snapshot_path = str(request.snapshot_path or "").strip()
        if snapshot_path and os.path.isfile(snapshot_path) and os.path.isfile(request.template):
            try:
                if os.stat(request.template).st_mtime_ns > os.stat(snapshot_path).st_mtime_ns:
                    snapshot_path = ""
            except OSError:
                pass
        effect = RUNNER.EngineEffect(
            request.template, sheet_id, request.sheet_range, request.log_level,
            request.dataset_source_mode, snapshot_path=snapshot_path,
        )
        if symbol_base_path:
            PREVIEW.merge_symbol_base(effect, symbol_base_path)
        return effect

    def _preview_asset_lock(self, signature: str) -> threading.Lock:
        with self._lock:
            return self._preview_asset_locks.setdefault(signature, threading.Lock())

    def _ensure_preview_base(self, effect, datasets: list[dict]) -> tuple[str, str]:
        base = PREVIEW.symbol_base(effect, datasets)
        if not base["count"]:
            return "", ""
        path = str(base["path"])
        if not os.path.isfile(path):
            LOG.i(
                "[deckmaker.preview.symbols] cache miss count=%d path=%s",
                int(base["count"]), path,
            )
            self.activity(f'Preparing {base["count"]} reusable symbols...')
            PREVIEW.render_symbol_base(
                effect,
                APP_VERSION,
                base,
                text_query_service=self._ensure_text_query_service(),
            )
        else:
            LOG.i(
                "[deckmaker.preview.symbols] cache hit count=%d path=%s",
                int(base["count"]), path,
            )
        with self._lock:
            self._preview_base_path = path
            self._preview_base_signature = str(base["signature"])
        return path, str(base["signature"])

    @staticmethod
    def _export_preview_png(service, result: dict, target: str, dpi: int, *, reload_svg: bool = True) -> str:
        target = os.path.abspath(target)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        temporary = f"{os.path.splitext(target)[0]}.{os.getpid()}.{threading.get_ident()}.tmp.png"
        try:
            service.export_png(
                result["path"], temporary, result["fallback_area"],
                dpi=max(1, round(float(dpi))), compression=1, reload_svg=reload_svg,
            )
            os.replace(temporary, target)
            return target
        finally:
            if os.path.isfile(temporary):
                os.unlink(temporary)

    def _schedule_preview_prefetch(
        self,
        request: AppRequest,
        datasets: list[dict],
        entries: list[dict],
        current_key: str,
        base_path: str,
        base_signature: str,
        direction: int,
        asset_keys: set[str] | None = None,
    ) -> None:
        if len(entries) < 2:
            return
        with self._lock:
            self._preview_prefetch_generation += 1
            generation = self._preview_prefetch_generation

        current_index = next(
            (index for index, entry in enumerate(entries) if entry["key"] == current_key), 0,
        )
        step = 1 if direction >= 0 else -1
        keys = []
        for offset in (step, step * 2, -step):
            key = entries[(current_index + offset) % len(entries)]["key"]
            if key != current_key and key not in keys:
                keys.append(key)
        high_priority_keys = set(keys)
        for distance in range(3, len(entries)):
            key = entries[(current_index + step * distance) % len(entries)]["key"]
            if key != current_key and key not in keys:
                keys.append(key)
        if asset_keys is not None:
            keys = [key for key in keys if key in asset_keys]
            high_priority_keys.intersection_update(asset_keys)
        if not keys:
            return

        def worker():
            time.sleep(PREVIEW_PREFETCH_IDLE_DELAY_S)
            with self._lock:
                if self._closed or generation != self._preview_prefetch_generation:
                    return
            for key in keys:
                with self._lock:
                    if self._closed or generation != self._preview_prefetch_generation:
                        return
                started = time.perf_counter()
                try:
                    service = self._ensure_preview_prefetch_service()
                    effect = self._preview_effect(request, base_path)
                    effect._dm_iconify_mode = "offline"
                    prepared = PREVIEW.prepare(
                        effect, datasets=datasets, asset_key=key, base_signature=base_signature,
                    )
                    paths = PREVIEW.cache_paths(effect, prepared["signature"])
                    target = paths["high"] if key in high_priority_keys else paths["quick"]
                    dpi = 600 if key in high_priority_keys else 96
                    if os.path.isfile(target):
                        continue
                    with self._preview_asset_lock(prepared["signature"]):
                        if os.path.isfile(target):
                            continue
                        result = PREVIEW.render(
                            effect, APP_VERSION, text_query_service=service, prepared=prepared,
                        )
                        self._export_preview_png(service, result, target, dpi)
                    LOG.d(
                        "[deckmaker.preview.prefetch] asset=%s dpi=%d total_ms=%.1f",
                        key, dpi, (time.perf_counter() - started) * 1000.0,
                    )
                except Exception:
                    with self._lock:
                        if self._closed or generation != self._preview_prefetch_generation:
                            return
                    LOG.w("[deckmaker.preview.prefetch] asset=%s failed\n%s", key, traceback.format_exc())

        threading.Thread(target=worker, name="deckmaker-preview-prefetch", daemon=True).start()

    def _preview_monitor(self) -> None:
        """Refresh dataset values while preview is active, without occupying Inkscape."""
        while not self._preview_monitor_stop.is_set():
            with self._lock:
                sheet_active = time.monotonic() < self._sheet_editor_active_until
            self._preview_monitor_wake.wait(1.0 if sheet_active else None)
            self._preview_monitor_wake.clear()
            if self._preview_monitor_stop.is_set():
                break
            with self._lock:
                sheet_active = time.monotonic() < self._sheet_editor_active_until
                final_poll = self._sheet_editor_final_poll
                self._sheet_editor_final_poll = False
                if not sheet_active and not final_poll:
                    continue
                if self._busy or not self._preview_datasets or not self._preview_asset_key:
                    continue
                request = self._request
                known_signature = self._preview_dataset_signature
                current_key = self._preview_asset_key
                known_base_signature = self._preview_base_signature
                known_asset_signatures = dict(self._preview_asset_signatures)
                direction = self._preview_direction
            try:
                effect = self._preview_effect(request)
                with LOG.silence():
                    fresh = PREVIEW.prepare(effect, asset_key=current_key)
                datasets = fresh["source_datasets"]
                signature = PREVIEW.datasets_signature(datasets)
                if signature == known_signature:
                    self._preview_monitor_error = ""
                    continue
                base = PREVIEW.symbol_base(effect, datasets)
                asset_signatures = PREVIEW.asset_signatures(datasets)
                changed_keys = {
                    key
                    for key in set(known_asset_signatures) | set(asset_signatures)
                    if known_asset_signatures.get(key) != asset_signatures.get(key)
                }
                base_changed = str(base["signature"]) != known_base_signature
                resolved_key = fresh["entry"]["key"]
                with self._lock:
                    if request != self._request:
                        continue
                    self._preview_datasets = datasets
                    self._preview_dataset_signature = signature
                    self._preview_asset_signatures = asset_signatures
                    self._preview_entries = list(fresh["entries"])
                    self._preview_prefetch_generation += 1
                    if base_changed:
                        self._preview_base_path = ""
                        self._preview_base_signature = ""
                    base_path = self._preview_base_path
                    base_signature = self._preview_base_signature
                self._preview_monitor_error = ""
                LOG.i(
                    "[deckmaker.preview.poll] dataset changed; assets=%d base_changed=%s",
                    len(changed_keys), base_changed,
                )
                if base_changed or resolved_key in changed_keys:
                    if not self.preview(force=True, refresh=False, asset_key=resolved_key):
                        with self._lock:
                            self._preview_dataset_signature = known_signature
                        self._preview_monitor_wake.set()
                else:
                    self._schedule_preview_prefetch(
                        request,
                        datasets,
                        list(fresh["entries"]),
                        resolved_key,
                        base_path,
                        base_signature,
                        direction,
                        asset_keys=changed_keys,
                    )
            except Exception as error:
                message = str(error)
                if message != self._preview_monitor_error:
                    self._preview_monitor_error = message
                    LOG.w("[deckmaker.preview.poll] %s", message)

    def preview(self, *, force: bool = True, refresh: bool = True, movement: int = 0, asset_key: str = "") -> bool:
        if not self._can_generate():
            self._set_status("Choose CSV or Google Sheet source")
            return False

        self._cancel_preview_prefetch()

        with self._lock:
            cached_datasets = self._preview_datasets
            cached_entries = list(self._preview_entries)
            previous_key = self._preview_asset_key
            cached_asset_signatures = dict(self._preview_asset_signatures)
        if not refresh and not cached_datasets:
            self._set_status("Choose an asset to load the dataset first")
            return False

        direction = 1 if movement >= 0 else -1
        target_key = str(asset_key or previous_key or "").strip()
        if movement and cached_entries:
            current_index = next(
                (index for index, entry in enumerate(cached_entries) if entry["key"] == previous_key), 0,
            )
            target_key = cached_entries[(current_index + int(movement)) % len(cached_entries)]["key"]

        def worker():
            started = time.perf_counter()
            phase_started = started
            phase_timings: list[tuple[str, float]] = []

            def phase(name: str) -> None:
                nonlocal phase_started
                now = time.perf_counter()
                elapsed_ms = (now - phase_started) * 1000.0
                phase_timings.append((name, elapsed_ms))
                LOG.i(
                    "[deckmaker.preview.phase] asset=%s phase=%s ms=%.1f total_ms=%.1f",
                    target_key or previous_key or "first",
                    name,
                    elapsed_ms,
                    (now - started) * 1000.0,
                )
                phase_started = now

            request = self._request
            source_effect = self._preview_effect(request)
            phase("load-template")
            source_effect._dm_iconify_mode = "fast"
            self.activity("Refreshing dataset..." if refresh else "Selecting cached asset...")
            source_prepared = PREVIEW.prepare(
                source_effect, datasets=None if refresh else cached_datasets, asset_key=target_key,
            )
            phase("load-dataset")
            datasets = source_prepared["source_datasets"]
            base_path, base_signature = self._ensure_preview_base(source_effect, datasets)
            phase("resolve-symbol-cache")

            effect = self._preview_effect(request, base_path)
            phase("load-symbol-base")
            effect._dm_iconify_mode = "fast"
            prepared = PREVIEW.prepare(
                effect, datasets=datasets, asset_key=source_prepared["entry"]["key"],
                base_signature=base_signature,
            )
            phase("prepare-asset")
            entries = list(prepared["entries"])
            current_key = prepared["entry"]["key"]
            paths = PREVIEW.cache_paths(effect, prepared["signature"])
            asset_signatures = (
                PREVIEW.asset_signatures(datasets)
                if refresh or not cached_asset_signatures
                else cached_asset_signatures
            )
            phase("index-assets")
            with self._lock:
                self._preview_datasets = datasets
                self._preview_dataset_signature = PREVIEW.datasets_signature(datasets)
                self._preview_asset_signatures = asset_signatures
                self._preview_entries = entries
                self._preview_asset_key = current_key
                self._preview_direction = direction

            cache_hit = (not force) and os.path.isfile(paths["high"])
            result = None
            if not cache_hit:
                if (not force) and os.path.isfile(paths["quick"]):
                    with self._lock:
                        self._preview_path = paths["quick"]
                        self._preview_label = f'{prepared["label"]} · 1 item'
                        self._preview_quality = "draft"
                        self._preview_revision += 1
                    self._emit("state")
                with self._preview_asset_lock(prepared["signature"]):
                    cache_hit = (not force) and os.path.isfile(paths["high"])
                    if not cache_hit:
                        text_query_service = self._ensure_text_query_service()
                        phase("prepare-inkscape")
                        self.activity("Rendering SVG preview...")
                        result = PREVIEW.render(
                            effect, APP_VERSION,
                            text_query_service=text_query_service,
                            prepared=prepared,
                            force=force,
                        )
                        phase("render-svg")
                        self.activity("Rendering quick preview...")
                        preview_svg_loaded = False
                        if force or not os.path.isfile(paths["quick"]):
                            quick_path = self._export_preview_png(
                                text_query_service, result, result["quick_png_path"], 96,
                            )
                            preview_svg_loaded = True
                            with self._lock:
                                self._preview_path = quick_path
                                self._preview_label = f'{result["label"]} · {result["count"]} item(s)'
                                self._preview_quality = "draft"
                                self._preview_revision += 1
                            self._emit("state")
                            phase("export-96dpi-first-visible")
                        self.activity("Refining preview at 600 dpi...")
                        self._export_preview_png(
                            text_query_service,
                            result,
                            result["png_path"],
                            600,
                            reload_svg=not preview_svg_loaded,
                        )
                        phase("export-600dpi")

            with self._lock:
                self._preview_path = paths["high"]
                self._preview_label = f'{prepared["label"]} · 1 item'
                self._preview_quality = "high"
                self._preview_signature = prepared["signature"]
                self._preview_revision += 1
            self._emit("state")
            phase("publish")
            LOG.i(
                "[deckmaker.preview.timing] asset=%s %s total=%.1fms cached=%s",
                current_key,
                " ".join(f"{name}={elapsed:.1f}ms" for name, elapsed in phase_timings),
                (time.perf_counter() - started) * 1000.0,
                cache_hit,
            )
            LOG.i(
                "[deckmaker.preview] asset=%s total_ms=%.1f cached=%s bytes=%d",
                current_key,
                (time.perf_counter() - started) * 1000.0,
                cache_hit,
                os.path.getsize(paths["high"]),
            )
            self._set_status("Preview ready (cached)" if cache_hit else "Preview ready")
            self._schedule_preview_prefetch(
                request, datasets, entries, current_key, base_path, base_signature, direction,
            )

        return self._begin("preview", "Rendering preview...", worker)

    def refresh_previews(self) -> bool:
        if not self._can_generate():
            self._set_status("Choose CSV or Google Sheet source")
            return False
        with self._lock:
            if self._busy:
                return False
            request = self._request
            current_key = self._preview_asset_key
            self._preview_prefetch_generation += 1
            self._preview_datasets = None
            self._preview_entries = []
            self._preview_base_path = ""
            self._preview_base_signature = ""
            self._preview_asset_signatures = {}
            self._preview_dataset_signature = ""
        removed = PREVIEW.clear_cache(self._preview_effect(request))
        LOG.i("[deckmaker.preview] full cache refresh removed=%d template=%s", removed, request.template)
        return self.preview(force=True, refresh=True, asset_key=current_key)

    def _selected_export_outputs(self) -> list[str]:
        values = []
        if prefs.get_export_pdf() or prefs.get_export_pdfx():
            values.append("pdf")
        if prefs.get_export_png():
            values.append(prefs.get_export_other_format())
        if prefs.get_export_cut_template():
            values.append("cut")
        return values

    def _export_options(self) -> ExportOptions:
        formats = []
        if prefs.get_export_pdf() or prefs.get_export_pdfx():
            formats.append("pdf")
        if prefs.get_export_png():
            formats.append(prefs.get_export_other_format())
        return ExportOptions(
            formats=tuple(formats),
            pdf_profiles=tuple(prefs.get_pdf_profiles()),
            export_pdf_standard=prefs.get_export_pdf(),
            export_pdfx=prefs.get_export_pdfx(),
            pdf_raster_mode=prefs.get_pdf_raster_mode(),
            pdf_cmyk_icc=prefs.get_pdf_cmyk_icc(),
            pdf_cmyk_pure_black_text=prefs.get_pdf_cmyk_pure_black_text(),
            pdfx_version=prefs.get_pdfx_version(),
            export_dpi=prefs.get_export_dpi(),
            jpeg_quality=prefs.get_export_jpeg_quality(),
            other_format=prefs.get_export_other_format(),
            other_unit=prefs.get_export_other_unit(),
            other_pages=prefs.get_export_other_pages(),
            export_cut_template=prefs.get_export_cut_template(),
            cut_template_format=prefs.get_export_cut_template_format(),
        )

    def _export_now(self) -> None:
        if not self._selected_export_outputs():
            raise RuntimeError("No export outputs selected")
        template = self._request.template
        svg_path = DMPATHS.output_svg(template) if self._can_open_output() else template
        result = JOBS.export_project(
            template, svg_path, self._export_options(),
            JOBS.JobEvents(log=self.log, activity=self.activity, progress=self.progress),
        )
        self._set_status(result.status)

    def export(self) -> bool:
        if not self._can_export():
            self._set_status("No export outputs selected")
            return False
        return self._begin("export", "Exporting...", self._export_now)

    def open_output(self) -> bool:
        if not self._can_open_output():
            self._set_status("Generate output first")
            return False
        path = DMPATHS.output_svg(self._request.template)
        if not INKSCAPE.launch_gui(path):
            self._open_path(path)
        self._set_status("Opened output")
        return True

    @staticmethod
    def _open_path(path: str) -> None:
        target = DMPATHS.normalize(path)
        if not os.path.exists(target):
            raise FileNotFoundError(target)
        if os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", target])

    def _cached_play_previews(self, effect) -> dict[str, str]:
        datasets = list(getattr(effect, "_dm_loaded_datasets", None) or [])
        if not datasets:
            return {}
        with self._lock:
            if PREVIEW.datasets_signature(datasets) != self._preview_dataset_signature:
                return {}
            base_path = self._preview_base_path
            base_signature = self._preview_base_signature
        preview_effect = self._preview_effect(self._request, base_path)
        cached = {}
        for entry in PREVIEW.catalog(datasets):
            prepared = PREVIEW.prepare(
                preview_effect,
                datasets=datasets,
                asset_key=entry["key"],
                base_signature=base_signature,
            )
            high = PREVIEW.cache_paths(preview_effect, prepared["signature"])["high"]
            if os.path.isfile(high) and os.path.getsize(high) > 0:
                cached[entry["key"]] = high
        return cached

    def _publish_play_assets(self, effect=None):
        template = self._request.template
        svg_path = DMPATHS.output_svg(template)
        runtime = PLAY.find_runtime()
        cached_pngs = self._cached_play_previews(effect) if effect is not None else {}
        game_dir, game_id = PLAY.prepare_game(
            template, svg_path, runtime, cached_pngs=cached_pngs,
        )
        self._set_status("PnPPlay assets updated")
        return game_dir, game_id, runtime

    def _prepare_play(self, open_target: str = "designer") -> None:
        game_dir, game_id, runtime = self._publish_play_assets()
        if open_target == "lobby":
            url = self._play_server.lobby_url(game_dir, runtime)
        elif open_target == "game":
            url = self._play_server.open_game(game_dir, game_id, runtime)
        else:
            url = self._play_server.designer_url(game_dir, game_id, runtime)
        webbrowser.open(url, new=2)
        self._set_status("PnPPlay ready")

    def create_play_assets(self) -> bool:
        return self.generate(play_after=True)

    def toggle_play_server(self) -> bool:
        if self._play_server.running:
            if self._play_server.managed:
                self._play_server.close()
                self._set_status("PnPPlay server stopped")
            else:
                self._set_status("PnPPlay server is already running on port 8080")
            self._emit("state")
            return True

        def worker():
            template = self._request.template
            game_dir, _game_id = PLAY.game_project(template)
            runtime = PLAY.find_runtime()
            if not (game_dir / "game.json").is_file():
                raise RuntimeError("Create PnPPlay assets first")
            self._play_server.ensure(game_dir, runtime)
            self._set_status(f"PnPPlay server running on port {self._play_server.port}")

        return self._begin("play-server", "Starting PnPPlay server...", worker)

    def open_play(self, target: str) -> bool:
        def worker():
            template = self._request.template
            game_dir, game_id = PLAY.game_project(template)
            runtime = PLAY.find_runtime()
            if not (game_dir / "game.json").is_file():
                raise RuntimeError("Synchronize this project with PnPPlay first")
            if target == "lobby":
                url = self._play_server.lobby_url(game_dir, runtime)
            elif target == "game":
                url = self._play_server.open_game(game_dir, game_id, runtime)
            else:
                url = self._play_server.designer_url(game_dir, game_id, runtime)
            webbrowser.open(url, new=2)
            self._set_status("PnPPlay ready")

        return self._begin("play", "Opening PnPPlay...", worker)

    def open_auxiliary(self, target: str) -> bool:
        paths = {
            "preferences": prefs.ini_path(),
            "log": os.path.join(os.path.dirname(__file__), "pnpink.log"),
            "examples": DMPATHS.examples_dir(os.path.dirname(__file__)),
        }
        if target in paths:
            self._open_path(paths[target])
            return True
        urls = {
            "intro": "https://xoellijo.github.io/pnpink/intro/",
            "guide": "https://xoellijo.github.io/pnpink/quickstart/",
        }
        if target in urls:
            webbrowser.open(urls[target], new=2)
            return True
        return False

    def template_headers(self) -> dict:
        with self._lock:
            request = self._request
            datasets = self._preview_datasets
        if not request.template or not os.path.isfile(request.template):
            raise RuntimeError("Save/open an SVG template first")
        effect = self._preview_effect(request)
        if not datasets:
            try:
                datasets = DATASET.load_datasets(effect, effect.document_path(), validate=False)
            except Exception as error:
                LOG.w("[deckmaker.web] dataset unavailable while copying template headers; using SVG bbox fallback: %s", error)
                datasets = []
        return TEMPLATE_HEADERS.template_headers(effect.document.getroot(), datasets)

    def command(self, name: str) -> bool:
        commands = {
            "generate": self.generate,
            "preview": self.preview,
            "preview-initial": lambda: self.preview(force=False, refresh=True),
            "preview-refresh": self.refresh_previews,
            "preview-previous": lambda: self.preview(force=False, refresh=False, movement=-1),
            "preview-next": lambda: self.preview(force=False, refresh=False, movement=1),
            "open-output": self.open_output,
            "export": self.export,
            "play-create": self.create_play_assets,
            "play-server": self.toggle_play_server,
            "play-designer": lambda: self.open_play("designer"),
            "play-lobby": lambda: self.open_play("lobby"),
            "play-game": lambda: self.open_play("game"),
            "open-preferences": lambda: self.open_auxiliary("preferences"),
            "open-log": lambda: self.open_auxiliary("log"),
            "open-examples": lambda: self.open_auxiliary("examples"),
            "open-intro": lambda: self.open_auxiliary("intro"),
            "open-guide": lambda: self.open_auxiliary("guide"),
        }
        action = commands.get(str(name or "").strip().lower())
        if not action:
            raise ValueError(f"Unknown command: {name}")
        return bool(action())

    def close(self) -> None:
        LOG.remove_listener(self._on_log_line)
        PROGRESS.clear_listener()
        self._preview_monitor_stop.set()
        self._preview_monitor_wake.set()
        with self._lock:
            self._closed = True
            self._preview_prefetch_generation += 1
            preview_prefetch_service = self._preview_prefetch_service
            self._preview_prefetch_service = None
        if preview_prefetch_service is not None:
            preview_prefetch_service.close()
        self._text_query_service.close()
        self._play_server.close()
        try:
            import gsheets_client_pkce as GS
            GS.set_authorization_listener(None)
            GS.close_session()
        except Exception:
            pass
