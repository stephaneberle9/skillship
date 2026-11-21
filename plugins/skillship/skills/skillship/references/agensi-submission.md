# Submitting a pack to Agensi

[Agensi](https://www.agensi.io) is a marketplace for paid skills. Its sign-in is
OAuth with PKCE only, so a submission cannot run in CI: skillship's `agensi`
target **prepares** the package, and the submission happens here, through
Agensi's MCP connector, after the user confirms the preview.

## Prerequisites

- **The connector.** Agensi's remote MCP server is
  `https://mcp.agensi.io/mcp/auth` (Streamable HTTP). Look for its tools
  (`creator_status`, `submit_skill`, …). If they are missing, the user adds the
  server and signs in through `/mcp`, for Claude Code with

  ```text
  claude mcp add --transport http agensi https://mcp.agensi.io/mcp/auth
  ```

  and for claude.ai in the connector settings. Stop until it is connected.
- **Creator Mode** on the user's Agensi account, and a payout method for a paid
  listing. `creator_status` reports both.

## Steps

1. **Build.** `skillship build <name>` writes `dist/<name>/agensi/<name>.zip`
   and `<name>.preflight.md`.
2. **Pre-flight.** Read the report with the user. It mirrors Agensi's scan:
   eight checks, a score of 100 minus 20 per failed check and 5 per warning. A
   failed check blocks automatic approval, so fix fails in the source where the
   finding is real (a script that reads environment variables and calls the
   network, for example). A warning on `curl` or `base64` in documentation is
   often fine; say which ones are. Also read the notes on scripts that read
   sibling files, which Agensi runs alone.
3. **New or update.** `my_skills` lists the user's listings. An existing listing
   of this pack is updated with `update_skill` and a changelog of what changed
   since the last version; otherwise `submit_skill`. Names are unique across
   Agensi.
4. **Preview.** Read the tool's schema for how it takes the package, call it to
   build the preview (Agensi keeps it 7 days), and show the user the listing as
   Agensi will publish it: name, summary, price, the skills it found, the scan
   result.
5. **Submit only on an explicit yes** to that preview. A new listing then goes
   through an AI review and a person's approval; an update with no failed check
   and an AI approval can be approved automatically. Tell the user which
   applies.

## Not yet confirmed

These come from Agensi's docs and have not been seen in a live submission.
Compare them with what Agensi reports, and tell the user what differs:

- how a pack skill names a file in `shared/`: skillship rewrites such paths to
  `../../shared/…`;
- which frontmatter keys Agensi tolerates beyond `name`, `description` and its
  listing fields;
- whether the scan agrees with the pre-flight report.
