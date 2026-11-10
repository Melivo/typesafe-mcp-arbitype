# Testing guide

Arbitype is tested as an MCP STDIO service, not only as a collection of
Python functions.

## Local checks

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
python3 -m pip wheel --no-deps . --wheel-dir /tmp/arbitype-dist
make check
make release-check
```

The CI lint gate runs Ruff with the portable syntax/import rules used by the
runtime and test modules.

The test suite uses standard-library fakes and does not need an API key. It
covers request and response validation, credential redaction, retry behavior,
configuration errors, MCP schemas and annotations, modern metadata and JSON-RPC
boundaries, bounded STDIO framing and invalid UTF-8, shutdown, tool failures,
clean wheel contents, host setup safety, tool-selection dataset contracts, and
benchmark scoring.

The offline tool-selection evaluation validates 120 cases and does not invent
an accuracy result:

```bash
python3 scripts/run_tool_selection_eval.py --dry-run
```

The first real host + Jev selection run is opt-in and records only aggregate
results, case ids, and reproducibility metadata:

```bash
TYPESAFE_API_KEY=your-key \
  python3 scripts/run_tool_selection_live.py \
    --output evals/reports/tool-selection-YYYY-MM-DD.json
```

The report's schema-valid-call rate checks deterministic fixture arguments
against the actual MCP `tools/list` schemas; the dataset does not ask the
model to generate tool arguments.

The decision-stability evaluation is paid and opt-in. It is never part of
normal CI:

```bash
TYPESAFE_API_KEY=your-key python3 scripts/run_stability_benchmark.py --live --repeats 3
```

Stability standard deviation and range are computed per case, across repeated
invocation means. `global_probability_distribution` is only a pooled
descriptive distribution and is not a stability metric.

The old PyPI package migration was tested against the public
`typesafe-mcp==0.5.2` wheel. A same-name metadata-only replacement was not
released because pip can remove the legacy console scripts while replacing
the old distribution. Test existing environments with the documented
uninstall-then-install path instead.

The live verification performed during development made 35 real requests to
`jev-latest`, including a three-round, 19-call matrix covering every provider
backed tool, structured values, batch questions, routing, review, and gate
decisions. Successful responses returned `jev-1.13.0`, calibrated
probabilities/confidence, and `usage`; Arbitype accepted them after strict
contract validation. The boundary calls also confirmed that Noul needs
meaningful instructions or criteria and Score levels cannot be `null`; those
constraints are now rejected locally before a paid request. Live checks are
deliberately excluded from CI because they are paid and require a secret.

## Host check

After registering Arbitype, inspect it without making a provider request:

```bash
arbitype doctor --json
```

For the Codex host specifically, the equivalent inspection commands are:

```bash
codex mcp get arbitype
codex mcp list
```

The live command is opt-in because it makes a paid TypeSafe request:

```bash
TYPESAFE_API_KEY=your-key arbitype doctor --live
```

Do not put a real key in fixtures, command history, `config.toml`, or issue
reports. The host should forward it with `env_vars` (or its equivalent) in the
MCP configuration.

### Experimental image support

The image contract, synthetic examples, offline-default local STDIO-MCP recipe,
one-shot safety rules, and the bounded M01/M03/M04 evidence are documented in
[docs/IMAGE_SUPPORT.md](docs/IMAGE_SUPPORT.md). The recipe is not part of the
normal test suite; its default invocation makes no provider request. A live run
is paid and requires separate task-specific authorization. The recorded M04
attempt is consumed and must not be repeated.

The CI quality gate also runs `scripts/official_sdk_smoke.py` with the official
MCP Python SDK v2. It verifies that a real SDK client can negotiate the modern
`2026-07-28` STDIO path, list the advertised tools, and call the local `health`
tool. It also asserts the complete nine-tool catalog, including `score`. This
keeps the dependency-free runtime honest without adding the SDK to the package's
production dependencies.

## CI

GitHub Actions runs the test and wheel matrix on Python 3.10–3.13 across
Ubuntu, macOS, and Windows, then installs the wheel in a clean Linux virtual
environment and performs an MCP initialization/shutdown smoke test.
It also runs the official SDK interoperability smoke test on Linux.
