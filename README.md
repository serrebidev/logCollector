# Log Collector and Serrebi Fixes

An NVDA add-on with two parts:

[![Join SerrebiProjects on Telegram](https://img.shields.io/badge/Telegram-SerrebiProjects-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/SerrebiProjects)

- **Log Collector** — gathers ERROR/CRITICAL entries and tracebacks from the
  NVDA logs into a copyable report, plus live debug-log capture.
- **Serrebi Fixes** — targeted monkeypatches for NVDA core bugs observed in the
  wild (see `globalPlugins/serrebiFixes.py` for the full rationale of each fix).

**Questions, bugs, or release news?** Join the [SerrebiProjects Telegram group](https://t.me/SerrebiProjects), the fastest place to get help.

More serrebi projects: [projects.serrebiradio.com](https://projects.serrebiradio.com)

## Gestures

- `NVDA+Shift+L` — collect log errors/tracebacks into a copyable report.
- `NVDA+Shift+D` — toggle live debug-log capture.

## Build

```
python build.py
```

The version comes from `manifest.ini`, so the packed add-on is written to
`dist/logCollectorAndFixesFromSerrebi-<version>.nvda-addon`.

## Tests

```
python -m unittest discover -s tests
```

The guards patch NVDA core, so the suite runs against a reproduction of NVDA's
own auto-property machinery and of the core call sites each guard protects.
Run it before tagging a release.

## Credits

See [CREDITS.md](CREDITS.md) for where the ideas behind this project came from, and the third-party projects it builds on.
