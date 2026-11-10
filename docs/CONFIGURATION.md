# Configuration reference

Arbitype reads provider and safety settings from the process environment.
The only required setting is the TypeSafe credential:

~~~bash
export TYPESAFE_API_KEY="your-key"
~~~

Do not put this value in MCP tool arguments or commit it to a host
configuration file. Host setup uses environment forwarding or a password
prompt instead.

## Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| TYPESAFE_API_KEY | none | Required TypeSafe bearer credential. |
| TYPESAFE_BASE_URL | <https://api.typesafe.ai> | TypeSafe API base URL. |
| TYPESAFE_MODEL | jev-latest | Model alias; retained as a legacy-compatible setting. |
| TYPESAFE_DEFAULT_MODEL | jev-latest | Default model used by the TypeSafe client. |
| TYPESAFE_TIMEOUT_SECONDS | 10 | Timeout for each HTTP attempt. |
| TYPESAFE_MAX_RETRIES | 2 | Text retries after the initial request; image requests never retry automatically. |
| TYPESAFE_RETRY_BACKOFF_SECONDS | 0.5 | Initial exponential retry backoff. |
| TYPESAFE_MAX_STATE_CHARS | 120000 | Maximum serialized state size. |
| TYPESAFE_MAX_QUESTION_CHARS | 60000 | Maximum serialized question size. |
| TYPESAFE_MAX_REQUEST_BYTES | 512000 | Maximum complete request size. |
| TYPESAFE_MAX_RESPONSE_BYTES | 4194304 | Maximum provider response size. |

Set an explicit model when reproducibility matters:

~~~bash
export TYPESAFE_MODEL="jev-latest"
~~~

The canonical model setting is TYPESAFE_DEFAULT_MODEL. TYPESAFE_MODEL remains
accepted for compatibility with earlier configurations.

## Endpoint and transport rules

The default endpoint is:

~~~text
https://api.typesafe.ai
~~~

Custom endpoints must use HTTPS. Plain HTTP is accepted only for loopback
development endpoints such as localhost, 127.0.0.1, and ::1. Redirects are
not followed for provider requests, so a bearer credential cannot be copied to
a redirect target.

Arbitype validates request limits before sending a paid request and validates
provider responses before returning them to the MCP host.

## Experimental inline images

Only `evaluate` accepts an optional `assets` map with `format: png|jpg|jpeg`
and canonical padded `data_base64` (optional `type: image`). Every image call
requires explicit `allow_images: true`; this local field is not sent to the
provider. The adapter constructs multipart and the experimental image header.
There is no global image toggle and no path, URL or caller-supplied part input.

Only `evaluate` also supports `type: box`: meaningful nonempty instructions,
no `criteria` field (not even null), valid assets and explicit opt-in are
required. Pair it with a separate presence Noul; no automatic gate is added.
Box edges must be finite non-Boolean numbers with
`0 <= left < right <= 1`, `0 <= top < bottom <= 1`; confidence must be a finite
non-Boolean number in `[0,1]`. Invalid responses remain errors, not clamped boxes.
Use an explicit compatible model (the synthetic recipe pins `jev-1.14.0`);
the default model is unchanged and unsupported models remain provider errors.

Fixed local limits: four assets, **256 KiB decoded files together**, sides
**280–1568 px**, references **1024 UTF-8 bytes each / 65536 together**, and
**32 MiB** conservative reference expansion. Existing state/question, serialized
inline JSON, final HTTP body, response and STDIO limits still apply; increasing
an environment limit does not increase the fixed image limits. Image requests
have **zero automatic retries**, including timeouts and retryable HTTP errors.
An error after sending may mean an unknown outcome, not permission to resend.

PNG is limited to noninterlaced 8-bit grayscale/RGB with optional alpha and no
ancillary metadata. JPEG is baseline 8-bit grayscale/three-component, one
complete scan; local marker/scan checks do not decode entropy or pixels.
Unsupported variants fail locally. Full contract, example and evidence limits:
[IMAGE_SUPPORT.md](IMAGE_SUPPORT.md). Without assets the text behavior and
environment defaults are unchanged; omitting assets is the nonactivation path.

## Operational guidance

- Keep credentials in the process environment or a host-managed secret
  prompt.
- Do not include the API key in JSON request bodies, tool arguments, issue
  reports, or benchmark reports.
- Use the default endpoint unless a controlled HTTPS endpoint is required.
- Start with local diagnostics:

  ~~~bash
  arbitype doctor --json
  ~~~

- Make a provider request only when live behavior is intended:

  ~~~bash
  TYPESAFE_API_KEY="your-key" arbitype doctor --live
  ~~~

The limits above protect the local adapter and are not a substitute for
provider-side quotas or authorization controls. For the security model, see
[SECURITY.md](../SECURITY.md).
