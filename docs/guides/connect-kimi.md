# Kimi Code

`repowire setup` configures Kimi Code automatically. This page explains what was wired and what to check when it didn't.

## What gets installed

Hooks land in `~/.kimi-code/config.toml` under the `[[hooks]]` array. The Repowire MCP server is added to `~/.kimi-code/mcp.json`.

### Hooks

Three lifecycle events are wired by default:

| Event | What it does |
| --- | --- |
| `SessionStart` | Registers the peer with the daemon and spawns the WebSocket hook supervisor |
| `UserPromptSubmit` | Marks the peer `busy`; blocks the turn if the token budget is exhausted |
| `Stop` | Estimates token usage, delivers any pending legacy `/query`, fetches `/asks/pending` and emits a reminder block if open asks exist, then marks the peer `online` |

The hooks shell out to the `repowire` CLI: `repowire hook session --backend=kimi-code`, `repowire hook prompt --backend=kimi-code`, and `repowire hook stop --backend=kimi-code`.

### MCP server

The Repowire MCP server is added as `repowire`. It runs as `repowire mcp` over stdio and provides the stable tool surface: `ask`, `ack`, `notify_peer`, `broadcast`, peer listing, schedules, and related commands.

## Verifying

```bash
repowire status
```

Shows whether Kimi Code is detected and whether hooks are installed.

To confirm hooks fire, open a new Kimi Code session in tmux and watch `repowire peer list`. The peer should appear within a few seconds of the first prompt.

## Troubleshooting

- Hooks not firing → [Hooks not firing](../troubleshooting/hooks.md).
- Peer stuck `busy` after a turn ends → [Ghost peers and stuck busy state](../troubleshooting/ghost-peers.md).
