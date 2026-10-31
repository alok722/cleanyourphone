# Contributing

The most useful contribution is **your phone's list**: the next person with that phone skips the guesswork.
Coding agents: [AGENTS.md](AGENTS.md) has the same rules.

## Add your phone

1. Run cleanyourphone (`./cmp go`) and live with the result for a few days.
2. Create `devices/<brand>-<model>/debloat.txt`. Start from your `my-list.local.txt` and copy the format of
   [`devices/realme-7-rmx2151/debloat.txt`](devices/realme-7-rmx2151/debloat.txt): one package per line under a
   `# group` heading, a trailing `# note` on anything non-obvious, and a header with the exact model, Android and
   skin version (`./cmp probe` prints them), plus anything that broke.
3. Add `devices/<brand>-<model>/phone.json`: `phone`, `brand`, `os`, `status` (`tested`), `models` (the
   `ro.product.model` value) and a one-line `notes`.
4. Run `./cmp lint devices/*/debloat.txt` and `python3 -m unittest discover -s tests`, then open a pull request.

Only list packages you removed and lived with. Never list anything from [`data/never.txt`](data/never.txt):
`cmp` refuses those, and CI fails the PR. Never include your phone's serial number.

Tried a starter list and it worked? Copy it into your own `devices/<brand>-<model>/` folder as `tested`.

## Improve the tools

Python 3.8+, standard library only. Every `cmp` step ends with the `RESULT / SUMMARY / NEXT / ASK_USER` footer
the prompt relies on; keep it, and add a test against `tests/fake_adb.py`. One adb shell command per call.
