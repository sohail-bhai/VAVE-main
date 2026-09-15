"""
Home Screen component.
Faithful reproduction of the calm, minimal reference interface.
"""
from __future__ import annotations

import datetime
import customtkinter as ctk
from typing import Callable
from gui import icons, theme
from gui.store import store


class HomePage(ctk.CTkScrollableFrame):
    def __init__(self, parent, on_navigate: Callable[[str], None], on_execute_command: Callable[[str], None], on_voice_toggle: Callable[[], None] | None = None):
        super().__init__(
            parent,
            fg_color="transparent",
            scrollbar_button_color=theme.CARD_BORDER,
            scrollbar_button_hover_color=theme.TEXT_MUTED,
        )
        self.on_navigate = on_navigate
        self.on_execute_command = on_execute_command
        self.on_voice_toggle = on_voice_toggle
        # Tk forgets an image nothing references, so every drawn glyph on this
        # page is kept here for as long as the page lives.
        self._glyphs = []

        # 1. Header with greeting and date badge
        self._build_header()

        # 2. Main Command Input Box
        self._build_command_input()

        # 3. Suggestion Chips
        self._build_suggestion_chips()

        # 3.5 System Health Bar & Workspace Sync
        self._build_system_health_bar()

        # 4. Connected Environment (4 cards)
        self._build_environment_cards()

        # 5. Current Task Card
        self._build_current_task()

        # 6. Quick Access Cards (3 cards)
        self._build_quick_access()

        # 7. Bottom Simple Explanation
        self._build_bottom_explanation()

        # Listen for task updates
        store.subscribe(self._on_store_update)

    def _on_store_update(self, event: str, data: any):
        if event == "task_updated":
            self._update_task_ui()

    def _build_header(self):
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=16, pady=(16, 12))

        left = ctk.CTkFrame(header_frame, fg_color="transparent")
        left.pack(side="left")

        # Greeting with sun icon
        greet_row = ctk.CTkFrame(left, fg_color="transparent")
        greet_row.pack(anchor="w")

        # Dynamic greeting based on current time
        hour = datetime.datetime.now().hour
        greeting_text = "Good morning," if hour < 12 else ("Good afternoon," if hour < 17 else "Good evening,")
        ctk.CTkLabel(
            greet_row,
            text=greeting_text,
            font=theme.font(13),
            text_color=theme.TEXT_SECONDARY,
        ).pack(side="left")

        # Big bold question
        ctk.CTkLabel(
            left,
            text="How can I help you today?",
            font=theme.font(22, "bold"),
            text_color=theme.TEXT_PRIMARY,
        ).pack(anchor="w", pady=(2, 0))

        # Right date badge
        # Format date safely cross-platform. %d is zero-padded.
        now = datetime.datetime.now()
        date_str = now.strftime("%a, %d %b %Y") if hasattr(now, "strftime") else "Thu, 04 Sep 2025"
        # Optional: remove zero padding from day (e.g. '04' -> '4') by replacing ' 0' with ' '
        date_str = date_str.replace(" 0", " ")
        
        date_badge = ctk.CTkLabel(
            header_frame,
            text=date_str,
            font=theme.font(11),
            text_color=theme.TEXT_MUTED,
            fg_color=theme.CARD_BG,
            corner_radius=theme.RADIUS,
            padx=12,
            pady=6,
        )
        date_badge.pack(side="right", pady=6)

    def _build_command_input(self):
        cmd_card = ctk.CTkFrame(
            self,
            fg_color=theme.CARD_BG,
            corner_radius=theme.RADIUS,
            border_width=1,
            border_color=theme.CARD_BORDER,
        )
        cmd_card.pack(fill="x", padx=16, pady=(4, 10))

        row = ctk.CTkFrame(cmd_card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=10)

        spark = icons.image("sparkle", theme.ICON, theme.ACCENT_DEEP)
        self._glyphs.append(spark)
        ctk.CTkLabel(
            row,
            image=spark,
            text="" if spark else "*",
            font=theme.font(15, "bold"),
            text_color=theme.ACCENT_DEEP,
        ).pack(side="left", padx=(0, 10))

        self.cmd_entry = ctk.CTkEntry(
            row,
            placeholder_text="Tell me what you want to do...",
            placeholder_text_color=theme.TEXT_MUTED,
            font=theme.font(13),
            fg_color="transparent",
            border_width=0,
            text_color=theme.TEXT_PRIMARY,
        )
        self.cmd_entry.pack(side="left", fill="x", expand=True)
        self.cmd_entry.bind("<Return>", lambda e: self._submit_input())

        mic_glyph = icons.image("mic", theme.ICON, theme.TEXT_SECONDARY)
        self._glyphs.append(mic_glyph)
        mic_btn = ctk.CTkButton(
            row,
            text="" if mic_glyph else "Voice",
            image=mic_glyph,
            width=36,
            height=36,
            corner_radius=18,
            fg_color="transparent",
            hover_color=theme.SURFACE_SUBTLE,
            text_color=theme.TEXT_SECONDARY,
            command=self._toggle_voice,
        )
        mic_btn.pack(side="right", padx=(0, 6))

        send_glyph = icons.image("send", theme.ICON, theme.ON_ACCENT)
        self._glyphs.append(send_glyph)
        arrow_btn = ctk.CTkButton(
            row,
            text="" if send_glyph else "Send",
            image=send_glyph,
            font=theme.font(12, "bold"),
            width=36,
            height=36,
            corner_radius=18,
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER,
            text_color=theme.ON_ACCENT,
            command=self._submit_input,
        )
        arrow_btn.pack(side="right")

    def _submit_input(self):
        text = self.cmd_entry.get().strip()
        if text:
            self.cmd_entry.delete(0, "end")
            self.on_execute_command(text)

    def _toggle_voice(self):
        if self.on_voice_toggle:
            self.on_voice_toggle()

    def _build_suggestion_chips(self):
        chips_frame = ctk.CTkFrame(self, fg_color="transparent")
        chips_frame.pack(fill="x", padx=16, pady=(0, 16))

        ctk.CTkLabel(
            chips_frame,
            text="Try something like:",
            font=theme.font(11),
            text_color=theme.TEXT_MUTED,
        ).pack(side="left", padx=(0, 10))

        # Suggestion chips come from what is actually used, so the home screen
        # teaches the commands that matter to this user. Fresh installs with no
        # history yet fall back to a sensible starter set.
        from gui import integrations
        chips = integrations.frequent_command_chips(limit=4) or [
            ("Find my presentation", "file"),
            ("Continue my project", "play"),
            ("Research something", "search"),
            ("Summarize my emails", "mail"),
        ]

        for text, icon_name in chips:
            glyph = icons.image(icon_name, theme.ICON_SM, theme.TEXT_MUTED)
            self._glyphs.append(glyph)
            chip = ctk.CTkButton(
                chips_frame,
                image=glyph,
                text=f"  {text}" if glyph else text,
                compound="left",
                font=theme.font(11),
                fg_color=theme.CARD_BG,
                hover_color=theme.CARD_HOVER,
                text_color=theme.TEXT_SECONDARY,
                border_width=1,
                border_color=theme.CARD_BORDER,
                corner_radius=theme.RADIUS_SM,
                height=30,
                command=lambda t=text: self.on_execute_command(t),
            )
            chip.pack(side="left", padx=4)

    def _build_system_health_bar(self):
        self.health_card = ctk.CTkFrame(
            self,
            fg_color=theme.CARD_BG,
            border_width=1,
            border_color=theme.CARD_BORDER,
            corner_radius=theme.RADIUS,
        )
        self.health_card.pack(fill="x", padx=16, pady=(0, 14))
        self._render_system_health_bar()

    def _render_system_health_bar(self):
        for child in self.health_card.winfo_children():
            child.destroy()

        from gui import integrations
        health = integrations.system_health_status()
        drive_info = integrations.drive_sync_status()

        row = ctk.CTkFrame(self.health_card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=10)

        # 1. CPU
        cpu_box = ctk.CTkFrame(row, fg_color=theme.SURFACE_SUBTLE, corner_radius=theme.RADIUS_SM)
        cpu_box.pack(side="left", fill="both", expand=True, padx=(0, 6))
        ctk.CTkLabel(cpu_box, text=f"CPU {health['cpu']}%", font=theme.font(11, "bold"), text_color=theme.TEXT_PRIMARY).pack(pady=6, padx=8)

        # 2. RAM
        ram_box = ctk.CTkFrame(row, fg_color=theme.SURFACE_SUBTLE, corner_radius=theme.RADIUS_SM)
        ram_box.pack(side="left", fill="both", expand=True, padx=3)
        ctk.CTkLabel(ram_box, text=f"RAM {health['ram']}%", font=theme.font(11, "bold"), text_color=theme.TEXT_PRIMARY).pack(pady=6, padx=8)

        # 3. C: Disk
        c_gb = health["c_free_gb"]
        from assistant.config import get_setting
        warn_gb = float(get_setting("disk_warning_gb", 15.0))
        disk_color = theme.DANGER if c_gb < warn_gb else theme.SUCCESS
        disk_box = ctk.CTkFrame(row, fg_color=theme.SURFACE_SUBTLE, corner_radius=theme.RADIUS_SM)
        disk_box.pack(side="left", fill="both", expand=True, padx=3)
        ctk.CTkLabel(disk_box, text=f"C: {c_gb} GB free", font=theme.font(11, "bold"), text_color=disk_color).pack(pady=6, padx=8)

        # 4. Drive Sync
        drive_box = ctk.CTkFrame(row, fg_color=theme.SURFACE_SUBTLE, corner_radius=theme.RADIUS_SM)
        drive_box.pack(side="left", fill="both", expand=True, padx=(6, 0))
        d_row = ctk.CTkFrame(drive_box, fg_color="transparent")
        d_row.pack(pady=4, padx=6)
        files_count = drive_info.get("total_files", 0)
        ctk.CTkLabel(d_row, text=f"Drive: {files_count} docs", font=theme.font(11, "bold"), text_color=theme.TEXT_PRIMARY).pack(side="left", padx=(0, 6))

        def _on_sync_click():
            import threading
            def _bg_sync():
                try:
                    from assistant.workspace.drive_indexer import DriveSemanticIndexer
                    idx = DriveSemanticIndexer()
                    idx.sync_drive_index()
                    idx.close()
                except Exception:
                    pass
                from gui import ui_queue
                ui_queue.post_to(self, self._render_system_health_bar)
            threading.Thread(target=_bg_sync, daemon=True).start()

        sync_btn = ctk.CTkButton(
            d_row,
            text="Sync",
            width=42,
            height=22,
            font=theme.font(10, "bold"),
            fg_color=theme.ACCENT,
            text_color=theme.ON_ACCENT,
            hover_color=theme.ACCENT_HOVER,
            corner_radius=theme.RADIUS_SM,
            command=_on_sync_click,
        )
        sync_btn.pack(side="left")

    def _build_environment_cards(self):
        self.env_grid = ctk.CTkFrame(self, fg_color="transparent")
        self.env_grid.pack(fill="x", padx=16, pady=(0, 16))
        self._render_environment_cards()

    def _render_environment_cards(self):
        grid = self.env_grid
        for child in grid.winfo_children():
            child.destroy()
        grid.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="env")

        from assistant.api.server import get_local_server
        from gui import integrations, ui_queue

        # Each card says what is true right now. A card that always reads
        # "Connected" teaches the user to ignore all four. The checks talk to
        # the network, so they answer from cache and refresh in the
        # background rather than holding up the window.
        def _refresh(_value):
            ui_queue.post_to(self, self._render_environment_cards)

        google = integrations.cached("google", _refresh)
        internet = integrations.cached("internet", _refresh)
        phone_paired = get_local_server().running

        # Colour marks reachability and nothing else, so an unreachable card
        # stays grey instead of picking a decorative hue.
        cards_data = [
            ("My Computer", "Online", "monitor", True, "devices"),
            ("My Phone", "Ready to pair" if phone_paired else "Not connected",
             "phone", phone_paired, "devices"),
            ("Google", google["label"], "google", google["connected"], "google"),
            ("Internet", internet["label"], "globe", internet["connected"], "web"),
        ]

        for idx, (name, status, icon_name, live, route) in enumerate(cards_data):
            tint = theme.ACCENT_DEEP if live else theme.TEXT_MUTED
            card = ctk.CTkFrame(
                grid,
                fg_color=theme.CARD_BG,
                border_width=1,
                border_color=theme.ACCENT_BORDER if live else theme.CARD_BORDER,
                corner_radius=theme.RADIUS,
                height=118,
            )
            card.grid(row=0, column=idx, padx=4, sticky="nsew")
            card.pack_propagate(False)

            content = ctk.CTkFrame(card, fg_color="transparent")
            content.pack(fill="both", expand=True, padx=14, pady=(14, 10))

            # The glyph sits above the label rather than beside it: four cards
            # across a narrow column have no room for a side-by-side row, which
            # is what was clipping the names.
            glyph = icons.image(icon_name, theme.ICON, tint)
            self._glyphs.append(glyph)
            chip = ctk.CTkLabel(
                content,
                image=glyph,
                text="" if glyph else name[0],
                fg_color=theme.ACCENT_LIGHT if live else theme.SURFACE_SUBTLE,
                text_color=tint,
                corner_radius=theme.RADIUS_SM,
                width=30,
                height=30,
            )
            chip.pack(anchor="w")

            name_lbl = ctk.CTkLabel(
                content,
                text=name,
                font=theme.font(12, "bold"),
                text_color=theme.TEXT_PRIMARY,
                anchor="w",
            )
            name_lbl.pack(anchor="w", pady=(8, 0))

            status_row = ctk.CTkFrame(content, fg_color="transparent")
            status_row.pack(anchor="w")

            dot = ctk.CTkLabel(
                status_row,
                text="\u25cf",
                font=theme.font(8, "bold"),
                text_color=theme.SUCCESS if live else theme.TEXT_MUTED,
            )
            dot.pack(side="left", padx=(0, 5))

            status_lbl = ctk.CTkLabel(
                status_row,
                text=status,
                font=theme.font(11),
                text_color=theme.TEXT_SECONDARY,
            )
            status_lbl.pack(side="left")

            # Clickable overlay to navigate
            for widget in (card, content, chip, name_lbl, status_row, dot,
                           status_lbl):
                widget.bind("<Button-1>", lambda e, r=route: self.on_navigate(r))

    def _build_current_task(self):
        self.task_card = ctk.CTkFrame(
            self,
            fg_color=theme.CARD_BG,
            corner_radius=theme.RADIUS,
            border_width=1,
            border_color=theme.CARD_BORDER,
        )
        self.task_card.pack(fill="x", padx=16, pady=(0, 16))

        # Top row: title & details button
        top_row = ctk.CTkFrame(self.task_card, fg_color="transparent")
        top_row.pack(fill="x", padx=18, pady=(16, 4))

        title_col = ctk.CTkFrame(top_row, fg_color="transparent")
        title_col.pack(side="left", fill="x", expand=True)

        badge_row = ctk.CTkFrame(title_col, fg_color="transparent")
        badge_row.pack(anchor="w")

        badge_glyph = icons.image("refresh", theme.ICON_SM, theme.ACCENT_DEEP)
        self._glyphs.append(badge_glyph)
        ctk.CTkLabel(
            badge_row,
            image=badge_glyph,
            text="" if badge_glyph else "~",
            font=theme.font(12, "bold"),
            text_color=theme.ACCENT_DEEP,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(
            badge_row,
            text="Current Task",
            font=theme.font(11, "bold"),
            text_color=theme.TEXT_MUTED,
        ).pack(side="left")

        self.task_title_lbl = ctk.CTkLabel(
            title_col,
            text=store.current_task.get("title", "Preparing your project"),
            font=theme.font(16, "bold"),
            text_color=theme.TEXT_PRIMARY,
            anchor="w",
        )
        self.task_title_lbl.pack(anchor="w", pady=(2, 0))

        self.task_sub_lbl = ctk.CTkLabel(
            title_col,
            text=store.current_task.get("subtitle", "VAVE is researching, organizing files and writing a draft for you."),
            font=theme.font(12),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        )
        self.task_sub_lbl.pack(anchor="w", pady=(2, 0))

        # View Details button
        details_btn = ctk.CTkButton(
            top_row,
            text="View Details",
            font=theme.font(11),
            fg_color=theme.MAIN_BG,
            hover_color=theme.CARD_BORDER,
            text_color=theme.TEXT_PRIMARY,
            border_width=1,
            border_color=theme.CARD_BORDER,
            corner_radius=theme.RADIUS_SM,
            width=90,
            height=28,
            command=lambda: store.open_drawer("task", store.current_task),
        )
        details_btn.pack(side="right", anchor="n")

        # Step indicators container
        self.steps_row = ctk.CTkFrame(self.task_card, fg_color="transparent")
        self.steps_row.pack(fill="x", padx=18, pady=(12, 16))
        self._render_task_steps()

    def _render_task_steps(self):
        for child in self.steps_row.winfo_children():
            child.destroy()

        steps = store.current_task.get("steps", [])
        for idx, step in enumerate(steps):
            col = ctk.CTkFrame(self.steps_row, fg_color="transparent")
            col.pack(side="left", padx=(0, 16))

            st = step.get("status")
            if st == "done":
                mark, color = "check", theme.SUCCESS
            elif st == "active":
                mark, color = None, theme.ACCENT_DEEP
            else:
                mark, color = None, theme.TEXT_MUTED

            glyph = icons.image(mark, 12, color, stroke_width=2.4) if mark else None
            self._glyphs.append(glyph)
            ctk.CTkLabel(
                col,
                image=glyph,
                text="" if glyph else ("\u25cf" if st == "active" else "\u25cb"),
                font=theme.font(10, "bold"),
                text_color=color,
                width=14,
            ).pack(side="left", padx=(0, 5))

            ctk.CTkLabel(
                col,
                text=f"{idx+1}. {step.get('text', '')}",
                font=theme.font(11, "bold" if st == "active" else "normal"),
                text_color=theme.TEXT_PRIMARY if st != "pending" else theme.TEXT_MUTED,
            ).pack(side="left")

    def _update_task_ui(self):
        self.task_title_lbl.configure(text=store.current_task.get("title", ""))
        self.task_sub_lbl.configure(text=store.current_task.get("subtitle", ""))
        self._render_task_steps()

    def _build_quick_access(self):
        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.pack(fill="x", padx=16, pady=(0, 16))
        grid.grid_columnconfigure((0, 1, 2), weight=1, uniform="quick")

        items = [
            ("My Files", "Find and access your files from any device", "files", "files"),
            ("Google", "Search your Drive, Gmail, Calendar and more", "google", "google"),
            ("Web", "Ask VAVE to search the internet or do online tasks", "globe", "web"),
        ]

        for idx, (title, desc, icon_name, route) in enumerate(items):
            card = ctk.CTkFrame(
                grid,
                fg_color=theme.CARD_BG,
                border_width=1,
                border_color=theme.CARD_BORDER,
                corner_radius=theme.RADIUS,
                height=82,
            )
            card.grid(row=0, column=idx, padx=4, sticky="nsew")
            card.pack_propagate(False)

            content = ctk.CTkFrame(card, fg_color="transparent")
            content.pack(fill="both", expand=True, padx=14, pady=12)

            glyph = icons.image(icon_name, theme.ICON, theme.ACCENT_DEEP)
            self._glyphs.append(glyph)
            left_icon = ctk.CTkLabel(
                content,
                image=glyph,
                text="" if glyph else title[0],
                fg_color=theme.ACCENT_LIGHT,
                text_color=theme.ACCENT_DEEP,
                corner_radius=theme.RADIUS_SM,
                width=30,
                height=30,
            )
            left_icon.pack(side="left", padx=(0, 12))

            text_col = ctk.CTkFrame(content, fg_color="transparent")
            text_col.pack(side="left", fill="both", expand=True)

            title_row = ctk.CTkFrame(text_col, fg_color="transparent")
            title_row.pack(fill="x", anchor="w")

            ctk.CTkLabel(
                title_row,
                text=title,
                font=theme.font(12, "bold"),
                text_color=theme.TEXT_PRIMARY,
            ).pack(side="left")

            ctk.CTkLabel(
                title_row,
                text=" ›",
                font=theme.font(12),
                text_color=theme.TEXT_MUTED,
            ).pack(side="left")

            ctk.CTkLabel(
                text_col,
                text=desc,
                font=theme.font(10),
                text_color=theme.TEXT_SECONDARY,
                wraplength=170,
                justify="left",
                anchor="w",
            ).pack(anchor="w", pady=(2, 0))

            card.bind("<Button-1>", lambda e, r=route: self.on_navigate(r))
            for child in [content, left_icon, text_col, title_row]:
                child.bind("<Button-1>", lambda e, r=route: self.on_navigate(r))

    def _build_bottom_explanation(self):
        bar = ctk.CTkFrame(
            self,
            fg_color=theme.SURFACE_SUBTLE,
            corner_radius=theme.RADIUS,
        )
        bar.pack(fill="x", padx=16, pady=(0, 20))

        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=14)

        # Left: Sparkle + core idea
        left_col = ctk.CTkFrame(inner, fg_color="transparent")
        left_col.pack(side="left", fill="x", expand=True)

        heading_row = ctk.CTkFrame(left_col, fg_color="transparent")
        heading_row.pack(anchor="w")

        hint_glyph = icons.image("sparkle", theme.ICON_SM, theme.ACCENT_DEEP)
        self._glyphs.append(hint_glyph)
        ctk.CTkLabel(
            heading_row,
            image=hint_glyph,
            text="" if hint_glyph else "*",
            font=theme.font(12, "bold"),
            text_color=theme.ACCENT_DEEP,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(
            heading_row,
            text="You can just ask in normal language.",
            font=theme.font(12, "bold"),
            text_color=theme.TEXT_PRIMARY,
        ).pack(side="left")

        examples = ctk.CTkLabel(
            left_col,
            text='"Send this file to my phone"   "Find my last email from college"   "Open my project folder"',
            font=theme.font(11),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        )
        examples.pack(anchor="w", pady=(2, 0))

        # Right note
        right_lbl = ctk.CTkLabel(
            inner,
            text="VAVE understands. No technical knowledge needed.",
            font=theme.font(11),
            text_color=theme.TEXT_MUTED,
        )
        right_lbl.pack(side="right")
