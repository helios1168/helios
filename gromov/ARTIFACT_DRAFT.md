# How Gromov attacks a problem

This is a distillation of Mikhail Gromov's working method, built from 930 claims extracted from ten units of his collected writing: lecture courses on curvature, isoperimetry and probability, essays on biology, cognition and language, and his surveys. Every claim survived an adversarial review. Quotes were machine-checked character by character against the cited page, and a fresh reader judged whether the claimed move was stated there. Claims marked `WEAK` by that review are used here only under their narrower readings.

The 930 claims collapse into fourteen moves. A move made the list when it recurs across *independent* units, and, wherever possible, when Gromov states it as a principle rather than performing it once. A principle stated in a geometry lecture and performed in a biology essay counts for far more than one repeated five times inside a single manuscript. Every quote below is verbatim from the corpus and carries its claim id, so it can be traced back to source and page. Two sections at the end are not his: a stopping rule (flagged as constructed, uncited) and an account of what this corpus cannot show.

A caution before the list: the moves are not independent of each other. Symmetry-hunting (Move 3) is what makes counting (Move 4) work; the statement ladder (Move 11) presupposes the proof dissection of Move 10; the ignorance bookkeeping of Move 14 is what keeps the surrogate retreat of Move 8 honest. Gromov runs them as one instrument.

---

## 1. Build the language in which the question becomes askable

**The move.** Before attacking, ask whether the question can even be stated. If not, treat constructing the language as the problem itself. Formulate quasi-mathematically with minimal input from the domain; permit deliberate, *labeled* vagueness ("Vague Conjecture", "Vague Question") and metaphor as scaffolding; lower the target, when necessary, from "solve the problem" to "make the problem formulable". The formulation is a deliverable in its own right.

**Trigger.** You are facing a domain (a new field, an undefined concept, a phenomenon like "life", "meaning", "understanding") where every attempted precise statement either dissolves or visibly misses the point, and you cannot articulate what a good question would even look like.

**In his words.**
> "We do not expect close similarities between these spaces but we want to develop a mathematical language applicable to all of them. That would help in overcoming the most serious difficulty in approaching unknown: our disability to ask questions. A good language would allow articulations of questions about B inspired by certain knowledge about A."
> — large dimensions 14dec 2108.md, p.4  `bio-dimensions-052`

> "Formulating problems is, probabaly, most essential aspect of human mathematics; for this reason, mathematicians have been shying from a mathematical study of this process. Breaking the tradition, let is try to do this in the context of *induced geometric structures*."
> — nash-Dec-2015.md, p.60  `probability-nash-entropy-062`

> "Eventually, we have to express this in truly mathematical terms, but we resort to a metaphoric language for a while, since we do not want to narrow our field of vision and miss the target with prematurely precise definitions."
> — ergo-new-21-apr-2015, p.81  `cognition-a-057`

**Range.** This is the most widely attested move in the corpus: it organizes the biology essays (the "pre-mathematical problem" of finding a formulation, `bio-dimensions-036`), the cognition program (offering "a possible language for formulating the problem", `cognition-b-086`), and the Nash survey, where he literally builds coordinates for a "space of questions" (`probability-nash-entropy-062`). He even treats the ability to ask questions as a milestone a construction must *earn*: "Looks promising, but I have not arrived at a point of asking questions" (`survey-056`). His own hedge is severe: identifying which formulation will grow into mathematics "is the first and the most formidable question", and "the viability of a seed is seen only with the hindsight" (`bio-dimensions-025`). The failure case is also on record: the cognition program is thirty-plus years of language-building that, by his own repeated admission, never reached a working model. The move can run indefinitely without converging (see the stopping section).

## 2. Refuse to be hypnotized by definitions

**The move.** Treat every received definition as a record of past insight, not a constraint on the future. Test a definition operationally: does any argument actually *consume* it? If not, discard it regardless of pedigree. Conversely, when a working formula proves things efficiently, promote the formula to the definition. Judge candidate definitions by their generative reach, since a good one points at the unknown. Refuse to define the most general terms at all, letting examples carry them.

**Trigger.** A proof is grinding because the standard definition delivers nothing usable; or a definition feels "natural" but you cannot name a single theorem that runs through it; or you catch yourself deferring to a definition merely because it is canonical.

**In his words.**
> "Morale. Our best definitions, e.g. that of a manifold, tower as prominent landmarks over our former insights. Yet, we should not be hypnotized by definitions. After all, they are remnants of the past and tend to misguide us when we try to probe the future."
> — manifolds-Poincare-Oct1-10.md, p.3  `bio-dimensions-071`

> "But this is an illusion: there is no single known (are there unknown?) geometric argument, which would make use of this definition. The immediate reason for this is *the infinitesimal* nature of the volume comparison property: it *doesn't integrate* to the corresponding property of balls of specified, let them be small, radii … The following *alternative*, let it be also only infinitesimal, property of the scalar curvature seems more promising"
> — Scalar-Jul-8-2021.md, p.8  `curvature-scalar-002`

> "A definition worth its name – mathematicians learned this from Alexander Grothendieck is not a concise wording of what everyone knows but a pointer toward unknown. Many unexpected fruits have been harvested from what grew from the seeds of ideas in his definitions."
> — Quotations and Ideas.md, p.44  `cognition-b-074`

**Range.** Practiced across all the geometry units: he re-founds sectional curvature on the equidistant-variation formula because it "delivers fast clean proofs … by far more efficiently than … the cumbersome language of Jacobi fields", a passage repeated in at least four separate documents (`curvature-misc-016`, `curvature-scalar-012`, `isoperimetry-042`); he distinguishes a definition from a computation ("One doesn't define the area of the disk as the limit of the areas of the regular inscribed n-gons", `curvature-misc-002`); and he holds that easy-arriving definitions should be *rejected* for that very reason (`cognition-b-118`). The counterweight is explicit: for the most general concepts ("structure", "ergosystem") he refuses definitions entirely and works from examples (`cognition-a-094`, `cognition-b-117`) — so the move is not "always redefine" but "never let the definition outrank the arguments it feeds."

## 3. Find the symmetry that pays for the tool

**The move.** For any tool that works "unreasonably" well (probability, entropy, statistics, a sharp inequality), locate the symmetry (equiprobability, invariance group, homogeneity) that is secretly paying for it. Before importing the tool into a new domain, check whether that symmetry is present there; where it is absent or degraded, expect the tool to stall, and either restrict its use or rebuild it. Run the move generatively too: enlarge the symmetry group and let the enlargement dictate the extension of the whole theory.

**Trigger.** You are about to transplant a successful formalism (probabilistic reasoning, entropy, a variational principle) into a new domain, especially a heterogeneous one like biology or language; or you are wondering *why* a formalism works and want more than "it does".

**In his words.**
> "These numbers, the *values of probabilities* of micro-states of ensembles of particles, are *physically meaningless*. This doesn't bother a physicist. He/she boldly *assumes* that these numbers are all *mutually equal* and derives from this and a few similar *assumptions* physically sound conclusions. *It is symmetry, not any idea of "measure of underminancy", which makes the concept of probability to work so beautifully in physics.*"
> — lectures IprobHES May 2022.md, p.7  `probability-paris-025`

> "Apparently, the elegance and success of probabilistic models in mathematics and science (always?) depends on (often tacitly assumed and/or hidden) symmetry. … But if there is not enough symmetry and one can not postulate equiprobability (and/or something of this kind such as independence) of certain 'events', then the advance of the classical calculus stalls, be it mathematics, physics, biology, linguistic or gambling."
> — ergo-new-21-apr-2015, p.55  `cognition-a-040`

> "(This, possibly, may explain the "unreasonable effectiveness" of entropy in mathematical physics and in math generated by physics, and which points toward "quantum nature" of entropy. But exactly this beautiful hidden symmetry makes one wary of transplanting the idea of entropy from physics to mathematical models of Life.)"
> — large dimensions 14dec 2108.md, p.38  `bio-dimensions-063`

**Range.** Exceptionally strong independence: stated as principle in the probability lectures, the cognition manuscripts, and the biology essays, and performed constantly in geometry: "SYMMETRY BEGETS STATISTICS" (`bio-dimensions-059`), matching the measuring norm to the target's symmetries for sharp inequalities (`curvature-misc-037`), extending classical to quantum entropy by replacing Sym(I) with O(n) (`probability-paris-064`), varying the invariance group of a known inequality to generate new ones (`probability-paris-048`). He also audits the symmetry *cost* of formalisms: Turing encoding "irrevocably erases symmetries" (`cognition-a-131`), and convenient coordinates "sacrifice" geometry (`bio-dimensions-072`). One warning he issues himself: do not assume the extremum of a symmetric problem inherits the symmetry, because "symmetric equations may have non-symmetric solutions" (`bio-dimensions-092`).

## 4. If you don't understand — count

**The move.** When conceptual understanding is missing, count: cardinalities of possibility spaces, parameters against constraints, equations against unknowns, bits available against bits required. The count predicts what is special before you can explain why, tells you where an invariant must live and how big it is, and sets the default expectation for solvability. But always attach the escape clause: state explicitly what kind of hidden identity or structure would overturn the count.

**Trigger.** You face a "why is this pattern so frequent?" question with no mechanism in sight; or you are sizing up a system of constraints and need a first verdict on rigid-versus-flexible; or you suspect an object is special but cannot say in what way.

**In his words.**
> "And even if you have no idea why this is so, why trees are special, you can predict this by following an amazingly effective mathematician's motto: **If you don't understand – COUNT!**"
> — 2024 lectures 1-4 life and cognition.md, p.30  `isoperimetry-050`

> "Here, counting parameters tells you that if  $q < s_n = \frac{n(n+1)}{2}$  and  $r$  is large… then generic  $X^n \subset \mathbb{R}^q$  must be, locally as well as globally, rigid UNLESS there is some miraculous identity between high derivatives of the (extrinsic) curvatures of this  $X^n$ ."
> — nash-Dec-2015.md, p.7  `probability-nash-entropy-038`

**Range.** The motto appears verbatim in three independent units (biology course, isoperimetry lectures, trees essay: `bio-dimensions-017`, `isoperimetry-050`, `survey-057`), and parameter-counting drives the curvature lectures (the size of the curvature tensor is announced by arithmetic before any computation, `curvature-hopf-015`). Gromov is equally insistent on the move's failure boundary, and he charts it rather than hiding it: counting predicted no flat metrics and no Kähler metrics, "Yet flat metric exists!" (`survey-014`); Nash's C¹ theorem is "sheer madness from a hard-minded analyst's point of view" precisely because it defeats the count (`survey-019`); the heuristic "may brake down at low regularity" and locating the demarcation line is itself posed as a problem (`probability-nash-entropy-066`); and he constructs cases where a convincing counting "proof" is simply false because a hidden symmetry beats independence (`cognition-a-123`). The full move is therefore: count, act on the count, and hunt the structured exception that would defeat it.

## 5. Set numerical bounds before you speculate

**The move.** Refuse to discuss any model, especially of living or thinking systems, until its spatial, temporal and informational parameters are pinned within realistic numerical ranges. Ban unearned infinities and asymptotics: check whether your phenomenon lives at specific finite parameter values that limit theorems never reach. Reject "in principle" solutions whose resource count leaves the physically realizable range. Then use the bounds positively: tight finiteness is what makes the description tractable at all.

**Trigger.** You (or a literature you are entering) are about to reason with "arbitrarily large", "in the limit", or "in principle computable" about a system that is emphatically finite: a genome, a brain, a corpus, an algorithm that must run in a lifetime.

**In his words.**
> "But any speculation on natural or artificially designed 'intelligent' systems strikes one as meaningless, if spacial and temporal parameters of possible implementations of such systems are not specified and set within realistic numerical bounds."
> — ergo-new-21-apr-2015, p.92  `cognition-a-068`

> "'Infinite', 'arbitrarily small', 'arbitrarily large', etc, are bona fide mathematical concepts. Their application in modeling physical systems (e.g. via differential equations) is (non-trivially) justifiable and (often inexplicably) successful. But their unrestrained use in biology, psychology, linguistics and philosophy of AI breeds nothing but pointless speculations."
> — Learning Language-sections 1.1-1.9 and 2.1.md, p.9  `cognition-b-011`

> "The numerical constrains on anticipated networks NET are hard to reconcile with the free spirit of mathematics: (almost) all our cherished concepts, constructions and theorems apply to an infinite range of possibilities. But the (stronger) bounds on the size of the description of UNINET and the dynamics of learning in it makes such a description amenable to a mathematical reasoning available to us."
> — mathematic of mental processes.md, p.18  `cognition-b-164`

**Range.** Stated as doctrine throughout the cognition and biology units: complexity budgets for learning algorithms ("at most log-linear … with no large constant attached", `cognition-a-067`), "Squares are unacceptable" (`cognition-a-079`), the listable/unlistable divide (`cognition-a-114`), explanation-with-bounds for the peacock's tail (`cognition-a-099`), rejection of brute force whose count leaves the multiverse (`cognition-b-156`). It surfaces in pure mathematics as the demand for effective constants (`curvature-scalar-074`), for hypotheses that are algorithmically checkable ("what the hell does it mean 'assume'?", `survey-038`), and for definitions with controlled convergence rates (`probability-nash-entropy-019`). Where the corpus shows the move under strain: where a phenomenon genuinely lives "not so much in the limit but at specific (large but not very large) values of parameters" (`bio-dimensions-101`), existing asymptotic mathematics simply has no tools, and the bounds mark a gap without filling it.

## 6. Treat the improbable as a signal of structure

**The move.** Weight events by their improbability, not their frequency. A pattern recurring far above its combinatorial baseline, a rare event too structured for chance, a successful compression that had negligible prior probability, your own amazement at something "simple". Each certifies an invisible structure and should be promoted to the driving question. Conversely, use improbability to certify uniqueness: a reduction so unlikely to exist at all is, once found, essentially unique.

**Trigger.** Something recurs that "should not": a twenty-letter string appears twice in a corpus; trees dominate where networks should; a model fits far better than its parameter count entitles it to; a fact you fully believe still astonishes you.

**In his words.**
> "Besides, ergo-learning depends on 'structurally significant' (often amazing) rare events … For example, the boundary of a visual image, albeit it has negligible 'probability', is the most essential feature of this image. Similarly, if a randomly looking sequence of, say 20, letters appears twice in a 10^5-letter text, it can not be brushed off as a statistical fluctuation."
> — ergobrain-web-june2013, p.89  `cognition-a-128`

> "Judging phenomena of 'Ramanujans' and 'Mozarts' statistically insignificant is like signing off explosions of supernovae to mere accidents, just because only a dozen of supernovae were recorded in our galaxy with billions stars (none since October 9, 2004)."
> — memorandum ergo.md, p.32  `cognition-b-099`

> "The lesson we draw from this example is that if something, however simple, amazes your mind eye (or, rather, your 'ergo eye'), there must be an invisible deep structure underlying it."
> — ergobrain-web-june2013, p.45  `cognition-a-105`

**Range.** Cross-unit and stated as principle: the trees-versus-networks disproportion drives the biology lectures (`bio-dimensions-015`), the improbable-fit-certifies-structure argument recurs in both cognition manuscripts (`cognition-a-121`, `cognition-b-137`), and in geometry the same reflex appears as "amazingly simple, where this 'amazing' brings it far from 'trivial'" (`curvature-scalar-051`) and "It is hard to believe this may be true!" as a marker of hidden structure (`probability-nash-entropy-080`). The corpus also contains his own antidote: probabilistic heuristics "may be deceptive at times", and he deliberately constructs cases where the improbability argument proves something false (`cognition-a-123`). So the move licenses attention rather than belief. The improbable earns investigation, and the investigation still has to be done.

## 7. Never study the object alone: pass to the space of its kin

**The move.** Replace the individual object by the space of all such objects: parametrize, take families, ask for the morphisms, and read features positionally. When the given set is a historical accident (extant proteins, one evolved brain, the manifolds someone happened to construct), it cannot be described by general principles. Either complete it to the *conceivable* set governed by rules, or restrict to certifiably representative fragments, or quotient down to what observation actually reaches.

**Trigger.** Your object is one sample of a random or historical process; or every property you compute feels arbitrary because there is nothing to compare against; or a theorem about one object begs to be a statement about a moduli space.

**In his words.**
> "'Structured objects' do not appear in isolations but rather as members of 'communities' of 'similar objects', where the 'position' of an object in its community determines the essential (sometimes all) features of this object and where 'position' is expressed in terms of the structure(s) of the community."
> — ergobrain-web-june2013, p.82  `cognition-a-125`

> "But the true biological problem, which is more subtle and more interesting than the (essentially physical/mathematical) folding problem, concerns not individual spaces (C_P, E_P), but their totality parametrised by the space P ∋ P of the polypeptide (sequences) P, where the present day P_now can be seen as a set of quasistationary points of the evolutionary dynamics acting on P"
> — large dimensions 14dec 2108.md, p.3  `bio-dimensions-050`

> "Introduce parameters wherever possible is a motto of modern mathematics; Grothendieck concept of *topos* – a category of sets parametrized by a "topological site" – is the most general manifestation of this."
> — Formulas&Di_erential Operators..md, p.67  `curvature-misc-033`

**Range.** Among the most independent clusters in the corpus: biology (escape the accidental set toward the conceivable, `bio-dimensions-049`, `bio-dimensions-069`, `bio-dimensions-070`), cognition (locate the unknown model inside an explicitly defined space of conceivable models, `cognition-b-005`), geometry (spaces of metrics, "*All* topological and geometric constraints … are accompanied by non-trivial homotopy theoretic properties of spaces of such metrics", `curvature-scalar-090`), probability (when nothing can be said about all objects, randomize the class and study the typical one, `probability-paris-016`), and surveys (`survey-006`). The categorical variant (read the object off its arrows, and re-derive the *right* morphisms for each new class rather than importing the old ones) is stated in both cognition units and performed in the curvature lectures (`cognition-a-119`, `cognition-b-161`, `curvature-scalar-020`, `survey-030`). His own cap on the move: "One may continue indefinitely along these lines but one has to stop somewhere. … if you fly too high in the sky of math you may miss your destination down on Earth" (`cognition-a-050`). That is the nearest thing to a stopping rule the corpus contains, and it caps *generalization* rather than effort.

## 8. Step down to a surrogate, then audit the step

**The move.** When the real object is intractable, deliberately substitute a stripped model: the frozen corpus for the living language, the free chain for the folded protein, the equivariant subcase for the full conjecture. Then run two audits immediately. First: is the surrogate actually easy? (Often it is not, which is itself a finding.) Second: price the retreat. Estimate how much a total success on the surrogate would contribute to the real question, and let that number shape how much rigor the surrogate deserves.

**Trigger.** The honest formulation of your problem is out of reach and you are tempted to work on something adjacent. The temptation is fine, provided the substitution is explicit and priced rather than silent.

**In his words.**
> "Making math out of this is hard: following in steps after P.J. Flory and Orr, we turn to something easy: unfolded proteins, thought of as chains of beads freely floating in solution. But is it easy? Not at a all: the space of these looks something like this."
> — Protein Spaces.md, p.2  `bio-dimensions-037`

> "But even if we prove 200% of what we want, it will contribute something like 0.0002% to our knowledge of proteins."
> — Protein Spaces.md, p.3  `bio-dimensions-038`

> "A more realistic mathematical problem is that of finding a class of models of high dimensional stochastic gradient-like systems which may be far from real proteins but where the above questions have positive answers."
> — proteins-crystals-isoperimetry.md, p.23  `bio-dimensions-103`

**Range.** Performed in biology (above), in cognition (the imaginary non-human language with semantics deleted, `cognition-a-053`; observable homologues for unobservable learning, `cognition-b-157`), and pervasively in geometry, where the vocabulary is "realistic": retreat from the hard conjecture to the symmetric or simply-connected subcase explicitly labeled as such (`curvature-misc-013`), or "restrict the domain or enlarge the category" when the dream fails globally (`survey-039`). The corpus also shows the audit biting in both directions: the "easy" self-avoiding-walk model turns out to be where "none of the 'intuitively obvious' properties … have been rigorously proved" (`survey-004`, narrower reading: his amazement at one model's inaccessibility), and success gets redefined downward, openly, to "a class of models … where the above questions have positive answers". What he never does is let the surrogate silently *become* the problem: the distance to the real object stays on the books.

## 9. Bound the unknown by the stupidity of what produced it

**The move.** Constrain a hidden mechanism by the process that generated it. If Nature or the brain demonstrably does something, treat that as a constructive existence proof licensing the mathematical search. If the generating process is dumb (evolution as "big rather than structurally smart", mutation logic as straightforward), conclude that the mechanism cannot be too complicated, and search only among simple, universal candidates. Run it negatively too: when the naive model contradicts observed performance (folding in seconds against exponentially many minima), conclude the real object is special, and make that specialness the target.

**Trigger.** You are theorizing about a mechanism you cannot observe (learning, folding, development) and the space of candidate mechanisms feels unboundedly large.

**In his words.**
> "Of course, a structuralization of redundancies is possible only for rather special arrays of signals. But since our brain is able to do it, the signals must be special; mathematicians should be able to repeat what the brain does."
> — ergobrain-web-june2013, p.107  `cognition-a-142`

> "However, if we assume that the evolution is 'big rather than structurally smart' we conclude that the the 'logic' underlying/generating ergobrain (and ergosystems, in general) can not be too complicated and can be guessed on the basis of ergo-moods (surprised, amused, bored, etc.) that ergobrain induces in our conscious mind."
> — ergobrain-web-june2013, p.37  `cognition-a-025`

> "Can one find the "ground state" x_min = x_fold which minimizes the energy and corresponds to the folded protein? (After all if the nature does it in a few seconds, why a mathematician can not do it?) … But E may have lots of local minima: their number is likely to grow exponentially with the dimension of X … If so, a protein can not fold reasonably fast, if at all, for most E."
> — proteins-crystals-isoperimetry.md, p.21  `bio-dimensions-099`

**Range.** Concentrated in the cognition and biology units, with mutually reinforcing variants: cap model complexity by the genome's information content (`cognition-b-028`), deduce hierarchical block structure from the "exceedingly dumb and non-devious" selection process (`cognition-b-029`), draw optimism from "the poverty of hominid genome evolution" (`cognition-b-143`), rule out many specific programs by evolution's time budget (`cognition-b-090`). This is his boldest and least verifiable move: unlike the geometry moves, nothing in the corpus shows it *succeeding*. It generates the ergo-program's constraints, and the program remains unfinished. He partially hedges it himself: some improbable features must be credited to "internal constrains on possible architectures" rather than selection (`cognition-a-100`), which is an admission that the generative-process argument alone underdetermines the answer.

## 10. Dissect the proof down to the property it uses

**The move.** Treat proofs, not theorems, as the carriers of information, and dissect them. Isolate the single property a proof actually uses (then transfer the argument to everything with that property); measure a statement by its information content rather than its proof's novelty, since a 90%-identical proof can carry a disjoint message; note explicitly what a proof does *not* see; and when two proofs of "the same" theorem share nothing, suspect two different theorems and a missing unified theory behind them. Multiply proofs of what you already know: each proof is a different instrument.

**Trigger.** You have (or the literature hands you) a working proof and are about to move on; or a powerful method works for reasons nobody states; or two unrelated techniques keep landing on the same statements.

**In his words.**
> "But, surpassingly, although the proof of [☆] is 90% the same as that by Lichnerowicz, the information contents of the two statements are vastly different – almost nothing in common between them: Lichnerowicz is 99% about *delicate smooth topological invariants* of manifolds with  $Sc > 0$ , while [☆] reveals raw geometric essence of  $Sc(X) \geq \sigma > 0$ , which, as it becomes a *positive curvature* condition, *limits the size of  $X$* ."
> — Scalar-Jul-8-2021.md, p.88  `curvature-scalar-030`

> "What gives to a particular favour to  $Cl_n$  is the distinguished linear subspace  $V \subset Cl_n$ , which, on the one hand, *generates all* of  $Cl_n$ , on the other hand, the matrices corresponding to all  $v \neq 0$  in  $V$ , have maximal possible ranks … This "maximal rank property" is exactly what makes the Dirac operator *elliptic* and, because of this, so powerful in the Riemannian geometry."
> — Scalar-Jul-8-2021.md, p.75  `curvature-scalar-028`

> "The existence of two so different approaches to  $Sc > 0$  has no rational explanation at the present state of art. In general terms, the Schoen-Yau method appeals to the (non-linear) analysis in the space of submanifolds in  $V$  while the Dirac operator approach uses the linear analysis (of spinors) over  $V$ . One may hope for the existence of a unified general theory which would treat simultaneously non-linear objects inside  $V$  as well as linear ones over  $V$  in a way similar to what happens in algebraic geometry. Probably, such a unification may be possible only in an infinite dimensional framework."
> — Sign and geometric meaning of curvature.md, p.92  `curvature-hopf-049`

**Range.** The backbone of the curvature and isoperimetry lectures: extracting the common skeleton of all scalar-curvature arguments (`curvature-misc-017`), isolating the minimal hypothesis so the argument transfers (`probability-nash-entropy-021`), stripping a symmetrization proof of the symmetry it never needed (`isoperimetry-013`), locating the "innocuously looking corollary of Gauss' formula" that unlocked machinery available for a decade (`curvature-misc-021`), grading each step of a proof by the logical resources it consumes (`isoperimetry-018`), charting point-by-point the borders between two rival methods (`curvature-scalar-082`), and slowing down over "trivial" steps because the step the proof turns on may live there, detachable from the machinery (`curvature-scalar-026`). His lecture policy is the move institutionalized: "we try to show several different proofs of most of them" (`isoperimetry-001`). This is also the move most clearly *usable* by others: it requires patience and honesty more than genius.

## 11. Treat statements as raw material: run the ladder

**The move.** A proved result is an input, not an endpoint. Run the standard ladder: rough inequality → sharp inequality → equality case (rigidity) → near-equality (stability). Then operate algebraically on statements themselves: couple them under products, push products to fibrations and then foliations, exchange the roles of the two sides ("who is extremal?"), vary the invariance group, and read an inequality backwards so that what constrained smooth objects becomes the *definition* of an invariant on general spaces.

**Trigger.** You have just proved or understood a sharp result and the field feels finished; or a body of scattered results needs organizing into a program; or you need a supply of conjectures that are neither random nor trivial.

**In his words.**
> "Inequalities relating geometric quantities  $\mathcal{A}$  and  $\mathcal{B}$  of geometric objects  $Ob$  progress along the following lines. 1. *Rough Inequalities.* … 2. *Sharp Inequalities.* … 3. *Rigidity.* … 4. *Stability.* An extremal object  $Ob_{extr}$  is *stable* if convergence … implies that  $Ob_\varepsilon \rightarrow Ob_{extr}$  in a "suitable sense", where determination of this "sense" is the main problem here."
> — Scalar-Jul-8-2021.md, p.201  `curvature-scalar-064`

> "Propositions/properties  $\mathcal{P}|Sc$  concerning the scalar curvatures of Riemannian manifolds or related invariants, makes a kind of an "algebra" … properties of invariants  $\mathcal{P}|Sc$  can be modified, generalized, stabilized in a systematic manner, e.g. those concerning  $X$  and  $Y$ , can be coupled to corresponding propositions … concerning the *Riemannian products*  $X \times Y$ . Then these hybridised propositions can be developed/generalized to statements on *fibrations over  $Y$  with  $X$ -like fibers* and then further to *foliations*"
> — Scalar-Jul-8-2021.md, p.297  `curvature-scalar-084`

> "But lower bounds on Lipschitz constants of homologically substantial maps  $X \rightarrow P$  entailed by the inequality  $Sc(X) \geq \sigma > 0$ , that, for a fixed  $P$ , tell you something about the geometry of  $X$ , can be used the other way around for the definition of scalar curvature-like invariants of general metric spaces  $P$ "
> — Scalar-Jul-8-2021.md, p.261  `curvature-scalar-078`

**Range.** Demonstrated at industrial scale in the scalar-curvature lectures and stated as reflex: "It is impossible not to ask oneself what happens for λ = 1" (`curvature-misc-036`); the product guiding-principle as a conjecture machine (`curvature-scalar-036`); asking for the complete list of inequalities of a given shape (`probability-paris-072`); inverting the Hessian-of-entropy theorem into a definition to breed "generalised probability" (`probability-paris-020`). It transfers wherever a field has quantitative results. Gromov's own warning limits it: mechanical question-generation by confronting categories "seduce[s] us by simplicity and apparent naturality … but often the mirage of naturality lures us into featureless desert with no clear perspective" (`survey-051`). The ladder is for results that earned it, and no substitute for judgment about which results those are.

## 12. Chart soft against hard and work the borderline

**The move.** Before investing in a class of objects or conditions, place it on the flexibility/rigidity spectrum. Test for softness first: if every object approximately satisfies the condition (an h-principle holds, the class is dense), there is no structural geometry to find, so walk away. If the class is rigid, expect classification-type results. Then aim your effort at the borderline where softness runs out, because, in his experience, that boundary is where the significant mathematics lives.

**Trigger.** You are choosing among research directions in a technical field and need to predict, before years of work, whether a condition harbors structure or dissolves into flexibility.

**In his words.**
> "**Soft-versus-Rigid Problem.** Outline the softness domain  $\mathcal{S}$  in the space  $\mathcal{E}$  of all PDE and analyze equations on the borderline separating "rigid equations" from "soft ones". Experience shows that this borderline host most beautiful mathematics."
> — NASH--2023-OCT 1.md, p.2  `probability-nash-entropy-022`

> "Encouraged by  $\text{Ri} \geq 0$  one turns to  $\text{Ri} \leq 0$  formally generalizing  $K \leq 0$  but the naive logic does not work: *every metric can be approximated by those with  $\text{Ri} < 0$  by the Lohkamp  $h$ -principle* and no hard structural geometry exist."
> — Spaces and Questions.md, p.19  `survey-036`

> "A tantalizing wish is to find new instances, besides the big three, where softness reaches its limits with something great happening at the boundary. Is there yet undiscovered life at the edge of chaos? Are we for ever bound to elliptic equations? … And if this wish does not come true we still can make living in soft spaces exploring their geometry (their topology is completely accounted for by the  $h$ -principle)"
> — Spaces and Questions.md, p.11  `survey-021`

**Range.** Stated as a program in the surveys and the Nash lectures, and used as a working filter elsewhere: probing a condition's plasticity until the obstruction appears (`survey-015`), noting that density of a class would kill its geometry (`curvature-scalar-022`), using the h-principle to identify where a problem is vacuous and confining study to the complement (`curvature-misc-045`), and closing a survey with "Is this 'hard' or 'soft' mathematics?" as the question that decides which toolset applies (`curvature-misc-053`). He limits the move's pretensions himself: soft/hard is "not meant to reveal something profound about the nature of mathematics, but rather to predispose us" (`survey-022`). It is a prior rather than a law, and the corpus gives no recipe for finding the borderline, only for recognizing softness once demonstrated.

## 13. Purge the words that think for you

**The move.** Treat the working vocabulary as an instrument that can be miscalibrated, and audit it before reasoning. Strip out self-gratifying or teleological words (intuitive, important, useful, "for the sake of") that smuggle in a theory; interrogate the low-dimensional imagery a technical word evokes ("landscape") against the honest high-dimensional object; treat "just" in any definition as an alarm; and rename objects deliberately ("graph", not "semiosis") to redirect attention from imagined meaning to actual structure. You cannot improve a science without improving its nomenclature.

**Trigger.** An argument feels persuasive but you cannot locate its force; a field's standard metaphor is doing your imagining for you; or you notice your questions are the ones the inherited vocabulary makes easy to ask.

**In his words.**
> "The self-gratifying ego-vocabulary of intuitive, intelligent, rational, serious, objective, important, productive, efficient, successful, useful. will lead you astray in any attempt of a rational description of processes of learning; these words may be used only metaphorically. We can not, as Lavoisier says, to improve a science without improving the language or nomenclature which belongs to it."
> — ergo-new-21-apr-2015, p.10  `cognition-a-007`

> "Here beware: the word "landscape" misdirects you imagination: what is commonly seen as "landscape" has rather primitive tree structure in it – mountains don't branch much. It takes a bit of mathematical thinking to see trees in the multidimensional energy and/or fitness landscapes and to appreciate the significance of this treeness."
> — 2024:biology course first 5 lectures 4.md, p.39  `bio-dimensions-018`

> "Even if tacit, 'just', is a sign of something being wrong with a definition. (An elephant is not 'just' a big mouse with two tails.) … More to the substance, the above definition hides 'true structures', e.g. symmetries of the space which are apparent in our ordinary 3-space."
> — ergobrain-web-june2013, p.65  `cognition-a-116`

**Range.** Independently attested: the vocabulary purge and the Lavoisier line recur across both cognition manuscript families (`cognition-b-098`); the landscape warning recurs across three units (`isoperimetry-051`, `survey-062`); metaphor-displacement diagnosis dissolves arguments in the Quotations essays (`cognition-b-034`, `cognition-b-072`); and in his own field he practices it by renaming an invariant when its behavior betrays its name ("K-cowaist" for a non-additive "area", `curvature-scalar-069`). Adjacent and equally attested: distrust of trained intuition itself. "The past mathematical experience channals your imagination toward the old rather than new mathematical concepts" (`bio-dimensions-022`), the rejection of introspection as data (`cognition-b-036`), and "if the source of your idea in science is common sense, search for another idea" (`cognition-a-002`), which he states without hedge and at his most extreme. The corpus also shows the cost: he admits erasing the "ego-imprints" is a fight the writer is losing too (`cognition-a-023`), and his replacement vocabulary (ergo-words) never demonstrably paid off. The purge is well-evidenced; the proposed replacement much less so.

## 14. Keep itemized books on your ignorance

**The move.** Maintain explicit, specific, graded bookkeeping of what you do not know and have not verified, and publish it. Convert diffuse ignorance into named unknowns; label every conjecture with the quality of its evidence and whether you even know where a counterexample would live; quarantine results whose proofs you have not mastered and route your own arguments around them; flag your own suspiciously strong results as probable errors; grade your own arguments honestly ("60% Proof", "convincing but it is not quite a proof").

**Trigger.** Continuously, but especially when writing anything, when a result depends on literature you have not checked, when your theorem is stronger than the field's for no visible reason, and when you notice you cannot say precisely *what* you don't understand.

**In his words.**
> "What is really depressing is the difficulty of specifically articulating what you don't understand. Saying I know that I know nothing is senseless if you don't know what stands behind this "nothing"."
> — large dimensions 14dec 2108.md, p.45  `bio-dimensions-066`

> "My, rather superficial, understanding of Lohkamp works suggests that this is possible, but it can't be safely applied unless everything is written out in full detail. I feel more comfortable at this point with generalizing theorem 4.6 from the Schoen-Yau paper [SY(singularities) 2017]"
> — Scalar-Jul-8-2021.md, p.116  `curvature-scalar-041`

> "the sad truth is that one has a poor understanding of what these classes actually are, how much they overlap and what part of the world of groups they fairly represent. At the moment, there is no basis for believing in this conjecture and there is no idea where to look for a counterexample either."
> — Scalar-Jul-8-2021.md, p.173  `curvature-scalar-053`

**Range.** The most consistently *performed* move in the corpus, across every unit: "This, as many other our conjectures, is based on a limited class of examples with no idea of where to look for counter examples" (`curvature-misc-025`); "But I mainly failed to extract relevant information. Maybe you'll have better luck" (`bio-dimensions-060`); the "*Worrisome Remark*" suspecting his own definitions when his hypotheses come out suspiciously weak (`curvature-scalar-049`); "*Admission.* I don't even see, why…" (`curvature-scalar-037`); the "*Melancholic Remarks*" reading a diversity of partial results as the symptom of the missing true theorem (`curvature-scalar-076`); reading the chaos of accumulated results as a map of ignorance (`curvature-scalar-044`). Notice what this bookkeeping is *for*: the ignorance ledger is his question-generation engine, and the named unknowns of Move 14 are the inputs to Move 1. This move transfers to any researcher immediately and at zero technical cost; it is also the one the incentive structure of publishing most punishes, which Gromov, unusually, simply ignores.

---

## Stopping: not from Gromov

**Nothing in this section is Gromov's, and nothing in it is cited; it is constructed by the compiler.** The corpus reports abandoned attempts but states no criterion for abandonment; several independent readers flagged this gap. The closest he comes is a cap on *generalization* (stop abstracting when altitude stops serving the destination; see Move 7's range) and scattered remarks that boredom terminates barren iteration, that problems smelling of undecidability should be dropped, and that a direction should be able to show "new results and/or specific questions". None of these tells you when to stop working a problem. A method whose every instruction is "go deeper, look wider, change the object" will run forever. His own cognition program, decades long and unfinished by his own account, is the demonstration. So here is a stopping rule assembled *from* his moves, in their spirit, without his authority.

The organizing idea: Gromov's engine runs on questions. His test for an interesting structure is the abundance of problems it generates; his ignorance ledger exists to name unknowns; his first move makes the question the deliverable. The natural stopping criterion for such an engine is not "the problem is solved" or "the problem is hard" but **the space of questions has stopped yielding.**

Concretely, review a line of attack at fixed intervals (monthly, or per unit of serious effort) against three tests:

1. **Question yield.** Write down the questions this period's work produced that you *could not have formulated* before it. Reformulations of old questions in new vocabulary do not count unless the new vocabulary shortens them (a shorter statement is itself a yield). If two consecutive review periods yield nothing, the line is stagnant.

2. **Marginal value.** Re-run the surrogate audit (Move 8) on the current form of the problem: if everything you are now attempting succeeded completely, what fraction of the question you originally cared about would it answer? If that fraction has been shrinking across reviews, each retreat priced and the product of the prices now negligible, the retreats have replaced the problem. Stop, and either return to the original question by another route or admit you have changed problems.

3. **Language health.** Track the cost of stating what you believe: if formulations still take pages where they should take lines, *and* successive reformulations are not getting shorter, the language-building move has failed for this object at this time. (If they are getting shorter, the line is alive even with no theorems: formulation progress is progress.)

Stagnation on all three triggers one permitted escape: change the object once: pass to the space of kin, swap the surrogate, enlarge or restrict the category. This is exactly the move his method would make, and it is often right. But it is permitted **once per stagnation**. If the line stagnates again after the escape, stop entirely: file the sharpest formulations and the named unknowns in the ignorance ledger, and leave.

Two clarifications, both compiler's inference. First, stopping work is not renouncing the question: the ledger keeps it, at zero cost, available to a future language, consistent with his practice of re-classifying his own questions as misguided while keeping them on file. Second, the rule deliberately does not use difficulty as a signal in either direction: on his account hard-and-fruitful and hard-and-barren feel identical from inside, and only question-yield separates them.

---

## What this corpus does not show

**What he never discusses.** Collaboration is essentially absent: co-authorship, division of labor, running a seminar, advising. Nothing. It surfaces only as anecdote (consulting Bogomolov and Pisier, whose knowledge arrested a doomed proof attempt, `curvature-misc-005`; the self-implicating story of the "expert" who wrongly killed a colleague's correct idea, where the expert "was myself", `probability-nash-entropy-032`). How he chooses which problem to spend years on is explicitly declared unknowable rather than taught: problem-selection is "the first and the most formidable question", with promise visible "only with the hindsight" (`bio-dimensions-025`), and problem-quality discoverable only by solving ("we never know which problem is nice and which is ugly unless we solve it", `curvature-hopf-010`). When to quit is never stated (hence the constructed section above). The mechanics of writing, revision, and daily work practice appear only obliquely (a preference for self-contained exposition, `curvature-scalar-001`; the standard that mathematics should be doable "solely in your mind", `curvature-misc-010`). There is nothing on careers, students, refereeing, or recovering from error beyond flagging errors honestly.

**Where the material is thin or not independent.** The apparent breadth of the cognition evidence overstates its independence: cognition-a and cognition-b are largely the same program (ergo-systems) written out in overlapping manuscripts, so a principle recurring five times there may be one decision, restated. The same holds for the biology essays, and the isoperimetry lecture notes exist in near-duplicate versions. Moves attested only inside those families (Move 9 in particular, and the specific replacement vocabulary of Move 13) rest on far less independent evidence than the citation count suggests. Several vivid items are single-sourced asides the review already marked WEAK (the Gamov story, the Maillet/Darwin/Lyell anecdote, the Burnside comparison) and accordingly none of them carries a move here.

**Validation is asymmetric.** The geometry moves (2, 10, 11, 12, and the geometric halves of 3 and 4) are validated by theorems: the corpus shows them producing scalar-curvature geometry, hyperbolic groups, metric-measure geometry. The cognition and biology moves (1, 5, 9, and the programmatic halves of 6 and 13) are *stated* far more explicitly as principles, since Gromov the essayist articulates method that Gromov the geometer mostly performs. But the programs they drive produced, on the evidence here, no comparable success. The distillation above therefore has highest confidence exactly where statement and performance meet: symmetry (3), counting (4), the space of kin (7), and the honesty discipline (14), each stated as principle in one unit and performed in others.

**Where the moves may not transfer.** Several moves presuppose Gromov's own equipment. "Count the possibilities" requires knowing what to count and against which baseline, and his counts draw on a command of combinatorics, representation theory, and geometry that the motto does not supply. "Find the symmetry" presupposes a repertoire of symmetries to recognize. The "naturality principle" (true formulas derived with no calculation) is one he himself admits failing to execute ("Regretfully, I couldn't do this and have resort to the (standard) symbolic manipulations", `curvature-scalar-029`), so a reader should expect to fail at it more often than he does. The extreme positions in Move 13 (discard any idea sourced from common sense; assume the truth is maximally dissimilar from what intuition whispers, `cognition-b-082`) are stated without hedge and are reported here as stated; whether they are advice or temperament, the corpus cannot say. Most transferable, at essentially no technical cost: the ignorance bookkeeping (14), the surrogate audit (8), the definition test "does any argument consume it?" (2), and the discipline of treating the question, not the answer, as the unit of progress (1). Least transferable as stated: 9 (an inference style with no validation record) and the full statement-algebra of 11, which requires a mature quantitative field to operate on.

**Finally, the shape of the source.** These are lecture notes, essays, and working documents: Gromov writing *about* and *around* mathematics, much of it late-career and reflective. A method distilled from what a great mathematician says about his method is evidence of his self-model, and his self-model is unusually honest (he documents his own failures, errors, and superficial understandings throughout). But the corpus contains no independent record of what he actually did hour by hour in the years that produced the theorems, and no testimony from anyone else. What this document captures, at best, is the method as he understood and taught it.
