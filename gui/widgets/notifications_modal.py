"""
Notifications popup dialog.
Displays clean user alerts with direct jump actions.
"""
from __future__ import annotations

import customtkinter as ctk
from typing import Callable
from gui import icons, theme
from gui.store import store


class NotificationsModal(ctk.CTkToplevel):
    def __init__(self, parent, on_navigate: Callable[[str], None]):
        super().__init__(parent)
        self.on_navigate = on_navigate

        self.title("Notifications")
        self.geometry("400x340")
        self.resizable(False, False)
        self.configure(fg_color=theme.CARD_BG)

        self.transient(parent)
        self.grab_set()

        # Position near top right of parent
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_w = parent.winfo_width()
        x = parent_x + parent_w - 430
        y = parent_y + 60
        self.geometry(f"+{max(10, x)}+{max(10, y)}")

        container = ctk.CTkFrame(self, fg_color=theme.CARD_BG, corner_radius=theme.RADIUS)
        container.pack(fill="both", expand=True, padx=16, pady=16)

        # Header
        hdr = ctk.CTkFrame(container, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            hdr,
            text="Notifications",
            font=theme.font(15, "bold"),
            text_color=theme.TEXT_PRIMARY,
        ).pack(side="left")

        self._close_glyph = icons.image("x", theme.ICON_SM, theme.TEXT_SECONDARY)
        def _mark_all_read():
            for notif in store.notifications:
                notif["unread"] = False
            store.unread_notifications_count = 0
            store.notify("notifications_updated")
            self.destroy()

        ctk.CTkButton(
            hdr,
            text="Mark all read",
            font=theme.font(10),
            width=75,
            height=24,
            fg_color=theme.MAIN_BG,
            hover_color=theme.CARD_BORDER,
            text_color=theme.TEXT_SECONDARY,
            corner_radius=theme.RADIUS_SM,
            command=_mark_all_read,
        ).pack(side="right", padx=(0, 8))

        ctk.CTkButton(
            hdr,
            image=self._close_glyph,
            text="" if self._close_glyph else "X",
            font=theme.font(11),
            width=26,
            height=26,
            fg_color=theme.MAIN_BG,
            hover_color=theme.CARD_BORDER,
            text_color=theme.TEXT_SECONDARY,
            corner_radius=13,
            command=self.destroy,
        ).pack(side="right")

        # Notification Items
        for notif in store.notifications:
            card = ctk.CTkFrame(
                container,
                fg_color=theme.MAIN_BG if not notif.get("unread") else theme.INFO_LIGHT,
                border_width=1,
                border_color=theme.INFO_BORDER if notif.get("unread") else theme.CARD_BORDER,
                corner_radius=theme.RADIUS_SM,
            )
            card.pack(fill="x", pady=4)

            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=(8, 2))

            ctk.CTkLabel(
                row,
                text=notif.get("title", ""),
                font=theme.font(12, "bold"),
                text_color=theme.TEXT_PRIMARY,
            ).pack(side="left")

            ctk.CTkLabel(
                row,
                text=notif.get("time", ""),
                font=theme.font(10),
                text_color=theme.TEXT_MUTED,
            ).pack(side="right")

            ctk.CTkLabel(
                card,
                text=notif.get("message", ""),
                font=theme.font(11),
                text_color=theme.TEXT_SECONDARY,
                wraplength=340,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=10, pady=(0, 8))
