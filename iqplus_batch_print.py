import argparse
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path
from tkinter import END, BOTH, DISABLED, NORMAL, Button, Frame, Label, Listbox, Tk, filedialog, messagebox
from tkinter import ttk
from PIL import Image, ImageTk

# Tk's Windows shell folder picker requires an STA main thread. Set this
# before importing pywinauto/comtypes, which otherwise initializes MTA.
sys.coinit_flags = 2
from pywinauto import Desktop, keyboard
from pywinauto.timings import TimeoutError as PywinautoTimeoutError


APP_TITLE = "iQ+ Batch PDF Printer"
DEFAULT_PRINTER = "Microsoft Print to PDF"
VIEWER_TITLE_RE = r".*Waveform Viewer.*"


class BatchError(Exception):
    pass


def pdf_is_complete(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                return False
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 1024))
            return b"%%EOF" in stream.read()
    except OSError:
        return False


def clean_pdf_name(cdf_path: Path) -> str:
    return cdf_path.with_suffix(".pdf").name


def wait_for_file_stable(path: Path, timeout: float = 180.0, stable_seconds: float = 2.0) -> None:
    deadline = time.time() + timeout
    last_size = -1
    stable_from = None

    while time.time() < deadline:
        if path.exists():
            size = path.stat().st_size
            if size > 0 and size == last_size:
                if stable_from is None:
                    stable_from = time.time()
                elif time.time() - stable_from >= stable_seconds and pdf_is_complete(path):
                    return
            else:
                stable_from = None
                last_size = size
        time.sleep(0.4)

    raise BatchError(f"Timed out waiting for PDF to finish: {path}")


def wait_window(title_re: str, timeout: float, process=None):
    # Legacy common dialogs are sometimes absent from the UIA top-level tree.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for backend in ("win32", "uia"):
            criteria = dict(title_re=title_re, visible_only=True, enabled_only=False)
            if process is not None:
                criteria["process"] = process
            try:
                matches = Desktop(backend=backend).windows(**criteria)
                if len(matches) == 1:
                    return matches[0]
            except Exception:
                pass
        time.sleep(0.25)
    raise BatchError(f"Could not detect window {title_re!r} within {timeout:g} seconds.")


def close_iqplus_windows() -> None:
    desktop = Desktop(backend="uia")
    for win in desktop.windows(title_re=VIEWER_TITLE_RE):
        try:
            win.set_focus()
            keyboard.send_keys("%{F4}")
            time.sleep(0.8)
        except Exception:
            pass


def set_pdf_path_in_save_dialog(output_pdf: Path) -> None:
    save = wait_window(r"^(Save Print Output As|Save As)$", 60)
    save.set_focus()
    time.sleep(0.3)

    # The modern shell dialog exposes its field name through UIA, while the
    # native Edit text is initially empty. Rebind the same dialog by handle.
    filename = None
    deadline = time.monotonic() + 15
    while filename is None and time.monotonic() < deadline:
        for backend in ("uia", "win32"):
            dialog = Desktop(backend=backend).window(handle=save.handle).wrapper_object()
            edits = dialog.descendants(control_type="Edit") if backend == "uia" else dialog.descendants(class_name="Edit")
            for edit in edits:
                if backend == "uia":
                    matches = edit.element_info.name.replace("&", "").strip().rstrip(":").lower() == "file name"
                else:
                    matches = edit.control_id() in (1148, 1001)
                if matches:
                    filename = edit
                    break
            if filename is not None:
                break
        if filename is None:
            time.sleep(0.25)
    if filename is None:
        raise BatchError("The PDF save dialog appeared, but its filename field could not be identified.")
    filename.set_edit_text(str(output_pdf.resolve()))
    entered = filename.get_value() if filename.backend.name == "uia" else filename.window_text()
    if entered != str(output_pdf.resolve()):
        raise BatchError("The PDF filename field did not retain the full output path.")
    submit_pdf_save(save, output_pdf)


def submit_pdf_save(save, output_pdf: Path, timeout=45.0) -> None:
    deadline = time.monotonic() + timeout
    last_click = -float("inf")
    attempts = 0
    while time.monotonic() < deadline:
        dialog = Desktop(backend="win32").window(handle=save.handle)
        if not dialog.exists() or not dialog.is_visible():
            return
        if time.monotonic() - last_click >= 3.0 and dialog.is_enabled():
            buttons = dialog.descendants(class_name="Button")
            button = next((b for b in buttons if b.window_text().replace("&", "").strip() == "Save"), None)
            if button is not None and button.is_enabled() and button.is_visible():
                # Reacquire the same dialog and button on each attempt. Native
                # click messages can be ignored by the shell before it is ready.
                if attempts == 0:
                    button.click()
                else:
                    dialog.set_focus()
                    button.click_input()
                attempts += 1
                last_click = time.monotonic()
        time.sleep(0.25)
    raise BatchError(f"Windows did not accept Save for {output_pdf}. The batch stopped.")


def ensure_printer(print_dialog) -> None:
    try:
        combos = print_dialog.descendants(class_name="ComboBox") if print_dialog.backend.name == "win32" else print_dialog.descendants(control_type="ComboBox")
        for combo in combos:
            try:
                if DEFAULT_PRINTER.lower() in combo.window_text().lower():
                    return
            except Exception:
                continue
        for combo in combos:
            try:
                combo.select(DEFAULT_PRINTER)
                return
            except Exception:
                continue
    except Exception:
        pass
    raise BatchError("Could not select Microsoft Print to PDF. No print job was submitted.")


def print_one_cdf(cdf_path: Path, output_pdf: Path, log=print) -> None:
    if not cdf_path.exists():
        raise BatchError(f"CDF file not found: {cdf_path}")
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    if output_pdf.exists():
        output_pdf.unlink()

    log(f"Opening in iQ+: {cdf_path.name}")
    os.startfile(str(cdf_path))
    viewer = wait_window(VIEWER_TITLE_RE, 45)
    viewer.set_focus()
    time.sleep(2.0)

    log("Sending Print Full Record Length command")
    keyboard.send_keys("^p")

    try:
        print_dialog = wait_window(r"^Print$", 45, process=viewer.process_id())
    except (PywinautoTimeoutError, BatchError) as exc:
        raise BatchError("The iQ+ Print dialog did not appear after Ctrl+P.") from exc

    print_dialog.set_focus()
    ensure_printer(print_dialog)
    time.sleep(0.3)

    try:
        buttons = print_dialog.descendants(class_name="Button") if print_dialog.backend.name == "win32" else print_dialog.descendants(control_type="Button")
        ok = next(b for b in buttons if b.window_text().replace("&", "") == "OK")
        ok.click() if print_dialog.backend.name == "win32" else ok.invoke()
    except Exception as exc:
        raise BatchError("Could not activate the Print dialog OK button.") from exc

    log(f"Saving PDF: {output_pdf.resolve()}")
    set_pdf_path_in_save_dialog(output_pdf)
    wait_for_file_stable(output_pdf)

    log(f"Done: {output_pdf}")
    try:
        viewer.set_focus()
        keyboard.send_keys("%{F4}")
    except Exception:
        pass
    time.sleep(1.0)


def expand_inputs(paths: list[str]) -> list[Path]:
    cdfs: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            cdfs.extend(sorted(path.rglob("*.cdf")))
        elif path.suffix.lower() == ".cdf":
            cdfs.append(path)
    return cdfs


def convert_batch(cdfs: list[Path], output_dir: Path | None, log=print) -> int:
    if not cdfs:
        raise BatchError("No .cdf files were selected.")

    log("Please leave iQ+ and the print dialogs in front while the batch runs.")
    close_iqplus_windows()
    completed = 0

    for index, cdf_path in enumerate(cdfs, start=1):
        target_dir = output_dir if output_dir else cdf_path.parent
        output_pdf = target_dir / clean_pdf_name(cdf_path)
        log(f"[{index}/{len(cdfs)}] {cdf_path.name}")
        print_one_cdf(cdf_path, output_pdf, log=log)
        completed += 1

    log(f"Finished {completed} file(s).")
    return completed


class BatchGui:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("760x440")
        self.root.geometry("1000x720")
        self.root.minsize(800, 600)
        self.root.configure(bg="#f3f4f5")
        self.root.option_add("*Font", "{Segoe UI} 10")
        self.root.option_add("*Button.Cursor", "hand2")
        assets = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets"
        if (assets / "q.ico").exists():
            self.root.iconbitmap(str(assets / "q.ico"))
        header = Frame(root, bg="white")
        header.pack(fill="x")
        self.brand_image = ImageTk.PhotoImage(Image.open(assets / "Qualitrol-logo.png").resize((280, 70)))
        Label(header, image=self.brand_image, bg="white").pack(side="left", padx=24, pady=22)
        title = Frame(header, bg="white")
        title.pack(side="right", padx=24)
        Label(title, text="CDF to PDF", font=("Segoe UI", 23, "bold"), bg="white", fg="#343a3c").pack(anchor="e")
        Label(title, text="iQ+ Batch Printer", bg="white", fg="#6a7175").pack(anchor="e")
        Frame(root, bg="#da0712", height=3).pack(fill="x")
        self.files: list[Path] = []
        self.output_dir: Path | None = None
        self.messages: queue.Queue[str] = queue.Queue()
        self.scan_results = queue.Queue()
        self.scanning = False

        top = Frame(root, bg="#f3f4f5")
        top.pack(fill="x", padx=24, pady=20)

        Button(top, text="Add CDF files", command=self.add_files, width=18).pack(side="left", padx=(0, 8))
        Button(top, text="Add folder", command=self.add_folder, width=14).pack(side="left", padx=(0, 8))
        Button(top, text="Output folder", command=self.choose_output, width=14).pack(side="left", padx=(0, 8))
        self.start_button = Button(top, text="Start batch", command=self.start, width=14)
        self.start_button.pack(side="left")
        for button in top.winfo_children():
            button.configure(bg="white", fg="#343a3c", relief="flat", bd=0, pady=10, activebackground="#e4e6e8")
        self.start_button.configure(bg="#da0712", fg="white", activebackground="#b50610", activeforeground="white")

        self.output_label = Label(root, text="Output: beside each CDF", anchor="w")
        self.output_label.pack(fill="x", padx=12)
        self.output_label.configure(bg="#f3f4f5", fg="#60696e", padx=12)
        self.queue_label = Label(root, text="FILES  /  0", bg="#f3f4f5", fg="#343a3c", font=("Segoe UI", 10, "bold"), anchor="w")
        self.queue_label.pack(fill="x", padx=24, pady=(18, 4))

        self.listbox = Listbox(root, height=9)
        self.listbox.pack(fill=BOTH, expand=True, padx=12, pady=(8, 6))
        self.listbox.configure(bg="white", fg="#343a3c", bd=0, highlightthickness=1, highlightbackground="#dce0e2", selectbackground="#fbe4e5", selectforeground="#a10610", activestyle="none", font=("Segoe UI", 11))
        scroll = ttk.Scrollbar(self.listbox, orient="vertical", command=self.listbox.yview)
        scroll.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=scroll.set)

        Label(root, text="ACTIVITY", anchor="w", bg="#f3f4f5", fg="#343a3c", font=("Segoe UI", 10, "bold")).pack(fill="x", padx=24, pady=(14, 4))
        self.logbox = Listbox(root, height=8)
        self.logbox.pack(fill=BOTH, expand=True, padx=12, pady=(0, 12))
        self.logbox.configure(bg="#eef0f1", fg="#505b61", bd=0, highlightthickness=0, font=("Consolas", 9))
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Batch.Horizontal.TProgressbar", background="#da0712", troughcolor="#e3e6e8", borderwidth=0)
        self.progress = ttk.Progressbar(root, style="Batch.Horizontal.TProgressbar", maximum=1)
        self.progress.pack(fill="x", padx=24, pady=(0, 8))
        self.status = Label(root, text="Ready", bg="#f3f4f5", fg="#60696e", anchor="w")
        self.status.pack(fill="x", padx=24, pady=(0, 14))
        self.completed = 0

        self.root.after(200, self.drain_messages)

    def log(self, message: str) -> None:
        self.messages.put(message)

    def drain_messages(self) -> None:
        try:
            paths, error = self.scan_results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.scanning = False
            self.start_button.config(state=NORMAL)
            if error:
                self.log(f"Folder scan failed: {error}")
            else:
                self.append_files(paths)
                self.log(f"Folder scan complete: {len(paths)} CDF file(s) found.")
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            self.logbox.insert(END, message)
            self.logbox.see(END)
            self.status.config(text=message)
            if message.startswith("Done:"):
                self.completed += 1
                self.progress.config(value=self.completed)
        self.root.after(200, self.drain_messages)

    def add_files(self) -> None:
        selected = filedialog.askopenfilenames(parent=self.root, title="Select CDF files", filetypes=[("CDF files", "*.cdf")])
        self.add_paths(selected)

    def add_folder(self) -> None:
        if self.scanning:
            return
        selected = filedialog.askdirectory(parent=self.root, title="Select a folder containing CDF files", mustexist=True)
        if selected:
            self.scanning = True
            self.start_button.config(state=DISABLED)
            self.log(f"Scanning folder: {selected}")
            threading.Thread(target=self.scan_folder, args=(selected,), daemon=True).start()

    def scan_folder(self, selected) -> None:
        try:
            self.scan_results.put((expand_inputs([selected]), None))
        except Exception as exc:
            self.scan_results.put(([], str(exc)))

    def add_paths(self, paths) -> None:
        self.append_files(expand_inputs(list(paths)))

    def append_files(self, paths) -> None:
        existing = {str(path).lower() for path in self.files}
        for path in paths:
            key = str(path).lower()
            if key not in existing:
                self.files.append(path)
                existing.add(key)
                self.listbox.insert(END, str(path))
        self.queue_label.config(text=f"FILES  /  {len(self.files)}")

    def choose_output(self) -> None:
        selected = filedialog.askdirectory(parent=self.root, title="Select output folder", mustexist=True)
        if selected:
            self.output_dir = Path(selected)
            self.output_label.config(text=f"Output: {self.output_dir}")

    def start(self) -> None:
        if not self.files:
            messagebox.showerror(APP_TITLE, "Add at least one .cdf file first.")
            return
        self.start_button.config(state=DISABLED)
        self.completed = 0
        self.progress.config(maximum=len(self.files), value=0)
        worker = threading.Thread(target=self.run_batch, daemon=True)
        worker.start()

    def run_batch(self) -> None:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            convert_batch(self.files, self.output_dir, log=self.log)
            self.log("Batch complete.")
        except Exception as exc:
            self.log(f"ERROR: {exc}")
            error = str(exc)
            self.root.after(0, lambda: messagebox.showerror(APP_TITLE, error))
        finally:
            pythoncom.CoUninitialize()
            self.root.after(0, lambda: self.start_button.config(state=NORMAL))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Batch-print iQ+ CDF files to PDF using iQ+ itself.")
    parser.add_argument("paths", nargs="*", help="CDF files or folders. Drag-and-drop files onto the EXE also works.")
    parser.add_argument("-o", "--output", help="Output folder. Defaults to saving beside each CDF.")
    parser.add_argument("--gui", action="store_true", help="Open the file picker GUI.")
    args = parser.parse_args(argv)

    if args.gui or not args.paths:
        root = Tk()
        app = BatchGui(root)
        app.add_paths(args.paths)
        root.mainloop()
        return 0

    try:
        cdfs = expand_inputs(args.paths)
        output_dir = Path(args.output) if args.output else None
        convert_batch(cdfs, output_dir)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
