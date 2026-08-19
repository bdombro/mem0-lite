# Doc for tracking temporal notes, ideas for future changes.

## Ideas

These are not committed and should be considered wild ideas.

### Skil or plugin to create/sync /thread-memory memories for git-committed memories

And/or what if we started saving them in ~/.mem0/epics/{epic}?

### Performance optimizations -- less ai round-trips possible?

### Memory corrections and/or self-healing

### Pruning of decisions and footguns

### Use a sqlite db at ~/.mem0/telemetry.db

Use a sqlite db at ~/.mem0/telemetry.db instead of jsonl logs. And since select and updates will become cheap, we could combine the 3 logs into a single table. And wipe could flags like --debug which would purge only the debug table columns

