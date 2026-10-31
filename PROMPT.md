Clean up my Android phone with cleanyourphone. You run every command yourself with `./cmp` (`.\cmp.ps1` on
Windows); never ask me to run a command or name a step. Follow its output exactly:

- The last lines of every command are `RESULT`, `SUMMARY`, `NEXT`, and sometimes `ASK_USER` and `ASK_JSON`.
- `ASK_JSON`: ask me those questions in your own interactive question box (in Claude Code the AskUserQuestion
  tool; in other assistants their equivalent), at most 4 questions at a time, as checkboxes where
  `multi_select` is true, and wait for my answers. You may shorten an option's label but keep its meaning.
  Only if you have no question box, ask in chat and wait.
- `ASK_USER` without `ASK_JSON`: ask me that question word for word and wait for my answer.
- `NEXT`: run that command next, filled in from my answers. If it is an install command, run it yourself.
- `RESULT: stop`: show me the output, including any `ERROR` and the steps under it, in plain words, and
  wait. When I say I've fixed it (or "continue"), run `./cmp go` again: it picks up where it stopped and
  doesn't ask again. Don't retry before that.
- Don't run your own `adb`, `pm` or `settings` commands, don't edit `data/never.txt`, and never show my
  phone's full serial number.

Start with `./cmp go`. When a `./cmp go` finishes with a `DONE` block, show it to me as it is.
