#!/usr/bin/env python3
"""Run the personal KB on localhost with Ollama and isolated local data.

Start the project Postgres service first. Set CAIRN_LOCAL_DATABASE_URL to use a
different database. Provider keys in .env are not used by this profile. Google
client settings are still read from .env.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'var' / 'local'


def environment() -> dict[str, str]:
    return {
        'DATABASE_URL': os.environ.get('CAIRN_LOCAL_DATABASE_URL',
            'postgresql://postgres:postgres@127.0.0.1:5434/ai_brain'),
        'AUTH_MODE': 'dev',
        'LLM_PROVIDER': 'ollama',
        'LLM_MODEL': os.environ.get('CAIRN_LOCAL_MODEL', 'llama3.1:8b'),
        'OLLAMA_BASE': os.environ.get('CAIRN_OLLAMA_BASE', 'http://127.0.0.1:11434'),
        'ABSORB_BASE': os.environ.get('CAIRN_OLLAMA_BASE', 'http://127.0.0.1:11434'),
        'ABSORB_PROTOCOL': 'ollama',
        'ABSORB_MODEL': os.environ.get('CAIRN_LOCAL_MODEL', 'llama3.1:8b'),
        'ABSORB_API_KEY': 'ollama-local',
        'S3_BUCKET': '',
        'WIKI_ROOTS': str(DATA / 'wiki'),
        'REPO_WIKI_DIR': str(DATA / 'repo-wikis'),
        'REPO_CLONE_DIR': str(DATA / 'clones'),
        'SOURCES_DIR': str(DATA / 'sources'),
        'GDRIVE_TARGET_REPO': str(ROOT),
        'GOOGLE_TOKEN_FILE': str(ROOT / 'secrets' / 'google-local.json'),
        'GOOGLE_ACCOUNT_EMAIL': os.environ.get(
            'CAIRN_GOOGLE_ACCOUNT', os.environ.get('GOOGLE_ACCOUNT_EMAIL', '')),
        'STEEL_BASE_URL': os.environ.get('CAIRN_STEEL_BASE', 'http://127.0.0.1:3003'),
        'STEEL_WS_URL': os.environ.get('CAIRN_STEEL_WS', 'ws://127.0.0.1:3003'),
        'STEEL_VIEWER_BASE_URL': os.environ.get('CAIRN_STEEL_VIEWER', 'http://127.0.0.1:3003'),
        'CONNECTOR_SYNC_INTERVAL_MIN': '0',
        'DEV_UI': '1',
    }


if __name__ == '__main__':
    for directory in ('wiki', 'repo-wikis', 'clones', 'sources'):
        (DATA / directory).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **environment()}
    port = os.environ.get('CAIRN_LOCAL_PORT', '8300')
    os.chdir(ROOT)
    os.execve(str(ROOT / '.venv/bin/uvicorn'),
              ['uvicorn', 'server.app:app', '--host', '127.0.0.1', '--port', port], env)
