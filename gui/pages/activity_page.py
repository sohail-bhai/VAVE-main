"""
Activity Screen component.
Displays human-readable timeline of past events and what VAVE has been doing.
"""
from __future__ import annotations

import datetime

import customtkinter as ctk
from gui import theme
from gui.store import store


def _journal_item(entry):
    """One task_journal record in the shape the timeline renders."""
    outcome = str(entry.get("outcome", "completed"))
    tools = entry.get("tools_run") or []
    bits = [outcome]
    if tools:
        shown = ", ".join(tools[:4])
        extra = f" +{len(tools) - 4} more" if len(tools) > 4 else ""
        bits.append(f"{len(tools)} tool{'s' if len(tools) != 1 else ''}: {shown}{extra}")
    if entry.get("step_count"):
        bits.append(f"{entry['step_count']} steps")
    if entry.get("reason"):
        bits.append(str(entry["reason"]))
    ts = entry.get("ts", 0) or 0
    try:
        clock = datetime.datetime.fromtimestamp(ts).strftime("%H:%M")
    except (OSError, OverflowError, ValueError):
        clock = "--:--"
    return {
        "time": clock,
        "title": (str(entry.get("request") or "Untitled"))[:80],
        "detail": " · ".join(bits),
        "category": "Journal",
        "tone": theme.SUCCESS if outcome == "completed" else theme.DANGER,
    }


def journal_items(limit=50):
    """Task journal entries for the timeline, newest first. Never raises."""
    try:
        from assistant import task_journal
        return [_journal_item(e) for e in task_journal.recent_entries(limit)]
    except Exception:
        return []


class ActivityPage(ctk.CTkScrollableFrame):
    def __init__(self, parent):
        super().__init__(
            parent,
            fg_color="transparent",
            scrollbar_button_color=theme.CARD_BORDER,
        )
        self.selected_filter = "All"
        self.filter_buttons = {}

        # 1. Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 12))

        ctk.CTkLabel(
            header,
            text="What VAVE has been doing",
            font=theme.font(20, "bold"),
            text_color=theme.TEXT_PRIMARY,
            anchor="w",
        ).pack(anchor="w")

        ctk.CTkLabel(
            header,
            text="A transparent timeline of actions taken on your devices and in the cloud.",
            font=theme.font(12),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        # 2. Filters
        filters_row = ctk.CTkFrame(self, fg_color="transparent")
        filters_row.pack(fill="x", padx=16, pady=(0, 14))

        filters = ["All", "Files", "Google", "Web", "Tasks", "Journal"]
        for f in filters:
            btn = ctk.CTkButton(
                filters_row,
                text=f,
                font=theme.font(11, "bold" if f == "All" else "normal"),
                fg_color=theme.ACCENT if f == "All" else theme.CARD_BG,
                hover_color=theme.ACCENT_HOVER if f == "All" else theme.CARD_HOVER,
                text_color=theme.ON_ACCENT if f == "All" else theme.TEXT_SECONDARY,
                border_width=0 if f == "All" else 1,
                border_color=theme.CARD_BORDER,
                corner_radius=theme.RADIUS_SM,
                height=30,
                command=lambda cat=f: self._set_filter(cat),
            )
            btn.pack(side="left", padx=3)
            self.filter_buttons[f] = btn

        # 3. Timeline Container
        self.timeline_container = ctk.CTkFrame(self, fg_color="transparent")
        self.timeline_container.pack(fill="x", padx=16, pady=(0, 20))

        self._render_timeline()

    def _set_filter(self, category: str):
        self.selected_filter = category
        for cat, btn in self.filter_buttons.items():
            if cat == category:
                btn.configure(
                    fg_color=theme.ACCENT,
                    text_color=theme.ON_ACCENT,
                    border_width=0,
                    font=theme.font(11, "bold"),
                )
            else:
                btn.configure(
                    fg_color=theme.CARD_BG,
                    text_color=theme.TEXT_SECONDARY,
                    border_width=1,
                    font=theme.font(11, "normal"),
                )
        self._render_timeline()

    def _render_timeline(self):
        for child in self.timeline_container.winfo_children():
            child.destroy()

        logs = store.activity_logs
        if self.selected_filter == "Journal":
            # The journal is not in the GUI store; it is read live from the
            # audit ledger. Tail-reading a rotated log is fast enough here.
            logs = journal_items()
        elif self.selected_filter != "All":
            logs = [item for item in logs if item.get("category") == self.selected_filter]

        for item in logs:
            card = ctk.CTkFrame(
                self.timeline_container,
                fg_color=theme.CARD_BG,
                border_width=1,
                border_color=theme.CARD_BORDER,
                corner_radius=theme.RADIUS,
            )
            card.pack(fill="x", pady=4)

            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=12)

            time_box = ctk.CTkFrame(row, fg_color=theme.MAIN_BG, corner_radius=8, width=74, height=36)
            time_box.pack(side="left", padx=(0, 12))
            time_box.pack_propagate(False)
            ctk.CTkLabel(time_box, text=item["time"], font=theme.font(10, "bold"), text_color=theme.TEXT_PRIMARY).pack(expand=True)

            col = ctk.CTkFrame(row, fg_color="transparent")
            col.pack(side="left", fill="both", expand=True)

            title_row = ctk.CTkFrame(col, fg_color="transparent")
            title_row.pack(fill="x", anchor="w")

            ctk.CTkLabel(title_row, text=item["title"], font=theme.font(12, "bold"), text_color=theme.TEXT_PRIMARY).pack(side="left")

            badge = ctk.CTkLabel(
                title_row,
                text=item["category"],
                font=theme.font(9, "bold"),
                text_color=item.get("tone", theme.TEXT_MUTED),
                fg_color=theme.MAIN_BG,
                corner_radius=6,
                padx=6,
                pady=2,
            )
            badge.pack(side="left", padx=8)

            ctk.CTkLabel(col, text=item["detail"], font=theme.font(11), text_color=theme.TEXT_SECONDARY, wraplength=450, justify="left", anchor="w").pack(anchor="w", pady=(2, 0))

            view_btn = ctk.CTkButton(
                row,
                text="Details ›",
                font=theme.font(11),
                fg_color="transparent",
                hover_color=theme.MAIN_BG,
                text_color=theme.ACCENT_DEEP,
                width=60,
                command=lambda act=item: store.open_drawer("activity", act),
            )
            view_btn.pack(side="right")

            card.bind("<Button-1>", lambda e, act=item: store.open_drawer("activity", act))
            col.bind("<Button-1>", lambda e, act=item: store.open_drawer("activity", act))
