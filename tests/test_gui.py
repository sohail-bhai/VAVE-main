import importlib.util
import unittest

HAS_TKINTER = importlib.util.find_spec("tkinter") is not None

if HAS_TKINTER:
    from gui.store import AppStore
    from gui.app import VaveDashboardApp


@unittest.skipUnless(HAS_TKINTER, "tkinter is not installed on this machine")
class TestVaveGUI(unittest.TestCase):
    def test_store_operations(self):
        store = AppStore()
        self.assertEqual(store.current_page, "home")
        
        # Test navigation
        store.set_page("devices")
        self.assertEqual(store.current_page, "devices")
        
        # Test logs
        initial_count = len(store.system_logs)
        store.add_system_log("Test log entry", "working")
        self.assertEqual(len(store.system_logs), initial_count + 1)
        self.assertEqual(store.system_logs[-1]["text"], "Test log entry")
        
        # Test memory
        initial_mem_count = len(store.memories)
        first_mem_id = store.memories[0]["id"]
        store.forget_memory(first_mem_id)
        self.assertEqual(len(store.memories), initial_mem_count - 1)
        
        # Test drawer
        store.open_drawer("device", {"name": "Test Device"})
        self.assertIsNotNone(store.active_drawer)
        self.assertEqual(store.active_drawer["type"], "device")
        store.close_drawer()
        self.assertIsNone(store.active_drawer)

    def test_app_lifecycle_and_page_switching(self):
        # Instantiate full CustomTkinter dashboard
        app = VaveDashboardApp()
        
        # Verify initial layout and widgets
        self.assertIsNotNone(app.sidebar)
        self.assertIsNotNone(app.topbar)
        self.assertIsNotNone(app.system_log_panel)
        self.assertIsNotNone(app.detail_drawer)
        
        # Cycle through all pages to ensure no errors in rendering
        for page_name in ["home", "devices", "files", "google", "web", "activity", "settings"]:
            app.navigate_to(page_name)
            self.assertEqual(app.current_page_widget, app.pages[page_name])
            app.update_idletasks()
            
        # Test drawer open/close
        app.open_drawer("device", {"name": "My Computer", "status": "Online", "capabilities": ["Access files"]})
        app.update_idletasks()
        app.close_drawer()
        app.update_idletasks()
        
        # Clean up window
        app.destroy()

    def test_approval_resolution_and_events(self):
        import threading
        from assistant import confirm
        from assistant import events

        bus = events.EventBus()
        confirm.configure(bus)

        resolved_values = []
        def _ask():
            res = confirm.ask("Delete database file?", origin="gui")
            resolved_values.append(res)

        t = threading.Thread(target=_ask)
        t.start()

        # Check bus received EVENT_CONFIRM_REQUEST
        evt = bus.get_nowait()
        self.assertIsNotNone(evt)
        self.assertEqual(events.EVENT_CONFIRM_REQUEST, evt.event_type)
        req_id = evt.payload["req_id"]
        self.assertEqual("Delete database file?", evt.message)

        # Resolve via confirm.resolve
        confirm.resolve(req_id, True)
        t.join(timeout=2)
        self.assertEqual([True], resolved_values)

    def test_approval_modal_buttons(self):
        import threading
        from assistant import confirm
        from assistant import events
        from gui.widgets.approval_modal import ApprovalModal

        app = VaveDashboardApp()
        try:
            bus = events.EventBus()
            confirm.configure(bus)

            # Test Approve
            resolved = []
            def _ask_true():
                resolved.append(confirm.ask("Approve task step?", origin="gui"))

            t1 = threading.Thread(target=_ask_true)
            t1.start()
            evt = bus.get_nowait()
            req_id = evt.payload["req_id"]

            modal = ApprovalModal(app, {
                "title": "Action Approval",
                "description": "Approve task step?",
                "req_id": req_id
            })
            modal._approve()
            t1.join(timeout=2)
            self.assertEqual([True], resolved)

            # Test Reject
            rejected = []
            def _ask_false():
                rejected.append(confirm.ask("Run dangerous script?", origin="gui"))

            t2 = threading.Thread(target=_ask_false)
            t2.start()
            evt2 = bus.get_nowait()
            req_id2 = evt2.payload["req_id"]

            modal2 = ApprovalModal(app, {
                "title": "Action Approval",
                "description": "Run dangerous script?",
                "req_id": req_id2
            })
            modal2._reject()
            t2.join(timeout=2)
            self.assertEqual([False], rejected)

        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
