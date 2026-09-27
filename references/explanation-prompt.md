# Explanation prompt — Korean word explanation for Anki cards

This is what generates the `explanation` field. The goal is Korean-only, beginner-friendly
explanations that sound like a native speaker talking to a 13-year-old — matching the style
of Nate's existing cards so mined cards feel consistent.

## The prompt

```
Please write a short explanation of the word '{word}' using the context of the original sentence: '{sentence}'.

Write an explanation that helps a Korean beginner understand the word and how it is used with this context as an example.

Explain it in the same way a native would explain it to a 13-year-old. Don't use any English, only use simpler Korean.

1. Don't write pronunciation guides in brackets after the word.
2. Don't start with stuff like 이 단어를 간단히 설명할게요, just dive straight into explaining after starting with the word.
```

`{word}` → the lemma (dictionary form). `{sentence}` → the source sentence text.

## When you (Claude) write the explanation

- Start with the word itself (e.g. `기박`), then explain.
- All Korean. No English at all. No romanization.
- ~150–250 Korean characters is the sweet spot — short enough that Gemini TTS produces a clean 20–40 second clip, long enough to convey nuance.
- Match the register of the source: if the video is casual speech between friends, use 반말 or friendly 해요체; if it's formal news/drama, be more polished.
- Reference the source sentence's situation when it helps clarify the meaning.
- For words with multiple senses (e.g. 배 = 배(腹)/배(梨)/배(船)), clarify which sense is being shown by the context sentence.

## A good example

For `기운` in sentence `"요즘 기운이 없어서 집에만 있어"`:

> 기운은 몸이나 마음에 힘이 있는 느낌이야. "기운이 없다"고 하면 몸이 피곤하거나 의욕이 없다는 뜻이야. 이 문장에서는 요즘 너무 지쳐있어서 집에만 있게 된다는 뜻으로 쓰인 거야.

Note: starts with the word, defines it simply, then ties it to the source sentence. Mirror this shape.

## Grammar-pattern variant (`"pos": "GRAM"` candidates)

Grammar patterns (`-는데`, `-고 있다`, `-(으)ㄹ 수 있다`, ...) are mined and pushed the
same way vocabulary is — see [known-words.md](known-words.md) — but the explanation
should read as a grammar explanation, not a word definition. Use this prompt instead:

```
Please write a short explanation of the Korean grammar pattern '{word}' using the
context of the original sentence: '{sentence}'.

Write an explanation that helps a Korean beginner understand what this grammar
pattern MEANS and how/when it's used, with this sentence as an example.

Explain it in the same way a native would explain it to a 13-year-old. Don't use
any English, only use simpler Korean.

1. Don't write pronunciation guides in brackets after the pattern.
2. Don't start with stuff like 이 문법을 간단히 설명할게요, just dive straight into
   explaining after starting with the pattern.
3. If the pattern has a common alternate form (e.g. -아서/-어서), briefly mention
   the form actually used in the sentence rather than listing every variant.
```

`{word}` → the pattern's canonical id (e.g. `-는데`), exactly as it appears in the
candidate's `lemma`/`word` field. `{sentence}` → the source sentence.

### A good example

For `-는데` in sentence `"그거 하는데 힘들어."`:

> -는데는 어떤 상황을 먼저 말해주고 그다음에 관련된 이야기를 이어서 할 때 써. "그거 하는데 힘들어"라고 하면 "그걸 하고 있는 상황인데, 그게 힘들다"는 뜻을 자연스럽게 이어서 말하는 거야. 뒤에 놀라움이나 반대되는 이야기가 나올 때도 많이 써.

Note: starts with the pattern itself, explains its function (not a dictionary
meaning — patterns don't have one), then ties it to the source sentence.
