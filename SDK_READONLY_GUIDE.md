# CTN 2026 official SDK: read-only first connection

**SDK source:** https://github.com/capturethenarrative/pypi-capturethenarrative

**Official 2026 package documentation:** https://pypi.org/project/capturethenarrative/

**Official Getting Started:** https://capturethenarrative.com/info/competitions/2026/getting-started/

**Safety:** This addition has no `register`, `login`, `reply`, `post`, `like`, `report`, or LLM invocation. `main.py --mode live` is still blocked. Its single explicit `--read-live` option only requests an unauthenticated public trending/latest feed. No sensitive keys needed. The SDK cannot be downloaded in the assistant's isolated execution environment, so SDK installation and network connectivity need a local user run.

## Windows PowerShell: in the extracted project directory

```powershell
python -m unittest discover -s tests -v
python official_sdk_probe.py --offline-demo

# Install ONLY the 2026 release into your own local environment
python -m pip install -r requirements-official.txt
python official_sdk_probe.py --check-install

# When you are ready for ONE authorised, unauthenticated read from Legit Social:
python official_sdk_probe.py --read-live --feed trending --limit 10
```

If preferred, use `--feed latest`. The probe prints counts, best matching post ID and length, and server rate-limit headers, but **not** body text or credentials. A server with no posts can correctly return zero. A 429 means requests were throttled; a connection error means live access was not established; neither means you should bypass protections.

## Documented API field mapping

| Official feed item | Existing v0.3.1 internal field |
| --- | --- |
| `post['post_id']` (numeric or string) | `MockPost.id` (string) |
| `post['author_id']` | `MockPost.author` |
| `post['body']` | `MockPost.text` |

`MockPost` is unfortunately named for live-normalised data, but retaining it avoids altering v0.3.1's tested logic. The selector continues to require the ASCII `?` and chooses the post with the longest body in Python characters. `execute` always throws `OfficialWriteBlocked`.

## NOT YET DONE

- Authenticating an account or checking team registration/permissions
- Official LLM requests and quota checks
- Writing replies, likes, posts, follows or reports
- End-to-end feedback/scoring measurement
- Live-mode policy approval and account/anti-duplication guarantees

Do not assume `main.py --mode live` works. Do not send team codes, account passwords or session tokens in chat.


## v0.4.1: `--limit 30` regression fix

The v0.4 script rejected a total `--limit 30` **before connecting**, because its own
`OfficialReadOnlySocialClient` used a local `[1,20]` guard. That was not an
HTTP or official rate-limit error.

v0.4.1 treats `--limit` as the **total records desired**, max 50 as a local
safety cap (NOT an official per-page maximum). It reads up to 10 per SDK
page, walking `Page.next_cursor` until it has enough posts or runs out of
pages. For 30 posts this can require 3 feed API calls, which count against
the competition's shared feed request budget. No login or write operations.

```powershell
python -m unittest discover -s tests -v
python official_sdk_probe.py --offline-demo
python official_sdk_probe.py --read-live --feed trending --limit 10
# Optional after a successful 10-post smoke test:
python official_sdk_probe.py --read-live --feed trending --limit 30
```

The SDK's `rate_limit` (and `429`) remain authoritative. If the feed ends
early, `received_posts` can be lower than the requested total. Never share
credentials or sensitive platform responses.
