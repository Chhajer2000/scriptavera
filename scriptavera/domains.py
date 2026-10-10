# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Catalogue of mathematical domains.

The user picks one to three of these before anything else happens. The choice
limits the literature search (arXiv categories, MSC classes) and is given to
every model as context.
"""

from __future__ import annotations

# id = arXiv category. msc = 2-digit (or finer) MSC2020 prefixes.
DOMAINS: list[dict] = [
    # Algebra
    {"id": "math.GR", "group": "Algebra", "name": "Group Theory", "msc": ["20"],
     "blurb": "Finite and infinite groups, subgroup structure, permutation groups"},
    {"id": "math.RA", "group": "Algebra", "name": "Rings and Algebras", "msc": ["16", "17", "15", "08"],
     "blurb": "Associative and non-associative rings, linear algebra, universal algebra"},
    {"id": "math.AC", "group": "Algebra", "name": "Commutative Algebra", "msc": ["13"],
     "blurb": "Commutative rings, modules, ideals, homological methods"},
    {"id": "math.RT", "group": "Algebra", "name": "Representation Theory", "msc": ["20C", "20G", "22E", "16G", "17B"],
     "blurb": "Representations of groups and algebras, Lie theory, characters"},
    {"id": "math.CT", "group": "Algebra", "name": "Category Theory", "msc": ["18"],
     "blurb": "Categories, functors, homological algebra, higher categories"},
    {"id": "math.KT", "group": "Algebra", "name": "K-Theory and Homology", "msc": ["19", "18G"],
     "blurb": "Algebraic and topological K-theory, cyclic homology"},
    {"id": "math.QA", "group": "Algebra", "name": "Quantum Algebra", "msc": ["16T", "17B", "81R", "18M"],
     "blurb": "Quantum groups, Hopf algebras, vertex algebras, operads"},
    # Number theory and geometry
    {"id": "math.NT", "group": "Number Theory & Geometry", "name": "Number Theory", "msc": ["11"],
     "blurb": "Analytic, algebraic and combinatorial number theory, Diophantine problems"},
    {"id": "math.AG", "group": "Number Theory & Geometry", "name": "Algebraic Geometry", "msc": ["14"],
     "blurb": "Varieties, schemes, moduli, sheaves, arithmetic geometry"},
    {"id": "math.DG", "group": "Number Theory & Geometry", "name": "Differential Geometry", "msc": ["53", "58"],
     "blurb": "Riemannian, complex and contact geometry, geometric analysis"},
    {"id": "math.SG", "group": "Number Theory & Geometry", "name": "Symplectic Geometry", "msc": ["53D", "37J"],
     "blurb": "Symplectic and Poisson manifolds, Floer theory, Hamiltonian systems"},
    {"id": "math.MG", "group": "Number Theory & Geometry", "name": "Metric Geometry", "msc": ["51", "52"],
     "blurb": "Convex and discrete geometry, metric spaces, Euclidean geometry"},
    # Topology
    {"id": "math.AT", "group": "Topology", "name": "Algebraic Topology", "msc": ["55"],
     "blurb": "Homotopy theory, homology and cohomology, spectral sequences"},
    {"id": "math.GT", "group": "Topology", "name": "Geometric Topology", "msc": ["57"],
     "blurb": "Manifolds, knots, low-dimensional topology, mapping class groups"},
    {"id": "math.GN", "group": "Topology", "name": "General Topology", "msc": ["54"],
     "blurb": "Point-set topology, continuum theory, compactness, dimension"},
    # Analysis
    {"id": "math.CA", "group": "Analysis", "name": "Classical Analysis and ODEs",
     "msc": ["26", "28", "33", "34", "39", "40", "41", "42", "44", "45"],
     "blurb": "Real analysis, special functions, harmonic analysis, ODEs, inequalities"},
    {"id": "math.AP", "group": "Analysis", "name": "Analysis of PDEs", "msc": ["35"],
     "blurb": "Existence, regularity and behaviour of solutions of PDEs"},
    {"id": "math.FA", "group": "Analysis", "name": "Functional Analysis", "msc": ["46", "47"],
     "blurb": "Banach and Hilbert spaces, operators, distributions"},
    {"id": "math.CV", "group": "Analysis", "name": "Complex Variables", "msc": ["30", "32"],
     "blurb": "Holomorphic functions in one and several variables"},
    {"id": "math.OA", "group": "Analysis", "name": "Operator Algebras", "msc": ["46L", "47L"],
     "blurb": "C*-algebras, von Neumann algebras, noncommutative geometry"},
    {"id": "math.SP", "group": "Analysis", "name": "Spectral Theory", "msc": ["47A", "35P", "58J", "81Q"],
     "blurb": "Spectra of operators, Schrödinger operators, spectral geometry"},
    {"id": "math.DS", "group": "Analysis", "name": "Dynamical Systems", "msc": ["37"],
     "blurb": "Ergodic theory, chaos, bifurcations, complex dynamics"},
    # Discrete
    {"id": "math.CO", "group": "Discrete Mathematics", "name": "Combinatorics", "msc": ["05"],
     "blurb": "Graph theory, enumeration, extremal and algebraic combinatorics, designs"},
    {"id": "math.LO", "group": "Discrete Mathematics", "name": "Logic", "msc": ["03"],
     "blurb": "Set theory, model theory, proof theory, computability"},
    {"id": "cs.DM", "group": "Discrete Mathematics", "name": "Discrete Mathematics and Theoretical CS",
     "msc": ["68", "05"], "extra_cats": ["cs.CC", "cs.DS", "cs.LO"],
     "blurb": "Algorithms, complexity, formal languages, discrete structures"},
    {"id": "math.IT", "group": "Discrete Mathematics", "name": "Information and Coding Theory", "msc": ["94"],
     "extra_cats": ["cs.IT"],
     "blurb": "Error-correcting codes, cryptography, information measures"},
    # Probability and statistics
    {"id": "math.PR", "group": "Probability & Statistics", "name": "Probability", "msc": ["60"],
     "blurb": "Stochastic processes, limit theorems, random structures"},
    {"id": "math.ST", "group": "Probability & Statistics", "name": "Statistics Theory", "msc": ["62"],
     "extra_cats": ["stat.TH", "stat.ML"],
     "blurb": "Estimation, testing, asymptotics, statistical learning theory"},
    # Applied
    {"id": "math.NA", "group": "Applied Mathematics", "name": "Numerical Analysis", "msc": ["65"],
     "blurb": "Numerical methods, error analysis, scientific computing"},
    {"id": "math.OC", "group": "Applied Mathematics", "name": "Optimization and Control", "msc": ["49", "90", "93"],
     "blurb": "Convex and nonconvex optimization, calculus of variations, control"},
    {"id": "math.MP", "group": "Applied Mathematics", "name": "Mathematical Physics",
     "msc": ["70", "74", "76", "78", "80", "81", "82", "83"], "extra_cats": ["math-ph"],
     "blurb": "Mathematical methods of mechanics, quantum theory, statistical physics"},
    # General
    {"id": "math.HO", "group": "General", "name": "History and Overview", "msc": ["01", "97", "00"],
     "blurb": "Expository work, history of mathematics, education"},
    {"id": "math.GM", "group": "General", "name": "General Mathematics", "msc": ["00"],
     "blurb": "Topics that do not fit another category"},
]

_BY_ID = {d["id"]: d for d in DOMAINS}


def get(domain_id: str) -> dict | None:
    return _BY_ID.get(domain_id)


def resolve(ids: list[str]) -> list[dict]:
    return [d for d in (_BY_ID.get(i) for i in ids or []) if d]


def names(ids: list[str]) -> str:
    return ", ".join(d["name"] for d in resolve(ids)) or "Mathematics"


def arxiv_categories(ids: list[str]) -> list[str]:
    cats: list[str] = []
    for d in resolve(ids):
        for c in [d["id"], *d.get("extra_cats", [])]:
            if c not in cats:
                cats.append(c)
    return cats


def msc_prefixes(ids: list[str]) -> list[str]:
    out: list[str] = []
    for d in resolve(ids):
        for m in d["msc"]:
            if m not in out:
                out.append(m)
    return out


def describe(ids: list[str], subarea: str = "") -> str:
    """One paragraph given to every model so it knows the field."""
    ds = resolve(ids)
    if not ds:
        base = "Mathematics (domain not specified)"
    else:
        base = "; ".join(f"{d['name']} ({d['id']}, MSC {'/'.join(d['msc'])}: {d['blurb']})" for d in ds)
    if subarea.strip():
        base += f". Sub-area stated by the author: {subarea.strip()}"
    return base
