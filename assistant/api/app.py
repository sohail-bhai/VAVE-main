"""HTTP and WebSocket boundary for the VAVE control plane.

This is the seam between the VAVE core and any client: the desktop app on
Windows, macOS or Linux, and the mobile interface. Clients hold no logic of
their own - they read state here and post user decisions back, so every client
sees the same tasks, approvals and activity.

Run it with:

    python -m assistant.api                 # localhost only
    python -m assistant.api --host 0.0.0.0  # reachable from a phone

Binding to 0.0.0.0 exposes control of this computer to the local network, so it
is opt-in and warns on startup. Remote clients must pair first and then carry a
device token; see `assistant/api/auth.py`.
"""

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
import threading
import time

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from assistant.api.auth import (
    ApiSecurity,
    PairingError,
    RateLimited,
    bearer_token,
)
from assistant.api.errors import error_body, install_error_handlers
from assistant import files as shared_files
from assistant.control.capabilities import catalog
from assistant.control.models import EventType
from assistant.control.notifier import Notifier, TelegramChannel
from assistant.config import get_setting
from assistant.control.executor import get_executor
from assistant.control.service import get_control_plane

logger = logging.getLogger(__name__)

API_VERSION = "1"


# -- request bodies ---------------------------------------------------------

class PlannedStep(BaseModel):
    label: str = Field(..., min_length=1, description="What VAVE will do.")
    depends_on: list[int] = Field(default_factory=list,
                                  description="Positions that must finish first.")
    agent_id: str = Field("", description="Which agent should do it.")
    capability: str = Field("", description="Access this step needs.")


class CreateTaskRequest(BaseModel):
    goal: str = Field(..., min_length=1, description="What the user asked for.")
    steps: list[str] = Field(default_factory=list,
                             description="Observable steps, run in order.")
    plan: list[PlannedStep] = Field(
        default_factory=list,
        description="Steps as a graph. Steps with no dependencies run together.")
    capability: str = Field("", description="Capability needed, e.g. 'research'.")
    run: bool = Field(False,
                      description="Start working the steps immediately.")
    autoplan: bool = Field(False,
                           description="Let VAVE break the goal into steps.")


class ResolveApprovalRequest(BaseModel):
    approved: bool


class GrantRequest(BaseModel):
    resource: str
    actions: list[str]
    task_id: str = ""
    seconds: int = Field(1800, gt=0, le=24 * 3600)


class RegisterHelperRequest(BaseModel):
    name: str
    capabilities: list[str]
    framework: str = Field("native", description="native, http, mcp, langgraph, crewai.")
    version: str = ""
    endpoint: str = Field("", description="Where a remote agent is reached.")
    metadata: dict = Field(default_factory=dict)


class HeartbeatRequest(BaseModel):
    status: str = Field("", description="idle, working or offline.")
    latency_ms: int | None = Field(None, ge=0, description="How long the last work took.")
    ok: bool | None = Field(None, description="Whether the last work succeeded.")


class KillAgentRequest(BaseModel):
    reason: str = ""


class RegisterDeviceRequest(BaseModel):
    name: str
    kind: str = "computer"
    platform: str = ""
    capabilities: list[str] = Field(default_factory=list)


class DeviceHeartbeatRequest(BaseModel):
    capabilities: list[str] | None = None


class StepUpdateRequest(BaseModel):
    position: int
    detail: str = ""
    failed: bool = False


class CapabilityRequest(BaseModel):
    capability: str = Field(..., min_length=1,
                            description="Namespaced capability, e.g. 'google.gmail.read'.")
    agent_id: str = Field("", description="Which agent is asking.")
    task_id: str = Field("", description="The task this belongs to.")
    resource: str = Field("", description="What it applies to, if narrower than the capability.")
    seconds: int = Field(1800, gt=0, le=24 * 3600)
    reason: str = Field("", description="Why this is needed, shown to the user.")


class PolicyRuleRequest(BaseModel):
    capability: str = Field(..., min_length=1,
                            description="Capability or pattern, e.g. 'google.*'.")
    decision: str = Field(..., description="allow, require_approval or deny.")
    agent_id: str = ""
    task_id: str = ""
    resource: str = ""
    reason: str = ""


class DelegateRequest(BaseModel):
    label: str = Field(..., min_length=1, description="What the other agent should do.")
    capability: str = Field("", description="Capability the work needs.")
    agent_id: str = Field("", description="A specific agent, if it matters.")
    after: list[int] | None = Field(
        None, description="Positions this must wait for. Defaults to the unfinished ones.")


class MoveFileRequest(BaseModel):
    source: str = Field(..., description="What to move, as it was listed.")
    destination: str = Field(..., description="Where it should end up.")


class NewFolderRequest(BaseModel):
    path: str = Field(..., min_length=1, description="Folder to create.")


class SecretRequest(BaseModel):
    value: str = Field(..., min_length=1,
                       description="The credential. It is never returned again.")
    description: str = Field("", description="What this is for.")


class DraftEmailRequest(BaseModel):
    to: str = Field(..., min_length=3, description="Who it goes to.")
    subject: str = Field("", description="Subject line.")
    body: str = Field("", description="The message.")


class SendEmailRequest(DraftEmailRequest):
    """Sending is the same shape as drafting; only the consequence differs."""


class CalendarEventRequest(BaseModel):
    summary: str = Field(..., min_length=1, description="What the event is called.")
    start_time_iso: str = Field("", description="When it starts, ISO 8601. "
                                                "Empty means an hour from now.")
    duration_minutes: int = Field(60, gt=0, le=24 * 60)
    description: str = Field("", description="Details shown on the event.")


class NewDocRequest(BaseModel):
    title: str = Field(..., min_length=1)
    content: str = Field("", description="Text to put in the document.")


class NewDeckRequest(BaseModel):
    title: str = Field(..., min_length=1)
    slides: list = Field(default_factory=list,
                         description="Slide titles, or {title, bullets} entries.")


class DriveUploadRequest(BaseModel):
    name: str = Field(..., min_length=1, description="The file name in Drive.")
    content: str = Field("", description="The file's text content.")
    mime_type: str = Field("text/plain")


class PairRequest(BaseModel):
    code: str = Field(..., min_length=4, description="The pairing code shown on the computer.")
    name: str = Field("", description="What to call this device, e.g. 'Rav's phone'.")
    kind: str = Field("phone", description="phone, computer, server, tablet.")
    platform: str = Field("", description="android, ios, windows, darwin, linux.")


# Paths that must work before a client holds a token.
OPEN_PATHS = ("/", "/health", "/docs", "/redoc", "/openapi.json",
              "/api/pair", "/api/pair/code")


def create_app(control=None, executor=None, security=None, notifier=None):
    """Build the API.

    Accepts a control plane, an executor, a security policy and a notifier so
    tests, and any embedding app, can inject their own instead of driving the
    shared ones.
    """
    plane = control or get_control_plane()
    runner = executor or get_executor(plane=plane)
    guard = security if security is not None else ApiSecurity(plane.store)
    alerts = notifier if notifier is not None else Notifier(
        plane, channels=[TelegramChannel()]
        if get_setting("notify_telegram", False) else [])

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        if get_setting("auto_resume_tasks", True):
            def _auto_resume():
                try:
                    resumed = runner.resume_interrupted()
                    if resumed:
                        logger.info(f"[API] Automatically resumed {len(resumed)} interrupted task(s).")
                except Exception as error:
                    logger.debug(f"[API] Task auto-resume skipped: {error}")

            threading.Thread(target=_auto_resume, daemon=True, name="api-task-auto-resume").start()
        yield

    app = FastAPI(
        title="VAVE Control Plane",
        version=API_VERSION,
        description="Goals, helpers, devices, permissions and activity.",
        lifespan=lifespan,
    )

    # The desktop and mobile clients are separate origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.control = plane
    app.state.executor = runner
    app.state.security = guard
    app.state.notifier = alerts

    install_error_handlers(app)

    @app.middleware("http")
    async def authenticate_and_limit(request: Request, call_next):
        """Every call names a device, and no device may flood the API."""
        path = request.url.path
        client_host = request.client.host if request.client else ""

        token = bearer_token(request.headers.get("authorization")) or request.query_params.get("token", "")
        if path in OPEN_PATHS or path.startswith("/docs") or path.startswith("/static"):
            # Open to everyone, but a token still names the caller: pairing a
            # second device from a phone depends on knowing who is asking.
            device = guard.device_for_token(token)
        else:
            try:
                device = guard.authenticate(token, client_host)
            except PermissionError as error:
                return JSONResponse(status_code=401,
                                    content=error_body(401, str(error)))

        try:
            guard.check_rate(device.id if device is not None else client_host or "local")
        except RateLimited as error:
            return JSONResponse(
                status_code=429,
                content=error_body(429, "Too many requests. Slow down."),
                headers={"Retry-After": str(error.retry_after)})

        request.state.device = device
        return await call_next(request)

    # -- health and status -------------------------------------------------

    @app.get("/", tags=["system"])
    def root():
        """Point a browser at something useful instead of a bare 404."""
        return {
            "name": "VAVE Control Plane",
            "version": API_VERSION,
            "documentation": "/docs",
            "endpoints": {
                "status": "/api/status",
                "tasks": "/api/tasks",
                "devices": "/api/devices",
                "helpers": "/api/helpers",
                "approvals": "/api/approvals",
                "permissions": "/api/permissions",
                "activity": "/api/activity",
                "live_activity": "/ws/activity",
            },
        }

    @app.get("/health", tags=["system"])
    def health():
        return {"ok": True, "version": API_VERSION}

    @app.get("/api/status", tags=["system"])
    def status():
        return plane.status()

    # -- pairing -----------------------------------------------------------

    @app.post("/api/pair/code", tags=["security"])
    def pairing_code(request: Request):
        """Mint a short-lived code for a new device to claim.

        Only this computer, or an already paired device, may do this - it is
        the step that decides who gets to control the machine.
        """
        if not guard.may_pair(getattr(request.state, "device", None),
                              request.client.host if request.client else ""):
            raise HTTPException(status_code=403,
                                detail="Ask for a pairing code on the computer itself.")
        return guard.issue_pairing_code()

    @app.post("/api/pair", status_code=201, tags=["security"])
    def pair_device(request: PairRequest):
        """Trade a pairing code for a device token. The token is shown once."""
        try:
            device, token = guard.pair(request.code, request.name,
                                       kind=request.kind, platform=request.platform)
        except PairingError as error:
            raise HTTPException(status_code=403, detail=str(error))

        plane.record(f"Paired a new device: {device.name}.")
        return {"device": device.to_dict(), "token": token,
                "expires_at": device.token_expires_at,
                "note": "Store this token now. It is not shown again."}

    @app.post("/api/auth/rotate", tags=["security"])
    def rotate_token(request: Request):
        """Rotate the token for an active device before it expires."""
        token = bearer_token(request.headers.get("authorization", ""))
        if not token:
            raise HTTPException(status_code=401, detail="Bearer token required for rotation.")
        try:
            device, new_token = guard.rotate(token)
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error))

        plane.record(f"Rotated token for device: {device.name}.")
        return {
            "device": device.to_dict(),
            "token": new_token,
            "expires_at": device.token_expires_at,
            "note": "Store this new token now. The old token is invalidated."
        }

    @app.delete("/api/devices/{device_id}/token", tags=["security"])
    def unpair_device(device_id: str):
        """Revoke one device's access without touching any other device."""
        device = guard.unpair(device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="No such device.")
        plane.record(f"Revoked access for {device.name}.")
        return device.to_dict()

    # -- tasks -------------------------------------------------------------

    @app.get("/api/tasks", tags=["tasks"])
    def list_tasks(limit: int = 50, active_only: bool = False):
        return [task.to_dict()
                for task in plane.list_tasks(limit=limit, active_only=active_only)]

    @app.post("/api/tasks", status_code=201, tags=["tasks"])
    def create_task(request: CreateTaskRequest):
        steps = [step.model_dump() for step in request.plan] or request.steps
        if not steps and request.autoplan:
            steps = runner.planner.plan(request.goal)

        try:
            task = plane.create_task(request.goal, steps=steps,
                                     capability=request.capability or None)
        except RuntimeError as error:
            # Raised while an emergency stop is latched.
            raise HTTPException(status_code=409, detail=str(error))

        if request.run:
            runner.submit(task.id)

        return plane.task_detail(task.id)

    @app.get("/api/tasks/{task_id}", tags=["tasks"])
    def get_task(task_id: str):
        detail = plane.task_detail(task_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="No such task.")
        return detail

    @app.post("/api/tasks/{task_id}/run", status_code=202, tags=["tasks"])
    def run_task(task_id: str):
        """Hand the task's steps to the AI brain and work them in order.

        This returns as soon as the work starts. Progress arrives over
        /ws/activity, or by polling the task.
        """
        if plane.is_stopped:
            raise HTTPException(status_code=409,
                                detail="VAVE is stopped. Resume before starting work.")

        task = plane.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="No such task.")
        if task.status.is_terminal:
            raise HTTPException(status_code=409, detail="This task already finished.")

        runner.submit(task_id)
        return plane.task_detail(task_id)

    @app.post("/api/tasks/{task_id}/steps/start", tags=["tasks"])
    def start_step(task_id: str, request: StepUpdateRequest):
        step = plane.start_step(task_id, request.position)
        if step is None:
            raise HTTPException(status_code=404, detail="No such step.")
        return step.to_dict()

    @app.post("/api/tasks/{task_id}/steps/finish", tags=["tasks"])
    def finish_step(task_id: str, request: StepUpdateRequest):
        step = plane.finish_step(task_id, request.position,
                                 detail=request.detail, failed=request.failed)
        if step is None:
            raise HTTPException(status_code=404, detail="No such step.")
        return step.to_dict()

    @app.post("/api/tasks/{task_id}/complete", tags=["tasks"])
    def complete_task(task_id: str, summary: str = ""):
        task = plane.complete_task(task_id, summary=summary)
        if task is None:
            raise HTTPException(status_code=404, detail="No such task.")
        return plane.task_detail(task_id)

    @app.post("/api/tasks/{task_id}/cancel", tags=["tasks"])
    def cancel_task(task_id: str):
        """Stop a task. A step already running is interrupted, not waited on."""
        if plane.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="No such task.")
        runner.stop(task_id)
        return plane.task_detail(task_id)

    @app.post("/api/tasks/{task_id}/delegate", status_code=201, tags=["tasks"])
    def delegate_step(task_id: str, request: DelegateRequest):
        """Hand part of this task to another agent, under the same task."""
        if plane.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="No such task.")

        step = plane.delegate(task_id, request.label, capability=request.capability,
                              agent_id=request.agent_id, after=request.after)
        if step is None:
            raise HTTPException(
                status_code=409,
                detail="No agent is available with that capability.")
        return step.to_dict()

    @app.post("/api/tasks/resume", tags=["tasks"])
    def resume_interrupted():
        """Pick up tasks that were running when VAVE last stopped."""
        return [task.to_dict() for task in runner.resume_interrupted()]

    @app.post("/api/tasks/plan", tags=["tasks"])
    def plan_goal(request: CreateTaskRequest):
        """Break a goal into steps without committing to running them."""
        return {"goal": request.goal, "steps": runner.planner.plan(request.goal)}

    # -- capabilities and policy -------------------------------------------

    @app.get("/api/capabilities", tags=["security"])
    def list_capabilities(prefix: str = ""):
        """Everything an agent may ask for, with the risk each one carries."""
        return catalog(prefix)

    @app.post("/api/capabilities/request", tags=["security"])
    def request_capability(request: CapabilityRequest):
        """Ask for access. Answers granted, waiting on you, or denied."""
        try:
            return plane.request_capability(
                request.capability, agent_id=request.agent_id,
                task_id=request.task_id, resource=request.resource,
                seconds=request.seconds, reason=request.reason)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error))

    @app.get("/api/policies", tags=["security"])
    def list_policies():
        return [rule.to_dict() for rule in plane.list_policy_rules()]

    @app.post("/api/policies", status_code=201, tags=["security"])
    def add_policy(request: PolicyRuleRequest):
        try:
            rule = plane.add_policy_rule(
                request.capability, request.decision, agent_id=request.agent_id,
                task_id=request.task_id, resource=request.resource,
                reason=request.reason)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Decision must be allow, require_approval or deny.")
        return rule.to_dict()

    @app.delete("/api/policies/{rule_id}", tags=["security"])
    def remove_policy(rule_id: str):
        rule = plane.remove_policy_rule(rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="No such policy rule.")
        return rule.to_dict()

    # -- files -------------------------------------------------------------
    # Your computer's files, reachable from a paired phone anywhere. Only the
    # folders in `file_shares` are visible, and every path is resolved before
    # it is checked, so a symlink out of a share fails like any other path.

    def _files_or_404(action, *args, **kwargs):
        try:
            return action(*args, **kwargs)
        except shared_files.FileAccessError as error:
            raise HTTPException(status_code=404, detail=str(error))

    def _writing_allowed():
        if not get_setting("files_allow_write", True):
            raise HTTPException(
                status_code=403,
                detail="Changing files from a phone is switched off. "
                       "Set files_allow_write in config.json.")

    @app.get("/api/files/shares", tags=["files"])
    def list_shares():
        """The folders that are reachable at all."""
        return shared_files.describe_shares()

    @app.get("/api/files", tags=["files"])
    def list_files(path: str = ""):
        """What is in a folder, folders first."""
        return _files_or_404(shared_files.list_dir, path)

    @app.get("/api/files/search", tags=["files"])
    def search_files(query: str, path: str = "", limit: int = 100):
        """Find a file by name, without knowing where you left it."""
        return _files_or_404(shared_files.search, query, path, limit)

    @app.get("/api/files/download", tags=["files"])
    def download_file(path: str, request: Request):
        """Send a file to the phone. Streamed, so size is not a problem."""
        target, media_type = _files_or_404(shared_files.open_for_download, path)

        device = getattr(request.state, "device", None)
        plane.record(f"Sent {target.name} to {device.name if device else 'this computer'}.",
                     metadata={"path": str(target)})
        return FileResponse(target, media_type=media_type, filename=target.name)

    @app.post("/api/files/upload", status_code=201, tags=["files"])
    async def upload_file(request: Request, file: UploadFile = File(...),
                          folder: str = Form(""), overwrite: bool = Form(False)):
        """Take a file from the phone and put it in a shared folder."""
        _writing_allowed()
        saved = _files_or_404(shared_files.save_upload, folder, file.filename,
                              file.file, overwrite)

        device = getattr(request.state, "device", None)
        plane.record(f"Received {saved['name']} from "
                     f"{device.name if device else 'this computer'}.",
                     metadata={"path": saved["path"]})
        return saved

    @app.post("/api/files/folder", status_code=201, tags=["files"])
    def create_folder(request_body: NewFolderRequest):
        _writing_allowed()
        return _files_or_404(shared_files.make_folder, request_body.path)

    @app.post("/api/files/move", tags=["files"])
    def move_file(request_body: MoveFileRequest):
        _writing_allowed()
        return _files_or_404(shared_files.move, request_body.source,
                             request_body.destination)

    @app.delete("/api/files", tags=["files"])
    def delete_file(path: str, request: Request):
        """Delete a file, or an empty folder. Off unless you turn it on."""
        if not get_setting("files_allow_delete", False):
            raise HTTPException(
                status_code=403,
                detail="Deleting from a phone is switched off. "
                       "Set files_allow_delete in config.json.")

        result = _files_or_404(shared_files.delete, path)
        device = getattr(request.state, "device", None)
        plane.record(f"Deleted {Path(result['path']).name} at the request of "
                     f"{device.name if device else 'this computer'}.",
                     metadata=result)
        return result

    # -- Google Workspace --------------------------------------------------
    # Drive, Gmail, Calendar, Docs, Sheets and Slides through one gateway.
    # Reading is direct. Anything the outside world would notice - sending
    # mail, putting an event on a calendar, uploading a file - is held for
    # approval, and the approval is bound to those exact arguments.

    def _google():
        from assistant.workspace.gateway import gateway as workspace_gateway
        return workspace_gateway

    def _google_live():
        """Whether these answers come from Google or are examples."""
        return _google().is_connected()

    def _google_payload(items, **extra):
        """Every Google answer says which it is, so no screen has to guess."""
        body = {"live": _google_live(), "items": items}
        if not body["live"]:
            body["notice"] = ("Google isn't connected yet. These are examples, "
                              "not your data.")
        body.update(extra)
        return body

    def _fingerprint(action, arguments):
        """Bind an approval to one specific action.

        Approving "email Sam the budget" must not also authorise emailing
        someone else, so the resource carries a hash of the arguments.
        """
        import hashlib
        import json

        digest = hashlib.sha256(
            json.dumps(arguments, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
        return f"google:{action}:{digest}"

    def _held_for_approval(capability, action, question, impact, arguments,
                           request):
        """Run a consequential Google action only once the user has said yes.

        Returns None when the action may go ahead - the grant is spent on the
        way out, so one approval is one action. Otherwise returns the pending
        approval for the client to show.
        """
        resource = _fingerprint(action, arguments)

        for permission in plane.list_permissions(active_only=True):
            if permission.resource == resource and permission.allows(capability):
                # Spend it. A second identical call needs a second approval.
                plane.revoke(permission.id, reason="Used once, as approved.")
                return None

        device = getattr(request.state, "device", None)
        approval = plane.request_approval(
            action=action, question=question, impact=impact,
            reason=f"Asked from {device.name}." if device else "",
            capability=capability, resource=resource,
            risk=plane.capability_risk(capability))
        return approval

    @app.get("/api/google/status", tags=["google"])
    def google_status():
        """Whether Google is connected, and what it can reach if it is."""
        return _google().get_status()

    @app.post("/api/google/connect", tags=["google"])
    def google_connect():
        """Start the Google sign-in. The browser opens on this computer.

        The consent screen can only be answered where the browser is, so this
        returns immediately and the status endpoint reports the outcome.
        """
        from assistant.workspace import auth as workspace_auth

        state = workspace_auth.connection_state()
        if state["state"] == workspace_auth.LIVE:
            return _google().get_status()
        if state["state"] == workspace_auth.NOT_CONFIGURED:
            raise HTTPException(status_code=409, detail=state["detail"])

        def _run():
            result = workspace_auth.authorize()
            plane.record(
                "Connected your Google account." if result["state"] == workspace_auth.LIVE
                else f"Google sign-in did not finish. {result['detail']}",
                metadata={"state": result["state"]})

        threading.Thread(target=_run, daemon=True, name="google-oauth").start()
        plane.record("Opened Google sign-in on this computer.")
        return {"state": workspace_auth.NEEDS_AUTHORIZATION,
                "detail": "Finish signing in to Google in the browser on your "
                          "computer, then check the status again.",
                "connected": False}

    @app.post("/api/google/disconnect", tags=["google"])
    def google_disconnect():
        """Forget this computer's Google token. The account is untouched."""
        from assistant.workspace import auth as workspace_auth

        state = workspace_auth.disconnect()
        plane.record("Disconnected your Google account from this computer.")
        return {"connected": False, **state}

    @app.get("/api/google/drive", tags=["google"])
    def google_drive(limit: int = 20):
        """Recent files in Drive."""
        return _google_payload(_google().execute_capability(
            "google.drive.list", limit=limit))

    @app.get("/api/google/drive/search", tags=["google"])
    def google_drive_search(query: str, limit: int = 20):
        return _google_payload(_google().execute_capability(
            "google.drive.search", query=query, limit=limit))

    @app.get("/api/google/gmail", tags=["google"])
    def google_gmail(query: str = "is:unread", limit: int = 10):
        return _google_payload(_google().execute_capability(
            "google.gmail.search", query=query, max_results=limit),
            query=query)

    @app.get("/api/google/calendar", tags=["google"])
    def google_calendar(limit: int = 10):
        return _google_payload(_google().execute_capability(
            "google.calendar.read", max_results=limit))

    @app.post("/api/google/gmail/draft", status_code=201, tags=["google"])
    def google_draft_email(body: DraftEmailRequest):
        """A draft changes nothing until someone sends it, so it needs no approval."""
        result = _google().execute_capability(
            "google.gmail.draft", to=body.to, subject=body.subject, body=body.body)
        plane.record(f"Drafted an email to {body.to}.")
        return {"live": _google_live(), "draft": result}

    @app.post("/api/google/gmail/send", tags=["google"])
    def google_send_email(body: SendEmailRequest, request: Request):
        """Send mail - held for approval, and the approval names the recipient."""
        arguments = {"to": body.to, "subject": body.subject, "body": body.body}
        approval = _held_for_approval(
            "google.gmail.send", "Send email",
            f"Send \"{body.subject}\" to {body.to}?",
            "The message leaves your account and cannot be unsent.",
            arguments, request)
        if approval is not None:
            return JSONResponse(status_code=202, content={
                "status": "waiting_approval",
                "approval": approval.to_dict(),
                "detail": "Approve this on your phone or the desktop app, then "
                          "send it again."})

        result = _google().execute_capability("google.gmail.send", **arguments)
        plane.record(f"Sent an email to {body.to}.", result="sent")
        return {"live": _google_live(), "status": "sent", "message": result}

    @app.post("/api/google/calendar/events", tags=["google"])
    def google_create_event(body: CalendarEventRequest, request: Request):
        """Put an event on the calendar - other people may see it, so ask first."""
        arguments = {"summary": body.summary, "start_time_iso": body.start_time_iso,
                     "duration_minutes": body.duration_minutes,
                     "description": body.description}
        approval = _held_for_approval(
            "google.calendar.write", "Create calendar event",
            f"Put \"{body.summary}\" on your calendar?",
            "The event appears on your calendar and to anyone you invite.",
            arguments, request)
        if approval is not None:
            return JSONResponse(status_code=202, content={
                "status": "waiting_approval",
                "approval": approval.to_dict(),
                "detail": "Approve this, then create it again."})

        conflicts = _google().execute_capability(
            "google.calendar.conflicts",
            start_iso=body.start_time_iso or "",
            end_iso=body.start_time_iso or "") if body.start_time_iso else []
        result = _google().execute_capability("google.calendar.write", **arguments)
        plane.record(f"Added \"{body.summary}\" to your calendar.", result="created")
        return {"live": _google_live(), "event": result, "conflicts": conflicts}

    @app.post("/api/google/docs", status_code=201, tags=["google"])
    def google_create_doc(body: NewDocRequest):
        """A new document in your own Drive. Nothing is shared by creating it."""
        result = _google().execute_capability(
            "google.docs.create", title=body.title, content=body.content)
        plane.record(f"Created the document \"{body.title}\".")
        return {"live": _google_live(), "document": result}

    @app.post("/api/google/slides", status_code=201, tags=["google"])
    def google_create_slides(body: NewDeckRequest):
        """Turn an outline into a presentation."""
        result = _google().execute_capability(
            "google.slides.create", title=body.title, slides=body.slides)
        plane.record(f"Created the presentation \"{body.title}\".")
        return {"live": _google_live(), "presentation": result}

    @app.post("/api/google/drive/upload", status_code=201, tags=["google"])
    def google_upload(body: DriveUploadRequest, request: Request):
        """Put a file in Drive - held for approval, like anything that leaves."""
        arguments = {"name": body.name, "content": body.content,
                     "mime_type": body.mime_type}
        approval = _held_for_approval(
            "google.drive.write", "Upload to Drive",
            f"Upload \"{body.name}\" to your Google Drive?",
            "The file is stored in your Drive.",
            arguments, request)
        if approval is not None:
            return JSONResponse(status_code=202, content={
                "status": "waiting_approval",
                "approval": approval.to_dict(),
                "detail": "Approve this, then upload it again."})

        result = _google().execute_capability("google.drive.write", **arguments)
        plane.record(f"Uploaded \"{body.name}\" to your Google Drive.")
        return {"live": _google_live(), "file": result}

    # -- secrets -----------------------------------------------------------
    # Values go in and are never handed back. Agents receive secret://name and
    # the control plane resolves it at the moment a tool runs.

    @app.get("/api/secrets", tags=["security"])
    def list_secrets():
        """What VAVE holds, by name. Never the values."""
        return plane.secrets.list()

    @app.put("/api/secrets/{name}", status_code=201, tags=["security"])
    def put_secret(name: str, request: SecretRequest):
        """Store or replace a credential. Use secret://<name> to refer to it."""
        try:
            return plane.secrets.put(name, request.value,
                                     description=request.description)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))

    @app.delete("/api/secrets/{name}", tags=["security"])
    def delete_secret(name: str):
        if plane.secrets.delete(name) is None:
            raise HTTPException(status_code=404, detail="No such secret.")
        return {"name": name, "deleted": True}

    @app.post("/api/secrets/import-config", tags=["security"])
    def import_config_secrets():
        """Move credentials out of config.json, leaving references behind."""
        moved = plane.secrets.import_from_config()
        if moved:
            plane.record(f"Moved {len(moved)} credential"
                         f"{'s' if len(moved) != 1 else ''} into the secret store.")
        return {"moved": moved}

    # -- devices and helpers ----------------------------------------------

    @app.get("/api/devices", tags=["devices"])
    def list_devices():
        return [device.to_dict() for device in plane.list_devices()]

    @app.post("/api/devices", status_code=201, tags=["devices"])
    def register_device(request: RegisterDeviceRequest):
        device = plane.register_device(request.name, kind=request.kind,
                                       platform_name=request.platform,
                                       capabilities=request.capabilities)
        return device.to_dict()

    @app.post("/api/devices/{device_id}/heartbeat", tags=["devices"])
    def device_heartbeat(device_id: str, request: DeviceHeartbeatRequest):
        """A device says it is still here, and what it can do."""
        device = plane.device_heartbeat(device_id, capabilities=request.capabilities)
        if device is None:
            raise HTTPException(status_code=404, detail="No such device.")
        return device.to_dict()

    # -- agents -------------------------------------------------------------
    # /api/helpers is the older name for the same thing and still works.

    @app.get("/api/agents", tags=["agents"])
    @app.get("/api/helpers", tags=["helpers"])
    def list_agents():
        return [helper.to_dict() for helper in plane.list_helpers()]

    @app.post("/api/agents", status_code=201, tags=["agents"])
    @app.post("/api/helpers", status_code=201, tags=["helpers"])
    def register_agent(request: RegisterHelperRequest):
        helper = plane.register_helper(request.name, request.capabilities,
                                       framework=request.framework,
                                       version=request.version,
                                       endpoint=request.endpoint,
                                       metadata=request.metadata)
        return helper.to_dict()

    @app.get("/api/agents/health", tags=["agents"])
    def agent_health():
        """One row per agent: status, error rate and slow-end latency."""
        return plane.agent_health()

    @app.get("/api/agents/{agent_id}", tags=["agents"])
    def get_agent(agent_id: str):
        helper = plane.get_helper(agent_id)
        if helper is None:
            raise HTTPException(status_code=404, detail="No such agent.")
        return helper.to_dict()

    @app.post("/api/agents/{agent_id}/heartbeat", tags=["agents"])
    def agent_heartbeat(agent_id: str, request: HeartbeatRequest):
        """An agent reports that it is alive, and how its last work went."""
        try:
            helper = plane.heartbeat(agent_id, latency_ms=request.latency_ms,
                                     ok=request.ok, status=request.status or None)
        except ValueError:
            raise HTTPException(status_code=422,
                                detail="Status must be idle, working or offline.")
        if helper is None:
            raise HTTPException(status_code=404, detail="No such agent.")
        return helper.to_dict()

    @app.post("/api/agents/{agent_id}/enable", tags=["agents"])
    def enable_agent(agent_id: str):
        helper = plane.set_helper_enabled(agent_id, True)
        if helper is None:
            raise HTTPException(status_code=404, detail="No such agent.")
        return helper.to_dict()

    @app.post("/api/agents/{agent_id}/disable", tags=["agents"])
    def disable_agent(agent_id: str):
        """Stop giving this agent work, without forgetting what it did."""
        helper = plane.set_helper_enabled(agent_id, False)
        if helper is None:
            raise HTTPException(status_code=404, detail="No such agent.")
        return helper.to_dict()

    @app.post("/api/agents/{agent_id}/kill", tags=["security"])
    def kill_agent(agent_id: str, request: KillAgentRequest):
        """Stop an agent now: its work, its access, and any future request."""
        helper = plane.kill_helper(agent_id, reason=request.reason)
        if helper is None:
            raise HTTPException(status_code=404, detail="No such agent.")
        return helper.to_dict()

    # -- approvals ---------------------------------------------------------

    @app.get("/api/approvals", tags=["approvals"])
    def list_approvals(pending_only: bool = True):
        return [approval.to_dict()
                for approval in plane.list_approvals(pending_only=pending_only)]

    @app.post("/api/approvals/{approval_id}", tags=["approvals"])
    def resolve_approval(approval_id: str, request: ResolveApprovalRequest):
        approval = plane.resolve_approval(approval_id, request.approved)
        if approval is None:
            raise HTTPException(status_code=404,
                                detail="No such approval, or it was already decided.")
        return approval.to_dict()
 
    # -- confirmations -----------------------------------------------------

    @app.get("/api/confirmations", tags=["confirmations"])
    def list_confirmations():
        from assistant import confirm
        return {"pending_ids": confirm.list_pending()}

    @app.post("/api/confirmations/{req_id}", tags=["confirmations"])
    def resolve_confirmation(req_id: str, request: ResolveApprovalRequest):
        from assistant import confirm
        success = confirm.resolve(req_id, request.approved)
        if not success:
            raise HTTPException(status_code=404,
                                detail="No such confirmation request, or it was already decided.")
        return {"req_id": req_id, "approved": request.approved, "resolved": True}

    # -- permissions -------------------------------------------------------

    @app.get("/api/permissions", tags=["security"])
    def list_permissions(active_only: bool = True):
        return [permission.to_dict()
                for permission in plane.list_permissions(active_only=active_only)]

    @app.post("/api/permissions", status_code=201, tags=["security"])
    def grant_permission(request: GrantRequest):
        permission = plane.grant(request.resource, request.actions,
                                 task_id=request.task_id, seconds=request.seconds)
        return permission.to_dict()

    @app.delete("/api/permissions/{permission_id}", tags=["security"])
    def revoke_permission(permission_id: str):
        permission = plane.revoke(permission_id)
        if permission is None:
            raise HTTPException(status_code=404, detail="No such permission.")
        return permission.to_dict()

    # -- activity ----------------------------------------------------------

    @app.get("/api/activity", tags=["activity"])
    def list_activity(limit: int = 100, task_id: str = "", types: str = ""):
        """The timeline, newest first. `types` filters, comma separated."""
        wanted = [item.strip() for item in types.split(",") if item.strip()]
        for item in wanted:
            if item not in {event.value for event in EventType}:
                raise HTTPException(status_code=422,
                                    detail=f"There is no event type called '{item}'.")

        return [event.to_dict()
                for event in plane.list_events(limit=limit, task_id=task_id or None,
                                               types=wanted or None)]

    @app.get("/api/event-types", tags=["activity"])
    def list_event_types():
        """Every event a client can filter or subscribe to."""
        return sorted(event.value for event in EventType)

    @app.get("/api/notifications", tags=["activity"])
    def list_notifications(limit: int = 20):
        """What VAVE would have sent to your phone, newest first."""
        return alerts.recent(limit=limit)

    # -- emergency stop ----------------------------------------------------

    @app.post("/api/emergency-stop", tags=["security"])
    def emergency_stop():
        from assistant.safety_stop import hard_stop
        hard_stop(source="api")
        return plane.emergency_stop()

    @app.post("/api/resume", tags=["security"])
    def resume():
        from assistant.safety_stop import reset_hard_stop
        reset_hard_stop()
        return plane.resume()

    # -- live activity stream ----------------------------------------------

    @app.get("/api/events/stream", tags=["activity"])
    async def sse_activity_stream(request: Request, token: str = "", limit: int = 0):
        """Stream real-time control plane activity events using Server-Sent Events (SSE)."""
        auth_token = token or bearer_token(request.headers.get("authorization", ""))
        client_host = request.client.host if request.client else ""
        try:
            guard.authenticate(auth_token, client_host)
        except PermissionError as error:
            raise HTTPException(status_code=401, detail=str(error))

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)

        def on_event(event):
            loop.call_soon_threadsafe(_offer, event)

        def _offer(event):
            try:
                queue.put_nowait(event.to_dict())
            except asyncio.QueueFull:
                pass

        unsubscribe = plane.subscribe(on_event)

        async def event_generator():
            sent = 0
            try:
                yield f"event: connected\ndata: {json.dumps({'status': 'connected'})}\n\n"
                sent += 1
                if limit and sent >= limit:
                    return

                for item in reversed(plane.list_events(limit=5)):
                    yield f"event: activity\ndata: {json.dumps(item.to_dict())}\n\n"
                    sent += 1
                    if limit and sent >= limit:
                        return

                while True:
                    if limit and sent >= limit:
                        break
                    if await request.is_disconnected():
                        break
                    try:
                        data = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield f"event: activity\ndata: {json.dumps(data)}\n\n"
                        sent += 1
                    except asyncio.TimeoutError:
                        yield f"event: ping\ndata: {json.dumps({'time': time.time()})}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                unsubscribe()

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.websocket("/ws/events")
    async def event_stream(websocket: WebSocket, token: str = "", types: str = "",
                           task_id: str = ""):
        """Every event as it happens, optionally filtered.

        /ws/activity carries the same events for older clients; this one adds
        filtering and the fields a client groups on.
        """
        wanted = {item.strip() for item in types.split(",") if item.strip()}
        await _stream(websocket, token,
                      keep=lambda event: ((not wanted or event.type.value in wanted)
                                          and (not task_id or event.task_id == task_id)))

    @app.websocket("/ws/notifications")
    async def notification_stream(websocket: WebSocket, token: str = ""):
        """Only what is worth interrupting a person for."""
        try:
            guard.authenticate(
                token or bearer_token(websocket.headers.get("authorization")),
                websocket.client.host if websocket.client else "")
        except PermissionError:
            await websocket.close(code=1008)
            return

        await websocket.accept()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        class SocketChannel:
            name = "websocket"

            def deliver(self, notification):
                loop.call_soon_threadsafe(_offer, notification.to_dict())

        def _offer(payload):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass        # a slow phone must not stall the control plane

        channel = alerts.add_channel(SocketChannel())
        try:
            for item in reversed(alerts.recent(limit=10)):
                await websocket.send_json(item)
            while True:
                await websocket.send_json(await queue.get())
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("Notification stream failed")
        finally:
            alerts.remove_channel(channel)

    @app.websocket("/ws/activity")
    async def activity_stream(websocket: WebSocket, token: str = ""):
        """Pushes activity events to a client as they happen.

        A socket carries the same authority as the REST API, so it is
        authenticated the same way - by token, or by being local.
        """
        try:
            guard.authenticate(
                token or bearer_token(websocket.headers.get("authorization")),
                websocket.client.host if websocket.client else "")
        except PermissionError:
            await websocket.close(code=1008)
            return

        await websocket.accept()

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)

        def on_event(event):
            # Called from worker threads, so hop back onto the event loop.
            loop.call_soon_threadsafe(_offer, event)

        def _offer(event):
            try:
                queue.put_nowait(event.to_dict())
            except asyncio.QueueFull:
                pass  # A slow client must not stall the control plane.

        unsubscribe = plane.subscribe(on_event)

        try:
            # Send recent history first so a client opens with context.
            for event in reversed(plane.list_events(limit=25)):
                await websocket.send_json(event.to_dict())

            while True:
                await websocket.send_json(await queue.get())
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("Activity stream failed")
        finally:
            unsubscribe()

    async def _stream(websocket, token, keep):
        """Shared plumbing for the event sockets."""
        try:
            guard.authenticate(
                token or bearer_token(websocket.headers.get("authorization")),
                websocket.client.host if websocket.client else "")
        except PermissionError:
            await websocket.close(code=1008)
            return

        await websocket.accept()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)

        def on_event(event):
            if keep(event):
                loop.call_soon_threadsafe(_offer, event.to_dict())

        def _offer(payload):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

        unsubscribe = plane.subscribe(on_event)
        try:
            while True:
                await websocket.send_json(await queue.get())
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("Event stream failed")
        finally:
            unsubscribe()

    return app


def local_addresses():
    """The addresses a phone or another computer could use to reach this one.

    A machine has several and only some of them are useful, so this returns
    what someone could actually type rather than the whole interface list.
    """
    import socket

    found = []
    try:
        # Nothing is sent; this only asks the routing table which address this
        # machine would use to reach the outside, which is the one on the LAN.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        found.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith("127.") and address not in found:
                found.append(address)
    except OSError:
        pass

    return found


def request_pairing_code(port):
    """Ask the running API for a code, and print it for someone to type in.

    The codes live in the running process, so this has to go through it. The
    request comes from this machine, which is what the server trusts.
    """
    import requests

    try:
        response = requests.post(f"http://127.0.0.1:{port}/api/pair/code", timeout=5)
        response.raise_for_status()
    except requests.RequestException as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        if status == 404:
            print(
                f"Something is listening on port {port}, but it is not the current "
                "VAVE pairing API.\n"
                "Stop that process, then start this server again:\n\n"
                f"    python main.py --server --host 0.0.0.0 --port {port}"
            )
            return 1
        print(f"Could not reach VAVE on port {port}. Is it running?  ({error})")
        return 1

    body = response.json()
    minutes = body.get("expires_in", 600) // 60
    print()
    print("  Enter this code on your phone:")
    print(f"\n      {body['code']}\n")
    print(f"  It stops working in {minutes} minutes.")
    print()
    return 0


def _print_welcome(host, port):
    """Tell the user what to type on the phone, since only they can see this."""
    print()
    print("  VAVE is listening.")
    print()
    if host in ("127.0.0.1", "localhost"):
        print(f"  This computer only:      http://127.0.0.1:{port}")
        print("  To let a phone connect, start it with --host 0.0.0.0")
    else:
        for address in local_addresses() or [host]:
            print(f"  Enter on your phone:     {address}:{port}")
    print(f"  To connect a phone:      python -m assistant.api --pair --port {port}")
    print()


def _port_available(host, port):
    """Return False when another process already owns this bind address."""
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((host, port))
        return True
    except PermissionError:
        return True
    except OSError:
        return False
    finally:
        probe.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the VAVE control plane API.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address. Use 0.0.0.0 to allow phone access.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--pair", action="store_true",
                        help="Show a code for a new phone, then exit. "
                             "VAVE must already be running.")
    args = parser.parse_args(argv)

    if args.pair:
        return request_pairing_code(args.port)

    import uvicorn

    from assistant import logging_setup
    logging_setup.configure_logging()

    if args.host not in ("127.0.0.1", "localhost"):
        logger.warning(
            "Listening on %s. Anything on your network can control this computer. "
            "Only do this on a network you trust.", args.host)

    if not _port_available(args.host, args.port):
        print(
            f"Port {args.port} is already in use. Stop the existing process or choose "
            f"another port:\n\n"
            f"    python main.py --server --host {args.host} --port {args.port + 1}"
        )
        return 1

    _print_welcome(args.host, args.port)

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
