"""
Clean approval modal dialog.
Prompts for permission with plain-English context and clear action buttons.
"""
from __future__ import annotations

import customtkinter as ctk
from gui import icons, theme
from gui.store import store


class ApprovalModal(ctk.CTkToplevel):
    def __init__(self, parent, approval_data):
        super().__init__(parent)
        self.approval_data = approval_data

        self.title("Approval Required")
        self.geometry("440x300")
        self.resizable(False, False)
        self.configure(fg_color=theme.CARD_BG)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._reject)

        # Center on parent
        try:
            parent_x = parent.winfo_rootx()
            parent_y = parent.winfo_rooty()
            parent_w = parent.winfo_width()
            parent_h = parent.winfo_height()
            x = parent_x + (parent_w // 2) - 220
            y = parent_y + (parent_h // 2) - 150
            self.geometry(f"+{max(10, x)}+{max(10, y)}")
        except Exception:
            pass

        container = ctk.CTkFrame(self, fg_color=theme.CARD_BG, corner_radius=theme.RADIUS)
        container.pack(fill="both", expand=True, padx=20, pady=20)

        # Header with warning/attention icon
        hdr = ctk.CTkFrame(container, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 10))

        self._warn_glyph = icons.image("warning", theme.ICON_LG, theme.WARNING)
        ctk.CTkLabel(
            hdr,
            image=self._warn_glyph,
            text="" if self._warn_glyph else "!",
            text_color=theme.WARNING,
            font=theme.font(16, "bold"),
        ).pack(side="left", padx=(0, 10))

        ctk.CTkLabel(
            hdr,
            text=approval_data.get("title", "VAVE needs your approval"),
            font=theme.font(16, "bold"),
            text_color=theme.TEXT_PRIMARY,
        ).pack(side="left")

        # Description box
        desc_card = ctk.CTkFrame(
            container,
            fg_color=theme.WARNING_LIGHT,
            border_width=1,
            border_color=theme.WARNING_BORDER,
            corner_radius=theme.RADIUS,
        )
        desc_card.pack(fill="both", expand=True, pady=(0, 16))

        ctk.CTkLabel(
            desc_card,
            text=approval_data.get("description", ""),
            font=theme.font(12),
            text_color=theme.TEXT_PRIMARY,
            justify="left",
            wraplength=360,
            anchor="nw",
        ).pack(fill="both", expand=True, padx=16, pady=14)

        # Action Buttons
        btn_row = ctk.CTkFrame(container, fg_color="transparent")
        btn_row.pack(fill="x")

        ctk.CTkButton(
            btn_row,
            text="Not Now",
            font=theme.font(12),
            fg_color=theme.MAIN_BG,
            hover_color=theme.CARD_BORDER,
            text_color=theme.TEXT_SECONDARY,
            border_width=1,
            border_color=theme.CARD_BORDER,
            corner_radius=theme.RADIUS_SM,
            height=36,
            command=self._reject,
        ).pack(side="left", expand=True, fill="x", padx=(0, 6))

        ctk.CTkButton(
            btn_row,
            text="Approve",
            font=theme.font(12, "bold"),
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER,
            text_color=theme.ON_ACCENT,
            corner_radius=theme.RADIUS_SM,
            height=36,
            command=self._approve,
        ).pack(side="left", expand=True, fill="x", padx=(6, 0))

    def _approve(self):
        self.destroy()
        req_id = self.approval_data.get("req_id")
        if req_id:
            try:
                from assistant import confirm
                confirm.resolve(req_id, True)
            except Exception:
                pass
        on_resolve = self.approval_data.get("on_resolve")
        if callable(on_resolve):
            try:
                on_resolve(True)
            except Exception:
                pass
        store.resolve_approval(True)

    def _reject(self):
        self.destroy()
        req_id = self.approval_data.get("req_id")
        if req_id:
            try:
                from assistant import confirm
                confirm.resolve(req_id, False)
            except Exception:
                pass
        on_resolve = self.approval_data.get("on_resolve")
        if callable(on_resolve):
            try:
                on_resolve(False)
            except Exception:
                pass
        store.resolve_approval(False)
