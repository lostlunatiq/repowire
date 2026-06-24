# Connect Abacus AI CLI

Repowire supports the [Abacus AI CLI](https://abacus.ai/help/abacusai-desktop/cli-installation) (`abacusai`) as a mesh peer.

## Install

If `abacusai` is on your PATH and `~/.abacusai` exists, run:

```bash
repowire setup
```

This writes a dedicated MCP config file at:

```
~/.abacusai/repowire-mcp.json
```

The default spawn command automatically loads it:

```bash
abacusai --permission-mode yolo --auto-accept-edits --mcp-config ~/.abacusai/repowire-mcp.json
```

## Start a session

### Option 1: spawn from Repowire

```bash
repowire peer new . --backend abacusai
```

### Option 2: start manually in an existing tmux pane

```bash
abacusai --permission-mode yolo --auto-accept-edits --mcp-config ~/.abacusai/repowire-mcp.json
```

## Verify

Start a session and ask the agent to run:

```text
whoami
```

It should return your mesh display name and circle.

## Limitations

- Abacus AI CLI does not expose lifecycle hooks (`SessionStart` / `Stop`), so automatic peer registration at session start is not available. Mesh registration happens lazily the first time the agent calls a Repowire MCP tool.
- Inbound ask/notify injection into an `abacusai` pane requires a future sidecar wrapper because there is no native hook to inject into.
- Token-budget stop reminders are not surfaced automatically for `abacusai`.
