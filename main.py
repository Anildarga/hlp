from __future__ import annotations

import sys
import traceback
from pathlib import Path
from tkinter import messagebox

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tkinter as tk
# from tkinter import ttk


from common.auth.machine_lock import is_authorized
from common.auth.session import Session
from models.hv.app import HVApp


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ASSETS_DIR = _base_dir() / "models" / "hv" / "assets"


def _install_error_dialog(root: tk.Tk) -> None:
    def _report(exc_type, exc_value, exc_tb):
        traceback.print_exception(exc_type, exc_value, exc_tb)
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))[-1500:]
        try:
            messagebox.showerror("Unexpected error", f"{exc_value}\n\n{detail}", parent=root)
        except Exception:
            pass

    root.report_callback_exception = _report


def main() -> None:
    root = tk.Tk()
    

    if not is_authorized():
        root.withdraw() 
        messagebox.showwarning("Warning", "This Machine is not Authorized")
        sys.exit(1)

    root.title("HV Label Printer — ULTRAVIOLETTE AUTOMATIVE")
    root.geometry("1280x860")
    root.minsize(1000, 600)
    # root.resizable(False,False)
    root.state("zoomed")

    icon_path = ASSETS_DIR / "logo.ico"
    if icon_path.exists():
        try:
            root.iconbitmap(str(icon_path))
        except tk.TclError:
            pass


    _install_error_dialog(root)

    session = Session() # logged out by default — AuthBar shows its own Login button
    HVApp(root, session=session).pack(fill=tk.BOTH, expand=False)
    

    root.mainloop()


if __name__ == "__main__":
    main()