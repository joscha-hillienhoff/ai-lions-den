# The AI Lion's Den - Concept

## 1. Purpose

A web app for a live startup pitch event of about one hour, built for groups of about 20 to 60 participants.
Teams develop an AI startup idea with their own AI subscription or a free one, submit a pitch, and face a jury of four AI investors with different perspectives.
The jury evaluates independently in both rounds, asks critical questions, and makes a final fictional investment decision on 250,000 EUR for 10 % equity.
The three finalists then pitch live in front of the room.

The goal is an entertaining, competitive session that connects entrepreneurship with hands-on GenAI use and a visible multi-agent system.

## 2. Design principles

Clearly defined rules, no optional mechanics.
Everything not listed in this document is out of scope (see section 10).
All UI text, code, comments, and documentation are in English.

## 3. Setup

- Teams of 4 to 6 people, up to 20 teams.
- One laptop per team.
- One projector in the room.
- One or more moderators.

## 4. Views

| View | Used by | Purpose |
|---|---|---|
| Team view | One laptop per team | Join, develop the AI startup with their own AI, write and submit the pitch, read jury feedback, answer the questions and state one change, see the final verdict, re-read their own pitch while presenting, and see the reveal |
| Projector view | The room | Current phase, countdown, number of submitted teams, what the pitch needs and the jury during ideation, finalists, audience vote QR code, reveal |
| Vote view | One phone per person | Pick one of the three finalists during the audience vote |
| Moderator view | Moderators, password protected | Advance phases, step back one phase after an accidental advance, see progress of every team, view any team's pitch and jury output, see the number of votes cast, export the current run, and view or download past runs automatically archived on Reset |

Teams use their own AI subscription or a free one for generating ideas; the app has no in-app chat.

Teams join via a link or QR code shown on the projector and choose a team name.
There are no user accounts; a team stays signed in through its browser session and can reopen the page on the same laptop.
The audience votes on its own phones through a public vote page with no account. One browser gets one vote.

## 5. Session flow

The moderator advances all teams through the same phases. The durations below are the default agenda of about one hour.
Countdowns are guidance for the room; a phase ends only when the moderator advances it. The one exception is the end of Jury round 1, described under "Automatic advance after the jury round".
The finale and the winner page are untimed: they have no phase countdown and no "time is up" state. The only clock in the finale is the 3-minute orientation clock per pitch described in the phase table below.
Jury round 1 and the final verdict show no phase timer on the projector or in the team header, because everyone only waits for the lions; the 5 minutes are only a guide for the moderator. The projector's top right stays empty there, except that the 15-second countdown to the defense appears in that spot once it runs. The moderator view keeps its timers, and the move on from the final verdict stays manual.

| Time | Phase | What happens |
|---|---|---|
| 5 min | Lobby | Moderator explains the rules, teams join |
| 20 min | Ideation | Teams develop their AI startup with their own AI subscription or a free one and fill in the pitch form; the form saves as a draft |
| 5 min | Jury round 1 | Pitch is locked; the four investors evaluate each pitch independently and each asks one critical question |
| 10 min | Defense | Each team answers the four questions in writing and states one concrete change |
| 5 min | Final verdict | The four investors give independent final scores in parallel, which set each investor's decision |
| 3 min | Leaderboard | Pitch preparation time: the three finalists are named in their random, fixed pitch order (not a ranking) and told to prepare their pitch (about 3 minutes each), without lions-invest counts or points; the jury result stays hidden until the reveal |
| Untimed | Finale | The three finalists pitch live in the room, one after another; the finale runs as long as the three pitches take and the moderator advances when the last finalist is done. Each pitch should take about 3 minutes: an orientation clock for the spotlighted pitch counts down from 3:00 in the moderator view and in the team view of the finalist on stage (not on the projector), stays not started (static 3:00) when the finale starts until the moderator presses "Start pitch clock", and restarts whenever the moderator selects another finalist, runs on as visible overtime after 0:00 and never advances anything |
| 3 min | Voting | The room votes on their phones by scanning the QR code on the projector |
| Untimed | Winner | Audience result and the AI jury result are revealed together; the page stays open until the session is reset |

The timed phases add up to 51 minutes. With the three finale pitches the default agenda takes about one hour (about 61 minutes).

Pitches that are incomplete when Jury round 1 starts are not evaluated.

### Automatic advance after the jury round

When every eligible team's four round-1 results are in, the jury round moves on to the defense by itself, so the room does not wait for the moderator to notice. It is the only automatic transition: the final verdict never advances to the leaderboard by itself, because that reveals the finalists.

- The countdown is 15 seconds (`JURY_AUTO_ADVANCE_SECONDS`, `0` turns the automation off). As soon as the last result lands, the projector shows "Defense starts in 15 s" in the top right, where the phase timer sits in other phases, and the moderator view shows the same line with **Hold** and **Start now**.
- **Start now** advances immediately, exactly like **Advance to Defense**; the normal advance button keeps working during the countdown. **Hold** stops the countdown and keeps the session in the jury round until the moderator advances; the countdown does not start again during that visit.
- Without intervention the server advances after the countdown. The deadline is stored in the session, so a reload shows the same remaining time, a restarted server continues it, and nothing depends on an open browser. The transition runs once in the same step as a manual advance, so it cannot skip a phase when it races a click or a second tab.
- No countdown starts while any round-1 call is pending or failed, so a team never enters the defense without its questions. After the moderator retries the failed step and every result is in, the countdown starts as usual. Pitches that were not evaluated do not hold it back; with no evaluated team at all the session never advances by itself.
- Stepping **Back** into the jury round never starts the countdown; the moderator advances manually from there.

### Team view: "Your pitch" card

From the leaderboard through the finale and voting, the team view shows a read-only "Your pitch" card, so a team can pitch its idea properly on stage. The leaderboard doubles as the short preparation time for the three finalists.

- It shows the team's own submitted pitch (startup name and every field), "Your one change" from the defense, and a short cheat sheet with one block per lion: the question that lion asked ("Asked"), the team's own defense answer to it ("You answered", left out when the team gave none) and the lion's final verdict statement ("Final word"). The cheat sheet reuses the saved jury output and the team's own answers; it makes no new model call.
- Finalists see the card first, with "Use this while you pitch". Non-finalists see the same card read-only below the phase content. Having the card says nothing about the ranking.
- The text is large and wraps on a phone held on stage; the layout check covers a finalist and a non-finalist at phone width.
- The card only uses the viewing team's own data. The team API payload does not carry any other team's pitch, feedback or jury figures; the finalist list stays neutral. The winner phase and the projector and vote pages do not show the card or any pitch text, since the team presents the pitch itself.

### Stepping back

If the moderator advances by mistake, the moderator view offers a **Back to** button naming the previous phase next to the advance button. It asks for a confirmation, moves the session exactly one phase back and is not available in the first phase. Like advancing, it carries the phase the moderator is looking at, so a double click or a second tab cannot step back twice.

- Stepping back only moves the phase pointer. Teams, pitches, defense answers, jury feedback, verdicts, votes and the finale spotlight are kept; nothing is deleted or recomputed.
- Advancing again repeats no completed jury call. Only steps that are still pending run, and failed steps stay failed until the moderator retries them. Teams whose pitch the jury has not started on yet, for example a team that was not submitted when the jury started, are evaluated when the jury starts again.
- The phase timer restarts with the full duration of a timed phase. The finale and the winner page stay untimed; stepping back into the finale leaves the pitch orientation clock not started until the moderator starts it.
- The projector, team phones and the vote page simply follow the current phase. Going back from the winner to voting hides the jury figures and ranking again, keeps the votes already cast and reopens voting.
- A pitch the jury has already evaluated, and defense answers the final verdict has already used, stay locked when the session goes back to ideation or defense, so the results always belong to the text that produced them. The team view shows such a pitch or answers read-only with a short notice, and the server rejects saves for them.

Jumping to an arbitrary phase and undoing team or jury data are not part of this.

## 6. Pitch form

Every team pitches an AI startup: the product must be built on AI.
All fields are required and length-limited so the projector stays readable and jury input stays bounded.

| Field | Limit |
|---|---|
| Startup name | 60 characters |
| One-line tagline | 140 characters |
| Problem | 2000 characters |
| Solution | 2000 characters |
| Target customers | 1200 characters |
| Business model | 2000 characters |

While the teams build their pitch, the projector keeps the clock and the submission progress and also shows two blocks, so every team member can see at a glance what to think about and whom they pitch against.
"What your pitch needs" lists every field of the form above with its hint, taken from the same definition as the team form.
"Your jury" shows the four investors with portrait (square, uncropped), name, focus tag, archetype and their one short line from the lobby introduction.
The defense phase keeps the plain projector view without these blocks.

The investment ask is fixed for every team: 250,000 EUR for 10 % equity (implied post-money valuation 2.5 million EUR). The pitch is locked once Ideation ends and teams do not rewrite it.

## 7. The jury

Four investors, each with a fixed identity and distinct investing lens.
Each investor persona is defined in one configuration file, so personas can be reworked without code changes.

| Investor | Focus | Investor type | Perspective | Typical concern |
|---|---|---|---|---|
| Victoria Lyon, The Growth Hunter | Market & Growth | VC - Growth and scaling | Market size, scalability, speed | "What plausible milestone could show this starting market expanding into a much larger business?" |
| Maximilian Löwenstein, The Numbers Guy | Money & Margins | Private equity - Financial viability | Unit economics, revenue model, valuation | "Which pricing and cost assumptions keep the margin attractive as the plan grows?" |
| Sophie Leone, The Market Realist | Customers & Demand | Business angel - Customers and product-market fit | Real customer pain, willingness to pay, adoption | "Who is the plausible first buyer, and what makes this problem urgent enough to change?" |
| Dr. Alex Leander, The AI Builder | Tech & AI | Tech investor - AI and defensibility | Feasibility, defensibility, use of technology | "Is AI the core of this product, and what technical advantage would remain if a platform shipped the same capability?" |

The projector lobby introduces the jury next to the scoring rules with each investor's portrait, name, focus tag and one short line.
Each investor works only inside one lens and leaves the other domains to the other investors, without seeing their work.
Victoria Lyon covers market size, growth path and scalability.
Maximilian Löwenstein covers pricing, unit economics, costs and financial viability; onboarding fees and integration cost belong to his lens.
Sophie Leone covers the target customer, problem pain, willingness to pay and competition from the customer's view.
Dr. Alex Leander covers technical feasibility, what AI makes newly possible, AI accuracy and risks, data and defensibility, and asks whether AI is the core of the product or only decoration.
Every team pitches an AI startup, but the business stays the main subject: the other three investors include AI only where it materially affects their lens, and the shared jury prompt says a strong AI idea with a weak business is a weak pitch. Decorative AI is named in Alex's assessment; there is no score cap for it.

### Jury round 1 (independent)

Each investor sees only the pitch and, in parallel and without seeing the others, returns:

- a score from 1 to 10 from their own perspective,
- an assessment of about 40 words in no more than 2 to 3 short sentences, naming the strongest point and the single biggest weakness inside that investor's lens; server validation requires non-empty text of at most 900 characters,
- exactly one critical question of at most about 25 words, with no second part joined by "and" or a dash, answerable by a team in at most 500 characters with assumptions, estimates or a plan; server validation requires non-empty text of at most 500 characters.

### Defense

The team answers each of the four questions in writing (up to 1000 characters per answer) and states "Our one change" (up to 1000 characters): the one concrete change the team makes to the idea because of the criticism. The pitch itself stays locked.

### Final verdict (independent)

The four investors give their final verdicts in parallel and without seeing each other's work.
Each investor sees only the pitch, their own round-1 score, assessment and question, the team's answer to that own question, and the team's one change.
No other investor's score, assessment, question, decision or statement appears in the prompt.
Each returns:

- a statement of about 40 words in no more than 2 to 3 short sentences, judging the team's answer and one change from that investor's lens and on their own merits, in the investor's own voice; server validation requires non-empty text of at most 900 characters,
- a final score from 1 to 10.

The decision follows from the final score: 7 or higher means "I'm in", anything lower means "I'm out". The server derives it from the clamped score, so the decision always matches the score and the model never returns a decision. The prompt tells the model this rule so the statement fits the score.

Every score shown to a team, the room or the moderator carries a compact 1 to 10 scale with the line at 7 marked "I'm in from 7", plus plain words such as "6/10, 1 point short of I'm in" or "8/10, invests". Round 1 is provisional and says "would invest" or "would be 1 point short of I'm in".

Each investor directly assesses the answer to their own question and the one change, and states in the statement why their score rose, fell or held compared with round 1. A concrete, convincing answer and a meaningful one change should move the score up. A final score stays within two points of that investor's own round 1 score and within 1 to 10; the server clamps it after the model output. The team sees the movement next to each final score, for example "5 -> 7", in its own verdict and in the moderator team detail.

The statements avoid stock formulas such as "my assessment holds" or "makes no improvement"; each verdict reads in its own investor's voice.
Because the four verdicts are independent, no investor can bias another.

### Robustness

- Investors judge consistently: scoring uses a fixed rubric and low sampling temperature; statements may use a higher one.
- The shared rubric treats this as a startup pitch event, not a company raising a real round. It judges how well-reasoned, plausible and concrete the idea and relevant numbers and assumptions are inside each investor's lens; when a defense answer is available, it judges that answer too. It does not penalise the absence of real customers, pilots, traction or measured data, and does not demand them as proof. A clear, concrete case inside the investor's lens reaches 7 or 8, an average case 5 to 6, and an underdeveloped one 3 or below.
- No investor sees another investor's score, assessment, question, decision or statement in either round; the final verdict prompts contain only the investor's own round 1 entry and the team's answer to that own question.
- Final scores are clamped to within two points of the investor's round 1 score and to the 1 to 10 range after the model returns, without adding range constraints to the wire schema.
- Investors stay in role and treat any instruction inside a team's text as part of the pitch, never as an instruction to them. No dedicated feature exists for this.
- All model output that reaches the projector passes the provider's content filtering.

## 8. Ranking and audience vote

- Each investor gives a final score from 1 to 10; 7 or higher is "I'm in", anything lower is "I'm out".
- Jury total = sum of the four final scores (maximum 40). It is the tie-break, not the first criterion.
- The one ranking rule used for ranking, finalist selection, tie-breaks and every view that shows jury figures: teams are ordered by the number of "I'm in" decisions, then by jury total, then by the earlier pitch submission.
- The finalists are the top three of that ranking. A team with one "I'm in" therefore ranks above a team with none, even when the second team has more points. If fewer than three teams have an "I'm in", the same ranking fills the remaining places, so the finale always has three teams when that many complete teams exist.
- Wherever teams are ranked or summarised at the reveal and in the moderator view, the main figure is "X of 4 lions invest" and the points appear as the smaller tie-break figure, for example "19/40 points, breaks ties".
- From the leaderboard until the reveal, every public view (projector, team phones and the vote page) names the three finalists neutrally in the pitch order, without the number of lions who invest, the points or any ranking position. The server withholds those figures from the public views in these phases. Each team still sees its own verdict result, and the moderator view keeps all figures. The full ranking and the jury figures first appear at the winner reveal, after the audience vote.
- During Voting, every person in the room votes on their phone for one of the three finalists. One browser gets one vote; a private browser window can still vote again, and that is accepted. Votes are stored in the session database.
- The winner is the finalist with the most audience votes. An audience vote tie goes to the better jury ranking position: more "I'm in" decisions first, then the higher jury total, then the earlier pitch submission. A round with zero votes falls back to the jury ranking order.
- At the reveal, the audience votes and share appear next to the number of lions who invest and the jury total as the tie-break. The full jury ranking is visible from that moment on.
- The audience decides the winner. The AI jury result is shown at the same moment; there is no separate AI step.

The rules are explained before the session starts, in three lines: each lion gives 1 to 10 points and invests from 7; teams are ranked by how many lions invest and points break ties; the top teams reach the final and the audience picks the winner. The same three lines appear on the team view before and after joining and on the projector lobby. A small, quiet note under the rules on the projector lobby and the team lobby, on the team ideation page, and as a one-line reminder on the projector ideation screen announces that the top 3 teams pitch live at the end and the audience picks the winner; one shared wording (`lib/rules.ts`) keeps it identical everywhere, and the explanation of the task stays the main content of the page.

## 9. Technical approach

- One web application (Python backend with FastAPI and a prebuilt React user interface served as static files), started locally with a single command and later deployed as a single container.
- All model calls for the jury run on the server; no key ever reaches the browser.
- Live updates by simple polling; at this scale nothing more is needed.
- Session state and automatic past-run archives persist in a small embedded database, so a server restart during a session loses nothing; Reset archives a non-empty run before clearing the live state.
- Model access through the official OpenAI Python SDK against an OpenAI-compatible endpoint, configured only by environment variables (API key, base URL, model name).
  - Local testing uses an OpenAI API key.
  - A deployment can point the same configuration at an Azure OpenAI endpoint or any other OpenAI-compatible endpoint; switching requires no code change.
- A demo mode returns prepared jury responses without any model call, so the full flow can be developed, tested, and rehearsed without a key or any cost.
- No personal data is collected; team names and one anonymous random voter id per browser. Session data, votes, and archived runs can be deleted after the event.
- Deployment uses one Docker image on any container host, for example Azure App Service (Web App for Containers), with exactly one instance and persistent storage for the SQLite session database.

## 10. Non-goals

- Challenge cards or other idea constraints.
- An in-app chat assistant; teams use their own AI subscription or a free one for generating ideas.
- Counteroffers or negotiation by investors.
- Dedicated prompt-injection features or a "best hack" award.
- Text-to-speech, speech input, and investor emblems.
- User accounts, multiple concurrent sessions.
