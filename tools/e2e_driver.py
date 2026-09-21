#!/usr/bin/env python3
"""Run the frozen WebChat scenario symmetrically against two git SHAs.

The default mode owns a uniquely named copy of the wave-1 ``alicedev-e2e``
Compose project. Each SHA is materialized in a detached worktree and its
``bot`` and ``templates`` trees are mounted read-only at runtime. The same
seed image, fresh application volume, scenario, and browser viewport are used
sequentially for baseline and candidate.

``--stub`` is an explicit conductor wiring fixture. It never starts Docker or
claims real user-surface evidence; every record is labelled ``backend=stub``.
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import datetime as datetime_module
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = "alicedev.e2e-driver/v1"
SCENARIO_ID = "alicedev-help-webchat-v1"
COMPOSE_BASE = Path("deploy/e2e/docker-compose.yml")
BOT_TARGET = "/AstrBot/data/plugins/alicedev"
TEMPLATES_TARGET = "/AstrBot/alicedev-templates"
DEFAULT_VIEWPORT = (1280, 577)
DEFAULT_USER = "astrbot"
DEFAULT_PASSWORD = "E2e-Local-Password9"
STUB_BASELINE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
STUB_CANDIDATE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/w8AAusB9WnTq9sAAAAASUVORK5CYII="
)


class DriverError(RuntimeError):
    """Expected fail-closed driver error."""


@dataclasses.dataclass(frozen=True)
class Scenario:
    scenario_id: str
    command: str


SCENARIOS = {SCENARIO_ID: Scenario(SCENARIO_ID, "/alicedev")}


@dataclasses.dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclasses.dataclass
class SideRecord:
    side: str
    sha: str
    scenario_id: str
    run_id: str
    capture_time: str
    backend: str
    dashboard_url: str | None = None
    dashboard_port: int | None = None
    browser_session: str | None = None
    source_root: str | None = None
    mounts: dict[str, Any] = dataclasses.field(default_factory=dict)
    checks: dict[str, bool] = dataclasses.field(default_factory=dict)
    failing_checks: list[str] = dataclasses.field(default_factory=list)
    error_signatures: list[str] = dataclasses.field(default_factory=list)
    response_text: str = ""
    browser_log: str | None = None
    capture_path: str | None = None
    capture_sha256: str | None = None
    capture_bytes: int | None = None
    error: str | None = None

    def json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class Context:
    repo: Path
    output: Path
    run_id: str
    scenario: Scenario
    baseline_sha: str
    candidate_sha: str
    compose_file: Path
    project: str
    browser: str
    dashboard_user: str
    dashboard_password: str
    dashboard_new_password: str
    viewport: tuple[int, int]
    stub: bool
    keep_stack: bool
    skip_build: bool
    work_root: Path | None = None
    override: Path | None = None
    image: str | None = None
    volume: str | None = None
    network: str | None = None
    stack_created: bool = False


class Runner:
    def __init__(self, log: Path | None = None) -> None:
        self.log = log

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> CommandResult:
        command = tuple(str(part) for part in argv)
        try:
            process = subprocess.run(
                command,
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise DriverError(f"required executable not found: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise DriverError(f"command timed out: {shell_join(command)}") from exc
        result = CommandResult(command, process.returncode, process.stdout, process.stderr)
        if self.log is not None:
            with self.log.open("a", encoding="utf-8") as stream:
                stream.write(f"$ {shell_join(command)}\n")
                if process.stdout:
                    stream.write(process.stdout)
                    if not process.stdout.endswith("\n"):
                        stream.write("\n")
                if process.stderr:
                    stream.write(process.stderr)
                    if not process.stderr.endswith("\n"):
                        stream.write("\n")
        if check and process.returncode:
            detail = (process.stderr.strip() or process.stdout.strip() or "no output")[-2000:]
            raise DriverError(f"command failed ({process.returncode}): {shell_join(command)}\n{detail}")
        return result


def shell_join(argv: Iterable[str]) -> str:
    return " ".join(repr(str(part)) for part in argv)


def now() -> str:
    return datetime_module.datetime.now(datetime_module.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_id(value: str, limit: int = 24) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-") or "run"
    return value[:limit].rstrip("-")


def quote_yaml(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def resolve_repo(path: Path) -> Path:
    result = Runner().run(["git", "-C", str(path), "rev-parse", "--show-toplevel"])
    root = Path(result.stdout.strip()).resolve()
    if not (root / "bot").is_dir() or not (root / "deploy/e2e").is_dir():
        raise DriverError(f"not an alicedev checkout: {root}")
    return root


def resolve_sha(repo: Path, ref: str) -> str:
    result = Runner().run(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        check=False,
    )
    if result.returncode:
        raise DriverError(f"cannot resolve {ref!r}: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def refuse_production(repo: Path, compose_file: Path, output: Path) -> None:
    production = Path("/srv/alicedev").resolve()
    for path in (repo.resolve(), compose_file.resolve(), output.resolve()):
        if path == production or production in path.parents:
            raise DriverError(f"production path refused: {path}")
    if compose_file.name != "docker-compose.yml" or compose_file.parent.name != "e2e":
        raise DriverError("compose file must be deploy/e2e/docker-compose.yml")


def check_tools(ctx: Context) -> None:
    for executable in ("git", "docker", ctx.browser):
        if shutil.which(executable) is None:
            raise DriverError(f"required executable not found: {executable}")
    Runner().run(["docker", "compose", "version"])
    Runner().run([ctx.browser, "--version"], check=False)


def materialize(ctx: Context, side: str, sha: str) -> Path:
    assert ctx.work_root is not None
    checkout = ctx.work_root / side / "checkout"
    checkout.parent.mkdir(parents=True, exist_ok=True)
    Runner().run(["git", "-C", str(ctx.repo), "worktree", "add", "--detach", str(checkout), sha])
    required = ("bot", "templates", "deploy/e2e/Dockerfile", "deploy/e2e/seed-data")
    missing = [path for path in required if not (checkout / path).exists()]
    if missing:
        raise DriverError(f"{side} SHA {sha} is missing required paths: {', '.join(missing)}")
    return checkout


def remove_worktrees(ctx: Context) -> None:
    if ctx.work_root is None:
        return
    runner = Runner()
    for side in ("baseline", "candidate"):
        checkout = ctx.work_root / side / "checkout"
        if checkout.exists():
            runner.run(["git", "-C", str(ctx.repo), "worktree", "remove", "--force", str(checkout)], check=False)


def make_override(ctx: Context, side_root: Path) -> Path:
    assert ctx.work_root is not None
    ctx.image = f"alicedev/e2e:{ctx.project}"
    ctx.volume = f"{ctx.project}-data"
    ctx.network = f"{ctx.project}-network"
    baseline_root = ctx.work_root / "baseline" / "checkout"
    override = ctx.work_root / f"compose-{safe_id(ctx.run_id)}.yml"
    override.write_text(
        f"""services:
  astrbot:
    image: {ctx.image}
    build:
      context: {quote_yaml(str(baseline_root))}
      dockerfile: deploy/e2e/Dockerfile
    container_name: {ctx.project}-astrbot
    volumes:
      - {quote_yaml(f"{side_root / 'bot'}:{BOT_TARGET}:ro")}
      - {quote_yaml(f"{side_root / 'templates'}:{TEMPLATES_TARGET}:ro")}
  t2i:
    container_name: {ctx.project}-t2i
networks:
  e2e:
    name: {ctx.network}
volumes:
  e2e_astrbot_data:
    name: {ctx.volume}
""",
        encoding="utf-8",
    )
    ctx.override = override
    return override


def compose(ctx: Context, *args: str) -> list[str]:
    if ctx.override is None:
        raise DriverError("compose override is not ready")
    return [
        "docker", "compose", "--project-name", ctx.project,
        "--file", str(ctx.compose_file), "--file", str(ctx.override), *args,
    ]


def refuse_collision(ctx: Context) -> None:
    runner = Runner()
    result = runner.run(
        ["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={ctx.project}", "--format", "{{.Names}}"],
        check=False,
    )
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    resources: list[str] = list(names)
    for kind, command in (
        ("volume", ["docker", "volume", "ls", "--format", "{{.Name}}"]),
        ("network", ["docker", "network", "ls", "--format", "{{.Name}}"]),
    ):
        listed = runner.run(command, check=False)
        exact = f"{ctx.project}-data" if kind == "volume" else f"{ctx.project}-network"
        if exact in {line.strip() for line in listed.stdout.splitlines()}:
            resources.append(f"{kind}:{exact}")
    if resources:
        raise DriverError(f"private Compose resources already exist: {', '.join(resources)}")


def config_json(ctx: Context) -> dict[str, Any]:
    result = Runner().run([*compose(ctx, "config", "--format", "json")])
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DriverError(f"invalid docker compose config JSON: {exc}") from exc


def source_by_target(config: dict[str, Any], target: str) -> list[str]:
    volumes = ((config.get("services") or {}).get("astrbot") or {}).get("volumes") or []
    sources: list[str] = []
    for volume in volumes:
        if isinstance(volume, dict):
            if str(volume.get("target")) == target:
                sources.append(str(volume.get("source")))
        else:
            source, _, volume_target = str(volume).partition(":")
            if volume_target == target:
                sources.append(source)
    return sources


def validate_config(ctx: Context, side_root: Path) -> None:
    rendered = config_json(ctx)
    for target, expected in ((BOT_TARGET, side_root / "bot"), (TEMPLATES_TARGET, side_root / "templates")):
        actual = source_by_target(rendered, target)
        if actual != [str(expected)]:
            raise DriverError(f"compose config mount mismatch for {target}: {actual!r} != {[str(expected)]!r}")


def build_image(ctx: Context) -> None:
    if not ctx.skip_build:
        Runner().run([*compose(ctx, "build", "--pull=false", "astrbot")], timeout=1800)


def down(ctx: Context) -> None:
    Runner().run([*compose(ctx, "down", "--volumes", "--remove-orphans")], check=False, timeout=120)
    ctx.stack_created = False


def inspect_live(ctx: Context, side_root: Path) -> dict[str, Any]:
    result = Runner().run(["docker", "inspect", f"{ctx.project}-astrbot"])
    try:
        payload = json.loads(result.stdout)[0]
    except (json.JSONDecodeError, IndexError) as exc:
        raise DriverError(f"invalid docker inspect response: {exc}") from exc
    labels = payload.get("Config", {}).get("Labels") or {}
    if labels.get("com.docker.compose.project") != ctx.project:
        raise DriverError("running astrbot container is not owned by this driver")
    mounts: dict[str, list[str]] = {}
    for mount in payload.get("Mounts", []):
        mounts.setdefault(str(mount.get("Destination")), []).append(str(mount.get("Source")))
    expected = {BOT_TARGET: str(side_root / "bot"), TEMPLATES_TARGET: str(side_root / "templates")}
    for target, source in expected.items():
        if mounts.get(target) != [source]:
            raise DriverError(f"live mount mismatch for {target}: {mounts.get(target)!r} != {[source]!r}")
    return {"labels": labels, "mounts": mounts, "expected": expected}


def start(ctx: Context, side_root: Path) -> tuple[int, dict[str, Any]]:
    make_override(ctx, side_root)
    validate_config(ctx, side_root)
    Runner().run([*compose(ctx, "up", "--detach", "--force-recreate", "--no-build")], timeout=300)
    ctx.stack_created = True
    info = inspect_live(ctx, side_root)
    result = Runner().run([*compose(ctx, "port", "astrbot", "6185")])
    match = re.search(r":(\d+)\s*$", result.stdout.strip())
    if not match:
        raise DriverError(f"cannot parse WebChat port: {result.stdout!r}")
    return int(match.group(1)), info

def wait_http(port: int, timeout_seconds: float = 60) -> None:
    import urllib.request

    deadline = time.monotonic() + timeout_seconds
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
                if response.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001 - polling boundary
            last = str(exc)
        time.sleep(0.5)
    raise DriverError(f"WebChat readiness timeout on port {port}: {last}")


def browser_snapshot(runner: Runner, ctx: Context, session: str) -> str:
    return runner.run([ctx.browser, "--session", session, "snapshot", "-i"], timeout=60).stdout


def browser_body(runner: Runner, ctx: Context, session: str) -> str:
    return runner.run([ctx.browser, "--session", session, "get", "text", "body"], check=False).stdout.strip()


def extract_ref(line: str) -> str | None:
    match = re.search(r"ref=@?([A-Za-z0-9_-]+)", line)
    return f"@{match.group(1)}" if match else None


def find_ref(snapshot: str, labels: Sequence[str]) -> str | None:
    for line in snapshot.splitlines():
        if any(label.lower() in line.lower() for label in labels):
            ref = extract_ref(line)
            if ref:
                return ref
    return None


def find_composer(snapshot: str) -> tuple[str | None, str | None]:
    lines = snapshot.splitlines()
    send_index = next(
        (index for index, line in enumerate(lines) if "发送" in line or re.search(r"\bsend\b", line, re.I)),
        len(lines),
    )
    inputs: list[str] = []
    for line in lines[:send_index]:
        if "textbox" in line.lower():
            ref = extract_ref(line)
            if ref:
                inputs.append(ref)
    send_ref = find_ref(snapshot, ("发送", "send"))
    return (inputs[-1] if inputs else None, send_ref)
def textbox_ref(snapshot: str, label: str) -> str | None:
    for line in snapshot.splitlines():
        if "textbox" in line.lower() and label.lower() in line.lower():
            ref = extract_ref(line)
            if ref:
                return ref
    return None


def dismiss_overlays(runner: Runner, ctx: Context, session: str, snapshot: str) -> str:
    for _ in range(4):
        # AstrBot opens the welcome dialog above the mandatory account dialog.
        if "欢迎使用 AstrBot" in snapshot:
            close_ref = find_ref(snapshot, ("关闭",))
            if close_ref is None:
                raise DriverError("welcome dialog has no close control")
            runner.run([ctx.browser, "--session", session, "click", close_ref])
            runner.run([ctx.browser, "--session", session, "wait", "200"])
            snapshot = browser_snapshot(runner, ctx, session)
            continue
        if "修改账户" in snapshot:
            current_ref = textbox_ref(snapshot, "当前密码")
            new_ref = textbox_ref(snapshot, "新密码")
            confirm_ref = textbox_ref(snapshot, "确认新密码")
            save_ref = find_ref(snapshot, ("保存修改",))
            if not all((current_ref, new_ref, confirm_ref, save_ref)):
                raise DriverError("mandatory account dialog controls are missing")
            runner.run([ctx.browser, "--session", session, "fill", current_ref, ctx.dashboard_password])
            runner.run([ctx.browser, "--session", session, "fill", new_ref, ctx.dashboard_new_password])
            runner.run([ctx.browser, "--session", session, "fill", confirm_ref, ctx.dashboard_new_password])
            runner.run([ctx.browser, "--session", session, "click", save_ref])
            runner.run([ctx.browser, "--session", session, "wait", "500"])
            runner.run([ctx.browser, "--session", session, "reload"])
            runner.run([ctx.browser, "--session", session, "wait", "--load", "networkidle"], timeout=60)
            snapshot = browser_snapshot(runner, ctx, session)
            continue
        return snapshot
    raise DriverError("WebChat account/welcome overlays did not clear")


def login_needed(snapshot: str) -> bool:
    return "登录" in snapshot and ("用户名" in snapshot or "password" in snapshot.lower())


def observed_response(snapshot: str, body: str) -> bool:
    combined = f"{snapshot}\n{body}".lower()
    if "alicedev 可用指令" in combined or "使用帮助" in combined:
        return True
    # The wave-1 card is exposed as a button labelled with its generated PNG.
    return bool(re.search(r"\b\d{10,}_[a-z0-9]+\.(?:png|jpe?g)\b", combined))


def wait_response(runner: Runner, ctx: Context, session: str) -> tuple[str, str]:
    deadline = time.monotonic() + 45
    latest_snapshot = ""
    latest_body = ""
    while time.monotonic() < deadline:
        latest_snapshot = browser_snapshot(runner, ctx, session)
        latest_body = browser_body(runner, ctx, session)
        if observed_response(latest_snapshot, latest_body):
            return latest_snapshot, latest_body
        time.sleep(1)
    raise DriverError(f"WebChat did not render response for {ctx.scenario.command}: {latest_snapshot[-2000:]}")


def normal_error(value: str) -> str:
    value = re.sub(r"\d{2,}", "<n>", value.lower())
    value = re.sub(r"/[^\s]+", "<path>", value)
    return re.sub(r"\s+", " ", value).strip()[:300]


def run_browser(ctx: Context, side: str, sha: str, port: int, side_dir: Path, mount_info: dict[str, Any]) -> SideRecord:
    session = f"{safe_id(ctx.project)}-{side}"
    browser_log = side_dir / "browser.log"
    runner = Runner(browser_log)
    record = SideRecord(
        side=side, sha=sha, scenario_id=ctx.scenario.scenario_id, run_id=ctx.run_id,
        capture_time=now(), backend="agent-browser", dashboard_port=port,
        dashboard_url=f"http://127.0.0.1:{port}/#/chat", browser_session=session,
        source_root=str(ctx.work_root / side / "checkout") if ctx.work_root else None,
        mounts=mount_info, browser_log=str(browser_log.relative_to(ctx.output)),
    )
    chat_url = f"http://127.0.0.1:{port}/#/chat"
    try:
        wait_http(port)
        runner.run([ctx.browser, "--session", session, "open", chat_url])
        runner.run([ctx.browser, "--session", session, "set", "viewport", str(ctx.viewport[0]), str(ctx.viewport[1])])
        runner.run([ctx.browser, "--session", session, "wait", "--load", "networkidle"], timeout=60)
        first_snapshot = browser_snapshot(runner, ctx, session)
        record.checks["dashboard_reachable"] = True
        if login_needed(first_snapshot):
            user_ref = find_ref(first_snapshot, ("用户名", "username")) or "@e4"
            password_ref = find_ref(first_snapshot, ("密码", "password")) or "@e6"
            login_ref = find_ref(first_snapshot, ("登录",)) or "@e3"
            runner.run([ctx.browser, "--session", session, "fill", user_ref, ctx.dashboard_user])
            runner.run([ctx.browser, "--session", session, "fill", password_ref, ctx.dashboard_password])
            runner.run([ctx.browser, "--session", session, "click", login_ref])
            runner.run([ctx.browser, "--session", session, "wait", "1000"])
            runner.run([ctx.browser, "--session", session, "open", chat_url])
            runner.run([ctx.browser, "--session", session, "wait", "--load", "networkidle"], timeout=60)
        chat_snapshot = dismiss_overlays(runner, ctx, session, browser_snapshot(runner, ctx, session))
        input_ref, send_ref = find_composer(chat_snapshot)
        if input_ref is None or send_ref is None:
            raise DriverError(f"cannot find WebChat composer refs: {chat_snapshot[-3000:]}")
        runner.run([ctx.browser, "--session", session, "fill", input_ref, ctx.scenario.command])
        runner.run([ctx.browser, "--session", session, "click", send_ref])
        record.checks["scenario_submitted"] = True
        response_snapshot, response_body = wait_response(runner, ctx, session)
        record.response_text = response_body
        record.checks["response_observed"] = observed_response(response_snapshot, response_body)
        capture = side_dir / "capture.png"
        runner.run([ctx.browser, "--session", session, "screenshot", str(capture)])
        if not capture.is_file() or capture.stat().st_size == 0:
            raise DriverError("agent-browser returned without a non-empty capture")
        record.capture_time = now()
        record.capture_path = str(capture.relative_to(ctx.output))
        record.capture_sha256 = file_sha256(capture)
        record.capture_bytes = capture.stat().st_size
        record.checks["capture_written"] = True
        record.checks["mounts_match_sha"] = True
    except (DriverError, OSError, subprocess.SubprocessError) as exc:
        record.error = str(exc)
        record.error_signatures.append(normal_error(str(exc)))
    finally:
        runner.run([ctx.browser, "--session", session, "close"], check=False)
    record.failing_checks = sorted(name for name, value in record.checks.items() if not value)
    if record.error:
        record.failing_checks = sorted(set(record.failing_checks) | {"runtime_error"})
    return record


def stub_record(ctx: Context, side: str, sha: str, side_dir: Path) -> SideRecord:
    capture = side_dir / "capture.png"
    capture.write_bytes(STUB_BASELINE_PNG if side == "baseline" else STUB_CANDIDATE_PNG)
    return SideRecord(
        side=side, sha=sha, scenario_id=ctx.scenario.scenario_id, run_id=ctx.run_id,
        capture_time=now(), backend="stub",
        checks={"dashboard_reachable": True, "scenario_submitted": True, "response_observed": True, "capture_written": True, "mounts_match_sha": True},
        capture_path=str(capture.relative_to(ctx.output)), capture_sha256=file_sha256(capture), capture_bytes=capture.stat().st_size,
    )


def metric(baseline: SideRecord, candidate: SideRecord) -> dict[str, Any]:
    failing = sorted(set(candidate.failing_checks))
    signatures = sorted(set(candidate.error_signatures))
    return {
        "failing_checks": failing,
        "error_signatures": signatures,
        "distance": len(failing) + len(signatures),
        "distance_definition": "cardinality(failing_checks) + cardinality(error_signatures); candidate side",
        "baseline_failing_checks": sorted(set(baseline.failing_checks)),
        "baseline_error_signatures": sorted(set(baseline.error_signatures)),
        "baseline_distance": len(set(baseline.failing_checks)) + len(set(baseline.error_signatures)),
    }


def evidence(ctx: Context, baseline: SideRecord, candidate: SideRecord, run_metric: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": ctx.run_id,
        "baseline_sha": ctx.baseline_sha,
        "candidate_sha": ctx.candidate_sha,
        "scenario_id": ctx.scenario.scenario_id,
        "capture_time": now(),
        "backend": "stub" if ctx.stub else "agent-browser",
        "same_scenario": {
            "command": ctx.scenario.command,
            "viewport": {"width": ctx.viewport[0], "height": ctx.viewport[1]},
            "fixture": "wave-1 deploy/e2e seed-data; fresh isolated application volume per side",
            "surface": "AstrBot WebChat",
            "sides_are_sequential": True,
        },
        "telegram": {
            "status": "not_run",
            "reason": "WebChat is the frozen real surface for this run; no Telegram auth seed was supplied",
            "seed_step": "docker exec -it alicedev-tg-cli tg chats, then invoke tools/tgctl with the seeded chat",
        },
        "baseline": baseline.json(),
        "candidate": candidate.json(),
        "metric": run_metric,
        "evidence_paths": ["baseline/capture.png", "baseline/run.json", "candidate/capture.png", "candidate/run.json", "metric.json"],
    }


def write_side(record: SideRecord, output: Path) -> None:
    atomic_json(output / record.side / "run.json", record.json())


def cleanup(ctx: Context) -> None:
    if ctx.keep_stack and not ctx.stub:
        return
    if not ctx.stub and ctx.stack_created:
        down(ctx)
    remove_worktrees(ctx)
    if ctx.work_root is not None:
        shutil.rmtree(ctx.work_root, ignore_errors=True)


def run_driver(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo).resolve())
    output = Path(args.output).resolve()
    compose_file = Path(args.compose_file)
    if not compose_file.is_absolute():
        compose_file = (repo / compose_file).resolve()
    refuse_production(repo, compose_file, output)
    scenario = SCENARIOS.get(args.scenario)
    if scenario is None:
        raise DriverError(f"unknown scenario: {args.scenario}")
    baseline_sha = resolve_sha(repo, args.baseline)
    candidate_sha = resolve_sha(repo, args.candidate)
    if args.require_difference and baseline_sha == candidate_sha:
        raise DriverError("baseline and candidate resolve to the same commit; pass --no-require-difference for a symmetry smoke")
    run_id = args.run_id or f"e2e-{datetime_module.datetime.now(datetime_module.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    project = f"alicedev-e2e-driver-{safe_id(run_id)}"
    ctx = Context(
        repo=repo, output=output, run_id=run_id, scenario=scenario,
        baseline_sha=baseline_sha, candidate_sha=candidate_sha, compose_file=compose_file,
        project=project, browser=args.browser, dashboard_user=args.dashboard_user,
        dashboard_password=args.dashboard_password, dashboard_new_password=args.dashboard_new_password,
        viewport=(args.viewport_width, args.viewport_height),
        stub=args.stub, keep_stack=args.keep_stack, skip_build=args.skip_build,
    )
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "request.json", {
        "schema_version": SCHEMA_VERSION, "run_id": run_id,
        "baseline_sha": baseline_sha, "candidate_sha": candidate_sha,
        "scenario_id": scenario.scenario_id, "requested_at": now(),
        "backend": "stub" if args.stub else "agent-browser", "repo": str(repo),
        "compose_file": str(compose_file),
    })
    baseline: SideRecord | None = None
    candidate: SideRecord | None = None
    try:
        if not args.stub:
            check_tools(ctx)
            refuse_collision(ctx)
        (output / "baseline").mkdir(parents=True, exist_ok=True)
        (output / "candidate").mkdir(parents=True, exist_ok=True)
        if args.stub:
            baseline = stub_record(ctx, "baseline", baseline_sha, output / "baseline")
            candidate = stub_record(ctx, "candidate", candidate_sha, output / "candidate")
        else:
            temp_parent = args.temp_dir or None
            ctx.work_root = Path(tempfile.mkdtemp(prefix=f"{project}-", dir=temp_parent))
            baseline_root = materialize(ctx, "baseline", baseline_sha)
            candidate_root = materialize(ctx, "candidate", candidate_sha)
            make_override(ctx, baseline_root)
            validate_config(ctx, baseline_root)
            build_image(ctx)
            baseline_port, baseline_info = start(ctx, baseline_root)
            baseline = run_browser(ctx, "baseline", baseline_sha, baseline_port, output / "baseline", baseline_info)
            write_side(baseline, output)
            down(ctx)
            candidate_port, candidate_info = start(ctx, candidate_root)
            candidate = run_browser(ctx, "candidate", candidate_sha, candidate_port, output / "candidate", candidate_info)
        if baseline is None or candidate is None:
            raise DriverError("missing baseline or candidate record")
        write_side(baseline, output)
        write_side(candidate, output)
        run_metric = metric(baseline, candidate)
        atomic_json(output / "metric.json", run_metric)
        atomic_json(output / "evidence.json", evidence(ctx, baseline, candidate, run_metric))
        status = "success" if not baseline.failing_checks and not candidate.failing_checks and run_metric["distance"] == 0 else "failed"
        atomic_json(output / "e2e-yield.json", {
            "phase": "e2e", "status": status, "commit": candidate_sha,
            "evidence_paths": ["baseline/capture.png", "baseline/run.json", "candidate/capture.png", "candidate/run.json", "metric.json", "evidence.json", "e2e-yield.json"],
            "run_id": run_id, "backend": "stub" if args.stub else "agent-browser",
        })
        return 0 if status == "success" else 1
    except Exception as exc:  # noqa: BLE001 - structured CLI failure boundary
        error = str(exc)
        if baseline is None:
            baseline = SideRecord("baseline", baseline_sha, scenario.scenario_id, run_id, now(), "stub" if args.stub else "agent-browser", failing_checks=["driver_error"], error_signatures=[normal_error(error)], error=error)
            write_side(baseline, output)
        if candidate is None:
            candidate = SideRecord("candidate", candidate_sha, scenario.scenario_id, run_id, now(), "stub" if args.stub else "agent-browser", failing_checks=["driver_error"], error_signatures=[normal_error(error)], error=error)
            write_side(candidate, output)
        run_metric = metric(baseline, candidate)
        atomic_json(output / "metric.json", run_metric)
        atomic_json(output / "evidence.json", evidence(ctx, baseline, candidate, run_metric))
        atomic_json(output / "e2e-yield.json", {
            "phase": "e2e", "status": "failed", "commit": candidate_sha,
            "evidence_paths": ["baseline/run.json", "candidate/run.json", "metric.json", "evidence.json", "e2e-yield.json"],
            "run_id": run_id, "backend": "stub" if args.stub else "agent-browser", "error": error,
        })
        print(f"e2e driver failed: {error}", file=sys.stderr)
        return 1
    finally:
        cleanup(ctx)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run one frozen scenario against two git SHAs")
    run.add_argument("--repo", default=".")
    run.add_argument("--baseline", required=True)
    run.add_argument("--candidate", required=True)
    run.add_argument("--scenario", choices=sorted(SCENARIOS), default=SCENARIO_ID)
    run.add_argument("--output", required=True)
    run.add_argument("--compose-file", default=str(COMPOSE_BASE))
    run.add_argument("--browser", default="agent-browser")
    run.add_argument("--dashboard-user", default=DEFAULT_USER)
    run.add_argument("--dashboard-password", default=os.environ.get("E2E_DASHBOARD_PASSWORD", DEFAULT_PASSWORD))
    run.add_argument("--dashboard-new-password", default=os.environ.get("E2E_DASHBOARD_NEW_PASSWORD", "E2e-Driver-Password9"))
    run.add_argument("--viewport-width", type=int, default=DEFAULT_VIEWPORT[0])
    run.add_argument("--viewport-height", type=int, default=DEFAULT_VIEWPORT[1])
    run.add_argument("--run-id")
    run.add_argument("--temp-dir")
    run.add_argument("--skip-build", action="store_true")
    run.add_argument("--keep-stack", action="store_true")
    run.add_argument("--stub", action="store_true")
    run.add_argument("--no-require-difference", dest="require_difference", action="store_false")
    run.set_defaults(require_difference=True)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return run_driver(args)
    except (DriverError, OSError) as exc:
        print(f"e2e driver failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
