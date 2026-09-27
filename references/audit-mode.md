# Audit Mode

Source: an **existing deck** of already-mined cards (usually `config.decks.main`, but Nate can point at any deck). No new cards are created — this mode reviews what's already there and hands Nate copy-pasteable Anki search queries to clean it up himself.

When this mode triggers:
- Nate says "audit my deck", "check my cards", "clean up my deck", "look through my Anki cards"
- Nate asks something like "are any of my cards not real words", "did anything get mislabeled", "do I have duplicate cards for the same word"
- Nate names a deck and asks you to review it for quality

If Nate wants a *sentence* replaced on a card whose word is fine → that's [replace mode](replace-mode.md), not audit mode. Audit mode's job is deciding what to **remove**, not what to fix in place.

## Why audit mode exists

Sentence-mining pipelines (this skill's own video/bank modes, or any other tool — Kimchi Reader, manual imports, older versions of this skill) all share the same failure modes: a tokenizer occasionally mines a bound fragment as if it were a real word, ASR occasionally mishears a name or set phrase and mines whatever garbled string came out, a talk-show transcript mints cast members' names and show titles as if they were vocabulary, a technically-correct sentence leans on unseen context and never actually shows what the word means, and re-mining the same word from different sources creates duplicate cards. None of this is visible from a glance at the deck — it takes actually reading each word against its sentence. Audit mode is that read-through, done systematically and left in a state Nate can act on with one paste into Anki's Browse window.

**This mode never calls `deleteNotes`.** Deleting is presented as copy-pasteable `nid:` search queries for Nate to run himself — see [Never delete directly](#never-delete-directly) below for why.

## Step 1: Pull the cards

```bash
python3 <skill-dir>/scripts/audit_pull.py > ~/Downloads/sentence-mining/audit.<deck-slug>.json
```

Defaults to `config.decks.main`. Pass `--deck "Some::Deck"` (repeatable) to audit a different deck or several at once — ask Nate if it's not obvious which deck he means. Output is `{"deck": [...], "cards": [{"noteId": ..., "word": "...", "sentence": "..."}, ...]}` with HTML already stripped from both fields.

Zero cards? Say so and stop.

## Step 2: Review every card (inline — no script)

This is the heart of audit mode and it's Claude's judgment, not a heuristic script — the same way curation in video/bank mode is inline reading, not a filter function. Walk the full card list and sort findings into five buckets. A single card can land in more than one bucket (e.g. a genuinely fake word whose sentence is also garbled) — report it under whichever bucket is the more fundamental problem, don't double-list it.

### Bucket A — not a standalone word in any sense

The word field is a **bound morpheme or fragment** — a piece Korean speakers never use independently, only ever glued onto something else (a prefix, a name-suffix, one syllable of a compound slang term that a tokenizer split apart). The test: could you use this string on its own, in an ordinary sentence, and have it mean something? If it only ever appears stuck to other syllables as part of a larger fixed expression, it belongs here.

This is a *stricter* bucket than "used in an unusual sense" — a real dictionary word being used in a slangy or unexpected way is a Bucket C problem, not this one. Only put something here if it genuinely isn't an independent word at all.

Example from a past audit: 현 (a fragment of the slang compound 현타, "reality-check moment" — 현 alone isn't a word you'd ever say by itself) and 순 (a fragment of 집순이, "homebody" — bound name-element, not standalone).

### Bucket B — misattributed: ASR mis-transcription

The sentence field **never actually contained a genuine use of the word at all**. What happened is an audio-to-text error produced a wrong or nonsense string, and whatever mined the card grabbed a real dictionary word that happened to coincidentally match a fragment of that garbled text — usually because a proper noun, brand name, or set phrase got mangled beyond recognition.

**This one needs a confidence tier, because catching it reliably requires a source to check against.** If Nate has (or you have) a clean reference transcript of the same source audio/video — from an earlier mining pass on the same source, or one you can generate — cross-check the suspicious sentence against it and mark the finding **"confirmed via source"**. Without something to verify against, you're going on whether the sentence *reads* like natural Korean or like ASR wreckage — mark those **"suspected — unverified"** rather than stating them as fact. Ask Nate up front whether a reference transcript exists for the deck (or the specific cards) he wants audited; if the source is identifiable and re-transcribing it is cheap, offer to do that instead of guessing.

Example from a past audit (confirmed via source): a card mined 표지 ("cover," as in a book cover) from a sentence that was supposed to say 편집 ("editing") — 편집 got ASR-mangled into something that read as 표지. Also 창 (window) from a mangled 침착맨 (a streamer's name), 톡톡 from 틱톡 (TikTok), 캐다 from 케데헌 (a movie title), 전차 from 전체 화면 ("full screen").

### Bucket C — misattributed: wrong sense / wrong lemma

The word genuinely *is* in the sentence — no ASR issue — but the sentence uses a different sense than the headword implies, or an inflected/honorific surface form got lemmatized back to the wrong dictionary entry entirely. No source transcript needed here; this is catchable by reasoning about the word and sentence alone.

Two flavors:
- **Homonym collision**: the surface form is real and correctly transcribed, but means something else here than what the headword card would teach. Example: 갓 normally means the traditional Korean hat, but a sentence using it as the internet-slang intensifier prefix ("god-tier," borrowed from English) is teaching a completely different word that happens to share the spelling.
- **Wrong-headword lemmatization**: an inflected form got attributed to the wrong base verb/adjective. Example: "가십시오" (a polite imperative) is honorific 가다 ("to go") + -시-, but got lemmatized to "가시다" — which is *also* a real, separate dictionary verb meaning "(a taste/smell/feeling) to fade or subside." The card ends up teaching the wrong headword's meaning entirely, not just a wrong sense of the right one.

Don't flag ordinary, transparent metaphorical extensions here — Korean (like any language) stretches core meanings naturally, and flagging every metaphor as "wrong sense" would bury real findings in noise. 건지다 ("to fish out/salvage," used for "got good footage today") and 넘치다 ("to overflow," used for "talent is overflowing") are normal, easily-inferred extensions — leave those alone. The bar is: would this usage actively mislead Nate about what the word means, not just "is this the most literal sense."

### Bucket D — proper noun used as a proper noun

Talk shows, variety shows, and interviews mine cast members' names and show titles constantly — a name gets used dozens of times across an episode, so it's exactly the kind of thing that ends up over-represented in mined vocabulary despite teaching Nate nothing reusable. This mirrors the "Pop-culture proper nouns" rule video/bank mode already applies when *creating* cards (see Step 3.5 in SKILL.md) — audit mode is catching the same problem after the fact.

**The test is about what the sentence is doing, not what the string could theoretically mean elsewhere: is this card's sentence using the word as a specific person's name or a specific title?** If so, remove it — even when the same spelling happens to also be an ordinary dictionary word in other contexts. A card that only ever demonstrates "this is what Kwon Yuri did" teaches Nate nothing about the word "glass," no matter how coincidentally the spellings line up; keeping it around under the theory that the string is "technically also a real word" just leaves a misleading card in the deck. If a card's sentence genuinely uses the word in its ordinary dictionary sense, it was never a Bucket D case to begin with — that's just a normal, fine card.

Example from a past audit: 유리 (18 cards) — every single sentence is about the person Kwon Yuri, never "glass," so all 18 are removed despite 유리 being a real word elsewhere. Same for 진영 (person's name, though 진영 also means "camp/faction" in other contexts), 수정 ("Writer Sujeong," though 수정 also means "crystal"/"correction" elsewhere), 존 (fragment of the show title "더 존"), 무한 (fragment of "무한도전"), and 동현 (a cast member's name, with no other meaning at all). What matters in every case is that the *sentence on the card* is the proper-noun usage — not whether the bare string has some other life outside this deck.

### Bucket E — vague sentence, no real context for the word

The word is real, correctly transcribed, and used in its right sense — but the sentence doesn't actually teach Nate anything about it. This almost always happens through an unstated referent: "이게," "그게," "저거," "이거," pointing at something the sentence itself never names, so the word just sits next to a vague pointer instead of in a context that shows what it means.

**The test: cover up the target word — could you narrow down what it might be from the rest of the sentence, or would a dozen different words fit equally well?** If the sentence would work just as well with almost any word slotted into the target's place, it's teaching Nate the sentence's shape, not the word.

This is the same signal already used to break ties between duplicate cards (Step 3, point 4, "self-contained over deictic") — the difference is scope. That rubric only fires when there's already a second card to compare against and pick the better of. Bucket E asks the same question of every card, including ones that have no duplicate to be measured against, because a lone vague card is just as unhelpful as a vague one that lost a duplicate comparison.

Don't over-apply this. Casual spoken Korean leans on situational context constantly — a card doesn't need textbook clarity, and demanding it would strip a mined deck of anything that sounds like a real person actually talking. The bar is specifically whether the sentence supplies enough of the word's *own* meaning to be worth learning from, not whether it would stand alone perfectly out of all context.

Example: "이게 한계인 것 같아요" ("I think this is the limit") uses 한계 (limit) correctly, but "이게" never says what "this" is — nothing in the sentence conveys what a limit actually looks like here. Contrast with "와 70이 한계인듯?" ("Wow, 70 seems to be the limit?") — same word, but naming a concrete number ("70") gives the sentence something to actually teach.

## Step 3: Duplicate words — pick one per word, explain why

Group the card list by word field, using whatever's left after Buckets A/B/C/D/E have already pulled their cards — a duplicate group where one twin got removed for being vague or misattributed isn't a duplicate anymore, it just resolves to whatever survives. For every word that still has 2+ cards, pick exactly one to keep. Use this rubric — it's what separated the good sentence from the weaker one in past audits, and the same signals generalize well:

1. **Complete thought, not a trail-off.** A sentence that dangles on a non-final connective (-는데, -고, -아/어서...) with no resolution is harder to learn from than one that actually finishes. Prefer the one that lands somewhere.
2. **Clean audio/transcript, no stutters.** ASR artifacts (a repeated or garbled fragment mid-sentence) make a card actively harder to review — prefer the cleaner take.
3. **Core sense, not a stretch.** If one sentence uses the word in its plain common meaning and the other stretches it into slang or an unusual extension, prefer the plain one — that's what generalizes to everywhere else Nate will encounter the word.
4. **Self-contained over deictic.** A sentence that depends on unseen context to make sense ("this filter," "like this," pointing at something the reader can't see) is weaker than one that stands on its own without that crutch.
5. **Memorability as a tiebreaker.** When two sentences are otherwise close, the more vivid or emotionally resonant one wins — a natural repetition landing on the target word, a relatable moment, concrete imagery. Flatter, more clinical phrasing loses close ties.

Write one line of reasoning per word explaining the pick, referencing whichever of the above actually applied — don't just say "this one's better."

## Step 4: Output

Six deliverables, in this order. Every "remove" list pairs a copy-pasteable word list with a copy-pasteable `nid:` query — that's what makes the finding actually actionable, not just a report. **Every word list in this output — in any of the six deliverables — is a single comma-separated line, never one word per line.** A multi-line block forces Nate to select carefully or copy line-by-line; a comma-separated line is one drag-select or one click-and-copy.

**1. Not standalone words (Bucket A)**
```
현, 순, ...

nid:1788197712881,1788197715827
```

**2. Proper nouns used as proper nouns (Bucket D)**
```
유리 (every sentence is the person Kwon Yuri, never "glass"), 동현 (a cast member's name, no other meaning)

nid:1788354741911,1788354758182,...
```

**3. Vague sentences, no real context (Bucket E)**
```
한계 ("이게 한계인 것 같아요" — "이게" never says what "this" is)

nid:1788354422432,...
```

**4. Misattributed (Buckets B + C)** — group by confidence tier and sub-type so Nate knows which findings are certain and which are judgment calls:
```
Confirmed via source (ASR mis-transcription):
  표지, 창, 톡톡, 캐다, 전차 — nid:...

Suspected, unverified (ASR mis-transcription — no reference transcript to check against):
  숨 — nid:...

Wrong sense / wrong lemma:
  갓 (hat → slang "god-tier" prefix), 가시다 (honorific of 가다, misattributed to the unrelated "fade/subside" verb)
  nid:...
```

**5. Duplicate resolution** — per word, the note ID being dropped plus the one-line reason, then a combined query for everything being dropped:
```
활용하다: drop 1788197695626 ("이 필터들을 활용해서 약간" trails off on 약간 with no landing) → keep 1788197848326
적용: drop 1788197778111 (tangled -는데 clause, hard to parse) → keep 1788197782518
열받다: drop 1788197818029 (stretches 열받다 into a slangy "make it extreme" sense) and 1788197809897 (garbled "팔로워로워" stutter) → keep 1788197824762 (vivid, doubled repetition, correct core sense)

nid:1788197695626,1788197778111,1788197818029,1788197809897
```

**6. Final surviving word list** — every card that isn't in any removal list above, as a single comma-separated line of Word field values (not one per line — Nate copy-pastes this whole and a comma-separated line is easier to grab in one motion than a multi-line block). This is the deliverable Nate is actually after; the removal lists are how you get there.
```
크기, 벤치, 고개, 좁히다, 최대한, ...
```

## Never delete directly

Even with an explicit "yes, delete those" from Nate, don't call AnkiConnect's `deleteNotes` — permanently removing data isn't something to do on Claude's own authority, confirmed or not. The whole value of this mode is turning a fuzzy "is my deck any good" question into a precise, reviewable action Nate takes himself in one paste. If he asks you to just do it, say so plainly and hand him the query — don't look for a workaround.

## Out of scope

Audio/media quality (padding, clips that run long, multiple sentences bleeding into one clip) is a separate, unrelated concern from what's on the *card fields* — don't fold it into an audit pass. If Nate raises it, treat it as its own investigation: check whether the "extra" is silence (safe to trim) or genuine extra speech (needs the clip re-cut against a source transcript, which most existing cards won't have available).
