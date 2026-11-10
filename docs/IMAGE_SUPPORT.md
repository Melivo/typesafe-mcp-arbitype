# Experimental image support and feasibility

## Scope and observed result

As of **2026-11-10**, local `evaluate` supports inline PNG/JPEG assets for
Noul, Choice, Score and Box. This is experimental transport support, not evidence
of model quality or production readiness. Text calls remain unchanged. Box is
evaluate-only, requires assets and explicit opt-in, and adds no automatic gate.

The historical G01/M01 feasibility record below is separate from the local
M02/M03 implementation and its provider-free synthetic tests.

### M03 product-path check

`tests/box_live_probe.py` is a small, offline-by-default recipe using the
actual `TypeSafeClient.evaluate` validation/multipart/response path. It reuses
the historical recipe's PNG generator, independent pixel checker and safe
output reservation/report writer without changing that recipe. Its fixed
HTTPS target, pinned `jev-1.14.0`, disabled proxies/redirects, zero retries,
60-second timeout and 1-MiB response cap are independent of environment overrides.
The report whitelists typed numeric answers, usage, HTTP status, fixture/source/
product hashes and status. It reports Noul raw with **no presence threshold**;
localization alone uses the predeclared **0.04** maximum error per edge. This
rule is fixture-specific, not calibration or permission to use a box for actions.

On **2026-11-10**, the initial M03 command stopped locally under plan p01:

```bash
direnv exec /path/to/approved-environment \
  python3 -B tests/box_live_probe.py --live \
  --output .agentic-workflow/runs/run-G01-M03-v1-p01-r01/checks/box-api.json
```

It exited **2** before reservation or transport: the required output parent
`checks/` did not exist in the created run. No report or marker was created
and no HTTP result was obtained. The missing-parent guard is covered offline
and calls no transport; reservation precedes opener construction and product
evaluation. That original failed attempt remains unchanged and is not evidence
of provider failure or successful live localization.

The independently reviewed technical replacement plan p02 allowed exactly one
restart after preparing the missing parent, retaining the **same output path**.
The parent was checked as a real directory with mode **0700**, all absolute
path components were checked for symlinks, and the marker was absent. Product,
probe and fixture-source hashes matched the original attempt. After fresh
credential-free, loopback-only CK01–CK03 passed, the command ran once under p02
and exited **0**. No product code was changed for this preparation repair.

The retained report at the path above records UTC
`2026-11-10T06:16:40.813740+00:00`, HTTP **200**, model **jev-1.14.0**,
usage **1051 input / 36 output tokens**, and:

- Presence Noul: **0.87**, reported raw without a pass/fail threshold.
- Box: left/top **0.25**, right/bottom **0.75**; confidence **0.92**.
- All four edges match the expected box; maximum edge error **0.00**, within
  the predeclared **0.04** tolerance. The report records `geometry_pass: true`
  and `status: pass` for this fixture-specific localization check only.
- Pixel-verified 512×512 PNG SHA-256:
  `51f5a2cd8c5c531fa5d6b5b864c9e125820acf8f1f85d16c9bbdda4fb590a85a`.
  The report also binds the probe, fixture source and three product files by hash.

This is a real product-API localization example, not MCP-image-live-E2E,
presence-quality calibration, production readiness or action authorization.
Independent implementation review and milestone completion remain separate
coordinator verifications. The historical M01 fail below remains unchanged.

In the command above, `/path/to/approved-environment` is an illustrative
placeholder for an environment directory already approved and configured by
the operator; replace it with that local path. This guide does not require
`direnv` or prescribe a machine-specific environment location.

The output parent must already exist; it is not created by the recipe. Before
any send an exclusive no-follow marker is written and fsynced, including the
parent directory. Existing, interrupted, empty or partial output prevents reuse;
post-send persistence errors mean unknown outcome, not permission to resend.
No failure authorizes a new filename or retry. The p02 exception was limited
to the proven pre-reservation preparation failure and is now consumed; do not
repeat this live command. Offline invocation:

```bash
python3 -B tests/box_live_probe.py
```

### Historical M01 API result (unchanged)

Exactly one synthetic PNG request reached the fixed HTTPS System One endpoint
with `jev-1.14.0`, multipart upload and the image opt-in header. It returned
HTTP **200**, model **jev-1.14.0**, usage **1051 input / 36 output tokens**,
and both requested answer types:

- Presence Noul: **0.86**.
- Box: left **0.25**, top **0.26**, right **0.75**, bottom **0.75**;
  confidence **0.9**.
- Expected edges: left/top **0.25**, right/bottom **0.75**.
  Every edge is within the predeclared **0.04** tolerance.
- The predeclared presence minimum was **0.90**: presence failed, geometry
  passed. **CK02 failed (exit 1)**. Neither the threshold nor the report was
  changed afterward. No retry was made.

The original CK02 remains **fail**. The replacement plan's separate offline
CK02 binds the archived source, PNG pixels, multipart request and report by
hash, validates the Noul/Box/usage types and reproduces the matching geometry.
This supports only MAC02's original PNG transfer/response contract/geometry
claim from that completed API action. It neither reruns nor reclassifies the
original experiment, changes its 0.90 rule, nor establishes presence quality.
The cause of 0.86 is unknown. This one sample is not accuracy calibration or
production quality; Box confidence is not an independent presence guarantee.
At the time of this historical M01 result, JPEG, image Choice/Score, and
end-to-end MCP image calls had not been live tested. The later, separate M04
synthetic MCP result is recorded below; it does not change this M01 outcome.
Milestone completion remains a separate coordinator verification after review.

Original local evidence is
`.agentic-workflow/runs/run-G01-M01-v1-p01-r01/checks/image-api.json`:
UTC `2026-11-10T03:58:54.380349+00:00`, fixture SHA-256
`51f5a2cd8c5c531fa5d6b5b864c9e125820acf8f1f85d16c9bbdda4fb590a85a`.
The report also binds the request bytes and exact probe source by hash.
The unchanged source is archived beside it as
`image_feasibility_probe.py.original`; the repaired working recipe is not
represented as the source used for that historical request. New offline logs
are under `.agentic-workflow/runs/run-G01-M01-v1-p02-r01/checks/`.

## Sources and versions

Retrieved on **2026-11-10**:

- [TypeSafe Images](https://docs.typesafe.ai/images): experimental assets,
  upload rules, preprocessing, limits and image errors.
- [TypeSafe Box](https://docs.typesafe.ai/primitives/box.md): Jev 1.14,
  request/response fields, normalized edges, separate presence requirement.
- [MCP Tools 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools):
  tool input/output JSON Schema, structured results and error handling.
  Its image content example describes **tool result content**, not automatic
  support for arbitrary image arguments to this repository's tools.

Context7 `/typesafe-docs/typesafeai` freshly returned the older GitHub
`docs/03-state.md` and `docs/12-models.md`: text-only and current Jev 1.13.
That snapshot conflicts with the current official image pages and the actual
HTTP response. Do not use its text-only assertion as the current image contract.
The plan's earlier Context7 MCP `2026-07-28` lookup yielded only a SEP proposal;
it is not used as normative evidence. This task does not migrate MCP versions.

## Current provider contract

POST `https://api.typesafe.ai/v1/systemone`, bearer authentication in memory.
Every image or Box request needs `TypeSafe-Danger-Zone-Images: true`.
Images work with Jev 1.13/1.14; Box requires 1.14. This experiment pins
`jev-1.14.0`, not a mutable latest alias.

Top-level `assets` maps reference strings to images. References occur as
whole values or substrings in state/instructions/criteria, never object keys.
All assets must be referenced. Prefer collision-resistant UUIDs: short strings
can accidentally match other text. Each reference use expands the image again;
put a shared image once in state, then refer to its field from questions.

Multipart is recommended over Base64's roughly one-third size overhead:

- First part `request`, content type `application/json`, contains the JSON.
- Each image has a unique nonempty part name, not the reserved `request`.
  The asset uses `{"type":"image","part":"fixture"}`.
- Each file part belongs to exactly one asset. Missing, unclaimed or multiply
  claimed file parts fail. Image MIME types: `image/png`, `image/jpeg`,
  `image/jpg`, `application/octet-stream`, or absent.
- JSON fallback uses `data_base64` and lowercase `format` (`png`, `jpg`,
  `jpeg`), standard padded Base64 without whitespace or a `data:` prefix.
   `type` is optional but, if present, must be `image`. If both upload methods
   are present the provider uses `part`. The implemented adapter rejects caller
   parts rather than silently choosing an upload method. It accepts
   only inline Base64 input, then creates its own multipart parts.

Images may appear in nested state, instructions and criteria for Noul,
Choice or Score. Box has only `type: box` and meaningful `instructions`,
**no criteria**. It returns one box even if the target is absent. Ask a separate
Noul in the same request and do not infer presence from Box confidence.
Validate four finite non-Boolean numbers with
`0 <= left < right <= 1` and `0 <= top < bottom <= 1`, plus confidence in
`[0,1]`. Coordinates start at top-left and refer to the sent image; conversion
to original pixels must account for resize/crop/padding. No universal confidence
threshold is recommended. Coordinates do not authorize clicks or other actions.

### Limits and errors

- Still PNG/JPEG only; animated PNG, corrupt/truncated and other formats fail.
- File: at most **16 MiB**, decoded size for Base64.
- Each side: **280–1568 px**, inclusive. Total pixels:
  **78,400–2,458,624**, inclusive. Rejected images are not resized by the API.
- Request body: **32 MiB**, including JSON, Base64 and multipart overhead,
  excluding HTTP headers. Compression does not increase the allowance.
- Distinct images after provider processing together: **32 MiB**; re-encoding
  can enlarge small uploads. Expanded text/questions/structure/images:
  **32 MiB**; repeated references count repeatedly.
- Each reference: **1024 UTF-8 bytes**; all references: **65,536 bytes**.
  Empty/whitespace-only references fail. Context-token limits also apply;
  image tokens appear in `usage.input_tokens`, with no separate image rate limit.
- Provider strips metadata and does not apply EXIF orientation. Rotate pixels
  upright before sending; baseline 8-bit JPEG is recommended over progressive.
  These JPEG details are documented, not verified by this PNG experiment.
- HTTP **400**: missing opt-in, invalid image/assets/Base64/multipart, undersized
  image, or `max_tokens_exceeded`. **413**: file/dimension/pixel/body/processed/
  expanded/reference limits exceeded. Any rejected image fails the whole request;
  no partial answers. Only one problem may be reported at a time.
- Authentication failures, rate limits, server errors, redirects, timeout,
  invalid JSON, oversized response, mismatched model/answer types, invalid
  edges or usage remain errors, not model answers. Do not retry this experiment.

## Implemented local evaluate contract (M02/M03)

`arbitype/images.py` owns inline decoding, bounded structure validation,
references and internal multipart names. `core.py` retains request/response
validation and HTTP; `mcp.py` advertises optional image fields only on evaluate.
No new dependency, tool, setting or protocol version is introduced.

Generate `data_base64` programmatically from an authorized synthetic image;
do not send the placeholder literally:

```json
{
  "state": {"photo": "unique-image-reference"},
  "allow_images": true,
  "assets": {
    "unique-image-reference": {
      "type": "image",
      "format": "png",
      "data_base64": "<canonical padded Base64>"
    }
  },
  "questions": {
    "visible": {"type": "noul", "instructions": "Is the target visible in photo?"},
    "target": {"type": "box", "instructions": "The target in photo"},
    "color": {
      "type": "choice", "instructions": "Which color is the target?",
      "criteria": {"red": null, "blue": null}
    },
    "clarity": {
      "type": "score", "instructions": "How clear is the target?",
      "criteria": ["unclear", "clear"]
    }
  }
}
```

- `allow_images: true` is required on every asset request, stripped before
  provider transmission. Only such requests get the danger-zone header.
  With no assets, the ordinary JSON transport, text retries and defaults remain.
- Box requires valid assets, meaningful nonempty structured instructions and
  no criteria field, including null. Answers require exactly four finite
  non-Boolean box edges ordered within `[0,1]` and finite non-Boolean confidence
  within `[0,1]`. Missing, extra, nonfinite, reversed or degenerate edges fail
  closed, never clamp. Presence Noul remains independent; there is no gate,
  universal confidence threshold, new tool or automatic pixel/click conversion.
  The global model default stays unchanged; use a compatible explicit model.
- Assets must be a nonempty map of at most **four** inline images, with only
  optional `type: image`, required `format: png|jpg|jpeg` and `data_base64`.
  Base64 is canonical, padded, without whitespace or a data-URL prefix.
  No file paths, URLs, arbitrary multipart parts or network fetching are accepted.
- Decoded image files together are limited to **256 KiB**; both sides must be
  **280–1568 pixels**. Provider maxima above are not local promises.
- Each reference must contain non-whitespace text, at most **1024 UTF-8 bytes**;
  references together are capped at **65536 bytes** (the four-asset cap is tighter).
  Every reference must occur in a nested string value of state, instructions or
  criteria. Object keys, question ids and model do not count. Substrings and
  repeated uses count; overlapping matches count conservatively too.
- Local expansion adds at least RGBA-size bytes plus scanline overhead per
  occurrence (or upload bytes if larger) to serialized text/structure and caps
  the sum at **32 MiB**. This is a conservative guard, not a prediction of
  provider re-encoding; provider processed-size/expanded-size 400/413 errors remain.
- Existing **120000/60000-byte** state/question defaults, **512000-byte**
  request default and **16-MiB** STDIO frame cap remain. Both inline JSON and
  final multipart body must fit the configured request limit. A small custom
  request limit may reject even an otherwise valid image.
- PNG validation checks the signature, IHDR-first/unique order, CRC of every
  chunk, consecutive IDAT, IEND/exact EOF, bounded zlib output, exact scanline
  length and legal filter bytes. Supported: noninterlaced **8-bit grayscale,
  RGB, grayscale-alpha or RGBA**. Optional PLTE is allowed only for RGB/RGBA.
  Indexed/sub-8/16-bit, interlaced, animated and ancillary/metadata chunks are
  deliberately unsupported and fail locally. No metadata decompression occurs.
- JPEG validation traverses the entire marker/segment/scan structure, checking
  lengths, dimensions, quantization and Huffman tables, component/table selectors,
  scan parameters, stuffing, restart-marker sequence and exact EOI. Supported:
  baseline sequential **8-bit grayscale or three-component, one complete scan**;
  progressive, arithmetic, lossless, multiscan and other markers are rejected.
  APP/COM payloads remain opaque. This is **not entropy or pixel decoding**:
  coefficient validity, restart spacing in MCUs and successful full JPEG decode
  are left to the provider. A structurally valid but undecodable image can still
  produce a provider error, never an invented model answer.
- Validated images become internally named file parts; JSON is the first
  `request` part. No caller text enters multipart headers. Redirect prohibition,
  credential redaction, response bounds and typed answer/usage validation remain.
  **No image request retries automatically**, even on 429/5xx or transport loss.
  A failed request may have reached the provider; do not infer non-send.

`tests/test_images.py` uses deterministic stdlib-generated PNG and constant-gray
baseline JPEG fixtures plus fake transport, including mixed Noul/Choice/Score,
malformed structures, expansion, opt-in, limits and failure/no-retry checks.
These are offline contract checks, not by themselves live model evidence. The
separate M04 MCP result below covers only its two synthetic fixtures and named
questions; it does not generalize to other images or prove quality. Box coverage includes combined Noul, schema/runtime rejection,
nonfinite/Boolean/degenerate coordinates, bounded transport and STDIO success/
error handling with a local fake provider. Actual Windows/macOS execution remains unproved. The sole
historical PNG result above remains unchanged; the cause of 0.86 is unknown.
Omit assets to leave images inactive. The global MCP registration is untouched.

## Synthetic STDIO-MCP recipe and result (M04)

`tests/image_mcp_probe.py` is a small standard-library client for the actual
local MCP process. From a checkout, no package installation is needed:

```bash
python3 -B tests/image_mcp_probe.py
```

This default is offline: it returns without starting a child, generating
fixtures, reserving an output path, or contacting TypeSafe. To make an
authorized live run, provide the already-approved credential in the inherited
process environment and a new, previously unused report path:

```bash
REPORT_PATH="path/in/an/existing-directory/new-report.json"
python3 -B tests/image_mcp_probe.py --live --output "$REPORT_PATH"
```

Replace the example value with the approved path before running it; do not
paste the placeholder unchanged.

The report's parent directory must already exist; the recipe does not create
it. For other local `evaluate` image calls, `allow_images: true` is required in
each request. This fixed recipe sets that opt-in itself and pins
`jev-1.14.0`; the global default model is not changed.

The script requires `TYPESAFE_API_KEY` in its environment; it does not accept
the key as an argument. Follow the existing approved credential setup, such as
the host-managed environment or an explicitly approved `direnv exec` wrapper;
never inspect or copy secret-file contents. A live run sends the recipe's fixed
synthetic PNG and JPEG cases serially over STDIO to the unchanged `server.py`,
after MCP initialize/initialized and tools/list. It calls `evaluate` with the
predefined Noul/Choice/Score questions (and Box for PNG); this is not a shell
CLI for submitting arbitrary questions. The child pins `jev-1.14.0`, a fixed
HTTPS endpoint, zero retries and bounded I/O. Normal EOF is the shutdown path;
on timeout the recipe terminates and, if needed, kills only its own child.

Before any possible send, the exclusive output marker is created and fsynced.
An existing, empty, interrupted or partial path blocks reuse; an uncertain
post-send result is unknown, not permission to retry at another path. The M04
live marker and attempt have already been consumed. **Do not rerun the recorded
command or reuse its output path.** Reproduction instructions are not
authorization: any future paid image run needs new, task-specific approval and
its own fresh output path. The probe never edits global MCP configuration. When
its local child exits, the prior global MCP registration/process remains as it
was; do not change global configuration to return to it.

The retained M04 report
(`.agentic-workflow/runs/run-G01-M04-v1-p01-r01/checks/image-mcp-api.json`,
SHA-256 `98808b00415c75f859a8d736190c6217f6756633784b38915f96aeff3cab3570`)
records exactly two synthetic requests and MCP protocol `2025-06-18`:

- PNG: Noul **0.86** raw; Choice **red**; Score **1.18** raw; Box
  left/top/right/bottom **0.25/0.27/0.76/0.75**, confidence **0.88**.
  Maximum edge error is **0.02**, within this fixture's **0.04** tolerance.
  Usage: **1136 input / 84 output tokens**.
- JPEG: Noul **0.98** raw; Choice **gray**; Score **1.0** raw. Usage:
  **599 input / 68 output tokens**. The gray expectation follows the fixed
  fixture's construction; the report does not claim pixel verification.

The report's eight source hashes bind the historical published source at
commit `b5cb07b01dcb0aa962bf234b28ed7de2d1bc0074`, not every later checkout.
The subsequent cross-platform repair canonicalizes only test-owned temporary
directories and forwards only an existing Windows `SystemRoot` to the isolated
probe child. The probe and its test source have therefore changed; their old
report hashes are not current-source matches. The product `core.py`, `mcp.py`
and `images.py` remain at the report's source hashes. The report and its
historical results remain unchanged, with no new live attempt. Linux offline
regressions do not establish Windows/macOS CI success.

Both calls returned validated answers from `jev-1.14.0`; the fixture-specific
Choice expectations matched. Noul and Score remain raw signals without a new
threshold. The result demonstrates only these synthetic MCP cases and the
questions actually sent. It is not a general JPEG decoder or image-quality
calibration, production-readiness evidence, Windows/macOS operational proof, or
authorization for actions. Historical M01's **0.86 vs 0.90 fail** and M03's
separate raw **0.87** remain unchanged; neither is reclassified by M04.

## Reproduction and safety

`tests/image_feasibility_probe.py` uses only stdlib and generates a 512×512
white RGB PNG with red pixels in `[128,384)` on both axes. An independent
decoder verifies CRCs and every pixel **before** transport. No file/URL input,
private image, package installation or global host change is involved.

Default invocation is offline and sends nothing:

```bash
python3 -B tests/image_feasibility_probe.py
python3 -B -m unittest discover -s tests -v
```

The authorized one-shot command was executed from repository root:

```bash
direnv exec /path/to/approved-environment \
  python3 -B tests/image_feasibility_probe.py --live \
  --output .agentic-workflow/runs/run-G01-M01-v1-p01-r01/checks/image-api.json
```

**Do not rerun the live check for new evidence.** The existing output is its
exclusive attempt marker, even if interrupted or invalid. Inspect its status
instead. A new output name is not authorization for another paid attempt.
The output parent must already exist. Symlink components and existing files
are refused; descriptor-relative creation uses exclusive/no-follow flags,
mode 0600, and file/directory fsync before networking. The marker is retained
if a process dies; an empty or partial marker means unknown outcome, not no send.
This recipe assumes a trusted local directory, not a hostile same-user actor.
It requires POSIX `O_DIRECTORY`, `O_NOFOLLOW` and `os.open` with `dir_fd`.
Platforms without these capabilities reject locally before transport; there is
no weaker fallback. POSIX marker/symlink/interruption/persistence tests run only
where these capabilities exist; portable fixture, validation, transport-mock,
opt-in and missing-capability tests remain active everywhere. An OS/filesystem
error can still abort before sending. After possible sending, a write/fsync
failure leaves the marker and the CLI reports potentially unknown outcome,
never proof of non-send. Do not retry, even with another output path.

Only the inherited `TYPESAFE_API_KEY` is consumed in process memory through
the already approved direnv environment. Never supply it as an argument or
inspect a key store. Missing auth stops locally. The request has a fixed target,
no redirects or proxies, a 60-second timeout, no retry and a 1-MiB response cap.
Reports contain whitelisted numeric synthetic answers, fixed model, usage,
UTC/HTTP status and hashes; no request bodies, headers or raw exception/error
strings. A transport failure may have reached the provider: marker inspection
cannot prove the absence of billing and does not justify another send.

The replacement's ordinary suite covers opt-in, fixture pixels, multipart,
response rejection, false geometry, absence, marker-before-send,
duplicate/symlink prevention, interruption, redaction, error/oversize handling
and fixed transport constraints. It adds missing-capability rejection and a
post-send fsync regression with one fake send, retained marker and no retry.
CK01 runs with an isolated network namespace and only loopback enabled, with
an empty inherited environment, so external provider access is unavailable.
Offline fakes do not establish future model behavior or production readiness.
