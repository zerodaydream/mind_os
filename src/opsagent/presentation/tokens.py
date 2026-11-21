"""Design tokens: one palette and one glyph set shared by Rich rows and Textual CSS.

No widget or renderer hard-codes a color or glyph; they read from here. The CSS side receives
the same values as `$ops-*` variables (see `OpsApp.get_css_variables`).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Palette:
    accent: str = "#5fb3d9"
    text: str = "#d7dae0"
    dim: str = "#7f8796"
    border: str = "#4b5263"
    user_bg: str = "#2a2f3a"
    ok: str = "#98c379"
    warn: str = "#e5c07b"
    error: str = "#e06c75"
    danger_bg: str = "#3a2326"

    def css_variables(self) -> dict[str, str]:
        return {
            "ops-accent": self.accent,
            "ops-text": self.text,
            "ops-dim": self.dim,
            "ops-border": self.border,
            "ops-user-bg": self.user_bg,
            "ops-ok": self.ok,
            "ops-warn": self.warn,
            "ops-error": self.error,
            "ops-danger-bg": self.danger_bg,
        }


@dataclass(frozen=True, slots=True)
class Glyphs:
    bullet: str = "⏺"
    result: str = "⎿"
    prompt: str = ">"
    thinking: str = "✻"
    todo_open: str = "☐"
    todo_active: str = "◐"
    todo_done: str = "☒"
    ok: str = "✓"
    fail: str = "✗"
    rejected: str = "⊘"
    warn: str = "⚠"
    info: str = "ℹ"
    ellipsis: str = "…"
    down: str = "↓"
    spinner: tuple[str, ...] = ("·", "✢", "✳", "✶", "✻", "✽")

    @staticmethod
    def ascii() -> Glyphs:
        return Glyphs(
            bullet="*",
            result="L",
            thinking="*",
            todo_open="[ ]",
            todo_active="[~]",
            todo_done="[x]",
            ok="v",
            fail="x",
            rejected="-",
            warn="!",
            info="i",
            ellipsis="...",
            down="v",
            spinner=(".", "o", "O", "o"),
        )


PALETTE = Palette()
