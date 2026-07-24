# Image Prompt — Guidelines

Used to generate the token's emblem (the `image_prompt` field on a Concept).

## Goal
A crisp, memorable **logo/emblem** — not an illustration, not a scene.

## Template
```
Bold minimalist emblem for '<NAME>', motif evoking <core idea> and <TICKER> energy,
high-contrast, vector, centered, no text, crypto-native, flat, iconic.
```

## Rules
- **No text in the image** (symbols/logos render text poorly and it dates fast).
- High contrast, works as a small circular avatar.
- One clear motif — don't cram.
- Avoid trademarked logos or a company's exact brand marks (clean footprint).
- Prefer vector/flat/iconic over photoreal.

## Pipeline note
Image generation is not wired to a provider by default (ships over polish). The
`image_prompt` is stored on every Concept; attach a generator by populating
`Concept.image_url` before the launch request is built, and it flows through to
Bankr's `--image` / prompt automatically.
