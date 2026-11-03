"""
Sidebar component for the VAVE desktop interface.

A white rail. The current page is marked by a soft tint and a short accent
bar rather than a full block of colour, which keeps the orange for the few
places that need attention.
"""
from __future__ import annotations

import customtkinter as ctk
from gui import icons, theme
from gui.store import store


class Sidebar(ctk.CTkFrame):
    def __init__(self, parent, on_navigate, on_voice_toggle):
        super().__init__(
            parent,
            width=230,
            fg_color=theme.SIDEBAR_BG,
            corner_radius=0,
        )
        self.grid_propagate(False)
        self.pack_propagate(False)
        self.on_navigate = on_navigate
        self.on_voice_toggle = on_voice_toggle
        self.nav_buttons = {}

        # 1. Brand Section
        brand_frame = ctk.CTkFrame(self, fg_color="transparent")
        brand_frame.pack(fill="x", padx=20, pady=(6, 24))

        title_lbl = ctk.CTkLabel(
            brand_frame,
            text="VAVE",
            font=theme.font(18, "bold"),
            text_color=theme.SIDEBAR_TEXT,
            anchor="w",
        )
        title_lbl.pack(anchor="w")

        tagline_lbl = ctk.CTkLabel(
            brand_frame,
            text="Your AI, Everywhere",
            font=theme.font(11),
            text_color=theme.SIDEBAR_TEXT_MUTED,
            anchor="w",
        )
        tagline_lbl.pack(anchor="w", pady=(2, 0))

        # 3. Navigation Items
        self.nav_items = [
            ("home", "Home", "home"),
            ("projects", "Projects", "sparkle"),
            ("devices", "My Devices", "devices"),
            ("files", "My Files", "files"),
            ("google", "Google", "google"),
            ("web", "Web", "globe"),
            ("activity", "Activity", "activity"),
            ("settings", "Settings", "settings"),
        ]

        self.nav_container = ctk.CTkFrame(self, fg_color="transparent")
        self.nav_container.pack(fill="x", padx=12, anchor="n")

        # Tk drops an image the moment Python stops referencing it, so both
        # states of every glyph are held here for the life of the sidebar.
        self._icons = {}
        self.nav_markers = {}

        for page_id, label, icon_name in self.nav_items:
            selected = page_id == "home"
            self._icons[page_id] = {
                "on": icons.image(icon_name, theme.ICON, theme.ACCENT_DEEP),
                "off": icons.image(icon_name, theme.ICON, theme.SIDEBAR_TEXT_MUTED),
            }
            image = self._icons[page_id]["on" if selected else "off"]

            # Row = indicator bar + button, so the marker can sit outside the
            # button's own rounded fill.
            row = ctk.CTkFrame(self.nav_container, fg_color="transparent",
                               height=38)
            row.pack(fill="x", pady=1)
            # Without this the row grows to share the container's height and
            # the rail ends up with a huge gap between every entry.
            row.pack_propagate(False)

            marker = ctk.CTkFrame(
                row,
                width=3,
                corner_radius=2,
                fg_color=theme.ACCENT_DEEP if selected else "transparent",
            )
            marker.pack(side="left", fill="y", padx=(0, 6), pady=6)

            btn = ctk.CTkButton(
                row,
                image=image,
                compound="left",
                text=f"   {label}" if image else label,
                font=theme.font(13, "bold" if selected else "normal"),
                fg_color=theme.SURFACE_SUBTLE if selected else "transparent",
                text_color=theme.SIDEBAR_TEXT if selected else theme.SIDEBAR_TEXT_MUTED,
                hover_color=theme.SIDEBAR_HOVER,
                corner_radius=theme.RADIUS_SM,
                height=38,
                anchor="w",
                command=lambda pid=page_id: self._select_page(pid),
            )
            btn.pack(side="left", fill="x", expand=True)

            self.nav_buttons[page_id] = btn
            self.nav_markers[page_id] = marker

        # 4. Bottom Status Pill (VAVE Online & Voice Control)
        bottom_frame = ctk.CTkFrame(
            self,
            fg_color=theme.SIDEBAR_HOVER,
            corner_radius=theme.RADIUS,
            border_width=1,
            border_color=theme.SIDEBAR_BORDER,
        )
        bottom_frame.pack(side="bottom", fill="x", padx=14, pady=16)

        status_row = ctk.CTkFrame(bottom_frame, fg_color="transparent")
        status_row.pack(fill="x", padx=12, pady=(12, 4))

        self.dot_label = ctk.CTkLabel(
            status_row,
            text="●",
            font=theme.font(14, "bold"),
            text_color=theme.SUCCESS,
        )
        self.dot_label.pack(side="left")

        self.status_title = ctk.CTkLabel(
            status_row,
            text=" VAVE Online",
            font=theme.font(12, "bold"),
            text_color=theme.SIDEBAR_TEXT,
        )
        self.status_title.pack(side="left")

        self.status_sub = ctk.CTkLabel(
            bottom_frame,
            text="All systems running",
            font=theme.font(10),
            text_color=theme.SIDEBAR_TEXT_MUTED,
            anchor="w",
        )
        self.status_sub.pack(fill="x", padx=16, pady=(0, 12))

    def _select_page(self, page_id: str):
        self.highlight(page_id)
        self.on_navigate(page_id)

    def highlight(self, page_id: str):
        """Mark one row as current, without navigating anywhere."""
        for pid, btn in self.nav_buttons.items():
            selected = pid == page_id
            btn.configure(
                fg_color=theme.SURFACE_SUBTLE if selected else "transparent",
                text_color=theme.SIDEBAR_TEXT if selected else theme.SIDEBAR_TEXT_MUTED,
                font=theme.font(13, "bold" if selected else "normal"),
            )
            marker = self.nav_markers.get(pid)
            if marker is not None:
                marker.configure(fg_color=theme.ACCENT_DEEP if selected
                                 else "transparent")
            image = self._icons.get(pid, {}).get("on" if selected else "off")
            if image:
                btn.configure(image=image)

    def _toggle_voice(self):
        self.on_voice_toggle()

    def update_voice_state(self, is_listening: bool):
        if is_listening:
            self.status_sub.configure(text="Listening for voice...")
        else:
            self.status_sub.configure(text="All systems running")
