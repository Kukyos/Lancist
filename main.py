"""
Oryn Outreach — Lead Manager (desktop UI).

  - Paste a website URL → scraper auto-fills business name / email / phone /
    location / description, then scores the site /100 across 6 dimensions
    and produces category-aware recommendations.
  - "Find Leads" sweeps the open web (India-biased) for SMB sites worth
    pitching, deduped against your existing leads.
  - One click generates a personalised cold email — backed by either
    Anthropic Claude or any local OpenAI-compatible server (Oddyssey,
    Ollama, LM Studio, vLLM…) chosen in Settings.
  - "Make demo" mirrors the site to a local folder and drops an ORYN.md
    checklist so you can hand-improve it before sending.
  - Send the email via Gmail SMTP, with attachments.
  - Status tracking, CSV export, monochrome desktop UI ready to ship as .exe.
"""
from __future__ import annotations

import os
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog
from typing import Optional

import customtkinter as ctk

import theme
from scraper import scrape, ScrapeResult
from analyzer import analyze, Analysis
from store import load_leads, upsert_lead, delete_lead
from email_writer import generate_email
from csv_log import export_all
from config import load as load_config, save as save_config, get_api_key, llm_configured
from paths import APP_DIR
from cloner import Cloner, CloneResult, slugify
from demo_writer import write_oryn_md
from lead_finder import (
    Finder, FindResult, FoundLead,
    CATEGORY_LABELS, CATEGORY_TAGS,
    geolocate_ip, geocode_place, circle_polygon,
)
import smtp_sender
import llm
import tkintermapview


theme.setup()

DEMOS_DIR = APP_DIR / "demos"
ICON_ICO = APP_DIR / "icon.ico"
ICON_PNG = APP_DIR / "icon.png"

# PyInstaller --add-data unpacks to sys._MEIPASS at runtime — check there too.
if hasattr(sys, "_MEIPASS"):
    _BUNDLED = Path(sys._MEIPASS)
    if not ICON_ICO.exists() and (_BUNDLED / "icon.ico").exists():
        ICON_ICO = _BUNDLED / "icon.ico"
    if not ICON_PNG.exists() and (_BUNDLED / "icon.png").exists():
        ICON_PNG = _BUNDLED / "icon.png"


STATUSES = ["new", "analyzed", "drafted", "sent", "replied", "closed"]
STATUS_LABEL = {
    "new":      "NEW",
    "analyzed": "ANALYZED",
    "drafted":  "DRAFTED",
    "sent":     "SENT",
    "replied":  "REPLIED",
    "closed":   "CLOSED",
}


# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Settings")
        self.geometry("580x780")
        self.configure(fg_color=theme.BG)
        self.transient(master)
        self.grab_set()
        _try_set_window_icon(self)

        cfg = load_config()

        outer = ctk.CTkScrollableFrame(self, fg_color=theme.BG,
                                       scrollbar_button_color=theme.BORDER)
        outer.pack(fill="both", expand=True)

        ctk.CTkLabel(outer, text="Settings",
                     font=theme.font_h1(), text_color=theme.TEXT
                     ).pack(anchor="w", padx=24, pady=(20, 14))

        # ---- LLM Provider ----
        ctk.CTkLabel(outer, text="LLM PROVIDER",
                     font=theme.font_micro(), text_color=theme.TEXT_MUTED, anchor="w"
                     ).pack(anchor="w", padx=24, pady=(0, 6))

        self.provider_var = ctk.StringVar(
            value=cfg.get("llm_provider", "anthropic"))
        ctk.CTkLabel(
            outer,
            text="Choose between Anthropic's hosted Claude or any local / self-hosted "
                 "OpenAI-compatible server (Oddyssey, Ollama, LM Studio, vLLM…).",
            font=theme.font_small(), text_color=theme.TEXT_MUTED, anchor="w",
            wraplength=500, justify="left",
        ).pack(anchor="w", padx=24, pady=(0, 4))
        ctk.CTkOptionMenu(
            outer, values=["anthropic", "openai_compatible"],
            variable=self.provider_var, fg_color=theme.SURFACE,
            button_color=theme.SURFACE_HI, button_hover_color=theme.BORDER_HI,
            text_color=theme.TEXT, dropdown_fg_color=theme.SURFACE,
            dropdown_text_color=theme.TEXT, dropdown_hover_color=theme.SURFACE_HI,
            command=self._on_provider_change,
        ).pack(fill="x", padx=24, pady=(2, 14))

        # ---- Anthropic block ----
        self.anthropic_block = ctk.CTkFrame(outer, fg_color="transparent")
        self.anthropic_block.pack(fill="x")
        ctk.CTkLabel(self.anthropic_block, text="ANTHROPIC",
                     font=theme.font_micro(), text_color=theme.TEXT_MUTED, anchor="w"
                     ).pack(anchor="w", padx=24, pady=(0, 6))

        ctk.CTkLabel(self.anthropic_block, text="Anthropic API key",
                     font=theme.font_body(), text_color=theme.TEXT,
                     anchor="w").pack(anchor="w", padx=24)
        self.api_entry = _entry(self.anthropic_block, show="•")
        self.api_entry.pack(fill="x", padx=24, pady=(2, 10))
        self.api_entry.insert(0, cfg.get("anthropic_api_key", ""))

        ctk.CTkLabel(self.anthropic_block, text="Model", font=theme.font_body(),
                     text_color=theme.TEXT, anchor="w"
                     ).pack(anchor="w", padx=24, pady=(2, 2))
        self.model_var = ctk.StringVar(value=cfg.get("anthropic_model", "claude-sonnet-4-6"))
        ctk.CTkOptionMenu(
            self.anthropic_block,
            values=["claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5-20251001"],
            variable=self.model_var, fg_color=theme.SURFACE,
            button_color=theme.SURFACE_HI, button_hover_color=theme.BORDER_HI,
            text_color=theme.TEXT, dropdown_fg_color=theme.SURFACE,
            dropdown_text_color=theme.TEXT, dropdown_hover_color=theme.SURFACE_HI,
        ).pack(fill="x", padx=24, pady=(0, 14))

        # ---- OpenAI-compatible block ----
        self.openai_block = ctk.CTkFrame(outer, fg_color="transparent")
        self.openai_block.pack(fill="x")
        ctk.CTkLabel(self.openai_block, text="OPENAI-COMPATIBLE SERVER",
                     font=theme.font_micro(), text_color=theme.TEXT_MUTED, anchor="w"
                     ).pack(anchor="w", padx=24, pady=(0, 6))

        ctk.CTkLabel(
            self.openai_block,
            text="Examples — Ollama: http://localhost:11434  ·  LM Studio: http://localhost:1234  ·  "
                 "vLLM/Oddyssey: whatever URL its server exposes.",
            font=theme.font_small(), text_color=theme.TEXT_MUTED, anchor="w",
            wraplength=500, justify="left",
        ).pack(anchor="w", padx=24, pady=(0, 4))

        ctk.CTkLabel(self.openai_block, text="Base URL",
                     font=theme.font_body(), text_color=theme.TEXT, anchor="w"
                     ).pack(anchor="w", padx=24)
        self.llm_url_entry = _entry(self.openai_block,
                                    placeholder="http://localhost:11434")
        self.llm_url_entry.pack(fill="x", padx=24, pady=(2, 10))
        self.llm_url_entry.insert(0, cfg.get("llm_base_url", ""))

        ctk.CTkLabel(self.openai_block, text="Model name (as the server exposes it)",
                     font=theme.font_body(), text_color=theme.TEXT, anchor="w"
                     ).pack(anchor="w", padx=24)
        self.llm_model_entry = _entry(self.openai_block,
                                      placeholder="e.g. llama3.1:8b  or  oddyssey-xl")
        self.llm_model_entry.pack(fill="x", padx=24, pady=(2, 10))
        self.llm_model_entry.insert(0, cfg.get("llm_model", ""))

        ctk.CTkLabel(self.openai_block,
                     text="API key (optional — many local servers don't need one)",
                     font=theme.font_body(), text_color=theme.TEXT, anchor="w"
                     ).pack(anchor="w", padx=24)
        self.llm_key_entry = _entry(self.openai_block, show="•")
        self.llm_key_entry.pack(fill="x", padx=24, pady=(2, 14))
        self.llm_key_entry.insert(0, cfg.get("llm_api_key", ""))

        self._on_provider_change(self.provider_var.get())

        # ---- Sender signoff (shared) ----
        ctk.CTkLabel(outer, text="EMAIL SIGNOFF",
                     font=theme.font_micro(), text_color=theme.TEXT_MUTED, anchor="w"
                     ).pack(anchor="w", padx=24, pady=(0, 6))
        ctk.CTkLabel(outer, text="Sender signoff (used inside the email)",
                     font=theme.font_body(), text_color=theme.TEXT, anchor="w"
                     ).pack(anchor="w", padx=24)
        self.sender_entry = _entry(outer)
        self.sender_entry.pack(fill="x", padx=24, pady=(2, 14))
        self.sender_entry.insert(0, cfg.get("from_name", "Aether, Oryn"))

        # ---- SMTP ----
        ctk.CTkLabel(outer, text="EMAIL SENDING (Gmail SMTP)",
                     font=theme.font_micro(), text_color=theme.TEXT_MUTED, anchor="w"
                     ).pack(anchor="w", padx=24, pady=(8, 6))

        ctk.CTkLabel(outer, text="From address",
                     font=theme.font_body(), text_color=theme.TEXT, anchor="w"
                     ).pack(anchor="w", padx=24)
        self.from_entry = _entry(outer, placeholder="aether@oryn.in")
        self.from_entry.pack(fill="x", padx=24, pady=(2, 10))
        self.from_entry.insert(0, cfg.get("smtp_from_email", ""))

        ctk.CTkLabel(outer, text="Gmail app password (16 chars, no spaces)",
                     font=theme.font_body(), text_color=theme.TEXT, anchor="w"
                     ).pack(anchor="w", padx=24)
        ctk.CTkLabel(
            outer, text="Create at: myaccount.google.com/apppasswords (2FA required)",
            font=theme.font_small(), text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", padx=24, pady=(0, 2))
        self.app_pw_entry = _entry(outer, show="•")
        self.app_pw_entry.pack(fill="x", padx=24, pady=(2, 14))
        self.app_pw_entry.insert(0, cfg.get("smtp_app_password", ""))

        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.pack(fill="x", padx=24, pady=(8, 20))
        ctk.CTkButton(btn_row, text="Cancel", width=110, height=34,
                      fg_color="transparent", border_width=1,
                      border_color=theme.BORDER, text_color=theme.TEXT_DIM,
                      hover_color=theme.SURFACE_HI,
                      command=self.destroy).pack(side="right", padx=(8, 0))
        ctk.CTkButton(btn_row, text="Save", width=110, height=34,
                      fg_color=theme.ACCENT, text_color="#000000",
                      hover_color=theme.ACCENT_DIM,
                      command=self._save).pack(side="right")

    def _on_provider_change(self, value: str):
        # show/hide the corresponding block
        if value == "anthropic":
            self.anthropic_block.pack(fill="x")
            try:
                self.openai_block.pack_forget()
            except Exception:
                pass
            self.openai_block.pack(fill="x")  # keep it visible too so user can pre-fill
        else:
            self.anthropic_block.pack(fill="x")
            self.openai_block.pack(fill="x")

    def _save(self):
        save_config({
            "llm_provider":       self.provider_var.get().strip() or "anthropic",
            "anthropic_api_key":  self.api_entry.get().strip(),
            "anthropic_model":    self.model_var.get(),
            "llm_base_url":       self.llm_url_entry.get().strip(),
            "llm_model":          self.llm_model_entry.get().strip(),
            "llm_api_key":        self.llm_key_entry.get().strip(),
            "from_name":          self.sender_entry.get().strip(),
            "smtp_from_email":    self.from_entry.get().strip(),
            "smtp_app_password":  self.app_pw_entry.get().strip(),
        })
        self.destroy()


def _entry(parent, *, show=None, placeholder=""):
    return ctk.CTkEntry(
        parent, height=34, show=show or "",
        placeholder_text=placeholder, fg_color=theme.SURFACE,
        border_color=theme.BORDER, text_color=theme.TEXT,
    )


# ---------------------------------------------------------------------------
# Find Leads dialog
# ---------------------------------------------------------------------------

class FindLeadsDialog(ctk.CTkToplevel):
    """
    Map-based lead finder. Drop a pin on the map, pick a business category,
    set a radius, and we query OpenStreetMap via Overpass for every matching
    place in that circle. Places with websites become importable leads.

    No API keys — uses Nominatim (geocoding), ipapi.co (IP location), and
    Overpass (POI search).
    """

    DEFAULT_LAT  = 19.0760     # Mumbai
    DEFAULT_LNG  = 72.8777
    DEFAULT_ZOOM = 12
    MIN_RADIUS_M = 500
    MAX_RADIUS_M = 25_000

    def __init__(self, master):
        super().__init__(master)
        self.master_app = master
        self.title("Find Leads")
        self.geometry("1140x740")
        self.minsize(960, 640)
        self.configure(fg_color=theme.BG)
        self.transient(master)
        _try_set_window_icon(self)

        cfg = load_config()

        self.results: list[FoundLead] = []
        self.checkbox_vars: list[ctk.BooleanVar] = []
        self._markers: list = []
        self._radius_polygon = None
        self._centre_marker = None
        self._finder: Optional[Finder] = None
        self._last_ip_info: Optional[dict] = None

        self.centre_lat = float(cfg.get("finder_last_lat") or self.DEFAULT_LAT)
        self.centre_lng = float(cfg.get("finder_last_lng") or self.DEFAULT_LNG)
        self.zoom_level = int(cfg.get("finder_last_zoom") or self.DEFAULT_ZOOM)
        self.radius_m   = int(cfg.get("finder_last_radius_m") or 3000)
        self.last_category_key = cfg.get("finder_last_category") or "cafe"

        # ---- layout: row 0 header, row 1 main, row 2 status ----
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Find Leads",
                     font=theme.font_h1(), text_color=theme.TEXT, anchor="w"
                     ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            head,
            text="Drop a pin, choose a category, search OpenStreetMap. Only places "
                 "with a website are importable — those are the ones we can pitch.",
            font=theme.font_small(), text_color=theme.TEXT_MUTED, anchor="w",
            wraplength=900, justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        # ---- main split: map on left, controls + results on right ----
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(4, 4))
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2, minsize=380)
        body.grid_rowconfigure(0, weight=1)

        # --- map column ---
        map_wrap = ctk.CTkFrame(body, fg_color=theme.SURFACE, corner_radius=8,
                                border_width=1, border_color=theme.BORDER)
        map_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        map_wrap.grid_rowconfigure(0, weight=1)
        map_wrap.grid_columnconfigure(0, weight=1)

        self.map_widget = tkintermapview.TkinterMapView(
            map_wrap, width=400, height=400, corner_radius=8,
        )
        self.map_widget.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        self.map_widget.set_position(self.centre_lat, self.centre_lng)
        self.map_widget.set_zoom(self.zoom_level)
        self.map_widget.add_left_click_map_command(self._on_map_click)

        map_hint = ctk.CTkLabel(
            map_wrap,
            text="Left-click anywhere to recentre the search.",
            text_color=theme.TEXT_MUTED, font=theme.font_small(),
        )
        map_hint.grid(row=1, column=0, sticky="ew", padx=10, pady=(2, 6))

        # --- right column: controls (top) + results (bottom) ---
        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        controls = ctk.CTkFrame(right, fg_color=theme.SURFACE, corner_radius=8,
                                border_width=1, border_color=theme.BORDER)
        controls.grid(row=0, column=0, sticky="ew")
        controls.grid_columnconfigure(0, weight=1)

        # location row
        ctk.CTkLabel(controls, text="LOCATION", font=theme.font_micro(),
                     text_color=theme.TEXT_MUTED, anchor="w"
                     ).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 2))

        loc_row = ctk.CTkFrame(controls, fg_color="transparent")
        loc_row.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        loc_row.grid_columnconfigure(0, weight=1)
        self.location_label = ctk.CTkLabel(
            loc_row,
            text=f"Centre: {self.centre_lat:.4f}, {self.centre_lng:.4f}",
            text_color=theme.TEXT, font=theme.font_body(), anchor="w",
        )
        self.location_label.grid(row=0, column=0, sticky="ew")
        self.use_ip_btn = ctk.CTkButton(
            loc_row, text="Use my IP", width=92, height=28,
            fg_color="transparent", border_width=1, border_color=theme.BORDER,
            text_color=theme.TEXT_DIM, hover_color=theme.SURFACE_HI,
            command=self._on_use_ip,
        )
        self.use_ip_btn.grid(row=0, column=1, padx=(6, 0))

        # geocoder row
        geo_row = ctk.CTkFrame(controls, fg_color="transparent")
        geo_row.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))
        geo_row.grid_columnconfigure(0, weight=1)
        self.geocode_entry = ctk.CTkEntry(
            geo_row, height=32, placeholder_text="Jump to: city, neighbourhood, address…",
            fg_color=theme.BG, border_color=theme.BORDER, text_color=theme.TEXT,
        )
        self.geocode_entry.grid(row=0, column=0, sticky="ew")
        self.geocode_entry.bind("<Return>", lambda _e: self._on_geocode())
        self.geocode_btn = ctk.CTkButton(
            geo_row, text="Go", width=58, height=32,
            fg_color="transparent", border_width=1, border_color=theme.BORDER_HI,
            text_color=theme.TEXT, hover_color=theme.SURFACE_HI,
            command=self._on_geocode,
        )
        self.geocode_btn.grid(row=0, column=1, padx=(6, 0))

        # category
        ctk.CTkLabel(controls, text="CATEGORY", font=theme.font_micro(),
                     text_color=theme.TEXT_MUTED, anchor="w"
                     ).grid(row=3, column=0, sticky="w", padx=14, pady=(4, 2))
        # label list keeps a stable order (insertion order of CATEGORY_LABELS)
        label_to_key = {v: k for k, v in CATEGORY_LABELS.items()}
        self._label_to_key = label_to_key
        labels = list(CATEGORY_LABELS.values())
        default_label = CATEGORY_LABELS.get(self.last_category_key, labels[0])
        self.category_var = ctk.StringVar(value=default_label)
        ctk.CTkOptionMenu(
            controls, values=labels, variable=self.category_var,
            fg_color=theme.BG, button_color=theme.SURFACE_HI,
            button_hover_color=theme.BORDER_HI, text_color=theme.TEXT,
            dropdown_fg_color=theme.SURFACE, dropdown_text_color=theme.TEXT,
            dropdown_hover_color=theme.SURFACE_HI,
        ).grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 10))

        # radius
        ctk.CTkLabel(controls, text="RADIUS", font=theme.font_micro(),
                     text_color=theme.TEXT_MUTED, anchor="w"
                     ).grid(row=5, column=0, sticky="w", padx=14, pady=(0, 2))
        rad_row = ctk.CTkFrame(controls, fg_color="transparent")
        rad_row.grid(row=6, column=0, sticky="ew", padx=14, pady=(0, 12))
        rad_row.grid_columnconfigure(0, weight=1)
        self.radius_slider = ctk.CTkSlider(
            rad_row,
            from_=self.MIN_RADIUS_M, to=self.MAX_RADIUS_M,
            number_of_steps=(self.MAX_RADIUS_M - self.MIN_RADIUS_M) // 500,
            command=self._on_radius_change,
            fg_color=theme.BORDER, progress_color=theme.ACCENT,
            button_color=theme.ACCENT, button_hover_color=theme.ACCENT_DIM,
        )
        self.radius_slider.set(self.radius_m)
        self.radius_slider.grid(row=0, column=0, sticky="ew")
        self.radius_label = ctk.CTkLabel(
            rad_row, text=self._radius_text(),
            text_color=theme.TEXT, font=theme.font_body(), width=72,
        )
        self.radius_label.grid(row=0, column=1, padx=(8, 0))

        # search button + status
        self.find_status = ctk.CTkLabel(
            controls, text="", text_color=theme.TEXT_DIM,
            font=theme.font_small(), anchor="w", wraplength=380, justify="left",
        )
        self.find_status.grid(row=7, column=0, sticky="ew", padx=14, pady=(0, 6))

        self.search_btn = ctk.CTkButton(
            controls, text="Search this area", height=36,
            fg_color=theme.ACCENT, text_color="#000000",
            hover_color=theme.ACCENT_DIM, command=self._on_search,
        )
        self.search_btn.grid(row=8, column=0, sticky="ew", padx=14, pady=(0, 12))

        self.find_progress = ctk.CTkProgressBar(
            controls, height=4, progress_color=theme.ACCENT,
            fg_color=theme.BORDER, mode="indeterminate",
        )
        self.find_progress.grid(row=9, column=0, sticky="ew", padx=14, pady=(0, 10))
        self.find_progress.set(0)
        self.find_progress.grid_remove()

        # results pane
        results_wrap = ctk.CTkFrame(right, fg_color=theme.SURFACE, corner_radius=8,
                                    border_width=1, border_color=theme.BORDER)
        results_wrap.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        results_wrap.grid_columnconfigure(0, weight=1)
        results_wrap.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(results_wrap, text="RESULTS", font=theme.font_micro(),
                     text_color=theme.TEXT_MUTED, anchor="w"
                     ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 4))
        self.results_frame = ctk.CTkScrollableFrame(
            results_wrap, fg_color="transparent",
            scrollbar_button_color=theme.BORDER,
            scrollbar_button_hover_color=theme.BORDER_HI,
        )
        self.results_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        ctk.CTkLabel(self.results_frame,
                     text="(run a search to see candidates)",
                     text_color=theme.TEXT_MUTED, font=theme.font_small()
                     ).pack(anchor="w", padx=8, pady=12)

        act = ctk.CTkFrame(results_wrap, fg_color="transparent")
        act.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))
        act.grid_columnconfigure(0, weight=1)
        self.import_status = ctk.CTkLabel(
            act, text="", text_color=theme.TEXT_DIM,
            font=theme.font_small(), anchor="w", wraplength=200, justify="left",
        )
        self.import_status.grid(row=0, column=0, sticky="w")
        self.select_all_btn = ctk.CTkButton(
            act, text="Select all", width=92, height=30,
            fg_color="transparent", border_width=1, border_color=theme.BORDER,
            text_color=theme.TEXT_DIM, hover_color=theme.SURFACE_HI,
            command=self._toggle_all,
        )
        self.select_all_btn.grid(row=0, column=1, padx=(6, 6))
        self.select_all_btn.configure(state="disabled")
        self.import_btn = ctk.CTkButton(
            act, text="Import selected", width=140, height=30,
            fg_color=theme.ACCENT, text_color="#000000",
            hover_color=theme.ACCENT_DIM, command=self._on_import,
        )
        self.import_btn.grid(row=0, column=2)
        self.import_btn.configure(state="disabled")

        # ---- footer status ----
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 14))
        foot.grid_columnconfigure(0, weight=1)
        self.footer_label = ctk.CTkLabel(
            foot, text="Tiles: OpenStreetMap. Geocoding: Nominatim. POI: Overpass.",
            text_color=theme.TEXT_MUTED, font=theme.font_small(), anchor="w",
        )
        self.footer_label.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            foot, text="Close", width=80, height=28,
            fg_color="transparent", border_width=1, border_color=theme.BORDER,
            text_color=theme.TEXT_DIM, hover_color=theme.SURFACE_HI,
            command=self._on_close,
        ).grid(row=0, column=1, sticky="e")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._redraw_overlays)
        # auto-fetch IP location only if there's no stored centre yet
        if cfg.get("finder_last_lat") is None:
            self.after(200, self._on_use_ip)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _radius_text(self) -> str:
        if self.radius_m >= 1000:
            return f"{self.radius_m/1000:.1f} km"
        return f"{self.radius_m} m"

    def _set_status(self, msg: str, color: str = None):
        self.find_status.configure(
            text=msg, text_color=color or theme.TEXT_DIM)

    def _category_key(self) -> str:
        return self._label_to_key.get(self.category_var.get(), "cafe")

    # ------------------------------------------------------------------
    # map overlays
    # ------------------------------------------------------------------

    def _redraw_overlays(self):
        """Clear and redraw the centre pin + radius circle."""
        try:
            if self._centre_marker:
                self._centre_marker.delete()
        except Exception:
            pass
        self._centre_marker = self.map_widget.set_marker(
            self.centre_lat, self.centre_lng,
            text="search centre",
            marker_color_circle=theme.ACCENT,
            marker_color_outside=theme.ACCENT_DIM,
        )

        try:
            if self._radius_polygon:
                self._radius_polygon.delete()
        except Exception:
            pass
        pts = circle_polygon(self.centre_lat, self.centre_lng, self.radius_m)
        self._radius_polygon = self.map_widget.set_polygon(
            pts,
            outline_color=theme.ACCENT,
            fill_color=None,
            border_width=2,
        )

    def _on_map_click(self, coords):
        try:
            self.centre_lat, self.centre_lng = float(coords[0]), float(coords[1])
        except Exception:
            return
        self.location_label.configure(
            text=f"Centre: {self.centre_lat:.4f}, {self.centre_lng:.4f}")
        self._redraw_overlays()

    def _on_radius_change(self, value):
        # snap to 500m steps
        self.radius_m = max(self.MIN_RADIUS_M,
                            min(self.MAX_RADIUS_M, int(round(value / 500) * 500)))
        self.radius_label.configure(text=self._radius_text())
        self._redraw_overlays()

    # ------------------------------------------------------------------
    # location helpers
    # ------------------------------------------------------------------

    def _on_use_ip(self):
        self.use_ip_btn.configure(state="disabled", text="…")
        def worker():
            info = geolocate_ip()
            self.after(0, self._after_ip, info)
        threading.Thread(target=worker, daemon=True).start()

    def _after_ip(self, info: Optional[dict]):
        self.use_ip_btn.configure(state="normal", text="Use my IP")
        if not info:
            self._set_status("IP geolocation failed (offline?).", theme.SEV_HIGH_BG)
            return
        self._last_ip_info = info
        self.centre_lat = info["lat"]
        self.centre_lng = info["lng"]
        self.zoom_level = 12
        self.map_widget.set_position(self.centre_lat, self.centre_lng)
        self.map_widget.set_zoom(self.zoom_level)
        city = info.get("city") or "(unknown city)"
        country = info.get("country") or ""
        self.location_label.configure(
            text=f"{city}, {country} — {self.centre_lat:.4f}, {self.centre_lng:.4f}")
        self._set_status(f"Centred on {city}.", theme.TEXT)
        self._redraw_overlays()

    def _on_geocode(self):
        q = self.geocode_entry.get().strip()
        if not q:
            return
        self.geocode_btn.configure(state="disabled", text="…")
        self._set_status(f"Looking up '{q}'…", theme.TEXT_DIM)
        cc = load_config().get("finder_country", "in") or ""
        def worker():
            res = geocode_place(q, country_code=cc)
            self.after(0, self._after_geocode, q, res)
        threading.Thread(target=worker, daemon=True).start()

    def _after_geocode(self, query: str, res: Optional[dict]):
        self.geocode_btn.configure(state="normal", text="Go")
        if not res:
            self._set_status(f"No match for '{query}'.", theme.SEV_HIGH_BG)
            return
        self.centre_lat = res["lat"]
        self.centre_lng = res["lng"]
        self.zoom_level = 13
        self.map_widget.set_position(self.centre_lat, self.centre_lng)
        self.map_widget.set_zoom(self.zoom_level)
        self.location_label.configure(text=res["display_name"][:80])
        self._set_status("Centred. Hit Search to find businesses here.", theme.TEXT)
        self._redraw_overlays()

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------

    def _on_search(self):
        category_key = self._category_key()
        existing = {
            (l.get("website") or "").lower().rstrip("/")
            for l in load_leads()
        }
        existing.discard("")

        # clear UI
        for w in self.results_frame.winfo_children(): w.destroy()
        self.results = []
        self.checkbox_vars = []
        self._clear_result_markers()
        self.import_btn.configure(state="disabled")
        self.select_all_btn.configure(state="disabled")
        self.search_btn.configure(state="disabled", text="Searching…")
        self.find_progress.grid()
        self.find_progress.start()

        def on_progress(msg: str):
            self.after(0, self._set_status, msg, theme.TEXT_DIM)

        finder = Finder(
            lat=self.centre_lat, lng=self.centre_lng,
            radius_m=self.radius_m, category_key=category_key,
            require_website=True,
            existing_websites=existing,
            progress=on_progress,
        )
        self._finder = finder

        def on_done(result: FindResult):
            self.after(0, self._after_search, result)

        finder.start(on_done=on_done)

    def _after_search(self, result: FindResult):
        self.find_progress.stop()
        self.find_progress.grid_remove()
        self.search_btn.configure(state="normal", text="Search this area")

        for w in self.results_frame.winfo_children(): w.destroy()
        self.results = result.leads
        self.checkbox_vars = []
        self._clear_result_markers()

        if not self.results:
            err = result.errors[0] if result.errors else "no matches"
            extra = (f"OSM had {result.raw_count} place(s) but none had a website tag."
                     if result.raw_count and not result.errors
                     else f"({err})")
            self._set_status(f"No usable leads. {extra}", theme.SEV_HIGH_BG)
            ctk.CTkLabel(
                self.results_frame,
                text="Try a bigger radius, a different category, or recentre the map.",
                text_color=theme.TEXT_MUTED, font=theme.font_small(),
                wraplength=320, justify="left",
            ).pack(anchor="w", padx=8, pady=12)
            return

        self._set_status(
            f"Found {len(self.results)} usable lead(s) "
            f"(OSM saw {result.raw_count} place(s) in the area).",
            theme.TEXT)

        for i, lead in enumerate(self.results):
            var = ctk.BooleanVar(value=True)
            self.checkbox_vars.append(var)
            self._render_result_card(i, lead, var)
            if lead.lat is not None and lead.lng is not None:
                m = self.map_widget.set_marker(
                    lead.lat, lead.lng,
                    text=lead.business_name[:40],
                    marker_color_circle=theme.TEXT,
                    marker_color_outside=theme.ACCENT,
                )
                self._markers.append(m)

        self.import_btn.configure(state="normal")
        self.select_all_btn.configure(state="normal", text="Deselect all")

    def _clear_result_markers(self):
        for m in self._markers:
            try: m.delete()
            except Exception: pass
        self._markers = []

    def _render_result_card(self, idx: int, lead: FoundLead, var: ctk.BooleanVar):
        card = ctk.CTkFrame(
            self.results_frame, fg_color=theme.BG, corner_radius=6,
            border_width=1, border_color=theme.BORDER,
        )
        card.pack(fill="x", padx=4, pady=3)
        card.grid_columnconfigure(1, weight=1)

        cb = ctk.CTkCheckBox(
            card, text="", variable=var, width=22,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_DIM,
            border_color=theme.BORDER_HI, checkmark_color="#000000",
        )
        cb.grid(row=0, column=0, rowspan=3, sticky="nw", padx=(8, 4), pady=8)

        ctk.CTkLabel(
            card, text=lead.business_name, anchor="w",
            font=theme.font_h2(), text_color=theme.TEXT,
            wraplength=260, justify="left",
        ).grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(8, 0))
        ctk.CTkLabel(
            card, text=lead.website, anchor="w",
            text_color=theme.TEXT_DIM, font=theme.font_small(),
            wraplength=260, justify="left",
        ).grid(row=1, column=1, sticky="ew", padx=(0, 8))
        if lead.address:
            ctk.CTkLabel(
                card, text=lead.address, anchor="w",
                text_color=theme.TEXT_MUTED, font=theme.font_small(),
                wraplength=260, justify="left",
            ).grid(row=2, column=1, sticky="ew", padx=(0, 8), pady=(2, 6))

        # clicking the card jumps the map to that lead
        if lead.lat is not None and lead.lng is not None:
            def goto(_e=None, lat=lead.lat, lng=lead.lng):
                self.map_widget.set_position(lat, lng)
                self.map_widget.set_zoom(max(14, self.map_widget.zoom))
            card.bind("<Button-1>", goto)
            for child in card.winfo_children():
                if isinstance(child, ctk.CTkLabel):
                    child.bind("<Button-1>", goto)

    def _toggle_all(self):
        if not self.checkbox_vars: return
        any_off = any(not v.get() for v in self.checkbox_vars)
        new = any_off
        for v in self.checkbox_vars:
            v.set(new)
        self.select_all_btn.configure(text="Deselect all" if new else "Select all")

    def _on_import(self):
        if not self.results: return
        chosen = [lead for lead, var in zip(self.results, self.checkbox_vars)
                  if var.get()]
        if not chosen:
            self.import_status.configure(
                text="Tick at least one candidate.",
                text_color=theme.SEV_HIGH_BG)
            return
        n = 0
        for c in chosen:
            upsert_lead(c.to_dict())
            n += 1
        self.import_status.configure(
            text=f"Imported {n} lead(s).", text_color=theme.TEXT)
        try:
            self.master_app.refresh_list()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # cleanup
    # ------------------------------------------------------------------

    def _on_close(self):
        try:
            save_config({
                "finder_last_lat":       self.centre_lat,
                "finder_last_lng":       self.centre_lng,
                "finder_last_zoom":      int(self.map_widget.zoom),
                "finder_last_radius_m":  int(self.radius_m),
                "finder_last_category":  self._category_key(),
            })
        except Exception:
            pass
        self.destroy()


# ---------------------------------------------------------------------------
# Severity pill
# ---------------------------------------------------------------------------

def _severity_pill(parent, severity: str):
    sev = (severity or "low").lower()
    if sev == "high":
        return ctk.CTkLabel(
            parent, text="  HIGH  ", text_color=theme.SEV_HIGH_FG,
            fg_color=theme.SEV_HIGH_BG, corner_radius=4,
            font=theme.font_micro(),
        )
    if sev == "medium":
        f = ctk.CTkFrame(parent, fg_color="transparent",
                         border_width=1, border_color=theme.SEV_MED_BORDER,
                         corner_radius=4)
        ctk.CTkLabel(f, text="  MED  ", text_color=theme.SEV_MED_FG,
                     font=theme.font_micro()).pack(padx=0, pady=0)
        return f
    return ctk.CTkLabel(parent, text="LOW", text_color=theme.SEV_LOW_FG,
                        font=theme.font_micro())


# ---------------------------------------------------------------------------
# Icon helper
# ---------------------------------------------------------------------------

def _try_set_window_icon(window):
    """Best-effort icon assignment that won't crash if files are missing."""
    try:
        if ICON_ICO.exists() and sys.platform.startswith("win"):
            window.iconbitmap(str(ICON_ICO))
            return
    except Exception:
        pass
    try:
        if ICON_PNG.exists():
            import tkinter as tk
            img = tk.PhotoImage(file=str(ICON_PNG))
            window.iconphoto(True, img)
            window._icon_ref = img   # keep reference so GC doesn't kill it
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class OrynApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Oryn Outreach")
        self.geometry("1320x860")
        self.minsize(1040, 680)
        self.configure(fg_color=theme.BG)
        _try_set_window_icon(self)

        DEMOS_DIR.mkdir(parents=True, exist_ok=True)

        self.grid_columnconfigure(0, weight=0, minsize=320)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.selected_website: Optional[str] = None
        self.current_analysis: Optional[dict] = None
        self.current_demo: Optional[dict] = None
        self.attachments: list[str] = []
        self._active_cloner: Optional[Cloner] = None

        self._build_topbar()
        self._build_sidebar()
        self._build_main()
        self.refresh_list()

    # ---------------- Top bar ----------------

    def _build_topbar(self):
        top = ctk.CTkFrame(self, fg_color=theme.BG, height=58, corner_radius=0)
        top.grid(row=0, column=0, columnspan=2, sticky="ew")
        top.grid_columnconfigure(1, weight=1)
        top.grid_propagate(False)

        logo_row = ctk.CTkFrame(top, fg_color="transparent")
        logo_row.grid(row=0, column=0, sticky="w", padx=24, pady=14)
        ctk.CTkLabel(logo_row, text="ORYN", font=theme.font_logo(),
                     text_color=theme.TEXT).pack(side="left")
        ctk.CTkLabel(logo_row, text="OUTREACH",
                     font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                     text_color=theme.TEXT_DIM
                     ).pack(side="left", padx=(8, 0), pady=(4, 0))

        # progress bar in the centre — shown only during long ops
        self.global_progress = ctk.CTkProgressBar(
            top, mode="indeterminate", height=2,
            progress_color=theme.ACCENT, fg_color=theme.BG,
        )
        self.global_progress.grid(row=0, column=1, sticky="ew", padx=20, pady=26)
        self.global_progress.set(0)

        right = ctk.CTkFrame(top, fg_color="transparent")
        right.grid(row=0, column=2, sticky="e", padx=18, pady=12)
        for label, fn in (("Export CSV", self._export_csv),
                          ("Settings", lambda: SettingsDialog(self)),
                          ("Find Leads", lambda: FindLeadsDialog(self))):
            ctk.CTkButton(
                right, text=label, width=110, height=32,
                fg_color="transparent", border_width=1, border_color=theme.BORDER,
                text_color=theme.TEXT_DIM, hover_color=theme.SURFACE,
                command=fn,
            ).pack(side="right", padx=(8, 0))

        ctk.CTkFrame(self, fg_color=theme.BORDER, height=1, corner_radius=0
                     ).grid(row=0, column=0, columnspan=2, sticky="ews", pady=(57, 0))

    # ---------------- Sidebar ----------------

    def _build_sidebar(self):
        side = ctk.CTkFrame(self, fg_color=theme.BG, corner_radius=0, width=320)
        side.grid(row=1, column=0, sticky="nsew")
        side.grid_propagate(False)
        side.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(side, text="LEADS",
                     font=theme.font_micro(), text_color=theme.TEXT_MUTED, anchor="w"
                     ).grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 4))

        bar = ctk.CTkFrame(side, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 8))
        bar.grid_columnconfigure(0, weight=1)
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_list())
        ctk.CTkEntry(
            bar, placeholder_text="Search…", textvariable=self.search_var,
            height=32, fg_color=theme.SURFACE, border_color=theme.BORDER,
            text_color=theme.TEXT,
        ).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            bar, text="+", width=32, height=32,
            fg_color=theme.ACCENT, text_color="#000000",
            hover_color=theme.ACCENT_DIM, command=self.clear_form,
        ).grid(row=0, column=1, padx=(6, 0))

        self.list_frame = ctk.CTkScrollableFrame(
            side, fg_color="transparent",
            scrollbar_button_color=theme.BORDER,
            scrollbar_button_hover_color=theme.BORDER_HI,
        )
        self.list_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=4)

        ctk.CTkFrame(self, fg_color=theme.BORDER, width=1, corner_radius=0
                     ).grid(row=1, column=0, sticky="nse")

    # ---------------- Main area ----------------

    def _build_main(self):
        wrap = ctk.CTkScrollableFrame(self, fg_color=theme.BG,
                                      scrollbar_button_color=theme.BORDER,
                                      scrollbar_button_hover_color=theme.BORDER_HI)
        wrap.grid(row=1, column=1, sticky="nsew", padx=(28, 22), pady=(20, 16))
        wrap.grid_columnconfigure(0, weight=1)
        self.wrap = wrap

        # ----- Header: title + status pill -----
        head = ctk.CTkFrame(wrap, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        head.grid_columnconfigure(0, weight=1)

        self.title_label = ctk.CTkLabel(head, text="Add new lead",
                                        font=theme.font_title(),
                                        text_color=theme.TEXT, anchor="w")
        self.title_label.grid(row=0, column=0, sticky="ew")

        self.status_pill = ctk.CTkLabel(
            head, text="  NEW  ", text_color="#000000",
            fg_color=theme.ACCENT, corner_radius=4, font=theme.font_micro(),
        )
        self.status_pill.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.status_pill.grid_remove()

        # ----- URL row -----
        url_card = ctk.CTkFrame(wrap, fg_color=theme.SURFACE, corner_radius=8,
                                border_width=1, border_color=theme.BORDER)
        url_card.grid(row=1, column=0, sticky="ew")
        url_card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(url_card, text="WEBSITE",
                     font=theme.font_micro(), text_color=theme.TEXT_MUTED
                     ).grid(row=0, column=0, columnspan=2, sticky="w",
                            padx=18, pady=(14, 4))
        self.url_entry = ctk.CTkEntry(
            url_card, height=44, font=ctk.CTkFont(family="Segoe UI", size=15),
            fg_color=theme.BG, border_color=theme.BORDER, text_color=theme.TEXT,
            placeholder_text="paste a website URL — we'll do the rest",
        )
        self.url_entry.grid(row=1, column=0, sticky="ew", padx=(18, 6), pady=(0, 14))
        self.url_entry.bind("<Return>", lambda _e: self.on_analyze())
        self.analyze_btn = ctk.CTkButton(
            url_card, text="Analyze", width=140, height=44,
            fg_color=theme.ACCENT, text_color="#000000",
            hover_color=theme.ACCENT_DIM, font=theme.font_h2(),
            command=self.on_analyze,
        )
        self.analyze_btn.grid(row=1, column=1, sticky="e", padx=(0, 18), pady=(0, 14))

        # ----- Detail fields -----
        details = ctk.CTkFrame(wrap, fg_color=theme.SURFACE, corner_radius=8,
                               border_width=1, border_color=theme.BORDER)
        details.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        details.grid_columnconfigure(1, weight=1)
        details.grid_columnconfigure(3, weight=1)
        self.fields: dict[str, ctk.CTkBaseClass] = {}

        def lbl(text, row, col):
            ctk.CTkLabel(details, text=text, font=theme.font_micro(),
                         text_color=theme.TEXT_MUTED, anchor="w"
                         ).grid(row=row, column=col, sticky="w",
                                padx=(18, 8), pady=(12, 2))

        def entry(row, col, key, placeholder=""):
            e = ctk.CTkEntry(details, height=36, placeholder_text=placeholder,
                             fg_color=theme.BG, border_color=theme.BORDER,
                             text_color=theme.TEXT)
            e.grid(row=row, column=col, sticky="ew", padx=(18, 8), pady=(0, 8))
            self.fields[key] = e

        lbl("BUSINESS NAME", 0, 0); entry(1, 0, "business_name", "auto from site")
        lbl("EMAIL",         0, 2); entry(1, 2, "email",         "auto from site")
        lbl("PHONE",         2, 0); entry(3, 0, "phone",         "auto from site")
        lbl("LOCATION",      2, 2); entry(3, 2, "location",      "auto from site")

        lbl("NOTES / DESCRIPTION", 4, 0)
        self.desc_box = ctk.CTkTextbox(
            details, height=80, fg_color=theme.BG, border_width=1,
            border_color=theme.BORDER, text_color=theme.TEXT,
        )
        self.desc_box.grid(row=5, column=0, columnspan=4, sticky="ew",
                           padx=18, pady=(0, 8))
        self.fields["description"] = self.desc_box

        self.extra_label = ctk.CTkLabel(
            details, text="", anchor="w", text_color=theme.TEXT_MUTED,
            font=theme.font_small(), justify="left", wraplength=860,
        )
        self.extra_label.grid(row=6, column=0, columnspan=4, sticky="ew",
                              padx=18, pady=(0, 12))

        # ----- Action / status bar -----
        bar = ctk.CTkFrame(wrap, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", pady=(14, 8))
        bar.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(bar, text="", anchor="w",
                                         text_color=theme.TEXT_DIM,
                                         font=theme.font_small())
        self.status_label.grid(row=0, column=0, sticky="ew")

        self.status_var = ctk.StringVar(value="new")
        self.status_menu = ctk.CTkOptionMenu(
            bar, values=STATUSES, variable=self.status_var, width=120, height=32,
            fg_color=theme.SURFACE, button_color=theme.SURFACE_HI,
            button_hover_color=theme.BORDER_HI, text_color=theme.TEXT,
            dropdown_fg_color=theme.SURFACE, dropdown_text_color=theme.TEXT,
            dropdown_hover_color=theme.SURFACE_HI, command=self._on_status_change,
        )
        self.status_menu.grid(row=0, column=1, sticky="e", padx=(6, 6))
        self.status_menu.grid_remove()

        self.delete_btn = ctk.CTkButton(
            bar, text="Delete", width=96, height=32, fg_color="transparent",
            border_width=1, border_color=theme.BORDER,
            text_color=theme.TEXT_DIM, hover_color=theme.SURFACE,
            command=self.on_delete,
        )
        self.delete_btn.grid(row=0, column=2, sticky="e", padx=(0, 6))
        self.delete_btn.grid_remove()

        self.save_btn = ctk.CTkButton(
            bar, text="Save", width=110, height=32,
            fg_color="transparent", border_width=1, border_color=theme.BORDER_HI,
            text_color=theme.TEXT, hover_color=theme.SURFACE_HI,
            command=self.on_save_only,
        )
        self.save_btn.grid(row=0, column=3, sticky="e")
        self.save_btn.grid_remove()

        # ----- Tab view: Analysis | Email | Demo -----
        self.tabs = ctk.CTkTabview(
            wrap, fg_color=theme.SURFACE, corner_radius=8,
            segmented_button_fg_color=theme.SURFACE_HI,
            segmented_button_selected_color=theme.ACCENT,
            segmented_button_selected_hover_color=theme.ACCENT_DIM,
            segmented_button_unselected_color=theme.SURFACE,
            segmented_button_unselected_hover_color=theme.SURFACE_HI,
            text_color="#000000",
            border_width=1, border_color=theme.BORDER,
        )
        self.tabs.grid(row=4, column=0, sticky="ew", pady=(18, 18))
        self.tab_analysis = self.tabs.add("Analysis")
        self.tab_email    = self.tabs.add("Email")
        self.tab_demo     = self.tabs.add("Demo")
        for t in (self.tab_analysis, self.tab_email, self.tab_demo):
            t.grid_columnconfigure(0, weight=1)

        self._build_analysis_tab(self.tab_analysis)
        self._build_email_tab(self.tab_email)
        self._build_demo_tab(self.tab_demo)

    # ---------------- Analysis tab ----------------

    def _build_analysis_tab(self, parent):
        ov = ctk.CTkFrame(parent, fg_color="transparent")
        ov.grid(row=0, column=0, sticky="ew", padx=12, pady=(14, 8))
        ov.grid_columnconfigure(0, weight=1)

        cat_box = ctk.CTkFrame(ov, fg_color="transparent")
        cat_box.grid(row=0, column=0, sticky="nsew")
        self.category_badge = ctk.CTkLabel(
            cat_box, text="—  Category will appear after analysis",
            text_color=theme.TEXT_DIM, font=theme.font_h2(), anchor="w",
        )
        self.category_badge.pack(anchor="w")
        self.category_rationale = ctk.CTkLabel(
            cat_box, text="", text_color=theme.TEXT_MUTED,
            font=theme.font_small(), anchor="w", justify="left", wraplength=760,
        )
        self.category_rationale.pack(anchor="w", pady=(4, 0))
        self.meta_label = ctk.CTkLabel(
            cat_box, text="", text_color=theme.TEXT_MUTED,
            font=theme.font_small(), anchor="w", justify="left", wraplength=760,
        )
        self.meta_label.pack(anchor="w", pady=(8, 0))

        score_box = ctk.CTkFrame(ov, fg_color="transparent")
        score_box.grid(row=0, column=1, sticky="ne", padx=(8, 6))
        self.score_label = ctk.CTkLabel(score_box, text="—",
                                        font=theme.font_score(),
                                        text_color=theme.TEXT_MUTED)
        self.score_label.pack(anchor="e")
        self.grade_label = ctk.CTkLabel(score_box, text="not analyzed",
                                        text_color=theme.TEXT_MUTED,
                                        font=theme.font_small())
        self.grade_label.pack(anchor="e", pady=(0, 2))

        ctk.CTkFrame(parent, fg_color=theme.BORDER, height=1, corner_radius=0
                     ).grid(row=1, column=0, sticky="ew", padx=12, pady=(8, 0))

        ctk.CTkLabel(parent, text="SCORE BREAKDOWN",
                     font=theme.font_micro(), text_color=theme.TEXT_MUTED, anchor="w"
                     ).grid(row=2, column=0, sticky="ew", padx=18, pady=(14, 6))
        self.dims_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.dims_frame.grid(row=3, column=0, sticky="ew", padx=12)
        self._render_dimensions_empty()

        ctk.CTkLabel(parent, text="WHAT ORYN CAN IMPROVE",
                     font=theme.font_micro(), text_color=theme.TEXT_MUTED, anchor="w"
                     ).grid(row=4, column=0, sticky="ew", padx=18, pady=(20, 6))
        self.recos_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.recos_frame.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 16))
        ctk.CTkLabel(self.recos_frame,
                     text="(analyze a site to see recommendations)",
                     text_color=theme.TEXT_MUTED, font=theme.font_small()
                     ).pack(anchor="w", padx=8, pady=12)

    def _render_dimensions_empty(self):
        for w in self.dims_frame.winfo_children(): w.destroy()
        ctk.CTkLabel(self.dims_frame, text="(no analysis yet)",
                     text_color=theme.TEXT_MUTED, font=theme.font_small()
                     ).pack(anchor="w", padx=8, pady=12)

    def _render_dimensions(self, dims: list[dict]):
        for w in self.dims_frame.winfo_children(): w.destroy()
        for d in dims:
            row = ctk.CTkFrame(self.dims_frame, fg_color="transparent")
            row.pack(fill="x", padx=6, pady=8)
            row.grid_columnconfigure(0, weight=1)

            head = ctk.CTkFrame(row, fg_color="transparent")
            head.grid(row=0, column=0, sticky="ew")
            head.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(head, text=d["label"], anchor="w",
                         font=theme.font_h2(), text_color=theme.TEXT
                         ).grid(row=0, column=0, sticky="w")
            ratio = d["score"] / max(d["max"], 1)
            colour = theme.score_color(int(ratio * 100))
            ctk.CTkLabel(head, text=f"{d['score']} / {d['max']}",
                         text_color=colour, font=theme.font_h2()
                         ).grid(row=0, column=1, sticky="e", padx=(8, 0))

            bar = ctk.CTkProgressBar(row, height=6, progress_color=colour,
                                     fg_color=theme.BORDER)
            bar.grid(row=1, column=0, sticky="ew", pady=(4, 6))
            bar.set(ratio)

            for item in d["items"]:
                gained, possible = item["gained"], item["possible"]
                if item["met"]:
                    mark, fg = "✓", theme.TEXT
                elif gained > 0:
                    mark, fg = "◐", theme.TEXT_DIM
                else:
                    mark, fg = "✗", theme.TEXT_MUTED
                ctk.CTkLabel(
                    row,
                    text=f"  {mark}  {item['label']}  ({gained}/{possible})",
                    text_color=fg, anchor="w", font=theme.font_small(),
                ).grid(sticky="w")

    def _render_recommendations(self, recos: list[dict]):
        for w in self.recos_frame.winfo_children(): w.destroy()
        if not recos:
            ctk.CTkLabel(self.recos_frame,
                         text="No obvious gaps — this site is already in great shape.",
                         text_color=theme.TEXT, font=theme.font_body()
                         ).pack(anchor="w", padx=8, pady=12)
            return
        for r in recos:
            card = ctk.CTkFrame(self.recos_frame, fg_color=theme.BG,
                                corner_radius=6,
                                border_width=1, border_color=theme.BORDER)
            card.pack(fill="x", padx=4, pady=4)

            head = ctk.CTkFrame(card, fg_color="transparent")
            head.pack(fill="x", padx=14, pady=(10, 0))
            head.grid_columnconfigure(1, weight=1)
            _severity_pill(head, r["severity"]).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(head, text=r["title"], anchor="w",
                         font=theme.font_h2(), text_color=theme.TEXT,
                         wraplength=720, justify="left"
                         ).grid(row=0, column=1, sticky="ew", padx=(10, 6))
            ctk.CTkLabel(head, text=r["bucket"], anchor="e",
                         text_color=theme.TEXT_MUTED, font=theme.font_micro()
                         ).grid(row=0, column=2, sticky="e")
            ctk.CTkLabel(card, text=r["rationale"], anchor="w",
                         text_color=theme.TEXT_DIM, font=theme.font_small(),
                         wraplength=820, justify="left"
                         ).pack(fill="x", padx=14, pady=(4, 10))

    # ---------------- Email tab ----------------

    def _build_email_tab(self, parent):
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(14, 8))
        head.grid_columnconfigure(0, weight=1)

        self.email_meta_label = ctk.CTkLabel(
            head, text="No draft yet.",
            text_color=theme.TEXT_DIM, font=theme.font_body(), anchor="w",
        )
        self.email_meta_label.grid(row=0, column=0, sticky="w")

        self.copy_btn = ctk.CTkButton(
            head, text="Copy", width=78, height=30, fg_color="transparent",
            border_width=1, border_color=theme.BORDER,
            text_color=theme.TEXT_DIM, hover_color=theme.SURFACE_HI,
            command=self._copy_email,
        )
        self.copy_btn.grid(row=0, column=1, padx=(6, 0))
        self.copy_btn.configure(state="disabled")

        self.regen_btn = ctk.CTkButton(
            head, text="Regenerate", width=120, height=30, fg_color="transparent",
            border_width=1, border_color=theme.BORDER_HI,
            text_color=theme.TEXT, hover_color=theme.SURFACE_HI,
            command=self._on_generate_email,
        )
        self.regen_btn.grid(row=0, column=2, padx=(6, 0))
        self.regen_btn.grid_remove()

        self.generate_btn = ctk.CTkButton(
            head, text="Generate with Claude", height=30, width=190,
            fg_color=theme.ACCENT, text_color="#000000",
            hover_color=theme.ACCENT_DIM,
            command=self._on_generate_email,
        )
        self.generate_btn.grid(row=0, column=3, padx=(6, 0))

        # To / From
        addr_row = ctk.CTkFrame(parent, fg_color="transparent")
        addr_row.grid(row=1, column=0, sticky="ew", padx=12, pady=(10, 0))
        addr_row.grid_columnconfigure(1, weight=1)
        addr_row.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(addr_row, text="TO", width=40,
                     font=theme.font_micro(), text_color=theme.TEXT_MUTED, anchor="w"
                     ).grid(row=0, column=0, sticky="w", padx=(6, 4))
        self.to_entry = ctk.CTkEntry(
            addr_row, height=32, fg_color=theme.BG,
            border_color=theme.BORDER, text_color=theme.TEXT,
        )
        self.to_entry.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        ctk.CTkLabel(addr_row, text="FROM", width=44,
                     font=theme.font_micro(), text_color=theme.TEXT_MUTED, anchor="w"
                     ).grid(row=0, column=2, sticky="w")
        self.from_label = ctk.CTkLabel(addr_row, text=load_config().get("smtp_from_email") or "(set in Settings)",
                                       text_color=theme.TEXT_DIM,
                                       font=theme.font_small(), anchor="w")
        self.from_label.grid(row=0, column=3, sticky="w", padx=(4, 6))

        # Subject
        ctk.CTkLabel(parent, text="SUBJECT", font=theme.font_micro(),
                     text_color=theme.TEXT_MUTED, anchor="w"
                     ).grid(row=2, column=0, sticky="ew", padx=18, pady=(12, 4))
        self.subject_entry = ctk.CTkEntry(
            parent, height=36, fg_color=theme.BG,
            border_color=theme.BORDER, text_color=theme.TEXT,
        )
        self.subject_entry.grid(row=3, column=0, sticky="ew", padx=12)

        # Body
        ctk.CTkLabel(parent, text="BODY", font=theme.font_micro(),
                     text_color=theme.TEXT_MUTED, anchor="w"
                     ).grid(row=4, column=0, sticky="ew", padx=18, pady=(10, 4))
        self.body_box = ctk.CTkTextbox(
            parent, height=260, fg_color=theme.BG, border_width=1,
            border_color=theme.BORDER, text_color=theme.TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=13),
        )
        self.body_box.grid(row=5, column=0, sticky="ew", padx=12)

        self.pitched_label = ctk.CTkLabel(
            parent, text="", text_color=theme.TEXT_MUTED,
            font=theme.font_small(), anchor="w", wraplength=820,
        )
        self.pitched_label.grid(row=6, column=0, sticky="ew", padx=18, pady=(8, 6))

        # Attachments strip + send bar
        att_row = ctk.CTkFrame(parent, fg_color="transparent")
        att_row.grid(row=7, column=0, sticky="ew", padx=12, pady=(0, 6))
        att_row.grid_columnconfigure(0, weight=1)
        self.attach_chips = ctk.CTkFrame(att_row, fg_color="transparent")
        self.attach_chips.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            att_row, text="+ Attach", width=92, height=30, fg_color="transparent",
            border_width=1, border_color=theme.BORDER,
            text_color=theme.TEXT_DIM, hover_color=theme.SURFACE_HI,
            command=self._on_attach,
        ).grid(row=0, column=1, padx=(6, 0))

        send_row = ctk.CTkFrame(parent, fg_color="transparent")
        send_row.grid(row=8, column=0, sticky="ew", padx=12, pady=(8, 14))
        send_row.grid_columnconfigure(0, weight=1)
        self.send_status = ctk.CTkLabel(
            send_row, text="", text_color=theme.TEXT_DIM,
            font=theme.font_small(), anchor="w",
        )
        self.send_status.grid(row=0, column=0, sticky="ew")
        self.send_btn = ctk.CTkButton(
            send_row, text="Send via Gmail", height=34, width=160,
            fg_color=theme.ACCENT, text_color="#000000",
            hover_color=theme.ACCENT_DIM, command=self._on_send_email,
        )
        self.send_btn.grid(row=0, column=1, sticky="e")
        self.send_btn.configure(state="disabled")

    # ---------------- Demo tab ----------------

    def _build_demo_tab(self, parent):
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(14, 8))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            head, text="Make a local copy of the site you can improve and send back.",
            text_color=theme.TEXT_DIM, font=theme.font_body(), anchor="w",
            wraplength=720, justify="left",
        ).grid(row=0, column=0, sticky="w")

        self.make_demo_btn = ctk.CTkButton(
            head, text="Make demo", height=34, width=150,
            fg_color=theme.ACCENT, text_color="#000000",
            hover_color=theme.ACCENT_DIM, command=self._on_make_demo,
        )
        self.make_demo_btn.grid(row=0, column=1, padx=(6, 0))

        self.demo_progress = ctk.CTkProgressBar(
            parent, height=4, progress_color=theme.ACCENT,
            fg_color=theme.BORDER, mode="indeterminate",
        )
        self.demo_progress.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 6))
        self.demo_progress.set(0)
        self.demo_progress.grid_remove()

        info = ctk.CTkFrame(parent, fg_color="transparent")
        info.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 14))
        info.grid_columnconfigure(0, weight=1)

        self.demo_status_label = ctk.CTkLabel(
            info, text="No demo yet for this lead.",
            text_color=theme.TEXT_DIM, font=theme.font_body(), anchor="w",
        )
        self.demo_status_label.grid(row=0, column=0, sticky="w")

        self.demo_folder_label = ctk.CTkLabel(
            info, text="", text_color=theme.TEXT_MUTED,
            font=theme.font_small(), anchor="w", wraplength=820, justify="left",
        )
        self.demo_folder_label.grid(row=1, column=0, sticky="w", pady=(4, 8))

        actions = ctk.CTkFrame(info, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="w")
        self.open_index_btn = ctk.CTkButton(
            actions, text="Open index.html", width=150, height=30,
            fg_color="transparent", border_width=1, border_color=theme.BORDER_HI,
            text_color=theme.TEXT, hover_color=theme.SURFACE_HI,
            command=self._open_demo_index,
        )
        self.open_index_btn.pack(side="left", padx=(0, 6))
        self.open_folder_btn = ctk.CTkButton(
            actions, text="Open folder", width=120, height=30,
            fg_color="transparent", border_width=1, border_color=theme.BORDER,
            text_color=theme.TEXT_DIM, hover_color=theme.SURFACE_HI,
            command=self._open_demo_folder,
        )
        self.open_folder_btn.pack(side="left", padx=(0, 6))
        self.open_oryn_md_btn = ctk.CTkButton(
            actions, text="Open ORYN.md", width=130, height=30,
            fg_color="transparent", border_width=1, border_color=theme.BORDER,
            text_color=theme.TEXT_DIM, hover_color=theme.SURFACE_HI,
            command=self._open_oryn_md,
        )
        self.open_oryn_md_btn.pack(side="left")
        self._set_demo_actions_enabled(False)

    def _set_demo_actions_enabled(self, enabled: bool):
        s = "normal" if enabled else "disabled"
        for b in (self.open_index_btn, self.open_folder_btn, self.open_oryn_md_btn):
            b.configure(state=s)

    # ---------------- Form actions ----------------

    def clear_form(self):
        self.selected_website = None
        self.current_analysis = None
        self.current_demo = None
        self.attachments = []
        self.url_entry.delete(0, "end")
        for k, w in self.fields.items():
            if isinstance(w, ctk.CTkTextbox):
                w.delete("1.0", "end")
            else:
                w.delete(0, "end")
        self.title_label.configure(text="Add new lead")
        self.status_pill.grid_remove()
        self.delete_btn.grid_remove()
        self.save_btn.grid_remove()
        self.status_menu.grid_remove()
        self._reset_overview()
        self._render_dimensions_empty()
        for w in self.recos_frame.winfo_children(): w.destroy()
        ctk.CTkLabel(self.recos_frame,
                     text="(analyze a site to see recommendations)",
                     text_color=theme.TEXT_MUTED, font=theme.font_small()
                     ).pack(anchor="w", padx=8, pady=12)
        self.extra_label.configure(text="")
        self._reset_email_tab()
        self._reset_demo_tab()
        self._set_status("", theme.TEXT_DIM)
        self.tabs.set("Analysis")

    def _reset_overview(self):
        self.category_badge.configure(
            text="—  Category will appear after analysis",
            text_color=theme.TEXT_DIM)
        self.category_rationale.configure(text="")
        self.meta_label.configure(text="")
        self.score_label.configure(text="—", text_color=theme.TEXT_MUTED)
        self.grade_label.configure(text="not analyzed", text_color=theme.TEXT_MUTED)

    def _reset_email_tab(self):
        self.subject_entry.delete(0, "end")
        self.body_box.delete("1.0", "end")
        self.to_entry.delete(0, "end")
        self.pitched_label.configure(text="")
        self.email_meta_label.configure(text="No draft yet.",
                                        text_color=theme.TEXT_DIM)
        self.regen_btn.grid_remove()
        self.generate_btn.grid()
        self.copy_btn.configure(state="disabled")
        self.send_btn.configure(state="disabled")
        self.send_status.configure(text="")
        self._render_attachment_chips()
        self.from_label.configure(
            text=load_config().get("smtp_from_email") or "(set in Settings)")

    def _reset_demo_tab(self):
        self.demo_status_label.configure(
            text="No demo yet for this lead.", text_color=theme.TEXT_DIM)
        self.demo_folder_label.configure(text="")
        self.make_demo_btn.configure(text="Make demo", state="normal")
        self.demo_progress.grid_remove()
        self._set_demo_actions_enabled(False)

    def load_lead_into_form(self, lead: dict):
        self.clear_form()
        self.selected_website = lead.get("website")
        self.url_entry.insert(0, lead.get("website", "") or "")
        for k in ("business_name", "email", "phone", "location"):
            v = lead.get(k, "") or ""
            w = self.fields.get(k)
            if w is not None:
                w.delete(0, "end"); w.insert(0, v)
        self.desc_box.delete("1.0", "end")
        self.desc_box.insert("1.0", lead.get("description", "") or "")

        self.title_label.configure(text=lead.get("business_name") or "Lead")
        self.save_btn.grid()
        self.delete_btn.grid()
        self.status_menu.grid()
        status = lead.get("status") or "new"
        self.status_var.set(status)
        self._render_status_pill(status)

        scrape_data = lead.get("scrape") or {}
        analysis = lead.get("analysis") or {}
        if scrape_data:
            self._render_meta(scrape_data)
            self._render_extras_from_scrape(scrape_data)
        if analysis:
            self.current_analysis = analysis
            self._render_analysis_dict(analysis)

        # email
        if lead.get("email_subject") or lead.get("email_body"):
            self.subject_entry.insert(0, lead.get("email_subject", ""))
            self.body_box.insert("1.0", lead.get("email_body", ""))
            self.to_entry.insert(0, lead.get("email", ""))
            self.email_meta_label.configure(
                text=f"Draft generated {lead.get('email_generated_at', '')}",
                text_color=theme.TEXT_DIM)
            self.generate_btn.grid_remove()
            self.regen_btn.grid()
            self.copy_btn.configure(state="normal")
            self.send_btn.configure(state="normal")
            services = lead.get("email_services_pitched") or []
            if services:
                self.pitched_label.configure(
                    text="Pitched: " + ", ".join(s.replace("_", " ") for s in services))
        else:
            self.to_entry.insert(0, lead.get("email", ""))

        # demo
        demo = lead.get("demo")
        if demo and demo.get("folder") and Path(demo["folder"]).exists():
            self.current_demo = demo
            self._render_demo_state(demo)

        n_high = sum(1 for r in (analysis.get("recommendations") or [])
                     if r.get("severity") == "high")
        self._set_status(f"Loaded — {n_high} high-priority pitch(es).", theme.TEXT)

    # ---------------- Analyze ----------------

    def on_analyze(self):
        url = self.url_entry.get().strip()
        if not url:
            self._set_status("Paste a website URL first.", theme.SEV_HIGH_BG)
            return
        self._lock(True)
        self._start_progress()
        self._set_status(f"Scraping {url}… this can take 15–30s.",
                        theme.TEXT_DIM)
        self._reset_overview()
        self.extra_label.configure(text="")
        for w in self.dims_frame.winfo_children(): w.destroy()
        ctk.CTkLabel(self.dims_frame, text="… probing the site …",
                     text_color=theme.TEXT_DIM,
                     font=theme.font_small()).pack(anchor="w", padx=8, pady=12)
        for w in self.recos_frame.winfo_children(): w.destroy()

        threading.Thread(target=self._analyze_thread, args=(url,), daemon=True).start()

    def _analyze_thread(self, url: str):
        result = scrape(url)
        analysis = None
        if result.status == "ok":
            analysis = analyze(result, user_description=self._current_description())
        self.after(0, self._apply_analyze, url, result, analysis)

    def _current_description(self) -> str:
        return self.desc_box.get("1.0", "end").strip()

    def _apply_analyze(self, url: str, result: ScrapeResult,
                       analysis: Optional[Analysis]):
        self._lock(False)
        self._stop_progress()
        if result.status != "ok" or analysis is None:
            self._set_status(f"Couldn't scrape: {result.error}",
                             theme.SEV_HIGH_BG)
            for w in self.dims_frame.winfo_children(): w.destroy()
            ctk.CTkLabel(self.dims_frame, text="(scrape failed)",
                         text_color=theme.SEV_HIGH_BG,
                         font=theme.font_small()).pack(anchor="w", padx=8, pady=12)
            return

        scrape_dict = result.to_dict()
        analysis_dict = analysis.to_dict()
        self.current_analysis = analysis_dict

        self._autofill_from_scrape(scrape_dict, url)
        self._render_extras_from_scrape(scrape_dict)

        data = self._collect_form()
        data["website"] = data["website"] or url
        if not data["business_name"]:
            data["business_name"] = scrape_dict.get("extracted_business_name") or url

        lead = {
            **data,
            "scrape":  scrape_dict,
            "analysis": analysis_dict,
            "category":       analysis.category.primary,
            "category_label": analysis.category.primary_label,
            "score":          analysis.scorecard.total,
            "grade":          analysis.scorecard.grade,
            "high_priority_count": sum(
                1 for r in analysis.recommendations if r.severity == "high"
            ),
        }
        existing = self._find_existing(lead["website"])
        if existing and existing.get("status") not in ("new", "analyzed", None):
            lead["status"] = existing.get("status")
        else:
            lead["status"] = "analyzed"
        # preserve existing demo + email if re-analyzing
        if existing:
            for k in ("demo", "email_subject", "email_body", "email_generated_at",
                      "email_services_pitched", "email_model", "email_sent_at"):
                if existing.get(k) and k not in lead:
                    lead[k] = existing[k]
        saved = upsert_lead(lead)
        self.selected_website = saved.get("website")

        self.title_label.configure(text=data["business_name"])
        self.save_btn.grid()
        self.delete_btn.grid()
        self.status_menu.grid()
        self.status_var.set(saved.get("status", "analyzed"))
        self._render_status_pill(saved.get("status", "analyzed"))

        # populate To: field from extracted email
        if not self.to_entry.get().strip() and data.get("email"):
            self.to_entry.insert(0, data["email"])

        self._render_meta(scrape_dict)
        self._render_analysis_dict(analysis_dict)
        # reflect saved demo if any
        if saved.get("demo"):
            self.current_demo = saved["demo"]
            self._render_demo_state(saved["demo"])

        n_high = lead["high_priority_count"]
        self._set_status(
            f"Saved {data['business_name']} · score {analysis.scorecard.total}/100 "
            f"({analysis.scorecard.grade}) · {n_high} high-priority gap(s).",
            theme.score_color(analysis.scorecard.total),
        )
        self.refresh_list()

    def _autofill_from_scrape(self, s: dict, url: str):
        def fill_if_empty(key, value):
            if not value: return
            w = self.fields.get(key)
            if w is None: return
            cur = (w.get() if isinstance(w, ctk.CTkEntry) else "").strip()
            if cur: return
            w.delete(0, "end"); w.insert(0, value)

        fill_if_empty("business_name", s.get("extracted_business_name"))
        if s.get("extracted_emails"):    fill_if_empty("email",    s["extracted_emails"][0])
        if s.get("extracted_phones"):    fill_if_empty("phone",    s["extracted_phones"][0])
        if s.get("extracted_addresses"): fill_if_empty("location", s["extracted_addresses"][0])
        if not self.desc_box.get("1.0", "end").strip():
            desc = s.get("og_description") or s.get("description") or ""
            if desc:
                self.desc_box.insert("1.0", desc)
        if not self.url_entry.get().strip():
            self.url_entry.insert(0, url)

    def _render_extras_from_scrape(self, s: dict):
        bits = []
        for key, label in (("extracted_emails",    "Other emails"),
                           ("extracted_phones",    "Other phones"),
                           ("extracted_addresses", "Other addresses")):
            vals = s.get(key) or []
            if len(vals) > 1:
                bits.append(f"{label}: " + ", ".join(str(v)[:80] for v in vals[1:4]))
        self.extra_label.configure(text="\n".join(bits))

    def _render_meta(self, scrape_dict: dict):
        bits = []
        if scrape_dict.get("title"):
            bits.append(scrape_dict["title"][:90])
        if scrape_dict.get("generator"):
            bits.append(f"generator: {scrape_dict['generator']}")
        if scrape_dict.get("detected_frameworks"):
            bits.append("stack: " + ", ".join(scrape_dict["detected_frameworks"]))
        pages = scrape_dict.get("pages_checked") or []
        if pages:
            bits.append(f"{len(pages)} page(s) checked")
        self.meta_label.configure(text="  ·  ".join(bits))

    def _render_analysis_dict(self, analysis: dict):
        cat = analysis.get("category") or {}
        sc = analysis.get("scorecard") or {}
        recos = analysis.get("recommendations") or []

        label = cat.get("primary_label") or "?"
        conf = cat.get("confidence") or 0
        sec = cat.get("secondary_label")
        badge = f"{label}     confidence {int(conf*100)}%"
        if sec and conf < 0.8:
            badge += f"     ·  maybe also: {sec}"
        self.category_badge.configure(text=badge, text_color=theme.TEXT)
        rationale = cat.get("rationale") or ""
        self.category_rationale.configure(
            text=("Why: " + rationale) if rationale else "")

        total = sc.get("total", 0); grade = sc.get("grade", "?")
        self.score_label.configure(text=str(total),
                                   text_color=theme.score_color(total))
        self.grade_label.configure(text=f"grade {grade}   ·   /100",
                                   text_color=theme.grade_color(grade))
        self._render_dimensions(sc.get("dimensions") or [])
        self._render_recommendations(recos)

    # ---------------- Save / delete / status ----------------

    def _collect_form(self) -> dict:
        return {
            "business_name": self.fields["business_name"].get().strip(),
            "website":       self.url_entry.get().strip(),
            "email":         self.fields["email"].get().strip(),
            "phone":         self.fields["phone"].get().strip(),
            "location":      self.fields["location"].get().strip(),
            "description":   self.desc_box.get("1.0", "end").strip(),
        }

    def _find_existing(self, website: str) -> Optional[dict]:
        if not website: return None
        key = website.strip().lower().rstrip("/")
        for l in load_leads():
            if (l.get("website") or "").strip().lower().rstrip("/") == key:
                return l
        return None

    def on_save_only(self):
        data = self._collect_form()
        if not data["website"]:
            self._set_status("Need a website URL.", theme.SEV_HIGH_BG); return
        existing = self._find_existing(data["website"])
        merged = {**(existing or {}), **data}
        merged.setdefault("status", "new")
        upsert_lead(merged)
        self.selected_website = data["website"]
        self.title_label.configure(text=data["business_name"] or "Lead")
        self._set_status("Saved.", theme.TEXT)
        self.refresh_list()

    def on_delete(self):
        if not self.selected_website: return
        delete_lead(self.selected_website)
        self.refresh_list()
        self.clear_form()
        self._set_status("Lead deleted.", theme.TEXT_DIM)

    def _on_status_change(self, new_status: str):
        if not self.selected_website: return
        existing = self._find_existing(self.selected_website)
        if not existing: return
        update = {"website": existing["website"], "status": new_status}
        if new_status == "sent" and not existing.get("email_sent_at"):
            update["email_sent_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        upsert_lead(update)
        self._render_status_pill(new_status)
        self.refresh_list()

    def _render_status_pill(self, status: str):
        label = STATUS_LABEL.get(status, status.upper())
        bg = theme.ACCENT if status in ("sent", "replied") else theme.SURFACE_HI
        fg = "#000000" if status in ("sent", "replied") else theme.TEXT
        self.status_pill.configure(text=f"  {label}  ", fg_color=bg, text_color=fg)
        self.status_pill.grid()

    # ---------------- Email generation ----------------

    def _on_generate_email(self):
        if not self.selected_website:
            self._set_status("Analyze a website first.", theme.SEV_HIGH_BG); return
        if not llm_configured():
            self._set_status("LLM not configured — open Settings.",
                             theme.SEV_HIGH_BG); SettingsDialog(self); return
        existing = self._find_existing(self.selected_website)
        if not existing or not existing.get("analysis"):
            self._set_status("Run analysis before generating email.",
                             theme.SEV_HIGH_BG); return

        # demo URL: if user has put one in the location/notes? for now, none
        demo_url = (existing.get("demo") or {}).get("hosted_url") or ""

        self.generate_btn.configure(state="disabled", text="Generating…")
        self.regen_btn.configure(state="disabled", text="Generating…")
        self.email_meta_label.configure(
            text="Asking Claude to write the email…",
            text_color=theme.TEXT_DIM,
        )
        self._start_progress()
        self.tabs.set("Email")
        threading.Thread(target=self._email_thread,
                         args=(existing, demo_url), daemon=True).start()

    def _email_thread(self, lead: dict, demo_url: str):
        try:
            result = generate_email(lead, demo_url=demo_url or None)
            self.after(0, self._apply_email, lead, result, None)
        except Exception as e:
            self.after(0, self._apply_email, lead, None, str(e))

    def _apply_email(self, lead: dict, result: Optional[dict], err: Optional[str]):
        self._stop_progress()
        self.generate_btn.configure(state="normal", text="Generate with Claude")
        self.regen_btn.configure(state="normal", text="Regenerate")

        if err:
            self.email_meta_label.configure(text=f"Failed: {err}",
                                            text_color=theme.SEV_HIGH_BG)
            self._set_status(f"Email generation failed: {err}",
                             theme.SEV_HIGH_BG)
            return

        self.subject_entry.delete(0, "end")
        self.subject_entry.insert(0, result["subject"])
        self.body_box.delete("1.0", "end")
        self.body_box.insert("1.0", result["body"])
        if not self.to_entry.get().strip() and lead.get("email"):
            self.to_entry.insert(0, lead["email"])

        services = result.get("services_pitched") or []
        if services:
            self.pitched_label.configure(
                text="Pitched: " + ", ".join(s.replace("_", " ") for s in services),
                text_color=theme.TEXT_DIM)
        else:
            self.pitched_label.configure(text="")

        usage = ""
        if result.get("input_tokens") and result.get("output_tokens"):
            usage = f"  ·  {result['input_tokens']}→{result['output_tokens']} tok"
        self.email_meta_label.configure(
            text=f"Drafted with {result['model']}{usage}",
            text_color=theme.TEXT_DIM,
        )
        self.regen_btn.grid(); self.generate_btn.grid_remove()
        self.copy_btn.configure(state="normal")
        self.send_btn.configure(state="normal")

        update = {
            "website": lead["website"],
            "email_subject": result["subject"],
            "email_body":    result["body"],
            "email_services_pitched": services,
            "email_generated_at":     result["generated_at"],
            "email_model":            result["model"],
            "status":  "drafted" if lead.get("status") in (None, "new", "analyzed") else lead.get("status"),
        }
        upsert_lead(update)
        self.status_var.set(update["status"])
        self._render_status_pill(update["status"])
        self._set_status("Email drafted.", theme.TEXT)
        self.refresh_list()

    def _copy_email(self):
        body = self.body_box.get("1.0", "end").strip()
        subject = self.subject_entry.get().strip()
        text = f"Subject: {subject}\n\n{body}" if subject else body
        self.clipboard_clear(); self.clipboard_append(text); self.update()
        self._set_status("Copied to clipboard.", theme.TEXT)

    # ---------------- Email send (SMTP) ----------------

    def _on_attach(self):
        paths = filedialog.askopenfilenames(
            title="Attach files to the email",
            initialdir=str(APP_DIR),
        )
        for p in paths:
            if p and p not in self.attachments:
                self.attachments.append(p)
        self._render_attachment_chips()

    def _render_attachment_chips(self):
        for w in self.attach_chips.winfo_children(): w.destroy()
        if not self.attachments:
            return
        for p in self.attachments:
            chip = ctk.CTkFrame(self.attach_chips, fg_color=theme.SURFACE_HI,
                                corner_radius=4, border_width=1,
                                border_color=theme.BORDER_HI)
            chip.pack(side="left", padx=(0, 4), pady=2)
            ctk.CTkLabel(chip, text=Path(p).name, text_color=theme.TEXT,
                         font=theme.font_small()
                         ).pack(side="left", padx=(8, 4), pady=4)
            ctk.CTkButton(
                chip, text="×", width=22, height=22, fg_color="transparent",
                hover_color=theme.BORDER_HI, text_color=theme.TEXT_DIM,
                command=lambda path=p: self._remove_attachment(path),
            ).pack(side="left", padx=(0, 4))

    def _remove_attachment(self, path: str):
        self.attachments = [a for a in self.attachments if a != path]
        self._render_attachment_chips()

    def _on_send_email(self):
        cfg = load_config()
        from_email   = cfg.get("smtp_from_email", "").strip()
        app_password = cfg.get("smtp_app_password", "").strip()
        from_name    = cfg.get("from_name", "").strip()
        if not from_email or not app_password:
            self.send_status.configure(
                text="Add Gmail credentials in Settings first.",
                text_color=theme.SEV_HIGH_BG)
            SettingsDialog(self); return
        to = self.to_entry.get().strip()
        subject = self.subject_entry.get().strip()
        body = self.body_box.get("1.0", "end").strip()
        if not to:
            self.send_status.configure(text="Enter a recipient address.",
                                       text_color=theme.SEV_HIGH_BG); return

        self.send_btn.configure(state="disabled", text="Sending…")
        self.send_status.configure(text="Connecting to Gmail…",
                                   text_color=theme.TEXT_DIM)
        self._start_progress()

        def run():
            try:
                smtp_sender.send(
                    from_email=from_email, from_name=from_name,
                    app_password=app_password, to=to, subject=subject, body=body,
                    attachments=self.attachments,
                    smtp_host=cfg.get("smtp_host", "smtp.gmail.com"),
                    smtp_port=int(cfg.get("smtp_port", 465)),
                )
                self.after(0, self._after_send, None)
            except Exception as e:
                self.after(0, self._after_send, str(e))

        threading.Thread(target=run, daemon=True).start()

    def _after_send(self, err: Optional[str]):
        self._stop_progress()
        self.send_btn.configure(state="normal", text="Send via Gmail")
        if err:
            self.send_status.configure(text=f"Send failed: {err}",
                                       text_color=theme.SEV_HIGH_BG)
            self._set_status(f"Send failed: {err}", theme.SEV_HIGH_BG); return
        self.send_status.configure(text="Sent.", text_color=theme.TEXT)
        self._set_status("Email sent.", theme.TEXT)
        if self.selected_website:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            upsert_lead({
                "website": self.selected_website,
                "status": "sent", "email_sent_at": now,
            })
            self.status_var.set("sent")
            self._render_status_pill("sent")
            self.refresh_list()

    # ---------------- Demo workflow ----------------

    def _on_make_demo(self):
        if not self.selected_website:
            self._set_status("Analyze a website first.", theme.SEV_HIGH_BG)
            return
        existing = self._find_existing(self.selected_website)
        if not existing:
            self._set_status("Analyze + save the lead before making a demo.",
                             theme.SEV_HIGH_BG); return

        slug = slugify(existing.get("business_name") or
                       existing.get("website") or "lead")
        out = DEMOS_DIR / slug
        out.mkdir(parents=True, exist_ok=True)

        self.make_demo_btn.configure(state="disabled", text="Cloning…")
        self.demo_progress.grid(); self.demo_progress.start()
        self._start_progress()
        self.demo_status_label.configure(
            text=f"Cloning to {out} …", text_color=theme.TEXT_DIM)
        self.tabs.set("Demo")

        def on_progress(msg: str):
            self.after(0, lambda: self.demo_status_label.configure(
                text=msg[:140], text_color=theme.TEXT_DIM))

        cloner = Cloner(existing["website"], out, progress=on_progress)
        self._active_cloner = cloner

        def on_done(result: CloneResult):
            self.after(0, self._after_clone, existing, result)

        cloner.start(on_done=on_done)

    def _after_clone(self, lead: dict, result: CloneResult):
        self.demo_progress.stop(); self.demo_progress.grid_remove()
        self._stop_progress()
        self.make_demo_btn.configure(state="normal", text="Re-clone")

        if not result.index_path:
            err = result.errors[0] if result.errors else "no index page produced"
            self.demo_status_label.configure(
                text=f"Clone failed: {err}", text_color=theme.SEV_HIGH_BG)
            return

        # write ORYN.md
        oryn_md = write_oryn_md(result.folder, lead, result.to_dict())
        demo_record = {
            "folder":     str(result.folder),
            "index_path": str(result.index_path),
            "oryn_md":    str(oryn_md),
            "pages":      [str(p) for p in result.pages],
            "asset_count": len(result.assets),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "errors":     result.errors[:10],
        }
        upsert_lead({"website": lead["website"], "demo": demo_record})
        self.current_demo = demo_record
        self._render_demo_state(demo_record)
        self._set_status(
            f"Demo ready: {len(result.pages)} page(s), "
            f"{len(result.assets)} asset(s).", theme.TEXT)

    def _render_demo_state(self, demo: dict):
        folder = demo.get("folder", "")
        n_pages = len(demo.get("pages") or [])
        n_assets = demo.get("asset_count", 0)
        self.demo_status_label.configure(
            text=f"Demo ready · {n_pages} page(s) · {n_assets} asset(s)",
            text_color=theme.TEXT)
        self.demo_folder_label.configure(text=folder)
        self.make_demo_btn.configure(text="Re-clone")
        self._set_demo_actions_enabled(True)

    def _open_demo_index(self):
        if not self.current_demo: return
        idx = self.current_demo.get("index_path")
        if idx and Path(idx).exists():
            webbrowser.open(Path(idx).as_uri())

    def _open_demo_folder(self):
        if not self.current_demo: return
        folder = self.current_demo.get("folder")
        if folder and Path(folder).exists():
            try:
                os.startfile(folder)        # type: ignore[attr-defined]  # Windows
            except (AttributeError, OSError):
                webbrowser.open(Path(folder).as_uri())

    def _open_oryn_md(self):
        if not self.current_demo: return
        path = self.current_demo.get("oryn_md")
        if path and Path(path).exists():
            try:
                os.startfile(path)          # type: ignore[attr-defined]
            except (AttributeError, OSError):
                webbrowser.open(Path(path).as_uri())

    # ---------------- Sidebar list ----------------

    def refresh_list(self):
        for w in self.list_frame.winfo_children(): w.destroy()
        leads = load_leads()
        q = (self.search_var.get() if hasattr(self, "search_var") else "").strip().lower()
        if q:
            leads = [
                l for l in leads
                if q in (l.get("business_name", "") or "").lower()
                or q in (l.get("website", "") or "").lower()
                or q in (l.get("category_label", "") or "").lower()
            ]
        if not leads:
            ctk.CTkLabel(
                self.list_frame, text="No leads yet.\nPaste a URL on the right →",
                text_color=theme.TEXT_MUTED, justify="left",
                font=theme.font_small(),
            ).pack(pady=20, padx=12)
            return
        leads.sort(key=lambda l: l.get("updated_at") or "", reverse=True)
        for lead in leads:
            self._build_list_item(lead)

    def _build_list_item(self, lead: dict):
        is_selected = (lead.get("website") == self.selected_website)
        bg = theme.SURFACE_HI if is_selected else theme.SURFACE
        card = ctk.CTkFrame(
            self.list_frame, fg_color=bg, corner_radius=6,
            border_width=1,
            border_color=theme.BORDER_HI if is_selected else theme.BORDER,
        )
        card.pack(fill="x", padx=4, pady=3)

        name = lead.get("business_name") or "(unnamed)"
        site = (lead.get("website") or "").replace("https://", "").replace("http://", "")
        score_val = lead.get("score")
        grade = lead.get("grade") or "?"
        category = lead.get("category_label") or "—"
        status = lead.get("status", "new")
        n_high = lead.get("high_priority_count", 0)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 0))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text=name, anchor="w",
                     font=theme.font_h2(), text_color=theme.TEXT
                     ).grid(row=0, column=0, sticky="w")
        if score_val is not None:
            ctk.CTkLabel(top, text=str(score_val), anchor="e",
                         text_color=theme.score_color(score_val),
                         font=theme.font_h2()
                         ).grid(row=0, column=1, sticky="e")

        ctk.CTkLabel(card, text=site, anchor="w",
                     text_color=theme.TEXT_DIM, font=theme.font_small()
                     ).pack(fill="x", padx=12)
        ctk.CTkLabel(card, text=category, anchor="w",
                     text_color=theme.TEXT_MUTED, font=theme.font_small()
                     ).pack(fill="x", padx=12)

        meta = ctk.CTkFrame(card, fg_color="transparent")
        meta.pack(fill="x", padx=12, pady=(2, 10))
        meta.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            meta, text=f"  {STATUS_LABEL.get(status, status.upper())}  ",
            fg_color=(theme.ACCENT if status in ("sent", "replied") else theme.BG),
            text_color=("#000000" if status in ("sent", "replied") else theme.TEXT_DIM),
            corner_radius=3, font=theme.font_micro(),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            meta, text=f"grade {grade} · {n_high} high",
            text_color=theme.TEXT_MUTED, font=theme.font_small(), anchor="e",
        ).grid(row=0, column=1, sticky="e")

        for widget in card.winfo_children() + [card]:
            widget.bind("<Button-1>",
                        lambda _e, lead=lead: self.load_lead_into_form(lead))
            try:
                widget.configure(cursor="hand2")
            except Exception:
                pass

    # ---------------- Export + helpers ----------------

    def _export_csv(self):
        leads = load_leads()
        n = export_all(leads)
        from paths import EMAILS_CSV
        self._set_status(f"Exported {n} email row(s) → {EMAILS_CSV.name}",
                         theme.TEXT)

    def _start_progress(self):
        try:
            self.global_progress.configure(mode="indeterminate")
            self.global_progress.start()
        except Exception:
            pass

    def _stop_progress(self):
        try:
            self.global_progress.stop()
            self.global_progress.set(0)
        except Exception:
            pass

    def _lock(self, locked: bool):
        state = "disabled" if locked else "normal"
        self.analyze_btn.configure(state=state)
        self.save_btn.configure(state=state)
        self.delete_btn.configure(state=state)
        self.analyze_btn.configure(text="Working…" if locked else "Analyze")

    def _set_status(self, text, colour):
        self.status_label.configure(text=text, text_color=colour)


def main():
    app = OrynApp()
    app.mainloop()


if __name__ == "__main__":
    main()
