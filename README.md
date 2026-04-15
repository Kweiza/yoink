# yoink — team coordination plugin for Claude Code

Lightweight, GitHub-backed coordination to surface file-level conflicts
between concurrent Claude Code sessions in the same repo.

## Install

```
/plugin install yoink-coordination@kweiza/yoink
```

Requires Claude Code v2.1.105+ and `gh` CLI authentication.

## Usage

After installing, start a Claude Code session in your target repo. The
plugin automatically records which files your session is working on and
surfaces warnings when another teammate's active session has declared the
same file.

Run `/yoink-coordination:team-status` to see current activity across
teammates.

## License

MIT
