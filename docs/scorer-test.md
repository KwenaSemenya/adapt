# Scorer test: can an LLM score its own brand's voice reliably?

**Short answer: it is consistent and it catches clearly broken copy, but on the one criterion that needs taste, it disagreed with me 11 times out of 12. The cause was the rubric, not the model.**

ADAPT's scorer is a separate, blind Claude call (Sonnet 5). It sees the brief, the Kin voice guide and rubric, and the finished variant. It never sees the drafting call's reasoning. It scores five criteria 0, 1 or 2. Scores are advisory and never block approval. This test checks whether that advice is worth showing.

Run on 29 September 2026: 75 scoring calls, about $0.60. Raw numbers are in [scorer-test-data.md](scorer-test-data.md) and [scorer-test-results.json](scorer-test-results.json). The script is [scripts/scorer_test.py](../scripts/scorer_test.py).

## What I tested

| Check | Method |
| --- | --- |
| Consistency | Scored each of the 12 seed variants (4 campaigns × 3 markets) 5 times |
| Catching bad copy | Scored 3 off-voice ads I wrote, each meant to break one criterion, 5 times each |
| Agreement with a human | Compared the scorer's most common score with my own scores for all 12 seed variants (60 scores) |

## What held

**Consistency is high.** Across 60 variant-criterion pairs, only 3 changed between repeats. Proposition, voice signatures and mandatories never moved. The wobble was all in Tone (1 variant) and Humour (2 variants): the subjective criteria.

**It catches clearly broken copy.** All three off-voice ads were caught in all five runs (the broken criterion scored 0 or 1). More convincing than the numbers, its reasons named the actual flaw each time:

- Humour ad: "phrasing like 'too frazzled' leans toward mocking the user, not the chore."
- Signatures ad: "switches to 'Users' instead of 'you', weak relief ending."
- Mandatories ad: "'Try Kin free' omits the required 14-day trial length."

**On the objective criteria it matched me almost exactly.** Proposition 12 of 12, voice signatures 12 of 12, mandatories 12 of 12, tone 11 of 12.

## What broke

**1. Humour: 1 of 12 in agreement with me.** Nine of the disagreements are the same line: "Kin rebooks the dentist you've dodged since March." The scorer gave it 2 every time ("a dry jab at the chore, not the user"). I gave it 0: to me "you've dodged" teases the reader's avoidance, which the voice guide rules out.

The scorer wasn't guessing. The Kin rubric uses that exact sentence as its example of a level-2 humour line. The rubric's level descriptions and examples were drafted by Claude early in the build, and I didn't challenge them. So the scorer followed the rubric faithfully, and the rubric encoded a judgement I don't share. The model is only as good as the calibration it's given, and nobody had calibrated it against a creative director.

The other direction happened too. For the US-reference variants, where the Thanksgiving joke was replaced, I scored humour 2 on all three. The scorer agreed on South Africa but gave Nigeria 0 in every run ("no dry humour present") and the UK mostly 1.

**2. The headline agreement figure flatters the scorer.** 48 of 60 exact matches (80%) sounds good, but almost every seed variant scores 2 on almost everything, from both of us. Agreeing is easy at the ceiling. The only criterion with real spread, humour, is where agreement collapsed.

**3. "Caught" partly means "marked everything down".** The off-voice ads scored 1 on most criteria, not only the broken one. The mandatories ad, which I wrote to be on-voice apart from the missing trial length, still lost points on tone in all five runs. So the scorer notices that something is off, but it isn't precise about which one thing.

**4. Noise alone can trip a confidence signal.** ADAPT marks a variant low confidence when a rescore moves any criterion by 2 or more. In this test, one variant's humour score moved from 2 to 0 across repeats with no change to the copy (scores 2 2 1 0 2). A reviewer could see "low confidence" after a rescore that only reflects scorer noise.

**5. The product's humour rule changed nothing here.** ADAPT raises a humour score from 0 to 1 when a grounded change dropped the joke for a stated reason (the scorer is blind to reasons). It fired once (US-reference Nigeria), but that variant still disagreed with my 2, so agreement didn't move.

## What I'd change

1. **Have the creative director write the rubric examples, especially for humour.** The fastest fix is to replace the level-2 humour example with a line a Kin CD agrees is dry and aimed at the chore, then rerun this test. I'd expect humour agreement to rise sharply, because the scorer is clearly anchored on the examples.
2. **Calibrate with a small human-scored set in the prompt.** Five or six variants scored by the CD, with one-line reasons, would teach the scorer the house's reading of "at the chore, not the user" better than a one-line description.
3. **Label humour as a taste call in the interface.** Keep showing it, but mark it as the least reliable score, and lead with the reason rather than the number.
4. **Stop scorer noise tripping low confidence.** Either ignore humour for the rescore-drift signal, or require the shift to hold across two rescores. Scoring three times and taking the median would also work, at three times the cost.
5. **Test with harder cases.** Add mid-quality variants (mostly on-brand, one subtle slip) so agreement isn't measured at the ceiling, and ads that break exactly one criterion while matching the master everywhere else.

## Limits of this test

- One human rater (me). A second creative director would show whether my humour reading is the house view or mine.
- Twelve seed variants, mostly strong. See point 2 under What broke.
- A small reporting bug in this run: where repeats disagreed, the quoted reason could come from a different repeat than the score. It affects two rows in the data file, which are marked; the script is fixed.
- No tuning was done to make the scorer pass. These are first-run numbers.
