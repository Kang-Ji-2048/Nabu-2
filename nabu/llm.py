"""Claude calls for the agents, with retries and an offline mode.

Every agent asks for JSON against a schema. If the reply is malformed or the
call fails twice, the agent falls back to its deterministic result and the
recovery is logged, so a long replay never dies on one bad response.
Offline mode (no API key) uses the deterministic result directly.
"""

import json
import os

MODEL = os.environ.get("NABU_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("NABU_EFFORT", "low")


class LLM:
    def __init__(self, log, offline=None, client=None):
        self.log = log
        self.offline = offline if offline is not None else client is None and not (
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        self.client = client
        self.calls = 0
        if not self.offline and self.client is None:
            import anthropic
            self.client = anthropic.Anthropic()

    def json(self, agent: str, system: str, payload: dict, schema: dict, fallback):
        """Return a dict matching `schema`, or `fallback()` if Claude can't provide one."""
        if self.offline:
            return fallback()
        content = json.dumps(payload, default=str)
        for attempt in (1, 2):
            try:
                self.calls += 1
                response = self.client.beta.messages.create(
                    model=MODEL, max_tokens=16000, system=system,
                    messages=[{"role": "user", "content": content}],
                    output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                )
                if response.stop_reason == "refusal":
                    raise ValueError("model refused")
                text = next(block.text for block in response.content if block.type == "text")
                result = json.loads(text)
                missing = [key for key in schema.get("required", []) if key not in result]
                if missing:
                    raise ValueError(f"missing keys {missing}")
                return result
            except Exception as exc:  # malformed output or API failure: retry once, then fall back
                self.log.emit(agent, "retry" if attempt == 1 else "recovered",
                              f"attempt {attempt} failed ({type(exc).__name__}: {exc})"
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
