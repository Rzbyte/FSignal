# Superseded alert screenshots

Both are real alerts, delivered to a real workspace. They are here rather than in
`docs/proof/` because they were captured before two fixes landed, and each shows
what the fix was for.

| File | Shows |
|---|---|
| `slack_evidence_1.png` | A ⚠️ beside the action buttons. Slack marks a message that way when nothing is configured to acknowledge a click. `POST /slack/interactions` now does, so the current screenshots have no warning. |
| `slack_evidence_2.png` | The founder's opening line quoted twice, with the search index's byline wedged in between. A search result glues a title to a snippet and the two overlap; the excerpt now keeps the fuller telling once. |

Kept because deleting the before is how a fix stops being checkable. Neither has
been edited.
