# EMOS Brain Modules

This package contains the DeepSeek-compatible discussion, robot-resume, skill, and model-client modules used by the scenario-driven EMOS integration. It is a Python library surface consumed by `algorithm.emos`; it does not provide a standalone command-line entry point and it does not load `.env` files.

## Installation

Install the maintained manifest from the repository root in the environment that runs EMOS:

```bash
python -m pip install -r algorithm/emos/requirements.txt
```

The manifest includes `numpy`, which is imported by `brain/robot_resume.py`, as well as the OpenAI-compatible client, Pydantic, terminal formatting, and docstring parsing dependencies.

## Provider Configuration

Set the provider credential in the process environment before calling the EMOS discussion path:

```bash
export DEEPSEEK_API_KEY="..."
```

`brain/API/Model_API.py` reads `DEEPSEEK_API_KEY` with `os.getenv`. It does not call `python-dotenv`; a local `.env` file has no effect unless the caller explicitly loads it. Do not commit credentials or print them in validation logs.

The public integration boundary is `EMOSDiscussionManager` in `algorithm/emos/engine.py`. Callers provide a scenario and an existing EAI/Isaac Lab-compatible environment; these brain modules do not construct or launch the simulator.

## Lightweight Verification

From the repository root, check the maintained EMOS brain sources without making an API request:

```bash
python -m py_compile \
  algorithm/emos/brain/*.py \
  algorithm/emos/brain/API/*.py \
  algorithm/emos/brain/actions/*.py \
  algorithm/emos/brain/skills/*.py
```

This verifies syntax only. It does not validate provider credentials, paid API access, model responses, or simulator integration.
