"""Run the Agent Airways browser agent five times and record what happened.

Each trial starts the unchanged course program in a new Python process. That
gives every trial a new headless Chromium with an empty profile, so the
practice site starts again on its search screen (the "reset" between runs).
The program runs its three-tool flight search first and then the full 16-tool
pretend reservation.

Once per trial the program prints the review and waits for a person to type
APPROVE. So that all five trials are handled the same way, this runner answers
the prompt itself: it compares the printed review with the traveler's request
field by field and types APPROVE only if everything matches, otherwise STOP.

Usage, from the repository root:
    python google_harness_extra_credit/run_five_trials.py
"""

import codecs
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
REPO_ROOT = FOLDER.parent
PROGRAM = REPO_ROOT / "Agent_Airways_One_Agent_16_Tools_Student_Tools_Annotated.py"
TOOLS_REPO = REPO_ROOT / "agent-airways-class-browser-agent"
LOG_DIR = FOLDER / "logs"
SHOT_DIR = FOLDER / "screenshots"
TRIALS = 5
TRIAL_TIMEOUT_SECONDS = 20 * 60
REVIEW_HEADER = "PLEASE CHECK THIS PRETEND RESERVATION:"
APPROVAL_PROMPT = "Type APPROVE to confirm, or anything else to stop:"
PROGRAM_OUTPUTS = ("airways_three_trace.json", "airways_three.png", "airways_full_trace.json", "airways_full.png")
TERMINAL_CODES = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SPINNER = re.compile(r"(?:pulling manifest ?[⠀-⣿]? *)+")


def clean_console_log(path):
    """Make a saved console log readable: one newline style, no terminal control
    codes or spinner frames from `ollama pull`, and paths relative to the repo."""
    # Read bytes, not text: text mode would already turn "\r\r\n" into two newlines.
    text = path.read_bytes().decode("utf-8", errors="replace")
    text = text.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    text = TERMINAL_CODES.sub("", text)
    text = SPINNER.sub("pulling manifest ", text)
    text = text.replace(str(REPO_ROOT), ".")
    path.write_text(text, encoding="utf-8", newline="\n")


def find_travel_date(console_text):
    """The date the program put in its request (today + 14 days when it started)."""
    match = re.search(r"from DCA to BOS on (\d{4}-\d{2}-\d{2})", console_text)
    if match:
        return match.group(1)
    return (date.today() + timedelta(days=14)).isoformat()


def expected_review(travel_date):
    # The same trip as GOAL in the program, written the way the review card shows it.
    return {
        "Route": "DCA → BOS",
        "Date": travel_date,
        "Flight": "AA101",
        "Seat": "1A",
        "Passenger": "Test Traveler",
        "Email": "test.traveler@example.com",
        "Checked bag": "No",
        "Priority boarding": "No",
    }


def check_printed_review(console_text, travel_date):
    """Return (matches, problems) for the review printed just before the prompt."""
    start = console_text.rfind(REVIEW_HEADER)
    end = console_text.find(APPROVAL_PROMPT, start)
    if start == -1 or end == -1:
        return False, ["no review was printed before the prompt"]
    try:
        review = json.loads(console_text[start + len(REVIEW_HEADER):end])
    except ValueError:
        return False, ["the printed review is not valid JSON"]
    problems = []
    for label, wanted in expected_review(travel_date).items():
        shown = str(review.get(label, ""))
        same = shown.lower() == wanted.lower() if label == "Email" else shown == wanted
        if not same:
            problems.append(f"{label}: expected {wanted!r}, shown {shown!r}")
    if not str(review.get("Mock total", "")).strip():
        problems.append("Mock total is empty")
    return not problems, problems


def action_of(step):
    try:
        return json.loads(step["model_proposal"]).get("action", "?")
    except (ValueError, TypeError, AttributeError):
        return "unparsed_reply"


def summarize_trace(path):
    """Counts and key results from one of the program's saved trace files."""
    if not path.exists():
        return {"status": "no trace file", "steps": 0, "failed": 0, "trace": []}
    saved = json.loads(path.read_text(encoding="utf-8"))
    trace = saved.get("trace", [])
    steps = [s for s in trace if "model_proposal" in s]
    failed = [s for s in steps if s["tool_result"].get("ok") is False]
    info = {"status": saved.get("status", ""), "steps": len(steps), "failed": len(failed), "trace": trace}
    for step in steps:
        result = step["tool_result"]
        if action_of(step) == "confirm_demo_reservation" and result.get("data", {}).get("clicked"):
            info["confirmation_code"] = result["data"].get("confirmation_code", "")
        if action_of(step) == "verify_confirmation":
            checks = result.get("data", {}).get("checks", {})
            if checks:
                info["verify_checks"] = checks
                info["verify_ok"] = result.get("ok") is True
    return info


def write_step_list(info, path):
    lines = []
    for step in info["trace"]:
        if "model_proposal" not in step:
            lines.append(f"(no model step) {json.dumps(step, ensure_ascii=False)[:300]}")
            continue
        result = step["tool_result"]
        mark = "ok " if result.get("ok") else "FAIL"
        lines.append(f"{step['step']:>2}  {mark}  {step['model_proposal']}\n      -> {result.get('message', '')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_trial(number):
    for name in PROGRAM_OUTPUTS:
        (TOOLS_REPO / name).unlink(missing_ok=True)  # never copy a file left by an earlier trial

    env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    started_at = datetime.now().isoformat(timespec="seconds")
    began = time.time()
    process = subprocess.Popen(
        [sys.executable, "-u", str(PROGRAM)],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    chunks = []
    lock = threading.Lock()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    approval = {"asked": False, "typed": "", "problems": []}
    timed_out = False

    with open(LOG_DIR / f"run_{number}_console.txt", "w", encoding="utf-8") as log:
        def copy_output():
            while True:
                data = process.stdout.read1(4096)
                if not data:
                    break
                with lock:
                    text = decoder.decode(data)
                    chunks.append(text)
                    log.write(text)
                    log.flush()

        reader = threading.Thread(target=copy_output, daemon=True)
        reader.start()
        while process.poll() is None:
            time.sleep(0.2)
            with lock:
                console_text = "".join(chunks)
            if not approval["asked"] and APPROVAL_PROMPT in console_text:
                matches, problems = check_printed_review(console_text, find_travel_date(console_text))
                typed = "APPROVE" if matches else "STOP"
                approval.update(asked=True, typed=typed, problems=problems)
                with lock:
                    log.write("\n[test runner] Printed review compared with the request: "
                              + ("every field matches" if matches else "; ".join(problems))
                              + f". Typed {typed}.\n")
                    log.flush()
                process.stdin.write((typed + "\n").encode("utf-8"))
                process.stdin.flush()
            if time.time() - began > TRIAL_TIMEOUT_SECONDS:
                process.kill()
                timed_out = True
                break
        process.wait()
        reader.join(timeout=10)

    clean_console_log(LOG_DIR / f"run_{number}_console.txt")
    seconds = round(time.time() - began, 1)
    copies = {
        "airways_three_trace.json": LOG_DIR / f"run_{number}_three_tool_trace.json",
        "airways_full_trace.json": LOG_DIR / f"run_{number}_full_trace.json",
        "airways_three.png": SHOT_DIR / f"run_{number}_three_tool_end.png",
        "airways_full.png": SHOT_DIR / f"run_{number}_full_end.png",
    }
    for name, destination in copies.items():
        if (TOOLS_REPO / name).exists():
            shutil.copyfile(TOOLS_REPO / name, destination)

    full = summarize_trace(copies["airways_full_trace.json"])
    three = summarize_trace(copies["airways_three_trace.json"])
    write_step_list(full, LOG_DIR / f"run_{number}_full_steps.txt")
    checks = full.get("verify_checks", {})
    return {
        "run": number,
        "result": "Success" if full["status"] == "success" else "Failure",
        "full_status": full["status"],
        "full_steps": full["steps"],
        "full_failed_tool_results": full["failed"],
        "approval_typed": approval["typed"] or "(not asked)",
        "review_problems": "; ".join(approval["problems"]),
        "confirmation_code": full.get("confirmation_code", ""),
        "verify_checks_passed": f"{sum(1 for c in checks.values() if c.get('passed'))}/{len(checks)}" if checks else "",
        "three_tool_status": three["status"],
        "three_tool_steps": three["steps"],
        "seconds": seconds,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "started_at": started_at,
    }


def main():
    LOG_DIR.mkdir(exist_ok=True)
    SHOT_DIR.mkdir(exist_ok=True)
    if not (TOOLS_REPO / "team_tools").is_dir():
        sys.exit("Clone https://github.com/onyourmark/agent-airways-class-browser-agent next to the program first.")

    rows = []
    for number in range(1, TRIALS + 1):
        print(f"Trial {number} of {TRIALS} ...", flush=True)
        row = run_trial(number)
        rows.append(row)
        print(f"  {row['result']}: full status {row['full_status']}, {row['full_steps']} steps, "
              f"{row['full_failed_tool_results']} failed tool results, approval {row['approval_typed']}, "
              f"checks {row['verify_checks_passed'] or '-'}, {row['seconds']} s", flush=True)

    with open(FOLDER / "results.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (FOLDER / "results.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Successes:", sum(1 for row in rows if row["result"] == "Success"), "of", len(rows))


if __name__ == "__main__":
    main()
