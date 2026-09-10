import time
import threading
try:
    import uiautomation as auto
except (ImportError, Exception):
    auto = None
import logging

from assistant.config import get_setting
from assistant import events
from assistant.overwatch.rules import OverwatchRule
from assistant.overwatch.limits import RateLimiter
from assistant.overwatch.scanner import scan_windows

logger = logging.getLogger(__name__)

class OverwatchEngine:
    def __init__(self):
        self._active = False
        self._thread = None
        self._bus = None
        self.rules = []
        self.limiter = RateLimiter(max_actions=5, interval=10.0)
        self._recent_clicks = {} 
        
    def configure(self, bus: events.EventBus):
        self._bus = bus
        self._reload_rules()
        
    def _reload_rules(self):
        config = get_setting("overwatch", {})
        self.rules = []
        for r in config.get("rules", []):
            self.rules.append(OverwatchRule(
                target_text=r.get("target_text", ""),
                auto_click=r.get("auto_click", False),
                require_focus=r.get("require_focus", False),
                pattern=r.get("pattern", "exact")
            ))
            
    def start(self):
        if self._active:
            return
        self._active = True
        self._reload_rules()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="OverwatchEngine")
        self._thread.start()
        if self._bus:
            self._bus.emit(events.EVENT_OVERWATCH_STATE, "Overwatch activated.")
            
    def stop(self):
        self._active = False
        if self._bus:
            self._bus.emit(events.EVENT_OVERWATCH_STATE, "Overwatch stopped.")
            
    def _run_loop(self):
        if auto:
            auto.UIAutomationInitializerInThread()
        
        while self._active:
            try:
                elements = scan_windows(max_z_order=get_setting("overwatch", {}).get("max_z_order", 3))
                
                for el in elements:
                    if not self._active:
                        break
                        
                    for rule in self.rules:
                        if rule.matches(el["name"]):
                            self._handle_match(el, rule)
                            
            except Exception as e:
                logger.error(f"Overwatch engine error: {e}")
                
            time.sleep(get_setting("overwatch", {}).get("scan_interval", 1.0))
            
        if auto:
            auto.UIAutomationUninitializerInThread()
        
    def _handle_match(self, element, rule):
        rid = tuple(element["runtime_id"])
        
        if rid in self._recent_clicks and time.time() - self._recent_clicks[rid] < 5.0:
            return
            
        if self._bus:
            self._bus.emit(events.EVENT_OVERWATCH_CANDIDATE, f"Found candidate: {element['name']}", name=element['name'])
            
        if not rule.auto_click:
            return
            
        if not self.limiter.allow():
            logger.warning("Overwatch rate limit exceeded. Ignoring match.")
            return
            
        from assistant import guard
        if guard.is_killed():
            logger.warning(f"Overwatch auto-click blocked: kill switch active.")
            return

        ctrl = element["control"]

        def _do_click():
            if rule.require_focus:
                ctrl.SetFocus()
            ctrl.Click(waitTime=0.1)

        try:
            guard.call(_do_click, _tool_name="overwatch_auto_click", name=element["name"])
            self._recent_clicks[rid] = time.time()
            logger.info(f"Overwatch auto-clicked: {element['name']}")
            
            if self._bus:
                self._bus.emit(events.EVENT_OVERWATCH_ACTION, f"Auto-clicked: {element['name']}")
                
        except guard.ToolDenied as e:
            logger.warning(f"Overwatch auto-click denied by guard: {e}")
        except Exception as e:
            logger.error(f"Failed to click element {element['name']}: {e}")

_engine = OverwatchEngine()

def configure(event_bus: events.EventBus):
    _engine.configure(event_bus)
    
def is_available():
    """Overwatch drives the Windows UI Automation API, which has no equivalent here."""
    try:
        import uiautomation  # noqa: F401
        return True
    except ImportError:
        return False

def start_overwatch(rules: list = None):
    if not is_available():
        return "Overwatch is not available on this computer. It needs Windows UI Automation."

    if rules:
        config = get_setting("overwatch", {})
        config["rules"] = rules
        from assistant.config import update_setting
        update_setting("overwatch", config)
        _engine._reload_rules()
        
    _engine.start()
    return "Overwatch started."
    
def stop_overwatch():
    _engine.stop()
    return "Overwatch stopped."
