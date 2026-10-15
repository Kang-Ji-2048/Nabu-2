"""Claude calls for the agents, with retries and an offline mode.

Every agent asks for JSON against a schema. If the reply is malformed or the
call fails twice, the agent falls back to its deterministic result and the
recovery is logged, so a long replay never dies on one bad response.
Offline mode (no API key) uses the deterministic result directly.
"""

import json
import os

from .validation import validate

MODEL = "claude-opus-5-5"
EFFORT = "low"


class LLM:
    def __init__(self, log, offline=None, client=None, max_calls=1000, calls=0):
        self.log = log
        # The CLI loads .env after importing this module.
        self.model = os.environ.get("NABU_MODEL", MODEL)
        self.effort = os.environ.get("NABU_EFFORT", EFFORT)
        self.offline = offline if offline is not None else client is None and not (
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        self.client = client
        if max_calls < 0 or calls < 0:
            raise ValueError("Call limits must be non-negative")
        self.calls, self.max_calls = calls, max_calls
        if not self.offline and self.client is None:
            import anthropic
            self.client = anthropic.Anthropic(timeout=60.0, max_retries=0)

    def json(self, agent: str, system: str, payload: dict, schema: dict, fallback):
        """Return a dict matching `schema`, or `fallback()` if Claude can't provide one."""
        if self.offline:
            return fallback()
        content = json.dumps(payload, default=str)
        for attempt in (1, 2):
            if self.calls >= self.max_calls:
                self.log.emit(agent, "recovered", "model call budget exhausted; using deterministic result")
                return fallback()
            try:
                self.calls += 1
                response = self.client.beta.messages.create(
                    model=self.model, max_tokens=16000, system=system,
                    messages=[{"role": "user", "content": content}],
                    output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                )
                if response.stop_reason != "end_turn":
                    raise ValueError(f"incomplete response: {response.stop_reason}")
                text = next(block.text for block in response.content if block.type == "text")
                result = json.loads(text)
                validate(result, schema)
                return result
            except Exception as exc:  # malformed output or API failure: retry once, then fall back
                self.log.emit(agent, "retry" if attempt == 1 else "recovered",
                              f"attempt {attempt} failed ({type(exc).__name__})"
                              + ("; retrying" if attempt == 1 else "; using deterministic result"))
        return fallback()


class Log:
    """Agent log: one row per step in agent_log (for the live UI) and a line on stdout."""

    def __init__(self, conn, run_id, quiet=False):
        self.conn, self.run_id, self.quiet = conn, run_id, quiet
        self.sim_at = None
        row = conn.execute("SELECT MAX(seq) FROM agent_log WHERE run_id=?", (run_id,)).fetchone()
        self.seq = row[0] or 0

    def emit(self, agent, kind, message, **data):
        self.seq += 1
        self.conn.execute("INSERT INTO agent_log VALUES (?,?,?,?,?,?,?)",
                          (self.run_id, self.seq, self.sim_at, agent, kind, message, json.dumps(data, default=str)))
        if not self.quiet:
            print(f"[{(self.sim_at or '')[:10]}] {agent:<12} {kind:<10} {message}", flush=True)
