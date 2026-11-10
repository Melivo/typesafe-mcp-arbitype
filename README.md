# Arbitype

**Typed decision tools for AI agents.**

Classify · Score · Verify · Gate · Route · Review

**MCP-native · Powered by TypeSafe Jev**\
[![Upstream CI](https://github.com/Renwang-Huang/arbitype/actions/workflows/ci.yml/badge.svg)](https://github.com/Renwang-Huang/arbitype/actions/workflows/ci.yml)
[![Upstream latest release](https://img.shields.io/github/v/release/Renwang-Huang/arbitype?display_name=tag&sort=semver)](https://github.com/Renwang-Huang/arbitype/releases)
[![Upstream Arbitype on PyPI](https://img.shields.io/pypi/v/arbitype?logo=pypi&logoColor=white)](https://pypi.org/project/arbitype/)
[![Upstream MIT license](https://img.shields.io/github/license/Renwang-Huang/arbitype)](https://github.com/Renwang-Huang/arbitype/blob/main/LICENSE)
![Python 3.10 or newer](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
[![MCP 2026-07-28](https://img.shields.io/badge/MCP-2026--07--28-6F42C1)](https://github.com/modelcontextprotocol/modelcontextprotocol/tree/main/docs/specification/2026-07-28)
[![Upstream Arbitype listed in the MCP Registry](https://img.shields.io/badge/MCP%20Registry-listed-2ea44f)](https://registry.modelcontextprotocol.io/?q=io.github.Renwang-Huang%2Farbitype)

[Quick start](#quick-start) ·
[Why Arbitype?](#why-arbitype) ·
[Tools](#tools) ·
[Benchmarks](#benchmark-snapshot) ·
[Documentation](#documentation)

## What is Arbitype?

Arbitype is an MCP-native typed decision layer for AI agents, powered by
[TypeSafe Jev](https://typesafe.ai). It turns probabilistic judgments into
structured decision primitives that an agent or program can consume directly.

This repository is the public fork [Melivo/typesafe-mcp-arbitype](https://github.com/Melivo/typesafe-mcp-arbitype) of [Renwang-Huang/arbitype](https://github.com/Renwang-Huang/arbitype). It keeps the upstream product and package identity while adding experimental image support; fork-only changes are available from this GitHub source, not from the upstream PyPI package or MCP Registry listing. The upstream availability badges above describe upstream CI and releases only. This fork has not published its own PyPI package, MCP Registry listing, or fork release.

The fork adds experimental `evaluate` support for inline PNG/JPEG `assets` and
the `allow_images` opt-in, with Noul, Choice and Score questions plus Box
localization. Two synthetic M04 cases were sent through the actual local MCP
STDIO path to the provider (PNG and JPEG; Box was requested for PNG). These
limited observations are not image-quality or production-readiness evidence;
the [image guide](docs/IMAGE_SUPPORT.md) records their results and preserves
the separate historical M01 and M03 findings.

> [!NOTE]
> Arbitype is an independent open-source project. It is not an official
> TypeSafe AI product or an official integration for any particular agent host.

The following MCP Registry name marker is retained for the upstream listing; it
does not identify a separate fork Registry entry.

<!-- mcp-name: io.github.Renwang-Huang/arbitype -->

## Quick Start

To use the fork's changes, start from its source checkout. The PyPI package
`arbitype` and the Registry listing belong to upstream and do not contain the
fork-only changes.

~~~bash
git clone https://github.com/Melivo/typesafe-mcp-arbitype.git
cd typesafe-mcp-arbitype
export TYPESAFE_API_KEY="your-key"
python3 -B server.py
~~~

Configure an MCP host to run `python3 -B` with the absolute path to
`server.py` as its argument. Keep `TYPESAFE_API_KEY` in the host's process
environment; do not put it in the command or MCP arguments.

If you prefer `uvx`, install directly from the fork at a full commit ID:

~~~bash
uvx --from 'git+https://github.com/Melivo/typesafe-mcp-arbitype.git@<full-commit>' arbitype
~~~

`<full-commit>` is an intentionally incomplete placeholder, not a usable
revision. Replace it with the full commit ID you want to run; a branch name
such as `main` is not a reproducible pin. No fork package version is implied.

### Host setup

Inspect detected hosts before writing anything:

~~~bash
arbitype setup --detect --dry-run
~~~

Then apply a reviewed plan:

~~~bash
arbitype setup codex
arbitype doctor --json
~~~

The setup flow is plan → unified diff → confirmation → backup → apply.
Use claude, cursor, or vscode in place of codex. Detailed host formats,
secret forwarding, --yes, --remove, ownership, and non-interactive behavior
are documented in [docs/HOST_SETUP.md](docs/HOST_SETUP.md).

### Configuration

At minimum, set TYPESAFE_API_KEY. To select a model explicitly:

~~~bash
export TYPESAFE_API_KEY="your-key"
export TYPESAFE_MODEL="jev-latest"
~~~

See the [full configuration reference](docs/CONFIGURATION.md) for endpoints,
timeouts, retries, and request/response limits.

### Upstream compatibility identifiers

The package and compatibility command names below are retained for compatibility
with upstream Arbitype and its migration path; their presence does not mean the
fork has published those distributions. For upstream users, see
[docs/REGISTRY_MIGRATION.md](docs/REGISTRY_MIGRATION.md).

For the safe migration from the historical distribution:

These uninstall/install commands describe the upstream migration path; they do
not install this fork's changes.

~~~bash
python -m pip uninstall typesafe-mcp
python -m pip install arbitype
~~~

| Surface | Canonical | Legacy compatibility |
| --- | --- | --- |
| PyPI / CLI | arbitype | typesafe-mcp, typesafe-codex-mcp |
| Python imports | arbitype | typesafe_mcp, typesafe_codex_mcp |
| MCP tools | route, review | codex_route, codex_review |

## Why Arbitype?

Arbitype is designed around contracts and bounded decisions rather than
free-form prose:

| Principle | What it means |
| --- | --- |
| **Contract-first** | Requests and provider responses are validated before they reach the agent. |
| **Safe by default** | Credentials stay out of MCP tool arguments and generated host configuration. |
| **Agent-oriented** | Purpose-built classify, verify, gate, route, review, and score primitives. |
| **Portable** | Zero third-party runtime dependencies and standard MCP STDIO. |
| **Measured** | Public tool-selection and decision-stability evaluation assets. |

The decision flow is:

~~~text
free-form state
      ↓
  TypeSafe Jev
      ↓
probabilistic judgment
      ↓
    Arbitype
      ↓
typed decision + probability
      ↓
agent / code branch
~~~

## Real Use Cases

Start with one of the small, runnable fixtures:

| Scenario | Tool | What it demonstrates |
| --- | --- | --- |
| [Support routing](examples/support-routing/) | route | Choose one next action without executing it. |
| [PR verification](examples/pr-verification/) | verify | Check several claims independently. |
| [Release review](examples/release-review/) | review / gate | Separate holistic review from thresholded checks. |
| [Agent next step](examples/agent-next-step/) | route | Keep the next workflow action bounded. |

The example outputs are illustrative fixtures. They are not live model results,
accuracy claims, or authorization decisions.

## Tools

Arbitype advertises nine read-only, idempotent MCP tools:

| Tool | Use when | Result |
| --- | --- | --- |
| evaluate | You need a custom typed Jev question set. | Raw typed Jev response. |
| classify | You need one choice from unordered labels. | Choice and probability distribution. |
| score | You need one ordered rating or level. | Weighted score and distribution. |
| check | You need a probability for one bounded criterion. | Noul yes/no probability. |
| verify | You need several named claims checked independently. | Noul answer per claim. |
| gate | You need checks and thresholds transformed into a signal. | pass, review, or fail. |
| route | You need one suggested next action. | One action; no action execution. |
| review | You need a holistic quality or risk assessment. | Review decision and evidence. |
| health | You need local diagnostics or an explicit live check. | Configuration and optional provider health. |

### Tool Selection Guide

| Need | Use | Do not substitute |
| --- | --- | --- |
| One unordered label | classify | route, which selects an action |
| One ordered rating | score | classify, which has no order |
| One bounded proposition | check | verify, which handles multiple claims |
| Several named claims | verify | review, which assesses a whole object |
| Checks plus thresholds | gate | review, which is holistic |
| One next action | route | classify, which returns a category |
| Whole diff, plan, release, or report | review | verify, which answers claim by claim |

Tool descriptions also state USE WHEN and DO NOT USE WHEN boundaries so hosts
can select a primitive without hidden prompt conventions.

Probabilities and confidence are model signals, not proof. gate and review
are advisory decision transformations, not authorization systems, security
boundaries, or approval engines.

## Architecture

~~~mermaid
flowchart LR
    host["AI Agent / MCP Host<br/>Codex · Claude · Cursor · VS Code"]
    arbitype["Arbitype<br/>Typed decision tools"]
    jev["TypeSafe Jev<br/>System One Model"]
    env["TYPESAFE_API_KEY<br/>process environment"]

    host -->|MCP| arbitype
    arbitype -->|validated HTTPS| jev
    jev -->|typed probabilistic judgment| arbitype
    arbitype -->|structured decision| host
    env -. credential .-> arbitype
~~~

The public product is Arbitype; TypeSafe Jev is the current provider. Provider
configuration intentionally keeps the TYPESAFE_* names because the credential
and endpoint belong to TypeSafe.

## Benchmark Snapshot

Recorded on the public 120-case Jev-mediated tool-selection evaluation:

| Metric | Result |
| --- | ---: |
| Tool-selection accuracy | **96.67%** |
| Invalid-tool rate | **0%** |
| Schema-valid rate | **100%** |

This is a Jev-mediated evaluation over Arbitype's advertised MCP tool catalog.
It is not a Codex, Claude, or Cursor host benchmark and is not a general
model-performance guarantee.

Details and reproducible assets:

- [Benchmark methodology](BENCHMARK.md)
- [Tool-selection dataset](evals/tool_selection/)
- [Recorded reports](evals/reports/)

## Security Boundaries

Arbitype is a local MCP adapter and typed decision layer. It is not:

- a sandbox for untrusted code;
- an authorization or identity system;
- a prompt-injection firewall;
- a security approval boundary; or
- an official TypeSafe AI product.

It does not execute actions suggested by route, edit files, run shell
commands, or treat model probabilities as proof. Read [SECURITY.md](SECURITY.md)
before using live credentials.

## Documentation

- [Host setup](docs/HOST_SETUP.md)
- [Configuration reference](docs/CONFIGURATION.md)
- [MCP Registry migration](docs/REGISTRY_MIGRATION.md)
- [Supply-chain posture](docs/SUPPLY_CHAIN.md)
- [Observability proposal](docs/OBSERVABILITY.md)
- [Experimental image support, limits, and local MCP recipe](docs/IMAGE_SUPPORT.md)
- [Testing](TESTING.md)
- [Benchmarks](BENCHMARK.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)
- [Upstream MCP Registry entry](https://registry.modelcontextprotocol.io/?q=io.github.Renwang-Huang%2Farbitype)
- [Upstream Glama listing](https://glama.ai/mcp/servers/Renwang-Huang/arbitype) (indexing and metadata refresh may lag publication)

## Development

The test suite uses local fakes and does not need an API key:

~~~bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
make check
make release-check
~~~

The official MCP Python SDK interoperability smoke test is in
[scripts/official_sdk_smoke.py](scripts/official_sdk_smoke.py). A live Jev
check is opt-in and paid:

~~~bash
TYPESAFE_API_KEY="your-key" arbitype doctor --live
~~~

Normal CI does not call the provider or run paid benchmarks.
