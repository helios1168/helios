# economic-theory — foundational papers

**Seeded:** 2026-09-02 · **Entries:** 95 · **DOIs resolved:** 89/90 via Crossref (88 Crossref records + 1 resolving only at doi.org; 1 unresolved; 5 books/chapters carry no DOI)

How to use: `/domain economic-theory` cites only from this file. Every DOI below was fetched from `api.crossref.org/works/<doi>` on 2026-09-02 and its title/year confirmed (one exception noted inline: Binmore et al. 1990 is registered with a non-Crossref agency and resolves at doi.org). Books without a DOI carry `DOI: none (book)` plus ISBN. Anything not listed here has not been verified — treat it as a claim requiring a fresh lookup.

Sections 2 and 4 (and the Crawford–Varian entry in section 5) are carried over from `/Users/ntlee/projects/td/literature/territory_bibliography.md`; their DOIs and establishes/relevance text are kept verbatim, so their relevance notes speak in that project's vocabulary (wholesalers, ZCTAs, MINLP contiguity). Read "two wholesalers dividing ZIPs" as the generic two-agent indivisible-allocation problem.

## 1. Non-cooperative games and equilibrium

- **von Neumann 1928** — von Neumann, J. (1928). *Zur Theorie der Gesellschaftsspiele*. Mathematische Annalen 100, 295–320. DOI: 10.1007/BF01448847
  - *establishes:* The minimax theorem: every finite two-person zero-sum game has a value and optimal mixed strategies, founding game theory as a mathematical subject.
  - *relevance:* The zero-sum benchmark against which every contested-allocation problem (competing distributors, competing insurers pricing the same risk pool) is first checked before richer non-zero-sum structure is assumed.

- **Nash 1950 PNAS** — Nash, J. F. (1950). *Equilibrium points in n-person games*. Proceedings of the National Academy of Sciences 36(1), 48–49. DOI: 10.1073/pnas.36.1.48
  - *establishes:* Every finite n-person game has an equilibrium point in mixed strategies, proved via Kakutani's fixed-point theorem.
  - *relevance:* The existence result that licenses treating pricing, product-design, or territory-competition outcomes as equilibria at all; cite when a model asserts an equilibrium exists without constructing it.

- **Nash 1951** — Nash, J. F. (1951). *Non-Cooperative Games*. Annals of Mathematics 54(2), 286–295. DOI: 10.2307/1969529
  - *establishes:* Full treatment of non-cooperative games with a Brouwer-based existence proof, the definition of the non-cooperative/cooperative distinction, and solvability notions.
  - *relevance:* Source of the framing that separates "players cannot commit" (annuity market competition, adverse-selection pricing) from "players sign binding agreements" (post-merger allocation), which decides which half of this bibliography applies.

- **Harsanyi 1967** — Harsanyi, J. C. (1967). *Games with Incomplete Information Played by "Bayesian" Players, I–III. Part I. The Basic Model*. Management Science 14(3), 159–182. DOI: 10.1287/mnsc.14.3.159
  - *establishes:* Converts games of incomplete information into games of imperfect information via a common prior over types, defining Bayesian Nash equilibrium.
  - *relevance:* The formal setting for every insurance and annuity model in which the buyer knows their own mortality or risk type and the seller does not; adverse selection (Rothschild–Stiglitz, Finkelstein–Poterba) is a Bayesian game in this sense.

- **Selten 1975** — Selten, R. (1975). *Reexamination of the perfectness concept for equilibrium points in extensive games*. International Journal of Game Theory 4, 25–55. DOI: 10.1007/BF01766400
  - *establishes:* Trembling-hand perfect equilibrium, refining subgame perfection by requiring robustness to small mistakes.
  - *relevance:* The refinement that rules out threats no one would carry out; needed when a bargaining or pricing model's disagreement point rests on a threat (cf. Nash 1953) and one must argue the threat is credible.

- **Kreps & Wilson 1982** — Kreps, D. M. & Wilson, R. (1982). *Sequential Equilibria*. Econometrica 50(4), 863–894. DOI: 10.2307/1912767
  - *establishes:* Sequential equilibrium: strategies plus beliefs that are consistent and sequentially rational at every information set, including off-path.
  - *relevance:* The equilibrium concept for multi-period contracting where beliefs about a counterparty's type update along the path (guaranteed-income riders, lapse behaviour, renewal pricing).

- **Fudenberg & Maskin 1986** — Fudenberg, D. & Maskin, E. (1986). *The Folk Theorem in Repeated Games with Discounting or with Incomplete Information*. Econometrica 54(3), 533–554. DOI: 10.2307/1911307
  - *establishes:* With patient players any feasible, individually rational payoff of a repeated game is sustainable as a subgame-perfect equilibrium.
  - *relevance:* Explains why long-lived relationships (distributor–carrier, reinsurer–cedent) can sustain cooperative allocations without a binding contract, and why the set of such outcomes is too large to predict without a selection criterion such as Nash bargaining.

- **Aumann 1987** — Aumann, R. J. (1987). *Correlated Equilibrium as an Expression of Bayesian Rationality*. Econometrica 55(1), 1–18. DOI: 10.2307/1911154
  - *establishes:* Correlated equilibrium is exactly the outcome of common-prior Bayesian rationality; it generalises Nash equilibrium and is a convex set.
  - *relevance:* The right solution concept when a third party (a plan sponsor, an exchange, an allocation committee) can send correlated recommendations; also computationally tractable by linear programming where Nash equilibrium is not.

- **Fudenberg & Tirole 1991** — Fudenberg, D. & Tirole, J. (1991). *Game Theory*. MIT Press. DOI: none (book). ISBN 978-0-262-06141-4
  - *establishes:* The standard graduate reference covering static and dynamic games, refinements, repeated games, and incomplete information in one consistent notation.
  - *relevance:* The citation for a definition or standard result in non-cooperative theory when no original paper needs to be named.

## 2. Cooperative bargaining theory

- **Nash 1950** — Nash, J. F. (1950). *The Bargaining Problem*. Econometrica. DOI: 10.2307/1907266
  - *establishes:* Axiomatises the two-person bargaining solution: Pareto efficiency, symmetry, scale invariance, IIA select the point maximising the product of gains over the disagreement point.
  - *relevance:* The source of the criterion. Note the convexity assumption on the feasible set, which indivisible units violate.

- **Nash 1953** — Nash, J. F. (1953). *Two-Person Cooperative Games*. Econometrica. DOI: 10.2307/1906951
  - *establishes:* Extends the bargaining solution to the two-person cooperative game with threats, deriving the disagreement point endogenously.
  - *relevance:* Relevant to any argument that the threat point should be modelled rather than assumed. NOTE: OpenAlex records the author as 'John C. Nash'; it is John F. Nash.

- **Kalai & Smorodinsky 1975** — Kalai, E. & Smorodinsky, M. (1975). *Other Solutions to Nash's Bargaining Problem*. Econometrica. DOI: 10.2307/1914280
  - *establishes:* Replaces IIA with individual monotonicity, selecting the solution that equalises fractions of maximum attainable gain.
  - *relevance:* The main alternative criterion. Systematically favours the party with the lower baseline.

- **Kalai 1977** — Kalai, E. (1977). *Proportional Solutions to Bargaining Situations: Interpersonal Utility Comparisons*. Econometrica. DOI: 10.2307/1913954
  - *establishes:* Axiomatises proportional/egalitarian solutions, dropping scale invariance in favour of interpersonal utility comparison.
  - *relevance:* The reference for any explicit entitlement or seniority weighting, and the natural fallback when the product criterion is undefined.

- **Rubinstein 1982** — Rubinstein, A. (1982). *Perfect Equilibrium in a Bargaining Model*. Econometrica. DOI: 10.2307/1912531
  - *establishes:* Alternating-offers game with discounting has a unique subgame-perfect equilibrium; its limit supports the Nash solution non-cooperatively.
  - *relevance:* Establishes what must be true for a bargaining solution to be descriptive: players must actually make offers. If a committee decides, the criterion is normative instead.

- **Binmore et al. 1990** — Binmore, K., Osborne, M. J. & Rubinstein, A. (1990). *Noncooperative Models of Bargaining*. RePEc: Research Papers in Economics (preprint). DOI: 10.22004/ag.econ.275482 (resolves at doi.org; not a Crossref record)
  - *establishes:* Survey of non-cooperative bargaining models and the conditions under which they justify cooperative solution concepts.
  - *relevance:* Background for the descriptive-versus-normative distinction. Record type is a working paper; the canonical version is the Handbook chapter.

- **Thomson 1994** — Thomson, W. (1994). *Chapter 35 Cooperative models of bargaining*. Handbook of Game Theory with Economic Applications (book chapter). DOI: 10.1016/s1574-0005(05)80067-0
  - *establishes:* Handbook survey of cooperative bargaining: axioms, solutions, and the relations among them.
  - *relevance:* Best single citation for positioning one criterion against the family.

- **Collard-Wexler et al. 2018** — Collard-Wexler, A., Gowrisankaran, G. & Lee, R. S. (2018). *"Nash-in-Nash" Bargaining: A Microfoundation for Applied Work*. Journal of Political Economy. DOI: 10.1086/700729
  - *establishes:* Provides microfoundations for 'Nash-in-Nash' bargaining used in applied IO, clarifying what the bilateral-product assumption requires.
  - *relevance:* Template for stating what a bargaining assumption buys and costs in an applied model.

- **Warren 2025** — Warren, M. (2025). *Continuum Nash bargaining solutions*. Nonlinear Differential Equations and Applications NoDEA 32:109. DOI: 10.1007/s00030-025-01118-7
  - *establishes:* Characterises the Nash bargaining solution over a continuum of goods; at a zero disagreement point the solution is an optimal-transport map, so the allocation boundary is a Laguerre-cell tessellation.
  - *relevance:* Independent corroboration of the d=(0,0) baseline, and the natural route to N>2 wholesalers — semi-discrete transport gives Laguerre cells, which are contiguous by construction.

- **Mariotti 1998** — Mariotti, M. (1998). *Nash bargaining theory when the number of alternatives can be finite*. Social Choice and Welfare. DOI: 10.1007/s003550050114
  - *establishes:* Develops bargaining theory when the alternative set is finite rather than convex.
  - *relevance:* The correct citation for a discrete allocation set, where the classical axioms deliver a lottery rather than a point.

- **Xu & Yoshihara 2005** — Xu, Y. & Yoshihara, N. (2005). *Alternative characterizations of three bargaining solutions for nonconvex problems*. Games and Economic Behavior. DOI: 10.1016/j.geb.2005.09.003
  - *establishes:* Characterises Nash, Kalai-Smorodinsky and egalitarian solutions on non-convex problems.
  - *relevance:* Companion to Mariotti; needed if a nonzero baseline is retained on a discrete feasible set.

## 3. Cooperative games and values

- **Shapley 1953** — Shapley, L. S. (1953). *A Value for n-Person Games*. In Kuhn & Tucker (eds.), Contributions to the Theory of Games II (Annals of Mathematics Studies 28), Princeton. DOI: 10.1515/9781400881970-018
  - *establishes:* The unique value satisfying efficiency, symmetry, additivity and the null-player axiom: each player's average marginal contribution over all orderings.
  - *relevance:* The default rule for attributing joint surplus or joint cost to participants — pooled risk, shared distribution infrastructure, feature attribution in a pricing model — whenever a single "fair share" number is needed.

- **Gillies 1959** — Gillies, D. B. (1959). *Solutions to General Non-Zero-Sum Games*. In Tucker & Luce (eds.), Contributions to the Theory of Games IV (Annals of Mathematics Studies 40), Princeton. DOI: 10.1515/9781400882168-005
  - *establishes:* Introduces the core: the set of imputations no coalition can improve upon by acting alone.
  - *relevance:* The stability criterion for any allocation of a merged book of business or shared risk pool; an allocation outside the core invites a subgroup to defect.

- **Shapley 1967** — Shapley, L. S. (1967). *On balanced sets and cores*. Naval Research Logistics Quarterly 14(4), 453–460. DOI: 10.1002/nav.3800140404
  - *establishes:* The Bondareva–Shapley theorem: a game has a non-empty core if and only if it is balanced (an LP duality condition).
  - *relevance:* Gives a computable test for whether a stable allocation exists before a model searches for one; the balancedness LP is the certificate to report.

- **Schmeidler 1969** — Schmeidler, D. (1969). *The Nucleolus of a Characteristic Function Game*. SIAM Journal on Applied Mathematics 17(6), 1163–1170. DOI: 10.1137/0117107
  - *establishes:* The nucleolus, which lexicographically minimises the maximum coalitional excess; it is unique and lies in the core when the core is non-empty.
  - *relevance:* The "least-unhappy-coalition" allocation, computable by a sequence of LPs; the natural rule for cost sharing where the objective is to minimise the loudest complaint rather than the average one.

- **Shapley & Shubik 1969** — Shapley, L. S. & Shubik, M. (1969). *On market games*. Journal of Economic Theory 1(1), 9–25. DOI: 10.1016/0022-0531(69)90008-8
  - *establishes:* Games arising from exchange economies with concave utilities are totally balanced, so every market game has a non-empty core.
  - *relevance:* Explains why allocations derived from an underlying market or resource-pooling structure tend to be core-stable, and when that guarantee fails (non-concave, indivisible technologies).

- **Shapley 1971** — Shapley, L. S. (1971). *Cores of convex games*. International Journal of Game Theory 1, 11–26. DOI: 10.1007/BF01753431
  - *establishes:* For convex (supermodular) games the core is non-empty, is the convex hull of marginal-contribution vectors, and contains the Shapley value.
  - *relevance:* Risk pooling with increasing returns to pool size is supermodular, so the Shapley allocation of pooled risk capital is automatically stable; check convexity before invoking this.

- **Aumann & Shapley 1974** — Aumann, R. J. & Shapley, L. S. (1974). *Values of Non-Atomic Games*. Princeton University Press. DOI: none (book). ISBN 978-0-691-08103-3
  - *establishes:* Extends the Shapley value to games with a continuum of players, yielding the Aumann–Shapley cost-sharing prices (integrals of marginal cost along the diagonal).
  - *relevance:* The theoretical basis for allocating capital or expense across a continuum of policies or product lines; the actuarial capital-allocation literature (Euler/gradient allocation) is a special case.

- **Myerson 1977** — Myerson, R. B. (1977). *Graphs and Cooperation in Games*. Mathematics of Operations Research 2(3), 225–229. DOI: 10.1287/moor.2.3.225
  - *establishes:* The Myerson value: a Shapley value for games where cooperation is restricted to a communication graph, uniquely characterised by component efficiency and fairness.
  - *relevance:* The value concept when players can only cooperate along a network (adjacent territories, hierarchical distribution channels); links cooperative theory to the graph-constrained fair division in section 4.

- **Young 1985** — Young, H. P. (1985). *Monotonic solutions of cooperative games*. International Journal of Game Theory 14, 65–72. DOI: 10.1007/BF01769885
  - *establishes:* Characterises the Shapley value by strong monotonicity (a player's payoff depends only on their marginal contributions), replacing additivity.
  - *relevance:* The axiomatic defence to hand a committee that objects to additivity: any allocation rule that rewards higher marginal contribution with a higher share is the Shapley value.

- **Aumann & Maschler 1985** — Aumann, R. J. & Maschler, M. (1985). *Game theoretic analysis of a bankruptcy problem from the Talmud*. Journal of Economic Theory 36(2), 195–213. DOI: 10.1016/0022-0531(85)90102-4
  - *establishes:* The Talmudic division of a contested estate coincides with the nucleolus of the associated bankruptcy game and is consistent (bilateral-consistent) under contested-garment splitting.
  - *relevance:* The canonical model of dividing an insufficient pool among claimants with fixed entitlements — insolvent insurer, oversubscribed guarantee fund, or a book too small for the territories claimed.

## 4. Fair division and Nash welfare

- **Eisenberg & Gale 1959** — Eisenberg, E. & Gale, D. (1959). *Consensus of Subjective Probabilities: The Pari-Mutuel Method*. The Annals of Mathematical Statistics. DOI: 10.1214/aoms/1177706369
  - *establishes:* The Eisenberg-Gale convex program, whose optimum is the product-of-utilities allocation.
  - *relevance:* Why maximising a product of linear utilities is a well-behaved convex problem; the mathematical ancestor of the log-concavity argument.

- **Varian 1974** — Varian, H. R. (1974). *Equity, envy, and efficiency*. Journal of Economic Theory. DOI: 10.1016/0022-0531(74)90075-1
  - *establishes:* Defines envy-freeness and relates equity to efficiency in allocation.
  - *relevance:* Origin of the envy criterion that EF1 relaxes.

- **Thomson 2011** — Thomson, W. (2011). *Fair Allocation Rules*. Handbook of Social Choice and Welfare (book chapter). DOI: 10.1016/s0169-7218(10)00021-3
  - *establishes:* Handbook survey of fair allocation rules and their axiomatic characterisations.
  - *relevance:* Reference work for choosing and defending a fairness criterion.

- **Lee 2017** — Lee, E. (2017). *APX-hardness of maximizing Nash social welfare with indivisible items*. Information Processing Letters. DOI: 10.1016/j.ipl.2017.01.012
  - *establishes:* Maximising Nash social welfare with indivisible items is APX-hard.
  - *relevance:* Explains why exhaustive validation stops at small n, and why exact solvability here is a property of special structure rather than of the problem.

- **Cole & Gkatzelis 2018** — Cole, R. & Gkatzelis, V. (2018). *Approximating the Nash Social Welfare with Indivisible Items*. SIAM Journal on Computing. DOI: 10.1137/15m1053682
  - *establishes:* First constant-factor approximation for maximising Nash social welfare with indivisible items.
  - *relevance:* Establishes the computational standing of the criterion; cite alongside the hardness result.

- **Caragiannis et al. 2019** — Caragiannis, I., Kurokawa, D., Moulin, H., Procaccia, A. D., Shah, N. & Wang, J. (2019). *The Unreasonable Fairness of Maximum Nash Welfare*. ACM Transactions on Economics and Computation. DOI: 10.1145/3355902
  - *establishes:* Maximum Nash welfare (product of utilities, zero baseline) over indivisible goods is Pareto efficient and envy-free up to one good.
  - *relevance:* The theorem that licenses an EF1 claim. It requires a ZERO baseline; subtracting a constant from each utility voids the guarantee.

- **Moulin 2019** — Moulin, H. (2019). *Fair Division in the Internet Age*. Annual Review of Economics. DOI: 10.1146/annurev-economics-080218-025559
  - *establishes:* Accessible survey of fair division, including maximum Nash welfare and its fairness guarantees.
  - *relevance:* The citation to hand a non-specialist or a committee.

- **Amanatidis et al. 2023** — Amanatidis, G., Aziz, H., Birmpas, G., Filos-Ratsikas, A., Li, B., Moulin, H., Voudouris, A. A. & Wu, X. (2023). *Fair division of indivisible goods: Recent progress and open questions*. Artificial Intelligence. DOI: 10.1016/j.artint.2023.103965
  - *establishes:* Current survey of fair division of indivisible goods: EF1, EFX, MMS, and open problems.
  - *relevance:* Fastest route to the state of the art and to what is still unresolved.

- **Bouveret et al. 2017** — Bouveret, S., Cechlárová, K., Elkind, E., Igarashi, A. & Peters, D. (2017). *Fair Division of a Graph*. Proceedings of the Twenty-Sixth International Joint Conference on Artificial Intelligence (IJCAI 2017). DOI: 10.24963/ijcai.2017/20
  - *establishes:* Founds the fair-division-of-a-graph model — items are vertices of a graph and each agent's bundle must induce a connected subgraph — and shows a connected maximin-share allocation always exists on trees but not on cycles, while deciding connected proportionality or envy-freeness is NP-hard even on paths.
  - *relevance:* Establishes the baseline vocabulary and first hardness result the rest of this literature builds on; Rook-adjacency ZCTA graphs are planar and far from tree-like, so the tree-only maximin-share existence guarantee does not transfer directly.

- **Bilò et al. 2022** — Bilò, V., Caragiannis, I., Flammini, M., Igarashi, A., Monaco, G., Peters, D., Vinci, C. & Zwicker, W. S. (2022). *Almost envy-free allocations with connected bundles*. Games and Economic Behavior. DOI: 10.1016/j.geb.2021.11.006
  - *establishes:* Characterizes exactly which graphs guarantee a connected EF1 allocation for two agents: precisely those whose biconnected-component tree is a path, computable in O(m) time by a discrete cut-and-choose protocol that also achieves each agent's graph-restricted maximin share simultaneously.
  - *relevance:* Because a Rook-adjacency grid region is biconnected whenever it has no articulation points, this gives an unconditional, cheap, certified EF1-and-MMS fallback for the two-wholesaler problem whenever the disputed census component lacks a chokepoint ZCTA.

- **Suksompong 2019** — Suksompong, W. (2019). *Fairly allocating contiguous blocks of indivisible items*. Discrete Applied Mathematics. DOI: 10.1016/j.dam.2019.01.036
  - *establishes:* For contiguous (path) allocations between exactly two agents, proves an envy bound of at most the single largest per-item valuation gap and a matching equitability guarantee, with a price-of-fairness table showing egalitarian welfare costs nothing extra under equitability for two agents.
  - *relevance:* Gives an absolute, instance-size-independent loss bound usable as a sanity check: if the MINLP's contiguity cost ever exceeds the largest per-zip valuation gap, something is likely mis-specified rather than merely expensive.

- **Igarashi & Peters 2019** — Igarashi, A. & Peters, D. (2019). *Pareto-Optimal Allocation of Indivisible Goods with Connectivity Constraints*. Proceedings of the AAAI Conference on Artificial Intelligence (AAAI 2019). DOI: 10.1609/aaai.v33i01.33012045
  - *establishes:* Shows finding any Pareto-optimal connected allocation is NP-hard on trees/forests via reductions that require three or more agents, and exhibits path instances with no allocation simultaneously Pareto-optimal and EF1 — again only in constructions using three or more agents.
  - *relevance:* The oft-repeated claim that Nash welfare with connectivity is EF1-incompatible rests on constructions needing 3+ agents and is not established for exactly the two-wholesaler case — a genuine gap worth flagging rather than assuming the counterexamples transfer down to n=2.

- **Lonc & Truszczyński 2020** — Lonc, Z. & Truszczyński, M. (2020). *Maximin Share Allocations on Cycles*. Journal of Artificial Intelligence Research. DOI: 10.1613/jair.1.11702
  - *establishes:* Studies the graph-restricted maximin share specifically on cycles, showing it need not exist there even though it always exists on trees, but also proves that for exactly two agents a connectivity-respecting maximin-share allocation always exists on any connected graph, regardless of topology.
  - *relevance:* Confirms two-agent maximin-share existence is graph-independent for the wholesaler problem; only the quality of that guarantee depends on topology, addressed by the price-of-connectivity result below.

- **Bei et al. 2022** — Bei, X., Igarashi, A., Lu, X. & Suksompong, W. (2022). *The Price of Connectivity in Fair Division*. SIAM Journal on Discrete Mathematics. DOI: 10.1137/20m1388310
  - *establishes:* Defines the Price of Connectivity — the worst-case ratio between unconstrained and graph-restricted maximin share — and shows it is exactly 4/3 (tight) for two agents on any biconnected graph, degrading to the number of pieces created by deleting a cut vertex when one exists.
  - *relevance:* The closest published quantitative bound on how much hard contiguity can cost a two-agent fair division, and it formally explains why a low-degree cut vertex is the worst topological case — matching the project's own empirically observed pre-existing-disconnection failure mode.

- **Deligkas et al. 2021** — Deligkas, A., Eiben, E., Ganian, R., Hamm, T. & Ordyniak, S. (2021). *The Parameterized Complexity of Connected Fair Division*. Proceedings of the Thirtieth International Joint Conference on Artificial Intelligence (IJCAI 2021). DOI: 10.24963/ijcai.2021/20
  - *establishes:* Proves that connected fair division (proportionality, envy-freeness, EF1, or EFX) is NP-hard even with exactly two agents and identical unit valuations, via a reduction from equitable connected graph partitioning, and that tractability additionally requires bounding the graph's clique-width or treewidth.
  - *relevance:* Shows restricting to two wholesalers does not by itself rescue tractability; ZCTA Rook grids have unbounded treewidth and clique-width, consistent with the project's finding that scale, not agent count, drives MILP failure.

- **Bouveret et al. 2019** — Bouveret, S., Cechlárová, K. & Lesca, J. (2019). *Chore division on a graph*. Autonomous Agents and Multi-Agent Systems. DOI: 10.1007/s10458-019-09415-z
  - *establishes:* Extends graph fair division to chores (disutility items), showing that goods and chores instances are not symmetric under a naive utility-sign flip.
  - *relevance:* Low direct relevance today since M_z/A_z/B_z are goods, but worth keeping in reserve if a future extension needs to treat undesirable, high-cost-to-serve ZCTAs as chores rather than low-value goods.

- **Igarashi 2023** — Igarashi, A. (2023). *How to Cut a Discrete Cake Fairly*. Proceedings of the AAAI Conference on Artificial Intelligence (AAAI 2023). DOI: 10.1609/aaai.v37i5.25705
  - *establishes:* Studies a hybrid discrete/continuous "discrete cake" model on a path where one item per cut point may be fractionally split between two agents, improving on pure-EF1 path results.
  - *relevance:* Low direct relevance since ZCTAs are genuinely indivisible, but useful as the boundary case that disappears once true indivisibility is enforced.

- **Igarashi & Zwicker 2023** — Igarashi, A. & Zwicker, W. S. (2023). *Fair division of graphs and of tangled cakes*. Mathematical Programming. DOI: 10.1007/s10107-023-01945-5
  - *establishes:* Connects discrete graph fair division to a continuous "tangled cake" built by gluing intervals per a graph structure, showing exactly the Hamiltonian ("stringable") graphs guarantee envy-free connected division for any number of agents.
  - *relevance:* A theoretically elegant bridge to the continuum optimal-transport literature (M. Warren 2025) on the other side of the same discrete/continuum divide; its open n≥3 conjecture is not an immediate concern for a strictly bilateral model but is useful vocabulary for a future leximin extension to three or more wholesalers.

- **Bei et al. 2025** — Bei, X., Igarashi, A., Lu, X. & Suksompong, W. (2025). *Dividing a Graphical Cake*. SIAM Journal on Discrete Mathematics. DOI: 10.1137/22m1500502
  - *establishes:* Generalizes graphical cake-cutting from paths/cycles to arbitrary graphs, where the cake is spread over a graph's edges or vertices, and shows proportionality with connected pieces is not always achievable on general graphs.
  - *relevance:* A continuum analogue of Bouveret et al. 2017's discrete impossibility results, establishing that graph topology beyond path/cycle is an active research frontier on the continuum side too.

## 5. Mechanism design and incentives

- **Vickrey 1961** — Vickrey, W. (1961). *Counterspeculation, Auctions, and Competitive Sealed Tenders*. The Journal of Finance 16(1), 8–37. DOI: 10.1111/j.1540-6261.1961.tb02789.x
  - *establishes:* The second-price sealed-bid auction is truthful (dominant-strategy incentive compatible) and revenue-equivalent to the English auction under independent private values.
  - *relevance:* The first mechanism shown to elicit private valuations truthfully; the template for any process that must extract honest self-reports (territory valuations, reservation prices, risk disclosures).

- **Hurwicz 1972** — Hurwicz, L. (1972). *On informationally decentralized systems*. In McGuire & Radner (eds.), Decision and Organization, North-Holland, 297–336. DOI: none (book chapter)
  - *establishes:* Defines incentive compatibility and shows that no informationally decentralised mechanism can be simultaneously Pareto-efficient, individually rational and incentive-compatible in classical exchange environments.
  - *relevance:* The origin of the incentive-compatibility constraint and of the recognition that a designer who does not know agents' preferences cannot in general achieve first-best allocations.

- **Clarke 1971** — Clarke, E. H. (1971). *Multipart pricing of public goods*. Public Choice 11, 17–33. DOI: 10.1007/BF01726210
  - *establishes:* The pivotal (Clarke) tax: charging each agent the externality they impose makes truthful reporting dominant in public-good decisions.
  - *relevance:* Half of the VCG family; the pivot-payment idea is what makes an allocation rule strategy-proof when self-reported valuations feed a shared decision.

- **Groves 1973** — Groves, T. (1973). *Incentives in Teams*. Econometrica 41(4), 617–631. DOI: 10.2307/1914085
  - *establishes:* The Groves class of transfer schemes under which every team member's dominant strategy is truthful reporting and the efficient decision is implemented.
  - *relevance:* Completes the Vickrey–Clarke–Groves mechanism; cite when an allocation model needs transfers that make truthful valuation reports optimal for every participant.

- **Gibbard 1973** — Gibbard, A. (1973). *Manipulation of Voting Schemes: A General Result*. Econometrica 41(4), 587–601. DOI: 10.2307/1914083
  - *establishes:* Every non-dictatorial voting scheme with at least three outcomes is manipulable.
  - *relevance:* Together with Satterthwaite 1975, the impossibility result that bounds what any strategy-proof committee or scoring rule can achieve when preferences are unrestricted.

- **Satterthwaite 1975** — Satterthwaite, M. A. (1975). *Strategy-proofness and Arrow's conditions: Existence and correspondence theorems for voting procedures and social welfare functions*. Journal of Economic Theory 10(2), 187–217. DOI: 10.1016/0022-0531(75)90050-2
  - *establishes:* Independently proves the Gibbard–Satterthwaite theorem and links strategy-proofness to Arrow's impossibility.
  - *relevance:* The companion citation to Gibbard 1973; use when the argument needs the correspondence between strategy-proof rules and Arrovian social welfare functions.

- **Myerson 1979** — Myerson, R. B. (1979). *Incentive Compatibility and the Bargaining Problem*. Econometrica 47(1), 61–73. DOI: 10.2307/1912346
  - *establishes:* The revelation principle: any equilibrium outcome of any mechanism can be replicated by a direct, incentive-compatible mechanism; applies it to bargaining under incomplete information.
  - *relevance:* Lets a modeller restrict attention to truthful direct mechanisms without loss, which is what makes mechanism-design problems (annuity menu design, allocation with private valuations) tractable optimisation problems.

- **Crawford & Varian 1979** — Crawford, V. P. & Varian, H. R. (1979). *Distortion of preferences and the Nash theory of bargaining*. Economics Letters. DOI: 10.1016/0165-1765(79)90118-6
  - *establishes:* Agents can gain by misrepresenting preferences to a bargaining solution; the Nash solution is manipulable.
  - *relevance:* The reference for reporting incentives. Matters only where inputs are self-reported; cite to establish that the question was considered.

- **Myerson 1981** — Myerson, R. B. (1981). *Optimal Auction Design*. Mathematics of Operations Research 6(1), 58–73. DOI: 10.1287/moor.6.1.58
  - *establishes:* Characterises revenue-maximising auctions via virtual valuations; revenue equivalence and optimal reserve prices follow.
  - *relevance:* The canonical solved mechanism-design problem; its virtual-value machinery is the same one used for optimal nonlinear pricing and screening menus in insurance.

- **Milgrom & Weber 1982** — Milgrom, P. R. & Weber, R. J. (1982). *A Theory of Auctions and Competitive Bidding*. Econometrica 50(5), 1089–1122. DOI: 10.2307/1911865
  - *establishes:* Affiliated-values auction theory: the linkage principle and the revenue ranking English ≥ second-price ≥ first-price when signals are affiliated.
  - *relevance:* Governs bidding when participants' information is correlated (competing reinsurers or block-acquisition bidders valuing the same book); explains why disclosing information raises expected price.

- **Myerson & Satterthwaite 1983** — Myerson, R. B. & Satterthwaite, M. A. (1983). *Efficient mechanisms for bilateral trading*. Journal of Economic Theory 29(2), 265–281. DOI: 10.1016/0022-0531(83)90048-0
  - *establishes:* No mechanism for bilateral trade under two-sided private information is simultaneously efficient, incentive-compatible, individually rational and budget-balanced.
  - *relevance:* The impossibility that bounds any two-party negotiation over a block of business, a territory, or a reinsurance treaty when each side privately knows its own value.

- **Maskin 1999** — Maskin, E. (1999). *Nash Equilibrium and Welfare Optimality*. Review of Economic Studies 66(1), 23–38. DOI: 10.1111/1467-937X.00076
  - *establishes:* Maskin monotonicity is necessary, and with no-veto-power and n≥3 sufficient, for a social choice rule to be Nash-implementable (circulated 1977).
  - *relevance:* Tells a designer which allocation rules can be implemented at all when agents play strategically rather than report truthfully; the test to apply before proposing a rule to a committee that will be gamed.

## 6. Decision theory under risk and uncertainty

- **von Neumann & Morgenstern 1944** — von Neumann, J. & Morgenstern, O. (1944). *Theory of Games and Economic Behavior*. Princeton University Press. DOI: none (book). ISBN 978-0-691-13061-3 (60th-anniversary ed.)
  - *establishes:* The expected-utility axioms (completeness, transitivity, continuity, independence) and the representation theorem, plus the foundation of cooperative n-person game theory via characteristic functions.
  - *relevance:* Expected utility is the default preference model behind every annuity-valuation, life-cycle and risk-pricing result in section 7; departures from it (sections below) are measured against these axioms.

- **Allais 1953** — Allais, M. (1953). *Le Comportement de l'Homme Rationnel devant le Risque: Critique des Postulats et Axiomes de l'École Américaine*. Econometrica 21(4), 503–546. DOI: 10.2307/1907921
  - *establishes:* The Allais paradox: systematic choices violating the independence axiom of expected utility.
  - *relevance:* The original evidence that retirees' preferences over guaranteed versus risky income streams need not obey expected utility; motivates the non-EU models below.

- **Savage 1954** — Savage, L. J. (1954). *The Foundations of Statistics*. Wiley (Dover reprint 1972). DOI: none (book). ISBN 978-0-486-62349-8
  - *establishes:* Subjective expected utility: from axioms on preferences over acts, derives a unique subjective probability and a utility function such that preferences maximise expected utility.
  - *relevance:* Justifies treating individual beliefs about longevity or market returns as probabilities to be elicited and used in a decision model; the benchmark that ambiguity models relax.

- **Ellsberg 1961** — Ellsberg, D. (1961). *Risk, Ambiguity, and the Savage Axioms*. The Quarterly Journal of Economics 75(4), 643–669. DOI: 10.2307/1884324
  - *establishes:* Ambiguity aversion: people prefer known to unknown probabilities in ways no single subjective prior can rationalise.
  - *relevance:* Explains reluctance to buy products whose payoff depends on poorly understood probabilities (own longevity, insurer solvency); the empirical trigger for Gilboa–Schmeidler and Schmeidler 1989.

- **Anscombe & Aumann 1963** — Anscombe, F. J. & Aumann, R. J. (1963). *A Definition of Subjective Probability*. The Annals of Mathematical Statistics 34(1), 199–205. DOI: 10.1214/aoms/1177704255
  - *establishes:* A tractable subjective-expected-utility axiomatisation using objective lotteries as a measuring device, simpler than Savage's.
  - *relevance:* The framework in which most ambiguity models (maxmin, Choquet, smooth ambiguity) are stated; cite when writing preferences over uncertain retirement outcomes formally.

- **Pratt 1964** — Pratt, J. W. (1964). *Risk Aversion in the Small and in the Large*. Econometrica 32(1/2), 122–136. DOI: 10.2307/1913738
  - *establishes:* The Arrow–Pratt coefficients of absolute and relative risk aversion, and their equivalence to comparative risk premia.
  - *relevance:* The parameter every annuity-demand and portfolio model calibrates; the language for stating how much guaranteed income a client would trade for expected return.

- **Rothschild & Stiglitz 1970** — Rothschild, M. & Stiglitz, J. E. (1970). *Increasing risk: I. A definition*. Journal of Economic Theory 2(3), 225–243. DOI: 10.1016/0022-0531(70)90038-4
  - *establishes:* Mean-preserving spreads, second-order stochastic dominance and the equivalence of three definitions of "riskier".
  - *relevance:* The dominance criterion for comparing retirement-income distributions without committing to a utility function; the correct tool for ranking product designs by risk alone.

- **Kahneman & Tversky 1979** — Kahneman, D. & Tversky, A. (1979). *Prospect Theory: An Analysis of Decision under Risk*. Econometrica 47(2), 263–291. DOI: 10.2307/1914185
  - *establishes:* Reference-dependent value function with loss aversion and diminishing sensitivity, plus nonlinear probability weighting; explains the certainty and reflection effects.
  - *relevance:* The behavioural model behind framing effects in annuity uptake (Brown et al. 2008) and loss-averse responses to product guarantees; needed whenever demand is modelled as it is rather than as it should be.

- **Gilboa & Schmeidler 1989** — Gilboa, I. & Schmeidler, D. (1989). *Maxmin expected utility with non-unique prior*. Journal of Mathematical Economics 18(2), 141–153. DOI: 10.1016/0304-4068(89)90018-9
  - *establishes:* Axiomatises maxmin expected utility: preferences evaluate acts by the minimum expected utility over a set of priors.
  - *relevance:* The standard model for robust decision-making under model uncertainty (longevity-trend, return-distribution ambiguity); the theoretical basis of worst-case pricing and of ambiguity-driven under-annuitisation.

- **Schmeidler 1989** — Schmeidler, D. (1989). *Subjective Probability and Expected Utility without Additivity*. Econometrica 57(3), 571–587. DOI: 10.2307/1911053
  - *establishes:* Choquet expected utility: preferences represented by integration against a non-additive capacity, with uncertainty aversion as convexity of the capacity.
  - *relevance:* The companion to maxmin EU; the capacity representation maps directly onto distortion risk measures used in insurance pricing.

- **Epstein & Zin 1989** — Epstein, L. G. & Zin, S. E. (1989). *Substitution, Risk Aversion, and the Temporal Behavior of Consumption and Asset Returns: A Theoretical Framework*. Econometrica 57(4), 937–969. DOI: 10.2307/1913778
  - *establishes:* Recursive utility separating risk aversion from the elasticity of intertemporal substitution, which time-additive expected utility conflates.
  - *relevance:* The preference specification for life-cycle retirement models where willingness to smooth consumption over time and willingness to bear risk must be calibrated independently.

- **Tversky & Kahneman 1992** — Tversky, A. & Kahneman, D. (1992). *Advances in prospect theory: Cumulative representation of uncertainty*. Journal of Risk and Uncertainty 5, 297–323. DOI: 10.1007/BF00122574
  - *establishes:* Cumulative prospect theory: rank-dependent probability weighting that respects stochastic dominance, with the standard parameter estimates.
  - *relevance:* The version of prospect theory used in calibrated models of insurance and annuity demand; supplies the weighting-function parameters to plug into a behavioural demand model.

## 7. Insurance and annuity economics

- **Borch 1962** — Borch, K. (1962). *Equilibrium in a Reinsurance Market*. Econometrica 30(3), 424–444. DOI: 10.2307/1909887
  - *establishes:* Pareto-optimal risk sharing among risk-averse insurers equalises marginal rates of substitution across states; optimal reinsurance treaties are functions of aggregate loss only.
  - *relevance:* The foundational result for allocating pooled risk among carriers or between an insurer and its reinsurer; the risk-sharing analogue of the core.

- **Arrow 1963** — Arrow, K. J. (1963). *Uncertainty and the Welfare Economics of Medical Care*. American Economic Review 53(5), 941–973. DOI: unresolved (AER 1963 issue not registered in Crossref; JSTOR stable 1812044)
  - *establishes:* Moral hazard, adverse selection and the optimality of deductible-plus-full-coverage-above-deductible contracts under proportional loading.
  - *relevance:* The origin of the informational-asymmetry framing of insurance markets and of the optimal-deductible result that structures product design.

- **Yaari 1965** — Yaari, M. E. (1965). *Uncertain Lifetime, Life Insurance, and the Theory of the Consumer*. The Review of Economic Studies 32(2), 137–150. DOI: 10.2307/2296058
  - *establishes:* With uncertain lifetime, actuarially fair annuities and no bequest motive, a consumer should annuitise all wealth.
  - *relevance:* The full-annuitisation benchmark against which every "annuity puzzle" and every product-design question is posed.

- **Mossin 1968** — Mossin, J. (1968). *Aspects of Rational Insurance Purchasing*. Journal of Political Economy 76(4), 553–568. DOI: 10.1086/259427
  - *establishes:* A risk-averse expected-utility maximiser buys full coverage at an actuarially fair premium and partial coverage under positive loading; insurance is an inferior good under DARA.
  - *relevance:* The demand-side workhorse for coverage-level decisions; the comparative statics used when pricing loadings against expected take-up.

- **Merton 1969** — Merton, R. C. (1969). *Lifetime Portfolio Selection under Uncertainty: The Continuous-Time Case*. The Review of Economics and Statistics 51(3), 247–257. DOI: 10.2307/1926560
  - *establishes:* Continuous-time optimal consumption and portfolio rules; with CRRA utility the risky share is constant in wealth.
  - *relevance:* The dynamic-programming template for lifetime retirement models into which longevity risk and annuities are later inserted (Milevsky–Young and successors).

- **Rothschild & Stiglitz 1976** — Rothschild, M. & Stiglitz, J. E. (1976). *Equilibrium in Competitive Insurance Markets: An Essay on the Economics of Imperfect Information*. The Quarterly Journal of Economics 90(4), 629–649. DOI: 10.2307/1885326
  - *establishes:* With hidden risk types, competitive equilibrium (if it exists) is separating: high risks get full coverage, low risks are rationed; pooling equilibria never exist.
  - *relevance:* The screening model for any menu of insurance or annuity contracts sold to a population of heterogeneous, privately informed buyers; determines when a single price is unsustainable.

- **Mitchell, Poterba, Warshawsky & Brown 1999** — Mitchell, O. S., Poterba, J. M., Warshawsky, M. J. & Brown, J. R. (1999). *New Evidence on the Money's Worth of Individual Annuities*. American Economic Review 89(5), 1299–1318. DOI: 10.1257/aer.89.5.1299
  - *establishes:* The money's-worth ratio methodology; US annuities in the 1990s returned roughly 80–85 cents per premium dollar to the population, more to annuitant mortality, and still raised utility for plausible risk aversion.
  - *relevance:* The standard empirical valuation method for annuity pricing and the first quantitative statement of how much loading a rational buyer will tolerate.

- **Finkelstein & Poterba 2004** — Finkelstein, A. & Poterba, J. (2004). *Adverse Selection in Insurance Markets: Policyholder Evidence from the U.K. Annuity Market*. Journal of Political Economy 112(1), 183–208. DOI: 10.1086/379936
  - *establishes:* Annuitants self-select on contract features (back-loading, guarantee periods) in ways correlated with mortality, confirming adverse selection on product characteristics, not only on the annuitise/don't decision.
  - *relevance:* The empirical warrant for modelling selection into product features when designing an annuity menu; the Rothschild–Stiglitz mechanism observed in the field.

- **Davidoff, Brown & Diamond 2005** — Davidoff, T., Brown, J. R. & Diamond, P. A. (2005). *Annuities and Individual Welfare*. American Economic Review 95(5), 1573–1590. DOI: 10.1257/000282805775014281
  - *establishes:* Yaari's full-annuitisation result survives incomplete markets, non-expected-utility preferences and bequests as long as annuities pay more than bonds; substantial annuitisation remains optimal under far weaker assumptions.
  - *relevance:* Rules out most preference-based explanations of low annuity demand, pushing the explanation toward market frictions, pricing and behaviour; the current theoretical baseline.

- **Brown, Kling, Mullainathan & Wrobel 2008** — Brown, J. R., Kling, J. R., Mullainathan, S. & Wrobel, M. V. (2008). *Why Don't People Insure Late-Life Consumption? A Framing Explanation of the Under-Annuitization Puzzle*. American Economic Review 98(2), 304–309. DOI: 10.1257/aer.98.2.304
  - *establishes:* Presenting an annuity in a consumption frame makes it attractive to a majority; presenting it in an investment frame reverses the preference.
  - *relevance:* Direct evidence that annuity demand is a function of presentation, so product-design and communication choices belong in the demand model.

- **Benartzi, Previtero & Thaler 2011** — Benartzi, S., Previtero, A. & Thaler, R. H. (2011). *Annuitization Puzzles*. Journal of Economic Perspectives 25(4), 143–164. DOI: 10.1257/jep.25.4.143
  - *establishes:* Surveys rational and behavioural explanations of low annuitisation and shows plan design (defaults, framing, availability of partial annuitisation) drives observed annuity choices.
  - *relevance:* The accessible synthesis to hand a product committee; the reference for treating annuity uptake as a design variable rather than a preference parameter.

## 8. Matching and market design

- **Gale & Shapley 1962** — Gale, D. & Shapley, L. S. (1962). *College Admissions and the Stability of Marriage*. The American Mathematical Monthly 69(1), 9–15. DOI: 10.1080/00029890.1962.11989827
  - *establishes:* A stable matching always exists in two-sided markets and the deferred-acceptance algorithm finds one that is optimal for the proposing side.
  - *relevance:* The existence-and-algorithm result for assigning agents to accounts, advisors to clients, or territories to distributors when both sides have preferences and no transfers are used.

- **Shapley & Shubik 1971** — Shapley, L. S. & Shubik, M. (1971). *The assignment game I: The core*. International Journal of Game Theory 1, 111–130. DOI: 10.1007/BF01753437
  - *establishes:* In two-sided markets with transferable utility the core is non-empty, equals the set of competitive-equilibrium price vectors, and has a lattice structure with buyer- and seller-optimal points.
  - *relevance:* The transferable-utility counterpart of Gale–Shapley; the model for territory-for-compensation exchanges where side payments are allowed, and the bridge between the core (section 3) and prices.

- **Shapley & Scarf 1974** — Shapley, L. S. & Scarf, H. (1974). *On cores and indivisibility*. Journal of Mathematical Economics 1(1), 23–37. DOI: 10.1016/0304-4068(74)90033-0
  - *establishes:* The housing market with indivisible goods and no money has a non-empty core, found by Gale's top-trading-cycles algorithm.
  - *relevance:* The canonical model of reallocating indivisible items (accounts, territories) among agents who each hold one, without transfers; TTC is the strategy-proof way to run such a swap.

- **Hylland & Zeckhauser 1979** — Hylland, A. & Zeckhauser, R. (1979). *The Efficient Allocation of Individuals to Positions*. Journal of Political Economy 87(2), 293–314. DOI: 10.1086/260757
  - *establishes:* A pseudo-market with artificial budgets yields an ex-ante efficient random assignment of individuals to positions without real money.
  - *relevance:* The mechanism for allocating indivisible positions fairly by lottery when transfers are prohibited; the origin of the "competitive equilibrium from equal incomes" idea used in modern fair division.

- **Roth 1982** — Roth, A. E. (1982). *The Economics of Matching: Stability and Incentives*. Mathematics of Operations Research 7(4), 617–628. DOI: 10.1287/moor.7.4.617
  - *establishes:* No stable matching mechanism is strategy-proof for both sides; deferred acceptance is strategy-proof for the proposing side only.
  - *relevance:* Sets the incentive limits of any stable assignment process and identifies which side of a two-sided allocation can safely be asked for truthful preferences.

- **Kelso & Crawford 1982** — Kelso, A. S. & Crawford, V. P. (1982). *Job Matching, Coalition Formation, and Gross Substitutes*. Econometrica 50(6), 1483–1504. DOI: 10.2307/1913392
  - *establishes:* Under the gross-substitutes condition a salary-adjustment (tâtonnement) process converges to a core allocation in many-to-one matching with wages, unifying Gale–Shapley and the assignment game.
  - *relevance:* Gross substitutes is the condition under which a decentralised price or salary process finds a stable allocation; the property to check before assuming a market for territories or accounts clears.

- **Roth 1984** — Roth, A. E. (1984). *The Evolution of the Labor Market for Medical Interns and Residents: A Case Study in Game Theory*. Journal of Political Economy 92(6), 991–1016. DOI: 10.1086/261272
  - *establishes:* The NRMP's clearinghouse is a deferred-acceptance algorithm and its stability explains why it survived where unstable predecessors unravelled.
  - *relevance:* The first field demonstration that stability, not efficiency alone, determines whether an allocation mechanism persists; an argument to make when proposing a matching process to an organisation.

- **Roth & Sotomayor 1990** — Roth, A. E. & Sotomayor, M. A. O. (1990). *Two-Sided Matching: A Study in Game-Theoretic Modeling and Analysis*. Cambridge University Press (Econometric Society Monographs 18). DOI: 10.1017/CCOL052139015X
  - *establishes:* The unified treatment of matching with and without money: stability, lattice structure, incentives and the assignment game.
  - *relevance:* The reference for any standard matching result when no original paper needs to be named.

- **Roth 2002** — Roth, A. E. (2002). *The Economist as Engineer: Game Theory, Experimentation, and Computation as Tools for Design Economics*. Econometrica 70(4), 1341–1378. DOI: 10.1111/1468-0262.00335
  - *establishes:* Frames market design as engineering: theory, experiments and computation must jointly handle the details (unravelling, congestion, safety of participation) that determine whether a mechanism works in practice.
  - *relevance:* The methodological charter for building an allocation or pricing mechanism that will be deployed, not just analysed.

- **Abdulkadiroğlu & Sönmez 2003** — Abdulkadiroğlu, A. & Sönmez, T. (2003). *School Choice: A Mechanism Design Approach*. American Economic Review 93(3), 729–747. DOI: 10.1257/000282803322157061
  - *establishes:* Recasts school choice as a one-sided matching problem with priorities and proposes deferred acceptance and top-trading-cycles as strategy-proof alternatives to the manipulable Boston mechanism.
  - *relevance:* The model for allocating scarce slots among agents with priorities but no transfers, and the standard evidence that a plausible-looking mechanism can be badly manipulable.

- **Hatfield & Milgrom 2005** — Hatfield, J. W. & Milgrom, P. R. (2005). *Matching with Contracts*. American Economic Review 95(4), 913–935. DOI: 10.1257/0002828054825466
  - *establishes:* Generalises matching to contracts with terms; under substitutable preferences stable allocations exist and form a lattice, unifying Gale–Shapley, Kelso–Crawford and package auctions.
  - *relevance:* The framework when the object matched is a contract with negotiable terms (territory plus compensation schedule, advisor plus commission structure) rather than a bare assignment.
