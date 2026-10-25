# Sprint backlog rules

- Each ticket is one focused change that can be implemented and tested on its own.
- Every BRD user story is covered by at least one ticket.
- P0 = needed for the first working version, P1 = needed for the MVP, P2 = polish.
- Size S = one module, M = a few modules, L = split it if you can.
- Owners: staff_swe (cross-cutting or high risk), lead_swe (core modules and contracts),
  senior_swe (well-specified implementation).
- Dependencies reference other ticket IDs; contracts and models come first.
- Acceptance criteria come from the BRD's Gherkin scenarios.
