import argparse
import json
import sys
from pathlib import Path
from .core import Studio, merged_preset
from .models import CATALOG, hardware, install, model_info, uninstall, versions
from .schema import DEFAULT_PRESET, Generation, Processing
from .storage import Fault, Store
from . import runtime


class CommandParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parser():
    root = CommandParser(prog="dthr", description="DTHR.EXE local image workstation")
    root.add_argument("--data-dir", help="Application storage root (or set DTHR_DATA)")
    root.add_argument("--json", action="store_true")
    subs = root.add_subparsers(dest="command", required=True)

    def command(name, help):
        sub = subs.add_parser(name, help=help)
        sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        return sub

    def settings(sub, generate):
        sub.add_argument("--preset", default=DEFAULT_PRESET)
        fields = {**Processing.model_fields, "width": None, "height": None}
        if generate:
            fields.update(Generation.model_fields)
        for k in fields:
            if k in {"palette_size", "transparent"}:
                continue
            field = fields[k]
            kind = type(field.default) if field else int
            flag = "--" + k.replace("_", "-")
            if kind is bool:
                sub.add_argument(
                    flag, action=argparse.BooleanOptionalAction, default=None
                )
            else:
                sub.add_argument(flag, type=kind if kind in (int, float, str) else int)

    gen = command("generate", "Generate and export, reusing compatible originals")
    settings(gen, True)
    gen.add_argument("--force", action="store_true")
    gen.add_argument("--output", type=Path)
    post = command("process", "Process an image or cached original without inference")
    settings(post, False)
    source = post.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--original-id")
    post.add_argument("--output", type=Path, required=True)
    imp = command("import", "Preserve an imported source in the cache")
    imp.add_argument("--input", type=Path, required=True)
    for name in ("models", "cache", "presets"):
        sub = command(name, f"Manage {name}")
        sub.add_argument(
            "action",
            choices=["list", "install", "remove"] if name == "models" else ["list"],
        )
        if name == "models":
            sub.add_argument("model_name", choices=list(CATALOG), nargs="?")
            sub.add_argument(
                "--confirm",
                action="store_true",
                help="Explicit consent for model installation/removal",
            )
    component = command("runtime", "Manage optional local generation support")
    component.add_argument("action", choices=["status", "install"])
    component.add_argument("--confirm", action="store_true")
    component.add_argument(
        "--from-dir",
        type=Path,
        help="Use checksum-verified release archives from this folder",
    )
    command("health", "Show hardware, paths and dependency diagnostics")
    pair = command("pair", "Generate the two daily vertical panels")
    for flag in ("issue", "theme", "left", "right"):
        pair.add_argument("--" + flag, required=flag in ("issue", "theme"), default="")
    pair.add_argument("--seed", type=int, default=123456)
    pair.add_argument("--output-dir", type=Path)
    serve = command("serve", "Run the authenticated local agent API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=7819)
    command("gui", "Open the desktop application").add_argument("project", nargs="?")
    return root


def execute(args):
    store = Store(args.data_dir)
    studio = Studio(store)
    cmd = args.command
    if cmd == "runtime":
        if args.action == "status":
            return runtime.status()
        if not args.confirm:
            raise Fault(
                "confirmation_required",
                "Pass --confirm to install the optional generation runtime",
            )
        return runtime.install(source=args.from_dir)
    if cmd == "gui":
        from .gui import launch

        return launch(studio, args.project)
    if cmd in ("generate", "process"):
        fields = set(Generation.model_fields) | {"width", "height"}
        values = vars(args)
        overrides = {k: values[k] for k in fields if values.get(k) is not None}
        overrides["postprocess"] = {
            k: values[k] for k in Processing.model_fields if values.get(k) is not None
        }
        p = merged_preset(store, args.preset, overrides)
        if cmd == "generate":
            return studio.generate(p, args.output, args.force)
        if args.original_id:
            entry = next(
                (e for e in store.entries() if e["id"] == args.original_id), None
            )
            if not entry:
                raise Fault("missing_source", "Unknown original ID")
            return studio.export(entry, p, args.output)
        return studio.process_file(args.input, p, args.output)
    if cmd == "import":
        entry, hit = store.import_image(args.input)
        return dict(original=entry, cache_hit=hit)
    if cmd == "cache":
        return store.entries()
    if cmd == "presets":
        return [store.preset(n).model_dump() for n in store.preset_names()]
    if cmd == "models":
        if args.action == "list":
            return [model_info(store, n) for n in CATALOG]
        if not args.model_name or not args.confirm:
            raise Fault(
                "confirmation_required",
                "Specify a model and --confirm for install/remove",
            )
        if args.action == "install":
            return install(
                store,
                args.model_name,
                lambda f, n: print(f"{f:.0%} {n}", file=sys.stderr),
            )
        uninstall(store, args.model_name)
        return model_info(store, args.model_name)
    if cmd == "pair":
        return studio.pair(
            args.issue, args.theme, args.left, args.right, args.seed, args.output_dir
        )
    if cmd == "health":
        return dict(
            service="DTHR.EXE",
            storage=str(store.root),
            hardware=hardware(),
            runtime=versions(),
        )
    if cmd == "serve":
        import ipaddress

        addr = ipaddress.ip_address(args.host)
        if not (
            addr.is_loopback
            or (addr.is_private or addr in ipaddress.ip_network("100.64.0.0/10"))
            and not addr.is_unspecified
        ):
            raise Fault(
                "invalid_host",
                "Choose loopback or a specific private interface address",
            )
        import uvicorn
        from .api import create_app

        uvicorn.run(create_app(studio), host=str(addr), port=args.port)
        return {"status": "stopped"}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser().parse_args(argv or ["gui"])
        result = execute(args)
        if args.command != "gui":
            print(
                json.dumps(result, ensure_ascii=True, indent=None if args.json else 2)
            )
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        code = getattr(
            exc,
            "code",
            "io_error"
            if isinstance(exc, OSError)
            else "invalid_request"
            if isinstance(exc, ValueError)
            else "runtime_error",
        )
        error = dict(code=code, message=str(exc))
        print(
            json.dumps(error, ensure_ascii=True)
            if "--json" in argv
            else f"{code}: {exc}",
            file=sys.stderr,
        )
        return 2 if code in {"invalid_request", "invalid_path", "invalid_output"} else 1
