# AGENTS.md

Rules for any coding agent (Claude Code, Codex, Gemini CLI, …) working in this repo.

## First: which job are you doing?

| Job | Do this |
|---|---|
| Cleaning someone's phone | Follow [PROMPT.md](PROMPT.md) step by step, using `./cmp` (`cmp.ps1` on Windows) |
| Changing the tools or lists | Read the rules below, run the tests, keep the footer format |

## Cleaning a phone: rules that are not negotiable

1. **Only drive the phone through `./cmp`.** Don't run your own `pm disable-user`, `pm uninstall` or
   `settings put` loops: `cmp` journals every change before making it, enforces the never-touch list
   and verifies each batch, and your own commands skip all three.
2. **Read the footer of every command and do what it says:**
   - `NEXT:` run that exact command next.
   - `ASK_USER:` ask the person that question, word for word, and wait for the answer.
   - `RESULT: stop`: stop, show the person the output, and don't work around it.
3. **Install what's missing yourself.** If `NEXT` is an install command (adb, Homebrew, platform-tools),
   run it without asking; the person opted in by starting cleanyourphone.
4. **Never edit `data/never.txt` to get a package through.** If a package is refused, tell the person why.
5. **Never paste the phone's serial number** into issues, PRs, device lists or posts. `cmp` prints a
   short device id instead.

## Repo map

| Path | What |
|---|---|
| `cmp`, `cmp.ps1` | Launchers for `tools/cmp.py` |
| `tools/cmp.py` | The CLI; every step ends with the `RESULT / SUMMARY / NEXT / ASK_USER` footer |
| `tools/phone.py` | adb wrapper: one shell command per call |
| `tools/journal.py` | Backups and the change journal in `~/.cleanyourphone/<device-id>/` |
| `tools/safety.py` | Never-touch list, role guards (Home, SMS, dialer, keyboard), list lint |
| `data/never.txt` | Core packages no list may contain |
| `devices/<brand>-<model>/` | `debloat.txt` + `phone.json` (`status`: `tested` or `starter`) |
| `tests/` | Unit tests against `tests/fake_adb.py`, which replays recorded phone output |

## Changing the tools

- Python 3.8+, standard library only. No pip dependencies in the core.
- One `phone.shell()` call per command; never chain with `&&` or `;` (a test enforces this).
- Keep the footer on every step and add a test that asserts it.
- `python3 -m unittest discover -s tests` and `./cmp lint devices/*/debloat.txt` must pass.

## Commit and PR style

Short imperative subject ("Add Samsung A50 list"), a body that says what was tested on which phone.
