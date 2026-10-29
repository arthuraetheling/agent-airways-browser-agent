# Agent Airways Browser Agent

Tianle Yang · CSYE 7374 (Fall 2026)

This repository holds the class browser agent for the [Agent Airways practice site](https://onyourmark.github.io/browser-agent-form/travel/) and my evaluation of it for the Google AI Harness extra-credit assignment.

- [`google_harness_extra_credit/`](google_harness_extra_credit/) contains the extra-credit write-up, the five-run test script, and the logs, traces and screenshots from the runs.
- `Agent_Airways_One_Agent_16_Tools_Student_Tools_Annotated.py` is the agent program from class, unchanged. One local model (qwen3:8b through Ollama) picks one tool at a time from the 16 tools written by the class teams in [onyourmark/agent-airways-class-browser-agent](https://github.com/onyourmark/agent-airways-class-browser-agent). Python validates each request, runs the tool with Playwright, asks a person before the final pretend confirmation, and checks the result.

The practice site is a simulation. Nothing is purchased, and the passenger details are invented.

## Running it

You need Python 3.10 or newer, Git, and [Ollama](https://ollama.com/).

```text
python -m pip install -r requirements.txt
python -m playwright install chromium
ollama pull qwen3:8b
python Agent_Airways_One_Agent_16_Tools_Student_Tools_Annotated.py
```

On its first run, the program clones the class tool repository next to itself. To repeat the five-run test:

```text
python google_harness_extra_credit/run_five_trials.py
```
