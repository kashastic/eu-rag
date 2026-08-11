# UPDATE LOG — decisions and gotchas

Dated, discrete entries. Newest first. Each one is either a **[DECISION]** (a
choice made, with the reason, so it isn't silently reversed later) or a
**[GOTCHA]** (something that cost time once and shouldn't cost it twice).

**Where things live**, so these four don't drift into each other:

| File | Holds | Shape |
|---|---|---|
| **this file** | one entry per decision or gotcha, dated | scannable list |
| [`DEVLOG.md`](DEVLOG.md) | what was built each session, with before/after numbers | narrative |
| [`../CLAUDE.md`](../CLAUDE.md) | the short list a new session must read first | standing rules |
| [`../context_files/PLAN_LIVE_SAFETY.md`](../context_files/PLAN_LIVE_SAFETY.md) | design decisions D1–D17 of the live-safety batch | approved plan |

Rules of thumb: if it's **load-bearing for every future session**, it also
belongs in CLAUDE.md's Gotchas (short form there, full reasoning here). If it
comes with **measurements**, the numbers go in DEVLOG and this file links to
them. Don't paste the same paragraph into three files — link.

---

## 2026-08-11 (Greetings got random answers; the phone had no menu)

### [DECISION] "hello" is answered from a constant, before retrieval

Reported as *"questions like hi / hello / how are you get random responses"*.
The cause is structural rather than a bug in any one component: **retrieval
ranks, it never rejects.** BM25 and vector search return their best k for any
input, so "hello" came back with six passages of EU law and the answerer was
told, correctly, to answer the question using only those sources.

Measured on the live pipeline before the fix (numbers in
[`DEVLOG.md`](DEVLOG.md)):

| input | what shipped | cost |
|---|---|---|
| `hello` | **three verbatim quotes from the Pay Transparency Directive**, mode `extractive` | HyDE + 2 Sonnet + 2 Opus |
| `blah blah` | an honest refusal (good) | HyDE + Sonnet + Opus |
| `how are you doing` | an honest refusal (good) | HyDE + Sonnet + Opus |
| `what is the weather in Berlin tomorrow` | an honest refusal (good) | HyDE + Sonnet + Opus |

So there were two distinct problems, and only the first is a quality problem:
`hello` produced **nonsense with citations**, which on a citation-first product
is the worst failure mode it has. The other three produced *good* answers at a
bad price.

**Fix (`core/smalltalk.py`):** a deterministic whole-string match, run at the
top of `pipeline.query`, answering with a fixed orientation message. No
retrieval, no model call, `mode="smalltalk"`.

Three properties are load-bearing:

- **Whole-string matching, never substring.** "hi, do I need a DPO?" is a
  question and must reach retrieval. A false positive answers a real question
  with a form letter, which is far worse than a missed greeting — that
  asymmetry is the whole design and is why there is no keyword or model-based
  matching in there.
- **It runs before contextualisation.** A bare "thanks" at the end of a thread
  would otherwise be rewritten by the contextualiser into a full standalone
  question and *then* answered at random — the same bug wearing a different
  hat. It also saves the Haiku call.
- **It does not spend a free question.** No model call, no charge — otherwise
  the fix trades a random answer for a shorter free trial. Implemented as a
  refund on all three ask paths (`api.deps.cost_nothing`), since the quota is
  consumed before the pipeline runs.

`MODES_REFUNDED` is deliberately just `{"smalltalk"}`. `no_sources` also skips
the model, but it only fires on an empty index — a broken deployment, not a
user action — and refunding it is unobservable in production while changing
what nine quota tests assert about the one gate on the owner's Anthropic bill.

### [GOTCHA] The relevance floor cannot be built on the cross-encoder score

The open plan for cutting escalation cost (HANDOFF item 2, *"the biggest cost
lever"*) was a **relevance floor**: threshold the cross-encoder's top score and
return `NO_SOURCES_MESSAGE` with zero LLM calls. It was built far enough to
measure and **the measurement killed it.**

Top rerank score, `Xenova/ms-marco-MiniLM-L-6-v2`, HyDE pinned off:

| group | n | min | median | max |
|---|---|---|---|---|
| golden set (English, on-corpus) | 32 | −7.85 | 3.40 | 8.33 |
| off-corpus + gibberish (English) | 16 | −11.11 | −8.99 | **−3.70** |
| **legitimate questions in DE/FR/ES/IT/NL/PL/SV/PT/DA** | 12 | **−11.38** | −9.03 | 2.49 |

The English-only groups nearly separate (a threshold near −8.5 would work).
**The third row destroys it.** A German question about late-payment interest
scores −11.38; Polish about breach notification −11.31; Italian about high-risk
AI systems −10.71 — *below* the worst English gibberish ("asdkjfh qwerty",
−11.11 aside, the rest sit above −10.9). The cross-encoder is trained on
English MS MARCO; the corpus and the product are not English-only, and
`answerer` promises to answer in the question's language.

Any threshold that catches "blah blah" therefore tells a Polish SME its
question falls outside the corpus — silently, on the product whose whole claim
is that it says so honestly. **No floor shipped.**

What shipped instead is the *instrument*: `top_score` now rides the
unconditional `query outcome:` line. The question is decidable from real
traffic, which is what was missing all along; it is not decidable from a
laptop and a hand-written list of 60 questions. If it is ever revisited, the
signal needs to be language-neutral — the model's own zero-citation refusal is
the obvious candidate, since it comes from reading the sources rather than from
an English encoder.

### [GOTCHA] A git worktree has no `.env`, so the "before" run is a different pipeline

Taking before/after harness numbers in a `git worktree` of HEAD gave
doc_hit 94% before and 100% after — a spectacular improvement from a change
that touches no ranking at all. `.env` is **gitignored**, so the worktree had
no `ANTHROPIC_API_KEY`: the contextualiser silently fell back to
`ExtractiveClient` and the three follow-up cases retrieved on their raw
fragments. This is the documented "`.env` is loaded by importing
`core.config`" trap wearing a worktree costume — and it flatters the change,
which is the dangerous direction.

Correct A/B pins **every** model in the loop, not just HyDE:
`EURAG_HYDE_MODEL=none EURAG_CONTEXTUALIZE_MODEL=none`. Pinned, before and
after are identical to the digit (94% / 0.94 / 87% / 67%), which is the
expected result: `retrieve()` is now a wrapper over `retrieve_scored()` and
ranking is untouched.

### [DECISION] The narrow-screen sidebar is off-canvas, not `display: none`

Below 720px the sidebar was simply hidden, and **nothing replaced it**. That
removed New chat, the saved-chat list, the account name and sign out from every
phone — so on mobile an account could not switch between its own saved chats,
which is most of what an account is for. Anonymously it also removed the only
"Sign in to save chats" button on the page.

Now a drawer: `transform: translateX(-100%)` with a scrim, a hamburger in the
masthead, dismissed by the scrim, a ✕, Escape, or picking a chat.
`visibility: hidden` while closed, or a keyboard user tabs into an off-screen
chat list.

**The trap it set:** the masthead title was styled by `.pane-head > span:first-child`,
and the menu button is the first child at *every* width (it is `display: none`
on desktop, not absent from the DOM). That silently unstyled the title on the
**desktop** layout — a change that is only supposed to affect phones. Now
`:first-of-type`. Verified by rendering at 1440 as well as 390 and 360, which is
the only reason it was caught.

## 2026-08-10 (The third question in a thread returned a validation error)

### [GOTCHA] A cap that rejects, on text the user did not write

Anonymous follow-ups reached
`422 answer: String should have at most 2000 characters` — rendered in the
transcript where the answer belongs — from the third question of a thread
onwards. `/query` is stateless, so the client sends prior turns; `HistoryTurn`
capped both fields with `max_length`, which **rejects**. Answers routinely run
past 2000 characters, so the request failed as soon as a long one was behind
you.

Three things made it worse than an ordinary validation error:

- The over-long string is **EURAG's own previous answer**. The user was refused
  for text they did not write and could not shorten.
- **Request validation runs before the route body**, so it failed ahead of the
  bot gate and the quota check. The user saw a validation error rather than the
  login wall they were actually due.
- The docstring already said *"answers are trimmed hard because only their topic
  matters to the rewrite"*. **Nothing trimmed them.** The comment described the
  intended behaviour and the code did the opposite, which is why it read as
  correct in review.

**Fix:** prior turns are truncated, not rejected (`HISTORY_CHARS`, a
`mode="before"` validator). Nothing is lost — the only consumer is
`QueryContextualizer.standalone`, which already reads at most
`MAX_ANSWER_CHARS` (400) of an answer and only the last `MAX_TURNS` (3) turns.
The API was refusing requests over text the pipeline would have sliced off
anyway. The client now trims to 600 chars before sending as well, so a
follow-up stops shipping kilobytes of prose for nothing.

The number of turns is still `max_length` and still rejects — that one is a
real bound on request size, and the client controls it.

**The lesson worth keeping:** when a bound exists to protect a downstream
consumer, check what that consumer actually reads. If it truncates, the bound
should truncate too; rejecting turns a non-problem into a user-visible failure.

---

## 2026-08-10 (Signing in destroyed the conversation that caused it)

### [GOTCHA] The login wall fired, and the sign-up threw away the thread

`onLoggedIn` called `setAnonMsgs([])`. The anonymous thread lived only in client
state, so signing in discarded it — and because the wall is raised *by*
`anonymous_limit_reached`, the moment of sign-up is precisely when the user has a
conversation they care about. They came back to an empty screen.

Reported against Google sign-in, but `onSuccess` is shared: **both** the password
and Google paths did it.

**Fix:** `POST /conversations/import` adopts the turns into a real saved chat
before the state is cleared, and the client opens that chat. Because the turns
are stored, `_history()` picks them up, so the next question is a follow-up with
the earlier context — the thread is continuable, not just visible.

### [DECISION] Import stores verbatim: no model call, no quota spend

The obvious implementation is to replay the questions through
`POST /conversations/{id}/messages`, and it is wrong twice over: it would bill
the user (or the server) a second time for answers they are already looking at,
and it would produce *different* answers than the ones on screen, which is a
strange thing to do to a transcript someone is watching.

So the route writes the turns as they were. That is also what stops it being a
free-answer path — it never reaches a model, and there is a test asserting
`pipeline.query` is not called. Citations are preserved deliberately: dropping
them would leave a citation-first product showing a transcript whose answers
appear uncited.

The content is client-supplied, but it lands only in the caller's own private
conversation — which they could fill with anything by typing — so it is capped
and shape-checked rather than trusted or refused.

### [GOTCHA] A failed import must not be the same as no import

The client only clears `anonMsgs` when the import **succeeds**, and the
transcript falls back to rendering them when a signed-in user has no active
chat. A network blip therefore leaves the conversation on screen rather than
reproducing the bug it was written to fix.

---

## 2026-08-10 (Visual identity — black ink on bond paper)

### [DECISION] The home screen explains the product by behaving like it

Brief was "lawyer theme, black and white paper, and say what this actually is".
The "what is this" copy is not a pitch paragraph: the opening statement carries
real superscript citation markers, and they resolve to a rail of three real
corpus documents (GDPR, AI Act, Late Payment, with their CELEX ids). Hovering or
tabbing a marker lights its footnote and vice versa — the same apparatus, and
the same two-way link, that an answer uses.

The point is that EURAG's single claim is *every sentence resolves to a numbered
article*. A page that asserts that in prose is marketing; a page that does it in
front of you is evidence. It also means a first-time visitor has already learned
to read the citation apparatus before they ask anything.

**The three cited documents must stay real.** If the corpus ever drops one, the
rail is a lie on the front page.

### [DECISION] One accent, and what it is allowed to mark

Palette is bond white `#fbfaf7` / wet black `#0c0c0d` / pencil grey, plus a
single oxblood `#7b1420`. Seal ink is allowed on exactly four things: the
masthead star, citation markers and their footnotes, the not-legal-advice
stamp, and destructive actions. **A fifth red thing makes the four that matter
stop reading as marked** — the CSS header says so; keep it true.

The stamp is the load-bearing one. Dressing a compliance tool in a law firm's
clothes raises the odds someone mistakes it for advice, so the disclaimer went
from a murmured grey line to a bordered, rotated stamp in seal ink. That is a
design responsibility created by the theme, not decoration.

### [DECISION] Typefaces were fixed before the redesign started

Fraunces / Source Serif 4 / IBM Plex Mono are self-hosted, and adding a fourth
would mean a font CDN — which would send every visitor's IP to a third party
and contradict what `/privacy` states in writing. So the identity shift had to
come from colour, structure and one signature device. Recorded because "the
display face is a bit generic, let's add one" is the obvious future suggestion
and it is a privacy regression, not a taste question.

### [GOTCHA] `1fr` grid tracks floor at min-content and blow out mobile

Every content-bearing grid track in `globals.css` is `minmax(0, 1fr)`, never a
bare `1fr`. A plain `1fr` is `minmax(auto, 1fr)`, and `auto` floors at
**min-content** — so one `white-space: nowrap` string anywhere inside (the
masthead badge, a threshold label) sets a floor the column cannot go below, and
the whole page ends up wider than the phone. The fix is the track definition,
not `overflow-x: hidden` on an ancestor: that converts an overflow into silent
clipping, which is harder to notice and looks like a text bug.

### [GOTCHA] Headless Chrome's `--window-size` does not set the layout viewport

`--screenshot` with `--window-size=390,844` produced a 390px-wide *image* of a
**500px-wide layout**, cropped — so the right-hand side of every element looked
chopped off and a perfectly fine page looked catastrophically broken on mobile.
Two rounds of CSS "fixes" went into a bug that did not exist. `--headless=new`
behaves the same.

**Confirm the viewport before believing a mobile screenshot.** A probe that
appended `document.documentElement.clientWidth` into the DOM, read back via
`--dump-dom`, is what settled it: `VW=500 SW=500`, i.e. no overflow at all.
To actually render at a phone width, load the page in a sized `<iframe>` from a
wrapper page and screenshot the wrapper — the iframe gets a genuine layout
viewport. `infra/scripts/` has no tooling for this; it was ad hoc in the
scratchpad.

### [DECISION] Starters go above the business-context block

Asking is the action on this screen; the profile is an optional refinement.
With the context block first it pushed the four starter questions off a
1440x900 laptop screen, which put a form where the primary action belongs.

---

## 2026-08-10 (Business context on the intro screen)

### [DECISION] The asker's context is a closed vocabulary, never free text

**What changed.** The free-text `Industry · optional` box above the composer is
gone. In its place: four optional dropdowns — country, company size band,
sector, AI role — offered on the intro screen and stored (`core/profile.py`,
`frontend/web/lib/profile.ts`).

**Why enums and not a text box, which was less work.** The context sentence is
injected into the prompt **outside** the `BEGIN SOURCES` fence — i.e. in the
region `SYSTEM_PROMPT` tells the model to *obey*, as opposed to the sources,
which it is told to quote and never follow. The old free-text field therefore
wrote user-authored text straight into the trusted region. It was bounded at 80
characters and sat next to the question, which is untrusted anyway, so it was
never dramatic; but the change here makes it **persistent** — set once, then
attached to every future question including inside saved chats — and a
persistent injection surface is a different animal from a per-query one.

So the user picks an option and **the server writes the sentence**. Validation
is `api.routes.query.ProfileBody` (422 on anything off-list); `describe()` in
`core/profile.py` silently drops unrecognised values as a second layer, so a
value that somehow got past the boundary still cannot reach a prompt. There is a
test for exactly that (`tests/test_answerer.py`), and one asserting the sentence
lands after `END SOURCES`.

**Do not add a free-text field to this model.** "Describe your business" is the
obvious next request and it reopens the whole thing.

### [DECISION] The AI field asks for the *role*, not for usage

`none` / `deployer` / `provider`, not a yes/no. Provider vs deployer is the
load-bearing distinction in the AI Act (Reg. 2024/1689, `32024R1689`, in the
corpus): a business using someone else's AI tool and a business putting its name
on one have obligations that differ by an order of magnitude. A boolean cannot
separate them, so it could not change an answer usefully — it would have been a
survey question rather than a retrieval-relevant one. The fragment uses the
Act's own vocabulary so the model can connect it to the Art. 3 definitions when
they are retrieved, and deliberately does **not** assert what either role owes.

### [DECISION] Context reaches the answerer only — retrieval stays blind

`pipeline._answer` passes the profile to `answer_question` and **not** to
`retriever.retrieve`, keeping the boundary that was already there for
`industry`. Folding country/sector terms into the query before BM25 is
plausible — national funding questions would likely benefit — but it is a
retrieval change, so standing rule 1 binds, and the golden set has **zero**
context-conditioned cases to measure it with. Building the eval is the
prerequisite, not an afterthought. Harness numbers are unchanged by this batch
because the harness sends no profile: same prompt, byte for byte.

### [DECISION] `describe()` warns the model off concluding size from headcount

The size bands are headcount-only, but the EU SME definition (Rec. 2003/361)
also turns on turnover and balance-sheet totals, and so do CSRD and NIS2. A
prompt that says "a small business (10-49 people)" and stops there invites the
model to conclude an SME exemption applies. The fragment therefore carries an
explicit "headcount alone does not settle it", alongside the more general "this
is not evidence; never state an obligation applies unless a cited source
supports it" and "if the sources do not answer the question for this asker, say
so". That last clause protects the `INSUFFICIENT_SOURCES` path: the profile must
never talk the model out of an honest refusal.

### [GOTCHA] The settings dialog only renders when `account` is set

The obvious place for the profile editor was `SettingsModal`, which is gated on
`{settingsOpen && account && …}` — so for an **anonymous** visitor, the natural
default here, the edit button would have opened nothing at all. That is the same
dead-button failure as the `/login` register form and the Turnstile-gated Ask
button, for the third time. The profile editor is therefore its own modal,
rendered regardless of auth state. **Check what a component is gated on before
hanging an anonymous-reachable control off it.**

### [DECISION] The intro block is offered, never a gate

It renders above the starter cards, the composer stays enabled behind it, every
field is optional, and Skip is remembered. EURAG is anonymous-first on purpose;
a wizard in front of the first question is the same mistake the always-visible
Turnstile checkbox was, in new clothing.

### [DECISION] `query outcome:` carries the profile; the old industry line is gone

`query industry context:` fired only when an industry was set, which made it
useless as a denominator. The profile now rides `query outcome:` — the one
unconditional per-query line — as `profile=country=DE,size=small,…` (ASCII, no
spaces, greppable). This is the first time HANDOFF's long-open *"which sectors
matter for Tier-2 sector law"* question is actually countable: free text never
aggregated, enums do.

**Wire compatibility:** `industry` is still accepted on both ask routes and is
**not forwarded anywhere**. Tabs open across the deploy would otherwise 422
mid-session. Remove after one release.

### [GOTCHA] `frontend/static/` posts to the same routes and was not in the plan

The plan covered `frontend/web/` and forgot the zero-dep static UI, which is
the local single-user mode and posts to `/query` directly. Because `industry` is
accepted-and-ignored, nothing broke loudly — the static UI would simply have
kept a visible "Your industry" box that the server had quietly stopped reading.
A dead control that looks alive is worse than a 422. It now ships the same four
dropdowns.

**The vocabulary therefore lives in three places** — `core/profile.py` (the
authority), `frontend/web/lib/profile.ts`, and the inline `PROFILE_OPTIONS` in
`frontend/static/index.html` — and **neither frontend has a build step that
would catch drift**: a stale value is a 422 the user experiences as a dropdown
that doesn't work. `tests/test_profile.py` parses both frontends and compares
against the server's vocabulary. That test was verified by injecting a drifted
value and watching it fail, because a comparison test that silently matches
nothing is decoration.

---

## 2026-08-10 (Privacy notice, terms, self-service erasure)

### [DECISION] No cookie banner — because there is nothing to consent to

**The question that started it:** every other site asks for cookie consent, so
why doesn't EURAG, and shouldn't it, to look production-grade?

**What the code actually does.** Audited before answering: the API sets **no
cookies at all** (no `Set-Cookie` anywhere), there is no analytics product, no
advertising, and no third-party tracker. The only thing stored on the visitor's
device is the session pair `eurag_access` / `eurag_refresh` in `localStorage`
(`frontend/web/lib/api.ts`).

**Why that means no banner.** The consent rule is ePrivacy Art. 5(3), and it
covers storing or accessing *any* information on the user's device —
`localStorage` counts, not just cookies. The carve-out is storage strictly
necessary for a service the user explicitly requested. Session tokens are the
textbook example; Turnstile is a security measure (the other established
exemption); Google Identity Services loads only after the user clicks
"Continue with Google". Nothing here falls in the consent-requiring bucket,
which is the bucket every banner you have ever clicked exists to serve.

**So the banner would be worse than nothing.** It would cost conversions on a
funnel that is only 2 anonymous questions wide, train people to dismiss
dialogs, and — since it would be managing consent that is not being collected —
be a false statement on a site whose entire pitch is EU compliance.

**Do not "add the missing cookie banner" in a later session.** If analytics is
ever added, that is the moment this decision is reopened; cookieless
self-hosted analytics keeps the exemption, anything else does not.

### [DECISION] The real gap was transparency, not consent — so /privacy and /terms exist now

The audit turned up personal data with no notice attached to it anywhere:
client IP stored as the anon-quota key (~2 days), username, email and
`google_sub` for Google accounts, saved chat text, the encrypted BYOK key, the
audit log, and — most disclosure-worthy — **every question is sent to Anthropic
in the US**, which nothing on the site said.

`/privacy` and `/terms` (`frontend/web/app/{privacy,terms}/page.tsx`, shared
constants in `lib/legal.ts`) now state all of it, linked from the composer's
disclaimer line so anonymous visitors reach them too. The pages are written to
be **checkable** — every claim maps to something in the code.

**`CONTACT_EMAIL` (`lib/legal.ts`) is a dedicated mailbox**, not the operator's
personal address and not a plus-alias — a `+tag` strips back to the real inbox,
so it publishes the personal address either way. A dedicated box also survives
EURAG getting a company or a custom domain. It is a published legal document's
contact route: if it stops being read, the notice stops being true.

### [DECISION] The web fonts are self-hosted, and must stay that way

`app/layout.tsx` loaded Fraunces / Source Serif 4 / IBM Plex Mono from
`fonts.googleapis.com`, so **Google received every visitor's IP on every page
view** — including visitors who never sign in, on a site selling EU compliance,
and matching the pattern German case law has gone against. Fixed the same day
the privacy notice went in, because the notice would otherwise have been
promising a fix rather than describing the product.

`frontend/web/app/fonts.css` (generated, 26 `@font-face` rules) + 26 woff2
files in `public/fonts/`, ~988 KB on disk. Google's own `unicode-range` rules
are kept verbatim, so a browser still downloads only the subsets a page needs —
about 356 KB for a Latin-alphabet visitor, the same bytes Google was serving.
Two edits to what Google returned: the **vietnamese** subset is dropped (not an
EU language — those glyphs fall back to a system face), and weights that share
one file are variable fonts, so they collapse from three rules into one
`font-weight: 400 700` range instead of storing the same bytes three times.

All three families are SIL OFL 1.1, which permits self-hosting. The refresh
procedure is in the file's header comment. **Do not revert this to a `<link>`
for convenience** — `/privacy` states in writing that reading the site tells
Google nothing.

### [DECISION] `public` and `deleted_account` are reserved usernames

A username here is not a label: `register` sets `tenant = username`, and the
audit log keys on it. So `RESERVED_USERNAMES` is refused by `register` and
skipped by `_free_username` (the Google-derived one — nothing stops someone
owning `public@` at their own Workspace domain, and the derived name would
otherwise be `public`; it now falls through to `public2`).

- **`public`** — the shared official corpus. That account's uploads would land
  in the 47 documents everyone reads, and its deletion would try to erase them.
- **`deleted_account`** — the erasure tombstone. A real account by that name
  would inherit every erased user's audit rows, and its own rows would be
  indistinguishable from erased ones.

Anything new that keys on a username string belongs in that set.

### [DECISION] Erasure pseudonymises the audit trail rather than deleting it

`DELETE /account` erases the user row, refresh tokens, saved chats, uploaded
documents and the lifetime quota row. The audit log instead has its `actor`
rewritten to `deleted_account` (`ERASED_ACTOR`).

Deleting those rows outright would hand anyone a way to erase the evidence of
their own attack: register, hammer another account, delete, and the
`auth.login_failed` trail goes too. Rewriting the actor keeps what the trail is
*for* while dropping what makes it personal data. A per-user pseudonym was
rejected — a stable per-person token is still an identifier. This is the one
exception to the audit log being append-only, and `core/security/auth.py`'s
module docstring now says so instead of claiming no update path exists.

Dropping the `user_quota` row does **not** weaken the free-tier cap: the cap is
per account, and registering a second account has always started a fresh
allowance, so keeping the row would retain a username and buy nothing.

### [GOTCHA] A user's tenant WAS their username with `public` registerable

`AuthStore.register` sets `tenant = username`, and `public` satisfies the
username rule (3-40 chars, alphanumeric). Self-service deletion therefore had a
path to `pipeline.erase_tenant("public")` — **erasing all 47 official
documents**. The admin route had guarded this at the route; the guard is now
inside `Pipeline.erase_tenant`, where it holds for every caller, and the
account route skips document erasure for a public-tenant account rather than
500ing.

**Closed the same day** by `RESERVED_USERNAMES` (see the decision above), which
stops the account existing in the first place. Both layers stay: the reserved
list is the fix, the `erase_tenant` guard is the floor that holds even if some
future code path invents a tenant name another way.

### [GOTCHA] A stateless JWT outlives the account it names

The first version of the erasure test failed on `GET /account` returning **200
for a deleted user**. `verify_access` only checks the signature, so a 15-minute
access token kept working after the account was gone: the UI would still look
signed in, and the ghost session could ask questions against a quota row that
erasure had just reset.

`api.deps._still_exists` now rejects a token whose account no longer exists, so
every authenticated request pays one primary-key lookup. That is a real cost
against the "validated statelessly by any instance" property — accepted,
because *"deleted immediately"* is a promise now printed on `/privacy`, and the
ask path already did a lookup of its own (`paid_tier` → `get_byok`). Anonymous
requests never reach the check.

### [GOTCHA] `httpx` has no `json=` on `.delete()`

`DELETE /account` takes a typed-username confirmation in the body, and
`TestClient.delete(json=...)` silently isn't a thing — the tests go through
`client.request("DELETE", …, json=…)`. Browser `fetch` sends a DELETE body
fine, so this bites only the test suite. The confirmation stayed in the body
rather than moving to a query string because a username in a URL lands in
every access log in the chain.

---

## 2026-08-09 (Google Sign-In)

### [GOTCHA] Anything in `_SCHEMA` that references a NEW column takes the API down on every existing database

**Symptom.** Straight after a deploy: `/healthz` returned no JSON at all
(`python3 -m json.tool` → *"Expecting value: line 1 column 1"* — that is Caddy's
error page, not an API response), the UI showed **0 documents indexed** instead
of 47, and the Google button never appeared. Three symptoms, one cause: the API
container never finished starting, and the frontend reads `documents` *and*
`google_client_id` from `/healthz`, so a dead healthz blanks both.

**Why.** `AuthStore.__init__` runs `db.executescript(_SCHEMA)` and *then* the
`ALTER TABLE` migrations. A `CREATE UNIQUE INDEX ... ON users (google_sub)` had
been added to `_SCHEMA` — so on an **existing** database, where every
`CREATE TABLE IF NOT EXISTS` is a no-op and `google_sub` does not exist yet, the
index statement raised `UndefinedColumn` out of `__init__`, out of the lifespan,
and the app never came up. On Postgres it is worse than one bad line:
`executescript` sends the whole script as a **single** statement, so one failure
discards all of it.

**Why no test caught it.** Every test built its database from scratch, where the
`CREATE TABLE` already contains the new column and the index succeeds. *A fresh
database cannot test a migration.* There are now two SQLite tests and one
Postgres test that start from the pre-Google table on purpose.

**The rule.** `_SCHEMA` may contain **only** statements that are correct against
the table's *old* shape. Anything that depends on a newly added column —
indexes, constraints, backfills — belongs after the `ALTER`s, in the
individually-guarded migration list. And verify a schema change by opening it on
a copy of the old database, not a new one.

### [DECISION] Google sign-in uses the ID-token flow, so there is no client secret

**Choice.** Google Identity Services hands the browser a signed ID token; the
browser POSTs it to `/auth/google`; `core/security/google_oauth.verify_id_token`
verifies it and the server mints its own JWT pair. **Not** the
authorization-code flow.

**Why.** EURAG wants an *identity* and never calls a Google API on the user's
behalf, so the code flow buys nothing and costs: a client secret to keep out of
the repo and rotate, a `/auth/google/callback` route, and server-side `state`
/PKCE storage that has to work across replicas. The ID-token flow needs only
the **client id, which is public by design** — it ships to every browser, so
`EURAG_GOOGLE_CLIENT_ID` is the one config value here that is deliberately not
a secret. It reaches the frontend at runtime via `/healthz`, the same pattern as
`turnstile_sitekey`, so enabling Google sign-in is an env change and not a
frontend rebuild.

**What makes it safe is the verification, so it is strict**: RS256 only (never
the token's own `alg`), signature against Google's JWKS, `iss` must be Google,
`exp`/`iat` enforced, `email_verified` must be true — and **`aud` must equal our
client id**. That last one is the load-bearing check: a token minted for someone
else's app is a genuine, correctly-signed Google token and still must not be a
login here. There is a test for each of these, including `alg=none` and a token
signed by a different key.

Unlike the Turnstile check, this one **fails closed on an unreachable JWKS** —
Turnstile fails open because the per-IP quota still bounds abuse, but this route
mints a session, and there is no safe way to wave that through.

### [GOTCHA] A Google login must never be able to land on an existing account

**The attack.** Register the username `alice` with a password, then wait for the
real alice@example.com to sign in with Google. If the Google path matched an
account by username — or by email — she would be handed the squatter's account,
along with their saved chats and stored API key. Account takeover by land-grab.

**The rule.** `upsert_google_user` keys on `google_sub` and **only** on
`google_sub` (stable and never reused by Google, unlike an email address, which
can change hands). A derived username that is already taken is *skipped*, not
reused — `alice` → `alice2`. There is a test named for this attack; don't
"simplify" it away.

Linking a Google identity to an existing password account would need proof of
ownership of that account, and existing accounts have no email to prove it with,
so the two identities are simply separate. A Google account stores an **empty
`pw_hash`** and `authenticate()` refuses it outright rather than comparing
against a value no input could produce.

## 2026-08-09 (tiers)

### [DECISION] The logged-in free tier is 10 questions for the life of the account, not per day

**Choice.** `EURAG_FREE_USER_QUESTIONS` (default 10), counted by
`core.quota.UserQuota` with **no day column** — it never resets. Spent → 402
`free_limit_reached`, and the only way on is BYOK.

**Why not per-day, like the anonymous tier.** The two quotas answer different
questions. An anonymous visitor has no account to attach a history to, so the
only sane reset is time. A logged-in user does — and the point of this gate is
that the server's key **stops** paying for a returning free user, which a daily
reset never achieves. This is a conversion gate, not a rate limit; the rate
limiter already exists separately.

**Two things that would each have been a bug:**

1. **There are two logged-in ask paths.** `/query` with a bearer token *and*
   `POST /conversations/{id}/messages`, which is the one the web app actually
   uses for saved chats. A gate on one door is not a gate, so it lives in
   `deps.spend_free_question` and both routes call it. There is a test that
   spends through one door and asserts the other counted it — that test is the
   point, don't delete it when refactoring.
2. **402, not 401.** The web client treats 401 as "refresh the session token"
   and would loop instead of showing the wall. This is the *same* trap already
   recorded for `byok_key_rejected`; it has now cost thinking twice, so: any new
   "you may not do this, but you are correctly logged in" response is 402/403,
   never 401.

BYOK skips the counter entirely rather than zeroing it, so the remainder is
frozen — removing a key returns the user to whatever free questions were left.

### [DECISION] Say plainly what happens to a user's API key, including the part that isn't reassuring

**Choice.** The BYOK dialog states that the key is stored on the server and
decrypted on each question, so **whoever operates the server can technically
read it**, and tells the user to create a dedicated key with a spend limit and
revoke it at Anthropic when done. Removing a key in EURAG is labelled as *not*
revoking it upstream, and a stored key past 30 days gets a rotation nudge (new
`users.byok_set_at` column).

**Why.** The old copy — "Stored encrypted; never shown again" — is true and
reads as a stronger guarantee than the design provides.
`EURAG_ENCRYPTION_KEY` lives on the same host as the database, so encryption at
rest defends against a stolen dump or a Postgres-only compromise, **not** against
root on the box or the operator. Asking strangers for an unscoped credential
that can spend their whole Anthropic balance and implying it's unreadable is the
kind of thing that is fine right up until it isn't.

**Treat weakening this copy as a security regression** — the honesty *is* the
mitigation, because the technical control can't be strengthened without moving
the encryption key off the host (a KMS), which this deployment doesn't have.
Full reasoning: [SECURITY.md](SECURITY.md) → "BYOK: what encrypting the user's
key does and does not protect against".

## 2026-08-09 (web UI)

### [GOTCHA] FastAPI's `detail` has three shapes, and the one we didn't parse made every 422 silent

**Symptom.** Reported from the live site: *"nothing happens when I click Create
account or Sign in."* The button un-greyed and no error appeared. The password
was 9 characters against a 10-character minimum.

**Why.** `lib/api.ts` parsed the error body as either a plain string or one of
our structured `{code, message}` objects:

```js
const message = d && typeof d === "object" ? d.message : d || `HTTP ${status}`;
```

FastAPI returns **request-validation** failures as an *array* of pydantic
errors. `typeof [] === "object"`, so the array took the object branch and
`d.message` was `undefined` → `new ApiError(422, undefined)` → `err.message` is
`""` → `{error && <p className="err">{error}</p>}` rendered **nothing**, because
the empty string is falsy. Not just the password: every field-level 422 in the
app was invisible the same way (username length/charset, a 2-character
question).

**Fix.** `errorFrom()` handles all three dialects and is contractually unable to
return an empty message. Both call sites also fall back to a non-empty string,
and the login form now checks the API's own bounds client-side so a short
password is answered instantly instead of costing a round trip.

**The general rule.** An error path that renders `{msg && …}` needs a
**guaranteed non-empty** message — otherwise a parser miss doesn't show a wrong
error, it shows *no* error, which the user reads as a dead button. Test the
failure shapes, not just the happy path: this was reachable from the very first
form on the site, and the whole flow had been verified end to end with *valid*
input.

### [DECISION] Turnstile is invisible and runs at submit time, never on page load

**Choice.** `components/Turnstile.tsx` renders with `appearance:
"interaction-only"` + `execution: "execute"` and exposes one imperative method,
`getToken()`. Callers mint a fresh single-use token **when the form is
submitted**. No button is ever disabled waiting for a challenge.

**Why.** The old widget rendered `appearance: "always"` on mount and the parent
gated Ask / Create-account on `!tsToken`. Three things fell out of that, and the
third is the serious one:

1. A **72px Cloudflare checkbox sat permanently above the composer** on every
   anonymous page load — the first thing a visitor saw on a page whose whole
   pitch is "ask a question."
2. It solved on load, so by the time a visitor typed a question the token could
   already be minutes into its 300s life. Execute-on-demand is always fresh.
3. **A challenge that never completes left a dead UI with no error text.**
   Measured with `context.route(...abort())` on `challenges.cloudflare.com`:
   Ask *and* Create-account were `disabled` forever and nothing on screen said
   why. Any ad blocker, privacy extension, corporate proxy, or sitekey/domain
   mismatch reproduces it — and a sitekey mismatch is exactly the failure a
   fresh deploy is most likely to hit.

**The general rule.** *Never gate a submit button on a third-party widget's
success.* Ask for the token at submit time, and treat "couldn't get one" as an
error you show, not a state you sit in. `TurnstileUnavailableError` carries a
visitor-facing message naming the likely cause (blocked
`challenges.cloudflare.com`); the server's fail-closed policy on a missing token
is unchanged — see [SECURITY.md](SECURITY.md).

**Two details worth keeping.** The container is a **0px box** until Cloudflare
paints a challenge into it, so spacing is driven by an `.active` class set from
`before-interactive-callback` / `after-interactive-callback` — the widget lives
in a shadow root, so there is no child selector (`:has(iframe)` matches
nothing). And `CHALLENGE_TIMEOUT_MS` is **120s**, not 30s: an interactive
challenge is waiting on a person noticing a widget that just appeared. While it
is up, the thread says "Waiting for the Cloudflare check below" instead of
"Consulting the corpus", which would be a lie.

### [GOTCHA] A second copy of a form is a second thing to keep correct — `/login` had rotted

**Symptom.** Creating an account at `/login` failed with *"Verification failed —
please retry the challenge"* on any deploy with the bot gate on, with no way to
recover from the page.

**Why.** `app/login/page.tsx` was a standalone copy of the sign-in form written
before the bot gate existed. It posted `/auth/register` with **no Turnstile
token and no widget to produce one**, so `api/routes/auth.register` rejected it
every single time. The modal on `/chat` had been updated; this copy had not, and
nothing linked to it (`/` redirects to `/chat`), so it was never exercised.
Locally it "worked" because no Turnstile secret is configured — the gate is
skipped, which is exactly why this only ever failed in production.

**Fix.** `/login` is now a redirect to `/chat?auth=login`; the modal is the one
sign-in UI. The query param is read from `window.location` rather than
`useSearchParams` so the client page still prerenders without a Suspense
boundary.

**The general rule.** When a gate is added to one entry point, grep for the
other callers of the endpoint it protects — a duplicate form that isn't linked
is a duplicate form nobody will notice is broken.

## 2026-08-09

### [GOTCHA] A per-query metric needs an unconditional log line, or it has no denominator

**Symptom.** Counting production escalations returned `0` and `0`. Nothing in
that result distinguishes "it never happened" from "there was no traffic" from
"the log history was wiped by a redeploy" from "the grep pattern was wrong."

**Why.** Every per-query log line in the pipeline fired on a *branch* —
`query industry context:` only when an industry is set, the contextualiser line
only on follow-ups, the escalation line only on escalation. So the numerator
had a log and the denominator had none. A second trap sat underneath: one of
the patterns contained the **em dash** from `"low-confidence answer — escalating
to %s"`, which can fail on encoding before it ever reaches a real question about
traffic.

**Fix.** `pipeline.query` now emits exactly one `query outcome:` line per query,
unconditionally and in ASCII, with the fields needed to divide:
`mode`, `escalated`, `primary_reason`, `insufficient`, `citations`.

**The general rule.** Before instrumenting a rate, ask what emits the
denominator. If the answer is "the same branch as the numerator," the number
will be uninterpretable no matter how carefully it is counted. And keep log
strings intended for grepping ASCII-only.

### [DECISION] An honest refusal may cite nothing — but only if it is short

**Choice.** `answer_question` accepts a zero-citation answer when the
insufficiency marker is present and the text is ≤ `MAX_UNCITED_REFUSAL_CHARS`
(600). Longer uncited bodies, fabricated markers, and uncited answers without
the marker all keep the old extractive downgrade.

**Why.** `SYSTEM_PROMPT` tells the model to cite nothing when the sources don't
cover the question; `validate_answer` required ≥1 citation unconditionally. The
model was being **rejected for obeying** — refusal, retry, refusal, downgrade to
verbatim quotes from the very chunks it had just refused to use, then escalation
repeating the whole loop on the expensive model. Four LLM calls, and a
user-facing answer worse than the one the model wrote. Numbers in
[`DEVLOG.md`](DEVLOG.md).

**Why a length cap rather than trusting the marker.** The marker alone is not
enough: a model could emit a full substantive answer with `INSUFFICIENT_SOURCES`
tacked on, and accepting that would ship an uncited legal claim — precisely what
citation enforcement exists to prevent. A refusal is a sentence or two. Length
is a crude signal, but it fails in the safe direction (a long refusal merely
gets the old behaviour), which no content heuristic would.

**The `not used` condition is load-bearing**, not redundant with "no citations":
an out-of-range marker leaves `used` non-empty, so a refusal citing a
hallucinated `[9]` is still rejected. The only validation failure this rescues
is the literal absence of citations.

**What it does not do.** The escalation still fires — `primary_reason` just
moves from `uncited` to `marker`. A model reporting a corpus gap is the case
deeper retrieval exists to rescue. This halves the cost of that path; reducing
the escalation *count* needs the relevance floor (HANDOFF open item 3).

### [DECISION] Instrument the escalation rate before fixing what inflates it

**Choice.** The `query outcome:` telemetry was built to ship ahead of the fix
above, so the pre-fix rate could be measured. **In the event both shipped in the
same session** — the fix was requested immediately, and the ordering cost
nothing because the log window contained no traffic to measure (the first count
came back `0` over a `0` denominator).

**Why it is still recorded.** The reasoning holds for the next one: a fix and
its own measurement are entangled, and shipping them together forfeits the
evidence of whether the fix was worth making. It was safe to collapse here only
because the "before" number was known to be empty. Check that before collapsing
them again.

**Reversible.** The telemetry is one log line and one unexposed dataclass
field; nothing downstream reads them.

### [GOTCHA] A rewrite step upstream of a guarantee must carry that guarantee

**What.** After follow-up contextualisation shipped, an **English** question
came back **answered in Spanish** in production.

**Why.** `answerer`'s prompt already says *"Write the answer in the same
language the question is written in — never switch languages on your own."*
It obeyed perfectly — it answered in the language of the question **it was
handed**, and that question was the contextualiser's rewrite, which had
silently translated. The guarantee lived at the answerer, but the answerer no
longer sees the user's words.

**The general rule.** Inserting a rewrite step in front of a component that
promises something about its input **moves the responsibility for that promise
to the rewrite**. Before adding another pre-retrieval or pre-generation
rewrite, re-read `answerer`'s prompt and carry every input-shaped promise into
the new step.

**Do this.** `_CONTEXTUALIZE_SYSTEM` now pins the output language, and
`tests/unit/test_expansion.py` asserts the constraint is still in the prompt —
behaviour depends on a model, but deleting the line would reintroduce the bug
invisibly.

**Also learned here:** phrasing matters more than emphasis. The first attempt
put the language rule first and stated it forcefully; the model read it as
"don't change the question" and stopped rewriting entirely. The working version leads
with the rewrite task, gives a concrete before/after example, and states the
language rule as a subordinate constraint ("resolving references is not
translating").

### [GOTCHA] `.env` only loads if `core.config` is imported

**What.** A scratch script that imported `core.generation.llm_client` directly
got `ExtractiveClient` and every LLM call silently fell back to the raw query —
making a prompt change look like a total regression when nothing was wrong.

**Why.** `core/config.py` calls `_load_dotenv()` **at import time**. Import
`core.config` (or anything that pulls it in, like `core.pipeline`) and the key
is present; skip it and `ANTHROPIC_API_KEY` is simply unset.

**Do this.** Put `import core.config` at the top of any ad-hoc script that
touches an LLM, and **check `type(llm).__name__` before trusting a result** —
`ExtractiveClient` means you measured nothing. The `standalone()` fallback is
deliberately silent on error, which makes this failure look like a bad rewrite
rather than a missing key.

### [GOTCHA] A follow-up question has no topic of its own

**What.** "what if I have 29 people?" retrieved the **Pay Transparency
Directive** after a GDPR conversation about data protection officers, and the
answer opened with *"too vague on its own"*.

**Why.** `pipeline.query()` took no history. Conversations were persisted for
display only, so prior turns never reached retrieval *or* generation. Stripped
of context the fragment's only signal is a headcount — which matches Pay
Transparency's "fewer than 100 workers" — so BM25 and the vector leg agreed on
the wrong act and the reranker faithfully ranked its passages.

**Do this.** `QueryContextualizer` rewrites the follow-up into a standalone
question before retrieval. **Order matters: contextualisation runs before
HyDE** — expanding a fragment amplifies the wrong topic rather than fixing it.

**Watch for.** Not every follow-up is broken. "what about the withdrawal
period?" resolves fine unaided, because it is lexically distinctive. That case
is kept in the golden set so the metric can't be gamed by assuming all
follow-ups fail.

**Detail:** [DEVLOG 2026-08-09](DEVLOG.md).

### [DECISION] A rewritten question, not history passed to the answerer

**Choice.** Contextualise the *query* and send the rewritten form downstream,
rather than plumbing conversation history into the answerer's prompt.

**Why.** One change fixes both halves of the bug: retrieval gets a real topic,
and the answerer receives a self-contained question so it never sees a
fragment. `answerer`'s cite-or-fail discipline and its prompt are untouched,
which keeps the blast radius off the part of the system that guarantees every
claim is cited.

**Trade-off accepted.** Answers won't refer back conversationally ("as I
mentioned above"). For a citation-first compliance tool that is arguably
correct — each answer stands alone with its own sources.

**Safety.** A bad rewrite is worse than none, because it silently retrieves the
wrong act. Empty, multi-line, or over-long replies are rejected and the raw
query is used instead.

### [DECISION] Anonymous sends history; logged-in reads it server-side

**Choice.** `/query` accepts history in the request body.
`/conversations/{id}/messages` ignores the client and reads the stored chat it
already loaded.

**Why.** Anonymous users have no saved conversation, and anonymous is the
default demo path — the bug was found there. Logged-in requests already have a
server-owned transcript, so trusting the client there would add untrusted input
for no benefit.

**Consequence.** The `/query` caps are load-bearing, not decorative: ≤10 turns,
≤2000 chars per field, only the last 3 used, answers truncated to 400 chars.
This is client-supplied text entering an LLM prompt, so it is bounded the same
way `/ingest` fields are.

### [GOTCHA] The retrieval harness is not deterministic with HyDE on

**What.** `core.evaluation.harness` calls HyDE expansion, which calls Haiku.
Every run therefore gets a different hypothetical document, a different vector
query, and a different candidate pool. Metrics move between runs of *identical*
code.

**How it bit.** During the `EURAG_RERANK_BATCH` A/B, batch=8 measured
`compound_hit` 67% against 100% at batch=64 — a clean-looking 33-point
regression. A repeat run of the same config gave 100%, and batch=12 then gave
67%. The golden set has only **3 compound cases**, so one knife-edge case
flipping is 33 points. A "safer" batch size was nearly shipped on noise.

**Do this.** Pin expansion before any retrieval A/B:

```bash
EURAG_HYDE_MODEL=none python -m core.evaluation.harness
```

A single-case delta is **not** attributable to a code change without it.

**Detail:** [DEVLOG 2026-08-09](DEVLOG.md).

### [DECISION] `EURAG_RERANK_BATCH` defaults to 8

**Choice.** Bound the cross-encoder's forward-pass batch at 8, rather than
leaving fastembed's default of 64.

**Why.** 64 is above every pool `hybrid_retriever` builds (`max(k*5, 30)`: 30 at
`top_k=6`, **60 on the escalation path** at `EURAG_ESCALATION_TOP_K=12`), so the
whole pool always went through in one forward pass. Measured on 60 real chunks:

| batch | rerank allocation | time |
|---|---|---|
| 64 | **1607 MB** | 2.27s |
| 16 | 605 MB | 2.58s |
| **8** | **284 MB** | 2.71s |

**5.7× less transient memory for +0.44s**, which is noise beside a multi-second
Claude call.

**Why it costs nothing.** Cross-encoder scores are per-pair independent, so
batching bounds memory without touching ranking. Verified: every harness metric
identical from batch 4 to 64 with expansion pinned off.

**Why not 16 or 4.** 16 still allocates 605 MB — more headroom than a 4 GB host
should be spending on one step. 4 buys little over 8 and adds latency.

**Watch out.** Raising `EURAG_ESCALATION_TOP_K` grows the pool and this peak
with it.

### [GOTCHA] Escalation is the memory maximum, not a normal query

**What.** Sizing was derived from idle (835 MB) and peak-while-answering
(1.83 GB). Neither is the ceiling: escalation widens retrieval to `top_k=12`, so
the cross-encoder sees 60 candidates instead of 30 and allocates roughly double.

**How it bit.** A follow-up question escalated in production and the api
container was OOM-killed on a 4 GB host. **Symptom: `HTTP 502` with no
traceback in the api log** — the process is `SIGKILL`ed, so there is nothing to
catch or log. Confirm with `dmesg -T | grep -i 'killed process'` and a jump in
`docker inspect ... .RestartCount`.

**Do this.** Size for the escalation path, not the common one.
[`DEPLOY.md` §4](DEPLOY.md) now measures both.

### [GOTCHA] A Linux deploy needs `chown -R 10001:10001 data/raw`

**What.** `data/raw/` is gitignored, so a fresh clone lacks it and Docker
creates the bind-mount source as `root:root`. The image runs as `USER eurag`
(uid 10001) and cannot write `data/raw/.seed.lock` — the seeder dies in ~5s with
`PermissionError` and `service_completed_successfully` correctly refuses to start
the API behind it.

**Why the rehearsal missed it.** Docker Desktop on macOS remaps bind-mount
ownership, so this failure mode **only exists on Linux**. The local prod
rehearsal validates behaviour, not permissions.

**Proper fix, not yet done.** Move the lock into `/app/var/.seed.lock` — the
`apivar` named volume is already owned by `eurag:eurag` and is shared across
replicas, so the flock keeps working with no host-side setup step.

### [DECISION] `replicas: 1` in the prod compose

**Choice.** Ship one API replica by default instead of two.

**Why.** Measured ~2.2 GB idle / **3.2 GB peak** at 2 replicas versus ~1.4 GB /
~2.4 GB at 1. The deploy target is a 4 GB box; two replicas leave almost nothing
for the OS and invite an OOM kill mid-answer.

**What it does not cost.** All state is shared (Postgres / Redis / Qdrant), so
this is a one-line change on a bigger host — the multi-instance design is intact
and still demonstrated.

### [DECISION] Bound the memory spike instead of resizing the VM

**Choice.** Fix the unbounded rerank batch and stay on `e2-medium` (4 GB), rather
than moving to `e2-standard-2` (8 GB).

**Why.** The app's real requirement is ~2.4 GB peak. 8 GB was proposed first
because GCP's `e2` ladder jumps 4 → 8 with nothing between — a convenient shape,
not a measured need. Sizing hardware around an unbounded allocation is how a
14 MB corpus ends up on a $50/month box. The batch fix removed the requirement
entirely.

### [DECISION] Live on GCP trial credit; Hetzner after it expires

**Choice.** `e2-medium`, Ubuntu 24.04, 30 GB disk, static IP, europe-west3, on
the $300 / 90-day trial. Post-credit target is Hetzner `CAX21` (~€7/month, ARM).

**Why.** Oracle Always Free was first choice and is out (signup rejected). Every
1 GB always-free VM (AWS `t3.micro`, Azure `B1S`, GCP `e2-micro`) OOMs on the
first question. ARM suits Hetzner because the original images were built
`linux/arm64` — note GCP `e2` is x86_64, so images are built **on the VM**.

### [DECISION] `EURAG_FREE_ANON_QUESTIONS` drops to 2 — supersedes "stays at 3"

**Choice.** Two free anonymous questions, not three. Default changed in
`core/config.py`, and the var is now passed through to the api container in
`docker-compose.prod.yml`.

**Why.** User's call, same day as the entry below. Two is still enough to show
what the product does (ask something, ask a follow-up) while cutting the
per-visitor exposure on the server's Anthropic key by a third — and the follow-up
is the more expensive path, since contextualisation adds a Haiku call on top of
the answer. Billing alerts are still unset (HANDOFF open item 2), so the number
is currently the *only* live cost control.

**The passthrough matters as much as the number.** The api service has no
`env_file`; it lists its environment explicitly. `EURAG_FREE_ANON_QUESTIONS` was
not in that list, so putting it in the VM's `.env` only fed compose
interpolation, never the container — the code default silently won and the
"reversible with one env var" claim below was false in prod. Now it is true.

**Reversible.** `EURAG_FREE_ANON_QUESTIONS` in the VM's `.env`; `0` makes the
deployment BYOK-only.

### [DECISION] ~~`EURAG_FREE_ANON_QUESTIONS` stays at 3~~ (superseded above)

**Choice.** Keep 3 free anonymous questions on a public URL, and control cost at
the source instead.

**Why.** It's a portfolio project — people trying it is the point. Turnstile now
blocks the bot floor that made this risky. The exposure is real (anonymous
answers are full-quality and escalation-enabled, billed to the *server's* key),
so the control is a spend limit on the Anthropic key plus GCP billing alerts,
not a smaller number.

**Reversible.** One env var; `0` makes the deployment BYOK-only.

---

## Before 2026-08-09

Not backfilled. Earlier decisions and gotchas live in their original homes:

- **Live-safety batch design decisions (D1–D17)** —
  [`../context_files/PLAN_LIVE_SAFETY.md`](../context_files/PLAN_LIVE_SAFETY.md)
- **Standing gotchas** (reseed after chunker changes, the double settings read in
  `api/main.py`, `.env` vs `.env.prod.local`, `EURAG_DOMAIN` at the compose
  layer, anon quota keyed per IP, `EURAG_TRUST_PROXY`) —
  [`../CLAUDE.md`](../CLAUDE.md) → Gotchas
- **Build history with measurements** — [`DEVLOG.md`](DEVLOG.md)
