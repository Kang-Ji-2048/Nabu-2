# Nabu pitch notes

Four slides · 120 seconds. Press N to see notes in the deck.

## 01 · Customer problem · 25 seconds

The buyer is an engineering manager or delivery lead preparing an epic. Lessons about overruns and missing work are scattered across old tickets. Nabu turns that history into specific risks and suggested tickets before a team commits. Customer demand remains a hypothesis.

## 02 · Architecture · 30 seconds

History, specialists, critic, review. The historian mines patterns. The planner routes the plan to estimate, dependency and missing-work checks. A fresh critic request accepts, requests one revision or drops warnings. The writer saves a review with cited evidence. A person decides whether to change the plan. The scorekeeper later checks outcomes and updates pattern weights.

## 03 · Event guidance applied · 45 seconds

Reference: the supplied event slide says Build it. Let it run. Prove it worked. Its sequence is Console setup, hosted agent, finish line, build eval and hill climb. Our equivalent is a custom Python workflow using Claude Messages API, a saved cited review as the finish line, durable checkpoints and bounded requests, then outcome scoring and comparison with a baseline. The event commands /claude-api managed-agents-onboard, /goal, /claude-api build-eval and /claude-api hillclimb are reference commands; we have not used or integrated them. Pattern weights adapt after hits and misses. That is not automated prompt optimisation: a controlled prompt hill-climb against preselected held-out cases remains a next step. Managed hosting is also future work. Code guards citations and scope; a delivery lead approves plan changes.

## 04 · Business viability · 20 seconds

Start with delivery organisations willing to pilot on authorised exports. £750 per month is a pricing hypothesis; the costs, margins and hours saved are assumptions. Measure useful planning changes, investigation time and repeat use before expanding.

