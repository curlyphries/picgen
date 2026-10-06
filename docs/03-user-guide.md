# User guide

How to get good pictures out of the studio. The studio has five tabs: **Create**, **Characters**, **Gallery**, **Library** and **Learning**. The status pill in the top bar shows whether the GPU is free (*ready*), busy (*model loading*, *drawing…*, *checking…*, plus how many jobs are waiting) or unreachable (*offline*).

## Your first picture

1. Open the studio. It opens on **Create**.
2. Type what you want in **Prompt**. Write plain sentences, subject first: *"A red fox asleep in fresh snow at dawn, soft pink light."* Long keyword lists work worse than one clear description.
3. Leave the character chips unselected. The hint says *none selected = plain text-to-image*.
4. Pick a **Model**. The line under the controls tells you its speed, what it is best at and a tip. Start with **Z-Image Turbo**.
5. Optionally set **Style** (photo, illustration, cinematic, cartoon), **Size** (square, landscape, portrait, wide), **Steps**, **Guidance** (blank means the model's own default) and **Seed** (blank means random).
6. Press **Generate**, or Ctrl+Enter (Cmd+Enter on a Mac). The status line counts seconds while it works.

The finished picture shows with a line of details (model, size, steps, seed, time) and the actions 👍, 👎, **Edit**, **Save as character**, **Download** and **Delete**. Every picture is also saved in the **Gallery**.

> **The first picture after a quiet spell takes longer.** The image engine shuts down after 5 minutes without work so other software can use the GPU. Starting it again adds about 40 seconds.

### Let the helper write the prompt

The **Talk it through** panel is a chat with a local language model. Describe the idea in your own words; it answers with a prompt written the way the image models like. Press **Use as prompt** to copy it into the prompt box. If you already have a rough prompt, **Rewrite for the generator** under the prompt box does the same in one step.

## Characters

A character is a saved reference picture plus a name. Once saved, you can put it in any scene and it keeps its face, colours and outfit.

### Add a character

1. Go to **Characters → Add a character**.
2. Give it a **Name**, optional **Notes**, and say **What is it?** (for example *girl*, *dachshund*, *robot*). This word goes into the prompts picgen writes.
3. Choose one **Reference picture** (PNG, JPEG or WebP, up to 40 MB). Use a colour picture of the character facing the camera, clearly lit, with the whole body or at least the upper body. A black-and-white or side-on reference makes the likeness drift.
4. Keep **Build the pose & expression sheet now** ticked, then press **Save character**.

The sheet is 13 extra pictures of the same character: sad, neutral, smiling, laughing, surprised and angry faces, three-quarter, profile, over the shoulder, from behind, standing full-body, walking side-on and sitting. picgen draws them one after another, about a minute each, and uses them as better references later. The card fills in as they finish.

You can also turn any picture you made into a character: press **Save as character** on it.

### Give a character more views

Each character card has two optional extras:

- **Add views** uploads your own pictures of the character, such as a strip of expressions from another tool. Tick *Each file is a strip of panels* and picgen splits the strip into separate views. Give one label per panel (*shocked, wink, thumbs up…*) so the planner can find them.
- **Make this character's own versions** draws the character in poses, expressions and outfits from the shared library, about 2 minutes each. A character's own versions work much better than borrowed guides, so do this for any character you will use often.

## Putting a character in a scene

1. On **Create**, click the character's chip, or press **Use in Create** on its card. The model switches to *FLUX.1 Kontext (character…)*.
2. Describe one moment, not a story. Name the action, the face and the clothes: *"running, scared, wearing cowboy boots, in a rainy alley."*
3. Read the line under the prompt. **Auto will use →** lists the pose, face and garments the planner picked, how many references that is, the guidance and steps it will use, and roughly how long it will take. It costs nothing to check.
4. If it says *no pose or face matched*, use plainer words from the library (*running*, *waving*, *sitting*, *laughing*), or press **Rewrite for the generator**.
5. Press **Generate**. A character picture takes about 90 seconds, plus about 55 seconds for each extra reference.

### Choosing references by hand

**Auto** is right most of the time. To override it:

- **Reference** picks the picture the character is drawn from: **Original**, one of its own views, or a *guide* from the library (a pose drawn with another character). Guides have a dashed border.
- **Wearing** picks up to two garments from the library, or **None** for no garments. **Auto** dresses the character in whatever the prompt names.

A picture can use at most four references: the character, a pose, a face and a garment. Past that, the last garment tends to drop out, so name the most important item first.

## Editing a picture

1. Press **Edit** on any picture.
2. In **What should change?** describe only the change: *"make it night"*, *"add sunglasses"*, *"he runs away, scared"*. picgen adds *"keep everything else exactly the same"* for you.
3. Press **Apply**. The edit is saved as a new picture; the original stays.
4. Press **Keep** to close, or **Edit again** to keep changing the result.

## When a picture comes out wrong

1. Press 👎 on it. If the automatic check found a problem, its strip says ⚠ and **Fix these** opens the same dialog.
2. Tick what is wrong: anatomy, gaze, pose, expression, garment, identity, scene, style, artifact or other. Add a note in your own words if it helps.
3. Read **Why it went wrong**, and the **Proposed fix** under it. You can reword the fix.
4. Choose:
   - **Submit & fix just this** edits the picture and changes only what you flagged. A *guided fix* first draws or borrows a pose picture to steer it.
   - **Submit & regenerate with lessons** draws a fresh picture with any newly learned lessons applied.
   - **Submit only** records the feedback.
5. The result opens next to the original so you can compare them. Grade the fix too; that is how picgen learns which fixes work.

Press 👍 on pictures that came out right. That credits the lessons that were used.

### Teach a pose

When a picture of a character comes out right, especially a fix or a pose borrowed from another character, press **Teach pose** on it (the studio also offers this after a 👍 when it is worthwhile). Describe what the character is doing, for example *reading a spellbook in a candlelit library*. The picture becomes one of the character's own views, and future prompts that mention that pose start from it. To undo, delete the view from the character's card.

## The Learning tab

Learning shows what the studio has learned:

- Approval rates (all time and the last 7 days), and how many pictures the automatic check reviewed.
- Which flaws you reported and which the check found.
- How each kind of fix has worked out.
- The **lessons**: rules applied before drawing, such as *"when the prompt mentions waving, add 'one raised hand, five fingers' and use at least 28 steps"*. Each has an on/off switch, a record and example pictures.

A lesson you switch off stays off: the automatic check will not switch it back on.

**Learn from my feedback** asks the vision model to propose new lessons from your recent thumbs-downs. They arrive switched off so you can review them first. **Add my own rule** lets you write one by hand.

## The shared library

The library holds reusable references that any character can borrow: expressions, poses, activities and ten kinds of garment (outfit, top, pants, shorts, underwear, socks, footwear, headwear, accessory, prop). Browse it with the kind pills and the filter.

To add items, use **Library → Add to library**:

1. Choose one or more pictures and set the **Kind**.
2. Set the **Layout**. **Auto** finds the grid in a sheet of panels. **Strip** is one row. **One view per file** keeps each file whole. **Custom** takes columns × rows, such as `3x2`.
3. Adjust **Caption crop** and **Top crop** to cut off printed labels.
4. For turnarounds (front, side and back of the same shoe), set **Group every N panels** to 3. The three views become one wide item.
5. Set **Drawn with** to the character in the pictures, if any. For that character the items count as its own views; for everyone else they are guides.
6. Write one **label** per panel or group in order, or `skip` to drop a panel. Labels are what the planner matches against prompts, so use the words people will type.

## Good habits

- One character per picture. Two characters in one picture is not supported in v1.0.
- Save colour, front-facing, well-lit references.
- Make the character's own versions of the poses you use often.
- Describe one moment with concrete words; avoid stories and keyword piles.
- Use Z-Image Turbo for quick ideas and anything with text in it, FLUX.1-dev for photoreal portraits, and Kontext for characters and edits.

## Shortcuts

| Key | Where | Does |
|---|---|---|
| Ctrl/Cmd + Enter | Prompt box | Generate |
| Enter | Chat box | Send (Shift + Enter for a new line) |
| Esc | Anywhere | Close the open picture or dialog |
| Esc | A filter box | Clear the filter |
