from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
from pathlib import Path
import os
import subprocess
import sys

from models.hv.gui.auth_bar import AuthBar
from models.hv.gui.battery_pack_dummy import BatteryPackDummyGUI
from models.hv.gui.battery_pack_id import BatteryPackIdGUI
from models.hv.gui.bms_id import BmsIdGUI
from models.hv.gui.dummy_id import DummyIdGUI

SCREENS = {
    "Battery Pack Dummy ID": BatteryPackDummyGUI,
    "Battery Pack ID": BatteryPackIdGUI,
    "BMS ID": BmsIdGUI,
    "Dummy ID (manual)": DummyIdGUI,
}


class HVApp(ttk.Frame):


    def __init__(self, parent: tk.Misc, *, session) -> None:
        
        def _resource_path(relative: str) -> Path:
            base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
            return base / relative   
                 
        super().__init__(parent)
        self.session = session

        top_bar = ttk.Frame(self, padding=(12, 8))
        top_bar.pack(fill=tk.X)
        
            # Logo — top-left, before the heading
        try:
            logo_path = _resource_path("assets/logo.png")
            
            if logo_path.exists():
                logo_image = tk.PhotoImage(file=str(logo_path))
                target_height = 32 
                
                if logo_image.height() > target_height:
                    factor = max(1, logo_image.height() // target_height)
                    logo_image = logo_image.subsample(factor, factor)
                    
                logo_label = ttk.Label(top_bar, image=logo_image)
                logo_label.image = logo_image  
                logo_label.pack(side=tk.LEFT, anchor=tk.W, padx=(0, 8))
                
        except tk.TclError:
            pass
        
        ttk.Label(top_bar, text="HV Label Printer — ULTRAVIOLETTE AUTOMATIVE",
                  font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)
        self.auth_bar = AuthBar(top_bar, self.session, self._on_auth_change)
        self.auth_bar.pack(side=tk.RIGHT)
        
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        selector_bar = ttk.Frame(self, padding=(12, 14))
        selector_bar.pack(fill=tk.X)
        selector_bar.columnconfigure(0, weight=1)
        selector_bar.columnconfigure(1, weight=0)
        selector_bar.columnconfigure(2, weight=1)

        selector_group = ttk.Frame(selector_bar)
        selector_group.grid(row=0, column=1)
        ttk.Label(
            selector_group, text="Id Type", font=("Segoe UI", 12, "bold")
        ).pack(side=tk.LEFT, padx=(0, 12))
        self._screen_var = tk.StringVar(value=next(iter(SCREENS)))
        screen_combo = ttk.Combobox(
            selector_group,
            textvariable=self._screen_var,
            values=list(SCREENS.keys()),
            state="readonly",
            width=32,
            font=("Segoe UI", 14),
            justify="center",
        )

        screen_combo.pack(side=tk.LEFT, ipady=8)
        screen_combo.bind("<<ComboboxSelected>>", lambda _e: self._show_screen())

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        self._content_host = ttk.Frame(self)
        self._content_host.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self._current_frame: ttk.Frame | None = None
        self._show_screen()

    def _show_screen(self) -> None: 
        if self._current_frame is not None:
            self._current_frame.destroy()

        frame = ttk.Frame(self._content_host)
        frame.pack(fill=tk.BOTH, expand=True)
        self._current_frame = frame

        screen_cls = SCREENS[self._screen_var.get()]
        screen_cls(frame, is_admin=self.session.is_admin, session=self.session)

    def _on_auth_change(self) -> None:
        self._show_screen()