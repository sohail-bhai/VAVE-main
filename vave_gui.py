def main(acquire_lock=True):
    instance_lock = None
    if acquire_lock:
        from assistant.reliability.instance import InstanceLock

        instance_lock = InstanceLock("gui")
        if not instance_lock.acquire():
            owner = (
                f" under PID {instance_lock.owner_pid}"
                if instance_lock.owner_pid
                else ""
            )
            print(f"VAVE gui is already running{owner}.")
            return 2
    try:
        try:
            from gui.app import VaveDashboardApp
        except ModuleNotFoundError as error:
            if error.name == "customtkinter":
                print("CustomTkinter is not installed.")
                print("Install dependencies with: pip install -r requirements.txt")
                return 1
            raise

        app = VaveDashboardApp()
        app.mainloop()
        return 0
    finally:
        if instance_lock is not None:
            instance_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
