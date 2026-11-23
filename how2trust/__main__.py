"""Command-line entry point for the local repository viewer."""

import argparse
import configparser
import json
import os
import sys
from pathlib import Path
from threading import Lock

from .discussion import Discussion
from .llm import ModelClient, profiles
from .outputs import existing_export
from .project import build_graph, canonical, digest
from .web import make_server, standalone


def generate_with_progress(store):
    from .generation_progress import GenerationProgress

    stages = {"prepare": "准备源码", "overview": "整理业务总览", "grouping": "归并业务分组",
              "review": "复核总览与源码", "saving": "校验并保存业务图"}
    phases = {"reading": "解读源码", "reviewing": "复核讲解", "done": "完成",
              "cached": "复用已校验结果", "failed": "失败"}
    tracker = GenerationProgress()
    tracker.start()
    output_lock = Lock()

    def report(stage, **fields):
        with output_lock:
            tracker.update(stage, **fields)
            if stage == "batches":
                state = tracker.snapshot()
                file_list = "、".join(fields["files"])
                message = (f"源码批次已完成 {state['completed']}/{state['total']} · "
                           f"第 {fields['batch']} 批{phases[fields['phase']]} · {file_list}")
            else:
                message = "复用已保存的业务讲解" if fields.get("phase") == "cached" else stages[stage]
            print(message, file=sys.stderr, flush=True)

    store.generate(progress=report)
    print("业务图生成完成", file=sys.stderr, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="how2trust", description="从 Python 源码生成多层架构图，用可配置的模型解释业务职责并追溯到源码")
    commands = parser.add_subparsers(dest="action", required=True)
    serve = commands.add_parser("serve", help="启动交互查看器，支持批注和模型问答")
    export = commands.add_parser("export", help="导出独立离线 HTML")
    wizard = commands.add_parser("review-gui", help="打开本机交付验收向导")
    wizard.add_argument("repository", type=Path, nargs="?", help="预选的交付目录")
    review = commands.add_parser("review", help="对照开工要求和基线验收本次交付")
    draft = commands.add_parser("draft", help="根据原始需求起草业务验收场景，供用户核定")
    draft.add_argument("--spec", required=True, type=Path, help="需求原文（纯文本或 Markdown）")
    draft.add_argument("-o", "--output", type=Path, required=True, help="待审阅的 JSON 清单")
    for cmd in (serve, export, review):
        cmd.add_argument("repository", type=Path, help="要分析的 Python 仓库目录")
    baseline = review.add_mutually_exclusive_group(required=True)
    baseline.add_argument("--base", help="开工前的 Git 提交或标签")
    baseline.add_argument("--base-dir", type=Path, help="开工前的源码目录（适用于无 Git 的项目）")
    review.add_argument("--head", help="交付版本的 Git 提交；省略时检查当前工作目录")
    review.add_argument("--spec", type=Path, required=True, help="原始任务 Markdown/文本或 JSON 验收清单")
    review.add_argument("--run-checks", action="store_true", help="在临时源码副本中运行指定清单的基线和交付检查")
    review.add_argument("-o", "--output", type=Path, required=True, help="独立验收报告 HTML")
    review.add_argument("--json", type=Path, help="同时保存机器可读报告，可用 serve --review 打开")
    serve.add_argument("--review", type=Path, help="加载此版本的交付验收报告 JSON")
    export.add_argument("-o", "--output", type=Path, required=True, help="输出 .html 文件")
    export.add_argument("--json", type=Path, help="同时写入包含证据与源码的图 JSON")
    serve.add_argument("--port", type=int, default=8765, help="本地端口，0 表示自动分配")
    serve.add_argument("--open", action="store_true", help="在默认浏览器打开查看器")
    serve.add_argument("--db", type=Path, help="批注和会话的 SQLite 文件")
    for cmd in (serve, export):
        cmd.add_argument("--with-semantics", action="store_true", help="生成业务讲解，已有同一快照的讲解时直接复用")
        cmd.add_argument("--semantics", type=Path, help="从指定的 JSON 文件载入业务讲解；生成时也保存到此处")
        cmd.add_argument("--requirements", type=Path, help="生成讲解时参考的原始需求文本")
    for cmd in (serve, export, draft):
        cmd.add_argument("--llm-config", type=Path, help="JSON 配置，可为语义、场景、问答指定独立供应商")
        cmd.add_argument("--base-url", help="单一模型 API 根地址，含 /v1 等前缀")
        cmd.add_argument("--model", help="模型服务提供的模型名称")
        cmd.add_argument("--key-env", dest="api_key_env", help="API 密钥的环境变量名")
        cmd.add_argument("--provider", help="单供应商模式的显示名称")
        cmd.add_argument("--protocol", choices=("openai", "anthropic"), help="API 请求协议")
    args = parser.parse_args(argv)
    try:
        if args.action == "review-gui":
            from .review_gui import launch
            return launch(args.repository)
        if args.action == "review":
            from .review import build_report
            from .review_html import render_report

            if args.output.suffix.lower() != ".html" or (args.json and args.json.suffix.lower() != ".json"):
                raise ValueError("请将验收报告写入 .html 和可选的 .json 文件")
            if args.spec.resolve() in {p.resolve() for p in (args.json, args.output) if p}:
                raise ValueError("报告不能覆盖原始验收清单")
            report = build_report(args.repository, args.spec, base=args.base, head=args.head,
                                  base_dir=args.base_dir, run=args.run_checks,
                                  outputs=[p for p in (args.output, args.json) if p])
            data = render_report(report)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(data)
            if args.json:
                args.json.parent.mkdir(parents=True, exist_ok=True)
                args.json.write_bytes(canonical(report).encode("utf-8"))
            print(args.output.resolve())
            for item in report["requirements"]:
                print(f"{item['id']}  {item['status']}  {item['text']}")
            return 0
        options = {k: getattr(args, k) for k in ("base_url", "model", "api_key_env", "provider", "protocol")
                   if getattr(args, k) is not None}
        config_path = args.llm_config or (None if options else os.getenv("HOW2TRUST_LLM_CONFIG"))
        state_dir = Path(os.getenv("LOCALAPPDATA") or os.getenv("XDG_DATA_HOME") or Path.home() / ".local/share")
        page_settings = state_dir / "how2trust" / "workbench-models.json"
        replay_or_static = args.action == "export" and not args.with_semantics
        stage_config = {stage: {} for stage in ("semantics", "chat")} if replay_or_static else profiles(config_path, options)
        if args.action == "serve" and not config_path and not options and page_settings.is_file():
            stage_config.update(json.loads(page_settings.read_text(encoding="utf-8")))
        if args.action == "draft":
            from .scenarios import draft_scenarios
            if args.output.suffix.lower() != ".json" or args.output.exists():
                raise ValueError("场景草案请指定一个新的 .json 文件，保留已审定的清单。")
            contract = draft_scenarios(args.spec.read_text(encoding="utf-8-sig"), ModelClient(stage_config["scenarios"]))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as output:
                output.write(json.dumps(contract, ensure_ascii=False, indent=2) + "\n")
            print(f"待审阅的业务场景：{args.output.resolve()}")
            return 0
        report = None
        if args.action == "serve" and args.review:
            from .review import graph_for_report
            report = json.loads(args.review.read_text(encoding="utf-8-sig"))
            if not isinstance(report, dict) or report.get("schema") != "how2trust.review.v1":
                raise ValueError("无法识别验收报告格式")
            if report.get("fingerprint") != digest({k: v for k, v in report.items() if k != "fingerprint"}):
                raise ValueError("验收报告内容与指纹不一致，请重新导出")
            graph = graph_for_report(args.repository, report)
        else:
            graph = build_graph(args.repository)
        from .semantic_store import SemanticStore
        requirements = args.requirements.read_text(encoding="utf-8-sig") if args.requirements else ""
        if args.semantics and args.semantics.suffix.lower() != ".json":
            raise ValueError("--semantics 需要 .json 文件")
        store = SemanticStore(graph, ModelClient(stage_config["semantics"]), requirements,
                              state_dir / "how2trust" / "semantics", args.semantics)
        if args.action == "export":
            if args.requirements and not (args.with_semantics or args.semantics):
                raise ValueError("从需求生成讲解请加 --with-semantics；离线重放只需 --semantics。")
            paths = [args.output] + ([args.json] if args.json else [])
            destinations = [*paths, *([args.semantics] if args.semantics else [])]
            if len({p.resolve() for p in destinations}) != len(destinations):
                raise ValueError("HTML、图 JSON、业务讲解需要分别指定文件名")
            repository = args.repository.resolve()
            inputs = [Path(p).resolve() for p in (config_path, args.requirements) if p]
            for path in paths:
                output = path.resolve()
                if (output in inputs or path.suffix.lower() in {".py", ".ini"} or
                        (output.is_relative_to(repository) and output.exists() and existing_export(output) != "graph")):
                    raise ValueError(f"请选择输出文档路径，避免覆盖源码或配置：{path}")
            if args.with_semantics:
                generate_with_progress(store)
            elif args.semantics and not store.load():
                raise ValueError("指定的业务讲解不存在。首次生成请加 --with-semantics。")
            document = standalone(graph)
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(document)
            if args.json:
                args.json.write_bytes(canonical(graph).encode("utf-8"))
            print(args.output.resolve())
            return 0
        if not 0 <= args.port <= 65535:
            raise ValueError("端口范围为 0 到 65535")
        if args.with_semantics:
            generate_with_progress(store)
        else:
            store.load()
        db = args.db or state_dir / "how2trust" / "discussions.sqlite3"
        db.parent.mkdir(parents=True, exist_ok=True)
        discussion = Discussion(db, graph, stage_config["chat"], review=report)
        from .model_settings import WorkbenchModels
        settings = WorkbenchModels(page_settings, stage_config, store, discussion)
        with make_server(graph, discussion, args.port, review=report, semantics=store, models=settings) as server:
            print(f"How2Trust · {graph['name']}\nhttp://127.0.0.1:{server.server_port}/", flush=True)
            if args.open:
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{server.server_port}/" + ("review" if report else ""))
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
        return 0
    except (OSError, UnicodeError, ValueError, RuntimeError, configparser.Error) as exc:
        print(f"How2Trust: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
