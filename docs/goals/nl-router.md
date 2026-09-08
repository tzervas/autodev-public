# nl-router — autodev goals (trust ladder rung 1)

Written from `csd-repo-survey` output and the source, not from memory. Every
signature and key below was read from the file it names.

## What the survey found

| evidence | goal it became |
|---|---|
| `cabal-self-review.yml` gates on `github.server_url` | G-CI-1 — the same defect that skipped every rung-0 run for a week |
| `classify()` has no test naming it, and `max()` on an empty table raises | G-CLASSIFY-1, G-CLASSIFY-2 |
| `client.chat()` points at `127.0.0.1:8080` LocalAI, which is gone | G-CLIENT-1 |
| subscripted keys `use_cases`, `choices`, `alias`, `max_tokens`, `temperature` | carried into every test goal below |

## Facts every goal here must respect

- `classify(text, table)` reads `table['use_cases']`, and each entry may carry
  `keywords` (a list) and `priority` (an int). A fixture without `use_cases`
  raises `KeyError`; a fixture with an EMPTY `use_cases` raises `ValueError` from
  `max()` on an empty sequence.
- `chat(alias, prompt, temperature, max_tokens)` reads
  `response['choices'][0]['message']['content']`.
- The runner offers `self-hosted linux x64 podman compute-cpu host-homelab`.

| id | goal | state |
|---|---|---|
| G-CI-1 | `.github/workflows/cabal-self-review.yml` line 11 reads `if: github.server_url == 'https://git.example.com'`. `github.server_url` is set from the RUNNER's registered address, which is `http://198.51.100.5:3080`, so this is always false and the job reports `skipped` — which reads as success. Change that one line to `if: github.repository_owner == 'cabal-collective'`, which is a fact about the repository and cannot drift with runner registration. Change ONLY that file and ONLY that line. | open |
| G-CLASSIFY-1 | In `tests/test_classify.py`, add tests for `classify` in `src/nl_router/classify.py`. Prove: a table whose entries have `keywords` returns the name whose keywords appear most often in the text; `priority` multiplies a use case's score so a lower-count higher-priority entry can win; and when no keyword matches at all, it returns `'general'` if `general` is a key of `table['use_cases']`. Build fixtures shaped `{'use_cases': {'<name>': {'keywords': [...], 'priority': N}}}` — `classify` subscripts `table['use_cases']` and will raise `KeyError` without it. Keep every existing test. | open |
| G-CLASSIFY-2 | `classify` in `src/nl_router/classify.py` calls `max(scores.values())` on a dict built from `table['use_cases']`. When `use_cases` is empty that raises `ValueError: max() arg is an empty sequence` instead of reporting a bad config. Make `classify` raise a `ValueError` whose message names the problem, or return a documented default — pick one and say which in the code. This CHANGES behaviour. Change ONLY `src/nl_router/classify.py`. Its tests are a separate goal. | open |
| G-CLASSIFY-3 | In `tests/test_classify.py`, add a test proving the empty-`use_cases` behaviour that G-CLASSIFY-2 introduced in `src/nl_router/classify.py`. Read that function first and assert what it actually does now — do not assume. Change ONLY the test file. Keep every existing test. | open |
| G-CLIENT-1 | `src/nl_router/client.py` sets `LOCALAI_URL = 'http://127.0.0.1:8080/v1/chat/completions'`. LocalAI is retired; inference is the LiteLLM gateway. Make the base URL read from the `NL_ROUTER_GATEWAY_URL` environment variable, defaulting to `http://203.0.113.20:4000/v1/chat/completions`, and rename the constant to `GATEWAY_URL`. Keep the existing `LOCALAI_API_KEY` fallback working but prefer `NL_ROUTER_API_KEY` when set. This CHANGES behaviour. Change ONLY `src/nl_router/client.py`. | open |
| G-CLIENT-2 | In `tests/`, add a test module for `chat` in `src/nl_router/client.py` that does NOT make a network call: monkeypatch `urllib.request.urlopen` to return a canned response and prove `chat` sends `model`, `temperature` and `max_tokens` in the JSON body, sets the Authorization header only when a key is in the environment, and returns `response['choices'][0]['message']['content']`. Change ONLY the new test file. | open |
| G-CI-2 | In .github/workflows/cabal-self-review.yml, replace the hardcoded https://git.example.com/api/v1/... URL with the runner-supplied github.api_url and replace secrets.CABAL_FORGEJO_AGENT_TOKEN with secrets.GITHUB_TOKEN, assert the job succeeds when secrets.GITHUB_TOKEN is set and fails with a clear message if empty, Changes behaviour, Keep every existing test. | open |
