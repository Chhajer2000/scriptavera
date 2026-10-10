# Licensing Scriptavera

Scriptavera is copyright © 2026 Vaibhav Chhajer ("the copyright holder"). It is offered under two licences. You
choose the one that fits what you want to do.

## 1. The open-source licence: GNU AGPL, version 3 only

The program is free software under the **GNU Affero General Public License, version 3 only**
(SPDX: `AGPL-3.0-only`), with the additional permission in section 3 below. The full text is in
[`LICENSE`](LICENSE). In short, and the licence text is what counts:

- You may run, study and change Scriptavera, for any purpose, including commercial purposes. Changes you keep to
  yourself and use only yourself carry no obligation.
- If you give copies to others, in original or changed form, you must give them the complete corresponding source
  code under the same licence, with the copyright and licence notices kept intact.
- If you change it and let other people use your changed version **over a network** (for example as a website or a
  service), you must offer those users the complete corresponding source code of your changed version, under the
  same licence (section 13 of the licence). The app helps with this: the link "Source code of this copy" downloads
  the program, its tests and these terms, as found in the folder the app runs from.
- You may not add further restrictions, and there is no warranty.

## 2. The commercial licence

If you want to use Scriptavera **without** the conditions of the AGPL, you need a commercial licence from the
copyright holder. This is the case if, for example, you want to

- distribute Scriptavera, or a product containing it, without providing its source code under the AGPL;
- let others use a changed version over a network without offering them its source code under the AGPL;
- combine it with proprietary software in a way the AGPL does not allow;
- have a licence with a warranty, an indemnity, support, or other contractual terms.

A commercial licence is a separate written agreement. Its scope, term, price and support are agreed case by case.
To ask for one, write to **vchhajer0504@gmail.com** (subject: "Scriptavera commercial licence").

A commercial licence covers the code whose copyright the copyright holder owns or controls: his own code and every
contribution received under the Contributor Licence Agreement ([`CLA.md`](CLA.md)). It does not cover the
third-party components listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md), which keep their own
(permissive) licences and whose notices every licensee must keep. It grants rights to the Scriptavera name and logo
only if it says so ([`TRADEMARKS.md`](TRADEMARKS.md)).

## 3. Additional permission: your papers are yours

As an additional permission under section 7 of the GNU AGPL, the copyright holder grants you permission to use,
copy, change, publish and license, under any terms you choose, the output Scriptavera produces from your notes:
the papers, LaTeX sources, bibliographies, figures, HTML and PDF files, including the parts of Scriptavera's own
templates, macros, style sheets and scripts that the program copies into that output. This permission does not
cover Scriptavera itself. Anyone who conveys a copy of Scriptavera may remove this additional permission, as
section 7 of the licence allows.

## 4. Contributions

Every contribution to this repository is accepted only under the Contributor Licence Agreement in
[`CLA.md`](CLA.md). Under it, the copyright in the contribution is assigned to the copyright holder (or, where an
assignment is not possible, licensed to him with the right to relicense). That is what allows contributed code to
be offered under both licences above, including in commercial licences.

Code that someone changes in their own copy and never contributes stays theirs: the AGPL lets them share it under
the AGPL, and it does not let anyone else relicense it. Only contributions made under the CLA can be relicensed.

## 5. What the AGPL still allows without a commercial licence

So that the business model rests on the right expectations: under the AGPL anyone may, without paying, use
Scriptavera internally (changed or not), run an unchanged or changed copy as a service as long as they offer its
source code under the AGPL, and use it through a separate program at arm's length. What protects the copyright
holder's own service is the commercial licence for closed use, the trademark for the name, and the quality of the
service itself.

## 6. The hosted service

The copyright holder may run Scriptavera as a paid hosted service. As the owner of the copyright he is not bound by
the AGPL for his own copy (this holds for his own code and for contributions received under the CLA). Versions he
publishes as open source remain available under the AGPL. His hosted copy can switch off the source-code link by
setting `SCRIPTAVERA_SOURCE_LINK=0`.

## Notices in the source

Each program file (Python, JavaScript, style sheets, HTML, batch files) carries the notice

    SPDX-License-Identifier: AGPL-3.0-only
    Copyright (C) 2026 Vaibhav Chhajer

which means: this file is part of Scriptavera, offered under the AGPL version 3 only with the additional permission
above, unless you have a commercial licence that says otherwise. The KaTeX files in `scriptavera/static/vendor/katex/`
are under the MIT licence (see the `LICENSE` file there). The logo files are covered by [`TRADEMARKS.md`](TRADEMARKS.md).

*This file explains the licensing; it is not legal advice. Have a lawyer review the commercial agreement and the
CLA before relying on them.*
