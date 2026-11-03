"""
Projects & Self-Evolution Page component.
Autonomous Project Manager control, tool orchestration, and capability gap reviewer.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict

import customtkinter as ctk
from gui import theme, ui_queue

logger = logging.getLogger(__name__)


class ProjectsPage(ctk.CTkScrollableFrame):
    def __init__(self, parent):
        super().__init__(
            parent,
            fg_color="transparent",
            scrollbar_button_color=theme.CARD_BORDER,
        )
        self._running_project = False
        self._running_gap_ids = set()
        self._running_gap_batch = False

        # 1. Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 12))

        ctk.CTkLabel(
            header,
            text="Projects & Self-Evolution",
            font=theme.font(20, "bold"),
            text_color=theme.TEXT_PRIMARY,
            anchor="w",
        ).pack(anchor="w")

        ctk.CTkLabel(
            header,
            text="Autonomous multi-tool project manager (Antigravity, OpenCode, Swarm, OpenClaw) & self-evolving capability system.",
            font=theme.font(12),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        # 2. Segmented Mode Switcher
        self.mode_var = ctk.StringVar(value="Manager Mode")
        self.seg_button = ctk.CTkSegmentedButton(
            self,
            values=["Manager Mode", "Capability Gaps & Evolution"],
            variable=self.mode_var,
            command=self._on_mode_change,
            font=theme.font(12, "bold"),
            selected_color=theme.ACCENT,
            selected_hover_color=theme.ACCENT_HOVER,
        )
        self.seg_button.pack(fill="x", padx=16, pady=(0, 16))

        # 3. Dynamic Content Containers
        self.manager_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.gaps_frame = ctk.CTkFrame(self, fg_color="transparent")

        self._build_manager_view()
        self._build_gaps_view()

        # Mount default view
        self.manager_frame.pack(fill="both", expand=True, padx=16)

    def _on_mode_change(self, value: str):
        if value == "Manager Mode":
            self.gaps_frame.pack_forget()
            self.manager_frame.pack(fill="both", expand=True, padx=16)
        else:
            self.manager_frame.pack_forget()
            self._refresh_gaps()
            self.gaps_frame.pack(fill="both", expand=True, padx=16)

    # -------------------------------------------------------------------------
    # Manager View
    # -------------------------------------------------------------------------
    def _build_manager_view(self):
        card = ctk.CTkFrame(
            self.manager_frame,
            fg_color=theme.CARD_BG,
            border_color=theme.CARD_BORDER,
            border_width=1,
            corner_radius=10,
        )
        card.pack(fill="x", pady=(0, 14))

        ctk.CTkLabel(
            card,
            text="New Managed Project",
            font=theme.font(14, "bold"),
            text_color=theme.TEXT_PRIMARY,
        ).pack(anchor="w", padx=16, pady=(14, 8))

        ctk.CTkLabel(
            card,
            text="Goal Specification",
            font=theme.font(11, "bold"),
            text_color=theme.TEXT_MUTED,
        ).pack(anchor="w", padx=16, pady=(0, 4))

        self.goal_entry = ctk.CTkEntry(
            card,
            placeholder_text="e.g. Build a FastAPI app with user auth and pytest test suite...",
            height=36,
            font=theme.font(12),
        )
        self.goal_entry.pack(fill="x", padx=16, pady=(0, 10))

        # Controls row
        ctrl_row = ctk.CTkFrame(card, fg_color="transparent")
        ctrl_row.pack(fill="x", padx=16, pady=(0, 14))

        ctk.CTkLabel(ctrl_row, text="Max Cycles: ", font=theme.font(11)).pack(side="left")
        self.cycles_combo = ctk.CTkComboBox(
            ctrl_row,
            values=["1", "2", "3", "5"],
            width=70,
            height=28,
            font=theme.font(11),
        )
        self.cycles_combo.set("3")
        self.cycles_combo.pack(side="left", padx=(0, 16))

        self.launch_btn = ctk.CTkButton(
            ctrl_row,
            text="🚀 Launch Project",
            font=theme.font(12, "bold"),
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER,
            command=self._launch_project,
        )
        self.launch_btn.pack(side="right")

        # Results & Output Card
        output_card = ctk.CTkFrame(
            self.manager_frame,
            fg_color=theme.CARD_BG,
            border_color=theme.CARD_BORDER,
            border_width=1,
            corner_radius=10,
        )
        output_card.pack(fill="both", expand=True, pady=(0, 14))

        top_out = ctk.CTkFrame(output_card, fg_color="transparent")
        top_out.pack(fill="x", padx=16, pady=(14, 8))

        ctk.CTkLabel(
            top_out,
            text="Manager Execution Console",
            font=theme.font(13, "bold"),
            text_color=theme.TEXT_PRIMARY,
        ).pack(side="left")

        self.status_badge = ctk.CTkLabel(
            top_out,
            text="IDLE",
            font=theme.font(11, "bold"),
            text_color=theme.TEXT_MUTED,
        )
        self.status_badge.pack(side="right")

        self.console_box = ctk.CTkTextbox(
            output_card,
            height=260,
            font=("Consolas", 11),
            fg_color="#0F1117",
            text_color="#E0E6ED",
        )
        self.console_box.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.console_box.insert(
            "end",
            "Project Manager online.\nReady to synthesize specifications, orchestrate tools, and review results.\n"
        )
        self.console_box.configure(state="disabled")

    def _launch_project(self):
        if self._running_project or self._running_gap_ids or self._running_gap_batch:
            return

        goal = self.goal_entry.get().strip()
        if not goal:
            return

        try:
            max_loops = int(self.cycles_combo.get())
        except ValueError:
            max_loops = 3

        self._running_project = True
        self.launch_btn.configure(state="disabled", text="⏳ Running...")
        self.status_badge.configure(text="SYNTHESIZING SPEC...", text_color=theme.WARNING)

        self._append_console(f"\n=========================================\nStarting Project: {goal}\nMax Loops: {max_loops}\n=========================================\n")

        def _worker():
            try:
                from assistant.project_manager import ProjectManager
                pm = ProjectManager()
                result = pm.run_managed_project(goal=goal, max_loops=max_loops)
                ui_queue.post_to(self, lambda: self._on_project_done(result))
            except Exception as exc:
                logger.error("Project run failed: %s", exc)
                error_text = str(exc)
                ui_queue.post_to(
                    self,
                    lambda message=error_text: self._on_project_error(message),
                )

        threading.Thread(target=_worker, daemon=True).start()

    def _on_project_done(self, result: Dict[str, Any]):
        self._running_project = False
        self.launch_btn.configure(state="normal", text="🚀 Launch Project")

        ok = result.get("ok", False)
        if result.get("dry_run"):
            status_text = "REHEARSAL"
            status_color = theme.WARNING
        else:
            status_text = "APPROVED" if ok else "NEEDS ATTENTION"
            status_color = theme.SUCCESS if ok else theme.DANGER
        self.status_badge.configure(text=status_text, text_color=status_color)

        summary = result.get("summary", "")
        cycles = result.get("cycles_run", 1)
        spec = result.get("spec", {})

        self._append_console(f"\nCompleted in {cycles} cycle(s).\nResult: {summary}\n\nVision Spec Summary: {spec.get('summary', '')}\nCriteria: {json.dumps(spec.get('acceptance_criteria', []), indent=2)}\n")

    def _on_project_error(self, err_msg: str):
        self._running_project = False
        self.launch_btn.configure(state="normal", text="🚀 Launch Project")
        self.status_badge.configure(text="ERROR", text_color=theme.DANGER)
        self._append_console(f"\nERROR: {err_msg}\n")

    def _append_console(self, text: str):
        self.console_box.configure(state="normal")
        self.console_box.insert("end", text)
        self.console_box.see("end")
        self.console_box.configure(state="disabled")

    # -------------------------------------------------------------------------
    # Capability Gaps & Self-Evolution View
    # -------------------------------------------------------------------------
    def _build_gaps_view(self):
        top_bar = ctk.CTkFrame(self.gaps_frame, fg_color="transparent")
        top_bar.pack(fill="x", pady=(0, 12))

        ctk.CTkButton(
            top_bar,
            text="🔄 Refresh Gaps",
            font=theme.font(11, "bold"),
            width=110,
            command=self._refresh_gaps,
        ).pack(side="left")

        ctk.CTkButton(
            top_bar,
            text="⚡ Implement All Approved",
            font=theme.font(11, "bold"),
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER,
            command=self._implement_all,
        ).pack(side="right")

        self.gaps_list_container = ctk.CTkFrame(self.gaps_frame, fg_color="transparent")
        self.gaps_list_container.pack(fill="both", expand=True)

    def _refresh_gaps(self):
        for widget in self.gaps_list_container.winfo_children():
            widget.destroy()

        from assistant.self_evolution import list_capability_gaps
        gaps = list_capability_gaps()

        if not gaps:
            empty_card = ctk.CTkFrame(
                self.gaps_list_container,
                fg_color=theme.CARD_BG,
                border_color=theme.CARD_BORDER,
                border_width=1,
                corner_radius=10,
            )
            empty_card.pack(fill="x", pady=10)
            ctk.CTkLabel(
                empty_card,
                text="🎉 No Capability Gaps Recorded!\nVAVE is operating with full tool coverage.",
                font=theme.font(13),
                text_color=theme.TEXT_SECONDARY,
                justify="center",
            ).pack(padx=20, pady=30)
            return

        for gap in reversed(gaps):
            card = ctk.CTkFrame(
                self.gaps_list_container,
                fg_color=theme.CARD_BG,
                border_color=theme.CARD_BORDER,
                border_width=1,
                corner_radius=10,
            )
            card.pack(fill="x", pady=(0, 10))

            header_row = ctk.CTkFrame(card, fg_color="transparent")
            header_row.pack(fill="x", padx=16, pady=(12, 6))

            ctk.CTkLabel(
                header_row,
                text=f"{gap.missing_capability} ({gap.id})",
                font=theme.font(13, "bold"),
                text_color=theme.TEXT_PRIMARY,
            ).pack(side="left")

            status_color = (
                theme.SUCCESS if gap.status == "implemented"
                else theme.WARNING if gap.status in ("pending", "approved")
                else theme.DANGER
            )
            ctk.CTkLabel(
                header_row,
                text=gap.status.upper(),
                font=theme.font(11, "bold"),
                text_color=status_color,
            ).pack(side="right")

            # Details
            details_text = f"• Task: {gap.task_description}\n• Limitation: {gap.reason}\n• Blueprint: {gap.proposed_solution}"
            if gap.implementation_log:
                details_text += f"\n• Log: {gap.implementation_log}"

            ctk.CTkLabel(
                card,
                text=details_text,
                font=theme.font(11),
                text_color=theme.TEXT_SECONDARY,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=16, pady=(0, 10))

            if gap.status in ("pending", "approved", "failed"):
                btn_row = ctk.CTkFrame(card, fg_color="transparent")
                btn_row.pack(fill="x", padx=16, pady=(0, 12))

                ctk.CTkButton(
                    btn_row,
                    text=(
                        "Implement Approved"
                        if gap.status == "approved"
                        else "Retry & Implement"
                        if gap.status == "failed"
                        else "Approve & Implement"
                    ),
                    font=theme.font(11, "bold"),
                    fg_color=theme.ACCENT,
                    hover_color=theme.ACCENT_HOVER,
                    height=28,
                    command=lambda g_id=gap.id: self._trigger_implement_gap(g_id),
                ).pack(side="left", padx=(0, 8))

                ctk.CTkButton(
                    btn_row,
                    text="Dismiss",
                    font=theme.font(11),
                    height=28,
                    width=80,
                    command=lambda g_id=gap.id: self._trigger_reject_gap(g_id),
                ).pack(side="left")

    def _trigger_implement_gap(self, gap_id: str):
        if self._running_project or self._running_gap_batch or self._running_gap_ids:
            return

        from assistant.self_evolution import approve_gap, get_capability_gap

        gap = get_capability_gap(gap_id)
        if not gap:
            self._refresh_gaps()
            return
        from assistant import guard
        if guard.dry_run_active():
            from assistant.self_evolution import preview_capability

            preview = preview_capability(gap_id)
            logger.info("%s", preview.get("message", preview.get("error", "")))
            self._refresh_gaps()
            return
        if gap.status != "approved":
            approved = approve_gap(gap_id)
            if not approved.get("ok"):
                self._refresh_gaps()
                return

        self._running_gap_ids.add(gap_id)
        self._refresh_gaps()

        def _bg_impl():
            try:
                from assistant.self_evolution import implement_capability

                implement_capability(gap_id)
            finally:
                ui_queue.post_to(
                    self,
                    lambda: self._finish_gap_job(gap_id),
                )

        threading.Thread(target=_bg_impl, daemon=True).start()

    def _finish_gap_job(self, gap_id: str):
        self._running_gap_ids.discard(gap_id)
        self._refresh_gaps()

    def _trigger_reject_gap(self, gap_id: str):
        from assistant import guard
        if guard.dry_run_active():
            logger.info(
                "DRY RUN: would reject capability gap %s. Registry unchanged.",
                gap_id,
            )
            return
        from assistant.self_evolution import reject_gap
        reject_gap(gap_id)
        self._refresh_gaps()

    def _implement_all(self):
        if self._running_project or self._running_gap_batch or self._running_gap_ids:
            return
        self._running_gap_batch = True

        def _bg_impl_all():
            try:
                from assistant.self_evolution import implement_all_approved_gaps

                implement_all_approved_gaps()
            finally:
                ui_queue.post_to(self, self._finish_gap_batch)

        threading.Thread(target=_bg_impl_all, daemon=True).start()

    def _finish_gap_batch(self):
        self._running_gap_batch = False
        self._refresh_gaps()
