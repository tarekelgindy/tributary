# Incident Protocol

What Tributary does, publicly, when a community decides it is enemy infrastructure.

Written 2026-07-09, while calm — because the worst time to decide any of this is mid-incident,
angry, at 1am, watching a quote-tweet counter climb. If an incident is happening right now:
stop, read this top to bottom, and follow it even where it feels too slow or too soft.
Especially where it feels too slow or too soft.

**The premise:** a provenance tool that traces narratives will eventually trace a narrative
some community holds dear, and that community will read the trace as an attack. This is not a
failure state; it is a predictable consequence of doing the work. The failure state is
responding in a way that confirms the accusation — becoming a combatant instead of an
instrument.

---

## What counts as an incident

Any of the following, at any scale:

- Hostile viral criticism — a post/thread calling Tributary biased, an op, funded by X,
  targeting community Y, spreading quickly.
- A traced narrative's community objecting — "this tool says our belief was *seeded*" —
  whether or not the trace contains an error.
- An error report delivered with hostility. (Strip the hostility; it's a correction report.
  See below.)
- Coordinated pile-on, brigading of the repo/issues, or bad-faith flooding of the
  corrections channel.
- A journalist asking about any of the above.

**One rule above all others: separate the substance from the tone before doing anything
else.** Every incident gets split into (1) claims about our output that can be checked, and
(2) characterizations of us that cannot. Part (1) goes into the corrections process below,
regardless of how nastily it was phrased. Part (2) gets, at most, one calm response, ever.

---

## (a) Corrections posture and turnaround

Posture: **hostile critics are unpaid QA.** Every entry in [CORRECTIONS.md](CORRECTIONS.md)
so far was found by looking hard at our own output; a critic who finds the next one is doing
us the same favor with worse manners. The manners are not our problem; the error is.

Process, for any checkable claim about our output:

1. **Acknowledge within 24 hours** in the venue where it surfaced: "Checking this — will
   post what we find by [date]." Acknowledgment is not agreement; say only that we're
   checking.
2. **Assess within 72 hours.** This is one person with a job (see MISSION_PLAN Standing
   Disciplines) — 72 hours is honest, so promise that, not "immediately." If the assessment
   needs longer (archives, offline sources), say so publicly with a new date and keep it.
3. **If it's an error:** fix the artifact, log it in [CORRECTIONS.md](CORRECTIONS.md) with
   date, error, fix, and root cause — **with credit to the finder, hostile or not, in the
   same words we'd use for a friendly one** — then reply linking the log entry. The reply
   leads with "you were right," not with mitigation.
4. **If it's partially an error:** correct the true part explicitly and fully before saying
   anything about the rest. Never let the wrong 80% excuse ignoring the right 20%.
5. **If it's not an error:** publish the check itself — what was claimed, what we verified,
   receipts — once, in one linkable place. Then stop. We do not relitigate a published check
   in replies.

While assessing: the disputed artifact **stays up** with a visible "under review — [link]"
note. We do not silently unpublish under pressure (that reads as guilt and destroys the
receipt trail), and we never silently *edit* a disputed artifact — every change ships through
the corrections log. Exception: content creating a safety problem (doxxing, targeting a
private individual) comes down first and gets explained after.

Bad-faith flooding of the corrections channel: reports are triaged in order; obviously
duplicate or evidence-free reports get one pointer to the
[correction template](.github/ISSUE_TEMPLATE/correction.md) ("we need a link and a checkable
claim") and are otherwise not engaged. The template is the front door for everyone,
including people who hate us.

## (b) Tone rules for hostile viral criticism

1. **Respond to claims, not characterizations.** "Your trace has the wrong date" gets a
   process. "You're a psy-op" gets, at most, one link to this document and METHODOLOGY.md.
2. **One substantive response per venue, in one linkable artifact, updated in place.** No
   reply threads with hostile accounts. If it's genuinely viral, a single pinned statement
   beats twenty replies — it's checkable, it can be corrected, and it doesn't feed the
   ratio.
3. **Write the response, then hold it for at least a few hours** (12 if there's any anger in
   the room) unless it's a trivial factual fix. Nothing about a viral cycle changes in 12
   hours except our judgment, which improves.
4. **Speak in the tool's register:** what the output claims, how it was produced, its known
   failure modes, how to check it, where the error log is. Structural, checkable sentences.
   No sarcasm, no irony, no dunks, no screenshots-of-critics, no vaguing about the incident.
5. **Never match reach.** We respond where we publish; we do not chase the criticism across
   platforms or boost it into audiences that hadn't seen it.
6. **Thank accurate critics plainly**, including hostile ones. "You were right about the
   date; fixed and logged here" — full stop, no "however."
7. **One voice.** Tarek speaks for Tributary; nobody speaks *as* the community's defender.
   We never ask or hint that supporters should respond, counter-post, or report anyone.
8. Legal threats and press inquiries leave the public thread immediately: acknowledge,
   move to email, answer the press factually with links to the same public documents
   everyone else gets. No exclusives on incidents, no off-the-record characterizations of
   critics.

## (c) What we never do

- **Never litigate motives.** We do not speculate — publicly or in the logs — about why a
  critic or community is attacking, who they're aligned with, or what they're "really"
  doing. Content only. The moment we explain criticism by its origin, we've become the thing
  we're accused of being.
- **Never issue truth verdicts in self-defense.** The no-verdicts principle
  ([METHODOLOGY.md](METHODOLOGY.md), Principle 1) has no self-defense exception. We do not
  call criticism of Tributary "misinformation," "disinformation," "a false narrative," or
  any synonym — not even when it is one.
- **Never turn the instrument on its critics.** We do not fingerprint, trace, or
  coverage-map the criticism of Tributary as a rebuttal. Using the tool as a weapon in our
  own fight is the single fastest way to prove the enemy-infrastructure charge true.
  (If, much later and fully cold, an incident is genuinely interesting *as data*, that's a
  separate editorial decision made outside the incident — never as a response to it.)
- **Never claim virtue as a defense.** No "we're unbiased," no "we're neutral" — those are
  verdicts about ourselves. We point to method, receipts, and the error log, and let the
  recognition gate (METHODOLOGY.md) do the fairness-claiming: whether a summary is fair is
  for the summarized circle to say, not us.
- **Never brigade, counter-dunk, mass-report, or contact anyone's employer.** Obviously.
  Written down anyway, because this document exists for the moment "obviously" stops feeling
  obvious.
- **Never promise product or policy changes mid-incident to make it stop.** Appeasement
  changes are logged nowhere, resented immediately, and reversed badly. Changes come out of
  the post-incident review, on their merits.
- **Never memory-hole.** Hostile criticism gets logged verbatim in `FEEDBACK_LOG.md`
  (per MISSION_PLAN) like any other feedback — date, who, quote, action taken.

## (d) What we point to

Every incident response is built from the same four pointers, in roughly this order:

| Pointer | What it establishes |
|---|---|
| [METHODOLOGY.md](METHODOLOGY.md) | What each output actually claims, how it's produced, and its known failure modes — including that interpretive labels are AI judgments and that we issue no truth verdicts. |
| The **error rate** (METHODOLOGY.md § Error rate) | Our measured precision — or, until the audit publishes, the honest statement that it is *not yet measured*. We cite its current state either way; pretending otherwise mid-incident would be the real scandal. |
| [CORRECTIONS.md](CORRECTIONS.md) | The public, dated log of every error found, with root causes and credit. A known error rate is the point, not an embarrassment — this log is the strongest evidence we mean that. |
| The [correction path](.github/ISSUE_TEMPLATE/correction.md) | What the critic can *do*: file a checkable claim with a link and evidence, and get a public answer on the record. |

The generic skeleton of a response (adapt, don't paste):

> Tributary's trace of [X] claims [structural facts], produced by [method — link]. It does
> not claim [the thing being attributed to it — usually a truth verdict or a motive].
> [If an error was found: "The criticism was right about [Y]; fixed and logged here — link,
> with credit."] Our error log and methodology are public: [links]. If anything else in the
> trace is wrong, here's the corrections path: [link]. Confirmed errors are logged publicly
> with credit.

## After the incident

Within a week of the dust settling:

1. Log the episode verbatim in `FEEDBACK_LOG.md` (quotes, dates, what we did).
2. If output was wrong, confirm the CORRECTIONS.md entry captures the root cause, not just
   the symptom.
3. If this protocol was wrong — too slow, too soft, missing a case — amend it **now, calm
   again**, and note the change in MISSION_PLAN's Decision Log.
4. Resume the cadence. An incident handled by the book should leave behind: one linkable
   response, zero deleted posts, zero replies we regret, possibly one corrections entry with
   a critic's name on it — and nothing else.
