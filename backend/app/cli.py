"""``parker`` — the one CLI entrypoint for the engine.

Subcommands:

- ``serve``   — the FastAPI engine (what the desktop shell spawns)
- ``talk``    — the continuous voice loop (second sidecar process)
- ``onboard`` — terminal fallback for the desktop onboarding wizard
- ``doctor``  — is this machine ready? (``--json`` for scripts)
- ``download-model`` — fetch whisper weights to PARKER_HOME/models
- ``version``

Dev flows keep their make targets (``make run``, ``make talk-loop``);
those wrappers and this CLI drive the same functions.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
import time

from app import __version__

SERVE_DEFAULT_HOST = "127.0.0.1"
SERVE_DEFAULT_PORT = 8000

# Exit codes the shell can tell apart.
EXIT_PORT_BUSY_OTHER = 2
EXIT_PORT_BUSY_PARKER = 3


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        return probe.connect_ex((host, port)) == 0


def _preflight_port(host: str, port: int) -> int | None:
    """None when the port is usable; otherwise the exit code to die with."""

    if not _port_in_use(host, port):
        return None
    from app.doctor import _is_parker_health

    if _is_parker_health(host, port):
        print(
            json.dumps({"error": "port_in_use", "occupied_by": "parker", "port": port})
        )
        print(
            f"another Parker engine is already serving on {host}:{port}", file=sys.stderr
        )
        return EXIT_PORT_BUSY_PARKER
    print(json.dumps({"error": "port_in_use", "occupied_by": "other", "port": port}))
    print(f"{host}:{port} is occupied by something that is not Parker", file=sys.stderr)
    return EXIT_PORT_BUSY_OTHER


def start_parent_watchdog(
    parent_pid: int,
    *,
    poll_seconds: float = 2.0,
    on_orphaned=None,
    getppid=os.getppid,
) -> threading.Thread:
    """Exit the engine when the spawning shell dies (orphan protection).

    When the parent process disappears, this process is re-parented (to
    launchd/init), so ``getppid()`` stops matching. SIGTERM to ourselves
    gives uvicorn its normal graceful shutdown.
    """

    def _self_terminate() -> None:
        os.kill(os.getpid(), signal.SIGTERM)

    act = on_orphaned or _self_terminate

    def _watch() -> None:
        while True:
            time.sleep(poll_seconds)
            if getppid() != parent_pid:
                print(
                    f"parent process {parent_pid} is gone — shutting down", file=sys.stderr
                )
                act()
                return

    thread = threading.Thread(target=_watch, name="parker-parent-watchdog", daemon=True)
    thread.start()
    return thread


def cmd_serve(args: argparse.Namespace) -> int:
    from app import paths

    paths.ensure_parker_home()
    exit_code = _preflight_port(args.host, args.port)
    if exit_code is not None:
        return exit_code
    if args.parent_pid:
        start_parent_watchdog(args.parent_pid)

    import uvicorn

    # uvicorn owns SIGINT/SIGTERM → lifespan shutdown (scheduler stops).
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")
    return 0


def cmd_talk(args: argparse.Namespace) -> int:
    from app.demo.talk_loop import run_cli_loop

    run_cli_loop(seconds=args.seconds, server_port=args.port)
    return 0


def cmd_onboard(args: argparse.Namespace) -> int:
    from app.onboard import run_terminal_onboarding

    return run_terminal_onboarding()


def cmd_doctor(args: argparse.Namespace) -> int:
    from app.doctor import render_human, run_checks

    report = run_checks(port=args.port, model_size=args.model)
    print(json.dumps(report, indent=2) if args.json else render_human(report))
    return 0 if report["ok"] else 1


def cmd_download_model(args: argparse.Namespace) -> int:
    from app import paths
    from app.parker.setup_api import DOWNLOADABLE_MODELS, _download_whisper_model

    if args.model not in DOWNLOADABLE_MODELS:
        print(
            f"model must be one of {', '.join(DOWNLOADABLE_MODELS)}", file=sys.stderr
        )
        return 2
    location = paths.whisper_model_location(args.model)
    if location != "missing":
        print(f"{args.model} already available ({location})")
        return 0
    print(f"downloading {args.model} to {paths.models_dir()} …")
    try:
        _download_whisper_model(args.model)
    except Exception as exc:  # noqa: BLE001 — CLI boundary, report and fail
        print(f"download failed: {exc}", file=sys.stderr)
        return 1
    print("done")
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    print(f"parker {__version__}")
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    """One real engine turn on an in-memory DB — proves the bundle works.

    Exercises the whole keyless core (TextSession routing, capture,
    resolve, stage, screen-state publish) without audio deps, network,
    or touching the real database. The sidecar smoke script runs this.
    """

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.conversation.textloop import TextSession
    from app.db.database import create_tables
    from app.db.models import CallLog, StagedAction
    from app.parker.pipeline import resolve_captured_intents, stage_resolved_actions

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_tables(bind=engine)
    db = sessionmaker(bind=engine)()

    call = CallLog(call_sid="SELFTEST", call_type="selftest")
    db.add(call)
    db.commit()
    db.refresh(call)

    session = TextSession(db, call.id)
    turns = []
    response = session.handle("Remind me to take a short walk this afternoon")
    turns.append({"you": "Remind me to take a short walk this afternoon", **response})
    resolve_captured_intents(db)
    stage_resolved_actions(db)
    staged = db.query(StagedAction).count()

    refusal = session.handle("Double my medication dose tonight")
    turns.append({"you": "Double my medication dose tonight", **refusal})

    ok = (
        turns[0].get("kind") == "captured"
        and staged == 1
        and turns[1].get("kind") == "refused"
    )
    print(
        json.dumps(
            {
                "ok": ok,
                "version": __version__,
                "staged_actions": staged,
                "voice_deps": _probe_voice_deps(load_model=args.load_model),
                "turns": [
                    {"you": t["you"], "kind": t.get("kind"), "parker": t.get("speech")}
                    for t in turns
                ],
            },
            indent=2,
        )
    )
    db.close()
    return 0 if ok else 1


def _probe_voice_deps(*, load_model: bool = False) -> dict:
    """Can this build hear and speak? Import-level probes for the bundle.

    The engine core stays keyless/audio-free (selftest's exit code never
    depends on these), but the sidecar smoke script asserts them — a
    bundle whose ctranslate2/PortAudio dylibs went missing must fail the
    smoke, not the first family conversation.
    """

    from app import paths
    from app.voice.transcribe import DEFAULT_ASR_MODEL

    probes: dict = {"faster_whisper": False, "sounddevice": False, "model_loadable": None}
    try:
        import faster_whisper  # noqa: F401

        probes["faster_whisper"] = True
    except Exception as exc:  # noqa: BLE001 — report, never crash
        probes["faster_whisper_error"] = str(exc)
    try:
        import sounddevice  # noqa: F401

        probes["sounddevice"] = True
    except Exception as exc:  # noqa: BLE001
        probes["sounddevice_error"] = str(exc)

    if (
        load_model
        and probes["faster_whisper"]
        and paths.whisper_model_location(DEFAULT_ASR_MODEL) != "missing"
    ):
        try:
            from app.voice.transcribe import load_local_transcriber

            load_local_transcriber(model_size=DEFAULT_ASR_MODEL)
            probes["model_loadable"] = True
        except Exception as exc:  # noqa: BLE001
            probes["model_loadable"] = False
            probes["model_load_error"] = str(exc)
    return probes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="parker", description="Parker engine — voice assistant for effortful speech"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the engine server")
    serve.add_argument("--host", default=SERVE_DEFAULT_HOST)
    serve.add_argument("--port", type=int, default=SERVE_DEFAULT_PORT)
    serve.add_argument(
        "--parent-pid",
        type=int,
        default=0,
        help="exit when this process dies (set by the desktop shell)",
    )
    serve.set_defaults(func=cmd_serve)

    talk = sub.add_parser("talk", help="run the continuous voice loop")
    talk.add_argument("--seconds", type=float, default=12.0, help="max recording window")
    talk.add_argument(
        "--port", type=int, default=SERVE_DEFAULT_PORT,
        help="engine port shown in review-page hints",
    )
    talk.set_defaults(func=cmd_talk)

    onboard = sub.add_parser("onboard", help="terminal onboarding (the app has a wizard)")
    onboard.set_defaults(func=cmd_onboard)

    doctor = sub.add_parser("doctor", help="check this machine is ready for Parker")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--port", type=int, default=SERVE_DEFAULT_PORT)
    doctor.add_argument("--model", default=None)
    doctor.set_defaults(func=cmd_doctor)

    download = sub.add_parser("download-model", help="fetch whisper weights")
    download.add_argument("--model", default=None)
    download.set_defaults(func=cmd_download_model)

    version = sub.add_parser("version", help="print the engine version")
    version.set_defaults(func=cmd_version)

    selftest = sub.add_parser(
        "selftest", help="one engine turn on an in-memory DB (bundle smoke check)"
    )
    selftest.add_argument(
        "--load-model",
        action="store_true",
        help="also load the whisper model if present (bundle native-lib probe)",
    )
    selftest.set_defaults(func=cmd_selftest)

    return parser


def main(argv: list[str] | None = None) -> int:
    from app.voice.transcribe import DEFAULT_ASR_MODEL

    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "model") and args.model is None:
        args.model = DEFAULT_ASR_MODEL
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
