"""One epic review: planner -> specialists -> data quality -> critic (one send-back) -> writer."""

import json

from .agents.critic import Critic, DataQuality
from .agents.specialists import DependencyHunter, Estimator, GapFinder, Planner, load_patterns
from .agents.writer import Writer


class ReviewTeam:
    def __init__(self, llm, log):
        self.log = log
        self.planner = Planner(llm, log)
        self.specialists = {s.name: s for s in (Estimator(llm, log), DependencyHunter(llm, log), GapFinder(llm, log))}
        self.quality = DataQuality(log)
        self.critic = Critic(llm, log)
        self.writer = Writer(llm, log)

    def review(self, conn, run_id, epic, store, viewer) -> dict:
        """`store` is what the agents may read; `viewer` is what the reader of the review may see."""
        plan = [i for i in store.children(epic["id"])]
        ctx = {"epic": epic, "plan": plan, "store": store, "patterns": load_patterns(conn, run_id, [epic["scope"]])}
        routes = self.planner.run(epic, plan)
        profile = self.quality.assess(store)

        def gather(name, feedback=None):
            found = self.specialists[name].run(ctx, routes[name], feedback)
            for w in found:
                w["confidence"] = self.quality.confidence(w, profile, ctx["patterns"])
            return found

        warnings = [w for name in self.specialists for w in gather(name)]
        decisions = self.critic.judge(warnings, store, viewer, final_round=False)
        sent_back = {w["id"]: decisions[w["id"]][1] for w in warnings if decisions[w["id"]][0] == "revise"}
        if sent_back:  # weak warnings go back to their specialist once
            revised = [w for name in {w["agent"] for w in warnings if w["id"] in sent_back}
                       for w in gather(name, feedback=sent_back) if w["id"] in sent_back]
            decisions.update(self.critic.judge(revised, store, viewer, final_round=True))
            warnings = [w for w in warnings if w["id"] not in sent_back] + revised

        accepted = [w for w in warnings if decisions[w["id"]][0] == "accept"]
        suppressed = sum(1 for w in warnings if decisions[w["id"]][0] == "suppressed")
        for w in warnings:
            action, reason = decisions[w["id"]]
            status = {"accept": "accepted", "drop": "dropped", "revise": "dropped"}.get(action, action)
            conn.execute("INSERT OR REPLACE INTO warning VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (w["id"], run_id, epic["id"], store.as_of, w["agent"], w["pattern_id"], w["claim"],
                          json.dumps(w["evidence"]), w.get("suggested_ticket"), json.dumps(w["prediction"]),
                          w["confidence"], status, reason))
        report = self.writer.run(epic, accepted, suppressed, store)
        conn.execute("INSERT OR REPLACE INTO review VALUES (?,?,?,?)", (run_id, epic["id"], store.as_of, json.dumps(report)))
        return report
