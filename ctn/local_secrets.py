"""Local Team Code configuration. Do not log secrets or commit .env files.

Priority: CTN_TEAM_CODE from this process environment > project .env file.
Only the exact CTN_TEAM_CODE entry is parsed from the file; no executable syntax.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import os


class MissingTeamCode(ValueError):
    pass


class LocalSecretsError(ValueError):
    pass


def load_team_code(dotenv_path: Path, *, environ: Mapping[str, str] | None = None) -> tuple[str, str]:
    env = os.environ if environ is None else environ
    direct = env.get('CTN_TEAM_CODE', '').strip()
    if direct:
        return direct, 'process_environment'

    path = Path(dotenv_path)
    if not path.is_file():
        raise MissingTeamCode('Team Code not configured; create local .env or set CTN_TEAM_CODE')
    if path.stat().st_size > 8192:
        raise LocalSecretsError('Local .env file is larger than the accepted safety limit')

    found: str | None = None
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except (OSError, UnicodeError) as exc:
        raise LocalSecretsError('Cannot read local .env file') from exc
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        key, sep, value = line.partition('=')
        if not sep or key.strip() != 'CTN_TEAM_CODE':
            continue
        if found is not None:
            raise LocalSecretsError('Duplicate CTN_TEAM_CODE entry in local .env')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        found = value.strip()
    if not found or found == 'REPLACE_WITH_YOUR_OWN_TEAM_CODE':
        raise MissingTeamCode('Team Code not configured; fill in CTN_TEAM_CODE in local .env')
    return found, 'local_env_file'
