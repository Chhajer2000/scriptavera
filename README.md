<p align="center"><img src="docs/logo.png" alt="Scriptavera" width="360"></p>

# Scriptavera

*Scripta vera*, "true writings": Scriptavera turns handwritten research notes (a scanned PDF or photos) into a
research paper in LaTeX without changing what you wrote. The first field it supports is mathematics, the hardest one
to get exactly right; you choose the area first, and nothing in the app is specific to one topic or one paper.

The work is split into twenty small steps. Each AI call has one job, every step saves its result, and the app stops
up to six times to ask you what only you can decide.

When you start, you give the area, the problem you are working on in your own words, and keywords (both required),
and optionally the one or two papers your work relies on most. These are looked up first and read first.

**Website:** https://scriptavera.com  
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

Your notes are sent to the provider whose model reads them, under that provider's terms. Check them for unpublished
work: Google's Gemini API terms, for example, let Google use what is sent with a free key to improve its products and
let people review it, while prompts sent with a key from a Google Cloud project that has billing turned on are not
used that way.

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
7. *Final transcription.* Every page read again with the confirmed notation and the research context. A reading that
   breaks down (one command repeated endlessly, or parts of the model's answer left in it) is asked for once more.
8. *Figures.* Each drawing is judged from the text around it (see below). A figure the paper's reader needs is
   redrawn as a proper figure: its ink is measured (the pen strokes of a PDF, or the scanned image), straightened
   and aligned, and its labels are typeset at the size you wrote them; a model only reads the labels and checks the
   redrawing against your drawing, and anything that does not match is shown to you. A drawing that only explains
   your argument, as most drawings in proofs and proof sketches do, is not printed and not redrawn, but the writers
   still read it. Labels you wrote on a formula with arrows or braces are not figures: they are written into the
   formula as braces with the label under the term.
9. **Your review.** You check the transcript against the page images and say, for each figure for the paper, whether
   the redrawn version is what you drew (or ask for it to be drawn again, use your own drawing, keep it out of the
   paper, or leave it out). A drawing judged to be only an explanation is settled without a question: you see why,
   and one button prints it after all. If something taken for a figure is only your note on a formula, one button
   writes it into the text; the formula itself is checked to be unchanged.

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
    what that upholds is reported. A proof your notes only sketch is completed by its own steps (see below).
17. *Referee's read.* The whole paper is read the way a journal referee reads a submission.
18. **Your decisions.** Anything that touches the mathematics (a shortened proof, a completed sketch, a correction,
    a remark) is done only if you say so. If the three readings find nothing of that kind, this step is passed by itself.

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

## Drawings: for the paper, or only an explanation

A drawing in a proof or a proof sketch is usually your aid to see a step, and the proof is complete without it. A
drawing that shows something the text relies on (a construction, points or regions named only in the drawing, data)
belongs in the paper. The app decides which is which from the text around each drawing, not from the drawing
itself, so the decision does not depend on how well a model reads a sketch:

- **Where it sits.** The code finds the labelled part of your notes each drawing stands in (a proof, a sketch or
  "Idea:", a definition, an example, the statement of the problem), the passage around it, and every sentence of the
  notes that mentions a drawing ("see Figure 2", "as in the picture").
- **One reading of all the drawings** then decides each one: for the paper, only an explanation, or your own labels on
  a formula, with the reason, and whether that is sure. The question it answers: if the drawing were left out, would
  the text still say everything the argument needs?
- **The code holds each verdict to its reasons.** An "explanation" that the text relies on, one you numbered, or a
  "figure for the paper" inside a proof that nothing relies on, is not sure, and you are asked. So is anything the
  reading could not decide.
- **A sure explanation** is not redrawn and not printed. It stays in the notes the writers read, marked as not
  printed, so that they understand the proof; where the notes say "see the picture", the writer is told to write the
  sentence without the reference. If the writer prints the drawing anyway, the code removes it.
- **A step that the notes give only by a drawing** counts as sketched when the notes are assessed, and the
  completion of that proof (below) is written and checked with your drawings for it in view: it may put into words
  what a drawing shows, and nothing more.

## Completing a sketched proof

When the assessment finds that a proof is only sketched, the proof check offers a completed proof that follows your
sketch. It may only justify the steps you wrote; it may not change the statement, reorder or reword your sketch, or
bring in an idea of its own.

- **Your words stay.** The sketch is divided into its steps, and each step must be an exact quotation of your sketch,
  found by code; a step that is not quoted exactly, or out of order, is refused. Each addition is inserted after its
  step, at the end of a sentence and never inside a formula, and the code checks that removing the additions gives
  back your sketch character for character.
- **What the code refuses.** An addition may not state a new lemma or theorem, use a result that comes later in the
  paper, cite a paper the paper does not cite, or leave a formula unbalanced. A symbol that appears without being
  introduced is listed for the faithfulness check.
- **Computation.** Where an addition contains a calculation (an identity, an integral, an inequality between
  expressions), it is redone exactly by computer algebra (when SymPy is installed), and a counterexample sinks it.
- **A separate reading for faithfulness.** A separate call, which sees only the earlier results, the statement, your
  steps, your drawings for this proof and the additions (not the writer's instructions or reasoning), judges each
  addition: *faithful* (it justifies its step), *new idea* (it brings in an argument the sketch does not contain) or
  *wrong*; and whether the whole proves the statement as stated.
- **The proof check.** The completed proof then goes through the same proof check as every other proof, with a
  second opinion.
- If any of these fails, the completion is written once more with the reasons. If it fails again, nothing is
  offered; you get a remark saying which step could not be completed and why. When the writer finds a step that
  cannot be justified as written (for example, it needs an assumption the statement does not have), it says so instead
  of filling it, and that also becomes a remark.

You then see your sketch, the steps it was divided into, and the completed proof with every addition marked, and you
choose: replace the sketch with it, edit it and then replace, ask for changes (with a note; it is written again), or
keep your sketch.

## Which model does which job

**First, each key is checked.** When the app starts, it sends one tiny request with each Gemini key to a model that
only Google's paid tier serves. A free key is refused ("limit: 0"), so for that key Pro and Argon are set aside before
any work is given to it, and its jobs go straight to the best model it can use. Settings shows each key with its tier.

**How models are ranked.** Each job has a score for every model your keys reach. Gemini models are ranked by tier
first, then by version: Gemini 4 Argon (Google's flagship), then Pro, then Flash, then Flash-Lite, and within a tier
the newer version first. So 3.1 Pro comes before 3.8 Flash, and 3.8 Flash before 3.6 Flash.

**The jobs that decide the quality of the paper** (reading the handwriting, the figures, understanding, writing,
checking proofs, refereeing, copy-editing) use the best model on one key after the other: when the first key reaches
its limit for that model, the same model continues on the second key. When every key has reached it, the next best
model takes over, again key after key, but only a model almost as good: the same tier and a version at most 0.25
lower. With free keys that is 3.8 Flash, then 3.7 Flash, then 3.6 Flash. A weaker model (an older Flash, Flash-Lite, or
another provider's smaller model) is never used for these jobs. If none of them is free, the step **waits**, shows
when it will continue, and restarts by itself at that time from the exact point where it stopped. The small tasks (the
first look at the pages, search queries, scoring) use fast models, and move only to a model at least as capable.

**Why one model does several jobs.** Each job chooses its own model. A free key opens only Flash and Flash-Lite, and
3.8 Flash is the strongest of these at reading handwriting and at mathematical reasoning alike, so it is chosen for
every job above; with a paid key, Argon would be. Each model has its own daily allowance, so when 3.8 Flash has used
its allowance on every key, 3.7 Flash and then 3.6 Flash carry on.

Several keys for one provider are written in `.env` on one line, separated by commas
(`GEMINI_API_KEY=first,second`). Google counts limits per project, so a second key helps only if it comes from a
different Google Cloud project; it reaches Pro or Argon only if that project has billing enabled (Argon costs $2 per
million input and $10 per million output tokens until 30 November 2026, then $4 and $20).

With free keys the waiting matters. A paper needs roughly ninety calls to the strongest model, and free daily
allowances are small, so a run can take more than a day, most of it waiting. Your allowances are shown at
https://aistudio.google.com/rate-limit. A paid key, or a second key from a different Google project, removes most of
the waiting.

## Stopping and continuing

Everything is saved after every step, and inside the long steps after every page, section or proof. **Continue**
carries on from the exact point; **Start over** discards the results and begins again; **Redo from this step** repeats one
step and what follows it. If a step fails, the reason is shown.

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

## The LaTeX editor

When the paper is written, **Go to editor** opens it as a LaTeX project, the way Overleaf shows one: `main.tex` with
the preamble, the title and the abstract, one file per section in `sections/`, the references in `bibliography.tex`
and `references.bib`, and the figures. From then on these files are your copy. Scriptavera never rewrites them by
itself; if it builds the paper again (after a change of title, say), the editor tells you and offers to start again
from the new version, keeping yours in the history.

- **Files and editing**: a file list (new file, folder, upload, rename, delete), tabs, LaTeX highlighting, completion
  of `\ref`, `\cite` and environment names, search and replace, undo per file.
- **Compiling**: with the LaTeX installed on this computer, as often as references need (BibTeX or Biber when the
  project uses them), automatically while you type if you like. The PDF is shown beside the source; every error and
  warning names its file and line, and the AI can explain an error and propose the fix.
- **SyncTeX**: from a line of the source to its place in the PDF, and from a place in the PDF (double-click) to the line.
- **Outline**: every section, definition, lemma, theorem and equation with the number LaTeX printed for it.
- **History**: a version every few minutes while you type, one before and after every change you accept from the AI,
  and versions you save with a name. Any version can be compared with now and brought back.
- **Download**: the project as a zip that uploads to Overleaf as it is.

**Commands in plain words.** Type what should change: "remove Definition 2.1", "move Lemma 3.2 before Theorem 4.2",
"make Lemma 3.2 a proposition", "rewrite the proof of Theorem 4.2 in more detail". Every command ends as a proposal:
the changes, file by file, which you accept in whole or in part, or discard. Nothing is written before that.

- Commands that only rearrange the paper (remove, move, change the kind of a statement, go to) are carried out by
  code, exactly and without a model. Numbers are those of the PDF; "Definition 1" in a paper whose definitions are
  numbered 2.1, 2.2, … is read as the first definition, and the editor says so and offers the other readings (such as
  the definition labelled `def:D1`, the numbering of your notes).
- Everything else goes to the strongest writing model, which answers with operations on numbered items or exact text
  replacements, never a retyped paper; the code applies them, and asks the model once more if an answer cannot be
  placed exactly.
- LaTeX renumbers by itself: the statements after a removed one move up and every `\ref` follows. What LaTeX cannot
  fix is checked and said plainly: a `\ref` to something that no longer exists (it would print "??"); a number typed
  by hand ("Section~2"), which is corrected in the same proposal; a definition removed while the symbol it defines is
  still used (with how often and where); a statement that now comes after a place that uses it; a formula changed
  outside the part the command named, or inside a part rewritten by the model; and LaTeX left unbalanced.

The editor is for one author on one computer: there is no simultaneous editing with co-authors, no comments and no
Git or Dropbox synchronisation. For those, download the project and continue in Overleaf.

## A record of your notes

As soon as notes are uploaded, before anything else is done with them:

1. the original files are kept as they came, byte for byte (in the paper's folder, `originals/`);
2. each file's SHA-256 fingerprint goes into a short record, with the time of this computer and the particulars arXiv
   takes with a submission: every author (name, affiliation, e-mail, ORCID iD if given), who makes the record and
   whether they are an author, a declaration in their name ("I, …, am an author of these notes. The other authors
   listed agree to this record. The details in this record are correct …", which the form shows word for word before
   you tick it), what the notes are about (the problem you describe), the subject classes (arXiv category and MSC
   codes) and the keywords, and a statement that the copyright belongs to the authors;
3. the record is signed with your own key (Ed25519), made on this computer and kept in the folder `identity`;
4. the fingerprint of the record, 32 bytes from which nothing of the notes can be read, is sent to independent
   timestamping services: RFC 3161 time-stamp authorities (FreeTSA and DigiCert by default), which answer at once
   with a signed time, and OpenTimestamps, which anchors it in the Bitcoin blockchain within hours. A service that
   cannot be reached is asked again later, by itself.

The chip at the top of a paper shows the state of its record. It opens the details, the **certificate** (what was
recorded, when, by whom, and how to check it) and the **proof**, a zip with the record, its signature, the
authorities' answers and the Bitcoin proof, with or without the notes. Anyone can check it without Scriptavera, with
`sha256sum`, `openssl ts -verify` and `ots verify`; the app can check it too, from the proof and the files. In the
editor, **Record this version** does the same for a version of the paper.

What this proves: that whoever holds these exact files had them no later than the time stamped, and that your key
signed the record and the declaration in it. The declaration is your own statement, as an arXiv submitter's is in
arXiv's submission agreement: arXiv does not check authorship either, and what an arXiv record adds beyond this one is
that arXiv verified an e-mail address, holds the file itself and shows the record publicly (see "How this compares
with arXiv" below). What it does not prove by itself: who first had the ideas (a record made from someone else's notes
would only be later), or anything about a file that differs by one byte. Copyright protects how something is written,
never mathematical ideas as such; priority of ideas is settled by evidence like this, and by publication. To tie the
key to you, publish its fingerprint (shown in Settings) where people know it is yours, such as your ORCID record. How
electronic evidence is presented in a dispute depends on where you are (in India, for example, section 63 of the
Bharatiya Sakshya Adhiniyam, 2023 asks for a certificate that includes the record's hash value); ask a lawyer.

Keep a copy of the folder `identity` somewhere safe: records already made stay verifiable without it, but new records
can be signed with the same key only with it. Timestamping can be turned off in Settings; the signed record is made
either way. For a paper that has no record yet, the chip offers to make one from the page images Scriptavera kept;
such a record says that it was made later.

### How this compares with arXiv

| | arXiv | The record of your notes |
|---|---|---|
| Who wrote it | the authors as entered; the submitter states in the submission agreement that they are an author | the same particulars and a declaration in your name, signed |
| Who the submitter is | an account with an e-mail address arXiv verified, and endorsement for new authors | the name you give and your key; tie the key to you by publishing its fingerprint (ORCID, university page) |
| When | the time the submitter pressed "Submit", on arXiv's own clock | independent time-stamp authorities and the Bitcoin blockchain, which no single party can backdate |
| The document itself | kept by arXiv, for good | kept by you (the record holds its fingerprint); keep the originals and the proof together |
| Who can see it | everyone, after announcement; it cannot be removed | only you, until you show it: the notes stay private |

Neither proves authorship by itself: both are dated evidence of who claimed what. To make the claim stronger where it
matters, the author can add what this record cannot do from one computer: a signature with a Digital Signature
Certificate from a licensed Certifying Authority (identity checked by that authority), a deposit with a public archive
such as Zenodo, whose record page is public while the files can stay restricted, registration of the notes as an
unpublished literary work with the Copyright Office (in India, an entry in the Register of Copyrights is prima facie
evidence of its particulars), and, when the paper is ready, arXiv itself.

## Files

    app.py, run_app.bat     start the app
    scriptavera/            the program
      pipeline.py           reading steps, pausing and restarting
      writing.py            understanding, assessment, your plan, introduction, structure, first draft
      revision.py           shorter arguments, proof check, referee, your decisions
      completion.py         completing a sketched proof by its own steps
      drawings.py           which drawings are for the paper and which only explain an argument
      final.py              final version, copy-edit, typography, abstract, titles, the last step, files
      journals.py           journal lookup and requirements
      llm.py                model choice, limits, waiting
      paper.py, audit.py    LaTeX and HTML output, the formula comparison
      workspace.py          the LaTeX editor's project: files, history, compiling, the numbers LaTeX printed
      assist.py             commands in plain words: by rule or by the AI, checked, as proposals
      synctex.py            from the source to the PDF and back
      provenance.py         the signed record of the notes, its certificate and its checking
      timestamps.py         RFC 3161 and OpenTimestamps, without extra libraries
      prompts.py            every instruction given to a model
      static/               the interface (app.js; the editor: editor.html, editor.js, editor.css)
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
- The editor's commands check what can be checked mechanically (references, numbers typed by hand, definitions still
  in use, the order of statements, formulas). Whether a rearranged argument still holds is for you to read.
- The record of your notes is evidence of when files existed and who signed them, not a certificate of authorship.

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

## How to cite Scriptavera

If you used Scriptavera in preparing a paper, a talk or a thesis, please cite it, as you would cite GAP or SageMath:

> V. Chhajer, *Scriptavera: faithful conversion of handwritten research notes into papers*, Software, version 1.0
> (2026), https://scriptavera.com.

```bibtex
@misc{scriptavera,
  author       = {Chhajer, Vaibhav},
  title        = {{Scriptavera: faithful conversion of handwritten research notes into papers}},
  year         = {2026},
  note         = {Software, version 1.0},
  howpublished = {\url{https://scriptavera.com}},
  url          = {https://scriptavera.com}
}
```

Every paper Scriptavera makes ends, by default, with a short statement of what Scriptavera did for it (the
declaration of AI-assisted tools that most journals require; for Elsevier journals under the heading they ask for),
citing this entry. The author can remove both under Journal, Your statements. `CITATION.cff` holds the same
information for GitHub's "Cite this repository" button and for Zenodo.
