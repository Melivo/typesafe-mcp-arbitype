# MCP Registry migration

Arbitype uses the new canonical identity:

```text
io.github.Renwang-Huang/arbitype
```

This migration and its publisher procedure describe upstream
`Renwang-Huang/arbitype` only. The `Melivo/typesafe-mcp-arbitype` fork has no
PyPI or MCP Registry publication; its release path is a GitHub prerelease
([fork release contract](FORK_RELEASES.md)).

The former identity remains a legacy entry:

```text
io.github.Renwang-Huang/typesafe-mcp
```

The release workflow publishes only the new identity. It does not overwrite,
delete, or mutate the legacy entry. This prevents a package rename from
silently breaking existing host configurations.

## Maintainer procedure

Create a GitHub release and tag matching `arbitype._version.__version__` to
start the release workflows. The Registry workflow is gated on successful
completion of the canonical PyPI workflow. The effective publication order is:

1. Publish `arbitype` to PyPI.
2. Verify `arbitype==<version>` on PyPI and confirm that `uvx arbitype` can
   resolve the artifact.
3. Publish `server.json` under the new identity through `Publish to MCP Registry`.
4. Verify the new entry at the official Registry.
5. Do not publish a `typesafe-mcp==<version>` migration wheel. A real upgrade
   test from the public `typesafe-mcp==0.5.2` package showed that pip can
   remove the legacy console-script files while replacing the old distribution.
   Use the safe explicit migration instead:

   ```bash
   python -m pip uninstall typesafe-mcp
   python -m pip install arbitype
   ```

6. If the current `mcp-publisher` release documents a supported deprecation
   operation, manually mark the legacy entry with:

   ```text
   TypeSafe MCP has been renamed to Arbitype. Use
   io.github.Renwang-Huang/arbitype for new installations.
   ```

   Do not run an undocumented delete, overwrite, or identity-mutation command.
   Do this only after both the new PyPI artifact and new Registry identity are
   live. If deprecation is not supported, leave the legacy entry intact and
   keep the migration notice in the README and release notes.
7. Verify Glama after its crawl or submit its supported listing request.

The repository workflows deliberately enforce the safe part of this order:
the canonical PyPI release precedes Registry publication. The old PyPI project
is left untouched; Glama controls its own crawl schedule and is verified
separately.

Do not use `python -m pip install -U typesafe-mcp` as the migration path for
this release. It would select the historical project and does not provide the
canonical `arbitype` distribution.
