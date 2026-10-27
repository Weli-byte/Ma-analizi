# ADR 0084 — A pause switch for the injuries collection (2026-10-27)

## Finding
The API-Football account is suspended (the provider's support named a shared or dynamic IP such as GitHub Actions as a likely cause; not confirmed). The cloud job `markets-cloud` still called the provider every four hours with the suspended key.

## Decision
- `python -m src.markets.injuries` makes no request when `INJURIES_COLLECT=off` (it prints `PAUSED`, exit 0). The cloud job passes the repository variable `INJURIES_COLLECT` (unset = collect as before).
- Stored observations stay; the job keeps copying them and the existing three-day pruning applies, so the card shows older entries until they expire and then says nothing was observed, never that nobody is out (ADR 0057 unchanged).
- Resuming is one step: delete the variable or set it to anything but `off`.

## Not decided here
Moving the collection to the owner's PC (a fixed IP) is not built: it needs a write path from the PC to the private data repository and a live test with a working account, neither exists yet. To be decided after the provider answers.
