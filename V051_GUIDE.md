# Capture the Narrative 2026 — v0.5.1

## What this release can and cannot do

Read-only integration: public Trending/Latest Feed (one page, up to 10 posts) -> ASCII `?` candidate filter -> choose the longest post by `len(text)` -> optionally classify whether it is a real information-seeking question via the organisers' model -> local structured decision, SQLite and JSONL audit.

**No social account login, reply, post, repost, like, follow, or report method is invoked.** The existing v0.3 mock-execution code remains strictly separate from this entry point.

LLM responses never authorize any social action. When the classifier returns `false`, the final proposal is `noop`. If no ASCII `?` candidate exists, no LLM call is made.

## 1. Setup (PowerShell, Windows)

```powershell
cd E:\CTN\v5.1
python -m pip install -r requirements-official.txt
python -m unittest discover -s tests -v
```

## 2. Configure Team Code persistently

Do **not** add the code to any `.py` file, command line argument, README, screenshots, Git history or chat message.

```powershell
Copy-Item .env.example .env
notepad .env
```

Replace **only** `REPLACE_WITH_YOUR_OWN_TEAM_CODE` in `CTN_TEAM_CODE=REPLACE_WITH_YOUR_OWN_TEAM_CODE` with the actual **competition Team Code** (not your Legit Social account username or password). Save the file. No quotation marks are needed for a normal alphanumeric Team Code.

`.env` is ignored by the bundled `.gitignore`; the public template `.env.example` is safe to commit. This is a plaintext file on your own computer: protect your Windows account and do not attach `.env` to issue reports or zipped source packages. If you previously committed a secret, `.gitignore` alone does not undo the leak; rotate that Team Code in the competition community dashboard, then remove it from Git history.

Check without network requests or printing the value:

```powershell
python run_v051.py --check-config
```

Expected after configuration:

```json
{
  "configured": true,
  "source": "local_env_file",
  "network_calls": 0,
  "writes_attempted": 0
}
```

Alternatively, `CTN_TEAM_CODE` can be set in the process environment; it takes priority over `.env`.

## 3. Purely offline smoke tests (zero network calls)

```powershell
python run_v051.py --source mock --model fixture
python run_v051.py --source mock --model fixture --fixture-model-result false
```

The second command tests the negative classification and changes the final proposed action to `noop`.

## 4. Optional official LLM using **only local synthetic** post data

This is useful before relying on a real Feed containing an ASCII `?`. It makes **one call to the organisers' LLM**, but **no social feed request or write**. Requires the explicit flag and a configured Team Code:

```powershell
python run_v051.py --source mock --model official --confirm-official-llm
```

Expected if returned JSON is valid: `llm_calls: 1`, `classification_source: official_llm`, `writes_attempted: 0`.

## 5. End-to-end public Feed + official LLM, read-only

```powershell
python run_v051.py --source official --confirm-public-read --feed trending --limit 10 --model official --confirm-official-llm
```

This will read one public Feed page. If none of the returned posts contains ASCII `?`, then **no LLM request is needed**. Otherwise it calls the organiser-hosted model once for the longest unreviewed eligible post. It never publishes or replies.

By default it **does not mark candidates reviewed**. Add `--record-reviewed` only if you want to mark one candidate as *locally reviewed*, regardless of the boolean classification; it is never a platform-reply record.

Try `--feed latest` on a later occasion if necessary. Avoid repeatedly running live reads: the site documents a team-shared feed quota of 10/minute and an LLM quota of 3/minute, concurrency 1. This repository additionally reserves a 65-second Feed cooldown and 25-second model cooldown in SQLite; those local guards do NOT track other teammates or servers' SDK retries. A new DB path would reset guards; do not use that to bypass limits.

Output fields `llm_calls` indicate high-level SDK attempts, not internal HTTP retries or billing usage. `official_score: null` is correct: this version never fetches scoring feedback.

## 6. Default files and fault isolation

- Program: `run_v051.py` (new recommended entry point; the old `llm_readonly.py` remains for v0.5 compatibility).
- Team Code: `.env` in the project root, local only.
- Config reader: `ctn/local_secrets.py`.
- Feed adapter: `ctn/official_readonly.py`.
- Classification: `ctn/question_classifier.py`.
- Pipeline: `ctn/integrated_readonly.py`.
- SQLite state: `data/v051_review_state.sqlite3`.
- JSONL audit: `logs/v051_readonly.jsonl`.
- Kill switch: a file named `STOP` in the project root.

The program does not log raw posts, prompts, model replies, or Team Code. If `status: read_or_validation_failed`, it returns only an error type, without the raw exception text.

## 7. Limitations

- No official social platform writes are implemented or authorized by this entry point.
- One successful example does not establish model classification accuracy or competition value.
- Model output has a strict JSON boolean schema; incorrect formats fail closed.
- Only one Feed page and at most one LLM classification are attempted per CLI invocation.
- Post length is measured in Python characters, **not** words.
- Teams should verify rules on using any non-public data with AI and coordinate model rate limits. Only public Feed posts are passed to the organisers' supplied model in this prototype.
- Avoid putting Team Code in source code even if official Getting Started uses inline literals for short tutorial examples.

Official public references:
- https://capturethenarrative.com/info/competitions/2026/getting-started/
- https://github.com/capturethenarrative/pypi-capturethenarrative
