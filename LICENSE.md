This repository does not yet have a finalized open-source license.

**Status: unlicensed pending a maintainer decision.**

Until a `LICENSE` file with a specific, approved license text is added to
this repository, the default legal position applies: all rights to the
original code and documentation in this repository are reserved by their
authors, and no license to use, copy, modify, or distribute this work is
granted, notwithstanding the repository being publicly visible on GitHub.

## Why this file exists instead of a license

Athena's own documentation (`docs/README.md`) states that it extends
RareCure, described there as "an open-source, MIT-licensed pipeline."
That points toward MIT as a natural, compatible
choice for Athena's own license, for consistency with the project it
builds on and with the open-source principles stated in the project's
design documents (open data sources only, self-hostable, no proprietary
data licenses).

That said, choosing and applying a license is a legal decision for the
project's maintainers/copyright holders to make deliberately — not
something to default into silently. This placeholder exists so that:

- Contributors and users are not misled into thinking a license has been
  chosen when it hasn't.
- The intended direction (MIT, for RareCure-compatibility) is recorded
  for whoever makes the final call.
- No specific license text is asserted without maintainer sign-off, since
  an incorrect or premature LICENSE file is harder to walk back than an
  honest "not yet."

## What maintainers need to do

1. Confirm the license (MIT is the working recommendation; Apache 2.0 is
   a reasonable alternative if an explicit patent grant is wanted).
2. Confirm the copyright holder name to put in the license header
   (an individual, or the `healers-second-look` organization).
3. Replace this file with a proper `LICENSE` (no extension, plain text)
   containing the chosen license's exact, unmodified text.
4. Update `pyproject.toml`'s `[project]` table to add a `license` field
   matching the chosen license, and add a short "License" section to
   `README.md` linking to it.
5. Remove this file once `LICENSE` exists.

## In the meantime

Anyone who wants to use, modify, or redistribute this code beyond simply
viewing it on GitHub should contact the maintainers to clarify terms.
