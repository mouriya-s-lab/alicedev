#!/usr/bin/env python3
"""Run one frozen WebChat scenario against two SHAs on the resident e2e stack.

The e2e stack (``deploy/e2e``) is always up. Its AstrBot mounts ``bot/`` and
``templates/`` from a dedicated git checkout (host ``/srv/alicedev/e2e-src``,
``/deploy/e2e-src`` inside the paseo container). For each side this driver:

1. moves that checkout to the side's SHA with ``deployctl`` (restart
   activation: restarting AstrBot and t2i also drops every render cache),
2. verifies the running revision via the bot's ``/v1/health``,
3. drives AstrBot WebChat with agent-browser (real user surface) and sends the
   same messages, then captures a screenshot.

Outputs under ``--output``: ``request.json``, ``{baseline,candidate}/run.json``,
``{baseline,candidate}/capture.png``, ``metric.json`` (F = failing checks,
E = error signatures, d = |F| + |E| of the candidate side), ``evidence.json``.
Exit 0 only when both sides passed every check. The checkout is left at the
candidate SHA.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as datetime_module
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

SCHEMA_VERSION = "alicedev.e2e-driver/v2"
DEFAULT_VIEWPORT = (1280, 720)
DEFAULT_USER = "astrbot"
DEFAULT_CONTAINER = "alicedev-e2e-astrbot"
DEFAULT_T2I_CONTAINER = "alicedev-e2e-t2i"
DEFAULT_SRC = "/deploy/e2e-src"


class DriverError(RuntimeError):
    """Expected fail-closed driver error."""


def _load_deployctl() -> Any:
    path = Path(__file__).with_name("deployctl")
    loader = importlib.machinery.SourceFileLoader("alicedev_deployctl", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve string annotations via sys.modules
    loader.exec_module(module)
    return module


@dataclasses.dataclass
class SideRecord:
    side: str
    sha: str
    run_id: str
    capture_time: str
    messages: list[str]
    dashboard_url: str
    browser_session: str
    running_revision: str | None = None
    checks: dict[str, bool] = dataclasses.field(default_factory=dict)
    failing_checks: list[str] = dataclasses.field(default_factory=list)
    error_signatures: list[str] = dataclasses.field(default_factory=list)
    response_text: str = ""
    browser_log: str | None = None
    capture_path: str | None = None
    capture_sha256: str | None = None
    error: str | None = None

    def json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class Options:
    src: Path
    source: str
    output: Path
    run_id: str
    messages: tuple[str, ...]
    expect: re.Pattern[str] | None
    dashboard_url: str
    bot_api: str
    container: str
    t2i_container: str
    browser: str
    user: str
    passwords: tuple[str, ...]
    new_password: str
    viewport: tuple[int, int]
    reply_timeout: float


class Runner:
    def __init__(self, log: Path) -> None:
        self.log = log

    def run(self, argv: Sequence[str], *, check: bool = True, timeout: float | None = 90) -> str:
        command = [str(part) for part in argv]
        try:
            process = subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)
        except FileNotFoundError as exc:
            raise DriverError(f"required executable not found: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise DriverError(f"command timed out: {' '.join(command[:4])}") from exc
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(f"$ {' '.join(command)}\n{process.stdout}{process.stderr}\n")
        if check and process.returncode:
            detail = (process.stderr.strip() or process.stdout.strip() or "no output")[-1500:]
            raise DriverError(f"command failed ({process.returncode}): {' '.join(command[:4])}\n{detail}")
        return process.stdout


def now() -> str:
    return datetime_module.datetime.now(datetime_module.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normal_error(value: str) -> str:
    value = re.sub(r"\d{2,}", "<n>", value.lower())
    value = re.sub(r"/[^\s]+", "<path>", value)
    return re.sub(r"\s+", " ", value).strip()[:300]


# --- activation ------------------------------------------------------------------


def activate(opts: Options, sha: str, deployctl: Any) -> str:
    """Move the e2e checkout to ``sha`` and restart the stack; return the running revision."""
    subprocess.run(["docker", "restart", opts.t2i_container], capture_output=True, check=False)
    target = deployctl.Target(
        app=opts.src, source=opts.source, activate="restart", astrbot_api="", bot_api=opts.bot_api,
        container=opts.container, plugin="alicedev", docker="docker", timeout=240.0,
    )
    try:
        result = deployctl._deploy(target, sha, failed_first=False)  # noqa: SLF001 - shared deploy primitive
    except deployctl.DeployError as exc:
        raise DriverError(f"e2e activation of {sha} failed: {exc}") from exc
    return str(result["health"].get("revision"))


# --- browser ---------------------------------------------------------------------


def extract_ref(line: str) -> str | None:
    match = re.search(r"ref=@?([A-Za-z0-9_-]+)", line)
    return f"@{match.group(1)}" if match else None


def find_ref(snapshot: str, labels: Sequence[str], role: str | None = None) -> str | None:
    for line in snapshot.splitlines():
        if role and role not in line.lower():
            continue
        if any(label.lower() in line.lower() for label in labels):
            ref = extract_ref(line)
            if ref:
                return ref
    return None


def find_composer(snapshot: str) -> tuple[str | None, str | None]:
    lines = snapshot.splitlines()
    send_index = next((i for i, line in enumerate(lines) if "发送" in line or re.search(r"\bsend\b", line, re.I)), len(lines))
    inputs = [ref for line in lines[:send_index] if "textbox" in line.lower() and (ref := extract_ref(line))]
    return (inputs[-1] if inputs else None, find_ref(snapshot, ("发送", "send")))


class Browser:
    def __init__(self, opts: Options, session: str, runner: Runner) -> None:
        self.opts, self.session, self.runner = opts, session, runner

    def cmd(self, *args: str, check: bool = True, timeout: float | None = 90) -> str:
        return self.runner.run([self.opts.browser, "--session", self.session, *args], check=check, timeout=timeout)

    def snapshot(self) -> str:
        return self.cmd("snapshot", "-i", timeout=60)

    def body(self) -> str:
        return self.cmd("get", "text", "body", check=False).strip()

    def login_needed(self, snapshot: str) -> bool:
        return "登录" in snapshot and ("用户名" in snapshot or "password" in snapshot.lower() or "密码" in snapshot)

    def login(self, chat_url: str) -> None:
        for password in self.opts.passwords:
            snapshot = self.snapshot()
            if not self.login_needed(snapshot):
                return
            user_ref = find_ref(snapshot, ("用户名", "username"), "textbox")
            password_ref = find_ref(snapshot, ("密码", "password"), "textbox")
            login_ref = find_ref(snapshot, ("登录",), "button")
            if not (user_ref and password_ref and login_ref):
                raise DriverError(f"login form controls missing: {snapshot[-1500:]}")
            self.cmd("fill", user_ref, self.opts.user)
            self.cmd("fill", password_ref, password)
            self.cmd("click", login_ref)
            self.cmd("wait", "1500")
            self.cmd("open", chat_url)
            self.cmd("wait", "--load", "networkidle", timeout=60)
        if self.login_needed(self.snapshot()):
            raise DriverError("dashboard login failed with every configured password")

    def dismiss_overlays(self) -> str:
        snapshot = self.snapshot()
        for _ in range(4):
            if "欢迎使用 AstrBot" in snapshot:
                close_ref = find_ref(snapshot, ("关闭",))
                if close_ref is None:
                    raise DriverError("welcome dialog has no close control")
                self.cmd("click", close_ref)
                self.cmd("wait", "300")
                snapshot = self.snapshot()
                continue
            if "修改账户" in snapshot:
                refs = [find_ref(snapshot, (label,), "textbox") for label in ("当前密码", "新密码", "确认新密码")]
                save_ref = find_ref(snapshot, ("保存修改",))
                if not all(refs) or save_ref is None:
                    raise DriverError("mandatory account dialog controls are missing")
                current, new, confirm = refs
                self.cmd("fill", str(current), self.opts.passwords[-1])
                self.cmd("fill", str(new), self.opts.new_password)
                self.cmd("fill", str(confirm), self.opts.new_password)
                self.cmd("click", save_ref)
                self.cmd("wait", "800")
                self.cmd("reload")
                self.cmd("wait", "--load", "networkidle", timeout=60)
                snapshot = self.snapshot()
                continue
            return snapshot
        raise DriverError("WebChat welcome/account overlays did not clear")

    def send_and_wait(self, message: str) -> str:
        snapshot = self.dismiss_overlays()
        input_ref, send_ref = find_composer(snapshot)
        if input_ref is None or send_ref is None:
            raise DriverError(f"cannot find WebChat composer: {snapshot[-1500:]}")
        before = self.body()
        self.cmd("fill", input_ref, message)
        self.cmd("click", send_ref)
        deadline = time.monotonic() + self.opts.reply_timeout
        stable_since, last = None, ""
        while time.monotonic() < deadline:
            time.sleep(1.5)
            body = self.body()
            grown = len(body) > len(before) + len(message)
            if grown and body == last:
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= 3:
                    return body
            else:
                stable_since = None
            last = body
        raise DriverError(f"no bot reply rendered for {message!r} within {self.opts.reply_timeout:.0f}s")


def run_side(opts: Options, side: str, sha: str, deployctl: Any) -> SideRecord:
    side_dir = opts.output / side
    side_dir.mkdir(parents=True, exist_ok=True)
    session = f"e2e-{opts.run_id[-12:]}-{side}"
    runner = Runner(side_dir / "browser.log")
    chat_url = f"{opts.dashboard_url.rstrip('/')}/#/chat"
    record = SideRecord(side, sha, opts.run_id, now(), list(opts.messages), chat_url, session,
                        browser_log=f"{side}/browser.log")
    browser = Browser(opts, session, runner)
    try:
        record.running_revision = activate(opts, sha, deployctl)
        record.checks["revision_matches"] = record.running_revision == sha
        browser.cmd("open", chat_url)
        browser.cmd("set", "viewport", str(opts.viewport[0]), str(opts.viewport[1]))
        browser.cmd("wait", "--load", "networkidle", timeout=60)
        browser.login(chat_url)
        record.checks["dashboard_reachable"] = True
        body = ""
        for index, message in enumerate(opts.messages):
            body = browser.send_and_wait(message)
            record.checks[f"reply_{index}"] = True
        record.response_text = body[-6000:]
        if opts.expect is not None:
            record.checks["expectation_met"] = bool(opts.expect.search(body))
        capture = side_dir / "capture.png"
        browser.cmd("screenshot", str(capture))
        if not capture.is_file() or capture.stat().st_size == 0:
            raise DriverError("agent-browser returned an empty capture")
        record.capture_path = f"{side}/capture.png"
        record.capture_sha256 = file_sha256(capture)
        record.capture_time = now()
    except (DriverError, OSError, subprocess.SubprocessError) as exc:
        record.error = str(exc)
        record.error_signatures.append(normal_error(str(exc)))
    finally:
        browser.cmd("close", check=False)
    record.failing_checks = sorted(name for name, ok in record.checks.items() if not ok)
    if record.error:
        record.failing_checks = sorted(set(record.failing_checks) | {"runtime_error"})
    return record


def metric(baseline: SideRecord, candidate: SideRecord) -> dict[str, Any]:
    failing = sorted(set(candidate.failing_checks))
    signatures = sorted(set(candidate.error_signatures))
    return {
        "F": failing,
        "E": signatures,
        "d": len(failing) + len(signatures),
        "baseline": {"F": sorted(set(baseline.failing_checks)), "E": sorted(set(baseline.error_signatures))},
        "definition": "F = failing checks, E = error signatures, d = |F| + |E| (candidate side)",
    }


def run(args: argparse.Namespace) -> int:
    deployctl = _load_deployctl()
    src = Path(args.src).resolve()
    output = Path(args.output).resolve()
    run_id = args.run_id or f"e2e-{datetime_module.datetime.now(datetime_module.timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    passwords = tuple(p for p in (args.dashboard_new_password, args.dashboard_password) if p)
    opts = Options(
        src=src, source=args.source, output=output, run_id=run_id,
        messages=tuple(args.message or ["/alicedev"]),
        expect=re.compile(args.expect) if args.expect else None,
        dashboard_url=args.dashboard_url, bot_api=args.bot_api.rstrip("/"),
        container=args.container, t2i_container=args.t2i_container, browser=args.browser,
        user=args.dashboard_user, passwords=passwords, new_password=args.dashboard_new_password,
        viewport=(args.viewport_width, args.viewport_height), reply_timeout=args.reply_timeout,
    )
    target = deployctl.Target(app=src, source=args.source, activate="restart", astrbot_api="",
                              bot_api=opts.bot_api, container=opts.container, plugin="alicedev",
                              docker="docker", timeout=60)
    baseline_sha = deployctl._resolve(target, args.baseline)  # noqa: SLF001
    candidate_sha = deployctl._resolve(target, args.candidate)  # noqa: SLF001
    if args.require_difference and baseline_sha == candidate_sha:
        raise DriverError("baseline and candidate resolve to the same commit; pass --no-require-difference for a symmetry smoke")
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "request.json", {
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "baseline_sha": baseline_sha,
        "candidate_sha": candidate_sha, "messages": list(opts.messages), "requested_at": now(),
        "surface": "AstrBot WebChat via agent-browser", "viewport": list(opts.viewport),
    })
    baseline = run_side(opts, "baseline", baseline_sha, deployctl)
    atomic_json(output / "baseline" / "run.json", baseline.json())
    candidate = run_side(opts, "candidate", candidate_sha, deployctl)
    atomic_json(output / "candidate" / "run.json", candidate.json())
    run_metric = metric(baseline, candidate)
    atomic_json(output / "metric.json", run_metric)
    ok = not baseline.failing_checks and not candidate.failing_checks
    atomic_json(output / "evidence.json", {
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "status": "success" if ok else "failed",
        "baseline_sha": baseline_sha, "candidate_sha": candidate_sha, "captured_at": now(),
        "same_scenario": {"messages": list(opts.messages), "viewport": list(opts.viewport),
                          "surface": "AstrBot WebChat", "sides_are_sequential": True,
                          "caches": "AstrBot and t2i restarted before each side"},
        "baseline": baseline.json(), "candidate": candidate.json(), "metric": run_metric,
    })
    return 0 if ok else 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = root.add_subparsers(dest="command", required=True)
    r = commands.add_parser("run", help="run one frozen scenario against two SHAs")
    r.add_argument("--baseline", required=True, help="baseline ref/SHA (the running production SHA)")
    r.add_argument("--candidate", required=True, help="candidate ref/SHA")
    r.add_argument("--output", required=True)
    r.add_argument("--message", action="append", help="WebChat message to send (repeatable); default /alicedev")
    r.add_argument("--expect", help="regex the final page text must match on both sides")
    r.add_argument("--src", default=os.environ.get("E2E_SRC_DIR", DEFAULT_SRC))
    r.add_argument("--source", default="origin", help="remote or local repo path to fetch SHAs from")
    r.add_argument("--dashboard-url", default=os.environ.get("E2E_DASHBOARD_URL", f"http://{DEFAULT_CONTAINER}:6185"))
    r.add_argument("--bot-api", default=os.environ.get("E2E_BOT_API", f"http://{DEFAULT_CONTAINER}:6200"))
    r.add_argument("--container", default=DEFAULT_CONTAINER)
    r.add_argument("--t2i-container", default=DEFAULT_T2I_CONTAINER)
    r.add_argument("--browser", default="agent-browser")
    r.add_argument("--dashboard-user", default=DEFAULT_USER)
    r.add_argument("--dashboard-password", default=os.environ.get("E2E_DASHBOARD_PASSWORD", ""))
    r.add_argument("--dashboard-new-password", default=os.environ.get("E2E_DASHBOARD_NEW_PASSWORD", "E2e-Driver-Password9"))
    r.add_argument("--viewport-width", type=int, default=DEFAULT_VIEWPORT[0])
    r.add_argument("--viewport-height", type=int, default=DEFAULT_VIEWPORT[1])
    r.add_argument("--reply-timeout", type=float, default=60.0)
    r.add_argument("--run-id")
    r.add_argument("--no-require-difference", dest="require_difference", action="store_false")
    r.set_defaults(require_difference=True)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return run(args)
    except (DriverError, OSError) as exc:
        print(f"e2e driver failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # deployctl.DeployError from SHA resolution
        print(f"e2e driver failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
