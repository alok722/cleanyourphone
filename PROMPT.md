Clean up my Android phone with cleanyourphone. Use only `./cmp` (`.\cmp.ps1` on Windows) and follow its
output exactly:

- The last lines of every command are `RESULT`, `SUMMARY`, `NEXT` and sometimes `ASK_USER`.
- `ASK_USER`: ask me that question word for word and wait for my answer.
- `NEXT`: run that command next, filled in from my answer. If it is an install command, run it yourself.
- `RESULT: stop`: stop and show me the output, including any `ERROR` and the steps under it. Don't retry.
- Don't run your own `adb`, `pm` or `settings` commands, don't edit `data/never.txt`, and never show my
  phone's full serial number.

Start with `./cmp go`. When it asks which groups to keep and which apps to uninstall, pass my answers as
`--keep` and `--uninstall` (add `--adblock` only if I said "adblock"). When the second `./cmp go`
finishes, show me its `DONE` block as it is.
