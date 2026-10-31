Clean up my Android phone with cleanyourphone. Use only `./cmp` (`.\cmp.ps1` on Windows) and follow its
output exactly:

- The last lines of every command are `RESULT`, `SUMMARY`, `NEXT`, and sometimes `ASK_USER` and `ASK_JSON`.
- `ASK_JSON`: ask me those questions in your own interactive question box (in Claude Code the AskUserQuestion
  tool; in other assistants their equivalent), at most 4 questions at a time, as checkboxes where
  `multi_select` is true, and wait for my answers. You may shorten an option's label but keep its meaning.
  Only if you have no question box, ask in chat and wait.
- `ASK_USER` without `ASK_JSON`: ask me that question word for word and wait for my answer.
- `NEXT`: run that command next, filled in from my answers. If it is an install command, run it yourself.
- `RESULT: stop`: stop and show me the output, including any `ERROR` and the steps under it. Don't retry.
- Don't run your own `adb`, `pm` or `settings` commands, don't edit `data/never.txt`, and never show my
  phone's full serial number.

Start with `./cmp go`. When the second `./cmp go` finishes, show me its `DONE` block as it is.
