# Scorer test data

Model: claude-sonnet-5. 75 scoring calls, 5 repeats each, about $0.596.

## 1. Consistency across 5 repeats (12 seed variants)

| Criterion | Variants whose score changed between repeats | Swung by 2 | Mean SD |
|---|---|---|---|
| Proposition intact | 0 of 12 | 0 | 0.0 |
| Tone | 1 of 12 | 0 | 0.041 |
| Humour | 2 of 12 | 1 | 0.107 |
| Voice signatures | 0 of 12 | 0 | 0.0 |
| Mandatories | 0 of 12 | 0 | 0.0 |

Scores per variant (5 repeats):

| Variant | Proposition intact | Tone | Humour | Voice signatures | Mandatories |
|---|---|---|---|---|---|
| baseline · Nigeria | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 |
| baseline · United Kingdom | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 |
| baseline · South Africa | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 |
| us_reference · Nigeria | 2 2 2 2 2 | 2 2 2 2 2 | 0 0 0 0 0 | 2 2 2 2 2 | 2 2 2 2 2 |
| us_reference · United Kingdom | 2 2 2 2 2 | 2 2 2 2 2 | 2 1 2 1 1 | 2 2 2 2 2 | 2 2 2 2 2 |
| us_reference · South Africa | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 1 0 2 | 2 2 2 2 2 | 2 2 2 2 2 |
| pun · Nigeria | 2 2 2 2 2 | 2 1 1 1 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 |
| pun · United Kingdom | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 |
| pun · South Africa | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 |
| sensitivity · Nigeria | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 |
| sensitivity · United Kingdom | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 |
| sensitivity · South Africa | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 | 2 2 2 2 2 |

## 2. Off-voice variants (caught = the broken criterion scores 0 or 1)

| Variant | Breaks | Scores on that criterion | Caught |
|---|---|---|---|
| offvoice-1 | humour | 1 0 1 1 1 | 5 of 5 runs |
| offvoice-2 | signatures | 0 1 1 0 1 | 5 of 5 runs |
| offvoice-3 | mandatories | 1 1 1 1 1 | 5 of 5 runs |

## 3. Agreement with human scores (12 variants scored)

- Exact agreement, raw scorer: 48 of 60 (80.0%)
- Exact agreement, with the product's humour rule: 48 of 60 (80.0%)
- Within one point: 51 of 60

| Criterion | Exact agreement |
|---|---|
| Proposition intact | 12 of 12 |
| Tone | 11 of 12 |
| Humour | 1 of 12 |
| Voice signatures | 12 of 12 |
| Mandatories | 12 of 12 |

Disagreements (scorer's most common score vs human):

| Variant | Criterion | Human | Scorer | Scorer's reason |
|---|---|---|---|---|
| baseline-ng | Humour | 0 | 2 | "Dentist you've dodged since March" is a dry jab at the chore, not the user. |
| baseline-uk | Humour | 0 | 2 | "dentist you've dodged since March" is dry humour aimed at the chore, not the user. |
| baseline-za | Humour | 0 | 2 | "Dodged since March" is a dry jab at the chore, not the user. |
| us_reference-ng | Humour | 2 | 1 | No dry humour present; body is straightforward description without a wry turn. |
| us_reference-uk | Humour | 2 | 1 | (scores varied 2 1 2 1 1; this run's reasons weren't paired with scores, see note) |
| pun-ng | Tone | 2 | 1 | (scores varied 2 1 1 1 2; this run's reasons weren't paired with scores, see note) |
| pun-ng | Humour | 0 | 2 | 'Dodged since March' is dry, understated, aimed at the chore not the user. |
| pun-uk | Humour | 0 | 2 | "dodged since March" is dry humor aimed at the chore, not the user. |
| pun-za | Humour | 0 | 2 | "dodged since March" is dry humour aimed at the chore, not the user. |
| sensitivity-ng | Humour | 0 | 2 | "dodged since March" is a dry, understated jab at the chore, not the user. |
| sensitivity-uk | Humour | 0 | 2 | "dodged since March" is a dry, understated jab at the chore, not the user. |
| sensitivity-za | Humour | 0 | 2 | "dentist you've dodged since March" is dry humour aimed at the chore. |

Note: in this run the script quoted a reason from any repeat, not only from repeats that gave the reported score. That only matters where scores varied (the two rows marked above). The script is fixed for future runs.
