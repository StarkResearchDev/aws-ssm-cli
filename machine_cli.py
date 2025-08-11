#!/usr/bin/env python3
"""
Person of Interest / The Machine style terminal for multi-instance AWS operations.
Interactive + Batch mode, matrix rain animation, typing effect, parallel SSM commands,
file search and append-after-match (e.g. after `def run()`).

Dependencies:
  pip install boto3 rich questionary colorama

Usage (interactive):
  python machine_cli.py

Usage (batch example):
  python machine_cli.py --batch --profile dev --region us-east-1 \
    --instances i-0123,i-0456 --action custom --cmd "cd /opt/app && git pull" --parallel --concurrency 6
"""
import argparse
import boto3
import os
import sys
import time
import re
import shlex
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Dict

# UI libs
from rich.console import Console
from rich.theme import Theme
from rich.traceback import install as rich_traceback_install
import questionary
from colorama import Fore, Style

# optional: curses for matrix rain
import curses

# Setup
rich_traceback_install()
THEME = Theme({"machine": "bold green", "muted": "green", "error": "bold red"})
console = Console(theme=THEME)
LOG_DIR = os.path.expanduser("~/.tm_cli_logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"session-{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.log")


def log(s: str):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {s}"
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
    console.print(line, style="muted")


# --------------------------
# Matrix rain (curses)
# --------------------------
def matrix_rain(stdscr, duration=3.0, skip_on_keypress=True):
    """
    Play matrix rain for duration seconds. If skip_on_keypress True, pressing any key
    will exit the animation early.
    """
    curses.curs_set(0)
    stdscr.nodelay(True)  # non-blocking getch
    sh, sw = stdscr.getmaxyx()
    # characters to use
    charset = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@#$%&*"
    font_width = 1
    cols = max(1, sw // font_width)
    drops = [0 for _ in range(cols)]

    start_t = time.time()
    try:
        while True:
            now = time.time()
            if (now - start_t) > duration:
                break
            if skip_on_keypress:
                try:
                    k = stdscr.getch()
                    if k != -1:
                        break
                except Exception:
                    pass
            stdscr.erase()
            for i in range(cols):
                ch = charset[int(time.time() * 1000 + i) % len(charset)]
                x = i
                y = drops[i]
                if y >= sh:
                    if time.time() % 1 > 0.975:
                        drops[i] = 0
                        y = 0
                    else:
                        y = sh - 1
                try:
                    stdscr.addstr(y, x, ch, curses.color_pair(1))
                except curses.error:
                    # ignore drawing errors on tiny terminals
                    pass
                drops[i] = drops[i] + 1
            stdscr.refresh()
            time.sleep(0.03)
    except KeyboardInterrupt:
        pass
    finally:
        stdscr.nodelay(False)
        curses.curs_set(1)


def play_matrix(duration=3.0, skip_on_keypress=True):
    """
    Initialize colors and run curses matrix_rain.
    Run in try/except because some terminals may not support curses well.
    """
    try:
        def wrapper(stdscr):
            # init color: green on black if possible
            curses.start_color()
            curses.use_default_colors()
            try:
                curses.init_pair(1, curses.COLOR_GREEN, -1)
            except Exception:
                pass
            matrix_rain(stdscr, duration=duration, skip_on_keypress=skip_on_keypress)
        curses.wrapper(wrapper)
    except Exception as e:
        # fallback: small textual animation
        console.print("···", style="muted")
        time.sleep(min(duration, 0.8))


# --------------------------
# Typing effect
# --------------------------
def type_effect(text: str, delay: float = 0.008):
    for ch in text:
        console.print(ch, end="", style="machine")
        sys.stdout.flush()
        time.sleep(delay + (0.0 if ch == " " else 0.003 * (0.5 - time.time() % 1)))
    console.print("")  # newline


# --------------------------
# AWS helpers
# --------------------------
def boto3_session(profile: str = None, region: str = None):
    if profile:
        return boto3.Session(profile_name=profile, region_name=region)
    else:
        return boto3.Session(region_name=region)


def list_profiles() -> List[str]:
    # Use AWS CLI to get profiles reliably
    try:
        res = os.popen("aws configure list-profiles").read()
        profiles = [p.strip() for p in res.splitlines() if p.strip()]
        return profiles
    except Exception:
        return []


def list_regions(session: boto3.Session) -> List[str]:
    try:
        ec2 = session.client("ec2")
        regions = ec2.describe_regions()["Regions"]
        return [r["RegionName"] for r in regions]
    except Exception as e:
        return []


def get_ssm_instances(session: boto3.Session) -> List[str]:
    """
    Return list of instance ids that are SSM-enabled in the session's region(s).
    """
    try:
        ssm = session.client("ssm")
        out = []
        paginator = ssm.get_paginator("describe_instance_information")
        for page in paginator.paginate():
            for info in page.get("InstanceInformationList", []):
                out.append(info["InstanceId"])
        return out
    except Exception:
        return []


def describe_instances_by_ids(session: boto3.Session, ids: List[str]) -> Dict[str, Dict]:
    ec2 = session.client("ec2")
    res = {}
    if not ids:
        return res
    try:
        resp = ec2.describe_instances(InstanceIds=ids)
        for r in resp.get("Reservations", []):
            for inst in r.get("Instances", []):
                name = None
                for t in inst.get("Tags", []):
                    if t.get("Key") == "Name":
                        name = t.get("Value")
                        break
                res[inst["InstanceId"]] = {"name": name or "", "id": inst["InstanceId"]}
    except Exception as e:
        # ignore errors
        pass
    return res


def resolve_instances(session: boto3.Session, raw_list: List[str]) -> List[str]:
    """
    raw_list may contain instance ids or Name tags. Return instance IDs (running) that match.
    """
    ec2 = session.client("ec2")
    out = []
    for item in raw_list:
        item = item.strip()
        if not item:
            continue
        if re.match(r"^i-[0-9a-fA-F]+$", item):
            out.append(item)
            continue
        # treat as Name tag exact or glob
        try:
            resp = ec2.describe_instances(
                Filters=[{"Name": "tag:Name", "Values": [item]}, {"Name": "instance-state-name", "Values": ["running"]}]
            )
            ids = []
            for r in resp.get("Reservations", []):
                for inst in r.get("Instances", []):
                    ids.append(inst["InstanceId"])
            if ids:
                out.extend(ids)
            else:
                log(f"[WARN] No running instance found with Name tag '{item}'")
        except Exception as e:
            log(f"[WARN] Error resolving '{item}': {e}")
    # dedupe preserving order
    seen = set()
    result = []
    for i in out:
        if i not in seen:
            seen.add(i)
            result.append(i)
    return result


# --------------------------
# SSM send and wait utilities
# --------------------------
def send_ssm_command(session: boto3.Session, instance_id: str, command: str, timeout_seconds: int = 300):
    """
    Send a single SSM RunShellScript command to a single instance, poll for completion, return dict.
    """
    ssm = session.client("ssm")
    try:
        res = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [command]},
            TimeoutSeconds=timeout_seconds,
        )
        cmd_id = res["Command"]["CommandId"]
    except Exception as e:
        return {"instance": instance_id, "status": "FailedToSend", "error": str(e), "stdout": "", "stderr": ""}

    # poll
    for _ in range(int(timeout_seconds / 2)):
        time.sleep(2)
        try:
            inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
            status = inv.get("Status")
            if status in ("Success", "Failed", "TimedOut", "Cancelled"):
                return {"instance": instance_id, "status": status, "stdout": inv.get("StandardOutputContent", ""), "stderr": inv.get("StandardErrorContent", "")}
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(1)
        except Exception:
            # keep polling
            pass
    return {"instance": instance_id, "status": "TimedOut", "stdout": "", "stderr": ""}


def run_parallel(session: boto3.Session, instance_ids: List[str], command: str, concurrency: int = 8):
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        future_to_id = {ex.submit(send_ssm_command, session, iid, command): iid for iid in instance_ids}
        for fut in as_completed(future_to_id):
            res = fut.result()
            results.append(res)
            # log right away
            if res.get("status") == "Success":
                log(f"[{res['instance']}] SUCCESS")
                if res.get("stdout"):
                    log(f"[{res['instance']}] STDOUT:\n{res['stdout']}")
            else:
                log(f"[{res['instance']}] STATUS={res.get('status')}; STDERR:\n{res.get('stderr')}")
    return results


# --------------------------
# Higher-level actions
# --------------------------
def action_git_pull(session: boto3.Session, instance_ids: List[str], repo_path: str, concurrency: int):
    cmd = f"cd {shlex.quote(repo_path)} && git pull"
    return run_parallel(session, instance_ids, cmd, concurrency)


def action_git_checkout(session: boto3.Session, instance_ids: List[str], repo_path: str, branch: str, concurrency: int):
    cmd = f"cd {shlex.quote(repo_path)} && git fetch && git checkout {shlex.quote(branch)}"
    return run_parallel(session, instance_ids, cmd, concurrency)


def action_custom(session: boto3.Session, instance_ids: List[str], custom_cmd: str, concurrency: int):
    return run_parallel(session, instance_ids, custom_cmd, concurrency)


def action_find_files(session: boto3.Session, instance_ids: List[str], repo_path: str, filename_pattern: str, concurrency: int):
    # Use find: limit depth to 6 to avoid runaway searches (configurable)
    find_cmd = f"find {shlex.quote(repo_path)} -maxdepth 6 -type f -name {shlex.quote(filename_pattern)} -print"
    return run_parallel(session, instance_ids, find_cmd, concurrency)


def action_append_after_match(session: boto3.Session, instance_id: str, filepath: str, match_pattern: str, new_line: str):
    # We will run awk on the remote side. The awk script will:
    # - print each line
    # - upon the FIRST line matching the regex, capture leading indentation and then print the new_line with same indentation
    # Use -v to pass pattern and new_line to awk safely.
    # Note: pattern is a plain string; we use index($0, pattern) or $0 ~ pattern for regex.
    # We'll prefer regex match ($0 ~ pattern). Escape single quotes in pattern/new_line before embedding.
    esc_pattern = match_pattern.replace("'", "'\"'\"'")
    esc_newline = new_line.replace("'", "'\"'\"'")
    awk_script = (
        r"awk -v pattern='" + esc_pattern + r"' -v new_line='" + esc_newline +
        r"""' 'BEGIN{found=0} {print} !found && $0 ~ pattern { match($0, /[^[:space:]]/); lead = (RSTART>1?substr($0,1,RSTART-1):""); print lead new_line; found=1 }' """
        + shlex.quote(filepath) + " > /tmp/tm_cli_tmp.$$ && mv /tmp/tm_cli_tmp.$$ " + shlex.quote(filepath)
    )
    return send_ssm_command(session, instance_id, awk_script)


# --------------------------
# Interactive menus & helpers
# --------------------------
def interactive_select_profile() -> str:
    profiles = list_profiles()
    if not profiles:
        console.print("No AWS profiles found via `aws configure`. Use --profile or configure AWS.", style="error")
        return ""
    if len(profiles) == 1:
        return profiles[0]
    choice = questionary.select("Select AWS profile:", choices=profiles).ask()
    return choice


def interactive_select_region(session: boto3.Session) -> str:
    regions = list_regions(session)
    if not regions:
        console.print("Could not list regions; using default", style="error")
        return ""
    choice = questionary.select("Select AWS region:", choices=regions).ask()
    return choice


def interactive_select_instances(session: boto3.Session) -> List[str]:
    ssm_ids = get_ssm_instances(session)
    if not ssm_ids:
        console.print("No SSM-enabled instances found in this region.", style="error")
        return []
    desc = describe_instances_by_ids(session, ssm_ids)
    choices = []
    for iid in ssm_ids:
        name = desc.get(iid, {}).get("name") or "NoName"
        choices.append(f"{name} ({iid})")
    selected = questionary.checkbox("Select instances (space to select, enter to confirm):", choices=choices).ask()
    if not selected:
        return []
    ids = [re.search(r"(i-[0-9a-fA-F]+)", s).group(1) for s in selected]
    return ids


# --------------------------
# Argument parsing and main flow
# --------------------------
def parse_args():
    p = argparse.ArgumentParser(description="The Machine — AWS SSM Multi-instance CLI")
    p.add_argument("--profile", help="AWS profile name")
    p.add_argument("--region", help="AWS region")
    p.add_argument("--instances", help="Comma-separated instance ids or Name tags")
    p.add_argument("--action", help="Action: ssm | git-pull | git-checkout | custom | find | append")
    p.add_argument("--cmd", help="Custom command for action=custom")
    p.add_argument("--repo", help="Repo path on instance for git actions (e.g. /opt/app)")
    p.add_argument("--branch", help="Branch name for git-checkout")
    p.add_argument("--file", help="Filename or pattern for find")
    p.add_argument("--match", help="Match pattern (regex) for append after match (e.g. '^def run\\(\\)') or literal")
    p.add_argument("--newline", help="Text of line to insert (for append)")
    p.add_argument("--concurrency", type=int, default=8, help="Parallel concurrency")
    p.add_argument("--parallel", action="store_true", help="Run parallel for provided action")
    p.add_argument("--batch", action="store_true", help="Batch mode (no interactive prompts); requires other args")
    p.add_argument("--skip-rain", action="store_true", help="Skip matrix rain animation")
    return p.parse_args()


def main():
    args = parse_args()

    # Show animation unless skipped or in batch mode
    if not args.skip_rain and not args.batch:
        console.print("\n")
        play_matrix(duration=3.5, skip_on_keypress=True)
        type_effect("[THE MACHINE] Initializing AWS interface...\n", delay=0.01)
    else:
        type_effect("[THE MACHINE] Initializing (fast)...\n", delay=0.004)

    # Choose profile & region
    profile = args.profile
    if not profile and not args.batch:
        profile = interactive_select_profile()
    if profile:
        log(f"Using AWS profile: {profile}")

    # create session
    try:
        session = boto3_session(profile=profile, region=args.region)
    except Exception as e:
        console.print(f"Error creating boto3 session: {e}", style="error")
        session = boto3_session(region=args.region)

    region = args.region
    if not region and not args.batch:
        region = interactive_select_region(session)
    if region:
        log(f"Using region: {region}")
        # recreate session with region
        session = boto3_session(profile=profile, region=region)

    # determine instances
    instance_ids = []
    if args.instances:
        raw = [s.strip() for s in args.instances.split(",") if s.strip()]
        instance_ids = resolve_instances(session, raw)
    elif not args.batch:
        instance_ids = interactive_select_instances(session)

    if not instance_ids:
        console.print("No instances selected. Provide --instances or choose interactively.", style="error")
        sys.exit(1)

    log(f"Target instances: {', '.join(instance_ids)}")

    # If action provided in args -> batch mode behavior
    if args.batch or args.action:
        action = args.action
        if not action:
            console.print("Batch mode requested but no --action provided.", style="error")
            sys.exit(1)
        # Map actions
        if action == "ssm":
            # SSM session supports single target
            target = instance_ids[0]
            console.print(f"Starting SSM session to {target} ... (run aws ssm start-session locally)", style="machine")
            # delegate to aws cli (interactive)
            os.execvp("aws", ["aws", "ssm", "start-session", "--target", target])
        elif action == "git-pull":
            repo = args.repo or "/opt/app"
            if args.parallel:
                results = action_git_pull(session, instance_ids, repo, args.concurrency)
            else:
                results = action_git_pull(session, instance_ids, repo, 1)
            console.print("Completed git-pull", style="machine")
            sys.exit(0)
        elif action == "git-checkout":
            repo = args.repo or "/opt/app"
            branch = args.branch or "main"
            if args.parallel:
                action_git_checkout(session, instance_ids, repo, branch, args.concurrency)
            else:
                action_git_checkout(session, instance_ids, repo, branch, 1)
            console.print(f"Checkout complete -> {branch}", style="machine")
            sys.exit(0)
        elif action == "custom":
            if not args.cmd:
                console.print("--cmd required for custom action in batch mode", style="error")
                sys.exit(1)
            if args.parallel:
                action_custom(session, instance_ids, args.cmd, args.concurrency)
            else:
                action_custom(session, instance_ids, args.cmd, 1)
            console.print("Custom command executed.", style="machine")
            sys.exit(0)
        elif action == "find":
            repo = args.repo or "/opt/app"
            pattern = args.file or "*"
            action_find_files(session, instance_ids, repo, pattern, args.concurrency)
            console.print("Find executed.", style="machine")
            sys.exit(0)
        elif action == "append":
            if not args.file and not args.match:
                console.print("--file and --match required for append in batch mode", style="error")
                sys.exit(1)
            # For batch mode we assume filepath provided via --file (full path) or we will find first match
            repo = args.repo or "/opt/app"
            filepath = args.file
            match = args.match or r"^def run\(\)"
            newline = args.newline or ""
            if not newline:
                console.print("--newline required for append", style="error")
                sys.exit(1)
            # If filepath is not absolute, find first matching file on each instance
            for iid in instance_ids:
                chosen = filepath
                if not chosen:
                    # run find on that instance
                    find_res = action_find_files(session, [iid], repo, "*.py", 1)
                    # find_res returns list of results; extract stdout from first result
                    if find_res and find_res[0].get("stdout"):
                        lines = [l for l in find_res[0]["stdout"].splitlines() if l.strip()]
                        if lines:
                            chosen = lines[0]
                if not chosen:
                    log(f"No file found to append on {iid}")
                    continue
                res = action_append_after_match(session, iid, chosen, match, newline)
                log(f"Append result for {iid}: {res.get('status')}")
            console.print("Append(s) complete.", style="machine")
            sys.exit(0)
        else:
            console.print(f"Unknown action: {action}", style="error")
            sys.exit(1)

    # If interactive mode:
    while True:
        choice = questionary.select(
            "Choose action",
            choices=[
                "SSM Connect (single)",
                "Git Pull (repo)",
                "Git Checkout (branch)",
                "Custom Command",
                "Find files in repo",
                "Append after match (insert line)",
                "Exit",
            ],
        ).ask()

        if choice.startswith("Exit"):
            break

        if choice.startswith("SSM Connect"):
            tgt = instance_ids[0]
            console.print(f"Starting SSM session to {tgt} ... launching aws cli", style="machine")
            log(f"SSM start-session -> {tgt}")
            os.execvp("aws", ["aws", "ssm", "start-session", "--target", tgt])

        elif choice.startswith("Git Pull"):
            repo = questionary.text("Repo path on instance (e.g. /opt/app):", default="/opt/app").ask()
            par = questionary.confirm("Run in parallel?", default=True).ask()
            if par:
                concurrency = questionary.text("Concurrency:", default=str(8)).ask()
                action_git_pull(session, instance_ids, repo, int(concurrency))
            else:
                for iid in instance_ids:
                    send_res = send_ssm_command(session, iid, f"cd {shlex.quote(repo)} && git pull")
                    log(f"git pull {iid} -> {send_res.get('status')}")

        elif choice.startswith("Git Checkout"):
            repo = questionary.text("Repo path on instance (e.g. /opt/app):", default="/opt/app").ask()
            branch = questionary.text("Branch name to checkout:", default="main").ask()
            par = questionary.confirm("Run in parallel?", default=True).ask()
            if par:
                concurrency = int(questionary.text("Concurrency:", default="8").ask())
                action_git_checkout(session, instance_ids, repo, branch, concurrency)
            else:
                for iid in instance_ids:
                    res = send_ssm_command(session, iid, f"cd {shlex.quote(repo)} && git fetch && git checkout {shlex.quote(branch)}")
                    log(f"git checkout {iid} -> {res.get('status')}")

        elif choice.startswith("Custom Command"):
            custom_cmd = questionary.text("Enter custom shell command:").ask()
            par = questionary.confirm("Run in parallel?", default=True).ask()
            if par:
                concurrency = int(questionary.text("Concurrency:", default="8").ask())
                action_custom(session, instance_ids, custom_cmd, concurrency)
            else:
                for iid in instance_ids:
                    res = send_ssm_command(session, iid, custom_cmd)
                    log(f"custom {iid} -> {res.get('status')}")

        elif choice.startswith("Find files"):
            repo = questionary.text("Repo path:", default="/opt/app").ask()
            pattern = questionary.text("Filename pattern (glob):", default="*.py").ask()
            par = questionary.confirm("Run on all instances in parallel?", default=False).ask()
            if par:
                concurrency = int(questionary.text("Concurrency:", default="6").ask())
                results = action_find_files(session, instance_ids, repo, pattern, concurrency)
                for r in results:
                    console.print(f"[{r['instance']}] ({r['status']})")
                    if r.get("stdout"):
                        console.print(r.get("stdout"), style="muted")
            else:
                for iid in instance_ids:
                    res = run_parallel(session, [iid], f"find {shlex.quote(repo)} -maxdepth 6 -type f -name {shlex.quote(pattern)} -print", 1)
                    for r in res:
                        console.print(f"[{r['instance']}] files:\n{r.get('stdout')}", style="muted")

        elif choice.startswith("Append after match"):
            repo = questionary.text("Repo path:", default="/opt/app").ask()
            # find files first
            pattern = questionary.text("Filename pattern (glob):", default="*.py").ask()
            concurrency = int(questionary.text("Concurrency for find:", default="6").ask())
            find_results = action_find_files(session, instance_ids, repo, pattern, concurrency)
            # show found files per instance and ask which to edit
            for fr in find_results:
                iid = fr["instance"]
                stdout = fr.get("stdout") or ""
                files = [l for l in stdout.splitlines() if l.strip()]
                if not files:
                    console.print(f"[{iid}] No matching files found", style="muted")
                    continue
                console.print(f"Files found on {iid}:", style="machine")
                for idx, fpath in enumerate(files):
                    console.print(f"  {idx+1}. {fpath}", style="muted")
                chosen = questionary.text("Paste exact path to edit (or leave empty to skip):").ask()
                if not chosen:
                    continue
                match = questionary.text("Match regex to find (default '^def run\\(\\)'):", default="^def run\\(\\)").ask()
                newline = questionary.text("New line to insert AFTER match (exact text):").ask()
                res = action_append_after_match(session, iid, chosen, match, newline)
                console.print(f"[{iid}] -> {res.get('status')}", style="machine")
                log(f"append on {iid} file={chosen} status={res.get('status')}")

        else:
            console.print("Unknown selection", style="error")

    console.print("Exiting. Logs: " + LOG_FILE, style="muted")
    log("Session ended by user.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\nInterrupted. Bye.", style="error")
