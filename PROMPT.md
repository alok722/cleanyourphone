# The prompt

**Before you paste:** on the phone, Settings → About phone → tap **Build number** 7 times → back →
Developer options → **USB debugging** on. Plug the phone into the computer with a USB data cable.
For the best result keep it unlocked with the screen on (Settings → Display → Screen timeout: 5 minutes).

Then open this repo's folder in any coding assistant (Claude Code, Gemini CLI, Codex: free tiers are fine)
and paste everything below the line. It takes about 5 minutes and asks you one question.

---

Clean up my Android phone with cleanyourphone. Use only `./cmp` (`.\cmp.ps1` on Windows) and follow its
output exactly:

- The last lines of every command are `RESULT`, `SUMMARY`, `NEXT` and sometimes `ASK_USER`.
- `ASK_USER`: ask me that question word for word and wait for my answer.
- `NEXT`: run that command next, filled in from my answer. If it is an install command, run it yourself.
- `RESULT: stop`: stop and show me the output.
- Don't run your own `adb`, `pm` or `settings` commands, don't edit `data/never.txt`, and never show my
  phone's full serial number.

Start with `./cmp go`. If the phone asks "Allow USB debugging?", tell me to tap Allow. When it asks which
groups to keep, pass my answer as `--keep` (and add `--adblock` only if I said "adblock"). When the second
`./cmp go` finishes, show me its `DONE` block as it is.
