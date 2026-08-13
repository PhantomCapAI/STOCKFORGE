# Skills

Operator/agent-facing playbooks for working with StockForge and Bankr. These are
docs (for humans and future Claude Code / Grok Build iterations), not executable
skill packages.

- [`bankr-launch.md`](./bankr-launch.md) — how launching + fee claiming works
  against the **real** Bankr API, including the verified endpoints and the known
  unverified bits (stock-pairing).
- [`operating.md`](./operating.md) — run/pause/approve/claim playbook.

## Related upstream skill

Bankr publishes an official agent skill:
`install the bankr skill from https://github.com/BankrBot/skills`. StockForge
does **not** depend on it — it talks to Bankr directly via the REST Agent API and
the `@bankr/cli`. If you install the upstream skill in your agent environment,
it can coexist.
