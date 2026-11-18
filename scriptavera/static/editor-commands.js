/* SPDX-License-Identifier: AGPL-3.0-only
   Copyright (C) 2026 Vaibhav Chhajer */
"use strict";
/* The commands the editor suggests as you type a backslash, each with what it inserts ($1, $2 ... are the places
   Tab moves through; ${1:text} starts with that text selected), a few words on what it does, and where it works:
   "m" only in a formula, "t" only in text (outside a formula), "" both. The most used come first.
   The code check uses the same lists: a command marked "m" used outside a formula, or "t" inside one, stops LaTeX. */

const TEX_COMMANDS = [
  // structure
  ["\\section{$1}", "section heading", "t"], ["\\subsection{$1}", "subsection heading", "t"], ["\\subsubsection{$1}", "subsubsection heading", "t"],
  ["\\section*{$1}", "unnumbered section", "t"], ["\\subsection*{$1}", "unnumbered subsection", "t"], ["\\paragraph{$1}", "run-in paragraph heading", "t"],
  ["\\chapter{$1}", "chapter (book, report)", "t"], ["\\part{$1}", "part", "t"], ["\\appendix", "start the appendices", "t"],
  ["\\begin{$1}", "begin an environment", ""], ["\\end{$1}", "end an environment", ""],
  ["\\label{$1}", "label for \\ref", ""], ["\\ref{$1}", "number of a label", ""], ["\\eqref{$1}", "equation number in parentheses", ""],
  ["\\cref{$1}", "reference with its name (cleveref)", ""], ["\\Cref{$1}", "reference with its name, capitalised", ""],
  ["\\autoref{$1}", "reference with its name (hyperref)", ""], ["\\pageref{$1}", "page of a label", ""], ["\\nameref{$1}", "title of a labelled section", ""],
  ["\\cite{$1}", "citation", ""], ["\\citep{$1}", "citation in parentheses (natbib)", ""], ["\\citet{$1}", "citation in the text (natbib)", ""],
  ["\\cite[${1:p.~1}]{$2}", "citation with a page", ""], ["\\nocite{$1}", "list in the references without citing", ""],
  ["\\footnote{$1}", "footnote", ""], ["\\emph{$1}", "emphasis (italic)", ""], ["\\textbf{$1}", "bold", ""], ["\\textit{$1}", "italic", ""],
  ["\\texttt{$1}", "typewriter", ""], ["\\textsc{$1}", "small capitals", ""], ["\\textrm{$1}", "upright roman", ""], ["\\textsf{$1}", "sans serif", ""],
  ["\\underline{$1}", "underline", ""], ["\\text{$1}", "text inside a formula", ""], ["\\mbox{$1}", "unbreakable box", ""],
  ["\\item ", "list item", "t"], ["\\item[$1] ", "list item with its own label", "t"],
  ["\\includegraphics[width=0.8\\linewidth]{$1}", "image", ""], ["\\caption{$1}", "caption of a figure or table", "t"],
  ["\\centering", "centre the rest of the environment", ""], ["\\input{$1}", "insert another .tex file", ""], ["\\include{$1}", "insert a chapter file on a new page", "t"],
  ["\\usepackage{$1}", "load a package", "t"], ["\\usepackage[$1]{$2}", "load a package with options", "t"], ["\\documentclass{$1}", "the kind of document", "t"],
  ["\\documentclass[$1]{$2}", "the kind of document, with options", "t"],
  ["\\title{$1}", "title", "t"], ["\\author{$1}", "author", "t"], ["\\date{$1}", "date", "t"], ["\\maketitle", "print the title", "t"],
  ["\\address{$1}", "author's address (amsart)", "t"], ["\\email{$1}", "author's email (amsart)", "t"], ["\\thanks{$1}", "footnote to the title or author", "t"],
  ["\\keywords{$1}", "key words (amsart)", "t"], ["\\subjclass[2020]{$1}", "subject classification (amsart)", "t"],
  ["\\tableofcontents", "table of contents", "t"], ["\\bibliography{$1}", "the BibTeX file(s)", "t"], ["\\bibliographystyle{$1}", "style of the references", "t"],
  ["\\printbibliography", "references (biblatex)", "t"], ["\\addbibresource{$1}", "the .bib file (biblatex)", "t"], ["\\bibitem{$1}", "entry of thebibliography", "t"],
  ["\\newcommand{\\$1}{$2}", "define a command", ""], ["\\newcommand{\\$1}[${2:1}]{$3}", "define a command with arguments", ""],
  ["\\renewcommand{\\$1}{$2}", "redefine a command", ""], ["\\providecommand{\\$1}{$2}", "define a command unless it exists", ""],
  ["\\DeclareMathOperator{\\$1}{$2}", "define an operator name like \\sin", "t"], ["\\newtheorem{$1}{$2}", "define a theorem-like environment", "t"],
  ["\\newtheorem{$1}[$2]{$3}", "theorem-like environment sharing a counter", "t"], ["\\theoremstyle{${1:plain}}", "style of the next theorems", "t"],
  ["\\newenvironment{$1}{$2}{$3}", "define an environment", "t"], ["\\setlength{$1}{$2}", "set a length", ""],
  ["\\url{$1}", "web address", ""], ["\\href{$1}{$2}", "link with its text", ""], ["\\hypersetup{$1}", "hyperref settings", "t"],
  ["\\newpage", "new page", "t"], ["\\clearpage", "new page, after placing the figures", "t"], ["\\noindent", "no indent for this paragraph", ""],
  ["\\vspace{$1}", "vertical space", ""], ["\\hspace{$1}", "horizontal space", ""], ["\\medskip", "medium vertical space", "t"],
  ["\\smallskip", "small vertical space", "t"], ["\\bigskip", "big vertical space", "t"], ["\\par", "end the paragraph", "t"],
  ["\\linewidth", "width of a line", ""], ["\\textwidth", "width of the text", ""], ["\\hfill", "fill the line", ""], ["\\vfill", "fill the page", "t"],
  ["\\quad", "space of 1em", ""], ["\\qquad", "space of 2em", ""], ["\\ldots", "dots …", ""], ["\\dots", "dots, placed by the context", ""],
  ["\\today", "today's date", ""], ["\\LaTeX", "the LaTeX logo", ""], ["\\TeX", "the TeX logo", ""], ["\\qedhere", "put the end-of-proof mark here", ""],
  ["\\hline", "rule in a table", ""], ["\\toprule", "top rule (booktabs)", ""], ["\\midrule", "middle rule (booktabs)", ""], ["\\bottomrule", "bottom rule (booktabs)", ""],
  ["\\multicolumn{${1:2}}{${2:c}}{$3}", "cell over several columns", ""], ["\\cline{$1}", "partial rule in a table", ""],
  ["\\textcolor{${1:red}}{$2}", "coloured text (xcolor)", ""], ["\\color{$1}", "change the colour (xcolor)", ""],
  ["\\tikz", "a small TikZ picture", ""], ["\\draw", "TikZ: draw", "t"], ["\\node", "TikZ: node", "t"],
  ["\\verb|$1|", "verbatim text", "t"], ["\\protect", "protect a fragile command", ""], ["\\nonumber", "no number on this line", ""], ["\\notag", "no number on this line", ""],
  ["\\tag{$1}", "your own equation number", "m"], ["\\intertext{$1}", "text between lines of an align", "m"], ["\\shortintertext{$1}", "short text between lines (mathtools)", "m"],
  // fractions, roots, sums
  ["\\frac{$1}{$2}", "fraction", "m"], ["\\dfrac{$1}{$2}", "fraction, display size", "m"], ["\\tfrac{$1}{$2}", "fraction, text size", "m"],
  ["\\sqrt{$1}", "square root", "m"], ["\\sqrt[$1]{$2}", "n-th root", "m"], ["\\binom{$1}{$2}", "binomial coefficient", "m"],
  ["\\sum_{$1}^{$2}", "sum", "m"], ["\\prod_{$1}^{$2}", "product", "m"], ["\\int_{$1}^{$2}", "integral", "m"], ["\\iint", "double integral", "m"],
  ["\\iiint", "triple integral", "m"], ["\\oint", "contour integral", "m"], ["\\lim_{$1}", "limit", "m"], ["\\limsup", "upper limit", "m"], ["\\liminf", "lower limit", "m"],
  ["\\sup", "supremum", "m"], ["\\inf", "infimum", "m"], ["\\max", "maximum", "m"], ["\\min", "minimum", "m"], ["\\bigcup", "big union", "m"],
  ["\\bigcap", "big intersection", "m"], ["\\bigoplus", "direct sum", "m"], ["\\bigotimes", "big tensor product", "m"], ["\\coprod", "coproduct", "m"],
  // Greek
  ...["alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta", "theta", "vartheta", "iota", "kappa", "lambda", "mu", "nu", "xi",
    "pi", "varpi", "rho", "varrho", "sigma", "varsigma", "tau", "upsilon", "phi", "varphi", "chi", "psi", "omega",
    "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega"].map((g) => [`\\${g}`, `Greek ${g}`, "m"]),
  // relations
  ["\\le", "≤", "m"], ["\\ge", "≥", "m"], ["\\leq", "≤", "m"], ["\\geq", "≥", "m"], ["\\neq", "≠", "m"], ["\\ne", "≠", "m"], ["\\approx", "≈", "m"],
  ["\\sim", "∼", "m"], ["\\simeq", "≃", "m"], ["\\cong", "≅", "m"], ["\\equiv", "≡", "m"], ["\\propto", "∝", "m"], ["\\ll", "≪", "m"], ["\\gg", "≫", "m"],
  ["\\leqslant", "⩽ (amssymb)", "m"], ["\\geqslant", "⩾ (amssymb)", "m"], ["\\lesssim", "≲ (amssymb)", "m"], ["\\gtrsim", "≳ (amssymb)", "m"],
  ["\\in", "∈", "m"], ["\\notin", "∉", "m"], ["\\ni", "∋", "m"], ["\\subset", "⊂", "m"], ["\\subseteq", "⊆", "m"], ["\\supset", "⊃", "m"],
  ["\\supseteq", "⊇", "m"], ["\\subsetneq", "⊊ (amssymb)", "m"], ["\\mid", "∣ divides, such that", "m"], ["\\nmid", "∤ (amssymb)", "m"],
  ["\\parallel", "∥", "m"], ["\\perp", "⊥", "m"], ["\\models", "⊨", "m"], ["\\vdash", "⊢", "m"], ["\\prec", "≺", "m"], ["\\succ", "≻", "m"],
  ["\\preceq", "⪯", "m"], ["\\succeq", "⪰", "m"], ["\\coloneqq", "≔ (mathtools)", "m"], ["\\doteq", "≐", "m"],
  // operators
  ["\\cdot", "· product", "m"], ["\\times", "× product", "m"], ["\\div", "÷", "m"], ["\\pm", "±", "m"], ["\\mp", "∓", "m"], ["\\ast", "∗", "m"],
  ["\\star", "⋆", "m"], ["\\circ", "∘ composition", "m"], ["\\bullet", "•", "m"], ["\\oplus", "⊕", "m"], ["\\otimes", "⊗", "m"], ["\\odot", "⊙", "m"],
  ["\\cup", "∪ union", "m"], ["\\cap", "∩ intersection", "m"], ["\\setminus", "∖ set difference", "m"], ["\\wedge", "∧", "m"], ["\\vee", "∨", "m"],
  ["\\land", "∧ and", "m"], ["\\lor", "∨ or", "m"], ["\\lnot", "¬ not", "m"], ["\\neg", "¬", "m"], ["\\nabla", "∇", "m"], ["\\partial", "∂", "m"],
  ["\\infty", "∞", "m"], ["\\emptyset", "∅", "m"], ["\\varnothing", "∅ (amssymb)", "m"], ["\\forall", "∀", "m"], ["\\exists", "∃", "m"],
  ["\\nexists", "∄ (amssymb)", "m"], ["\\aleph", "ℵ", "m"], ["\\hbar", "ℏ", "m"], ["\\ell", "ℓ", "m"], ["\\Re", "ℜ", "m"], ["\\Im", "ℑ", "m"],
  ["\\wp", "℘", "m"], ["\\angle", "∠", "m"], ["\\triangle", "△", "m"], ["\\prime", "′", "m"], ["\\cdots", "⋯ centred dots", "m"],
  ["\\vdots", "⋮ vertical dots", ""], ["\\ddots", "⋱ diagonal dots", "m"], ["\\colon", ": in f\\colon A\\to B", "m"],
  // arrows
  ["\\to", "→", "m"], ["\\rightarrow", "→", "m"], ["\\leftarrow", "←", "m"], ["\\gets", "←", "m"], ["\\leftrightarrow", "↔", "m"],
  ["\\Rightarrow", "⇒", "m"], ["\\Leftarrow", "⇐", "m"], ["\\Leftrightarrow", "⇔", "m"], ["\\implies", "⟹", "m"], ["\\impliedby", "⟸", "m"],
  ["\\iff", "⟺", "m"], ["\\mapsto", "↦", "m"], ["\\longmapsto", "⟼", "m"], ["\\longrightarrow", "⟶", "m"], ["\\longleftarrow", "⟵", "m"],
  ["\\hookrightarrow", "↪", "m"], ["\\twoheadrightarrow", "↠ (amssymb)", "m"], ["\\uparrow", "↑", "m"], ["\\downarrow", "↓", "m"],
  ["\\nearrow", "↗", "m"], ["\\searrow", "↘", "m"], ["\\rightharpoonup", "⇀", "m"], ["\\xrightarrow{$1}", "arrow with text above", "m"],
  ["\\xleftarrow{$1}", "left arrow with text above", "m"],
  // functions
  ...["sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "log", "ln", "exp", "det", "dim", "ker",
    "deg", "gcd", "arg", "hom", "Pr"].map((f) => [`\\${f}`, `${f} (upright)`, "m"]),
  ["\\operatorname{$1}", "upright operator name", "m"], ["\\pmod{$1}", "(mod n)", "m"], ["\\bmod", "mod", "m"],
  // accents, fonts
  ["\\hat{$1}", "hat accent", "m"], ["\\widehat{$1}", "wide hat", "m"], ["\\bar{$1}", "bar accent", "m"], ["\\overline{$1}", "line over", "m"],
  ["\\tilde{$1}", "tilde accent", "m"], ["\\widetilde{$1}", "wide tilde", "m"], ["\\vec{$1}", "vector arrow", "m"], ["\\dot{$1}", "dot accent", "m"],
  ["\\ddot{$1}", "double dot", "m"], ["\\check{$1}", "check accent", "m"], ["\\breve{$1}", "breve accent", "m"], ["\\acute{$1}", "acute accent", "m"],
  ["\\grave{$1}", "grave accent", "m"], ["\\underbrace{$1}_{$2}", "brace below with a note", "m"], ["\\overbrace{$1}^{$2}", "brace above with a note", "m"],
  ["\\overset{$1}{$2}", "symbol over another", "m"], ["\\underset{$1}{$2}", "symbol under another", "m"], ["\\stackrel{$1}{$2}", "symbol over a relation", "m"],
  ["\\mathbb{$1}", "blackboard bold ℝ (amssymb)", "m"], ["\\mathcal{$1}", "calligraphic 𝒜", "m"], ["\\mathfrak{$1}", "Fraktur 𝔄 (amssymb)", "m"],
  ["\\mathscr{$1}", "script (mathrsfs)", "m"], ["\\mathbf{$1}", "bold in a formula", "m"], ["\\boldsymbol{$1}", "bold symbol (amsmath)", "m"],
  ["\\bm{$1}", "bold symbol (bm)", ""], ["\\mathrm{$1}", "upright in a formula", "m"], ["\\mathit{$1}", "italic in a formula", "m"],
  ["\\mathsf{$1}", "sans serif in a formula", "m"], ["\\mathtt{$1}", "typewriter in a formula", "m"], ["\\displaystyle", "display size", "m"],
  ["\\textstyle", "text size", "m"], ["\\scriptstyle", "script size", "m"], ["\\limits", "limits above and below", "m"], ["\\nolimits", "limits at the side", "m"],
  // delimiters
  ["\\left($1\\right)", "parentheses that grow", "m"], ["\\left[$1\\right]", "brackets that grow", "m"], ["\\left\\{$1\\right\\}", "braces that grow", "m"],
  ["\\left|$1\\right|", "bars that grow", "m"], ["\\left\\langle $1\\right\\rangle", "angle brackets that grow", "m"], ["\\left", "growing left delimiter", "m"],
  ["\\right", "growing right delimiter", "m"], ["\\middle", "growing middle delimiter", "m"], ["\\langle", "⟨", "m"], ["\\rangle", "⟩", "m"],
  ["\\lfloor", "⌊", "m"], ["\\rfloor", "⌋", "m"], ["\\lceil", "⌈", "m"], ["\\rceil", "⌉", "m"], ["\\lvert", "| left", "m"], ["\\rvert", "| right", "m"],
  ["\\lVert", "‖ left", "m"], ["\\rVert", "‖ right", "m"], ["\\|", "‖", "m"], ["\\big", "bigger delimiter", "m"], ["\\Big", "bigger delimiter", "m"],
  ["\\bigg", "bigger delimiter", "m"], ["\\Bigg", "biggest delimiter", "m"], ["\\bigl(", "bigger (", "m"], ["\\bigr)", "bigger )", "m"],
  ["\\Bigl(", "bigger (", "m"], ["\\Bigr)", "bigger )", "m"],
  // spacing in formulas
  ["\\,", "thin space", ""], ["\\;", "thick space", ""], ["\\:", "medium space", ""], ["\\!", "negative thin space", ""],
];

/* environments \begin{ suggests, with what goes inside */
const TEX_ENVS = {
  itemize: "\n  \\item $1\n", enumerate: "\n  \\item $1\n", description: "\n  \\item[$1] $2\n",
  equation: "\n  $1\n", "equation*": "\n  $1\n", align: "\n  $1\n", "align*": "\n  $1\n", gather: "\n  $1\n", "gather*": "\n  $1\n",
  multline: "\n  $1\n", "multline*": "\n  $1\n", split: "\n  $1\n", aligned: "\n  $1\n", cases: "\n  $1 & $2 \\\\\n  $3 & $4\n",
  pmatrix: "\n  $1\n", bmatrix: "\n  $1\n", vmatrix: "\n  $1\n", matrix: "\n  $1\n", array: "{${1:cc}}\n  $2\n",
  figure: "[htbp]\n  \\centering\n  \\includegraphics[width=0.8\\linewidth]{$1}\n  \\caption{$2}\n  \\label{fig:$3}\n",
  table: "[htbp]\n  \\centering\n  \\caption{$1}\n  \\label{tab:$2}\n  \\begin{tabular}{${3:cc}}\n    $4\n  \\end{tabular}\n",
  tabular: "{${1:cc}}\n  $2\n", center: "\n  $1\n", abstract: "\n  $1\n", proof: "\n  $1\n", quote: "\n  $1\n", quotation: "\n  $1\n",
  verbatim: "\n$1\n", minipage: "{${1:0.45\\linewidth}}\n  $2\n", tikzpicture: "\n  $1\n", document: "\n$1\n", thebibliography: "{99}\n  \\bibitem{$1} $2\n",
  theorem: "\n  $1\n", lemma: "\n  $1\n", proposition: "\n  $1\n", corollary: "\n  $1\n", definition: "\n  $1\n", remark: "\n  $1\n", example: "\n  $1\n",
};

/* packages \usepackage{ suggests (the ones a paper in mathematics most often needs), and document classes */
const TEX_PACKAGES = ["amsmath", "amssymb", "amsthm", "amsfonts", "mathtools", "graphicx", "hyperref", "cleveref", "xcolor", "geometry", "enumitem",
  "booktabs", "tikz", "pgfplots", "tikz-cd", "biblatex", "natbib", "babel", "fontenc", "inputenc", "lmodern", "microtype", "caption", "subcaption",
  "float", "array", "multirow", "longtable", "siunitx", "bm", "mathrsfs", "dsfont", "bbm", "stmaryrd", "thmtools", "thm-restate", "fancyhdr",
  "setspace", "parskip", "url", "listings", "algorithm", "algpseudocode", "algorithmic", "csquotes", "todonotes", "comment", "xspace",
  "esint", "cancel", "physics", "braket", "nicefrac", "upgreek", "accents", "relsize", "titlesec", "tocloft", "appendix", "wrapfig", "pdfpages", "lipsum"];
const TEX_CLASSES = ["amsart", "article", "amsbook", "amsproc", "book", "report", "revtex4-2", "elsarticle", "svjour3", "llncs", "IEEEtran", "acmart",
  "beamer", "memoir", "scrartcl", "standalone"];
const TEX_BIBSTYLES = ["amsplain", "amsalpha", "plain", "alpha", "abbrv", "unsrt", "siam", "ieeetr", "acm", "apalike", "plainnat", "abbrvnat", "unsrtnat"];
