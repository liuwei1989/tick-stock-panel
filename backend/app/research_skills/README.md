# Research skills

The 15 YAML research frameworks are from ZhuLinsen/daily_stock_analysis,
commit d3fee51a9e5ebec756fe8184c1d13b2accf5e39f (MIT; see LICENSE).

They are research prompts, not executable stock screening/backtest strategies.
The adapter supplies existing repository evidence and overrides tool references:
no upstream tool names are exposed or assumed callable. Results remain research
observations and never place orders. Missing evidence is disclosed.

Local additions belong in `data/research_skills/*.yaml`, use the same fields,
and cannot replace built-in IDs. YAML is loaded with safe_load, never executed.

`upstream.json` pins the source revision and SHA-256 of every original YAML.
The catalog test verifies the full file set and every declared metadata field.
Keep original definitions unchanged; project-specific execution rules belong
in `services/research_skills.py`.

The application exposes all 15 definitions in the stock-analysis page and in
the cockpit's batch/scheduled research controls. Each run can select up to
three skills, or use automatic selection. The detail view includes the full
instructions, aliases, applicable market regimes and required input types.

The loader preserves `core_rules`, `required_tools`, `allowed_tools`,
`default_active`, `default_router`, `default_priority`, `market_regimes`,
`user_invocable` and `disable_model_invocation`. Automatic selection matches
names/aliases and falls back to the highest-priority default-active skill;
explicit empty selection remains empty. Automatic invocation respects the
invocation flags. Regime and router metadata are retained for inspection,
not treated as evidence that market-dependent routing or tools have run.

The upstream root `SKILL.md`, Grok example and `.claude/skills` are assistant
integration/development workflows, not part of the application's 15 trading
skills. They are not loaded into this research catalog.
