# Third-party components

## Included in this repository

### KaTeX 0.16.47 (`scriptavera/static/vendor/katex/`)

Renders the mathematics in the interface. The JavaScript, the style sheet and the fonts are distributed as part of
the KaTeX package under the MIT licence (also in `scriptavera/static/vendor/katex/LICENSE`). The HTML version of a
paper that Scriptavera exports loads KaTeX 0.16.11 from the jsDelivr CDN when it is opened, under the same licence;
opening that file therefore contacts jsDelivr.

    The MIT License (MIT)

    Copyright (c) 2013-2020 Khan Academy and other contributors

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

### CodeMirror 5.65.1 (`scriptavera/static/vendor/codemirror/`)

The text editor of the LaTeX editor page (`lib/codemirror.js` and `lib/codemirror.css`, minified). The LaTeX mode,
search and completion are written for Scriptavera in `editor.js`. MIT licence, copyright (C) 2017 by Marijn
Haverbeke and others; the full text is in `scriptavera/static/vendor/codemirror/LICENSE`.

### PDF.js 6.2.108 (`scriptavera/static/vendor/pdfjs/`)

Shows the compiled PDF in the LaTeX editor (the "legacy" build, `pdf.min.mjs` and `pdf.worker.min.mjs`). Apache
License 2.0, copyright Mozilla Foundation; the full text is in `scriptavera/static/vendor/pdfjs/LICENSE`.

## Installed separately (not included)

These Python packages are installed by `pip install -r requirements.txt` and are not part of this repository. Each
keeps its own licence, as published by its project; all of them permit use in open-source and commercial software.

| Package | Licence |
|---|---|
| starlette | BSD-3-Clause |
| uvicorn | BSD-3-Clause |
| python-multipart | Apache-2.0 |
| requests | Apache-2.0 |
| pypdfium2 (and the PDFium library it contains) | Apache-2.0 or BSD-3-Clause; PDFium: BSD-3-Clause |
| Pillow | MIT-CMU |
| cryptography | Apache-2.0 or BSD-3-Clause |

## Services the program talks to

The program sends requests to services chosen by the user: AI model providers (with the user's own keys), and
scholarly databases (OpenAlex, Crossref, Semantic Scholar, arXiv, zbMATH Open, Unpaywall and doi.org). Using them is subject to each
service's own terms. Papers written with Scriptavera belong to their authors.

To timestamp the record of uploaded notes (unless this is turned off in Settings), it sends a 32-byte fingerprint,
and nothing else, to RFC 3161 time-stamp authorities (by default FreeTSA and DigiCert) and to the OpenTimestamps
calendar servers; to confirm a Bitcoin anchor it reads the block's public data from blockstream.info or mempool.space.
