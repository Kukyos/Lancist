"""
Monochrome theme constants — one source of truth for colours, type,
and spacing so the UI feels cohesive.
"""
import customtkinter as ctk

# ---- Palette ----
BG          = "#0B0B0B"   # window background
SURFACE     = "#161616"   # cards
SURFACE_HI  = "#1F1F1F"   # hovered / elevated cards
BORDER      = "#2A2A2A"
BORDER_HI   = "#3A3A3A"

TEXT        = "#F2F2F2"
TEXT_DIM    = "#A0A0A0"
TEXT_MUTED  = "#5E5E5E"

ACCENT      = "#FFFFFF"   # pure white for important highlights
ACCENT_DIM  = "#C8C8C8"

# Severity / score: monochrome ramp — brightness = priority
SEV_HIGH_BG    = "#FFFFFF"   # filled white pill
SEV_HIGH_FG    = "#000000"
SEV_MED_FG     = "#F2F2F2"
SEV_MED_BORDER = "#FFFFFF"
SEV_LOW_FG     = "#888888"


def setup():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")  # base; we override colors per-widget


def score_color(score: int) -> str:
    if score >= 80: return "#FFFFFF"
    if score >= 65: return "#DCDCDC"
    if score >= 50: return "#A8A8A8"
    if score >= 35: return "#7A7A7A"
    return "#555555"


def grade_color(grade: str) -> str:
    return {"A": "#FFFFFF", "B": "#E8E8E8", "C": "#B0B0B0",
            "D": "#787878", "F": "#555555"}.get(grade, TEXT_MUTED)


# ---- Type ----
def font_title():    return ctk.CTkFont(family="Segoe UI", size=26, weight="bold")
def font_h1():       return ctk.CTkFont(family="Segoe UI", size=18, weight="bold")
def font_h2():       return ctk.CTkFont(family="Segoe UI", size=14, weight="bold")
def font_body():     return ctk.CTkFont(family="Segoe UI", size=12)
def font_small():    return ctk.CTkFont(family="Segoe UI", size=11)
def font_micro():    return ctk.CTkFont(family="Segoe UI", size=10, weight="bold")
def font_score():    return ctk.CTkFont(family="Segoe UI", size=54, weight="bold")
def font_logo():     return ctk.CTkFont(family="Segoe UI", size=20, weight="bold")
