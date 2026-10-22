# CogniRings skill development

This is the reusable skill source. Personal archives belong elsewhere; do not commit user profiles, journal entries, credentials or raw research here.

- Keep README.md English by default with the Chinese switch; update i18n/README.zh-CN.md alongside user-facing changes.
- Maintain one installation request and the original last30days dependency. Do not modify upstream research logic.
- The v2 contract lives in references/archive-contract.md. knowledge/profile are current syntheses; journal is append-only history; .cognirings contains maintenance data. Preserve v1 readers and explicit migration without overwriting the source.
- Do not infer understanding from material volume or goals. Preserve the distinction between U/L1–L5, practice, assistance, scope and eight mentoring activities.
- Run python3 -m unittest discover -s scripts -p 'test_*.py' and python3 scripts/check_reading.py . after relevant code or contract changes. Validate examples against the real CLI; validate Mermaid after diagram changes.
- validate/status are read-only; refresh preserves prose outside generated blocks. Avoid rewriting historical records or silently resetting integrity manifests.
- Use descriptive codex/ branches, review remote changes before an authorized push, and never force-push.
