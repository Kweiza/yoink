---
description: Record this task's summary AND the list of files you plan to modify (declares them upfront so teammates see your scope and conflicts surface early)
---

Please run this bash command and show its output verbatim, with no additional commentary:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/bin/yoink-set-task" $ARGUMENTS
```

Invoke this command with a 1~2 sentence summary of your goal AND the list of files you intend to edit, separated by `--files`:

```
/yoink-coordination:task "compress temp.md and remove obsolete drafts" --files temp.md temp1.md temp2.md temp3.md
```

The summary appears in the team's yoink:status issue body alongside the declared file list. Declaring files upfront lets PreToolUse skip redundant acquires and lets teammates see scope before you start editing. PreToolUse remains a safety net for files you didn't pre-declare.
