# CTN 2026 v0.5 — read-only official model classification

## Scope and safety

This version **does not** reply/post/like/repost/follow/report or log into Legit Social.
Its only optional external calls are **public feed reads** and (after explicit opt-in) **one synchronous call** to the competition-provided LLM to classify the selected post. No Team Code, password, prompt, full post text or raw model output is stored in SQLite or JSONL.

- Deterministic candidate selection is unchanged: ASCII `?` only; longest character count wins; ties preserve feed order.
- The model does *not* choose which post to interact with, and cannot publish anything. It only returns one boolean classification.
- Local classification fixture: `{"is_question": true}` (hardcoded; not a genuine model judgement).
- Official LLM: `ctn.CompetitorLLM(team_code=...)` with `.ask(prompt, max_tokens=60)`. Model contract derived from the publicly published CTN Getting Started guide. *Live calls have not been tested by the assistant.*
- Invalid model JSON is rejected and a local review marker is not saved on that attempt.
- One official LLM call is permitted per invocation; a 25-second local cooldown is stored in SQLite. The official team's shared allowance is 3 calls/minute and concurrency 1. The local guard **cannot observe teammate requests or SDK retries**, so the server response and team coordination remain authoritative.
- The public Feed read has its own local 65-second cooldown. The live CLI reads at most 10 posts in one page.

## Installation (PowerShell)

```powershell
cd E:\CTN\v5
python -m pip install -r requirements-official.txt
python -m unittest discover -s tests -v
```

## Offline trial (no network, no credentials)

```powershell
python llm_readonly.py --source mock --model fixture
python llm_readonly.py --source mock --model fixture --record-reviewed
```

By default `--record-reviewed` is off, so repeated previews don't consume local candidates. For repeated experiments use a separate database path (`--db data/another-test.sqlite3`).

## Official public feed only (1 read, 0 model calls, 0 writes)

```powershell
python llm_readonly.py --source official --confirm-public-read --feed trending --limit 10
```

## Optional: one model classification using the official model (0 social writes)

First ensure the team is authorized and that reading a **public** social-media post through the organisers' **own** LLM fits your team's research and rules. Use a secure environment variable, **not a command-line argument**, for the Team Code:

```powershell
$secret = Read-Host "Team Code" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $env:CTN_TEAM_CODE = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
python llm_readonly.py --source official --confirm-public-read --model official --confirm-official-llm --limit 10
Remove-Item Env:\CTN_TEAM_CODE
```

Only the selected post is sent to the organiser-hosted LLM. If a page contains no eligible `?` post, no model call is made. The official model can produce invalid JSON; the tool then reports `read_or_validation_failed` and records `ClassificationError` without printing raw replies.

**Never provide Team Code/passwords to a chat or include them in a Git commit.** The already disclosed account password should be treated as exposed if it is still valid.

## Recent fixes

1. The reason `all_ascii_candidates_reviewed` now distinguishes reviewed questions from a feed with no ASCII `?` (`no_ascii_question`).
2. Review IDs are namespaced (`fixture:<post_id>` or `official:<post_id>`). `trending` and `latest` share the same official namespace, because they list the same underlying platform post IDs. The default DB is new (`data/v05_review_state.sqlite3`) and older v0.4.2 review markers are intentionally not imported.

## Verification limits

- Local unit tests and mock smoke checks validate code structure, not actual official-model credentials or response quality.
- No social network write calls are implemented here.
- Local review state is **not** proof of reply, publication or official score.
- There is no long-running official LLM loop in this release.
