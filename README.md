<p align="center"><img src="docs/logo.png" alt="Scriptavera" width="360"></p>

# Scriptavera

*Scripta vera*, "true writings": Scriptavera turns handwritten research notes (a scanned PDF or photos) into a
research paper in LaTeX without changing what you wrote. The first field it supports is mathematics, the hardest one
to get exactly right; you choose the area first, and nothing in the app is specific to one topic or one paper.

The work is split into twenty small steps. Each AI call has one job, every step saves its result, and the app stops
up to six times to ask you what only you can decide.

When you start, you give the area, the problem you are working on in your own words, and keywords (both required),
and optionally the one or two papers your work relies on most. These are looked up first and read first.

**2-Minute Video Walkthrough:** https://youtu.be/BoExpXqJ0BY

## Start

Windows: double-click `run_app.bat`. Otherwise:

    python -m pip install -r requirements.txt
    python app.py

The app opens in your browser at http://127.0.0.1:8765 and runs only on your computer. Keep its window open while it works.

## Keys

Open **Settings and API keys** and paste at least a Google Gemini key (free at https://aistudio.google.com/apikey).
A Groq key helps with the many small tasks. Keys are stored in the file `.env` in this folder and are sent only to the
provider they belong to.

## The steps

**Reading your notes**

1. *Scout read.* A few pages, words only, equations skipped, to find the topic.
2. *Literature search.* Papers in your area from arXiv, zbMATH, OpenAlex, Crossref and Semantic Scholar.
3. **Your topic.** You see what the app understood (with its confidence) and the papers it found. Confirm, exclude
   papers, correct the problem or keywords, or write more precisely what the notes are about: your comment goes to the
   model, the topic is rewritten, every paper is judged again and the search is repeated, until you confirm.
4. *Notation survey.* Every page read with the topic known; every symbol and special term catalogued.
5. **Your confirmation.** You confirm the topic and every symbol or term the notes use without defining. A page button
   marks on your page where the symbol was read.
6. *Deep reading.* A refined search, then the most relevant papers are read one by one.
7. *Final transcription.* Every page read again with the confirmed notation and the research context.
8. *Figures.* Each sketch in the notes is redrawn as a proper figure. Labels you wrote on a formula with arrows or
   braces are not figures: they are written into the formula as braces with the label under the term.
9. **Your review.** You check the transcript against the page images and say, for each figure, whether the redrawn
   version is what you drew (or ask for it to be drawn again, use your own drawing, or leave it out). If something
   taken for a figure is only your note on a formula, one button writes it into the text; the formula itself is
   checked to be unchanged.

Pages that look like a notebook cover or a blank page are left out, but only when nothing on them was read: a page
with any formula, printed text or drawing is always kept. Pages left out are shown to you at the topic stop and at
the review; including one later reads that page alone, and nothing else is read again.

**First draft**

10. *Understanding the paper.* All of the notes are read together with the papers read: the problem, every result,
   what depends on what. A second, independent reading checks that every "result" is one you state as such, and not a
   step or a case of a longer proof (such a step would lose the assumptions it depends on). Every later step works
   from this.
11. *Assessment of the notes.* For every result: is it fully proved in your notes, does the proof have a gap, is it
    only sketched, or is there no proof at all; and does it already appear in one of the papers that were read. And:
    does the main result stand on what is proved.
12. **Your plan for the paper.** You decide what the paper is (see below). If every result is proved and none was
    found in the literature, this step is passed by itself.
13. *Introduction.* A first introduction, used by the writers as context; you approve the final one at the end.
14. *Structure and first draft.* The order of definitions, lemmas and theorems is decided; an argument that runs over
    several pages always stays in one section. Each section writer sees the whole of your notes, writes only its own
    pages, and continues from the end of the previous section. Every formula of your notes is compared mechanically
    with the draft, and missing or altered ones are restored.

**Revision**

15. *Shorter arguments.* The draft is re-read for arguments carried out twice, methods that can be stated once as a
    general lemma, and proofs that take a long way round. Each proposal is written out and checked by a second,
    independent reading.
16. *Proof check.* Every proof is examined step by step. Each suspected problem goes to a second opinion, and only
    what that upholds is reported.
17. *Referee's read.* The whole paper is read the way a journal referee reads a submission.
18. **Your decisions.** Anything that touches the mathematics (a shortened proof, a correction, a remark) is done only
    if you say so. If the three readings find nothing of that kind, this step is passed by itself.

**Final version**

19. *Final version.* Your decisions are carried out; every part is copy-edited once (grammar, tone, consistency, the
    referee's points on presentation); typography is corrected by rule (punctuation after displayed formulas, ties,
    spacing); citations are checked; the abstract is written to the journal's length; five titles are suggested.
    The LaTeX file is generated by code and compiled.
20. **Title, introduction, citations.** You choose the title from the suggestions (or ask for five more, or write your
    own), accept or edit the introduction or say what should change, and tick the papers it cites from a list of
    the closest ones, each with a link to Google Scholar. The app lists what still stands between the paper and a
    submission (an error you chose to keep, a proof with a gap presented as proved, a formula that differs from
    your notes, a citation the cited paper may not support).

## Notes that are not (yet) a finished paper

Not every set of notes is a new result with complete proofs. The assessment says which case you are in, with the
evidence, and you choose:

- **A research article**: the main result is proved and was not found in the papers read.
- **A paper on what is proved**: what the notes prove is presented as results; what they do not prove is stated as a
  conjecture or an open question, never as proved. A result whose proof uses a conjecture is stated as conditional.
- **An expository note**: the results are already known. They are credited to their sources and nothing is claimed
  as new.
- **Not yet**: nothing more is written. Complete the notes and start again with them.

For each result with an incomplete proof you choose: keep it (you will complete the proof; the paper is then marked
as not ready for submission), state it as a conjecture, or leave it out. A result already in the literature is
credited to that paper, unless you say it is a different result. A result that others use cannot be left out alone.

Two limits: a result "not found in the papers read" is not thereby new, since only the papers found were compared;
and whether a proof is correct is checked later, by the proof check, not by the assessment.

## What you are asked, and what you are never asked

You are asked about mathematics: what a symbol means, whether a doubtful reading is right, whether a figure is the one
you drew, what kind of paper your notes support, whether to accept a shorter proof or a correction; and at the end,
the title, the introduction and what it cites.

You are never asked about language. Doubts about spelling, grammar, plurals or wording are settled by the app and
corrected in the copy-edit.

## How the mathematics is protected

- The writer may rewrite sentences but not formulas. After writing, every formula of your approved transcript is
  compared, symbol by symbol, with the paper (the **Fidelity** tab). This is a mechanical comparison, not a model's opinion.
- A copy-edit is kept only if it changed wording and nothing else: the same formulas, labels, citations and arrangement.
  Otherwise the passage stays as it was.
- A formula that changed because of a decision you made is listed as "changed with your approval", separately from errors.
- The proof check is strong evidence, not a certificate. It does not replace your own reading.

## Which model does which job

For the jobs that decide the quality of the paper (reading the handwriting, understanding, writing, checking proofs,
refereeing, copy-editing) only the strongest model your keys reach is used, or one equivalent to it. A weaker model
never steps in by itself.

If none of the strongest models is free (a daily limit, an overloaded provider), the step **waits**. The app shows when
it will continue, and restarts by itself at that time from the exact point where it stopped. If you would rather not
wait, a button lets the next best model do that one step; the step is marked, and can be redone later.

With free keys this matters. A paper needs roughly ninety calls to the strongest model, and free daily allowances are
small, so a run can take more than a day, most of it waiting. Your allowances are shown at
https://aistudio.google.com/rate-limit. A paid key, or a second key from a different Google project, removes most of the waiting.

## Stopping and continuing

Everything is saved after every step, and inside the long steps after every page, section or proof. **Continue**
carries on from the exact point; **Start over** discards the results and begins again; **Redo from this step** repeats one
step and what follows it. If a step fails, the reason is shown.

A paper started with an earlier version of the app keeps its reading, confirmed notation and transcript. The paper
itself is written again by the new steps; the one built earlier is kept in the folder `out_before_update`.

## Literature

Papers are found in OpenAlex, Semantic Scholar, arXiv, zbMATH and Crossref, and the most relevant are read (the full
text where it is open, otherwise the abstract). Google Scholar has no public interface for programs and blocks
automated searches, so the app does not query it; instead every paper in the final list has a link that looks it up
on Google Scholar, and there is a link to search Google Scholar for the topic. A paper you find there can be added by
its DOI, arXiv number or title.

## Target journal

Choose the journal when you start, or at any time later (the button next to the paper's name). Journals are looked up
by name in OpenAlex and Crossref, so there is no fixed list.

Requirements come from three places, the later overriding the earlier, and the app shows which is which:

1. what is typical for the publisher (marked as such: it was not checked for the journal);
2. the journal's own guide for authors, which the app reads from a web address or from pasted text and shows you
   before anything is applied;
3. what you set yourself.

The paper is then prepared accordingly: template, abstract length, number of keywords, subject classification,
reference labels and order, anonymous version, line numbers, double spacing, statements. A checklist shows what is met.

Templates built in: `amsart`, the standard `article` class, and Elsevier's `elsarticle`. Other publishers' own classes
(Springer Nature's `sn-jnl`, Taylor & Francis's `interact`, SIAM's `siamart` and so on) are not built in; for those the
paper is set in a standard class with the journal's content requirements, which most journals accept for a first
submission. If a class is not installed on your computer, the PDF is set in the standard class and both sources are
put in the download.

Acknowledgements, funding and other declarations are yours to make. They are never written by a model.

## Output

`main.tex`, `references.bib`, a PDF (if LaTeX is installed: MiKTeX or TeX Live), a web page, and a zip to upload to
Overleaf. Figures are redrawn in TikZ, so drawing them and compiling the PDF both need LaTeX on this computer; without
it your own drawings are used and the Overleaf package still works.

## Files

    app.py, run_app.bat     start the app
    scriptavera/            the program
      pipeline.py           reading steps, pausing and restarting
      writing.py            understanding, assessment, your plan, introduction, structure, first draft
      revision.py           shorter arguments, proof check, referee, your decisions
      final.py              final version, copy-edit, typography, abstract, titles, the last step, files
      journals.py           journal lookup and requirements
      llm.py                model choice, limits, waiting
      paper.py, audit.py    LaTeX and HTML output, the formula comparison
      prompts.py            every instruction given to a model
    projects/               your papers, one folder each
    docs/                   the logo
    LICENSE, LICENSING.md, CLA.md, TRADEMARKS.md, THIRD_PARTY_NOTICES.md   the terms
    tests/                  automated tests; they use simulated models and need no key

    python tests/run_all.py          every test
    python tests/run_all.py --ui     also drives the interface in a browser (needs playwright)

## Limits to know about

- The models can misread handwriting. The two stops for your confirmation and review exist for that reason.
- The proof check and the referee's report come from a language model. They find real slips, and they can also be
  wrong in both directions.
- Requirements marked "typical for the publisher" are not verified for a particular journal. Read the journal's guide.
- Quartile rankings (Q1, Q2, Q3) are not part of the databases used, so journals are not labelled with them.

## Licence

Scriptavera is copyright © 2026 Vaibhav Chhajer and is dual-licensed:

- **Open source:** the GNU Affero General Public License, version 3 only (`LICENSE`). Anyone may use, study and
  change it. Whoever gives copies to others, or lets others use a changed version over a network, must give them the
  source code under the same licence.
- **Commercial:** for use without those conditions (for example in a closed-source product or service), a separate
  paid licence is available. See `LICENSING.md`.

The papers and files Scriptavera produces from your notes are yours, under any terms you choose (`LICENSING.md`,
section 3). Contributions are accepted under the Contributor Licence Agreement in `CLA.md` (see `CONTRIBUTING.md`).
The AGPL grants no rights to the name Scriptavera or its logo (`TRADEMARKS.md`). Third-party parts keep their own
licences (`THIRD_PARTY_NOTICES.md`).
