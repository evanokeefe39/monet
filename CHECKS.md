Persona	ID	Check	Principle
System Architect	SA-01	Mixed responsibilities	SRP — business logic, I/O, and orchestration must not share a unit
System Architect	SA-02	Open/closed violation	OCP — logic requiring modification rather than extension when requirements change
System Architect	SA-03	Wrong dependency direction	DIP — concretes leaking into abstractions or lower layers pulled upward
System Architect	SA-04	Leaking boundaries	ISP / encapsulation — internal state crossing a subsystem interface
System Architect	SA-05	Inconsistent patterns	Cohesion — deviations from established conventions without justification
System Architect	SA-06	Unnecessary complexity	KISS — nesting, long functions, or tricks a reader cannot parse in two minutes
System Architect	SA-07	Dead or speculative code	YAGNI — infrastructure built for requirements that do not exist
Staff Engineer	SE-01	Architectural drift	Separation of concerns — deviations from established boundaries without justification
Staff Engineer	SE-02	Wrong layer dependencies	DIP — domain logic leaking into infrastructure or vice versa
Staff Engineer	SE-03	Scalability liabilities	Coupling — designs that will not hold under load, data volume, or team growth
Staff Engineer	SE-04	Poor service boundaries	SRP / cohesion — separation of concerns across module or service edges
Staff Engineer	SE-05	Missing or incorrect abstractions	Connascence — of meaning, timing, or position across components
Staff Engineer	SE-06	Ownership ambiguity	Bounded context — logic without a clear home indicates a missing domain boundary
Staff Engineer	SE-07	Platform misuse	YAGNI / KISS — wrong primitive or reinvention of existing platform capability
QA Engineer	QA-01	Untestable units	DIP — logic coupled to I/O, global state, or concretes that cannot be injected
QA Engineer	QA-02	Missing error paths	Defensive design — unhandled edge cases, missing boundary checks, silent failures
QA Engineer	QA-03	Non-determinism	Isolation — logic dependent on time, randomness, or external state without a seam
QA Engineer	QA-04	Test coverage gaps	Changed logic with no corresponding test change
QA Engineer	QA-05	Weak assertions	Tests that pass without actually verifying behaviour
QA Engineer	QA-06	Overly large units under test	SRP — units doing too much make it impossible to isolate a single behaviour
QA Engineer	QA-07	Test-to-implementation coupling	OCP — tests tied to internals that break on refactor without behaviour change
SRE	SRE-01	Missing observability	No logging, metrics, or tracing on critical paths or failure points
SRE	SRE-02	Silent failures	Defensive design — errors swallowed, retried without limit, or not surfaced
SRE	SRE-03	Missing retry and timeout controls	Resilience — external calls with no timeout, backoff, or circuit breaker
SRE	SRE-04	Resource leaks	Connections, handles, or threads not released on failure paths
SRE	SRE-05	Deployment risk	OCP — changes not backwards compatible, not feature-flagged, or not safe to roll back
SRE	SRE-06	Wide blast radius	Coupling — changes with broad impact scope and no isolation or graceful degradation
SRE	SRE-07	Capacity assumptions	Hardcoded limits, unbounded queues, or load-unaware logic
Senior Python Dev	PY-01	Duplicated logic	DRY — should be a function, class, or shared utility
Senior Python Dev	PY-02	Overcomplicated constructs	KISS — a simpler Python idiom exists (comprehensions, builtins, dataclasses)
Senior Python Dev	PY-03	Premature abstraction	YAGNI — unused generics or over-engineered base classes
Senior Python Dev	PY-04	Non-idiomatic Python	Context managers, comprehensions, and dataclasses ignored where applicable
Senior Python Dev	PY-05	Type safety gaps	Missing or incorrect annotations; use of Any without justification
Senior Python Dev	PY-06	Python footguns	Mutable default arguments, late binding closures, bare excepts
Senior Python Dev	PY-07	Poor exception handling	Swallowed exceptions or exceptions used for control flow
Senior Python Dev	PY-08	Import hygiene	Circular imports, wildcard imports, or missing __all__