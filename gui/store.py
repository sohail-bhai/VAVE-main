"""
Central state and mock data store for VAVE desktop interface.
Maintains data for pages, system log, drawer details, and the core demo flow.
"""
from __future__ import annotations

import datetime
import logging
import threading
import time
from typing import Callable, Any, Dict, List, Optional

from gui.redaction import redact

logger = logging.getLogger(__name__)


class AppStore:
    def __init__(self):
        self._listeners: List[Callable[[str, Any], None]] = []
        
        # Navigation
        self.current_page = "home"
        self.active_drawer: Optional[Dict[str, Any]] = None  # {"type": "device"|"file"|"task"|"activity"|"approval", "data": ...}
        
        # VAVE Status
        self.vave_online = True
        self.vave_status_text = "All systems running"
        self.voice_listening = False
        
        # System Log Entries
        # Each: {"time": "3:24 PM", "text": "...", "status": "working"|"completed"|"waiting"|"approval"|"info"}
        self.system_logs: List[Dict[str, str]] = [
            {"time": "3:24 PM", "text": "Searching your files for 'Hackwave presentation'", "status": "working"},
            {"time": "3:24 PM", "text": "Found 3 documents on your computer", "status": "working"},
            {"time": "3:25 PM", "text": "Also checking Google Drive", "status": "working"},
            {"time": "3:25 PM", "text": "Downloaded the latest version", "status": "completed"},
            {"time": "3:26 PM", "text": "Researching latest competitors online", "status": "working"},
            {"time": "3:27 PM", "text": "Reading and summarizing information", "status": "working"},
            {"time": "3:28 PM", "text": "Writing a draft for your presentation", "status": "waiting"},
            {"time": "3:28 PM", "text": "Waiting for next step...", "status": "info"},
        ]
        
        # Current Task
        self.current_task: Dict[str, Any] = {
            "id": "task-project",
            "title": "Preparing your project",
            "subtitle": "VAVE is researching, organizing files and writing a draft for you.",
            "status": "working",
            "steps": [
                {"id": 1, "text": "Finding relevant files", "status": "done"},
                {"id": 2, "text": "Researching on the web", "status": "active"},
                {"id": 3, "text": "Writing draft", "status": "pending"},
                {"id": 4, "text": "Finalizing", "status": "pending"},
            ],
            "helper": "Handled by VAVE (Research & Writing Helper)",
            "details": "Searched local drive for project presentation, scraped web for 3 references, compiling summary into Google Slides."
        }
        
        # Notifications
        self.notifications: List[Dict[str, Any]] = [
            {
                "id": "notif-1",
                "title": "Needs your approval",
                "message": "VAVE is ready to submit your project changes.",
                "time": "5m ago",
                "type": "approval",
                "unread": True,
            },
            {
                "id": "notif-2",
                "title": "Task completed",
                "message": "Your market research summary is ready.",
                "time": "25m ago",
                "type": "task",
                "unread": True,
            },
            {
                "id": "notif-3",
                "title": "Device connected",
                "message": "Your phone is now connected via secure sync.",
                "time": "1h ago",
                "type": "device",
                "unread": False,
            }
        ]
        
        # Connected Environment
        self.environment = [
            {"id": "comp", "name": "My Computer", "status": "Online", "color": "blue", "icon": "monitor", "route": "devices"},
            {"id": "phone", "name": "My Phone", "status": "Connected", "color": "green", "icon": "phone", "route": "devices"},
            {"id": "drive", "name": "Google Drive", "status": "Connected", "color": "amber", "icon": "folder", "route": "google"},
            {"id": "net", "name": "Internet", "status": "Ready", "color": "blue", "icon": "globe", "route": "web"},
        ]
        
        # Devices Data
        self.devices: List[Dict[str, Any]] = [
            {
                "id": "dev-1",
                "name": "My Computer",
                "type": "Primary Desktop",
                "status": "Online",
                "icon": "monitor",
                "capabilities": ["Access your files", "Run local tasks", "Use your browser"],
                "connected_helpers": ["VAVE Core", "Coding Assistant"],
                "recent_activity": "Completed project tests & file indexing",
                "ip": "192.168.1.104",
                "os": "macOS / Windows 11",
            },
            {
                "id": "dev-2",
                "name": "My Phone",
                "type": "Mobile Device",
                "status": "Connected",
                "icon": "phone",
                "capabilities": ["Send and receive files", "Read phone notifications", "Execute remote quick tasks"],
                "connected_helpers": ["Phone Notification Sync", "Telegram Connector"],
                "recent_activity": "Synced 2 notifications at 2:15 PM",
                "ip": "192.168.1.112",
                "os": "iOS / Android",
            },
            {
                "id": "dev-3",
                "name": "My Laptop",
                "type": "Work Laptop",
                "status": "Online",
                "icon": "monitor",
                "capabilities": ["Sync project folders", "Run light test suites", "Read screen context"],
                "connected_helpers": ["Workspace Mirror"],
                "recent_activity": "Synchronized 'Hackwave' repo 40m ago",
                "ip": "192.168.1.140",
                "os": "macOS Sonoma",
            },
            {
                "id": "dev-4",
                "name": "My Server",
                "type": "GPU Workstation",
                "status": "Online",
                "icon": "monitor",
                "capabilities": ["Run heavy tasks", "Train local models", "Host background builds"],
                "connected_helpers": ["Local LLM Ollama Worker"],
                "recent_activity": "Processed embeddings for memory store",
                "ip": "192.168.1.200",
                "os": "Ubuntu Linux",
            },
        ]
        
        # Files Data
        self.files: List[Dict[str, Any]] = [
            {
                "id": "f-1",
                "name": "Hackwave_Final.pptx",
                "source": "Computer",
                "folder": "Projects / Hackwave",
                "modified": "Yesterday",
                "size": "12 MB",
                "type": "presentation",
                "icon": "slides",
            },
            {
                "id": "f-2",
                "name": "Architecture_Overview.pdf",
                "source": "Google Drive",
                "folder": "Drive / Hackwave Docs",
                "modified": "Aug 31",
                "size": "4.2 MB",
                "type": "pdf",
                "icon": "document",
            },
            {
                "id": "f-3",
                "name": "Project_Budget_2025.xlsx",
                "source": "Google Drive",
                "folder": "Drive / Finance",
                "modified": "Sep 01",
                "size": "850 KB",
                "type": "spreadsheet",
                "icon": "sheet",
            },
            {
                "id": "f-4",
                "name": "Cleaned_Dataset.csv",
                "source": "Server",
                "folder": "Server / Data / Raw",
                "modified": "2 days ago",
                "size": "45 MB",
                "type": "data",
                "icon": "sheet",
            },
            {
                "id": "f-5",
                "name": "Meeting_Notes_Sept.txt",
                "source": "Computer",
                "folder": "Documents / Notes",
                "modified": "Today",
                "size": "15 KB",
                "type": "text",
                "icon": "document",
            },
            {
                "id": "f-6",
                "name": "Demo_Walkthrough.mp4",
                "source": "Phone",
                "folder": "Media / Videos",
                "modified": "3 days ago",
                "size": "78 MB",
                "type": "video",
                "icon": "file",
            },
        ]
        
        # Google Services Data
        self.google = {
            "status": "Connected",
            "account": "user@gmail.com",
            "overview": {
                "drive_files": "2,481 files",
                "unread_emails": "12 unread",
                "calendar_events": "3 events today",
                "recent_docs": "18 files",
            },
            "drive_items": [
                {"name": "Hackwave Architecture", "type": "Document", "date": "Updated 2h ago", "icon": "document"},
                {"name": "Hackwave Final Presentation", "type": "Presentation", "date": "Updated yesterday", "icon": "slides"},
                {"name": "Project Budget", "type": "Spreadsheet", "date": "Updated Sep 01", "icon": "sheet"},
                {"name": "College Submission Guidelines", "type": "PDF", "date": "Updated Aug 28", "icon": "file"},
            ],
            "emails": [
                {"sender": "Hackathon Team", "subject": "Final submission details & booth setup", "time": "11:20 AM", "unread": True},
                {"sender": "Google Cloud", "subject": "Your promotional credits are ready for use", "time": "9:45 AM", "unread": True},
                {"sender": "College Administration", "subject": "Important notification regarding seminar", "time": "Yesterday", "unread": False},
                {"sender": "GitHub Notifications", "subject": "Pull request merged: feat(ui): desktop frontend", "time": "Sep 03", "unread": False},
            ],
            "calendar": [
                {"time": "10:00 AM", "title": "Class: Distributed Systems Lecture", "duration": "1h 30m", "tag": "Academic"},
                {"time": "02:00 PM", "title": "Hackwave Hackathon Project Check-in", "duration": "1h", "tag": "Project"},
                {"time": "05:30 PM", "title": "Team Sync & Demo Review", "duration": "45m", "tag": "Meeting"},
            ],
            "docs": [
                {"title": "Hackwave Presentation", "app": "Google Slides", "desc": "Final pitch deck slides"},
                {"title": "System Design Spec", "app": "Google Docs", "desc": "Architecture & API contract notes"},
                {"title": "Expense Tracker", "app": "Google Sheets", "desc": "Monthly project costs & ledger"},
            ]
        }
        
        # Web Tasks Data
        self.web_tasks: List[Dict[str, Any]] = [
            {"id": "w-1", "query": "Researching React 20 & Vite performance", "status": "Working", "time": "Just now"},
            {"id": "w-2", "query": "Find GPU prices and cloud availability", "status": "Completed", "time": "1h ago"},
            {"id": "w-3", "query": "Research hackathon requirements and rubric", "status": "Completed", "time": "3h ago"},
            {"id": "w-4", "query": "Compare local TTS models (Edge vs Piper)", "status": "Completed", "time": "Yesterday"},
        ]
        
        # Web Browser Stepper
        self.browser_progress = {
            "title": "Finding the information you requested",
            "steps": [
                {"text": "Opened the website", "done": True},
                {"text": "Found the relevant page", "done": True},
                {"text": "Read the information", "done": True},
                {"text": "Comparing results", "active": True},
                {"text": "Preparing answer", "done": False},
            ],
            "needs_review": False
        }
        
        # Activity Timeline Data
        self.activity_logs: List[Dict[str, Any]] = [
            {
                "id": "act-1",
                "time": "12:42 PM",
                "category": "Tasks",
                "title": "Finished checking your project",
                "detail": "VAVE searched your computer for your Hackwave presentation, verified dependencies, and passed all tests.",
                "used": "Your Computer · Google Drive",
                "found": "3 files verified",
                "reason": "You asked VAVE to continue the project.",
            },
            {
                "id": "act-2",
                "time": "12:39 PM",
                "category": "Files",
                "title": "Opened your Hackwave presentation",
                "detail": "Retrieved Hackwave_Final.pptx from local drive and synced latest changes.",
                "used": "My Computer",
                "found": "Hackwave_Final.pptx (12 MB)",
                "reason": "You requested to inspect the pitch deck.",
            },
            {
                "id": "act-3",
                "time": "12:35 PM",
                "category": "Tasks",
                "title": "You approved an action",
                "detail": "Approved merging 14 changed files into the main project branch.",
                "used": "Local Repository",
                "found": "14 files committed",
                "reason": "Confirmation gate for live changes.",
            },
            {
                "id": "act-4",
                "time": "12:31 PM",
                "category": "Web",
                "title": "Started researching your topic",
                "detail": "Searched technical documentation for benchmark metrics.",
                "used": "Internet Web Assistant",
                "found": "4 relevant documentation articles",
                "reason": "Automated background research.",
            },
            {
                "id": "act-5",
                "time": "12:28 PM",
                "category": "Google",
                "title": "Checked your Google Drive",
                "detail": "Found matching design specifications and downloaded latest revisions.",
                "used": "Google Drive",
                "found": "2 documents",
                "reason": "Synced external cloud materials.",
            },
        ]
        
        # Memory / What VAVE Remembers
        self.memories: List[Dict[str, str]] = [
            {"id": "m-1", "fact": "You are working on the Hackwave project.", "source": "Your previous task"},
            {"id": "m-2", "fact": "You prefer concise bullet points and non-technical language.", "source": "Preference setting"},
            {"id": "m-3", "fact": "Your primary phone is connected via local network sync.", "source": "Device setup"},
            {"id": "m-4", "fact": "Class schedule: Distributed Systems every Thursday morning.", "source": "Calendar"},
        ]
        
        # Pending Approval State
        self.pending_approval: Optional[Dict[str, Any]] = None
        
        # AI Helpers
        self.ai_helpers = [
            {"name": "Coding Assistant", "status": "Working", "description": "Handles local code updates and project verification."},
            {"name": "Research Assistant", "status": "Ready", "description": "Gathers information from documents and web articles."},
            {"name": "VAVE Web Assistant", "status": "Ready", "description": "Navigates websites and reviews online data safely."},
        ]

    def subscribe(self, callback: Callable[[str, Any], None]):
        """Subscribe to store state updates."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def unsubscribe(self, callback: Callable[[str, Any], None]):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify(self, event: str, data: Any = None):
        for cb in list(self._listeners):
            try:
                cb(event, data)
            except Exception:
                pass

    def set_page(self, page_name: str):
        self.current_page = page_name
        self._notify("page_changed", page_name)

    def open_drawer(self, drawer_type: str, data: Any):
        self.active_drawer = {"type": drawer_type, "data": data}
        self._notify("drawer_opened", self.active_drawer)

    def close_drawer(self):
        self.active_drawer = None
        self._notify("drawer_closed", None)

    # A long session used to keep every line ever written, and the panel
    # rebuilt all of them on each new one. Older lines live in the Activity
    # timeline, so the live panel only needs the recent tail.
    MAX_SYSTEM_LOGS = 120

    def add_system_log(self, text: str, status: str = "working"):
        # Every log line passes through here, so this is the one place a
        # credential has to be stripped before it can reach the screen.
        now_str = datetime.datetime.now().strftime("%I:%M %p").lstrip("0")
        entry = {"time": now_str, "text": redact(text), "status": status}
        self.system_logs.append(entry)
        dropped = False
        while len(self.system_logs) > self.MAX_SYSTEM_LOGS:
            self.system_logs.pop(0)
            dropped = True
        self._notify("system_log_added", {"entry": entry, "dropped": dropped})

    def trigger_approval(self, title: str, description: str, on_approve: Callable[[], None], on_reject: Optional[Callable[[], None]] = None):
        self.pending_approval = {
            "title": title,
            "description": description,
            "on_approve": on_approve,
            "on_reject": on_reject,
        }
        self._notify("approval_requested", self.pending_approval)

    def resolve_approval(self, approved: bool):
        if not self.pending_approval:
            return
        approval = self.pending_approval
        self.pending_approval = None
        
        if approved:
            self.add_system_log("You approved the pending action", "completed")
            if approval.get("on_approve"):
                approval["on_approve"]()
        else:
            self.add_system_log("Action postponed by user", "waiting")
            if approval.get("on_reject"):
                approval["on_reject"]()
                
        self._notify("approval_resolved", approved)

    def refresh_devices(self):
        """Fetch live devices from the ControlStore if available."""
        try:
            from assistant.control.service import get_control_plane
            plane = get_control_plane()
            real_devices = plane.list_devices()
            if real_devices:
                live_list = []
                for d in real_devices:
                    icon = "phone" if "phone" in (d.kind or "").lower() else "monitor"
                    live_list.append({
                        "id": d.id,
                        "name": d.name,
                        "type": d.kind.title(),
                        "status": d.status.value.title(),
                        "icon": icon,
                        "capabilities": d.capabilities or ["Access your files", "Run local tasks"],
                        "token_expires_at": getattr(d, "token_expires_at", 0.0),
                        "is_expired": getattr(d, "is_expired", False),
                        "platform": d.platform,
                        "token_hash": bool(d.token_hash),
                    })
                # Preserve default local computer entry if not returned
                has_comp = any("comp" in str(x.get("id", "")) for x in live_list)
                if not has_comp and self.devices:
                    live_list.insert(0, self.devices[0])
                self.devices = live_list
                self._notify("devices_updated", self.devices)
        except Exception as e:
            logger.debug(f"Could not refresh devices from control plane: {e}")

    def rotate_device_token(self, device_id: str) -> Optional[str]:
        """Rotate token for a paired device directly from GUI."""
        try:
            import secrets
            from assistant.api.auth import hash_token
            from assistant.config import get_setting
            from assistant.control.models import DeviceStatus, now
            from assistant.control.service import get_control_plane

            plane = get_control_plane()
            dev = plane.store.get_device(device_id)
            if not dev:
                return None
            if not dev.is_paired:
                return None

            ttl = get_setting("device_token_ttl_seconds", 30 * 86400)
            new_expires_at = (now() + ttl) if (ttl and ttl > 0) else 0.0
            new_token = secrets.token_urlsafe(32)
            dev.token_hash = hash_token(new_token)
            dev.token_expires_at = new_expires_at
            dev.status = DeviceStatus.ONLINE
            dev.last_seen = now()
            plane.store.save_device(dev)

            self.add_system_log(f"Rotated access token for device: {dev.name}", "completed")
            self.refresh_devices()
            return new_token
        except Exception as e:
            logger.warning(f"Failed to rotate device token: {e}")
            self.add_system_log(f"Token rotation failed: {e}", "waiting")
            return None

    def forget_memory(self, memory_id: str):
        self.memories = [m for m in self.memories if m["id"] != memory_id]
        self._notify("memory_updated", self.memories)

    def run_hackwave_demo(self, on_complete: Optional[Callable[[], None]] = None):
        """
        Executes the Core Demo Flow specified in requirement #44:
        1. Updates task to 'Preparing your project'
        2. Logs each step into the System Log
        3. Prompts Approval Card
        4. When approved, completes task and updates logs
        """
        def _worker():
            # Step 1
            self.current_task["title"] = "Preparing your project"
            self.current_task["subtitle"] = "VAVE is researching your files and the web and preparing everything for you."
            self.current_task["steps"] = [
                {"id": 1, "text": "Finding relevant files", "status": "active"},
                {"id": 2, "text": "Researching on the web", "status": "pending"},
                {"id": 3, "text": "Preparing the document", "status": "pending"},
                {"id": 4, "text": "Finishing", "status": "pending"},
            ]
            self._notify("task_updated", self.current_task)
            self.add_system_log("Found your project on your computer", "working")
            time.sleep(1.2)

            # Step 2
            self.current_task["steps"][0]["status"] = "done"
            self.current_task["steps"][1]["status"] = "active"
            self._notify("task_updated", self.current_task)
            self.add_system_log("Found the latest project files", "working")
            time.sleep(1.0)
            self.add_system_log("Checking your Google Drive too", "working")
            time.sleep(1.2)

            # Step 3
            self.current_task["steps"][1]["status"] = "done"
            self.current_task["steps"][2]["status"] = "active"
            self._notify("task_updated", self.current_task)
            self.add_system_log("Researching the latest information online", "working")
            time.sleep(1.2)
            self.add_system_log("Reading and summarizing the results", "working")
            time.sleep(1.0)

            # Trigger Approval
            def _on_approved():
                self.current_task["steps"][2]["status"] = "done"
                self.current_task["steps"][3]["status"] = "done"
                self.current_task["title"] = "Project updated"
                self.current_task["subtitle"] = "Your presentation and research materials are ready."
                self.current_task["status"] = "completed"
                self._notify("task_updated", self.current_task)
                self.add_system_log("Changes merged successfully. All tests passed.", "completed")
                self.add_system_log("Finished preparing presentation", "completed")
                if on_complete:
                    on_complete()

            self.trigger_approval(
                title="VAVE needs your approval",
                description="I'm ready to merge your project changes.\n\n14 files were changed.\nAll tests passed.\nThis will make the changes live.",
                on_approve=_on_approved
            )

        t = threading.Thread(target=_worker, daemon=True)
        t.start()


# Global Singleton Store
store = AppStore()
