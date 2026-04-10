Theoretical Foundations Report for **Decoupled Safety (解耦安全)**

Abstract

This report gives a formal theoretical basis for **Decoupled Safety**: instead of asking whether a trillion-parameter stochastic black box is _globally safe_, we move safety to an **external, decidable, compositional constraint layer**. The move is not cosmetic. It is forced by computability. Classical computability theory shows that every non-trivial _semantic/extensional_ property of programs is undecidable by a reduction from the Halting Problem via Rice-style arguments. For probabilistic programs, the known hardness results are sharper: for the probabilistic guarded command language, deciding almost-sure termination is -complete, universal almost-sure termination is also -complete, positive almost-sure termination is -complete, and universal positive almost-sure termination is -complete; for richer imperative languages with bounded nondeterminism and rational variables, positive almost-sure termination is -complete. This places “implicit global safety verification” well beyond the tractable verification frontier. [[link.springer.com]](https://link.springer.com/chapter/10.1007/978-3-319-27889-6_9), [[Rice's the...University]](http://kilby.stanford.edu/~rvg/154/handouts/Rice.html), [[cs.cornell.edu]](https://www.cs.cornell.edu/kozen/Papers/ProbSem.pdf), [[cambridge.org]](https://www.cambridge.org/core/books/foundations-of-probabilistic-programming/semantics-of-probabilistic-programming-a-gentle-introduction/A7964205E44B5234A78C661192E294E1), [[link.springer.com]](https://link.springer.com/article/10.1007/s00236-018-0321-1), [[x-mol.com]](https://www.x-mol.com/paper/1717637755687424000), [[colab.ws]](https://colab.ws/articles/10.1145%2F3704899), [[popl24.sigplan.org]](https://popl24.sigplan.org/details/POPL-2024-popl-research-papers/39/Positive-Almost-Sure-Termination-Complexity-and-Proof-Rules)

By contrast, once safety is externalized as a decidable predicate or as a stepwise safety filter , **instance-level verification** collapses to the complexity of evaluating . If , then candidate checking is polynomial-time; if the branching factor per step is polynomially bounded, tokenwise projection into the safe set is also polynomial-time. In dynamic settings, discrete-time control barrier functions (DCBFs) provide a mathematically clean route to runtime invariance: if with , then the safe superlevel set remains forward invariant. This is precisely the kind of runtime guarantee that cannot be extracted from a globally opaque aligned model. [[ece.ualberta.ca]](https://www.ece.ualberta.ca/~tbs/pmwiki/pdf/IEEE-T-Cybenetics-Xiong-2022.pdf), [[ieeexplore.ieee.org]](https://ieeexplore.ieee.org/document/9777251)

Finally, safety in composite agent systems is **not** compositional in general. Recent work explicitly formalizes non-compositionality under conjunctive capability dependencies using hypergraph-style dependency structures, and empirical multi-agent work also documents the tradeoff between collaboration and security. We make that precise with a safety algebra: outputs lie in a risk poset, decoupled safety operators are monotone risk-non-increasing morphisms, sequential composition is closed, and logical conflict is resolved by a semilattice meet with an absorbing fail-safe bottom . Thus Decoupled Safety is not merely “better engineering”; it is the computationally necessary escape route from global undecidability toward local decidability and compositional control. [[arxiv.org]](https://arxiv.org/pdf/2603.15973v1), [[ojs.aaai.org]](https://ojs.aaai.org/index.php/AAAI/article/view/34970), [[ncatlab.org]](https://ncatlab.org/nlab/show/semilattice), [[ncatlab.org]](https://ncatlab.org/nlab/show/lattice)

* * *

1. Preliminaries

Definition 1 (Probabilistic program semantics)

Let be the input space and the output space. We model an LLM-like system as a probabilistic program

where is the set of probability distributions on . This abstraction is consistent with the denotational tradition in probabilistic programming: probabilistic programs map inputs/states to probability distributions over outputs/states. [[cs.cornell.edu]](https://www.cs.cornell.edu/kozen/Papers/ProbSem.pdf), [[research.ibm.com]](https://research.ibm.com/publications/semantics-of-probabilistic-programs--1), [[cambridge.org]](https://www.cambridge.org/core/books/foundations-of-probabilistic-programming/semantics-of-probabilistic-programming-a-gentle-introduction/A7964205E44B5234A78C661192E294E1)Definition 2 (Extensional/global safety property)

A property of programs is **extensional** if it depends only on the input-output behavior of the program and not on syntactic presentation; formally,

where means equality of the induced partial stochastic kernel / output distribution semantics on all inputs.

A safety property is **non-trivial** if there exist programs such that and .Definition 3 (Implicit vs. decoupled safety)

* **Implicit safety**: safety is claimed as a global semantic property of itself.
* **Decoupled safety**: safety is checked by a distinct operator , typically over individual instances or prefixes:

* * *

2. Pillar 1 — The Impossibility Theorem of Global Implicit Safety (定理 1)

Definition 4 (Global implicit safety verification problem)

Given a probabilistic program , decide whether

where is any non-trivial extensional safety property.

Typical examples include:

1. “For every input, unsafe output is impossible.”
2. “For every input, the probability of unsafe output is zero.”
3. “For every input, the execution almost surely terminates before any unsafe emission.”

These are semantic properties. Rice’s theorem states that every non-trivial semantic property of programs is undecidable. Standard proofs proceed by reduction from Halting. [[link.springer.com]](https://link.springer.com/chapter/10.1007/978-3-319-27889-6_9), [[handwiki.org]](https://handwiki.org/wiki/Rice's%20theorem), [[ai.dmi.unibas.ch]](https://ai.dmi.unibas.ch/_files/teaching/fs25/theo/slides/theory-c06-handout4.pdf), [[Rice's the...University]](http://kilby.stanford.edu/~rvg/154/handouts/Rice.html)

* * *

Theorem 1 (Undecidability of global implicit safety)

Let be any non-trivial extensional safety property over the semantics of probabilistic programs. Then the decision problem

is undecidable.

### Proof

Because is non-trivial, there exist probabilistic programs and such that

Take an arbitrary instance of the Halting Problem. Construct a probabilistic program as follows.

On any input :

1. Simulate on .
2. If halts, then execute on .
3. If does not halt, then diverge forever (or equivalently never reach the unsafe branch), or execute in the branch conditioned on non-halting.

By construction, the **semantic behavior** of is extensionally equivalent to:

* if halts;
* (or a semantically fixed safe behavior) if does not halt.

Therefore,

or equivalently

depending on which branch is assigned to the safe/unsafe witness.

Hence a decider for would decide the Halting Problem, contradiction.

* * *

Corollary 1.1 (No complete verifier for global implicit safety)

There is no algorithm that, given an arbitrary Turing-complete probabilistic model , always halts and correctly decides whether is globally safe in any non-trivial semantic sense.

* * *

Lemma 1.2 (Expectation bounds do not imply hard safety)

Let be a safety-loss random variable under . If

then in general this does **not** imply

### Proof

Take a Bernoulli random variable with , , and set . Then , yet

Thus expected loss control bounds average badness but does not eliminate bad events pointwise.

### Interpretation

This is the formal gap between **statistical alignment** and **hard safety**: optimizing expected reward or expected harmlessness can suppress average risk, but it does not prove support-level exclusion of unsafe behaviors. That is exactly why expectation-based implicit alignment remains heuristic rather than a source of formal guarantees.

* * *

Theorem 1.3 (Known hierarchy placements for probabilistic termination proxies)

For the probabilistic guarded command language studied by Kaminski–Katoen–Matheja:

* deciding almost-sure termination is -complete;
* deciding universal almost-sure termination is -complete;
* deciding positive almost-sure termination is -complete;
* deciding universal positive almost-sure termination is -complete. [[link.springer.com]](https://link.springer.com/article/10.1007/s00236-018-0321-1)

For a richer imperative language with rational variables, bounded nondeterministic choice, and discrete probabilistic choice, Majumdar–Sathiyanarayana show that positive almost-sure termination is -complete, while almost-sure termination remains arithmetical and bounded termination is -complete. [[colab.ws]](https://colab.ws/articles/10.1145%2F3704899), [[popl24.sigplan.org]](https://popl24.sigplan.org/details/POPL-2024-popl-research-papers/39/Positive-Almost-Sure-Termination-Complexity-and-Proof-Rules)

### Consequence

Even before asking for “global harmlessness”, **natural probabilistic safety proxies** already sit at , , , and even depending on the language model. Therefore, the verification task for implicit global safety is not merely “hard in practice”; it is formally outside the tractable decidable core. [[link.springer.com]](https://link.springer.com/article/10.1007/s00236-018-0321-1), [[colab.ws]](https://colab.ws/articles/10.1145%2F3704899), [[popl24.sigplan.org]](https://popl24.sigplan.org/details/POPL-2024-popl-research-papers/39/Positive-Almost-Sure-Termination-Complexity-and-Proof-Rules)

* * *

3. Pillar 2 — Decoupled Verifiability and Constraint Projection (定理 2)

The escape hatch is to stop verifying globally and instead verify **instances** against an external constraint layer.Definition 5 (Decoupled safety predicate)

A decoupled safety operator is a predicate

or in autoregressive form

where is the current prefix and the candidate action/token set at time .

The safe set induced by is

* * *

Theorem 2 (Instance-level verification collapses to polynomial time)

Assume , i.e. there exists a polynomial such that can be decided in time . Then **instance-level verifiability**

belongs to .

### Proof

Immediate: on input , run the decider for . By assumption its runtime is polynomial in the input size. Hence .

* * *

Corollary 2.1 (Tokenwise safe filtering is polynomial-time)

Suppose the model emits at step a polynomial-size candidate set , where , and . Then the safe filtered set

can be computed in polynomial time by scanning all candidates.

### Proof

Evaluate on each candidate. Total cost:

which is polynomial because is polynomially bounded.

* * *

Remark (important scope condition)

The theorem above is intentionally about **verification** and **bounded-branch projection**, not arbitrary global nearest-point projection onto an implicitly represented language of outputs. Exact projection onto a globally specified combinatorial safe language can be NP-hard or worse. What drops to is the decoupled **instance-checking problem**, and, under bounded candidate branching, the corresponding **runtime safety filter**.

That distinction is the right one for safety kernels: they verify and filter at runtime; they do not solve arbitrary offline synthesis problems.

* * *

Definition 6 (Constraint projection)

For an output , define a projection operator

for a chosen metric .

For autoregressive decoding, a one-step projection is

or, in score form,

where are token scores/logits after restriction to the safe candidate set.

* * *

Dynamic model and barrier-based safety

Probabilistic programs and token emitters are dynamical systems. The DCBF literature treats safety as forward invariance of a safe set for discrete-time systems. The cited work explicitly states that discrete-time control barrier functions are used to guarantee forward invariance of a safe set for discrete-time systems, including high relative degree cases. [[ece.ualberta.ca]](https://www.ece.ualberta.ca/~tbs/pmwiki/pdf/IEEE-T-Cybenetics-Xiong-2022.pdf), [[ieeexplore.ieee.org]](https://ieeexplore.ieee.org/document/9777251)Definition 7 (Discrete-time safety set)

Let

be the latent/state evolution, and let

be the safe set.

* * *

Theorem 2.2 (DCBF forward invariance)

Let . Suppose that for all ,

If , i.e. , then for all . In other words, is forward invariant.

### Proof

Rearrange:

Since , we have . If , then

By induction from , it follows that for all . Hence for all .

* * *

Corollary 2.3 (Runtime safety shield)

If the latent dynamics of token generation are monitored by a DCBF certificate and the emitted token/action is chosen from a decoupled safe candidate set, then safety is enforced by an **external runtime invariant** rather than by any unverifiable global claim about the model parameters.

* * *

4. Pillar 3 — Safety Algebra and Composability Proofs (定理 3)

The third pillar addresses a core systems fact: even if components look “safe” in isolation, composition can create unsafe capability combinations.

Recent formal work states exactly this central claim: safety is non-compositional in the presence of conjunctive capability dependencies, and directed hypergraphs are the right structure because AND-semantics cannot be represented faithfully by pairwise graphs without artifacts. Separate empirical work on multi-agent systems shows that defenses may reduce malicious spread but often tax collaboration capability. [[arxiv.org]](https://arxiv.org/pdf/2603.15973v1), [[ojs.aaai.org]](https://ojs.aaai.org/index.php/AAAI/article/view/34970)

We now give a crisp algebraic proof.

* * *

Definition 8 (Capability hypergraph)

A capability system is a directed hypergraph

where each hyperedge has the form

and can fire only if **all** capabilities in are present.

Given initial capability set , define the closure as the least fixed point generated by repeatedly applying:

Let be the forbidden capability set.

A subsystem with seed is safe iff

* * *

Theorem 3.1 (Implicit safety is non-compositional)

There exist seed sets such that

but

### Proof

Take

and a single hyperedge

Let

Then because , so the hyperedge cannot fire; similarly . Hence both are safe in isolation.

But

so the hyperedge fires and yields . Therefore

hence .

* * *

Corollary 3.2

Component-wise implicit safety certificates do **not** compose under conjunctive dependencies. Therefore, “each agent was aligned in isolation” is not a theorem about the composite system.

This is the formal reason agent safety must be externalized at the interface/composition layer.

* * *

Safety algebra

We now build a compositional alternative.Definition 9 (Risk poset)

Let be a poset of risk levels, where

means “ is no more risky than .” Thus lower elements are safer. Let be the least element, interpreted as **fail-safe**.

If finite meets exist, is a meet-semilattice. Standard order-theoretic references identify semilattices/lattices with posets having finite meets/joins, and bounded semilattices with a distinguished bottom/top element. [[ncatlab.org]](https://ncatlab.org/nlab/show/semilattice), [[ncatlab.org]](https://ncatlab.org/nlab/show/lattice)

* * *

Definition 10 (Decoupled safety operator)

A decoupled safety operator is a map

such that:

1. **Monotonicity**:

2. **Safety-preservation / risk non-increase**:

Interpretation: never makes risk worse.

* * *

Theorem 3.3 (Closure under sequential composition)

If are monotone and safety-preserving, then is also monotone and safety-preserving.

### Proof

Monotonicity:if , then , and then by monotonicity of ,

Safety-preservation:since , by monotonicity of ,

Since , transitivity yields

Thus is monotone and safety-preserving.

* * *

Corollary 3.4

The class of decoupled safety operators is strictly closed under sequential composition.

This is the compositional property implicit safety lacks.

* * *

Definition 11 (Pointwise conflict meet)

For two decoupled safety operators , define

provided is a meet-semilattice.

* * *

Theorem 3.5 (Semilattice of safety operators)

Let be a meet-semilattice. Then the set

is closed under the pointwise meet operation.

### Proof

Let . Since meet in a semilattice is monotone in each argument, and are monotone, is monotone. Also,

So .

* * *

Definition 12 (Fail-safe bottom operator)

Define the constant operator

* * *

Theorem 3.6 (Absorbing fail-safe)

The operator is monotone and safety-preserving. Moreover, for any ,

and pointwise,

### Proof

Monotonicity is immediate because is constant. Safety-preservation holds because for all . Composition:

but is least, so . Likewise . Finally,

* * *

Corollary 3.7 (Graceful degradation)

If conflicting safety constraints have no non-bottom common refinement, the algebra converges to . Thus irresolvable conflict is not a logical contradiction that breaks the system; it is a **forced fail-safe outcome**.

That is the mathematical form of graceful degradation.

* * *

5. Why implicit safety collapses, and decoupled safety does not

We can now state the main synthesis.Main Synthesis Theorem

Let be a Turing-complete probabilistic program used as a foundation model.

1. Any non-trivial **global semantic** safety property is undecidable by Rice-style reduction.
2. Concrete probabilistic safety proxies already inhabit high recursion-theoretic classes: AST is -complete in the pGCL setting, while richer positive almost-sure termination problems can be -complete.
3. An external decoupled safety predicate makes **instance-level verification polynomial-time**.
4. A DCBF condition yields runtime forward invariance of the safe set.
5. Safety-preserving external operators compose algebraically; implicit safety of components does not. [[Rice's the...University]](http://kilby.stanford.edu/~rvg/154/handouts/Rice.html), [[link.springer.com]](https://link.springer.com/article/10.1007/s00236-018-0321-1), [[colab.ws]](https://colab.ws/articles/10.1145%2F3704899), [[popl24.sigplan.org]](https://popl24.sigplan.org/details/POPL-2024-popl-research-papers/39/Positive-Almost-Sure-Termination-Complexity-and-Proof-Rules), [[ece.ualberta.ca]](https://www.ece.ualberta.ca/~tbs/pmwiki/pdf/IEEE-T-Cybenetics-Xiong-2022.pdf), [[arxiv.org]](https://arxiv.org/pdf/2603.15973v1), [[ojs.aaai.org]](https://ojs.aaai.org/index.php/AAAI/article/view/34970), [[ncatlab.org]](https://ncatlab.org/nlab/show/semilattice), [[ncatlab.org]](https://ncatlab.org/nlab/show/lattice)

### Conclusion

Therefore the move from **Implicit Safety** to **Decoupled Safety** is not merely an engineering preference. It is a **computational necessity**. Implicit safety asks us to decide non-trivial global semantic facts about a probabilistic Turing-complete program, which is blocked by undecidability and, in probabilistic refinements, by high levels of the arithmetical/analytical hierarchies. Decoupled safety instead relocates the verification problem to the instance level, where membership checks can be made decidable—and, under explicit assumptions, polynomial-time—and where runtime invariants and compositional algebra become available. This is how one escapes the bounds of global undecidability without pretending the black box has become formally transparent.

* * *

References (selected, real literature)

1. Dexter Kozen, **“Semantics of Probabilistic Programs”** (JCSS 1981): probabilistic programs as denotational mappings to distributions / operators. [[cs.cornell.edu]](https://www.cs.cornell.edu/kozen/Papers/ProbSem.pdf), [[research.ibm.com]](https://research.ibm.com/publications/semantics-of-probabilistic-programs--1), [[cambridge.org]](https://www.cambridge.org/core/books/foundations-of-probabilistic-programming/semantics-of-probabilistic-programming-a-gentle-introduction/A7964205E44B5234A78C661192E294E1)
2. Benjamin L. Kaminski, Joost-Pieter Katoen, Christoph Matheja, **“On the hardness of analyzing probabilistic programs”** (Acta Informatica 2019): AST -complete; PAST -complete; universal variants at /. [[link.springer.com]](https://link.springer.com/article/10.1007/s00236-018-0321-1)
3. Rupak Majumdar, V. R. Sathiyanarayana, **“Positive Almost-Sure Termination: Complexity and Proof Rules”** (POPL 2024 / PACMPL 2025): richer-language PAST is -complete. [[colab.ws]](https://colab.ws/articles/10.1145%2F3704899), [[popl24.sigplan.org]](https://popl24.sigplan.org/details/POPL-2024-popl-research-papers/39/Positive-Almost-Sure-Termination-Complexity-and-Proof-Rules)
4. Yuhan Xiong, Di-Hua Zhai, Mahdi Tavakoli, Yuanqing Xia, **“Discrete-Time Control Barrier Function: High-Order Case and Adaptive Case”** (IEEE TCYB): discrete-time CBFs guarantee forward invariance of safe sets. [[ece.ualberta.ca]](https://www.ece.ualberta.ca/~tbs/pmwiki/pdf/IEEE-T-Cybenetics-Xiong-2022.pdf), [[ieeexplore.ieee.org]](https://ieeexplore.ieee.org/document/9777251)
5. Mingzhang Huang, Hongfei Fu, Krishnendu Chatterjee, Amir Kafshdar Goharshady, **“Modular Verification for Almost-Sure Termination of Probabilistic Programs”** (OOPSLA 2019): sound modular rules for AST; polynomial-time synthesis in linear cases. [[dl.acm.org]](https://dl.acm.org/doi/epdf/10.1145/3360555)
6. Annabelle McIver, Carroll Morgan, Benjamin Kaminski, Joost-Pieter Katoen, **“A New Proof Rule for Almost-Sure Termination”** (POPL 2018): modern martingale-based proof rule for AST. [[trustworthy.systems]](https://trustworthy.systems/publications/full_text/McIver_MKK_18.pdf)
7. Cosimo Spera, **“Safety is Non-Compositional: A Formal Framework for Capability-Based AI Systems”** (arXiv 2026): formal non-compositionality under conjunctive capability dependencies. [[arxiv.org]](https://arxiv.org/pdf/2603.15973v1)
8. Pierre Peigné et al., **“Multi-Agent Security Tax: Trading Off Security and Collaboration Capabilities in Multi-Agent Systems”** (AAAI 2025): empirical evidence that defense and collaboration trade off in multi-agent systems. [[ojs.aaai.org]](https://ojs.aaai.org/index.php/AAAI/article/view/34970)
9. Standard order/categorical background on semilattices and lattices as posets with finite meets/joins. [[ncatlab.org]](https://ncatlab.org/nlab/show/semilattice), [[ncatlab.org]
