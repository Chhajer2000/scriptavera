# Contributing to Scriptavera

Thank you for helping. Bug reports, improvements to reading, writing and checking, support for new fields, tests
and documentation are all welcome.

## Before you start

- **Sign the Contributor Licence Agreement.** Contributions are accepted only under [`CLA.md`](CLA.md): the
  copyright in what you contribute is assigned to the maintainer, so that Scriptavera can be offered both under the
  GNU AGPL and under commercial licences ([`LICENSING.md`](LICENSING.md)). You keep the right to use your own work.
  Sign it before your first contribution is merged: by the comment the pull-request template asks for, or by
  e-mailing a signed copy (see "How to sign" in the CLA). Nothing is merged before that.
- **Contributors must be adults** (18 or older in India, or the age of majority where they live).
- **Check who owns your work.** If you are employed, or a student, your employer or university may own what you
  write. Get their written permission first (see section 7 of the CLA).
- **Open an issue first** for anything larger than a small fix, so that the approach can be agreed before you spend
  time on it.

## How to contribute

1. Fork the repository and create a branch for your change.
2. Keep the program general. Nothing may be written for one paper, one field or one set of notes: every rule,
   prompt and example must work for any notes in any area.
3. Never change the author's mathematics automatically. Anything that could change a formula must be checked
   mechanically and shown to the author.
4. Run every test; none of them needs an API key or the internet:

        python tests/run_all.py          the logic and the server
        python tests/run_all.py --ui     also the interface in a browser (needs playwright)

5. Add a test for what you change, open a pull request against `main`, and describe what it does and why.

New source files start with the notice

    SPDX-License-Identifier: AGPL-3.0-only
    Copyright (C) 2026 Vaibhav Chhajer

## Reporting a security problem

Do not open a public issue for a security problem (for example one that could expose a user's API keys or notes).
Write to vchhajer0504@gmail.com instead.

## Behaviour

Be respectful and constructive. Criticism is welcome when it is about the work.
