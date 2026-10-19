# v0.4.2 — Read-only integration checklist

| Component | Scope | Status |
|---|---|---|
| Official SDK 0.5.0 public trending/latest read | confirmed from docs; user previously demonstrated the 30-post v0.4.1 probe | adapter reused |
| ASCII `?` and longest-body selector | deterministic and tested | integrated |
| SQLite review IDs across restarts | **local reviewed/selected only**, no sent-action state | implemented/tested |
| SQLite conservative minimum read interval | 65 seconds per batch across processes using same DB | implemented/tested |
| Rate-limit header reporting | 2026 SDK `.rate_limit` | implemented/tested with fake SDK |
| 100/300/500 rounds | **offline fixture only** | supported/tested |
| Official LLM | must use authorized team code and rules | not connected |
| Authenticated personal feed, replies, likes, reports | outside this read-only milestone | intentionally unavailable |
| Engagement/Story/Poll score | actual server state | not simulated |

## Minimal recommended command

```powershell
cd E:\CTN\v4.2
python -m pip install -r requirements-official.txt
python -m unittest discover -s tests -v
python integrated_readonly.py --source official --confirm-public-read --feed trending --limit 10
```

The command uses `data/review_state.sqlite3` and `logs/readonly_events.jsonl`. Read-only collection is intended to return selected **post ID and character count only**. No platform write action is possible from this entry point.

To optionally persist local candidate selection, add `--record-reviewed`. On a later process invocation using the same DB, that ID is excluded if it reappears in the retrieved feed. To reset a disposable local experiment, remove its experimental `.sqlite3` database only while the program is closed; never treat that operation as deleting a real platform interaction.

### If the output shows `local_read_cooldown`

Stop issuing new requests and let the local 65-second gap pass. The actual official SDK rate limit may require a longer wait if other teammates consumed capacity. This script does not automatically sleep/retry on a `429`.

### Open risks for the next milestone

1. The scheduler is not production multi-worker capable; centralize the team's requests and use the actual SDK rate-limit headers.
2. Local review exclusion is not a durable record of successful platform posting, and must remain distinct when write APIs are introduced.
3. Treat model output and post content as untrusted; no external LLM connection or prompt strategy is implemented in v0.4.2.
4. Avoid replaying/scoring actual private user content through third-party AI without confirmation of competition sharing permission.
