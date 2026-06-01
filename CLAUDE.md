# Karpathy's 4 Rules

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

# How to ensure Always Works™ implementation
Please ensure your implementation Always Works™ for: $ARGUMENTS.

Follow this systematic approach:

## Core Philosophy
- "Should work" ≠ "does work" - Pattern matching isn't enough
- I'm not paid to write code, I'm paid to solve problems
- Untested code is just a guess, not a solution

# The 30-Second Reality Check - Must answer YES to ALL:
- Did I run/build the code?
- Did I trigger the exact feature I changed?
- Did I see the expected result with my own observation (including GUI)?
- Did I check for error messages?
- Would I bet $100 this works?

# Phrases to Avoid:
- "This should work now"
- "I've fixed the issue" (especially 2nd+ time)
- "Try it now" (without trying it myself)
- "The logic is correct so..."

# Specific Test Requirements:
- UI Changes: Actually click the button/link/form
- API Changes: Make the actual API call
- Data Changes: Query the database
- Logic Changes: Run the specific scenario
- Config Changes: Restart and verify it loads

# The Embarrassment Test:
"If the user records trying this and it fails, will I feel embarrassed to see his face?"

# Time Reality:
- Time saved skipping tests: 30 seconds
- Time wasted when it doesn't work: 30 minutes
- User trust lost: Immeasurable

A user describing a bug for the third time isn't thinking "this AI is trying hard" - they're thinking "why am I wasting time with this incompetent tool?"

- IF the file is large, prioritize learning from existing functions/systems so as not to break the code.
- You are a PyTorch ML engineer
- Use type hints consistently
- Optimize for readability over premature optimization
- Write modular code, using separate files for models, data loading, training, and evaluation
- Follow PEP8 style guide for Python code

# Mewbot guide
- For duels, their effects are linked to their effect_id, which exists in mongo, the pokemon db, moves table.
- BEFORE making database insertions, ensure the data will flow with the existing system and it works when "smoke tested" (i.e. it tallys with example user journeys in the bot like evolving, or forming, and duel form/mega-evolution effects, e.t.c)
- Before checking the JSON files in resources/ or shared/data/ ensure you have thoroughly checked the mongodb database, the mongodb is the primary source of truth for species data and it is the source of truth for moves and all other pokemon data.
- Carefully read all duel files before making changes to make sure it don't break.


# General guide.
- Prioritize readability and simplicity: Write code that is clear and easy to understand, avoiding unnecessary complexity or clever tricks. Focus on making the logic self-evident rather than relying on excessive abstractions or optimizations unless absolutely needed.

- Follow strict formatting conventions: Indent with 8-character tabs (not spaces), limit lines to around 80 columns for readability (but exceed if it improves clarity), and avoid trailing whitespace. Use K&R style for braces: opening braces on the same line for non-function blocks, on a new line for functions.

- Keep functions short and focused: Functions should handle one task, fit within one or two screenfuls, and limit local variables to 5-10. Avoid deep nesting (more than 3-4 levels) by refactoring; complexity should be inversely proportional to length.

- Use descriptive naming without fluff: Choose clear, lowercase names for globals (e.g., count_active_users); short names for locals (e.g., i for loops). Avoid Hungarian notation, mixed-case, or unnecessary typedefs—let types be handled by the compiler.

- Comment on purpose, not mechanics: Explain 'what' and 'why' before functions or blocks, not 'how' inline unless it's unusually tricky. Use kernel-doc for APIs, and prefer multi-line comments with leading asterisks for consistency.

- Avoid breaking existing behavior: Changes must maintain compatibility, especially for user-space APIs. Test for regressions in stability and performance, and justify any trade-offs clearly.

- Submit clean, focused patches: Each patch addresses one issue, builds standalone, and includes an imperative summary (e.g., "fix buffer overflow"). Use Signed-off-by, explain changes in detail, and go through maintainers.

- Communicate bluntly and honestly: Be direct in feedback, calling out flaws harshly if they waste time (e.g., labeling bad code as "garbage"), but focus on facts and technical issues, not personal attacks. Avoid politeness for its own sake or political games.

- Manage references explicitly: Use reference counting for shared data in multi-threaded environments to prevent leaks and races—no reliance on garbage collection.

- Shun over-engineering and premature features: Follow YAGNI—don't add abstractions for unneeded futures. Measure performance before optimizing, and prefer explicit code over macros unless they simplify without issues.

- Test rigorously and treat security as bugs: Validate inputs, handle errors gracefully, and use automated tests. View vulnerabilities as standard debugging problems, not special cases.

- Focus on data structures over code: Prioritize well-designed data and their relationships; good code follows from that. Minimize globals and use structs to group related data.

- Lead decisively as a benevolent dictator: Make firm technical decisions in your domain, saying "no" when needed, but build trust through consistent, project-focused choices. Communication is key in open source—read and write emails effectively, as coding takes a backseat in leadership.

# 