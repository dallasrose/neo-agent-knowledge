# Security Policy

Neo is early-stage software that may store private research notes, agent memory,
source URLs, and local database paths. Treat Neo databases as sensitive data.

## Reporting Vulnerabilities

Please do not open a public issue for a suspected vulnerability.

Use GitHub's private vulnerability reporting for this repository if available,
or contact the maintainer privately through GitHub before public disclosure.
Include enough detail to reproduce the issue and note whether it affects local
SQLite use, REST deployments, MCP transport, or hosted/remote configurations.

## Supported Versions

Only the latest commit on `main` is currently supported.

## Security Expectations

- Do not expose `neo serve-rest` publicly without network-level protection.
- Set `NEO_MCP_API_KEY` when exposing HTTP MCP transport.
- Keep `.env` files and Neo database files out of source control.
- Review source metadata before sharing exports; ingested nodes may include URLs,
  titles, transcripts, and agent notes.
