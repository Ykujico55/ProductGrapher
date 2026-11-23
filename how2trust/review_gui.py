"""Local desktop form for the existing review CLI."""

import os
import queue
import subprocess
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:
    tk = None


def _run_cli(args):
    result = subprocess.run([sys.executable, "-X", "utf8", "-m", "how2trust", *args],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, encoding="utf-8", errors="replace",
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise ValueError(result.stderr.strip() or "命令未完成")


def _draft(spec, task, output, model_config):
    with tempfile.TemporaryDirectory(prefix="how2trust-draft-") as temporary:
        if task:
            spec = Path(temporary) / "task.md"
            spec.write_text(task, encoding="utf-8")
        argv = ["draft", "--spec", str(spec), "-o", str(output)]
        if model_config:
            argv.extend(["--llm-config", model_config])
        _run_cli(argv)


def _generate(repository, base_option, baseline, spec, task, output, run):
    with tempfile.TemporaryDirectory(prefix="how2trust-task-") as temporary:
        if task:
            spec = Path(temporary) / "task.md"
            spec.write_text(task, encoding="utf-8")
        argv = ["review", repository,
                base_option, baseline, "--spec", str(spec), "-o", str(output),
                "--json", str(output.with_suffix(".json"))]
        if run:
            argv.append("--run-checks")
        _run_cli(argv)


class ReviewForm:
    def __init__(self, root, initial_repo=None):
        self.root = root
        root.title("How2Trust · 交付验收")
        root.columnconfigure(0, weight=1)
        root.minsize(620, 500)
        frame = ttk.Frame(root, padding=18)
        frame.grid(sticky="nsew")
        frame.columnconfigure(1, weight=1)
        self.repo = tk.StringVar(value=str(Path(initial_repo).resolve()) if initial_repo else "")
        self.base_mode = tk.StringVar(value="git")
        self.git = tk.StringVar(value="HEAD")
        self.folder = tk.StringVar()
        self.spec = tk.StringVar()
        self.spec_mode = tk.StringVar(value="file")
        self.output = tk.StringVar()
        self.model_config = tk.StringVar()
        self.running = False
        self.checks = tk.BooleanVar(value=False)
        self.status = tk.StringVar()
        self.results = queue.Queue()

        def row(index, label, variable, picker=None):
            ttk.Label(frame, text=label).grid(row=index, column=0, sticky="w", padx=(0, 10))
            entry = ttk.Entry(frame, textvariable=variable, width=58)
            entry.grid(row=index, column=1, sticky="ew", pady=3)
            browse = None
            if picker:
                browse = ttk.Button(frame, text="选择…", command=picker)
                browse.grid(row=index, column=2, padx=(6, 0))
            return entry, browse

        def choices(index, label, variable, options, command):
            ttk.Label(frame, text=label).grid(row=index, column=0, sticky="w", pady=(10, 3))
            group = ttk.Frame(frame)
            group.grid(row=index, column=1, sticky="w", pady=(10, 3))
            for name, value in options:
                ttk.Radiobutton(group, text=name, variable=variable, value=value, command=command).pack(side="left", padx=(0, 12))

        ttk.Label(frame, text="交付验收", font=("Microsoft YaHei UI", 18, "bold")).grid(row=0, columnspan=3, sticky="w", pady=(0, 12))
        row(1, "交付仓库", self.repo, lambda: self.pick(self.repo, filedialog.askdirectory))
        choices(2, "开工基线", self.base_mode, [("Git 提交或标签", "git"), ("旧版源码目录", "dir")], self.sync)
        self.git_entry, _ = row(3, "Git 基线", self.git)
        self.dir_entry, self.dir_btn = row(4, "旧版目录", self.folder,
                                          lambda: self.pick(self.folder, filedialog.askdirectory))
        choices(5, "原始要求", self.spec_mode, [("选择清单文件", "file"), ("粘贴任务文本", "text")], self.sync)
        self.spec_entry, self.spec_btn = row(6, "验收清单", self.spec, lambda: self.pick(
            self.spec, filedialog.askopenfilename, filetypes=[("任务或验收清单", "*.json *.md *.txt"), ("所有文件", "*.*")]))
        self.task = tk.Text(frame, width=40, height=5, wrap="word", relief="solid", borderwidth=1)
        self.task.grid(row=7, column=1, columnspan=2, sticky="ew", pady=4)
        self.task_hint = tk.StringVar()
        ttk.Label(frame, textvariable=self.task_hint).grid(row=8, column=1, columnspan=2, sticky="w")
        row(9, "模型配置（可选）", self.model_config, lambda: self.pick(
            self.model_config, filedialog.askopenfilename, filetypes=[("模型配置", "*.json")]))
        self.draft_btn = ttk.Button(frame, text="从原始要求生成场景草案…", command=self.start_draft)
        self.draft_btn.grid(row=10, column=1, sticky="w", pady=(4, 2))
        self.check_btn = ttk.Checkbutton(frame, text="运行所选 JSON 清单中的检查（命令使用当前用户的本机权限）",
                                         variable=self.checks)
        self.check_btn.grid(row=11, column=1, columnspan=2, sticky="w", pady=8)
        row(12, "HTML 报告", self.output, lambda: self.pick(self.output, filedialog.asksaveasfilename,
            defaultextension=".html", filetypes=[("HTML 报告", "*.html")], initialfile="交付验收.html"))
        ttk.Label(frame, text="完成后打开 HTML，同时在相同目录保存同名 JSON。").grid(row=13, column=1, sticky="w")
        self.submit = ttk.Button(frame, text="生成验收报告", command=self.start)
        self.submit.grid(row=14, column=1, sticky="w", pady=(15, 6))
        ttk.Label(frame, textvariable=self.status, wraplength=530).grid(row=15, columnspan=3, sticky="w")
        self.spec.trace_add("write", lambda *_: self.sync())
        self.sync()

    def pick(self, target, dialog, **options):
        value = dialog(parent=self.root, **options)
        if value:
            target.set(value)

    def sync(self):
        is_git = self.base_mode.get() == "git"
        is_file = self.spec_mode.get() == "file"
        self.git_entry.configure(state="normal" if is_git else "disabled")
        for widget in (self.dir_entry, self.dir_btn):
            widget.configure(state="disabled" if is_git else "normal")
        for widget in (self.spec_entry, self.spec_btn):
            widget.configure(state="normal" if is_file else "disabled")
        self.task.configure(state="disabled" if is_file else "normal")
        json_file = is_file and Path(self.spec.get()).suffix.lower() == ".json"
        self.task_hint.set("可从原始文本起草验收场景。留空模型配置时使用环境配置。" if not json_file else "")
        self.draft_btn.configure(state="disabled" if self.running or json_file else "normal")
        self.check_btn.configure(state="normal" if json_file else "disabled")
        if not json_file:
            self.checks.set(False)

    def start_draft(self):
        spec, task = self.requirements()
        if not (spec or task):
            self.status.set("请先选择原始要求文件，或粘贴任务文本。")
            return
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json",
                                            filetypes=[("场景草案", "*.json")], initialfile="验收场景草案.json")
        if not path:
            return
        output = Path(path).resolve()
        config = self.model_config.get().strip()
        if any(Path(source).resolve() == output for source in (spec, config) if source):
            self.status.set("请选择单独的草案文件，避免覆盖原始要求或模型配置。")
            return
        if output.exists():
            self.status.set("场景草案需要新的 JSON 文件名。")
            return
        args = (str(Path(spec).resolve()) if spec else "", task, output,
                str(Path(config).expanduser().resolve()) if config else "")
        self.run_job("正在生成场景草案…", _draft, args, output)

    def requirements(self):
        if self.spec_mode.get() == "text":
            return "", self.task.get("1.0", "end").strip()
        return self.spec.get().strip(), ""

    def start(self):
        pick_dir = self.base_mode.get() == "dir"
        baseline = (self.folder if pick_dir else self.git).get().strip()
        spec, task = self.requirements()
        path = self.output.get().strip()
        if not self.repo.get().strip() or not baseline or not (spec or task) or not path:
            self.status.set("请填写交付目录、基线、原始要求和报告保存位置。")
            return
        output = Path(path).expanduser().resolve()
        if output.suffix.lower() != ".html":
            self.status.set("请选择扩展名为 .html 的报告文件。")
            return
        if spec and Path(spec).resolve() == output.with_suffix(".json"):
            self.status.set("请更换报告名，避免生成的 JSON 覆盖验收清单。")
            return
        existing = [p.name for p in (output, output.with_suffix(".json")) if p.exists()]
        if existing and not messagebox.askyesno("覆盖报告", "将覆盖 " + "、".join(existing) + "。继续生成？", parent=self.root):
            return
        args = (str(Path(self.repo.get().strip()).resolve()), "--base-dir" if pick_dir else "--base",
                str(Path(baseline).resolve()) if pick_dir else baseline, str(Path(spec).resolve()) if spec else "",
                task, output, self.checks.get())
        self.run_job("正在对照基线并生成报告…", _generate, args, output)

    def run_job(self, status, operation, args, output):
        self.running = True
        self.submit.configure(state="disabled")
        self.draft_btn.configure(state="disabled")
        self.status.set(status)

        def work():
            try:
                operation(*args)
            except (OSError, ValueError) as exc:
                self.results.put((None, str(exc), operation))
            else:
                self.results.put((output, "", operation))

        threading.Thread(target=work, daemon=True).start()
        self.root.after(150, self.poll)

    def poll(self):
        try:
            output, error, operation = self.results.get_nowait()
        except queue.Empty:
            self.root.after(150, self.poll)
            return
        self.running = False
        self.sync()
        self.submit.configure(state="normal")
        if error:
            self.status.set("生成失败。修改输入后可以重试。")
            messagebox.showerror("生成失败", error, parent=self.root)
            return
        if operation is _draft:
            self.status.set(f"场景草案已保存　{output}")
            self.review_draft(output)
            return
        self.status.set(f"已生成报告　{output}")
        webbrowser.open(output.as_uri())

    def review_draft(self, output):
        from .acceptance import load_contract

        window = tk.Toplevel(self.root)
        window.title("审阅验收场景")
        window.transient(self.root)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)
        ttk.Label(window, text="逐项核对预期结果，可在 checks 中填写自己的检查命令。").grid(
            row=0, column=0, sticky="w", padx=12, pady=10)
        editor = tk.Text(window, width=82, height=22, wrap="word", undo=True)
        editor.grid(row=1, column=0, sticky="nsew", padx=12)
        editor.insert("1.0", output.read_text(encoding="utf-8-sig"))
        buttons = ttk.Frame(window)
        buttons.grid(row=2, column=0, sticky="e", padx=12, pady=10)
        ttk.Button(buttons, text="关闭", command=window.destroy).pack(side="left", padx=4)

        def use_draft():
            content = editor.get("1.0", "end-1c")
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    candidate = Path(tmp) / "contract.json"
                    candidate.write_text(content, encoding="utf-8")
                    load_contract(candidate)
                output.write_text(content, encoding="utf-8")
            except (OSError, ValueError) as exc:
                messagebox.showerror("草案未能载入", str(exc), parent=window)
                return
            self.spec_mode.set("file")
            self.checks.set(False)
            self.spec.set(str(output))
            self.status.set(f"已载入审阅后的场景　{output.name}")
            window.destroy()

        ttk.Button(buttons, text="保存并载入验收", command=use_draft).pack(side="left")
        window.grab_set()


def launch(initial_repo=None) -> int:
    fallback = "可用命令行生成报告：python -m how2trust review --help"
    if tk is None:
        print(f"当前 Python 未安装 Tk。{fallback}", file=sys.stderr)
        return 1
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"无法初始化 Tk 桌面窗口（{exc}）。{fallback}", file=sys.stderr)
        return 1
    ReviewForm(root, initial_repo)
    root.mainloop()
    return 0
