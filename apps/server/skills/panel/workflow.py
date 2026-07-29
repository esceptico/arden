# panel(question, n?, criteria?) — diverge -> judge -> synthesize.
question = args.get("question", "")
if not isinstance(question, str) or not question.strip():
    raise ValueError("panel requires a non-empty args['question'].")
raw_n = args.get("n", 3)
if isinstance(raw_n, bool) or not isinstance(raw_n, int):
    raise ValueError("panel args['n'] must be an integer from 1 to 5.")
n = raw_n
if not 1 <= n <= 5:
    raise ValueError("panel args['n'] must be an integer from 1 to 5.")
criteria = args.get("criteria") or ["correctness / fit", "simplicity", "risk"]
gen_model = args.get("gen_model")
synth_model = args.get("synth_model")

lenses = [
    "the simplest thing that works",
    "the most robust / scalable approach",
    "an unconventional angle others would miss",
    "the lowest-risk incremental path",
    "the highest-leverage approach",
]

phase(f"propose {n}")
takes = await parallel([
    agent(
        f"Propose ONE concrete approach to: {question}\nLens: {lenses[i % len(lenses)]}. "
        f"Be specific — what to do, why, and the key tradeoffs. Terse.",
        model=gen_model,
    )
    for i in range(n)
])
takes = [t for t in takes if t]
if not takes:
    return "No proposals were produced."

phase("judge")
JUDGE = {"scores": [{"criterion": "str", "score": "int", "note": "str"}], "verdict": "str"}
verdicts = await parallel([
    agent(
        f"Score this approach to \"{question}\" on each criterion {criteria} (1-5, with a one-line "
        f"note each) and give a one-line overall verdict.\n\nApproach:\n{t}",
        schema=JUDGE, model=gen_model,
    )
    for t in takes
])

phase("synthesize")
return await agent(
    f"Given these scored approaches to \"{question}\", recommend the single best one and justify it, "
    "grafting the strongest ideas from the runners-up. Be decisive — end with a clear pick.\n\n"
    + json.dumps([{"approach": t, "judgement": v} for t, v in zip(takes, verdicts)]),
    model=synth_model, agent_type="planner",
)
