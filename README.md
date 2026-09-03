# Log Collector and Serrebi Fixes

An NVDA add-on with two parts:

- **Log Collector** — gathers ERROR/CRITICAL entries and tracebacks from the
  NVDA logs into a copyable report, plus live debug-log capture.
- **Serrebi Fixes** — targeted monkeypatches for NVDA core bugs observed in the
  wild (see `globalPlugins/serrebiFixes.py` for the full rationale of each fix).

**Questions, bugs, or release news?** Join the [SerrebiProjects Telegram group](https://t.me/SerrebiProjects), the fastest place to get help.

## Gestures

- `NVDA+Shift+L` — collect log errors/tracebacks into a copyable report.
- `NVDA+Shift+D` — toggle live debug-log capture.

## Build

```
python build.py
```

The packed add-on is written to `dist/logCollectorAndFixesFromSerrebi-2026.8.19.1.nvda-addon`.
