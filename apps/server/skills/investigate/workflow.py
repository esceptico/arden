# investigate(target, question, breadth?) — parallel readers -> cited synthesis.
target = args.get("target", ".")
question = args["question"]
breadth = args.get("breadth", "normal")
n = {"focused": 2, "normal": 4, "wide": 6}.get(breadth, 4)
reader_model = args.get("reader_model")
synth_model = args.get("synth_model")

phase("derive angles")
derived = await agent(
    f'To answer this about {target}: "{question}" — list {n} distinct, concrete investigation '
    f"angles (each a specific facet, file-area, or sub-question to examine). Terse, one per item.",
    schema={"angles": ["str"]},
    model=reader_model,
    agent_type="explorer",
)
angles = (derived or {}).get("angles") or [question]

phase(f"investigate {len(angles)}")
findings = await parallel(
    [
        agent(
            f"Investigate {target} on this angle: {a}. Use targeted search/reads; cite exact file:line / "
            f"sources, don't dump files. Return concise findings that help answer: {question}",
            model=reader_model,
            agent_type="explorer",
        )
        for a in angles
    ]
)

phase("synthesize")
return await agent(
    f"Answer this precisely, citing sources inline: {question}\n\n"
    "Findings from parallel investigators:\n" + json.dumps([f for f in findings if f]),
    model=synth_model,
    agent_type="explorer",
)
